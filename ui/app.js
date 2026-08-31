const state = {
  page: "overview",
  view: "impacts",
  rows: [],
  reports: [],
  selectedReportId: null,
  mapData: null,
  mapLayer: "risk",
  mapZoneFocus: null,
  mapViewBox: [0, 0, 900, 520],
  research: null,
  marketState: null,
  alerts: [],
  marketDayIndex: null,
  refreshRunning: false,
  refreshPollTimer: null,
};

const el = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function capacity(value, signed = false) {
  if (value === null || value === undefined) return "—";
  const prefix = signed && value > 0 ? "+" : "";
  return `${prefix}${new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value / 1000)}k`;
}

function wholeNumber(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}

function ageLabel(hours) {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m old`;
  if (hours < 48) return `${Math.round(hours)}h old`;
  return `${Math.round(hours / 24)}d old`;
}

function dateLabel(value) {
  if (!value) return "—";
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(year, month - 1, day)));
}

function timestampLabel(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(value));
}

function period(row) {
  if (row.period_start === row.period_end) return dateLabel(row.period_start);
  return `${dateLabel(row.period_start)} – ${dateLabel(row.period_end)}`;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function renderRefreshStatus(data) {
  const button = el("refresh-data");
  const status = el("refresh-status");
  const detail = el("refresh-detail");
  const copy = button.closest(".refresh-control").querySelector(".refresh-copy");
  const wasRunning = state.refreshRunning;
  const running = data.status === "running";
  state.refreshRunning = running;
  button.disabled = running;
  button.textContent = running ? "Refreshing…" : "Refresh data";
  copy.classList.toggle("failed", data.status === "failed");
  status.textContent = running
    ? "Refreshing public data"
    : data.status === "completed"
      ? `Updated ${timestampLabel(data.completed_at_utc)}`
      : data.status === "failed"
        ? "Refresh failed"
        : data.status === "skipped_locked"
          ? "Refresh already running"
          : "Data refresh";
  detail.textContent = data.message || "Pull latest public sources";
  detail.title = detail.textContent;
  clearTimeout(state.refreshPollTimer);
  if (running) {
    state.refreshPollTimer = setTimeout(loadRefreshStatus, 1500);
  } else if (wasRunning && data.status === "completed") {
    detail.textContent = "Updated—reloading the current market view…";
    setTimeout(() => window.location.reload(), 600);
  }
}

async function loadRefreshStatus() {
  try {
    renderRefreshStatus(await fetchJson("/api/refresh"));
  } catch (error) {
    renderRefreshStatus({ status: "failed", message: error.message });
  }
}

async function startRefresh() {
  const button = el("refresh-data");
  button.disabled = true;
  try {
    const response = await fetch("/api/refresh", {
      method: "POST",
      headers: { "X-Pipeline-Pulse": "refresh" },
    });
    const data = await response.json();
    if (!response.ok && response.status !== 409) {
      throw new Error(data.message || `${response.status} ${response.statusText}`);
    }
    renderRefreshStatus(data);
  } catch (error) {
    renderRefreshStatus({ status: "failed", message: error.message });
  }
}

function showError(error) {
  if (state.view === "map") {
    el("map-inspector").innerHTML = `<div class="error-banner">${escapeHtml(error.message)}</div>`;
    return;
  }
  el("table-body").innerHTML = `<tr><td class="loading-cell"><div class="error-banner">${escapeHtml(error.message)}</div></td></tr>`;
}

async function loadOverview() {
  const data = await fetchJson("/api/overview");
  el("report-date").textContent = `Outage report · ${dateLabel(data.latest_report_date)}`;
  el("report-subtitle").textContent = `TGP notice ${data.latest_report_notice_id} · current maintenance and capacity outlook`;
  el("latest-source").href = data.source_url;
}

function alertEvidenceLine(alert) {
  const evidence = alert.evidence || {};
  const before = evidence.before || {};
  const after = evidence.after || {};
  if (alert.event_type === "capacity_snapshot_change") {
    return `${capacity(before.available_capacity_dth_per_day)} → ${capacity(after.available_capacity_dth_per_day)} Dth/day available · ${before.scheduled_pct_of_operating ?? "—"}% → ${after.scheduled_pct_of_operating ?? "—"}% scheduled`;
  }
  if (alert.event_type === "outage_capacity_revision") {
    return `${capacity(before.operating_capacity_dth_per_day)} → ${capacity(after.operating_capacity_dth_per_day)} Dth/day forecast capacity`;
  }
  if (alert.event_type === "notice_content_revision") {
    return `Notice ${evidence.subject?.notice_id || "—"} · ${(evidence.changed_fields || []).join(", ") || "content changed"}`;
  }
  return `Notice ${evidence.subject?.notice_id || "—"} · ${after.status || "new operator update"}`;
}

function renderSourceFreshness(data) {
  el("source-freshness").innerHTML = data.sources.map((source) => {
    const label = source.source_code === "ai_memo" ? "Analyst note" : source.label;
    return `
    <article class="freshness-source ${source.status === "stale" ? "stale" : ""}" title="Collected ${escapeHtml(source.collected_at_utc)}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(ageLabel(source.collection_age_hours))} · ${escapeHtml(source.status)}</strong>
      <small>Source as of ${escapeHtml(timestampLabel(source.source_as_of_utc))}</small>
    </article>
  `;
  }).join("");
}

function impactChannelLabel(channel) {
  return {
    forward_transport_capacity: "Forward transport",
    operating_capacity: "Current capacity",
    scheduling_pressure: "Scheduling pressure",
    operational_notice: "Operations",
    operator_notice: "Operator notice",
    critical_notice: "Operations",
  }[channel] || "Transport watch";
}

function alertMarketMeaning(alert) {
  if (alert.event_type === "outage_capacity_revision") {
    return ["worsened", "tightened"].includes(alert.current_status)
      ? "Lower forecast capacity raises the chance of a regional transport bottleneck if nominations remain firm and alternate routes cannot absorb the gas."
      : "Higher forecast capacity reduces the modeled transport constraint, all else equal.";
  }
  if (alert.event_type === "capacity_snapshot_change") {
    if (alert.current_status === "changed") {
      return "The reported values come from different gas days or nomination cycles. Treat this as a monitoring update until a like-for-like snapshot confirms the direction.";
    }
    return ["worsened", "tightened"].includes(alert.current_status)
      ? "Less available capacity or higher scheduling pressure can signal near-term transport stress; compare the same gas day and cycle before treating it as persistent."
      : "More available capacity or lower scheduling pressure eases the immediate transport setup.";
  }
  if (alert.event_type === "notice_content_revision") {
    return "The operator changed information under the same notice ID. The revised timing or language can alter the maintenance read, and it must only enter analysis from the time this version was captured.";
  }
  return "The notice may change near-term scheduling or transport availability. Its market effect depends on whether confirmed volumes, rerouting, or regional prices respond.";
}

function renderAlerts(data) {
  state.alerts = data.items || [];
  el("alert-summary").textContent = data.material_change_in_latest_pull
    ? `${data.returned_item_count < data.alert_count ? `${data.returned_item_count} of ` : ""}${data.alert_count} material updates · ${timestampLabel(data.latest_alert_at_utc)}`
    : data.recent_fallback
      ? `No change in latest pull · showing ${data.returned_item_count} recent`
      : `No priority change · latest check ${timestampLabel(data.latest_collection_at_utc)}`;
  if (!state.alerts.length) {
    el("alert-feed").innerHTML = `<p class="empty-context">No operator update crossed the alert threshold in the latest check.</p>`;
    return;
  }
  el("alert-feed").innerHTML = state.alerts.map((alert, index) => `
    <button class="alert-card ${escapeHtml(alert.severity_band)}" data-alert-index="${index}" type="button">
      <span class="alert-card-head">
        <span class="alert-status ${escapeHtml(alert.current_status)}">${escapeHtml(alert.current_status)}</span>
        <span class="alert-score">${escapeHtml(impactChannelLabel(alert.impact_channel))}</span>
      </span>
      <span class="alert-main">
        <strong>${escapeHtml(alert.headline)}</strong>
        <small>Detected ${escapeHtml(timestampLabel(alert.decision_at_utc))}</small>
      </span>
      <p class="alert-evidence-line">${escapeHtml(alertEvidenceLine(alert))}</p>
      <span class="alert-action">Why it matters <b>→</b></span>
      ${alert.evidence?.comparison_warning ? `<span class="alert-warning">${escapeHtml(alert.evidence.comparison_warning)}</span>` : ""}
    </button>
  `).join("");
}

async function loadChanges() {
  try {
    const [freshness, latestAlerts] = await Promise.all([
      fetchJson("/api/source-freshness"),
      fetchJson("/api/alerts?scope=latest&limit=6"),
    ]);
    const alerts = latestAlerts.items.length
      ? latestAlerts
      : {
        ...await fetchJson("/api/alerts?scope=recent&limit=6"),
        material_change_in_latest_pull: false,
        latest_collection_at_utc: latestAlerts.latest_collection_at_utc,
        recent_fallback: true,
      };
    renderSourceFreshness(freshness);
    renderAlerts(alerts);
  } catch (error) {
    el("alert-summary").textContent = "Change feed unavailable";
    el("alert-feed").innerHTML = `<div class="error-banner">${escapeHtml(error.message)}</div>`;
  }
}

const defaultGlossary = [
  { term: "Dth/day", definition: "Dekatherms per day, a standard measure of daily natural-gas volume or transport capacity." },
  { term: "Operating capacity", definition: "The amount of transportation capacity the operator currently says it can provide." },
  { term: "Scheduled quantity", definition: "Gas nominated and confirmed for transportation in the reported direction; it is not a physical flow meter." },
  { term: "Available capacity", definition: "Operating capacity left after scheduled quantity, floored at zero in TGP’s export." },
  { term: "Basis", definition: "The price difference between a regional gas market and a benchmark such as Henry Hub." },
];

function listItems(values, fallback) {
  const items = values?.length ? values : [fallback];
  return items.slice(0, 4).map((value) => `<li>${escapeHtml(analystText(value))}</li>`).join("");
}

function analystText(value) {
  return String(value ?? "")
    .replace(/\s*\[(?:km_|eia_)[\s\S]*\]\s*$/i, "")
    .replace(/\s+Evidence:\s+(?:km_|eia_|nws_|fred_|yahoo_)[\s\S]*$/i, "")
    .trim();
}

function deterministicWatchItems(packet) {
  const seen = new Set();
  return (packet.transport_impacts || []).filter((item) => {
    if (seen.has(item.operator_segment_id)) return false;
    seen.add(item.operator_segment_id);
    return true;
  }).slice(0, 3).map((item) => ({
    title: `Segment ${item.operator_segment_id || "unmapped"} · ${item.station_label}`,
    market_channel: item.research_status.replaceAll("_", " "),
    research_status: item.research_status,
    scenario: item.conditional_scheduled_shortfall_dth_per_day > 0
      ? `If the captured net schedule held through this event, ${capacity(item.conditional_scheduled_shortfall_dth_per_day)} Dth/day would exceed the ${capacity(item.forecast_operating_capacity_dth_per_day)} Dth/day forecast operating limit. That is a research scenario—not confirmed curtailment or flow loss.`
      : `The report removes ${capacity(item.gross_reduction_dth_per_day)} Dth/day of nominal capacity, but the captured net schedule still fits with ${capacity(item.forecast_headroom_vs_baseline_schedule_dth_per_day)} Dth/day of headroom. Monitor later cycles rather than inferring displacement.`,
    confirmation_needed: [
      "A TGP cycle at or near the event showing nominations still exceed forecast capacity",
      "Regional basis, flow, or rerouting evidence for the exposed zone",
    ],
    invalidation: [
      "TGP revises or removes the planned reduction",
      "Later operating capacity shows ample availability through the event window",
    ],
    confidence: "low",
  }));
}

function renderWatchItems(items) {
  el("watch-items").innerHTML = items.map((item) => `
    <article class="watch-card">
      <header>
        <h3>${escapeHtml(item.title)}</h3>
        <span class="channel ${escapeHtml(item.research_status || "monitor")}">${escapeHtml(item.market_channel)}</span>
      </header>
      <p>${escapeHtml(item.scenario)}</p>
      <div class="watch-checks">
        <div><strong>Confirm with</strong><ul>${listItems(item.confirmation_needed?.slice(0, 2), "A newer operator update")}</ul></div>
        <div><strong>Invalidated by</strong><ul>${listItems(item.invalidation?.slice(0, 2), "The source condition resolves")}</ul></div>
      </div>
    </article>
  `).join("") || `<p class="empty-context">No current maintenance row has a direction-matched capacity comparison.</p>`;
}

function storageFact(packet, geography, metric) {
  return (packet.eia_storage_context || []).find((item) => (
    item.geography === geography && item.metric === metric
  ));
}

function renderMarketDayDetail(marketState, index) {
  const days = marketState.days || [];
  const day = days[index];
  if (!day) return;
  state.marketDayIndex = index;
  document.querySelectorAll("[data-market-day-index]").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.marketDayIndex) === index);
  });
  const gap = Number(day.largest_conditional_shortfall_dth_per_day || 0);
  const threshold = Number(marketState.review_threshold_dth_per_day || 50_000);
  const status = gap >= threshold
    ? `Above the ${capacity(threshold)} Dth/day review screen`
    : gap > 0
      ? `Modeled gap remains below the ${capacity(threshold)} Dth/day review screen`
      : "No modeled unchanged-schedule gap";
  const weatherDay = (marketState.weather_by_date || []).find((item) => item.date === day.date);
  const weatherLine = weatherDay?.anchors?.length
    ? weatherDay.anchors.map((anchor) => (
      `${anchor.geography}: ${Number(anchor.hdd_65 || 0).toFixed(1)} HDD / ${Number(anchor.cdd_65 || 0).toFixed(1)} CDD`
    )).join(" · ")
    : "NWS demand weather does not extend to this date";
  const contributors = (day.top_constraints || []).map((item) => `
    <button class="day-constraint" data-open-market-segment="${escapeHtml(item.operator_segment_id || "")}" type="button">
      <span>
        <strong>${escapeHtml(item.station_label)}</strong>
        <small>${escapeHtml(item.tgp_zone || "Zone unavailable")}${item.segment_states ? ` · ${escapeHtml(item.segment_states)}` : ""} · ${escapeHtml(item.outage_flow_direction || "Direction unavailable")}</small>
      </span>
      <span class="constraint-values">
        <b>${escapeHtml(capacity(item.conditional_scheduled_shortfall_dth_per_day))} gap</b>
        <small>${escapeHtml(capacity(item.gross_reduction_dth_per_day))} planned reduction</small>
      </span>
    </button>
  `).join("");
  el("impact-day-detail").innerHTML = `
    <div class="day-detail-heading">
      <div>
        <span>${escapeHtml(dateLabel(day.date))}</span>
        <strong>${escapeHtml(status)}</strong>
      </div>
      <p>${escapeHtml(wholeNumber(day.affected_segment_count))} affected segments · ${escapeHtml(day.affected_zones || "No active zones")}</p>
    </div>
    <p class="day-weather-context">${escapeHtml(weatherLine)}</p>
    <div class="day-constraints">${contributors || '<p class="empty-context">No active TGP maintenance row on this date.</p>'}</div>
    <p class="day-detail-note">Rows are ranked by conditional schedule gap, then planned reduction. Select a row to inspect its source calculation.</p>
  `;
}

function renderMarketTimeline(marketState) {
  const days = marketState.days || [];
  if (!days.length) {
    el("impact-timeline").innerHTML = '<p class="empty-context">No daily TGP outlook is available.</p>';
    return;
  }
  const maxVolume = Math.max(1, ...days.flatMap((day) => [
    Number(day.largest_single_reduction_dth_per_day || 0),
    Number(day.largest_conditional_shortfall_dth_per_day || 0),
  ]));
  const weatherByDate = new Map(
    (marketState.weather_by_date || []).map((item) => [item.date, item.anchors || []]),
  );
  el("impact-timeline").innerHTML = days.map((day, index) => {
    const planned = Number(day.largest_single_reduction_dth_per_day || 0);
    const gap = Number(day.largest_conditional_shortfall_dth_per_day || 0);
    const [year, month, date] = day.date.split("-").map(Number);
    const label = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" })
      .format(new Date(Date.UTC(year, month - 1, date)));
    const dayNumber = new Intl.DateTimeFormat("en-US", { day: "numeric", timeZone: "UTC" })
      .format(new Date(Date.UTC(year, month - 1, date)));
    const monthLabel = index === 0 || date === 1
      ? new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" })
        .format(new Date(Date.UTC(year, month - 1, date)))
      : "";
    const weatherAnchors = weatherByDate.get(day.date) || [];
    const weatherCandidates = weatherAnchors.flatMap((anchor) => [
      { type: "H", value: Number(anchor.hdd_65 || 0), geography: anchor.geography },
      { type: "C", value: Number(anchor.cdd_65 || 0), geography: anchor.geography },
    ]).sort((left, right) => right.value - left.value);
    const leadingWeather = weatherCandidates[0];
    const weatherLabel = leadingWeather && leadingWeather.value > 0
      ? `${leadingWeather.type}${Math.round(leadingWeather.value)}`
      : "";
    const weatherTitle = weatherAnchors.map((anchor) => (
      `${anchor.geography}: ${Number(anchor.hdd_65 || 0).toFixed(1)} HDD, ${Number(anchor.cdd_65 || 0).toFixed(1)} CDD`
    )).join("; ");
    return `
      <button class="timeline-day ${escapeHtml(day.screen_state)}" data-market-day-index="${index}" type="button"
        title="${escapeHtml(label)} · ${escapeHtml(capacity(planned))} planned reduction · ${escapeHtml(capacity(gap))} conditional gap${weatherTitle ? ` · ${escapeHtml(weatherTitle)}` : ""}"
        aria-label="${escapeHtml(label)}, ${escapeHtml(capacity(planned))} planned reduction, ${escapeHtml(capacity(gap))} conditional schedule gap">
        <span class="timeline-bars" aria-hidden="true">
          <i class="planned" style="height:${Math.max(planned ? 3 : 0, (planned / maxVolume) * 100).toFixed(1)}%"></i>
          <i class="gap" style="height:${Math.max(gap ? 3 : 0, (gap / maxVolume) * 100).toFixed(1)}%"></i>
        </span>
        <span class="timeline-date"><small>${escapeHtml(monthLabel)}</small><b>${escapeHtml(dayNumber)}</b></span>
        <span class="weather-mark ${leadingWeather ? leadingWeather.type === "H" ? "hdd" : "cdd" : "unavailable"}">${escapeHtml(weatherLabel || "—")}</span>
      </button>
    `;
  }).join("");
  const peakDate = marketState.summary?.peak_day?.date;
  const initialIndex = Math.max(0, days.findIndex((day) => day.date === peakDate));
  renderMarketDayDetail(marketState, initialIndex);
}

function tradeGateLabel(gate) {
  return {
    transport_setup: "Transport setup",
    demand_overlap: "Demand overlap",
    inventory_backdrop: "Inventory backdrop",
    regional_price_confirmation: "Regional price",
    flow_or_rerouting_confirmation: "Flow & rerouting",
  }[gate] || gate.replaceAll("_", " ");
}

function renderTradePicture(marketState) {
  const picture = marketState.tradable_market_picture;
  if (!picture) {
    el("trade-picture").innerHTML = '<p class="empty-context">The tradable-market evidence path is unavailable.</p>';
    return;
  }
  const gates = (picture.gates || []).map((gate) => `
    <article class="trade-gate ${escapeHtml(gate.status)}">
      <span class="gate-status">${escapeHtml(gate.status.replaceAll("_", " "))}</span>
      <strong>${escapeHtml(tradeGateLabel(gate.gate))}</strong>
      <p>${escapeHtml(gate.finding)}</p>
    </article>
  `).join("");
  el("trade-picture").innerHTML = `
    <div class="trade-picture-heading">
      <div>
        <p class="eyebrow">Tradable market picture</p>
        <h3 id="trade-picture-heading">${escapeHtml(picture.headline)}</h3>
      </div>
      <span class="trade-status">${escapeHtml(picture.status.replaceAll("_", " "))}</span>
    </div>
    <div class="trade-gates">${gates}</div>
    <div class="market-expression">
      <div><span>Primary watch</span><strong>${escapeHtml(picture.market_expression.primary_watch)}</strong></div>
      <div><span>Henry Hub role</span><strong>${escapeHtml(picture.market_expression.henry_hub)}</strong></div>
      <p>${escapeHtml(picture.market_expression.current_conclusion)}</p>
    </div>
  `;
}

function renderBalanceBridge(packet, marketState) {
  marketState = marketState || packet.daily_market_state || {};
  const summary = marketState.summary || {};
  const current = summary.current_day || {};
  const near = summary.near_term_peak || {};
  const forward = summary.forward_peak || {};
  const lower48 = storageFact(packet, "Lower 48", "Storage vs 5-year average");
  const east = storageFact(packet, "East", "Storage vs 5-year average");
  const southCentral = storageFact(packet, "South Central", "Storage vs 5-year average");
  const spot = (packet.benchmark_context || []).find((item) => item.observation_type === "physical_spot");
  const weather = [...(packet.weather_summary || [])].sort((left, right) => (
    Math.max(Number(right.total_hdd_65 || 0), Number(right.total_cdd_65 || 0))
    - Math.max(Number(left.total_hdd_65 || 0), Number(left.total_cdd_65 || 0))
  ))[0];
  const weatherLead = weather
    ? Number(weather.total_hdd_65 || 0) > Number(weather.total_cdd_65 || 0)
      ? `${Number(weather.total_hdd_65).toFixed(1)} HDD in ${weather.geography}`
      : `${Number(weather.total_cdd_65).toFixed(1)} CDD in ${weather.geography}`
      : "Unavailable";
  el("balance-heading").textContent = summary.headline || "Current TGP schedules fit within the maintenance outlook";
  el("balance-summary").textContent = summary.explanation || "No daily TGP impact state is available.";
  el("market-state-metrics").innerHTML = `
    <article>
      <span>Today</span>
      <strong>${escapeHtml(capacity(current.largest_conditional_shortfall_dth_per_day || 0))}</strong>
      <p>Largest single schedule gap · ${escapeHtml(wholeNumber(current.affected_segment_count || 0))} segments under maintenance</p>
    </article>
    <article>
      <span>Next seven days</span>
      <strong>${escapeHtml(capacity(near.largest_conditional_shortfall_dth_per_day || 0))}</strong>
      <p>Peak single gap${near.date ? ` · ${escapeHtml(dateLabel(near.date))}` : ""}${near.peak_zone ? ` · ${escapeHtml(near.peak_zone)}` : ""}</p>
    </article>
    <article>
      <span>30-day peak</span>
      <strong>${escapeHtml(capacity(forward.largest_conditional_shortfall_dth_per_day || 0))}</strong>
      <p>${forward.date ? `${escapeHtml(dateLabel(forward.date))} · ` : ""}${escapeHtml(forward.peak_zone || "Zone unavailable")}${forward.peak_segment_states ? ` · ${escapeHtml(forward.peak_segment_states)}` : ""}</p>
    </article>
  `;
  renderMarketTimeline(marketState);
  renderTradePicture(marketState);
  const storageDirection = lower48
    ? Number(lower48.value) > 0 ? "above" : Number(lower48.value) < 0 ? "below" : "at"
    : null;
  el("balance-cards").innerHTML = `
    <article>
      <span>Northeast demand</span>
      <strong>${escapeHtml(weatherLead)}</strong>
      <p>Named NWS anchors frame heating or cooling pressure; they are not a pipeline-wide demand forecast.</p>
    </article>
    <article>
      <span>Inventory cushion</span>
      <strong>${lower48 ? `${Math.abs(Number(lower48.value)).toFixed(1)}% ${storageDirection}` : "Unavailable"}</strong>
      <p>${lower48 ? `Lower 48 storage versus its five-year average${east ? `; East is ${Math.abs(Number(east.value)).toFixed(1)}% ${Number(east.value) >= 0 ? "above" : "below"}` : ""}.` : "No current EIA storage comparison."}</p>
    </article>
    <article>
      <span>Physical benchmark</span>
      <strong>${spot ? `$${Number(spot.value).toFixed(2)}/MMBtu` : "Unavailable"}</strong>
      <p>${spot ? `Henry Hub observation ${escapeHtml(dateLabel(spot.period_start_utc))}. ` : ""}This is national context—not confirmation of TGP regional basis.</p>
    </article>
  `;
  el("balance-limit").textContent = `${marketState.aggregation_warning || "Overlapping station constraints are not additive."} Regional cash basis, measured flows, and rerouting remain the evidence needed to confirm price impact.`;
}

async function loadResearchBrief() {
  const [data, marketState] = await Promise.all([
    fetchJson("/api/research-brief"),
    fetchJson("/api/market-state"),
  ]);
  state.research = data;
  state.marketState = marketState;
  const packet = data.packet;
  const currentMemo = data.memo_status === "current" ? data.memo?.memo : null;
  const priorMemoAvailable = Boolean(data.memo?.memo && !currentMemo);
  const stale = packet.freshness.is_stale;
  const memoStatus = el("memo-status");
  const freshness = el("data-freshness");
  if (freshness) {
    freshness.textContent = `${stale ? "TGP capacity stale · as of" : "TGP capacity as of"} ${timestampLabel(packet.freshness.latest_capacity_source_posted_at_utc)}`;
    freshness.classList.toggle("stale", stale);
  }

  if (currentMemo) {
    memoStatus.textContent = stale ? "Analyst note · stale source" : "Analyst note · current";
    memoStatus.classList.toggle("stale", stale);
    el("brief-headline").textContent = analystText(currentMemo.headline);
    el("brief-summary").textContent = analystText(currentMemo.plain_english_summary);
    el("brief-why").textContent = analystText(currentMemo.why_it_matters);
  } else {
    const scenarios = (packet.transport_impacts || []).filter((item) => item.research_status === "research_scenario");
    const mapped = (packet.transport_impacts || []).filter((item) => item.research_status !== "no_trade_mapping");
    memoStatus.textContent = priorMemoAvailable
      ? "Current calculated view · AI refresh pending"
      : stale
        ? "Current calculated view · stale source"
        : "Current calculated view";
    memoStatus.classList.toggle("stale", stale || priorMemoAvailable);
    el("brief-headline").textContent = scenarios.length
      ? `${scenarios.length} TGP maintenance rows create a conditional scheduled-shortfall scenario`
      : `${mapped.length} forward-maintenance rows now have direction-matched capacity context`;
    el("brief-summary").textContent = `Each maintenance limit is compared with the most recently captured schedule moving in the same TGP direction. The comparison shows where unchanged transport demand would exceed forecast capacity.`;
    el("brief-why").textContent = scenarios.length
      ? `These rows merit regional basis and flow research because the captured schedule would not fit if it remained unchanged. Rerouting and future nominations are still unknown, so this is not a price call.`
      : `Current captured schedules fit within the matched forecast limits. The maintenance remains worth monitoring, but current operator evidence does not establish transport displacement or a trade setup.`;
  }

  const impactRows = packet.transport_impacts || [];
  const largestShortfall = Math.max(0, ...impactRows.map((item) => Number(item.conditional_scheduled_shortfall_dth_per_day || 0)));
  const lower48Surplus = (packet.eia_storage_context || []).find((item) => item.geography === "Lower 48" && item.metric === "Storage vs 5-year average");
  const lower48Value = lower48Surplus ? Number(lower48Surplus.value) : null;
  const henryHubSpot = (packet.benchmark_context || []).find((item) => item.observation_type === "physical_spot");
  const lower48Direction = lower48Value === null
    ? ""
    : lower48Value > 0
      ? "above the five-year average"
      : lower48Value < 0
        ? "below the five-year average"
        : "at the five-year average";
  el("system-signals").innerHTML = `
    <div class="signal-row"><span>TGP capacity update</span><strong>${escapeHtml(ageLabel(packet.freshness.capacity_source_age_hours))}</strong><small>Operator posting as of ${escapeHtml(timestampLabel(packet.freshness.latest_capacity_source_posted_at_utc))}${stale ? " · refresh before a live decision" : ""}</small></div>
    <div class="signal-row"><span>Largest conditional shortfall</span><strong>${escapeHtml(capacity(largestShortfall))}</strong><small>Dth/day if the matched captured schedule is unchanged through the event</small></div>
    <div class="signal-row"><span>Henry Hub physical spot</span><strong>${henryHubSpot ? `$${Number(henryHubSpot.value).toFixed(2)}` : "Unavailable"}</strong><small>${henryHubSpot ? `${escapeHtml(henryHubSpot.provider)} · observation ${escapeHtml(dateLabel(henryHubSpot.period_start_utc))}` : "No public spot vintage has been collected yet"}</small></div>
    <div class="signal-row"><span>Lower 48 storage vs 5-year</span><strong>${lower48Surplus ? `${lower48Value > 0 ? "+" : ""}${escapeHtml(lower48Surplus.value)}%` : "Unavailable"}</strong><small>${lower48Surplus ? `${escapeHtml(lower48Direction)} · EIA week ending ${escapeHtml(dateLabel(lower48Surplus.period_start_utc))}` : "No EIA storage release is available yet"}</small></div>
  `;
  renderBalanceBridge(packet, marketState);
  renderWatchItems(currentMemo?.watch_items || deterministicWatchItems(packet));
  el("counterevidence").innerHTML = listItems(
    currentMemo?.counterevidence,
    "The capacity snapshot is not a physical-flow measurement and is now stale for live use.",
  );
  el("missing-data").innerHTML = listItems(
    currentMemo?.missing_data,
    "Regional cash basis, nominations or measured flows, rerouting evidence, and newer capacity cycles.",
  );
  const glossary = currentMemo?.glossary?.length ? currentMemo.glossary.slice(0, 5) : defaultGlossary;
  el("brief-glossary").innerHTML = glossary.map((item) => `<dt>${escapeHtml(item.term)}</dt><dd>${escapeHtml(item.definition)}</dd>`).join("");
}

function selectedReport() {
  if (!state.reports.length) return null;
  if (!state.selectedReportId) return state.reports[0];
  return state.reports.find((report) => report.notice_id === state.selectedReportId) || state.reports[0];
}

function sourceNoticeUrl(noticeId) {
  return `https://pipeline2.kindermorgan.com/Notices/NoticeDetail.aspx?code=TGP&notc_nbr=${encodeURIComponent(noticeId)}`;
}

