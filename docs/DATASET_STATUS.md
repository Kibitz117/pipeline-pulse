# Dataset status

Pipeline Pulse does not keep hand-written row counts or freshness claims in
documentation. Those values become misleading as soon as the next operator
file arrives.

Every successful `scheduled-collect` run rebuilds the derived alert, transport,
market-state, and curated tables, then writes the current machine-readable
quality snapshot to:

```text
data/curated/tgp_dataset_status.json
```

The snapshot records its generation time, quality-gate result, source findings,
notice and maintenance-detail coverage, outage-report coverage, capacity bundle
alignment, location matching, and raw-artifact integrity. It is a runtime file
and is intentionally not committed because a clean-clone bootstrap regenerates
it from the newly collected source evidence.

To inspect the same current report directly:

```bash
uv run --locked pipeline-pulse quality
```

The investor UI reads current values from DuckDB rather than this document. Its
source timestamps and AI-memo fingerprint make stale operator data or an
outdated memo visible. When `CODEX_API_KEY` is configured, each collection also
checks the evidence fingerprint and regenerates the memo only when material
economic inputs changed. Without a key, current deterministic analysis remains
available and older AI prose is not shown as the current conclusion.

## Stable interpretation boundaries

- A maintenance notice is planned operator information, not measured flow or
  confirmed curtailment.
- Constraints along the same route are not added because the same gas can cross
  several reported locations.
- Capacity snapshots from different gas days or nomination cycles are labeled
  as descriptive changes, not directional tightening or relief.
- County coordinates are approximate map anchors. TGP's Segment / PIN schematic
  is the topology authority.
- EIA storage and Henry Hub spot describe the broader gas balance; they do not
  attribute a price move to TGP.
- NWS New York City and Boston degree days are demand-weather proxies, not
  measured gas burn or a pipeline-wide demand model.
- Regional basis, measured flow, and rerouting evidence are still required to
  promote a TGP transport watch into a defensible trade view.

Known upstream inconsistencies remain explicit in the generated quality report
and underlying rows. The system preserves them rather than silently changing
the operator's values.
