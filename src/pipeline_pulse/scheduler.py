from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Literal, TextIO

import pendulum

from .collector import (
    collect_tgp_critical_export,
    collect_tgp_critical_index,
    collect_tgp_locations,
    collect_tgp_operational_capacity,
    collect_tgp_notice_details,
    collect_eia_storage,
    collect_henry_hub_spot,
    collect_nws_degree_days,
)
from .curated import export_curated_notice_index, export_tgp_mvp_tables
from .alerts import build_tgp_alerts
from .impacts import build_tgp_transport_impacts


CollectionMode = Literal[
    "bootstrap",
    "incremental",
    "full-export",
    "capacity",
    "context",
]


class CollectionAlreadyRunning(RuntimeError):
    """A prior scheduled collection still owns the local source lock."""


@contextmanager
def exclusive_collection_lock(path: str | Path) -> Iterator[TextIO]:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CollectionAlreadyRunning(str(lock_path)) from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(pendulum.now("UTC").to_iso8601_string()))
        lock_file.flush()
        yield lock_file
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


@dataclass(frozen=True)
class ScheduledCollectionSummary:
    mode: CollectionMode
    status: Literal["completed", "skipped_locked"]
    started_at: str
    completed_at: str
    collection: dict[str, object] | None
    curated_output_path: str | None
    curated_row_count: int | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def run_scheduled_collection(
    *,
    mode: CollectionMode,
    database_path: str | Path,
    raw_root: str | Path,
    lock_path: str | Path,
    curated_output_path: str | Path = "data/curated/tgp_critical_notice_index.csv",
    detail_limit: int = 3,
    revision_check_limit: int = 3,
    bootstrap_detail_limit: int = 100,
) -> ScheduledCollectionSummary:
    started_at = pendulum.now("UTC")
    try:
        with exclusive_collection_lock(lock_path):
            if mode == "bootstrap":
                export_result = collect_tgp_critical_export(
                    database_path=database_path,
                    raw_root=raw_root,
                )
                location_result = collect_tgp_locations(
                    database_path=database_path,
                    raw_root=raw_root,
                )
                detail_result = collect_tgp_notice_details(
                    database_path=database_path,
                    raw_root=raw_root,
                    limit=bootstrap_detail_limit,
                    revision_check_limit=0,
                    notice_type="MAINTENANCE",
                )
                capacity_result = collect_tgp_operational_capacity(
                    database_path=database_path,
                    raw_root=raw_root,
                )
                collection = {
                    "export": asdict(export_result),
                    "locations": asdict(location_result),
                    "maintenance_details": asdict(detail_result),
                    "capacity": asdict(capacity_result),
                }
                context_sources = (
                    ("eia_storage", collect_eia_storage),
                    ("henry_hub_spot", collect_henry_hub_spot),
                    ("nws_degree_days", collect_nws_degree_days),
                )
                for source_name, collect_source in context_sources:
                    try:
                        result = collect_source(
                            database_path=database_path,
                            raw_root=raw_root,
                        )
                        collection[source_name] = asdict(result)
                    except Exception as exc:
                        collection[source_name] = {
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
            elif mode == "incremental":
                index_result = collect_tgp_critical_index(
                    database_path=database_path,
                    raw_root=raw_root,
                )
                detail_result = collect_tgp_notice_details(
                    database_path=database_path,
                    raw_root=raw_root,
                    limit=detail_limit,
                    revision_check_limit=revision_check_limit,
                )
                collection = {
                    "index": asdict(index_result),
                    "details": asdict(detail_result),
                }
            elif mode == "full-export":
                export_result = collect_tgp_critical_export(
                    database_path=database_path,
                    raw_root=raw_root,
                )
                location_result = collect_tgp_locations(
                    database_path=database_path,
                    raw_root=raw_root,
                )
                collection = {
                    "export": asdict(export_result),
                    "locations": asdict(location_result),
                }
            elif mode == "capacity":
                capacity_result = collect_tgp_operational_capacity(
                    database_path=database_path,
                    raw_root=raw_root,
                )
                collection = {"capacity": asdict(capacity_result)}
            elif mode == "context":
                collection = {}
                context_sources = (
                    ("eia_storage", collect_eia_storage),
                    ("henry_hub_spot", collect_henry_hub_spot),
                    ("nws_degree_days", collect_nws_degree_days),
                )
                completed_sources = 0
                for source_name, collect_source in context_sources:
                    try:
                        result = collect_source(
                            database_path=database_path,
                            raw_root=raw_root,
                        )
                        collection[source_name] = asdict(result)
                        completed_sources += 1
                    except Exception as exc:
                        collection[source_name] = {
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                if completed_sources == 0:
                    raise RuntimeError("all configured market-context sources failed")
            else:  # pragma: no cover - argparse and Literal guard this path
                raise ValueError(f"unsupported collection mode: {mode}")
            alert_summary = build_tgp_alerts(database_path)
            collection["alerts"] = asdict(alert_summary)
            try:
                impact_summary = build_tgp_transport_impacts(database_path)
                collection["transport_impacts"] = asdict(impact_summary)
            except RuntimeError as exc:
                collection["transport_impacts"] = {
                    "status": "not_ready",
                    "reason": str(exc),
                }
            curated = export_curated_notice_index(
                database_path,
                curated_output_path,
            )
            export_tgp_mvp_tables(
                database_path,
                Path(curated_output_path).parent,
            )
            return ScheduledCollectionSummary(
                mode=mode,
                status="completed",
                started_at=started_at.to_iso8601_string(),
                completed_at=pendulum.now("UTC").to_iso8601_string(),
                collection=collection,
                curated_output_path=curated.output_path,
                curated_row_count=curated.row_count,
            )
    except CollectionAlreadyRunning:
        return ScheduledCollectionSummary(
            mode=mode,
            status="skipped_locked",
            started_at=started_at.to_iso8601_string(),
            completed_at=pendulum.now("UTC").to_iso8601_string(),
            collection=None,
            curated_output_path=None,
            curated_row_count=None,
        )