function renderSelectedReport() {
  const report = selectedReport();
  if (!report) return;
  const isLatest = report.notice_id === state.reports[0].notice_id;
  el("vintage-eyebrow").textContent = isLatest ? "Latest TGP outage forecast" : "Selected historical TGP forecast";
  el("report-date").textContent = `Outage report · ${dateLabel(report.report_updated_on)}`;
  el("report-subtitle").textContent = `TGP notice ${report.notice_id} · ${wholeNumber(report.station_count)} stations · forecast ${dateLabel(report.first_forecast_date)}–${dateLabel(report.last_forecast_date)}`;
  el("latest-source").href = sourceNoticeUrl(report.notice_id);
  el("report-coverage").textContent = `${wholeNumber(report.station_count)} stations · ${wholeNumber(report.populated_capacity_rows)} of ${wholeNumber(report.station_period_rows)} forecast rows contain capacity values`;
  const reportQuality = el("report-quality");
  reportQuality.hidden = !report.reduction_mismatch_count;
  reportQuality.textContent = report.reduction_mismatch_count
    ? `${wholeNumber(report.reduction_mismatch_count)} operator reduction figure${report.reduction_mismatch_count === 1 ? " differs" : "s differ"} from nominal minus operating capacity`
    : "";
  el("vintage-state").textContent = isLatest
    ? `Current operator forecast · received ${timestampLabel(report.observed_at_utc)}`
    : `Historical operator forecast · published ${timestampLabel(report.posted_at_utc)} · archived ${timestampLabel(report.observed_at_utc)}`;
  el("vintage-state").classList.toggle("historical", !isLatest);
  el("return-latest").hidden = isLatest;
}

