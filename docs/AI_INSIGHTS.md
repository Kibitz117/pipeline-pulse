# AI insight contract

The research agent connects TGP maintenance and operational-capacity evidence. It
does not search for unrelated datasets, fetch new sources, change normalized
facts, or turn a hypothesis into a trading recommendation.

## Deterministic input

`tgp_research_packet_v8` contains:

- the latest captured point and segment capacity bundle, source clocks, and
  source caveats;
- reconciled counts for zero availability, operating below design, and
  reference coverage;
- the latest forward Outage Impact Report and its largest revisions;
- native-segment overlaps between capacity and maintenance;
- deterministic `FH -> TD1` and `BH -> TD2` direction alignment, station-row
  match method, TGP zone, conditional scheduled shortfall, headroom, and
  unresolved rerouting assumptions;
- aggregate near-term, 8-to-30-day, and later transport-risk horizons, with
  distinct segment/zone coverage and the largest single constraint rather than
  a misleading sum of overlapping rows;
- a compact 30-day market-state calendar with current, seven-day, and forward
  peaks plus the six most exposed segment/direction corridors;
- an explicit tradability funnel covering transport, overlapping demand
  weather, inventory, regional-price confirmation, and flow/rerouting evidence;
- EIA Lower 48, East, and South Central storage facts whose release
  `available_at` is no later than the decision timestamp;
- the latest captured direct-EIA Henry Hub physical spot observation and NWS
  HDD/CDD forecast vintage for New York City and Boston available by the
  decision timestamp;
- a deterministic per-anchor weather summary with complete-day count, total
  HDD/CDD, mean temperature, forecast window, provider, and evidence IDs;
- an optional Yahoo `NG=F` quote only when explicitly collected, with its
  contract label, quote clock, and rolling-symbol limitation;
- the highest-priority recent new-notice, same-ID notice-revision,
  outage-revision, and capacity alerts,
  including immutable before/after evidence and deterministic score components;
- exact artifact IDs and explicit interpretation limits;
- freshness and the deterministic quality-gate result.

The packet fingerprint excludes capture-specific artifact IDs, observation
timestamps, and the time at which the UI is opened. It is driven by material
alerts, transport-impact values, storage, spot, and weather vintages, and
whether capacity crossed the stale boundary. Re-fetching harmless or identical
data does not pay for another memo.

The alert ledger, not the model, decides that a change occurred. The agent may
explain a change's possible channel but cannot alter its status, score,
before/after values, or gas-day/cycle comparison warning.

## Agent output

The read-only agent must return strict JSON containing:

- direct facts with artifact IDs;
- a plain-English summary and why-it-matters explanation;
- no more than three conditional watch items;
- one explicit status per watch item: `no_trade_mapping`, `monitor`, or
  `research_scenario`;
- confirmation and invalidation checks for every watch item;
- counterevidence, missing data, a small glossary, and an internal uncertainty
  assessment retained for evaluation. The UI exposes the underlying freshness,
  evidence, and missing confirmation rather than a top-level confidence label.

The validator rejects a fact or scenario that cites an artifact outside the
packet. The raw packet, prompt, JSON schema, JSONL event stream, final response,
stderr, and validation report are retained in `sessions/insights/`. The memo is
written to `research_memos` only after validation succeeds.

## Scheduled operation

The example cron captures capacity at minute 7 and invokes
`generate-tgp-insights --if-changed` at minute 12 only when `CODEX_API_KEY` is
configured in the user's untracked `config/crontab.local`. Collection is not
coupled to model availability: no key or a failed agent never blocks raw data.

The runner pins `gpt-5.6-terra` and ignores machine-specific Codex config and
rules. Terra is used for its intelligence/cost balance: the job requires
careful scenario framing, evidence attribution, and uncertainty calibration,
but does not justify the cost of the flagship model on each update. The model
can be overridden explicitly for evaluation with `--model`.

## Language boundary

The agent may say that a confirmed transport restriction could reduce routing
flexibility or create a scenario for upstream/downstream basis separation. It
may not call posted availability physical flow, infer lost production,
override the deterministic direction mapping, invent price data, attribute EIA
storage to one TGP event, or claim a notice caused a price move.

User-facing output must sound like an analyst addressing an investor. It must
state the underlying market fact directly and must not mention packet versions,
database state, collectors, adapters, configured series, or product scope.

## Current source boundary

The current product remains TGP maintenance, outage revisions, operating
capacity, and location grounding, supplemented by EIA storage, direct-EIA Henry
Hub physical spot, and two NWS Northeast degree-day anchors. Regional TGP cash
basis, measured flow/rerouting, licensed futures, and other operators remain
outside the default evidence boundary.

## Future trade-layer gate

An agent may not propose a trade expression until its input packet supplies all
of the following deterministic fields:

- gross transport reduction and the comparable scheduled/operating rows;
- a bounded at-risk volume with unresolved rerouting explicitly represented;
- the affected TGP segment, direction, rate zone, leg, and regional balance;
- the named balance denominator, observation vintage, and calculated share;
- the exact regional benchmark or contract, delivery month, event-day overlap,
  and source/licensing status;
- contemporaneous price observations available by the decision timestamp;
- confirmation, invalidation, and material counterevidence.

The agent must distinguish three possible outputs:

1. `no_trade_mapping`: operationally interesting, but no defensible tradable
   location or contract mapping;
2. `monitor`: a plausible balance or basis channel exists, but fresh flow,
   substitution, or price confirmation is missing;
3. `research_scenario`: captured nominations would not fit within forecast
   capacity if unchanged, so a directional transport or basis hypothesis
   merits bounded follow-up. It is not a contract mapping, price call, or
   execution instruction.

A TGP zone basis instrument is normally more direct than Henry Hub flat price.
The agent may select standard Henry Hub `NG` only when the packet documents the
additional mechanism by which the event affects Henry Hub deliverability or a
meaningful share of the broader U.S. balance. Dividing event MMBtu by contract
size is permitted only as a scale illustration and must never be presented as
position sizing or predicted price sensitivity.
