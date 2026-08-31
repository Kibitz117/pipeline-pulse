# Data model and relationships

## Design principle

The durable object is an `event`, not a web page or notice. Notices are
versioned source claims about an event. Capacity observations and market data
are independent time series joined to the event as of an explicit decision
time.

```text
source artifact -> notice version -> event <- event impact -> facility/segment
                                      |
                                      +-- as-of market context -> alert
```

## Core entities

### Source and provenance

- `fetch_runs`: one execution of a collector.
- `source_artifacts`: immutable HTML, PDF, CSV, XLSX, or API response metadata,
  content hash, local path, and audit clocks.
- `notice_index_pages`: page-level pagination and source-reported coverage.
- `notice_index_exports`: full-export parsed, footer-reported, and independently
  reconciled row counts.
- `notice_index_observations`: one source row as observed in one artifact.
- `notice_version_observations`: one availability-time observation linking a
  raw detail artifact to its normalized semantic notice version. Repeated
  unchanged checks remain rows, allowing later edits and reversions to be
  replayed without look-ahead.
- `current_notice_index`: latest-page rows overlaid on the latest full export,
  with the best available nonempty subject for each notice ID.
- `agent_runs`: prompt, model, inputs, output, validation, and raw session path.

### Pipeline reference data

- `operators`: parent/operator platform.
- `pipeline_systems`: regulated pipeline/TSP and source code.
- `facilities`: locations, stations, receipt/delivery points, storage points,
  zones, and interconnects.
- `segments`: directional pipeline segments and their nominal units.
- `location_exports` and `location_observations`: immutable operator location
  snapshots, including native roles, zones, segments, interconnects, source
  column count, schema fingerprint, and parser version.
- `county_reference_observations`: versioned Census reference points used for
  deterministic geographic joins.
- `location_coordinate_observations`: location-to-coordinate claims with
  method, precision, source artifact, and matched geography.
- `map_reference_layers`: archived GeoJSON context such as state boundaries.

### Operator facts

- `notice_versions`: immutable semantic versions of an operator notice. The
  version hash covers parsed investor-relevant fields rather than volatile HTML
  wrapper state; raw byte identity remains on `source_artifacts.content_sha256`.
- `tgp_notice_version_timeline`: observation-by-observation notice history with
  prior version, revision flag, and captured availability timestamp.
- `events`: the canonical operational event and its current state.
- `event_notice_links`: many-to-many evidence and notice-chain roles.
- `event_impacts`: event-by-facility-by-gas-day capacity effect.
- `capacity_exports`: one immutable point or segment XLSX capture with source
  gas day, effective time, cycle, row count, schema fingerprint, and operator
  explanatory comments.
- `capacity_observations`: point/segment operating, scheduled, and available
  capacity by gas day and cycle, with native IDs retained even when a reference
  join is unresolved.
- `tgp_transport_impact_assessments`: one immutable comparison between an
  outage station-period row and a direction-matched capacity snapshot. It
  stores match method, TGP zone, baseline timing, gross reduction, conditional
  scheduled shortfall, headroom, status, unresolved reasons, and both source
  artifacts.
- `latest_tgp_daily_market_state`: a queryable 30-day current-state view. Each
  gas day reports active segments/zones plus the largest single planned
  reduction and conditional schedule gap. It deliberately does not sum serial
  constraints that may affect the same molecules.
- `latest_tgp_capacity`: latest received observation per gas-day capacity key;
  `tgp_capacity_capture_summary`: capture-level reconciliation and reference
  coverage.

### Market context and output

- `market_observations`: a long-form, vintage-aware table for price, storage,
  weather, production, consumption, and exports. `provider` and
  `observation_type` make source semantics queryable; `source_published_at`
  stays separate from conservative `available_at`, and `artifact_id` resolves
  to the exact canonical source URL and immutable raw body.
- `alerts`: the material change, deterministic score components, narrative,
  confidence, and evidence bundle shown to the user.

The TGP alert builder currently creates four fact types: newly observed
critical notices, every same-ID semantic notice revision, material forward
outage-report revisions, and material directional-segment capacity/tightness
changes. IDs are deterministic and
rebuilds are idempotent. Capacity comparisons cross a threshold only for a
50,000 Dth/day operating-capacity move, an 80% or 95% scheduling-pressure
crossing, a zero-availability crossing, or a 100,000 Dth/day availability move
while either snapshot is at least 80% scheduled. Outage revisions require a
25,000 Dth/day absolute move. These are screening rules, not price forecasts.