function renderReportHistory() {
  const selected = selectedReport();
  el("report-history").innerHTML = state.reports.map((report, index) => `
    <button class="report-item ${report.notice_id === selected.notice_id ? "active" : ""}" data-report-index="${index}" type="button">
      <strong>Forecast updated ${escapeHtml(dateLabel(report.report_updated_on))}</strong>
      <span>Operator notice ${escapeHtml(report.notice_id)}</span>
      <span>${escapeHtml(report.station_count)} stations · ${escapeHtml(dateLabel(report.first_forecast_date))}–${escapeHtml(dateLabel(report.last_forecast_date))}</span>
      <span>Largest reduction ${escapeHtml(capacity(report.max_reduction_dth_per_day))} Dth/day</span>
    </button>
  `).join("");
}

async function loadReports() {
  state.reports = await fetchJson("/api/reports");
  if (!state.reports.length) return;
  const dates = state.reports.map((report) => report.report_updated_on);
  el("report-range").textContent = `${state.reports.length} reports · newest first`;
  renderReportHistory();
  renderSelectedReport();
}

function marketKey(row) {
  return `${row.series_code}|${row.geography}`;
}

function marketValue(row) {
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(row.value)} ${row.unit}`;
}

async function loadMarketContext() {
  const report = selectedReport();
  const isHistorical = report && report.notice_id !== state.reports[0]?.notice_id;
  const query = isHistorical ? `?as_of=${encodeURIComponent(report.posted_at_utc)}` : "";
  const data = await fetchJson(`/api/market-context${query}`);
  if (!data.selected.length) {
    el("market-context-title").textContent = "No context available";
    el("market-context-cutoff").textContent = isHistorical
      ? `The selected forecast was posted ${dateLabel(report.posted_at_utc)}.`
      : "No benchmark, weather, or storage vintage has been loaded.";
    el("market-context").innerHTML = `<p class="empty-context">${isHistorical
      ? "No loaded market-context source was available by this report cutoff."
      : "Run the context collection to load the public market series."
    }</p>`;
    return;
  }
  el("market-context-title").textContent = isHistorical
    ? "Context available at this cutoff"
    : "Benchmark, weather & storage";
  const newestAvailable = [...data.selected]
    .sort((left, right) => String(right.available_at_utc).localeCompare(String(left.available_at_utc)))[0];
  el("market-context-cutoff").textContent = isHistorical
    ? `Only vintages available by ${timestampLabel(report.posted_at_utc)}`
    : `Latest context received ${timestampLabel(newestAvailable.available_at_utc)}`;

  const spot = data.selected.find((row) => row.observation_type === "physical_spot");
  const futures = data.selected.find((row) => row.observation_type === "futures_proxy");
  const sourceLink = (row) => row?.source_url
    ? `<a class="market-source" href="${escapeHtml(row.source_url)}" target="_blank" rel="noreferrer">${escapeHtml(row.provider || "Source")} ↗</a>`
    : escapeHtml(row?.provider || "Source unavailable");
  const benchmarkRows = [];
  if (spot) {
    benchmarkRows.push(`<div class="market-row market-benchmark">
      <strong>Henry Hub physical spot</strong>
      <b>$${escapeHtml(Number(spot.value).toFixed(2))}/MMBtu</b>
      <span>Observation ${escapeHtml(dateLabel(spot.period_start_utc))} · ${sourceLink(spot)}</span>
    </div>`);
  }
  if (futures) {
    const spread = spot ? Number(futures.value) - Number(spot.value) : null;
    const spreadText = spread === null
      ? "Physical-spot comparison unavailable"
      : `${spread >= 0 ? "+" : ""}$${spread.toFixed(2)} vs latest physical spot; not regional basis`;
    benchmarkRows.push(`<div class="market-row market-benchmark">
      <strong>Front-month futures proxy</strong>
      <b>$${escapeHtml(Number(futures.value).toFixed(2))}/MMBtu</b>
      <span>${escapeHtml(futures.vintage || "Rolling NG=F contract")} · ${escapeHtml(spreadText)} · ${sourceLink(futures)}</span>
    </div>`);
  }

  const weatherByAnchor = new Map();
  data.selected.filter((row) => row.observation_type === "weather_forecast").forEach((row) => {
    if (!weatherByAnchor.has(row.geography)) weatherByAnchor.set(row.geography, []);
    weatherByAnchor.get(row.geography).push(row);
  });
  const weatherRows = [...weatherByAnchor.entries()].map(([geography, rows]) => {
    const hdd = rows.filter((row) => row.metric === "Forecast HDD").reduce((sum, row) => sum + Number(row.value), 0);
    const cdd = rows.filter((row) => row.metric === "Forecast CDD").reduce((sum, row) => sum + Number(row.value), 0);
    const temperatures = rows.filter((row) => row.metric === "Forecast mean temperature");
    const mean = temperatures.length
      ? temperatures.reduce((sum, row) => sum + Number(row.value), 0) / temperatures.length
      : null;
    const dayCount = Math.max(
      rows.filter((row) => row.metric === "Forecast HDD").length,
      rows.filter((row) => row.metric === "Forecast CDD").length,
    );
    const source = rows[0];
    return `<div class="market-row market-weather">
      <strong>${escapeHtml(geography)} · ${escapeHtml(dayCount)} complete forecast days</strong>
      <b>${escapeHtml(hdd.toFixed(1))} HDD · ${escapeHtml(cdd.toFixed(1))} CDD</b>
      <span>${mean === null ? "Mean temperature unavailable" : `${mean.toFixed(1)}°F average`} · 65°F base · ${sourceLink(source)}</span>
    </div>`;
  });

  const storage = data.selected.filter((row) => (
    row.series_code.startsWith("EIA_WNGSR:")
    && ["Lower 48", "East", "South Central"].includes(row.geography)
    && ["Working gas storage", "Weekly storage change", "Storage vs 5-year average"].includes(row.metric)
  ));
  const latestByKey = new Map(data.latest.map((row) => [marketKey(row), row]));
  const grouped = new Map();
  storage.forEach((row) => {
    if (!grouped.has(row.geography)) grouped.set(row.geography, {});
    grouped.get(row.geography)[row.metric] = row;
  });
  const geographyOrder = ["Lower 48", "East", "South Central"];
  const storageRows = geographyOrder.filter((geography) => grouped.has(geography)).map((geography) => {
    const rows = grouped.get(geography);
    const working = rows["Working gas storage"];
    const change = rows["Weekly storage change"];
    const versusAverage = rows["Storage vs 5-year average"];
    const changePrefix = Number(change?.value) > 0 ? "+" : "";
    const averagePrefix = Number(versusAverage?.value) > 0 ? "+" : "";
    const latest = working ? latestByKey.get(marketKey(working)) : null;
    const comparison = isHistorical && latest && latest.available_at_utc !== working.available_at_utc
      ? `<small>Current release: ${escapeHtml(marketValue(latest))}</small>`
      : "";
    return `<div class="market-row market-region">
      <strong>${escapeHtml(geography)} storage</strong>
      <b>${working ? escapeHtml(marketValue(working)) : "—"}</b>
      <span>${change ? `${changePrefix}${escapeHtml(marketValue(change))} this week` : "Weekly change unavailable"} · ${versusAverage ? `${averagePrefix}${escapeHtml(versusAverage.value)}% vs 5-year average` : "5-year comparison unavailable"}</span>
      ${comparison}
    </div>`;
  });
  const sections = [];
  if (benchmarkRows.length) sections.push(`<div class="market-section-label">Henry Hub benchmark</div>${benchmarkRows.join("")}`);
  if (weatherRows.length) sections.push(`<div class="market-section-label">Northeast demand weather</div>${weatherRows.join("")}`);
  if (storageRows.length) sections.push(`<div class="market-section-label">Inventory backdrop</div>${storageRows.join("")}`);
  el("market-context").innerHTML = sections.join("") || '<p class="empty-context">Configured context series are not available for this cutoff.</p>';
}

const columns = {
  impacts: `
    <tr><th>Event / status</th><th>Station / mapping</th><th class="number">Gross reduction</th><th class="number">Baseline scheduled</th><th class="number">Conditional shortfall</th><th>Zone / direction</th></tr>`,
  constraints: `
    <tr><th>Period</th><th>Station / segment</th><th class="number">Reduction</th><th class="number">Operating / nominal</th><th>Direction</th><th>Operator explanation</th></tr>`,
  capacity: `
    <tr><th>Gas day / type</th><th>Location / segment</th><th class="number">Operating</th><th class="number">Scheduled</th><th class="number">Available</th><th>Direction / zone</th></tr>`,
  revisions: `
    <tr><th>Period</th><th>Station / segment</th><th class="number">Capacity change</th><th class="number">Prior → current</th><th>Direction</th><th>Operator explanation</th></tr>`,
  notices: `
    <tr><th>Posted</th><th>Notice</th><th>Status</th><th>Subject</th><th>Prior</th><th>Excerpt</th></tr>`,
};

function impactStatusLabel(status) {
  return {
    research_scenario: "Research scenario",
    monitor: "Monitor",
    no_trade_mapping: "No trade mapping",
  }[status] || status;
}

function renderImpacts(rows) {
  return rows.map((row, index) => `
    <tr data-index="${index}">
      <td>${escapeHtml(period(row))}<br><span class="tag ${escapeHtml(row.research_status)}">${escapeHtml(impactStatusLabel(row.research_status))}</span></td>
      <td class="station">${escapeHtml(row.station_label)}<br><span class="muted">${row.capacity_location_name ? `Matched ${escapeHtml(row.capacity_location_name)}` : "No unique capacity-row match"}</span></td>
      <td class="number impact-negative">${escapeHtml(capacity(row.gross_reduction_dth_per_day))}</td>
      <td class="number">${escapeHtml(capacity(row.baseline_scheduled_quantity_dth_per_day))}</td>
      <td class="number ${row.conditional_scheduled_shortfall_dth_per_day > 0 ? "impact-negative" : ""}">${escapeHtml(capacity(row.conditional_scheduled_shortfall_dth_per_day))}</td>
      <td>${escapeHtml(row.tgp_zone || "—")}<br><span class="muted">${escapeHtml(row.outage_flow_direction || "—")} → ${escapeHtml(row.capacity_flow_direction || "—")}</span></td>
    </tr>
  `).join("");
}

const zoneColors = {
  Z0: "#466b78",
  ZL: "#317f79",
  Z1: "#56a28e",
  Z2: "#75ad76",
  Z3: "#c5a245",
  Z4: "#d47d43",
  Z5: "#a65a54",
  Z6: "#775b82",
};

function mapProject(longitude, latitude) {
  const width = 900;
  const height = 520;
  const padding = 24;
  const x = padding + ((Number(longitude) + 102) / 37) * (width - padding * 2);
  const y = padding + ((48 - Number(latitude)) / 24) * (height - padding * 2);
  return [x, y];
}

function fittedMapViewBox(segments) {
  const points = segments.filter((segment) => segment.latitude !== null && segment.longitude !== null)
    .map((segment) => mapProject(segment.longitude, segment.latitude));
  if (!points.length) return [0, 0, 900, 520];
  const xs = points.map(([x]) => x);
  const ys = points.map(([, y]) => y);
  const centerX = (Math.min(...xs) + Math.max(...xs)) / 2;
  const centerY = (Math.min(...ys) + Math.max(...ys)) / 2;
  let width = Math.max(220, Math.max(...xs) - Math.min(...xs) + 110);
  let height = Math.max(130, Math.max(...ys) - Math.min(...ys) + 90);
  const aspect = 900 / 520;
  if (width / height < aspect) width = height * aspect;
  else height = width / aspect;
  width = Math.min(900, width);
  height = Math.min(520, height);
  const x = Math.max(0, Math.min(900 - width, centerX - width / 2));
  const y = Math.max(0, Math.min(520 - height, centerY - height / 2));
  return [x, y, width, height];
}

function applyMapViewBox() {
  el("network-map").setAttribute("viewBox", state.mapViewBox.map((value) => Number(value).toFixed(1)).join(" "));
}

function zoomMap(multiplier) {
  const [x, y, width, height] = state.mapViewBox;
  const nextWidth = Math.max(120, Math.min(900, width * multiplier));
  const nextHeight = Math.max(70, Math.min(520, height * multiplier));
  const centerX = x + width / 2;
  const centerY = y + height / 2;
  state.mapViewBox = [
    Math.max(0, Math.min(900 - nextWidth, centerX - nextWidth / 2)),
    Math.max(0, Math.min(520 - nextHeight, centerY - nextHeight / 2)),
    nextWidth,
    nextHeight,
  ];
  applyMapViewBox();
}

function ringPath(ring) {
  return ring.map((coordinate, index) => {
    const [x, y] = mapProject(coordinate[0], coordinate[1]);
    return `${index ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ") + " Z";
}

