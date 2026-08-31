# Product specification

## Goal

Pipeline Pulse turns TGP's public bulletin board into a current market read.
It first summarizes what the full maintenance calendar and newest notices mean
for transport availability, regional balance, and the likely price channel;
the user can then drill into zones, segments, calculations, and source notices.

The primary user question is:

> What does TGP's maintenance calendar mean for the gas market now, where is
> forward transport risk concentrated, and what would confirm a price effect?

## Information hierarchy

1. **Market read:** current versus forward pressure, inventory regime, and the
   most plausible market channel.
2. **Drivers:** the few notices, capacity changes, zones, and time windows that
   explain that read.
3. **Drill-down:** individual segments, assumptions, before/after values, and
   historical forecasts.
4. **Provenance:** source timestamps, raw records, and quality checks on demand.

The interface must never require a user to aggregate rows mentally before
understanding the conclusion.

## Build sequence

1. **Collect:** archive source-complete, immutable TGP operator data.
2. **Normalize:** create queryable tables without adding unsupported meaning.
3. **Audit:** measure completeness, revisions, parser quality, and point-in-time
   correctness.
4. **Explore:** analyze maintenance taxonomy, capacity effects, timing, and
   report-to-report revisions.
5. **Productize:** serve only insights that survive explicit usefulness and
   robustness tests.

Operator-change screening is deterministic and auditable. The UI leads with an
evidence-validated market assessment and aggregate transport horizons, then
shows only source changes large or urgent enough to merit follow-up. Screening
scores remain supporting methodology, not a proxy for market or price impact.

## User and workflow

The initial research user is a fundamental natural-gas, power, producer,
utility, or midstream analyst. The product supports four workflows:

1. **Orient:** understand the current and forward market setup in under a minute.
2. **Locate:** see which zones and segments drive the aggregate risk.
3. **Investigate:** inspect the notice chain, facilities, capacity math, and
   assumptions behind a scenario.
4. **Test:** query comparable historical maintenance events and revisions using
   only information available at the time.

The historical workflow should let an analyst select an old report, inspect
the projects and constraints it described, view market or balance observations
available around that source date, and compare them with the latest state.
Backfilled reports are labeled reconstructions; only reports captured live can
be used to measure alert latency or claim a live point-in-time signal.

## Current TGP product promise

For Tennessee Gas Pipeline, the system will:

- Detect newly published critical notices and backfill maintenance details.
- Preserve every captured source artifact and the operator's explicit
  prior-notice relationship.
- Extract affected stations, segments, forecast intervals, nominal capacity,
  estimated operating capacity, and outage descriptions from report tables.
- Calculate absolute and percentage capacity reduction deterministically.
- Compare each station-period value with the prior weekly vintage.
- Capture hourly best-available operating, scheduled, and available capacity
  for TGP receipt points, delivery points, and directional segments.
- Align TGP's two native direction vocabularies and calculate the captured
  schedule that would exceed each forecast operating limit if unchanged.
- Add the public weekly EIA storage release as point-in-time balance context,
  without treating it as event attribution.
- Compare source versions, deduplicate deterministic event IDs, screen
  operationally important changes, and retain before/after source records.
- Provide a local analyst UI with links to operator evidence.
- Explain native gas-market terms in plain language while keeping operator
  codes and raw evidence available for experienced analysts.

## Explicit non-goals

- Predicting an exact Henry Hub price move from a single pipeline notice.
- Treating lost transport capacity as lost U.S. production.
- Recreating paid regional basis, nomination, or flow datasets from inference.
- Covering every Kinder Morgan pipeline before the TGP adapter is reliable.
- Adding weather, precipitation, regional prices, or futures feeds without a
  tested decision use and clear point-in-time treatment.
- Sending autonomous trades or operational instructions.

## Futures-price policy

