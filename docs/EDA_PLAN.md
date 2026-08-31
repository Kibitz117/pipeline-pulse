# Exploratory data analysis plan

EDA is a product-discovery stage, not a search for evidence supporting a
preselected alert. Each stage produces auditable tables and a decision about
whether to proceed.

## Stage 1: source coverage and quality

Questions:

- How many notices are available per day, page, type, and pipeline?
- What portion of index rows have retrievable details and attachments?
- How often does the same notice body change under one notice ID?
- How often are timestamps, prior-notice IDs, facilities, or capacity values
  missing?
- Does the rolling source window or pagination create observable gaps?

Outputs:

- daily coverage matrix;
- missingness report;
- content-hash and duplicate report;
- parser failure samples;
- source latency and health statistics.

Decision gate: do not build investment analysis until coverage and revision
behavior are understood.

## Stage 2: notice taxonomy and information novelty

Questions:

- Which notice types dominate volume?
- How many daily cycle postings repeat the same operational state?
- Which types usually initiate, supersede, or terminate an event?
- Can deterministic rules separate administrative updates from operational
  changes without losing important exceptions?

Outputs:

- type/subtype frequency and seasonality;
- subject-template clusters;
- notice-chain length and lifetime distributions;
- text and structured-field change matrices.

Decision gate: define an event taxonomy only after the raw distribution is
visible.

## Stage 3: maintenance and capacity effects

Questions:

- What percentage of maintenance records contain explicit nominal and operating
  capacity?
- How large and long are reductions by segment, station, zone, and season?
- How often do planned dates or expected impacts change before and during work?
- Do planned outages correspond to observed changes in operationally available
  capacity?

Outputs:

- facility-by-gas-day capacity panel;
- outage revision waterfall;
- planned versus realized capacity comparison;
- overlapping-outage calendar.

Decision gate: only present a quantitative impact when the reduction reconciles
to source data.

## Stage 4: network grounding and propagation hypotheses

Questions:

- Which constrained report segments resolve cleanly to operator locations,
  zones, counties, and receipt/delivery roles?
- Are location clusters informative enough to distinguish upstream receipt,
  downstream delivery, storage, or broad corridor effects?
- Which station names need agent-assisted entity resolution, and which remain
  too ambiguous to place?
- Do recurring segment constraints overlap the same interconnects or zones?

Outputs:

- segment-to-location coverage matrix;
- constrained-segment map by report vintage;
- unresolved station/location queue with evidence;
- recurring corridor and interconnect tables.

Decision gate: describe a propagation channel only when native segment and
location evidence support it. County anchors are orientation, not exact
facility or route geometry.

## Limited balance context; broader market work deferred

The MVP now loads EIA weekly storage as a small, point-in-time regime label.
Weather, prices, broader supply/demand, and causal event studies remain a
second-stage research track.

Questions:

- Are event frequency or magnitude related to storage region, season, HDD/CDD,
  or national balance conditions?
- Does the same capacity reduction look different in storage-surplus and
  storage-deficit regimes?
- Is Henry Hub response distinguishable from normal volatility around the same
  dates?
- Are results robust after removing EIA-release windows, contract rolls,
  extreme-weather days, and overlapping events?

Outputs:

- point-in-time event/context panel;
- stratified descriptive statistics;
- predeclared one-, three-, and five-day event windows for daily prices;
- intraday event study only if a compliant futures feed becomes available;
- negative controls and sensitivity tables.

Decision gate: describe market movement as context or association unless the
research design supports more.

## Stage 5: candidate investor insights

Possible products are hypotheses until the earlier stages support them:

- material revision feed;
- forward maintenance and capacity calendar;
- recurring constrained-segment monitor;
- regime-aware event comparison;
- source-change and stale-data warnings;
- market-reaction explorer.

Each candidate must pass four tests:

1. It saves meaningful analyst time or reveals a non-obvious relationship.
2. Its underlying fields have acceptable coverage and latency.
3. Its language matches the strength of evidence.
4. A user can trace the result to raw point-in-time sources.

## Stage 6: balance and trade translation

Only alerts that pass the earlier data-quality and materiality gates enter this
stage. For each event:

1. Calculate gross transport reduction and a direction-aligned mechanical
   at-risk volume; preserve zero-to-gross-reduction uncertainty when scheduling
   or rerouting is unknown.
2. Compare at-risk volume with time-aligned regional demand, supply, storage,
   and pipeline receipts. Show the named denominator and vintage.
3. Map native segment -> TGP rate zone/leg -> regional price location -> exact
   contract month(s) overlapping the effective gas days.
4. Prefer a matching TGP basis instrument when one exists. Treat Henry Hub `NG`
   as a broad-balance exposure that requires an additional Gulf or national
   transmission channel.
5. Visualize the event window against balance history and prices, separating
   data observed before the alert from later outcomes.
6. Ask the agent for a directional scenario, confirmation, invalidation, and
   counterevidence only after deterministic calculations are complete.

Required outputs:

- transport reduction waterfall: gross reduction -> scheduled volume at risk
  -> estimated rerouting/storage offsets -> unresolved net balance range;
- affected-region balance share with explicit numerator, denominator, unit,
  and vintage;
- contract exposure table with location, symbol, delivery month, event-day
  overlap, price availability, and mapping confidence;
- scenario chart showing historical balance/price outcomes without labeling
  association as causation;
- `unresolved` states whenever the direction, substitute capacity, price
  license, or location-to-contract mapping is not supportable.