function geometryPath(geometry) {
  if (!geometry) return "";
  if (geometry.type === "Polygon") return geometry.coordinates.map(ringPath).join(" ");
  if (geometry.type === "MultiPolygon") {
    return geometry.coordinates.flatMap((polygon) => polygon.map(ringPath)).join(" ");
  }
  return "";
}

function stringMatches(values, query) {
  return values.some((value) => String(value ?? "").toLowerCase().includes(query));
}

const mapLayerDefinitions = {
  risk: {
    title: "Transport risk",
    description: "Where the latest captured net schedule would exceed forecast maintenance capacity if it remained unchanged.",
    legend: [
      ["#c7ced1", "No modeled gap"],
      ["#f2d49a", "Under 50k Dth/day"],
      ["#eda15b", "50–250k"],
      ["#dc653e", "250–500k"],
      ["#a52f2f", "500k+"],
    ],
  },
  reduction: {
    title: "All maintenance",
    description: "Maximum planned capacity reduction on each segment, whether or not the captured schedule indicates transport pressure.",
    legend: [
      ["#aeb8b5", "No reported reduction"],
      ["#f2d49a", "Under 10%"],
      ["#eda15b", "10–25%"],
      ["#dc653e", "25–50%"],
      ["#a52f2f", "50%+"],
    ],
  },
  revision: {
    title: "Report revision",
    description: "Largest change in forecast operating capacity versus the preceding TGP report for the same station, direction, and forecast period.",
    legend: [
      ["#aeb8b5", "No revision"],
      ["#b63d35", "Capacity decreased"],
      ["#e8e5d7", "Small change"],
      ["#2f8065", "Capacity improved"],
    ],
  },
  tightness: {
    title: "Current tightness",
    description: "Highest scheduled share of operating capacity among the latest directional rows for each segment.",
    legend: [
      ["#aeb8b5", "No usable capacity row"],
      ["#69a68d", "Under 60%"],
      ["#d4b45b", "60–80%"],
      ["#e18445", "80–95%"],
      ["#b63d35", "95%+"],
    ],
  },
};

function segmentMapStyle(segment) {
  if (state.mapLayer === "risk") {
    const value = Number(segment.risk_shortfall_dth_per_day);
    if (!Number.isFinite(value) || value <= 0) return { active: false, fill: "#c7ced1", stroke: "#929da2", radius: 5 };
    const fill = value >= 500_000 ? "#a52f2f" : value >= 250_000 ? "#dc653e" : value >= 50_000 ? "#eda15b" : "#f2d49a";
    const radius = Math.min(16, 8 + Math.log10(Math.max(1, value / 1000)) * 1.5);
    return { active: true, fill, stroke: "#7f332d", radius };
  }
  if (state.mapLayer === "reduction") {
    const value = Number(segment.planned_reduction_pct);
    if (segment.planned_reduction_pct === null) return { active: false, fill: "#aeb8b5", stroke: "#7f8d89", radius: 5 };
    const fill = value >= 50 ? "#a52f2f" : value >= 25 ? "#dc653e" : value >= 10 ? "#eda15b" : "#f2d49a";
    const radius = Math.min(14, 8 + Math.log10(Math.max(1, Number(segment.planned_reduction_dth_per_day) / 1000)) * 1.35);
    return { active: true, fill, stroke: "#7f332d", radius };
  }
  if (state.mapLayer === "revision") {
    const change = segment.revision_change_dth_per_day;
    if (change === null) return { active: false, fill: "#aeb8b5", stroke: "#7f8d89", radius: 5 };
    const percentage = Math.abs(Number(segment.revision_change_pct));
    const magnitude = Number.isFinite(percentage) ? percentage : Math.abs(Number(change)) / 10000;
    const strong = magnitude >= 20;
    const fill = Number(change) < 0 ? (strong ? "#9f302d" : "#d66b5e") : (strong ? "#26745d" : "#68a68e");
    const radius = Math.min(14, 8 + Math.log10(Math.max(1, Math.abs(Number(change)) / 1000)) * 1.35);
    return { active: true, fill, stroke: Number(change) < 0 ? "#742520" : "#1f5b49", radius };
  }
  const value = segment.tightness_pct;
  if (value === null) return { active: false, fill: "#aeb8b5", stroke: "#7f8d89", radius: 5 };
  const numericValue = Number(value);
  const fill = numericValue >= 95 ? "#b63d35" : numericValue >= 80 ? "#e18445" : numericValue >= 60 ? "#d4b45b" : "#69a68d";
  return { active: true, fill, stroke: "#526b64", radius: 8 };
}

