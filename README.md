# Pipeline Pulse

Pipeline Pulse turns Tennessee Gas Pipeline (TGP) maintenance disclosures into
point-in-time transport scenarios, revision alerts, and an investor-facing
market brief. It answers one narrow question:

> What changed in TGP maintenance, where and when could transport tighten, and
> what would need to confirm before that becomes a tradable gas-market view?

The included curated snapshot, raw source artifacts, UI, and AI-session exports
make the research auditable from a clean clone. The mutable DuckDB file is
created locally by the initial pull and is intentionally not committed. No key
is required for public data collection or browsing. A Codex API key or existing
local Codex login is needed only to generate a new AI memo.

## Run locally

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/). On
macOS, install it with `brew install uv`. The repository pins Python 3.12 for
local development; uv can provision it if it is not already installed.

```bash
uv sync --locked
uv run --locked pipeline-pulse scheduled-collect --mode bootstrap
uv run --locked pipeline-pulse serve
```

`uv sync --locked` selects the pinned Python, creates `.venv`, installs Pipeline
Pulse, and reproduces the exact dependency graph committed in `uv.lock`. If
`pyproject.toml` and the lock ever disagree, it fails instead of silently
resolving a different environment.
For a pip-only fallback, run `python3.12 -m venv .venv` followed by
`./.venv/bin/python -m pip install -e .` and use the `.venv/bin/pipeline-pulse`
executable directly.

The bootstrap is the only first-run data command. It creates
`data/pipeline_pulse.duckdb`, pulls the complete notice index, all available
maintenance details, locations, operating capacity, EIA context, and NWS
degree days, then builds alerts and curated tables. It is safe to rerun if a
source times out. Expect roughly 5–10 minutes because the operator requests are
deliberately paced. The first notice page establishes a baseline; "new notice"
alerts begin with subsequent incremental pulls rather than treating every row
in the initial page as newly arrived.

Open <http://127.0.0.1:8765>. There is no frontend build or second database.
The header's **Refresh data** button runs one bounded background update of the
latest notices, capacity, EIA, and NWS sources, then reloads the current view.
It is local-only, rejects overlapping jobs, and shows source failures without
discarding the last successful data.

The UI leads with the aggregate 30-day TGP pressure calendar and then lets an
analyst double-click into corridors, alerts, notice revisions, report vintages,
source evidence, and market context. The guide explains the gas-market terms
and the intentionally narrow TGP scope.

## What is in the terminal

| Layer | Investor use | Boundary |
| --- | --- | --- |
| TGP critical notices and same-ID versions | See new, superseded, terminated, or silently revised maintenance | A notice is planned operator information, not physical flow |
| TGP Outage Impact Report vintages | Compare the forward capacity schedule with the prior published report | Serial constraints are not summed as independent lost supply |
| TGP operating, scheduled, and available capacity | Test whether a captured nomination schedule would fit through a future limit if unchanged | A conditional shortfall is not confirmed curtailment |
| TGP locations, native segments, and zones | Ground the event and identify the regional market channel | County anchors are approximate; the operator schematic is authoritative for topology |
| EIA storage and Henry Hub physical spot | Frame the national inventory and benchmark regime | Neither attributes a move to TGP or substitutes for regional basis |
| NWS New York City and Boston HDD/CDD | Check whether named Northeast demand pressure overlaps the event window | These are demand proxies, not measured burn or a TGP-wide weighted forecast |
| Optional Yahoo `NG=F` | Personal-use front-month reference | Rolling proxy, not licensed production data or regional cash basis |

The current conclusion is deliberately bounded: TGP maintenance can create a
regional transport or basis watch, but the default public dataset cannot make a
defensible Henry Hub futures-price call without regional basis and measured
flow/rerouting confirmation.

## AI-native architecture

```text
KM / EIA / NWS public sources
          |
          v
immutable raw artifacts + SHA-256 + source/receipt/availability clocks
          |
          v
deterministic parsers, temporal joins, revisions, capacity math, alert rules
          |
          +--------------------> DuckDB + curated CSV + local API + UI
          |
          v
versioned evidence packet -> read-only Codex analyst -> schema/evidence validator
                                                    -> investor research memo
```

This division is intentional. Fetching, hashing, parsing, calculations,
database writes, and scheduling are repeatable code. The agent investigates the
material changes selected by that code, connects transport, inventory, weather,
and price context, handles contradictory evidence, chooses the next research
needed, and explains what is or is not investment-relevant. It cannot rewrite a
fact, invent a source, or promote a scenario beyond the deterministic evidence.

The operating agent uses `gpt-5.6-terra`: the task requires careful synthesis
and uncertainty handling but not the cost of a frontier coding model on every
scheduled refresh. The model is pinned for reproducibility and can be overridden
for evaluation.

## Point-in-time and revision design

Pipeline disclosures are mutable, so final-state history is unsafe for
backtests. Pipeline Pulse preserves five separate clocks where applicable:
operator posting, event effectiveness, receipt, processing, and research
availability.

- Every raw response is content-addressed and archived before parsing.
- Every notice-detail check becomes an observation; a semantic hash distinguishes
  a real field/body change from harmless HTML wrapper changes.
- Reversions to an older body remain new point-in-time transitions.
- `SUPERSEDE`, `TERMINATE`, prior-notice links, response requirements, and
  deadlines are retained.
- Historical API calls use `as_of`; later revisions cannot leak backward.
- The initial report archive is labeled as a backfill. Only ongoing captures can
  prove what the system actually knew at an earlier decision time.

## Successive pulls

After the bootstrap, use the smaller modes below from the repository root:

```bash
# Same bounded latest-data workflow exposed by the UI
uv run --locked pipeline-pulse scheduled-collect --mode refresh

# Notices, missing detail backlog, and rotating same-ID revision checks
uv run --locked pipeline-pulse scheduled-collect --mode incremental

# Delivery, receipt, and directional-segment operating capacity
uv run --locked pipeline-pulse scheduled-collect --mode capacity

# EIA storage, direct-EIA Henry Hub spot, and NWS HDD/CDD
uv run --locked pipeline-pulse scheduled-collect --mode context

# Integrity, source-schema, clock, reconciliation, and coverage gate
uv run --locked pipeline-pulse quality
```

Use `scheduled-collect --mode full-export` for the complete notice index and
location reference. Each collection archives the response, updates DuckDB,
rebuilds deterministic changes and transport scenarios, and refreshes curated
CSV exports. Source errors remain visible in `fetch_runs`; they do not silently
produce an empty dataset.

## Generate the AI brief

Install Codex CLI once if it is not already available:

```bash
npm install -g @openai/codex
uv run --locked pipeline-pulse generate-tgp-insights --if-changed
```

Manual runs can use an existing Codex login. For unattended local cron, put
`CODEX_API_KEY=<your key>` only in the untracked `config/crontab.local`.
Collection and the deterministic app continue to work if the key is absent or
the model call fails. When the key is configured, every successful collection
checks the economic-evidence fingerprint and refreshes the memo only when those
inputs changed.

The runner:

1. blocks on hard data-quality failures;
2. builds a point-in-time evidence packet from allowlisted relations;
3. runs Codex in a read-only sandbox with an exact JSON schema;
4. rejects unknown evidence IDs and raw IDs leaked into investor prose;
5. stores the memo only after validation; and
6. retains the packet, prompt, schema, event stream, output, diagnostics, and
   validation under `sessions/insights/`.

`--if-changed` fingerprints economic evidence, not harmless recaptures, so an
unchanged operator page does not trigger another paid call.

## Query the data

CSV files in `data/curated/` are the committed, portable research tables.
DuckDB is the full local point-in-time store created by the bootstrap:

```python
import duckdb

db = duckdb.connect("data/pipeline_pulse.duckdb", read_only=True)
rows = db.execute(
    """
    SELECT gas_day, transport_state, affected_segment_count,
           largest_single_reduction_dth_per_day,
           largest_conditional_shortfall_dth_per_day,
           peak_segment_id, peak_zone
    FROM latest_tgp_daily_market_state
    ORDER BY gas_day
    """
).fetchall()
```

For another local agent, `GET /api/catalog` describes every allowlisted dataset,
DuckDB relation, row count, JSON endpoint, and CSV download. Useful endpoints:

```text
GET /api/market-state?days=30&as_of=<ISO-8601>
GET /api/alerts?scope=recent&as_of=<ISO-8601>
GET /api/notices/<notice_id>/history?as_of=<ISO-8601>
GET /api/reports
GET /api/transport-impacts?report=<notice_id>&status=monitor
GET /api/market-context?as_of=<ISO-8601>
GET /api/research-brief
```

Arbitrary remote SQL is intentionally unavailable; a local process can query
the DuckDB file read-only.

## Keep it current with cron

```bash
cp config/crontab.example config/crontab.local
# Replace PIPELINE_PULSE_DIR with the absolute clone path, then:
crontab config/crontab.local
```

The example polls notices every 15 minutes, capacity hourly, public market
context every six hours, and the full notice/location export daily. A shared
file lock prevents overlapping jobs. Each successful pull rebuilds derived
tables, curated CSVs, and `data/curated/tgp_dataset_status.json`; when
`CODEX_API_KEY` is configured it also refreshes the AI memo if material evidence
changed. The cron file calls the `.venv` that `uv sync` created directly, so cron
does not need uv on its `PATH`. Nothing is installed into cron automatically.

## Submission evidence

| Assignment requirement | Repository evidence |
| --- | --- |
| Public information to structured data | Raw KM/EIA/NWS artifacts, bootstrap-created DuckDB tables, curated CSVs |
| Useful investor analysis | Transport-fit scenarios, no-double-count daily rollup, tradability gates, AI research brief |
| Timely updates and alerts | Incremental collectors, cron example, material alert ledger, same-ID revision detection |
| Investor-facing UI | Build-free local terminal in `ui/`, served over the DuckDB read model |
| AI central to operating | Raw operating-agent packets, prompts, events, outputs, and validation |
| Quality control | Content hashes, byte verification, source-schema fingerprints, audit clocks, reconciliation warnings, network-free test suite |

`sessions/insights/` contains the product's operating-agent runs, and
`sessions/manifest.json` records their runtime metadata and checksums. Private
development conversations are deliberately excluded from the public repository.

## License

Pipeline Pulse is released under the [MIT License](LICENSE).

## Development

```bash
uv run --locked python -m unittest discover -s tests -v
node --check ui/app.js
```

The test suite is network-free and uses saved fixtures or temporary databases.

Additional design detail lives in:

- `docs/PRODUCT.md` — product promise, non-goals, alert contract, trade gate
- `docs/DATA_MODEL.md` — entities, relationships, and point-in-time clocks
- `docs/SOURCE_INVENTORY.md` — exact sources, cadence, and deferred gaps
- `docs/AI_INSIGHTS.md` — agent input/output and validation contract
- `docs/DATASET_STATUS.md` — measured snapshot coverage and known anomalies
- `docs/EDA_PLAN.md` — analysis sequence from source quality to market meaning

The system does not execute trades, make nominations, or claim that a planned
capacity reduction is lost U.S. supply. Its job is to compress difficult public
pipeline information into a trustworthy research starting point and say exactly
what must happen next before an analyst expresses a view.
