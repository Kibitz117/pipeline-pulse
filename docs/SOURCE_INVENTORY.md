# Source inventory

This inventory is the collection contract. Public operator data is
archived exactly as received before any parser or agent operates on it.

## Kinder Morgan / Tennessee Gas Pipeline

### Critical notices

- URL: `https://pipeline2.kindermorgan.com/Notices/Notices.aspx?code=TGP&type=C`
- Cadence: every 15 minutes
- Index fields: TSP, notice ID, primary and secondary notice type, post time,
  effective time, end time, and subject.
- Detail fields: critical flag, response requirement, status description,
  prior notice ID, full text, and attachments.
- Retention behavior: the UI exposes a rolling 90-day window, so raw collection
  is required to build durable history.
- Complete export: archive `EXCEL-Summary (All)` daily to recover every index
  row in the portal's current window; keep its source-reported footer count
  separate from parsed and pagination-reconciled counts.
- Scheduling: local cron invokes one lock-protected application cycle; a slow
  request cannot create overlapping collectors.

Initial included types:

- force majeure;
- capacity constraint and curtailment;
- operational flow order and operational alert;
- maintenance and construction;
- storage restrictions;
- current pipeline conditions when they identify active restrictions.

Routine administrative, tariff, phone-list, invoicing, and marketing notices
are archived but excluded from investor alerts by default.

### Planned service outages — deferred

- URL: `https://pipeline2.kindermorgan.com/Notices/Notices.aspx?code=TGP&type=P`
- Current status: disabled for the mini-MVP.
- Purpose: forward calendar and revisions to scheduled work.
- Relationship: planned notices can later be linked to critical restrictions,
  outage-report rows, or force majeure events without being overwritten.

### Outage Impact Report

- Source: Word-style HTML tables embedded in maintenance notices whose subject
  is `TGP Outage Impact Report`.
- Cadence: fetched as notice details after each new critical-notice discovery.
- Published content: stations/segments, nominal operating capacity, seven-day
  daily capacity forecast, four-week weekly outlook, estimated operational
  impact, named outages, and outage dates.
- Important caveat: the report says it is updated weekly, is subject to change,
  and that DART-posted dates control when dates conflict. Each notice artifact
  is therefore retained rather than replaced.

### Operationally available capacity

- Point URL:
  `https://pipeline2.kindermorgan.com/Capacity/OpAvailPoint.aspx?code=TGP`
- Segment URL:
  `https://pipeline2.kindermorgan.com/Capacity/OpAvailSegment.aspx?code=TGP`
- Current status: enabled hourly. Each run archives the delivery-point,
  receipt-point, and segment HTML pages and XLSX exports before parsing.
- Selection: `BEST AVAILABLE`; the selected gas day, effective time, and
  nomination cycle are stored from each export rather than inferred from the
  collection time.
- Dimensions: gas day, cycle, point or segment, receipt/delivery purpose, and
  measurement basis, native location/segment ID, zone, and flow indicator.
- Measures: design capacity, operating capacity, total scheduled quantity, and
  operationally available capacity, in Dth per gas day, plus IT,
  all-quantity-available, and quantity-reason indicators.
- Validation: source footer count, strict column fingerprint, TGP identifier,
  nonnegative quantities, and `available = max(operating - scheduled, 0)`.
- Important interpretation: KM says availability can change intraday because
  of bidirectional nomination netting, partial paths, storage swings,
  exchanges, outages, and imbalance management. Scheduled quantity is a net
  directional value, and operating capacity can differ from design for
  maintenance or other observed or anticipated conditions. Central delivery
  points may be commercial rollups. These data are not physical flow meters
  and do not by themselves establish lost production or price impact.

### Locations and maps

- URL:
  `https://pipeline2.kindermorgan.com/LocationDataDownload/LocDataDwnld.aspx?code=TGP`
- Current status: enabled in the daily full-export collection.
- Fields: operator location ID and name, receipt/delivery/bidirectional role,
  county, state, facility type, receipt and delivery zones, native segment,
  status/effective dates, and interconnect counterparty identifiers.