function segmentLayerTitle(segment) {
  if (state.mapLayer === "risk") {
    return segment.risk_shortfall_dth_per_day === null
      ? `Segment ${segment.operator_segment_id} · no modeled schedule gap`
      : `Segment ${segment.operator_segment_id} · ${capacity(segment.risk_shortfall_dth_per_day)} Dth/day conditional gap`;
  }
  if (state.mapLayer === "reduction") {
    return segment.planned_reduction_dth_per_day === null
      ? `Segment ${segment.operator_segment_id} · no positive reduction in selected report`
      : `Segment ${segment.operator_segment_id} · ${capacity(segment.planned_reduction_dth_per_day)} Dth/day reduction (${segment.planned_reduction_pct}%)`;
  }
  if (state.mapLayer === "revision") {
    return segment.revision_change_dth_per_day === null
      ? `Segment ${segment.operator_segment_id} · no report-to-report revision`
      : `Segment ${segment.operator_segment_id} · ${capacity(segment.revision_change_dth_per_day, true)} Dth/day revision`;
  }
  return segment.tightness_pct === null
    ? `Segment ${segment.operator_segment_id} · no usable current capacity row`
    : `Segment ${segment.operator_segment_id} · ${segment.tightness_pct}% scheduled`;
}

function layerLegend() {
  const definition = mapLayerDefinitions[state.mapLayer];
  const colorMeaning = {
    risk: "largest single conditional transport gap in each zone",
    reduction: "largest planned capacity reduction as a share of nominal capacity",
    revision: "direction and size of the latest forecast-capacity revision",
    tightness: "highest scheduled share of operating capacity",
  }[state.mapLayer];
  return `
    <span class="legend-explainer"><strong>Color</strong> = ${escapeHtml(colorMeaning)}</span>
    <span class="legend-explainer"><strong>Circle size</strong> = affected segment count in the system view</span>
    <span class="legend-scale">${definition.legend.map(([color, label]) => `
      <span class="legend-item"><i class="legend-swatch" style="background:${color}"></i>${escapeHtml(label)}</span>
    `).join("")}</span>`;
}

function mapMetricValue(segment) {
  if (state.mapLayer === "risk") return Number(segment.risk_shortfall_dth_per_day || 0);
  if (state.mapLayer === "reduction") return Number(segment.planned_reduction_dth_per_day || 0);
  if (state.mapLayer === "revision") return Math.abs(Number(segment.revision_change_dth_per_day || 0));
  return Number(segment.tightness_pct || 0);
}

function mapMetricLabel(value, segment = null) {
  if (state.mapLayer === "risk") return `${capacity(value)} Dth/day max gap`;
  if (state.mapLayer === "reduction") return `${capacity(value)} Dth/day max planned reduction`;
  if (state.mapLayer === "revision") {
    const signed = segment?.revision_change_dth_per_day ?? value;
    return `${capacity(signed, true)} Dth/day largest revision`;
  }
  return `${Number(value).toFixed(1)}% scheduled at the tightest segment`;
}

function segmentMapZones(segment) {
  if (state.mapLayer === "risk" && segment.risk_zone) return [segment.risk_zone];
  return segment.zones || [];
}

function buildZoneSummaries(data) {
  const byZone = new Map();
  const ensure = (zone) => {
    if (!byZone.has(zone)) {
      byZone.set(zone, {
        zone,
        coordinateCount: 0,
        latitudeTotal: 0,
        longitudeTotal: 0,
        segmentIds: new Set(),
        activeSegmentIds: new Set(),
        peakValue: 0,
        peakSegment: null,
      });
    }
    return byZone.get(zone);
  };
  data.counties.forEach((county) => {
    county.zones.filter((zone) => zoneColors[zone]).forEach((zone) => {
      const item = ensure(zone);
      item.coordinateCount += 1;
      item.latitudeTotal += Number(county.latitude);
      item.longitudeTotal += Number(county.longitude);
    });
  });
  data.segments.forEach((segment) => {
    segmentMapZones(segment).filter((zone) => zoneColors[zone]).forEach((zone) => {
      const item = ensure(zone);
      item.segmentIds.add(segment.operator_segment_id);
      const style = segmentMapStyle(segment);
      if (!style.active) return;
      item.activeSegmentIds.add(segment.operator_segment_id);
      const value = mapMetricValue(segment);
      if (!item.peakSegment || value > item.peakValue) {
        item.peakValue = value;
        item.peakSegment = segment;
      }
    });
  });
  return [...byZone.values()].filter((item) => item.coordinateCount).map((item) => ({
    ...item,
    latitude: item.latitudeTotal / item.coordinateCount,
    longitude: item.longitudeTotal / item.coordinateCount,
    segmentCount: item.segmentIds.size,
    activeSegmentCount: item.activeSegmentIds.size,
  })).sort((left, right) => right.peakValue - left.peakValue || right.activeSegmentCount - left.activeSegmentCount);
}

function zoneMapStyle(zone) {
  if (!zone.peakSegment) return { fill: "#d5dadd", stroke: "#98a2a7", radius: 16, active: false };
  const peakStyle = segmentMapStyle(zone.peakSegment);
  return {
    ...peakStyle,
    radius: Math.min(42, 22 + Math.sqrt(zone.activeSegmentCount) * 6),
    active: true,
  };
}

function mapSourceCitation(data) {
  return `<div class="map-citation">
    <span>Geographic reference</span>
    <a href="${escapeHtml(data.sources.operator_segment_pin_map)}" target="_blank" rel="noreferrer">Verify connections on TGP's Segment / PIN map ↗</a>
    <small>Zone and segment markers are approximate analytical anchors, not surveyed pipe geometry.</small>
  </div>`;
}

function mapOverview(data, zones) {
  const definition = mapLayerDefinitions[state.mapLayer];
  const active = zones.filter((zone) => zone.activeSegmentCount);
  const leaders = active.slice(0, 2).map((zone) => zone.zone).join(" and ");
  const headline = !leaders
    ? "No active zone signal"
    : state.mapLayer === "risk"
      ? `Transport risk concentrates in ${leaders}`
      : state.mapLayer === "reduction"
        ? `The largest maintenance reductions concentrate in ${leaders}`
        : state.mapLayer === "revision"
          ? `The largest forecast revisions concentrate in ${leaders}`
          : `Current scheduling pressure concentrates in ${leaders}`;
  return `
    <p class="eyebrow">${escapeHtml(definition.title)} · system view</p>
    <h3>${escapeHtml(headline)}</h3>
    <p>${escapeHtml(definition.description)} Large circles summarize zones; select one to reveal its contributing segments.</p>
    <div class="zone-ranking">
      ${active.slice(0, 6).map((zone, index) => `<button data-map-zone="${escapeHtml(zone.zone)}" type="button">
        <span>${index + 1}</span>
        <strong>${escapeHtml(zone.zone)}</strong>
        <b>${escapeHtml(mapMetricLabel(zone.peakValue, zone.peakSegment))}</b>
        <small>${escapeHtml(zone.activeSegmentCount)} affected segment${zone.activeSegmentCount === 1 ? "" : "s"}</small>
      </button>`).join("") || `<p class="empty-context">No zones have an active value for this view.</p>`}
    </div>
    <p class="map-detail-note">Zone values show the largest single segment condition, not a sum. A segment can cross more than one county, and the same gas can traverse multiple segments.</p>
    ${mapSourceCitation(data)}
  `;
}

function mapZoneOverview(data, zone, segments) {
  const active = segments.filter((segment) => segmentMapStyle(segment).active)
    .sort((left, right) => mapMetricValue(right) - mapMetricValue(left));
  return `
    <button class="map-back-button" data-map-back type="button">← All TGP zones</button>
    <p class="eyebrow">${escapeHtml(mapLayerDefinitions[state.mapLayer].title)} · ${escapeHtml(zone.zone)}</p>
    <h3>${escapeHtml(zone.activeSegmentCount)} affected segment${zone.activeSegmentCount === 1 ? "" : "s"} in ${escapeHtml(zone.zone)}</h3>
    <p>${zone.peakSegment ? `The largest single condition is ${escapeHtml(mapMetricLabel(zone.peakValue, zone.peakSegment))}.` : "No active condition is present in this zone."} Select a labeled segment on the map for dates, direction, and operator evidence.</p>
    <div class="zone-segment-list">
      ${active.slice(0, 6).map((segment) => `<div><strong>Segment ${escapeHtml(segment.operator_segment_id)}</strong><span>${escapeHtml(mapMetricLabel(mapMetricValue(segment), segment))}</span></div>`).join("")}
    </div>
    ${mapSourceCitation(data)}
  `;
}

function renderMap() {
  const data = state.mapData;
  if (!data) return;
  const query = el("search").value.trim().toLowerCase();
  const selectedZone = el("filter").value;
  const focusZone = selectedZone === "all" ? state.mapZoneFocus : selectedZone;
  const zoneSummaries = buildZoneSummaries(data);
  const segments = data.segments.filter((segment) => {
    const zoneMatch = !focusZone || segmentMapZones(segment).includes(focusZone);
    const searchMatch = !query || stringMatches([
      segment.operator_segment_id,
      segment.risk_station_label,
      segment.planned_station_label,
      segment.planned_outage_description,
      segment.revision_station_label,
      ...segment.states,
      ...segment.counties,
      ...segment.zones,
      ...segment.sample_location_names,
    ], query);
    return zoneMatch && searchMatch;
  });

  const statePaths = (data.state_boundaries.features || []).map((feature) => {
    const name = feature.properties?.STUSAB || feature.properties?.NAME || "";
    return `<path class="map-state" d="${geometryPath(feature.geometry)}"><title>${escapeHtml(name)}</title></path>`;
  }).join("");
  const zonePoints = zoneSummaries.map((zone) => {
    const [x, y] = mapProject(zone.longitude, zone.latitude);
    const style = zoneMapStyle(zone);
    const metric = style.active ? mapMetricLabel(zone.peakValue, zone.peakSegment) : "No active condition";
    return `<g class="map-zone-group ${focusZone === zone.zone ? "selected" : ""}">
      <circle class="map-zone" data-map-type="zone" data-map-zone="${escapeHtml(zone.zone)}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${style.radius.toFixed(1)}" fill="${style.fill}" stroke="${style.stroke}"><title>${escapeHtml(`${zone.zone} · ${metric} · ${zone.activeSegmentCount} affected segments`)}</title></circle>
      <text class="map-zone-label" x="${x.toFixed(1)}" y="${(y - 2).toFixed(1)}">${escapeHtml(zone.zone)}</text>
      <text class="map-zone-value" x="${x.toFixed(1)}" y="${(y + 11).toFixed(1)}">${style.active ? escapeHtml(capacity(zone.peakValue)) : "—"}</text>
    </g>`;
  }).join("");
  const orderedSegments = [...segments].sort((left, right) => Number(segmentMapStyle(left).active) - Number(segmentMapStyle(right).active));
  const segmentPoints = orderedSegments.filter((item) => item.latitude !== null && item.longitude !== null).map((segment) => {
    const [x, y] = mapProject(segment.longitude, segment.latitude);
    const style = segmentMapStyle(segment);
    const displayRadius = Math.max(4, style.radius * 0.68);
    const index = data.segments.indexOf(segment);
    const label = style.active
      ? `<text class="map-segment-label" x="${x.toFixed(1)}" y="${(y - displayRadius - 3).toFixed(1)}">${escapeHtml(segment.operator_segment_id)}</text>`
      : "";
    return `<g class="map-segment-group">${label}<circle class="map-segment" data-map-type="segment" data-map-index="${index}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${displayRadius.toFixed(1)}" fill="${style.fill}" stroke="${style.stroke}"><title>${escapeHtml(segmentLayerTitle(segment))}</title></circle></g>`;
  }).join("");

  const showSegments = Boolean(focusZone || query);
  const mappedSegments = segments.filter((segment) => segment.latitude !== null && segment.longitude !== null);
  el("network-map").innerHTML = `${statePaths}${showSegments ? segmentPoints : zonePoints}`;
  state.mapViewBox = showSegments ? fittedMapViewBox(mappedSegments) : [0, 0, 900, 520];
  applyMapViewBox();
  el("map-legend").innerHTML = layerLegend();
  el("map-warning").textContent = data.coverage.coordinate_warning;
  const activeCount = mappedSegments.filter((segment) => segmentMapStyle(segment).active).length;
  if (showSegments) {
    const zone = zoneSummaries.find((item) => item.zone === focusZone) || {
      zone: focusZone || "Search results",
      activeSegmentCount: activeCount,
      peakSegment: null,
      peakValue: 0,
    };
    el("row-count").textContent = `${activeCount} affected · ${mappedSegments.length} segments shown`;
    el("map-inspector").innerHTML = mapZoneOverview(data, zone, mappedSegments);
  } else {
    const activeZones = zoneSummaries.filter((zone) => zone.activeSegmentCount);
    el("row-count").textContent = `${activeZones.length} active zones · ${activeZones.reduce((total, zone) => total + zone.activeSegmentCount, 0)} zone-segment links`;
    el("map-inspector").innerHTML = mapOverview(data, zoneSummaries);
  }
}

