# Dataset status

Snapshot date: 2026-08-30. This is a collection audit, not an investment
conclusion.

## TGP critical-notice index

- 609 current notice IDs: 597 from the initial full export plus 12 newer IDs
  observed on the August 30 page.
- Complete primary/secondary type and post/effective/end timestamps: 609/609.
- The newest HTML page supplies subjects for 75 current records.
- Full detail bodies, status, and prior-notice links loaded into DuckDB: 49/609.
- Maintenance detail coverage: 43/43.
- Ten initial detail requests failed under the workspace network sandbox; all
  ten notice IDs succeeded on retry and unresolved detail failures are zero.
- End timestamps using the apparent 2049 open-ended sentinel: 35.
- Latest full-export row coverage: 597/597.
- Source count anomaly: the captured HTML/XLSX footers reported 1 while the
  page rendered 75 rows across 8 pages and the export parsed 597 rows.

Initial full-export primary-type distribution (before the 12 newer IDs):

| Primary type | Notices |
| --- | ---: |
| Pipeline Conditions | 458 |
| TSP Capacity Offering | 43 |
| Maintenance | 42 |
| Capacity Constraint | 18 |
| Operational Flow Order | 16 |
| Force Majeure | 10 |
| Storage | 8 |
| Rates and Charges | 2 |

`current_notice_index` is the EDA entrypoint. It uses the latest complete XLSX
export as the notice universe, overlays the newest HTML page for recent fields,
and retains the artifact and observation timestamp selected for every row. The
curated CSV is regenerated from that view after each scheduled collection.

## TGP maintenance data

- 43 maintenance notices posted from 2026-06-01 through 2026-08-28.
- 46 maintenance-detail observations across 43 semantic notice versions. The
  first three live revision checks were unchanged; raw captures remain archived
  while the semantic revision count correctly remains zero.
- 15 distinct embedded TGP Outage Impact Report source vintages across 16
  captures; the additional capture is an unchanged revision check and is not
  duplicated in the report selector.
- 7,745 normalized station-period capture observations covering 40 station labels and
  39 operator segments from 2026-06-01 through 2026-11-29.
- Latest report: 467 station-period rows across 30 station labels; 441 rows
  contain an operating-capacity estimate.
- 274 non-zero operating-capacity revisions across successive reports.
- Four explicit reduction values fail to reconcile to nominal minus operating
  capacity. They occur in two duplicated August 13 report postings and remain
  flagged as source inconsistencies.

## TGP location reference

- 1,044 unique operator locations across 107 native segments, 198
  operator-reported county/area labels, and 16 states.
- 1,036 locations (99.2%) mapped to official Census county internal points.
- Eight offshore-area locations remain intentionally unmapped: South Pass,
  South Timbalier, West Cameron, and Mustang Island Large Block.
- The operator export currently contains `WORCHESTER` and `VERMILLION`;
  deterministic aliases resolve them to Worcester and Vermilion. Connecticut
  legacy counties use the official 2021 Gazetteer because the 2025 Census
  reference uses planning regions.
- The location export's own `source_as_of` clock moved backward between two
  captures. Pipeline Pulse preserves it but defines the current location
  snapshot by newest `observed_at` so an operator clock regression cannot hide
  the newest archived artifact.
- Current quality gate: every archived file matches its recorded SHA-256 and
  byte size; no audit clocks are invalid; all 39 outage-report segments match
  the operator location reference; the current 29-column schema matches the
  tested parser contract. Nine raw artifacts from visible failed parser runs
  remain archived and explicitly marked unprocessed.

## TGP operational capacity

- Two complete live bundles are archived: gas day 2026-08-28 / Intraday 1 and
  gas day 2026-08-30 / Evening. The latter is the current UI snapshot.
- 518 delivery-point rows, 305 receipt-point rows, and 214 directional segment
  rows covering 106 native segments: 1,037 observations total.
- All three source footer counts reconcile to parsed rows. One published
  availability value differs from the simple operating-minus-scheduled check;
  it remains visible with TGP's netting and intraday-adjustment caveat.
- 820/823 point rows resolve to the current location reference. The three
  unmatched native location IDs are retained for investigation rather than
  dropped or fuzzy-matched.
- 1,033/1,037 rows resolve to a native segment. The unresolved rows involve
  native segment IDs 260, 945, and 5360 and remain queryable with their source
  labels.
- 29 of the 106 capacity segments also appear in the latest forward Outage
  Impact Report; 28 have a positive calculated reduction in that vintage. The
  report begins August 31, so the August 28 capacity bundle is a baseline for
  later planned-versus-realized comparisons, not a same-day impact measurement.
- The capacity quality gate passes. One sandbox-blocked fetch attempt is
  recorded as failed, followed by a successful run, so unresolved failures are
  zero.

## Deterministic changes and alerts

- 39 material alerts are queryable: 12 newly observed critical notices, 14
  forward outage-report revisions, and 13 segment capacity/tightness changes.
- Alert IDs are deterministic, rebuilds are idempotent, and every alert stores
  before/after values, score components, source artifacts, and a source URL.
- The two capacity bundles use different gas days and nomination cycles, so
  their 13 changes are descriptive monitoring flags. The stored 0.70 internal
  comparison-quality weight reflects that they are not like-for-like intraday
  movement or a physical-flow claim; the UI shows the caveat instead of the
  number.

## Transport impact translation