- Coordinates: the current official Census county Gazetteer is primary. The
  official 2021 vintage is a narrow fallback for historic Connecticut county
  names still used by TGP. Two explicit TGP spelling aliases are deterministic
  and tested. Offshore planning areas remain unmapped.
- Precision: `county`. Markers are Census internal reference points, not
  facility coordinates. Maintenance segment anchors average the matched
  counties and are not pipeline geometry.
- Boundaries: generalized official Census TIGERweb state GeoJSON is archived
  before display.
- Static operator system and zone maps remain supporting evidence; the UI does
  not trace a route from a PDF or interpolate a fictitious pipeline line.

## Kinder Morgan / Natural Gas Pipeline Company of America

- Critical notices:
  `https://pipeline2.kindermorgan.com/Notices/Notices.aspx?code=NGPL&type=C`
- Point capacity:
  `https://pipeline2.kindermorgan.com/Capacity/OpAvailPoint.aspx?code=NGPL`
- Segment capacity:
  `https://pipeline2.kindermorgan.com/Capacity/OpAvailSegment.aspx?code=NGPL`
- Locations:
  `https://pipeline2.kindermorgan.com/LocationDataDownload/LocDataDwnld.aspx?code=NGPL`
- Identity contract: TSP `6931794`, FERC CID `C002096`, operator clock
  `America/Chicago`.
- Current status: complete notice-index export, bounded detail backfill and
  same-ID revision checks, location reference, and delivery/receipt/segment
  capacity are enabled through the shared Kinder Morgan adapter.
- Geography: blank state fields are preserved as unmapped source values; they
  are not rejected or assigned a guessed coordinate.
- Capacity caveat: NGPL states that Operationally Available Capacity is shown
  for Central Delivery Points rather than their physical member points. The
  source's `All Qty Avail` flag, numeric OAC, and explanatory comments therefore
  remain separate fields. A simple `operating - scheduled` difference is a
  diagnostic, not evidence that the source is wrong.
- Interpretation boundary: no NGPL-specific outage table, direction mapping,
  bottleneck rollup, regional price mapping, alert threshold, or AI conclusion
  is enabled yet. Successful normalized collection is not treated as a
  validated market-impact model.

## Market context

### EIA Weekly Natural Gas Storage Report

- URL: `https://ir.eia.gov/ngs/wngsr.json`
- Current status: enabled. A daily post-release check handles normal and
  holiday-shifted weekly vintages; raw JSON is archived before parsing.
- Frequency: weekly; release-aware capture around the official publication.
- Metrics: Lower-48, East, Midwest, Mountain, Pacific, South Central, salt, and
  nonsalt working gas; weekly change; year-ago and five-year comparisons where
  published.
- Use: inventory tightness and regional context.
- Point-in-time rule: use the release timestamp, not the week-ending date, as
  `available_at`.

The spot and weather sources below are ingested. Futures and regional cash
remain optional because their access and licensing constraints differ.

### EIA Henry Hub physical spot

- Primary access: EIA's daily Henry Hub history page
  `https://www.eia.gov/dnav/ng/hist/rngwhhdD.htm`.
- Fallback reference: FRED series `DHHNGSP`, sourced from EIA. The keyless FRED
  download host stalled during the August 30–31 live audit, so it is not a
  required scheduled dependency.
- Frequency: daily observations released in a weekly batch; weekly/monthly
  derivatives are computed locally.
- Metric: Henry Hub spot price in USD/MMBtu.
- Use: national benchmark level, one/five/twenty-day change, and historical
  event-study benchmark.
- Point-in-time rule: EIA's page release date is retained as the vintage, the
  gas-price date is the observation period, and conservative receipt time is
  `available_at` because the page does not expose an exact per-release clock.
  A Monday price first captured Wednesday cannot appear in a Monday replay.
- Caveat: it is not a Northeast basis price and cannot establish local impact.

EIA's historical NYMEX futures series is not a live solution: its public page
states that futures prices after April 5, 2024 are unavailable. We will not
quietly fill that gap with scraped or unclear-license data.

### Optional NYMEX Henry Hub futures

- Human-facing direct link:
  `https://www.cmegroup.com/markets/energy/natural-gas/natural-gas.quotes.html`