async function loadMap() {
  const report = selectedReport();
  const query = report ? `?report=${encodeURIComponent(report.notice_id)}` : "";
  state.mapData = await fetchJson(`/api/map${query}`);
  renderMap();
}

function renderMapDetail(type, index) {
  const data = state.mapData;
  const item = type === "county" ? data.counties[index] : data.segments[index];
  if (!item) return;
  if (type === "county") {
    el("map-inspector").innerHTML = `
      <p class="eyebrow">County location cluster</p>
      <h3>${escapeHtml(item.county_name)}, ${escapeHtml(item.state_abbreviation)}</h3>
      <p>This marker groups ${escapeHtml(item.location_count)} TGP locations at one county reference point.</p>
      <dl class="map-detail-grid">
        <div><dt>Flow roles</dt><dd>${escapeHtml(item.receipt_count)} receipt · ${escapeHtml(item.delivery_count)} delivery · ${escapeHtml(item.bidirectional_count)} bidirectional</dd></div>
        <div><dt>Native zones</dt><dd>${escapeHtml(item.zones.join(", ") || "Not reported")}</dd></div>
        <div><dt>Native segments</dt><dd>${escapeHtml(item.segment_ids.join(", ") || "Not reported")}</dd></div>
        <div><dt>Example locations</dt><dd>${escapeHtml(item.sample_location_names.join(" · "))}</dd></div>
      </dl>
      <p class="map-detail-note">Precision: county. These coordinates are not the physical facilities.</p>
      <a class="map-source-link" href="${escapeHtml(data.sources.operator_locations)}" target="_blank" rel="noreferrer">Open operator location export ↗</a>
    `;
    return;
  }
  let metricDetail;
  if (state.mapLayer === "risk") {
    const hasRisk = Number(item.risk_shortfall_dth_per_day) > 0;
    metricDetail = `
      <p class="eyebrow">Conditional transport risk</p>
      <h3>${escapeHtml(item.risk_station_label || `Segment ${item.operator_segment_id}`)}</h3>
      <p class="${hasRisk ? "impact-negative" : "muted"}">${hasRisk ? `${escapeHtml(capacity(item.risk_shortfall_dth_per_day))} Dth/day unchanged-schedule gap` : "No modeled schedule gap"}</p>
      <dl class="map-detail-grid">
        <div><dt>Forecast window</dt><dd>${hasRisk ? `${escapeHtml(dateLabel(item.risk_period_start))} – ${escapeHtml(dateLabel(item.risk_period_end))}` : "—"}</dd></div>
        <div><dt>Zone / direction</dt><dd>${escapeHtml(item.risk_zone || "—")} · ${escapeHtml(item.risk_flow_direction || "—")}</dd></div>
        <div><dt>Periods flagged</dt><dd>${escapeHtml(item.risk_period_count || 0)}</dd></div>
        <div><dt>Interpretation</dt><dd>Captured net schedule minus forecast maintenance capacity, floored at zero</dd></div>
      </dl>
      <p class="map-detail-note">This assumes the captured schedule persists through the event. It is not confirmed curtailment, measured flow, or a price forecast.</p>`;
  } else if (state.mapLayer === "reduction") {
    const hasReduction = item.planned_reduction_dth_per_day !== null;
    metricDetail = `
      <p class="eyebrow">Planned reduction · selected report</p>
      <h3>${escapeHtml(item.planned_station_label || `Segment ${item.operator_segment_id}`)}</h3>
      <p class="${hasReduction ? "impact-negative" : "muted"}">${hasReduction ? `${escapeHtml(capacity(item.planned_reduction_dth_per_day))} Dth/day · ${escapeHtml(item.planned_reduction_pct)}% reduction` : "No positive reduction reported for this segment"}</p>
      <dl class="map-detail-grid">
        <div><dt>Forecast window</dt><dd>${hasReduction ? `${escapeHtml(dateLabel(item.planned_first_period_start))} – ${escapeHtml(dateLabel(item.planned_last_period_end))}` : "—"}</dd></div>
        <div><dt>Operating / nominal</dt><dd>${hasReduction ? `${escapeHtml(capacity(item.planned_operating_capacity_dth_per_day))} / ${escapeHtml(capacity(item.planned_nominal_capacity_dth_per_day))} Dth/day` : "—"}</dd></div>
        <div><dt>Report direction</dt><dd>${escapeHtml(item.planned_flow_direction || "Not reported")}</dd></div>
        <div><dt>Operator explanation</dt><dd>${escapeHtml(item.planned_outage_description || "No active reduction explanation")}</dd></div>
      </dl>`;
  } else if (state.mapLayer === "revision") {
    const hasRevision = item.revision_change_dth_per_day !== null;
    const improved = Number(item.revision_change_dth_per_day) > 0;
    metricDetail = `
      <p class="eyebrow">Report-to-report revision</p>
      <h3>${escapeHtml(item.revision_station_label || `Segment ${item.operator_segment_id}`)}</h3>
      <p class="${hasRevision ? (improved ? "impact-positive" : "impact-negative") : "muted"}">${hasRevision ? `${escapeHtml(capacity(item.revision_change_dth_per_day, true))} Dth/day ${improved ? "capacity improvement" : "capacity decrease"}${item.revision_change_pct === null ? "" : ` · ${escapeHtml(item.revision_change_pct)}%`}` : "No non-zero revision for this segment"}</p>
      <dl class="map-detail-grid">
        <div><dt>Prior → current</dt><dd>${hasRevision ? `${escapeHtml(capacity(item.revision_prior_capacity_dth_per_day))} → ${escapeHtml(capacity(item.revision_current_capacity_dth_per_day))} Dth/day` : "—"}</dd></div>
        <div><dt>Forecast period</dt><dd>${hasRevision ? `${escapeHtml(dateLabel(item.revision_period_start))} – ${escapeHtml(dateLabel(item.revision_period_end))}` : "—"}</dd></div>
        <div><dt>Changed rows</dt><dd>${escapeHtml(item.worsened_period_count || 0)} worsened · ${escapeHtml(item.improved_period_count || 0)} improved</dd></div>
        <div><dt>Report direction</dt><dd>${escapeHtml(item.revision_flow_direction || "Not reported")}</dd></div>
      </dl>`;
  } else {
    const hasCapacity = item.tightness_pct !== null;
    metricDetail = `
      <p class="eyebrow">Latest operating-capacity snapshot</p>
      <h3>Segment ${escapeHtml(item.operator_segment_id)}</h3>
      <p class="${hasCapacity && Number(item.tightness_pct) >= 95 ? "impact-negative" : "muted"}">${hasCapacity ? `${escapeHtml(item.tightness_pct)}% of operating capacity scheduled` : "No usable positive-operating-capacity row"}</p>
      <dl class="map-detail-grid">
        <div><dt>Operating</dt><dd>${hasCapacity ? `${escapeHtml(capacity(item.tightness_operating_capacity_dth_per_day))} Dth/day` : "—"}</dd></div>
        <div><dt>Scheduled / available</dt><dd>${hasCapacity ? `${escapeHtml(capacity(item.tightness_scheduled_quantity_dth_per_day))} / ${escapeHtml(capacity(item.tightness_available_capacity_dth_per_day))} Dth/day` : "—"}</dd></div>
        <div><dt>Capacity direction</dt><dd>${escapeHtml(item.tightness_flow_direction || "Not reported")}</dd></div>
        <div><dt>Source posted</dt><dd>${escapeHtml(item.capacity_source_posted_at_utc || "—")}</dd></div>
      </dl>`;
  }
  el("map-inspector").innerHTML = `
    ${metricDetail}
    <dl class="map-detail-grid">
      <div><dt>Approximate reach</dt><dd>${escapeHtml(item.states.join(", ") || "No mapped counties")} · ${escapeHtml(item.county_count)} counties</dd></div>
      <div><dt>Native zones</dt><dd>${escapeHtml(item.zones.join(", ") || "Not reported")}</dd></div>
      <div><dt>Example locations</dt><dd>${escapeHtml(item.sample_location_names.join(" · ") || "None")}</dd></div>
    </dl>
    <p class="map-detail-note">This anchor averages mapped county reference points. It is not a surveyed route, and scheduled quantity is not measured physical flow.</p>
    ${state.mapLayer !== "tightness" && item.source_url ? `<a class="map-source-link" href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">Open selected source notice ↗</a>` : ""}
    <div class="map-citation">
      <span>Network source</span>
      <a href="${escapeHtml(data.sources.operator_segment_pin_map)}" target="_blank" rel="noreferrer">Verify segment connections on TGP Segment / PIN map ↗</a>
    </div>
  `;
}

function renderConstraints(rows) {
  return rows.map((row, index) => `
    <tr data-index="${index}">
      <td>${escapeHtml(period(row))}<br><span class="tag">${escapeHtml(row.report_kind.replace("_", " "))}</span></td>
      <td class="station">${escapeHtml(row.station_label)}</td>
      <td class="number impact-negative">${escapeHtml(capacity(row.calculated_reduction_dth_per_day))}<br><span class="muted">${escapeHtml(row.reduction_pct)}%</span></td>
      <td class="number">${escapeHtml(capacity(row.operating_capacity_dth_per_day))} / ${escapeHtml(capacity(row.nominal_capacity_dth_per_day))}</td>
      <td>${escapeHtml(row.flow_direction || "—")}</td>
      <td class="reason"><span class="truncate">${escapeHtml(row.outage_description || "No outage description published")}</span></td>
    </tr>
  `).join("");
}

function renderCapacity(rows) {
  return rows.map((row, index) => {
    const tight = row.available_capacity_dth_per_day === 0;
    const kind = row.capacity_kind === "segment" ? "segment" : `${row.point_role} point`;
    return `
      <tr data-index="${index}">
        <td>${escapeHtml(dateLabel(row.gas_day))}<br><span class="tag">${escapeHtml(kind)}</span></td>
        <td class="station">${escapeHtml(row.location_name)}<br><span class="muted">Segment ${escapeHtml(row.operator_segment_id)}${row.operator_location_id ? ` · Location ${escapeHtml(row.operator_location_id)}` : ""}</span></td>
        <td class="number">${escapeHtml(capacity(row.operating_capacity_dth_per_day))}</td>
        <td class="number">${escapeHtml(capacity(row.scheduled_quantity_dth_per_day))}<br><span class="muted">${escapeHtml(row.scheduled_pct_of_operating ?? "—")}% of operating</span></td>
        <td class="number ${tight ? "impact-negative" : ""}">${escapeHtml(capacity(row.available_capacity_dth_per_day))}</td>
        <td>${escapeHtml(row.flow_direction || "—")}<br><span class="muted">Zone ${escapeHtml(row.zone || "—")}</span></td>
      </tr>
    `;
  }).join("");
}

function renderRevisions(rows) {
  return rows.map((row, index) => {
    const worsened = row.operating_capacity_change_dth_per_day < 0;
    return `
      <tr data-index="${index}">
        <td>${escapeHtml(period(row))}<br><span class="tag">${escapeHtml(row.report_kind.replace("_", " "))}</span></td>
        <td class="station">${escapeHtml(row.station_label)}</td>
        <td class="number ${worsened ? "impact-negative" : "impact-positive"}">${escapeHtml(capacity(row.operating_capacity_change_dth_per_day, true))}</td>
        <td class="number">${escapeHtml(capacity(row.prior_operating_capacity_dth_per_day))} → ${escapeHtml(capacity(row.operating_capacity_dth_per_day))}</td>
        <td>${escapeHtml(row.flow_direction || "—")}</td>
        <td class="reason"><span class="truncate">${escapeHtml(row.outage_description || "No outage description published")}</span></td>
      </tr>
    `;
  }).join("");
}

function renderNotices(rows) {
  return rows.map((row, index) => `
    <tr data-index="${index}">
      <td>${escapeHtml(dateLabel(row.posted_at_utc))}</td>
      <td class="station">${escapeHtml(row.notice_id)}</td>
      <td><span class="tag ${escapeHtml(row.status_description.toLowerCase())}">${escapeHtml(row.status_description)}</span></td>
      <td>${escapeHtml(row.subject)}</td>
      <td>${escapeHtml(row.prior_notice_id || "—")}</td>
      <td class="reason"><span class="truncate">${escapeHtml(row.notice_excerpt)}</span></td>
    </tr>
  `).join("");
}

async function loadView() {
  const isMap = state.view === "map";
  document.querySelector(".table-wrap").hidden = isMap;
  el("map-view").hidden = !isMap;
  if (isMap) {
    el("row-count").textContent = "Loading map…";
    try {
      await loadMap();
    } catch (error) {
      showError(error);
    }
    return;
  }
  const search = encodeURIComponent(el("search").value.trim());
  const filter = encodeURIComponent(el("filter").value);
  el("table-head").innerHTML = columns[state.view];
  el("table-body").innerHTML = `<tr><td colspan="6" class="loading-cell">Loading TGP records…</td></tr>`;
  try {
    let url;
    const report = selectedReport();
    const reportQuery = report ? `&report=${encodeURIComponent(report.notice_id)}` : "";
    if (state.view === "impacts") url = `/api/transport-impacts?search=${search}&status=${filter}&limit=200${reportQuery}`;
    if (state.view === "constraints") url = `/api/constraints?search=${search}&kind=${filter}&limit=200${reportQuery}`;
    if (state.view === "capacity") url = `/api/operational-capacity?search=${search}&kind=${filter}&limit=200`;
    if (state.view === "revisions") url = `/api/revisions?search=${search}&direction=${filter}&limit=200${reportQuery}`;
    if (state.view === "notices") url = `/api/notices?search=${search}&limit=200`;
    state.rows = await fetchJson(url);
    el("row-count").textContent = `${state.rows.length} ${state.view === "impacts" ? "impact rows" : "rows"}`;
    const rendered = state.view === "impacts"
      ? renderImpacts(state.rows)
      : state.view === "constraints"
      ? renderConstraints(state.rows)
      : state.view === "capacity"
        ? renderCapacity(state.rows)
      : state.view === "revisions"
        ? renderRevisions(state.rows)
        : renderNotices(state.rows);
    const emptyMessage = state.view === "impacts" && report && report.notice_id !== state.reports[0]?.notice_id
      ? "No direction-matched capacity baseline was captured for this historical report. Its raw forecast remains available under Forecast constraints."
      : "No matching records.";
    el("table-body").innerHTML = rendered || `<tr><td colspan="6" class="empty-cell">${escapeHtml(emptyMessage)}</td></tr>`;
  } catch (error) {
    showError(error);
  }
}

function configureFilter() {
  if (state.view === "impacts") {
    el("filter").innerHTML = `<option value="all">All impact states</option><option value="research_scenario">Research scenarios</option><option value="monitor">Monitor</option><option value="no_trade_mapping">No trade mapping</option>`;
    el("search").placeholder = "Search station, segment, zone, or capacity match…";
  } else if (state.view === "constraints") {
    el("filter").innerHTML = `<option value="all">All horizons</option><option value="seven_day">Seven day</option><option value="monthly">Monthly</option>`;
    el("search").placeholder = "Search station, segment, or outage…";
  } else if (state.view === "capacity") {
    el("filter").innerHTML = `<option value="all">All capacity</option><option value="segment">Segments</option><option value="delivery">Delivery points</option><option value="receipt">Receipt points</option>`;
    el("search").placeholder = "Search location, segment, zone, or native ID…";
  } else if (state.view === "map") {
    el("filter").innerHTML = `<option value="all">All zones</option><option value="Z0">Zone 0</option><option value="ZL">Zone L</option><option value="Z1">Zone 1</option><option value="Z2">Zone 2</option><option value="Z3">Zone 3</option><option value="Z4">Zone 4</option><option value="Z5">Zone 5</option><option value="Z6">Zone 6</option>`;
    el("search").placeholder = "Search county, state, segment, or location…";
  } else if (state.view === "revisions") {
    el("filter").innerHTML = `<option value="all">All revisions</option><option value="worsened">Capacity decreased</option><option value="improved">Capacity improved</option>`;
    el("search").placeholder = "Search station, segment, or outage…";
  } else {
    el("filter").innerHTML = `<option value="all">All maintenance</option>`;
    el("search").placeholder = "Search notice subject or text…";
  }
}

function drawerMeta(items) {
  return `<div class="drawer-meta">${items.map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "—")}</strong></div>`).join("")}</div>`;
}

