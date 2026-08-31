# Pipeline Pulse engineering standards

Pipeline Pulse is a read-only investor research system. It collects public
pipeline-operating information and public market context; it does not nominate
gas, contact operators, or execute trades.

## Non-negotiable data rules

1. Store the raw artifact before parsing or agent analysis.
2. Preserve `posted_at`, `effective_at`, `received_at`, `processed_at`, and
   `available_at` as separate clocks.
3. Never overwrite a notice. A changed body or attachment creates a new
   version keyed by its content hash.
4. Only join market data whose `available_at` is at or before the alert's
   decision time. Revised history must not leak into point-in-time analysis.
5. Every extracted fact must retain a source artifact, evidence excerpt or
   location, extraction method, and confidence.
6. Do not describe correlation as causation. Henry Hub is initially a national
   regime benchmark, not a local pipeline basis price.

## Division of work

- Deterministic code fetches, hashes, parses stable fields, calculates capacity
  deltas, performs temporal joins, scores alerts, and writes DuckDB/Parquet.
- Agents link notice revisions into events, resolve ambiguous facility names,
  classify the impact channel, explain relevance, and investigate parser
  failures.
- High-severity or low-confidence agent outputs require a verification pass.

## Source behavior

- Use a descriptive User-Agent and conservative polling intervals.
- Honor source terms, robots directives, and server errors.
- A failed or changed parser must degrade to a visible source-health warning;
  it must not silently emit empty data.
- Keep source-specific logic behind an adapter and keep normalized models free
  of operator-specific field names.

## Verification

- Parsers use saved fixtures and network-free tests.
- Capacity reductions reconcile: `nominal - operating = reduction`.
- Notice chains cannot contain cycles.
- Alerts expose score components and missing inputs.
- Run `python -m unittest discover -s tests -v` before handoff.