## Relationship rules

### Notice to event

The operator's `prior_notice_id` is authoritative when present. Otherwise, an
agent may propose a link using shared facilities, event type, dates, subject,
and text similarity. Proposed links retain confidence and supporting evidence.
The event graph must remain acyclic.

### Event to capacity

Join in descending evidence quality:

1. explicit station/segment and capacity values in a notice;
2. matching Outage Impact Report row and gas day;
3. matching operational-capacity observation for the relevant cycle;
4. agent-inferred facility link with no numeric impact.

Never manufacture a capacity number. `NULL` plus an explanation is preferable
to a guessed value.

For current TGP reports, `FH` means the direction shown on the operator's
Segment / PIN map and maps to capacity `TD1`; `BH` is the opposite direction
and maps to `TD2`. Station matching proceeds by normalized name, station
number, then a unique segment-direction candidate. Ambiguous rows remain
`no_trade_mapping`.

Conditional scheduled shortfall is
`max(0, captured net scheduled quantity - forecast operating capacity)`. It is
an unchanged-schedule scenario, not measured flow, curtailment, or lost
production; future nominations and rerouting remain unresolved.

Daily aggregation uses `max`, not `sum`, across active station-period rows.
The view is an exposure screen: it identifies the day's largest individual
bottleneck and counts how broadly maintenance is distributed. A system-wide
volume estimate remains unavailable until route overlap, measured flows, and
rerouting are observed.

Operational availability is transportation capacity, not measured flow. Its
native flow direction, cycle, operator comments, source post time, and capture
time stay attached so later EDA can compare it with maintenance forecasts
without collapsing different nomination cycles or introducing lookahead.

### Event to geography

Resolve operator location identifiers to facilities, then map facilities to
pipeline zone, state, Census division, and EIA storage region. A pipeline can
cross multiple regions; the specific affected facility controls the mapping.

### Event to market context

All contextual joins use `available_at <= decision_at` and select the newest
eligible vintage. `decision_at` defaults to the first time Pipeline Pulse
received the changed artifact, not the operator's effective date.

For example:

```sql
SELECT e.event_id, m.series_code, m.value, m.unit, m.available_at
FROM events e
ASOF LEFT JOIN market_observations m
  ON e.last_changed_at >= m.available_at
WHERE m.series_code = 'EIA_HENRY_HUB_SPOT';
```

Production, consumption, and export data can be weeks old. The UI must display
the observation period and release/vintage age rather than presenting them as
live measurements.

### Historical replay modes

Historical research keeps two modes separate:

1. `source-time reconstruction` selects a report by its immutable artifact and
   uses the source-posted timestamp to reconstruct contemporaneous public
   market context. It answers what the archived source said and what public
   market observations existed around that source date.
2. `captured replay` selects only artifacts whose `received_at` or normalized
   `observed_at` is at or before the decision timestamp. It answers what
   Pipeline Pulse itself could actually have known and is the only valid mode
   for live-alert backtests.

For notice text, captured replay selects the final
`notice_version_observations` row at or before the cutoff. Operator-posted and
effective timestamps describe the source claim; they never move a revision
backward to a time before Pipeline Pulse observed it.

A backfilled operator report may support the first mode without supporting the
second. The UI must label that case as a historical reconstruction. Neither
mode may join to a market observation with `available_at` after its chosen
cutoff.

### Event to investment relevance

The impact channel is directional only when evidence supports it:

- `upstream_receipt`: possible stranded supply or weaker upstream basis;
- `downstream_delivery`: possible downstream scarcity or reliability pressure;
- `segment_transport`: corridor constraint with direction explicitly stored;
- `storage`: injection/withdrawal constraint;
- `system_balance`: OFO, imbalance, or broad operational condition;
- `unknown`: insufficient evidence.

The narrative describes possibilities and exposed regions. It does not make an
unsupported directional price forecast.

## Required audit clocks

- `source_published_at`: time stated by the publisher.
- `effective_start` and `effective_end`: operational interval.
- `requested_at` and `received_at`: collection interval.
- `processed_at`: parsing or agent completion.
- `available_at`: earliest defensible time an observation could be used.
- `recorded_at`: database commit time.

These clocks are intentionally not collapsed.

Application code represents them with Pendulum `DateTime` values and explicit
IANA zones. DuckDB persists instants as `TIMESTAMPTZ`; CSV exports include both
UTC and operator-local renderings where useful. `pytz` is installed only
because DuckDB's Python conversion layer imports it when returning
`TIMESTAMPTZ` values.