function openAlertDrawer(alert) {
  const drawer = el("detail-drawer");
  const evidence = alert.evidence || {};
  const before = evidence.before || {};
  const after = evidence.after || {};
  const subject = evidence.subject || {};
  const scores = Object.entries(alert.score_components || {});
  const statusClass = ["worsened", "tightened"].includes(alert.current_status)
    ? "impact-negative"
    : ["improved", "relieved"].includes(alert.current_status)
      ? "impact-positive"
      : "muted";
  const beforeAfter = alert.event_type === "capacity_snapshot_change"
    ? [
      ["Operating capacity", `${capacity(before.operating_capacity_dth_per_day)} → ${capacity(after.operating_capacity_dth_per_day)} Dth/day`],
      ["Scheduled quantity", `${capacity(before.scheduled_quantity_dth_per_day)} → ${capacity(after.scheduled_quantity_dth_per_day)} Dth/day`],
      ["Available capacity", `${capacity(before.available_capacity_dth_per_day)} → ${capacity(after.available_capacity_dth_per_day)} Dth/day`],
      ["Scheduled / operating", `${before.scheduled_pct_of_operating ?? "—"}% → ${after.scheduled_pct_of_operating ?? "—"}%`],
      ["Gas day / cycle", `${before.gas_day || "—"} ${before.cycle || ""} → ${after.gas_day || "—"} ${after.cycle || ""}`],
      ["Direction", subject.flow_direction || "Not reported"],
    ]
    : alert.event_type === "outage_capacity_revision"
      ? [
        ["Forecast capacity", `${capacity(before.operating_capacity_dth_per_day)} → ${capacity(after.operating_capacity_dth_per_day)} Dth/day`],
        ["TGP notice", `${before.notice_id || "—"} → ${after.notice_id || "—"}`],
        ["Changed periods", after.changed_period_count || "—"],
        ["Forecast window", `${after.first_changed_period || "—"} → ${after.last_changed_period || "—"}`],
        ["Station", subject.station_label || "—"],
        ["Direction", subject.flow_direction || "Not reported"],
      ]
      : alert.event_type === "notice_content_revision"
        ? [
          ["Notice", subject.notice_id || "—"],
          ["Changed fields", (evidence.changed_fields || []).join(", ") || "—"],
          ["Status", `${before.status || "—"} → ${after.status || "—"}`],
          ["Effective start", `${timestampLabel(before.effective_start)} → ${timestampLabel(after.effective_start)}`],
          ["Effective end", `${timestampLabel(before.effective_end)} → ${timestampLabel(after.effective_end)}`],
          ["Required response", `${before.required_response || "—"} → ${after.required_response || "—"}`],
          ["Response deadline", `${timestampLabel(before.response_at)} → ${timestampLabel(after.response_at)}`],
        ]
      : [
        ["Notice", subject.notice_id || "—"],
        ["Status", after.status || alert.current_status],
        ["Posted", timestampLabel(after.posted_at)],
        ["Effective", `${timestampLabel(after.effective_start)} → ${timestampLabel(after.effective_end)}`],
      ];

  el("drawer-backdrop").hidden = false;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  el("drawer-content").innerHTML = `
    <p class="eyebrow">Operator change · ${escapeHtml(impactChannelLabel(alert.impact_channel))}</p>
    <h2>${escapeHtml(alert.headline)}</h2>
    <p class="${statusClass}">${escapeHtml(alert.explanation)}</p>
    <div class="why-card"><strong>Potential market impact</strong><p>${escapeHtml(alertMarketMeaning(alert))}</p></div>
    ${drawerMeta([
      ["Detected", timestampLabel(alert.decision_at_utc)],
      ["Market channel", impactChannelLabel(alert.impact_channel)],
      ["Status", alert.current_status],
    ])}
    <h3>Before → after</h3>
    ${drawerMeta(beforeAfter)}
    ${evidence.comparison_warning ? `<div class="quality-note"><strong>Comparison caveat</strong><p>${escapeHtml(evidence.comparison_warning)}</p></div>` : ""}
    <details class="drawer-method"><summary>Why this update surfaced</summary>
      <p>Screening score: ${Math.round(alert.severity_score)} / 100. This ranks source-change size, timing, and operational relevance—not price impact or affected U.S. supply.</p>
      ${drawerMeta(scores.map(([name, value]) => [name.replaceAll("_", " "), `${value} points`]))}
    </details>
    <details class="drawer-method"><summary>Source record</summary>
      <div class="evidence-text">Artifact IDs\n${escapeHtml((evidence.artifact_ids || []).join("\n") || "No artifact IDs recorded")}</div>
    </details>
    ${after.notice_text ? `<h3>Operator notice text</h3><div class="evidence-text">${escapeHtml(after.notice_text)}</div>` : ""}
    ${evidence.operator_explanation ? `<h3>Operator explanation</h3><div class="evidence-text">${escapeHtml(evidence.operator_explanation)}</div>` : ""}
    ${evidence.source_url ? `<a class="drawer-link" href="${escapeHtml(evidence.source_url)}" target="_blank" rel="noreferrer">Open source evidence ↗</a>` : ""}
  `;
}