- Desired measures: exact contract month, trade/settlement timestamp, last or
  settlement price, volume, open interest, and the first four curve tenors.
- Derived measures: front-month return, curve slope, winter/summer spreads,
  roll-aware continuous return, and pre/post-alert markouts.
- Research adapter: Yahoo Finance `NG=F` through the same public chart response
  used by `yfinance`. It provides a rolling front-month Henry Hub futures proxy,
  but the adapter is
  unofficial and its own documentation describes Yahoo data as personal-use.
  If enabled for local research, every row must retain the actual contract
  month, expiry, quote timestamp, provider, and roll boundary. `NG=F` must never
  be labeled spot or treated as one immutable contract.
- Production-grade upgrade: Databento's CME dataset or CME's direct API, both
  configured by the user with the necessary key and market-data entitlement.
- Default status: manual opt-in; Yahoo rate-limited the public endpoint during
  the August 30 source audit. CME's public
  delayed display is useful for navigation but is not assumed to grant bulk
  redistribution rights.
- Use: market context and event-study response, not automatic causal language.

The CFTC's public Commitments of Traders API is a possible later positioning
overlay, but it is weekly and is not a price feed.

### EIA national supply and demand

- Frequency: monthly actuals from Natural Gas Monthly plus the current monthly
  Short-Term Energy Outlook vintage.
- Supply metrics: dry production and imports.
- Demand metrics: residential/commercial, industrial, electric power, pipeline
  exports, and LNG exports/feedgas proxy when publicly reusable.
- Derived metrics: total supply, total disposition, net exports, and balance.
- Use: label the macro backdrop and show how stale each underlying observation
  is. Weekly Update values sourced from third-party vendors are excluded until
  reuse terms are confirmed.

### NWS forecast and derived degree days

- Primary source: the public NWS API `forecastHourly`/`forecastGridData`
  endpoints for a documented set of TGP-zone demand anchors. Forecasts are
  generated from current NWS grids and are open for public use.
- Frequency: capture each new forecast vintage without overwriting prior runs.
- Current anchors: New York City and Boston, explicitly labeled as Northeast
  TGP delivery-market demand proxies rather than pipeline-wide coverage.
- Metrics: daily HDD/CDD calculated deterministically from hourly local-time
  temperatures for complete calendar days. Store daily mean temperature as
  well as the derived degree-day measures. No cross-anchor weighted aggregate
  is asserted in the current version.
- Use: weather-driven demand regime.
- Point-in-time rule: store issue time and valid interval; never replace an old
  forecast with a newer run.
- Fallback/model comparison: Open-Meteo, with attribution and an explicit
  model/run timestamp. Its best-match feed is easier to integrate and updates
  frequently, but it is not the default government source.
- Rejected as the primary live feed: CPC's precomputed seven-day degree-day
  `latest` directory lagged by more than a month during the August 30 source
  audit. CPC archives remain useful for normals and validation, not freshness.

### Regional cash prices

- Required locations: TGP Zone 4 200L/300L and the relevant downstream New
  York/New England delivery hubs, expressed as outright cash and basis to Henry
  Hub where available.
- Preferred optional provider: NGI Daily GPI API; Platts Gas Daily is a second
  licensed option. Both publish location-specific assessments that match the
  actual TGP price channel.
- Public default: absent. The UI must say `regional price unconfirmed` rather
  than substitute Henry Hub or a futures contract.
- Rejected as current defaults: EIA ended the Natural Gas Weekly Update in
  January 2026, and its New England dashboard showed delayed/partially
  unavailable regional data during the August 30 source audit.

## Deferred sources

- Index of Customers and contract entitlements: potentially valuable for direct
  shipper exposure, but not required to prove the first alert workflow.
- Regional cash-basis prices: activate the NGI/Platts adapter only when a user
  supplies valid credentials and accepts the provider's use terms.
- Futures pricing ingestion: optional adapter after data rights and contract
  roll treatment are explicit.
- Other Kinder Morgan pipelines: add after the TGP adapter passes fixtures and
  source-health tests.
- Williams and Enbridge platforms: prove true cross-operator portability after
  the common event contract is stable.
