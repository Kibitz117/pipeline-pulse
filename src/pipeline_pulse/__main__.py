from __future__ import annotations

import argparse

from .alerts import build_tgp_alerts
from .collector import (
    collect_eia_storage,
    collect_henry_hub_spot,
    collect_nws_degree_days,
    collect_tgp_critical_export,
    collect_tgp_critical_index,
    collect_tgp_locations,
    collect_tgp_notice_details,
    collect_tgp_operational_capacity,
    collect_yahoo_front_month_futures,
    reprocess_tgp_critical_indexes,
    reprocess_tgp_notice_details,
)
from .curated import export_curated_notice_index, export_tgp_mvp_tables
from .impacts import build_tgp_transport_impacts
from .insights import DEFAULT_INSIGHT_MODEL, generate_tgp_research_memo
from .pipelines import KINDER_MORGAN_PIPELINES
from .quality import build_tgp_quality_report
from .scheduler import (
    run_kinder_morgan_pipeline_collection,
    run_scheduled_collection,
)
from .web import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Pipeline Pulse source data.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser(
        "collect-tgp-critical",
        description="Archive and normalize TGP critical notices.",
    )
    collect.add_argument("--db", default="data/pipeline_pulse.duckdb")
    collect.add_argument("--raw-dir", default="data/raw")
    collect.add_argument("--json", action="store_true")
    export = subparsers.add_parser(
        "collect-tgp-critical-export",
        description="Archive and normalize KM's complete TGP critical XLSX export.",
    )
    export.add_argument("--db", default="data/pipeline_pulse.duckdb")
    export.add_argument("--raw-dir", default="data/raw")
    quality = subparsers.add_parser(
        "quality", description="Report current TGP notice-index data coverage."
    )
    quality.add_argument("--db", default="data/pipeline_pulse.duckdb")
    reprocess = subparsers.add_parser(
        "reprocess-tgp-critical",
        description="Rebuild missing TGP index rows from archived raw artifacts.",
    )
    reprocess.add_argument("--db", default="data/pipeline_pulse.duckdb")
    reprocess_details = subparsers.add_parser(
        "reprocess-tgp-notice-details",
        description="Replay archived TGP detail HTML and outage-impact tables.",
    )
    reprocess_details.add_argument("--db", default="data/pipeline_pulse.duckdb")
    scheduled = subparsers.add_parser(
        "scheduled-collect",
        description="Run one lock-protected collection cycle for cron.",
    )
    scheduled.add_argument(
        "--mode",
        choices=(
            "bootstrap",
            "refresh",
            "incremental",
            "full-export",
            "capacity",
            "context",
        ),
        required=True,
    )
    scheduled.add_argument("--db", default="data/pipeline_pulse.duckdb")
    scheduled.add_argument("--raw-dir", default="data/raw")
    scheduled.add_argument("--lock-file", default="data/pipeline-pulse.lock")
    scheduled.add_argument(
        "--curated-output",
        default="data/curated/tgp_critical_notice_index.csv",
    )
    scheduled.add_argument("--detail-limit", type=int, default=3)
    scheduled.add_argument("--revision-check-limit", type=int, default=3)
    scheduled.add_argument(
        "--bootstrap-detail-limit",
        type=int,
        default=100,
        help="Maximum maintenance details fetched by a first-run bootstrap.",
    )
    pipeline_collect = subparsers.add_parser(
        "collect-km-pipeline",
        description=(
            "Bootstrap or refresh one configured Kinder Morgan pipeline "
            "without applying TGP-specific market assumptions."
        ),
    )
    pipeline_collect.add_argument(
        "--pipeline",
        choices=tuple(sorted(KINDER_MORGAN_PIPELINES)),
        required=True,
    )
    pipeline_collect.add_argument(
        "--mode",
        choices=("bootstrap", "refresh", "full-export"),
        required=True,
    )
    pipeline_collect.add_argument("--db", default="data/pipeline_pulse.duckdb")
    pipeline_collect.add_argument("--raw-dir", default="data/raw")
    pipeline_collect.add_argument("--lock-file", default="data/pipeline-pulse.lock")
    pipeline_collect.add_argument("--detail-limit", type=int, default=5)
    pipeline_collect.add_argument("--revision-check-limit", type=int, default=3)
    pipeline_collect.add_argument("--bootstrap-detail-limit", type=int, default=25)
    curated = subparsers.add_parser(
        "export-curated",
        description="Refresh the reconciled current-notice CSV from DuckDB.",
    )
    curated.add_argument("--db", default="data/pipeline_pulse.duckdb")
    curated.add_argument(
        "--output", default="data/curated/tgp_critical_notice_index.csv"
    )
    mvp_export = subparsers.add_parser(
        "export-tgp-mvp",
        description="Export TGP maintenance, outage, and revision research tables.",
    )
    mvp_export.add_argument("--db", default="data/pipeline_pulse.duckdb")
    mvp_export.add_argument("--output-dir", default="data/curated")
    alerts = subparsers.add_parser(
        "build-tgp-alerts",
        description=(
            "Compare the newest TGP captures with their prior snapshots and "
            "store deterministic investor alerts."
        ),
    )
    alerts.add_argument("--db", default="data/pipeline_pulse.duckdb")
    impacts = subparsers.add_parser(
        "build-tgp-impacts",
        description=(
            "Align TGP outage directions with capacity rows and calculate "
            "conditional scheduled shortfalls."
        ),
    )
    impacts.add_argument("--db", default="data/pipeline_pulse.duckdb")
    storage = subparsers.add_parser(
        "collect-eia-storage",
        description="Archive and normalize EIA weekly natural-gas storage.",
    )
    storage.add_argument("--db", default="data/pipeline_pulse.duckdb")
    storage.add_argument("--raw-dir", default="data/raw")
    spot = subparsers.add_parser(
        "collect-henry-hub-spot",
        description="Archive EIA Henry Hub physical spot prices via FRED.",
    )
    spot.add_argument("--db", default="data/pipeline_pulse.duckdb")
    spot.add_argument("--raw-dir", default="data/raw")
    weather = subparsers.add_parser(
        "collect-nws-degree-days",
        description="Archive NWS hourly forecasts and derive TGP-area HDD/CDD.",
    )
    weather.add_argument("--db", default="data/pipeline_pulse.duckdb")
    weather.add_argument("--raw-dir", default="data/raw")
    futures = subparsers.add_parser(
        "collect-yahoo-futures",
        description=(
            "Archive Yahoo NG=F as an optional local front-month futures proxy."
        ),
    )
    futures.add_argument("--db", default="data/pipeline_pulse.duckdb")
    futures.add_argument("--raw-dir", default="data/raw")
    details = subparsers.add_parser(
        "collect-tgp-notice-details",
        description="Fetch a bounded batch of missing TGP notice details.",
    )
    details.add_argument("--db", default="data/pipeline_pulse.duckdb")
    details.add_argument("--raw-dir", default="data/raw")
    details.add_argument("--limit", type=int, default=3)
    details.add_argument(
        "--revision-check-limit",
        type=int,
        default=3,
        help="Recheck this many least-recently-observed active/recent notices.",
    )
    details.add_argument(
        "--notice-type",
        help="Restrict the backlog to one primary notice type, such as MAINTENANCE.",
    )
    locations = subparsers.add_parser(
        "collect-tgp-locations",
        description=(
            "Archive TGP locations and enrich them with Census county coordinates."
        ),
    )
    locations.add_argument("--db", default="data/pipeline_pulse.duckdb")
    locations.add_argument("--raw-dir", default="data/raw")
    capacity = subparsers.add_parser(
        "collect-tgp-capacity",
        description=(
            "Archive best-available TGP point and segment operational capacity."
        ),
    )
    capacity.add_argument("--db", default="data/pipeline_pulse.duckdb")
    capacity.add_argument("--raw-dir", default="data/raw")
    insights = subparsers.add_parser(
        "generate-tgp-insights",
        description=(
            "Generate and validate an evidence-grounded TGP research memo "
            "with a local Codex CLI session."
        ),
    )
    insights.add_argument("--db", default="data/pipeline_pulse.duckdb")
    insights.add_argument("--sessions-dir", default="sessions/insights")
    insights.add_argument(
        "--model",
        default=DEFAULT_INSIGHT_MODEL,
        help=f"OpenAI model ID (default: {DEFAULT_INSIGHT_MODEL}).",
    )
    insights.add_argument(
        "--if-changed",
        action="store_true",
        help="Skip the agent call when the material evidence fingerprint is unchanged.",
    )
    web = subparsers.add_parser(
        "serve",
        description="Run the local TGP analyst UI over DuckDB.",
    )
    web.add_argument("--db", default="data/pipeline_pulse.duckdb")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "collect-tgp-critical":
        result = collect_tgp_critical_index(
            database_path=args.db,
            raw_root=args.raw_dir,
        )
        if args.json:
            print(result.to_json())
        else:
            print(
                f"TGP critical page {result.page_index + 1}/{result.page_count}: "
                f"stored {result.page_row_count}/{result.source_row_count} index rows; "
                f"notices {result.newest_notice_id}..{result.oldest_notice_id}"
            )
    elif args.command == "quality":
        print(build_tgp_quality_report(args.db).to_json())
    elif args.command == "reprocess-tgp-critical":
        print(reprocess_tgp_critical_indexes(args.db).to_json())
    elif args.command == "reprocess-tgp-notice-details":
        print(reprocess_tgp_notice_details(args.db).to_json())
    elif args.command == "collect-tgp-critical-export":
        print(
            collect_tgp_critical_export(
                database_path=args.db,
                raw_root=args.raw_dir,
            ).to_json()
        )
    elif args.command == "scheduled-collect":
        print(
            run_scheduled_collection(
                mode=args.mode,
                database_path=args.db,
                raw_root=args.raw_dir,
                lock_path=args.lock_file,
                curated_output_path=args.curated_output,
                detail_limit=args.detail_limit,
                revision_check_limit=args.revision_check_limit,
                bootstrap_detail_limit=args.bootstrap_detail_limit,
            ).to_json()
        )
    elif args.command == "collect-km-pipeline":
        print(
            run_kinder_morgan_pipeline_collection(
                pipeline_id=args.pipeline,
                mode=args.mode,
                database_path=args.db,
                raw_root=args.raw_dir,
                lock_path=args.lock_file,
                detail_limit=args.detail_limit,
                revision_check_limit=args.revision_check_limit,
                bootstrap_detail_limit=args.bootstrap_detail_limit,
            ).to_json()
        )
    elif args.command == "export-curated":
        result = export_curated_notice_index(args.db, args.output)
        print(f"exported {result.row_count} rows to {result.output_path}")
    elif args.command == "export-tgp-mvp":
        result = export_tgp_mvp_tables(args.db, args.output_dir)
        print(
            f"exported TGP MVP tables to {result.output_directory}: "
            f"{result.maintenance_notice_rows} notices, "
            f"{result.notice_version_history_rows} notice observations, "
            f"{result.outage_report_rows} reports, "
            f"{result.latest_capacity_rows} latest capacity rows, "
            f"{result.capacity_revision_rows} revisions, "
            f"{result.location_rows} locations, "
            f"{result.operational_capacity_rows} current operational-capacity "
            f"rows from {result.operational_capacity_capture_rows} captures, "
            f"{result.alert_rows} material alerts, "
            f"{result.daily_market_state_rows} daily market-state rows"
            f", {result.transport_impact_rows} transport-impact rows, "
            f"{result.market_context_rows} market-context rows"
        )
    elif args.command == "build-tgp-alerts":
        print(build_tgp_alerts(args.db).to_json())
    elif args.command == "build-tgp-impacts":
        print(build_tgp_transport_impacts(args.db).to_json())
    elif args.command == "collect-eia-storage":
        print(
            collect_eia_storage(
                database_path=args.db,
                raw_root=args.raw_dir,
            ).to_json()
        )
    elif args.command == "collect-henry-hub-spot":
        print(
            collect_henry_hub_spot(
                database_path=args.db,
                raw_root=args.raw_dir,
            ).to_json()
        )
    elif args.command == "collect-nws-degree-days":
        print(
            collect_nws_degree_days(
                database_path=args.db,
                raw_root=args.raw_dir,
            ).to_json()
        )
    elif args.command == "collect-yahoo-futures":
        print(
            collect_yahoo_front_month_futures(
                database_path=args.db,
                raw_root=args.raw_dir,
            ).to_json()
        )
    elif args.command == "collect-tgp-notice-details":
        print(
            collect_tgp_notice_details(
                database_path=args.db,
                raw_root=args.raw_dir,
                limit=args.limit,
                revision_check_limit=args.revision_check_limit,
                notice_type=args.notice_type,
            ).to_json()
        )
    elif args.command == "collect-tgp-locations":
        print(
            collect_tgp_locations(
                database_path=args.db,
                raw_root=args.raw_dir,
            ).to_json()
        )
    elif args.command == "collect-tgp-capacity":
        print(
            collect_tgp_operational_capacity(
                database_path=args.db,
                raw_root=args.raw_dir,
            ).to_json()
        )
    elif args.command == "generate-tgp-insights":
        print(
            generate_tgp_research_memo(
                args.db,
                sessions_directory=args.sessions_dir,
                model=args.model,
                skip_if_unchanged=args.if_changed,
            ).to_json()
        )
    elif args.command == "serve":
        serve(args.db, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