function openDrawer(row) {
  const drawer = el("detail-drawer");
  el("drawer-backdrop").hidden = false;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  if (state.view === "notices") {
    el("drawer-content").innerHTML = `<p class="eyebrow">Notice ${escapeHtml(row.notice_id)}</p><h2>${escapeHtml(row.subject)}</h2><p class="muted">Loading full evidence…</p>`;
    Promise.all([
      fetchJson(`/api/notices/${encodeURIComponent(row.notice_id)}`),
      fetchJson(`/api/notices/${encodeURIComponent(row.notice_id)}/history`),
    ]).then(([notice, history]) => {
      const versionTimeline = history.versions.map((version) => `
        <article class="notice-version ${version.is_current_for_cutoff ? "current" : ""}">
          <span>${escapeHtml(timestampLabel(version.available_from_utc))}</span>
          <strong>${escapeHtml(version.is_current_for_cutoff ? "Current captured version" : "Prior captured version")}</strong>
          <small>${escapeHtml((version.changed_fields || []).join(", "))}</small>
        </article>
      `).join("");
      const relatedUpdates = history.related_operator_updates.length
        ? `<h3>Later operator updates referencing this notice</h3><div class="notice-version-list">${history.related_operator_updates.map((update) => `
            <article class="notice-version related">
              <span>${escapeHtml(timestampLabel(update.available_from_utc))}</span>
              <strong>${escapeHtml(update.status_description)} · Notice ${escapeHtml(update.notice_id)}</strong>
              <small>${escapeHtml(update.subject)}</small>
            </article>
          `).join("")}</div>`
        : "";
      el("drawer-content").innerHTML = `
        <p class="eyebrow">Maintenance notice ${escapeHtml(notice.notice_id)}</p>
        <h2>${escapeHtml(notice.subject)}</h2>
        ${drawerMeta([["Status", notice.status_description], ["Posted · Central", notice.posted_at_operator_local], ["Prior notice", notice.prior_notice_id], ["Required response", notice.required_response], ["Response deadline", timestampLabel(notice.response_at_utc)], ["Version available · UTC", notice.version_observed_at_utc]])}
        <h3>Point-in-time version history</h3>
        <p class="map-detail-note">${escapeHtml(history.point_in_time_rule)}</p>
        <div class="notice-version-list">${versionTimeline}</div>
        ${relatedUpdates}
        <h3>Operator text</h3>
        <div class="evidence-text">${escapeHtml(notice.notice_text)}</div>
        <a class="drawer-link" href="${escapeHtml(notice.source_url)}" target="_blank" rel="noreferrer">Open source notice ↗</a>
      `;
    }).catch((error) => { el("drawer-content").innerHTML += `<div class="error-banner">${escapeHtml(error.message)}</div>`; });
    return;
  }

  if (state.view === "impacts") {
    const evidence = row.evidence || {};
    el("drawer-content").innerHTML = `
      <p class="eyebrow">Impact translation · ${escapeHtml(impactStatusLabel(row.research_status))}</p>
      <h2>${escapeHtml(row.station_label)}</h2>
      <p class="${row.conditional_scheduled_shortfall_dth_per_day > 0 ? "impact-negative" : "muted"}">${escapeHtml(capacity(row.conditional_scheduled_shortfall_dth_per_day))} Dth/day conditional scheduled shortfall</p>
      ${drawerMeta([
        ["Forecast period", period(row)],
        ["Segment / zone", `${row.operator_segment_id || "—"} · ${row.tgp_zone || "—"}`],
        ["Direction mapping", `${row.outage_flow_direction || "—"} → ${row.capacity_flow_direction || "—"}`],
        ["Match method", row.match_method.replaceAll("_", " ")],
        ["Baseline gas day / cycle", `${dateLabel(row.baseline_gas_day)} · ${row.baseline_cycle || "—"}`],
        ["Baseline timing", row.baseline_timing.replaceAll("_", " ")],
        ["Price mapping", row.price_mapping_status],
      ])}
      <h3>Volume bridge</h3>
      ${drawerMeta([
        ["Forecast nominal", `${capacity(row.forecast_nominal_capacity_dth_per_day)} Dth/day`],
        ["Gross maintenance reduction", `${capacity(row.gross_reduction_dth_per_day)} Dth/day`],
        ["Forecast operating", `${capacity(row.forecast_operating_capacity_dth_per_day)} Dth/day`],
        ["Captured net schedule", `${capacity(row.baseline_scheduled_quantity_dth_per_day)} Dth/day`],
        ["Headroom if unchanged", `${capacity(row.forecast_headroom_vs_baseline_schedule_dth_per_day)} Dth/day`],
        ["Shortfall if unchanged", `${capacity(row.conditional_scheduled_shortfall_dth_per_day)} Dth/day`],
      ])}
      <div class="quality-note"><strong>What this means</strong><p>${escapeHtml(evidence.interpretation || "This is a conditional comparison, not measured flow or confirmed curtailment.")}</p></div>
      <div class="quality-note"><strong>Why there is no contract call</strong><p>${escapeHtml(row.price_mapping_reason)}</p></div>
      <h3>Still unresolved</h3>
      <ul>${listItems(row.unresolved_reasons, "No additional unresolved reason recorded.")}</ul>
      <details class="drawer-method"><summary>Source record</summary>
        <div class="evidence-text">Report record: ${escapeHtml(row.report_artifact_id)}\nCapacity record: ${escapeHtml(row.capacity_artifact_id || "No unique match")}</div>
      </details>
      <a class="drawer-link" href="${escapeHtml(row.source_url)}" target="_blank" rel="noreferrer">Open outage report ↗</a>
      ${evidence.direction_mapping?.operator_direction_notice_url ? `<a class="drawer-link secondary-drawer-link" href="${escapeHtml(evidence.direction_mapping.operator_direction_notice_url)}" target="_blank" rel="noreferrer">Verify direction convention ↗</a>` : ""}
      ${row.benchmark_reference_url ? `<a class="drawer-link secondary-drawer-link" href="${escapeHtml(row.benchmark_reference_url)}" target="_blank" rel="noreferrer">Open Henry Hub benchmark ↗</a>` : ""}
    `;
    return;
  }

  if (state.view === "capacity") {
    const kind = row.capacity_kind === "segment" ? "Directional segment" : `${row.point_role} point`;
    el("drawer-content").innerHTML = `
      <p class="eyebrow">Latest operating-capacity snapshot</p>
      <h2>${escapeHtml(row.location_name)}</h2>
      <p class="${row.available_capacity_dth_per_day === 0 ? "impact-negative" : "impact-positive"}">${escapeHtml(capacity(row.available_capacity_dth_per_day))} Dth/day reported available</p>
      ${drawerMeta([
        ["Type", kind],
        ["Gas day / cycle", `${dateLabel(row.gas_day)} · ${row.cycle}`],
        ["Native segment", row.operator_segment_id],
        ["Direction / zone", `${row.flow_direction || "—"} · ${row.zone || "—"}`],
        ["Operating capacity", `${capacity(row.operating_capacity_dth_per_day)} Dth/day`],
        ["Scheduled quantity", `${capacity(row.scheduled_quantity_dth_per_day)} Dth/day`],
      ])}
      <h3>How to interpret this</h3>
      <div class="evidence-text">Available capacity is operating capacity less scheduled quantity, floored at zero. It is transportation availability—not measured physical flow. TGP notes that bidirectional netting, partial paths, storage activity, exchanges, outages, and imbalance management can change this value intraday.</div>
      <p class="map-detail-note">Source posted ${escapeHtml(row.source_posted_at_utc)} · captured ${escapeHtml(row.observed_at_utc)}</p>
    `;
    return;
  }

  const isRevision = state.view === "revisions";
  const headlineValue = isRevision ? row.operating_capacity_change_dth_per_day : -row.calculated_reduction_dth_per_day;
  const headline = isRevision
    ? `${capacity(Math.abs(headlineValue))} Dth/day ${headlineValue < 0 ? "capacity decrease" : "capacity improvement"}`
    : `${capacity(row.calculated_reduction_dth_per_day)} Dth/day reduction`;
  el("drawer-content").innerHTML = `
    <p class="eyebrow">${isRevision ? "Report-to-report revision" : "Latest reported constraint"}</p>
    <h2>${escapeHtml(row.station_label)}</h2>
    <p class="${headlineValue < 0 ? "impact-negative" : "impact-positive"}">${escapeHtml(headline)}</p>
    ${drawerMeta([
      ["Forecast period", period(row)],
      ["Direction", row.flow_direction],
      ["Operating capacity", `${capacity(row.operating_capacity_dth_per_day)} Dth/day`],
      [isRevision ? "Prior capacity" : "Nominal capacity", `${capacity(isRevision ? row.prior_operating_capacity_dth_per_day : row.nominal_capacity_dth_per_day)} Dth/day`],
      ["Report date", dateLabel(row.report_updated_on)],
      ["Source notice", row.notice_id],
    ])}
    <h3>Operator explanation</h3>
    <div class="evidence-text">${escapeHtml(row.outage_description || "No outage description was published for this station-period row.")}</div>
    <a class="drawer-link" href="${escapeHtml(row.source_url)}" target="_blank" rel="noreferrer">Open source notice ↗</a>
  `;
}

function closeDrawer() {
  el("detail-drawer").classList.remove("open");
  el("detail-drawer").setAttribute("aria-hidden", "true");
  el("drawer-backdrop").hidden = true;
}

function activateView(view, { scroll = false } = {}) {
  const button = document.querySelector(`.tab[data-view="${view}"]`);
  if (!button) return;
  document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  state.view = view;
  el("report-context").hidden = !["impacts", "constraints", "revisions"].includes(view);
  configureFilter();
  loadView();
  if (scroll) el("data").scrollIntoView({ behavior: "smooth", block: "start" });
}

function activatePage(page, { activeButton = null, scroll = true } = {}) {
  state.page = page;
  document.querySelectorAll("[data-page-section]").forEach((section) => {
    section.hidden = section.dataset.pageSection.split(/\s+/).includes(page) === false;
  });
  document.querySelectorAll(".sidebar-link, .mobile-nav-link").forEach((item) => item.classList.remove("active"));
  const navSelector = page === "workspace" && activeButton?.dataset.navView
    ? `[data-nav-view="${activeButton.dataset.navView}"]`
    : `[data-nav-page="${page}"]`;
  document.querySelectorAll(`.sidebar-link${navSelector}, .mobile-nav-link${navSelector}`)
    .forEach((item) => item.classList.add("active"));
  if (page === "learn") el("learn").open = true;
  if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    const navView = button.dataset.view === "map" ? "map" : "impacts";
    const navButton = document.querySelector(`.sidebar-link[data-nav-view="${navView}"]`);
    activatePage("workspace", { activeButton: navButton, scroll: false });
    activateView(button.dataset.view);
  });
});

document.querySelectorAll("[data-nav-page]").forEach((button) => {
  button.addEventListener("click", () => activatePage(button.dataset.navPage, { activeButton: button }));
});

document.querySelectorAll("[data-nav-view]").forEach((button) => {
  button.addEventListener("click", () => {
    activatePage("workspace", { activeButton: button });
    activateView(button.dataset.navView);
  });
});

document.querySelectorAll("[data-guide-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    const selected = button.dataset.guideTab;
    document.querySelectorAll("[data-guide-tab]").forEach((item) => {
      const active = item.dataset.guideTab === selected;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll("[data-guide-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.guidePanel !== selected;
    });
  });
});

el("report-history").addEventListener("click", (event) => {
  const reportButton = event.target.closest("button[data-report-index]");
  if (!reportButton) return;
  const report = state.reports[Number(reportButton.dataset.reportIndex)];
  state.selectedReportId = report.notice_id === state.reports[0].notice_id ? null : report.notice_id;
  renderReportHistory();
  renderSelectedReport();
  const selectedHistoricalReport = state.selectedReportId !== null;
  if (selectedHistoricalReport && state.view === "impacts") {
    activateView("constraints");
    loadMarketContext().catch(showError);
  } else {
    Promise.all([loadView(), loadMarketContext()]).catch(showError);
  }
});

el("return-latest").addEventListener("click", () => {
  state.selectedReportId = null;
  renderReportHistory();
  renderSelectedReport();
  Promise.all([loadView(), loadMarketContext()]).catch(showError);
});

let searchTimer;
el("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadView, 220);
});
el("filter").addEventListener("change", () => {
  if (state.view === "map") {
    state.mapZoneFocus = el("filter").value === "all" ? null : el("filter").value;
  }
  loadView();
});
el("map-layer-switch").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-map-layer]");
  if (!button || button.dataset.mapLayer === state.mapLayer) return;
  state.mapLayer = button.dataset.mapLayer;
  document.querySelectorAll("#map-layer-switch button").forEach((item) => {
    item.classList.toggle("active", item.dataset.mapLayer === state.mapLayer);
  });
  renderMap();
});
el("map-zoom-in").addEventListener("click", () => zoomMap(0.75));
el("map-zoom-out").addEventListener("click", () => zoomMap(1.333));
el("map-zoom-reset").addEventListener("click", renderMap);
el("map-fullscreen").addEventListener("click", async () => {
  const mapView = el("map-view");
  if (!document.fullscreenElement) await mapView.requestFullscreen();
  else await document.exitFullscreen();
});
document.addEventListener("fullscreenchange", () => {
  el("map-fullscreen").textContent = document.fullscreenElement ? "Exit full screen" : "Full screen";
});
el("network-map").addEventListener("wheel", (event) => {
  event.preventDefault();
  zoomMap(event.deltaY > 0 ? 1.12 : 0.88);
}, { passive: false });
el("table-body").addEventListener("click", (event) => {
  const rowElement = event.target.closest("tr[data-index]");
  if (rowElement) openDrawer(state.rows[Number(rowElement.dataset.index)]);
});
el("alert-feed").addEventListener("click", (event) => {
  const card = event.target.closest("[data-alert-index]");
  if (card) openAlertDrawer(state.alerts[Number(card.dataset.alertIndex)]);
});
el("impact-timeline").addEventListener("click", (event) => {
  const button = event.target.closest("[data-market-day-index]");
  const marketState = state.marketState;
  if (button && marketState) {
    renderMarketDayDetail(marketState, Number(button.dataset.marketDayIndex));
  }
});
el("impact-day-detail").addEventListener("click", (event) => {
  const button = event.target.closest("[data-open-market-segment]");
  if (!button) return;
  const navButton = document.querySelector('[data-nav-view="impacts"]');
  activatePage("workspace", { activeButton: navButton });
  activateView("impacts");
  el("search").value = button.dataset.openMarketSegment;
  loadView();
});
el("network-map").addEventListener("click", (event) => {
  const target = event.target.closest("[data-map-type]");
  if (!target) return;
  if (target.dataset.mapType === "zone") {
    state.mapZoneFocus = target.dataset.mapZone;
    el("filter").value = target.dataset.mapZone;
    renderMap();
    return;
  }
  renderMapDetail(target.dataset.mapType, Number(target.dataset.mapIndex));
});
el("map-inspector").addEventListener("click", (event) => {
  const zoneButton = event.target.closest("[data-map-zone]");
  if (zoneButton) {
    state.mapZoneFocus = zoneButton.dataset.mapZone;
    el("filter").value = zoneButton.dataset.mapZone;
    renderMap();
    return;
  }
  if (event.target.closest("[data-map-back]")) {
    state.mapZoneFocus = null;
    el("filter").value = "all";
    renderMap();
  }
});
el("drawer-close").addEventListener("click", closeDrawer);
el("drawer-backdrop").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDrawer(); });
el("refresh-data").addEventListener("click", startRefresh);

configureFilter();
activatePage("overview", { scroll: false });
loadRefreshStatus();
Promise.all([loadOverview(), loadReports(), loadResearchBrief(), loadChanges()])
  .then(() => Promise.all([loadView(), loadMarketContext()]))
  .then(() => {
    const hash = window.location.hash.slice(1);
    if (hash === "map") {
      const mapButton = document.querySelector('[data-nav-view="map"]');
      activatePage("workspace", { activeButton: mapButton, scroll: false });
      activateView("map");
    } else if (["changes", "insights", "learn"].includes(hash)) {
      activatePage(hash, { scroll: false });
    }
  })
  .catch(showError);