- The latest outage vintage contains 220 positive station-period reductions.
  All 220 resolve to a segment-capacity row after applying the operator's
  default-direction convention (`FH -> TD1`, `BH -> TD2`) and the tested station
  matcher.
- Thirty rows are `research_scenario`: with the fresh August 30 pre-event
  baseline held constant, captured net schedules exceed forecast operating
  capacity. The largest conditional shortfall is 668,684 Dth/day at Station
  860 / segment 548 BH.
- These scenarios cover ten segments across six TGP zones. The immediate
  seven-day modeled gap peaks at only 3,684 Dth/day; the risk is forward-loaded,
  with the 668,684 Dth/day single-constraint peak on September 14–20.
- The other 190 rows are `monitor`. No row is currently
  `no_trade_mapping`, but that state remains explicit for future ambiguous or
  unsupported source rows.
- These are unchanged-schedule comparisons, not confirmed curtailment,
  rerouting, physical flow, price effect, or lost production.

## Daily TGP market state

- The latest outage forecast and direction-matched capacity baseline produce
  30 daily rows from August 31 through September 29.
- Today's largest conditional schedule gap is 3,684 Dth/day, below the explicit
  50,000 Dth/day review screen. The largest single forward gap is 668,684
  Dth/day at Station 860 / Segment 548 BH on September 14.
- Segment 399 FH in Pennsylvania contributes a separate 487,875 Dth/day
  scenario during the September 7–20 window.
- These figures are deliberately not added. The same gas can cross multiple
  TGP locations, so an additive total would overstate system supply impact.
- Daily NWS markers cover August 31 through September 5. They show cooling
  degree days at the named New York City and Boston anchors, but end before the
  first transport-review date on September 7. The demand overlap gate is
  therefore `not_observed`, not bearish or bullish.
- The current tradability state is `unconfirmed_regional_basis_watch`:
  transport exposure is present and storage context is available, while
  regional price and flow/rerouting confirmation remain unobserved.

## EIA weekly storage context

- The August 27 release for the week ending August 21 is archived as 40
  observations across eight EIA geographies and five metrics.
- Lower 48 working gas is 3,184 Bcf, 15 Bcf higher week over week and 5.5%
  above the five-year average. East is 5.1% above its five-year average; South
  Central is 3.0% above.
- `available_at` is the public 10:30 a.m. Eastern release time. Storage is
  balance context and is never attributed to a TGP maintenance row.

## Spot and demand-weather context

- EIA's August 26 direct Henry Hub release is archived with 492 recent daily
  observations; August 25 is the latest at $2.70/MMBtu.
- The EIA release date is retained as the vintage and receipt time is used as
  conservative `available_at`; historical rows are not backdated into earlier
  decision replays.
- NWS hourly forecasts are archived for New York City and Boston. The current
  capture yields six complete calendar days per anchor and 36 normalized rows:
  mean temperature, HDD, and CDD.
- These two anchors frame Northeast demand weather. They are not measured gas
  burn, precipitation analysis, or a pipeline-wide demand-weighted index.
- Yahoo `NG=F` is implemented only as a manual local-research proxy and is not
  required by scheduled context collection.

## Next collection order

1. Keep new TGP critical notices and their details current on the local cron.
2. Build genuine hourly capacity history so cycle-to-cycle and day-to-day
   changes can be compared with the forward maintenance calendar.
3. Normalize the 2049 end-date convention as an explicit open-ended flag while
   preserving the raw timestamp.
4. Analyze report-to-report capacity revisions and separate physical outages
   from meeting reminders and administrative maintenance notices.
5. Collect planned-service notices and revisions if the critical-notice stream
   leaves meaningful gaps.
6. Use the location layer to test which segment/zone relationships are useful;
   add exact facilities or structured route geometry only from defensible
   operator or regulatory sources.
7. Use the new NWS-derived HDD/CDD and direct-EIA Henry Hub spot vintages in
   event EDA. Keep precipitation out of scope and keep Yahoo `NG=F` opt-in.
8. The next price-data gap is licensed regional TGP cash basis (NGI or Platts),
   paired with measured flow or rerouting evidence—not another national proxy.

The current product read is therefore a forward regional transport watch, not
evidence of a present U.S. gas shortage or a Henry Hub futures call.

## AI research memo

- The latest completed read-only agent run uses evidence fingerprint
  `bf02e860773f3d7d02792ee136dcfe654a28effcd8216c331274699e4fd1ee4b`.
- Its cited facts and three conditional watch items use only packet evidence;
  validation passed with no unknown evidence IDs.
- The capacity bundle is beyond the 30-hour freshness threshold. EIA storage,
  direct-EIA Henry Hub spot, and NWS degree-day context are present; the memo
  notes that warm New York City weather adds cooling-demand pressure while
  above-normal storage moderates the national backdrop. Physical-flow,
  rerouting, regional price, and contract confirmation remain absent.
- Its lead read is that near-term TGP pressure is limited, the principal
  exposure begins September 7, and the largest single constraint peaks during
  September 14–20. The regional-basis watch is explicitly not trade-ready. It
  preserves the daily layer's non-additive methodology and evidence gates.
- Raw packet, prompt, output schema, JSONL event stream, final output, and
  validation result are preserved under `sessions/insights/`.
- The result demonstrates the intended behavior: surface potentially useful
  segment relationships while prominently explaining why they are not yet a
  trade.