Futures are useful, but they are an optional validation layer rather than a
dependency of the mini-MVP. A later version can link users directly to the
[CME Henry Hub Natural Gas futures page](https://www.cmegroup.com/markets/energy/natural-gas/natural-gas.quotes.html)
and display licensed or clearly reusable contract data when configured.

There are three increasingly strong claims the product may make:

1. **Context:** "The front contract was up or down when this alert arrived."
2. **Association:** "Futures repriced by X over a stated post-alert window."
3. **Estimated effect:** "Comparable unexpected events are associated with an
   abnormal return after controlling for overlapping releases and market
   conditions."

The first two are feasible once a compliant feed exists. The third is research,
not an alert default. The words `caused`, `because of`, or an exact price target
are prohibited unless a documented causal design supports them.

A proper event study must use the first-received alert timestamp, the exact
tradable contract at that timestamp, contemporaneous prices, predeclared
windows, and controls for EIA releases, weather revisions, expiry/roll effects,
and overlapping pipeline events. Results should be labeled as associated
market reaction unless identification is genuinely stronger.

## Post-alert trade layer

The destination is a trade-research layer, but it must not jump directly from
"TGP reduced capacity" to "buy or sell gas." It must answer a deterministic
sequence first:

1. **Transport change:** how much gross TGP capacity changed, on which native
   segment and direction, and during which gas days?
2. **Volume at risk:** how much pre-event scheduled volume would exceed the
   forecast operating capacity, using only direction- and time-aligned rows?
3. **Balance exposure:** what share of the relevant regional demand, supply,
   storage injection/withdrawal, or pipeline inflow does that volume represent?
4. **Substitution:** can other TGP directions, interconnects, storage, or other
   pipelines reroute the volume?
5. **Tradable mapping:** which location and delivery month price the exposed
   balance, and is the instrument a regional basis contract or broad Henry Hub?
6. **Price scenario:** what sign and relative magnitude are plausible, what
   confirms the scenario, and what invalidates it?

The first volume estimate is a range, not a point claim:

- lower bound: zero physical supply loss when all affected volume reroutes or
  was never scheduled;
- mechanical at-risk volume: `max(0, comparable scheduled quantity - forecast
  operating capacity)`;
- upper bound: the published gross capacity reduction, unless a smaller
  direction-aligned scheduled quantity caps it.

None of these quantities is automatically lost production. The UI must show
the denominator next to every percentage and retain the original Dth/day value.
Regional balance shares must use point-in-time demand/supply observations and
document any heat-content conversion between Dth, MMBtu, and Bcf.

Contract mapping is location-first and month-aware. CME's standard Henry Hub
Natural Gas future (`NG`) is physically delivered at Henry Hub in Louisiana
and represents 10,000 MMBtu, so it is the broad benchmark rather than the
automatic direct expression for a Northeast TGP event. Current ICE products
include TGP-specific basis contracts such as
[Tennessee Zone 4 200L](https://www.ice.com/products/72265887),
[Tennessee Zone 4 300L](https://www.ice.com/products/32749936), and
[Tennessee Zone 6 200L South](https://www.ice.com/products/72265903).
The system must map a segment to an operator zone/leg and then to an explicitly
configured contract; it must never choose `NG` merely because it is liquid.

For intuition, the UI may divide an at-risk daily MMBtu volume by a contract's
unit size. That is a volume-equivalent contract count—not a recommended
position size, market share, or estimate of price impact. If licensed prices or
an exact location mapping are absent, the trade field remains `unresolved`.

## Why market context may matter later

The same capacity reduction can have different relevance in different regimes.
A restriction during mild weather and surplus storage is not equivalent to the
same restriction during extreme degree-day forecasts and a storage deficit.

The current UI covers the first three dimensions plus a deliberately narrow
market-regime layer:

1. **Magnitude:** capacity reduction in Dth/d and percent of nominal capacity.
2. **Time:** start, end, duration, lead time, gas day, and nomination cycle.
3. **Network:** facility, segment, zone, geography, direction, and receipt or
   delivery role.
4. **Market regime:** current coverage includes regional and Lower-48 weekly
   storage, direct-EIA Henry Hub physical spot, and NWS-derived HDD/CDD for New
   York City and Boston. A roll-aware Yahoo `NG=F` front-month proxy is manual
   opt-in; regional TGP cash remains unresolved without licensed data.

The overview presents those inputs in a 30-day constraint calendar. Blue bars
show the largest single planned capacity reduction on each day; orange bars
show the largest single conditional schedule gap. The calendar never sums
station rows because multiple locations may constrain the same transported
gas. Daily `H`/`C` markers show the largest HDD or CDD value among the named
NWS anchors without summing cities. Selecting a day exposes the ranked station,
segment, zone, direction, weather context, and source calculation.

Below the calendar, an evidence funnel makes the boundary between a market
watch and a trade-ready setup explicit. Transport can be present while demand
overlap, measured flow/rerouting, regional price, directional sign, or exact
contract mapping remain unresolved. Henry Hub is labeled as broad context;
the first plausible transmission channel is affected TGP regional basis.

These are explanatory joins, not causal claims. A regional basis series can be
added later if a licensed or clearly reusable source is available.

## Alert contract

Every investor-facing alert must answer:

- **Change:** what is different from the prior known state?
- **Scale:** how much capacity is affected, or why is the magnitude unknown?
- **Timing:** when does the change begin and end?
- **Location:** which station, segment, zone, and flow direction are involved?
- **Channel:** who could be constrained upstream or downstream?
- **Confirmation:** what additional market observation would confirm or weaken
  the proposed channel?
- **Source:** a link to the operator posting, with raw provenance available on
  demand.

Example structure:

```text
RESOLVED · Tennessee Gas Pipeline · Station 542 / Segment 539 BH

Force majeure terminated for the Aug. 27 gas day. Nominations may again be
scheduled up to posted capacity through Station 542.

Capacity: current cycle observation pending
Evidence status: status and timing are explicit; capacity effect is not yet quantified
Evidence: termination notice -> initiating notice -> capacity observation
```

## Operational-change screening

The internal score is deterministic and decomposable. It decides which source
changes merit analyst attention; it is not displayed as market materiality and
does not estimate price impact. Current rules use:

- outage-forecast revisions of at least 25,000 Dth/day, then absolute size,
  percentage size, and lead time;
- live operating-capacity changes of at least 50,000 Dth/day;
- scheduled-utilization crossings at 80% or 95%, movement to or from zero
  available capacity, and large availability changes under high utilization;
- notice subject and time-to-effective, prioritizing emergencies, curtailments,
  restrictions, constraints, OFOs, and maintenance.

The agent explains potential market channels but cannot change these rules or
present the screening score as a trade signal.

## Later agent research layer

Agents become valuable after deterministic data coverage is stable. Their job
is to investigate ambiguous relationships across notices, locations, report
vintages, and later market context—not to paraphrase every row.

Each research memo must separate:

- `facts`: direct normalized observations with artifact citations;
- `hypotheses`: possible impact channels, never presented as established fact;
- `uncertainty`: the specific missing evidence or ambiguity for each claim;
- `counterevidence`: facts that weaken the proposed interpretation;
- `missing_data`: what would resolve the ambiguity;
- `next_checks`: bounded research or data-quality actions.

The default UI layer should answer “what changed, where, when, and why might I
care?” without assuming pipeline expertise. The evidence drawer and queryable
tables preserve the native labels needed to challenge that explanation.

### Agent execution gate

Every agent run must receive the deterministic quality report with its input
artifact IDs. A hard failure sets `agent_input_ready=false` and blocks new
interpretation. Warnings do not disappear: they are included in the agent's
context and must appear in any affected memo's limitations or counterevidence.
The agent may investigate a parser failure, but it may not treat an unprocessed
artifact as normalized investor evidence.

## Success criteria

- Maintain complete detail coverage for TGP maintenance notices in the source
  window.
- Preserve raw evidence and separate source, receipt, and processing clocks for
  every normalized row.
- Reconcile explicit outage-report capacity reductions and visibly flag source
  inconsistencies.
- Produce a useful alert within 20 minutes of a new critical posting.
- Let a user move from an alert to the exact source evidence in one click.
- Keep normalized keys pipeline-scoped so a later second operator does not
  require a schema rewrite.
