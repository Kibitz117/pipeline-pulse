from __future__ import annotations

import tempfile
import time
import unittest
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from unittest.mock import patch

from pipeline_pulse.__main__ import build_parser
from pipeline_pulse.insights import InsightRunSummary
from pipeline_pulse.scheduler import (
    CollectionAlreadyRunning,
    ScheduledCollectionSummary,
    exclusive_collection_lock,
    refresh_insights_if_configured,
    run_scheduled_collection,
)
from pipeline_pulse.web import RefreshManager


@dataclass(frozen=True)
class FakeCollectionResult:
    status: str = "completed"
    output_path: str = "data/curated/tgp_critical_notice_index.csv"
    row_count: int = 1


class SchedulerLockTests(unittest.TestCase):
    def test_cli_exposes_one_command_bootstrap(self) -> None:
        args = build_parser().parse_args(["scheduled-collect", "--mode", "bootstrap"])

        self.assertEqual(args.mode, "bootstrap")
        self.assertEqual(args.bootstrap_detail_limit, 100)
        refresh_args = build_parser().parse_args(
            ["scheduled-collect", "--mode", "refresh"]
        )
        self.assertEqual(refresh_args.mode, "refresh")

    def test_rejects_overlapping_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            lock_path = Path(temporary_directory) / "collector.lock"
            with (
                exclusive_collection_lock(lock_path),
                self.assertRaises(CollectionAlreadyRunning),
                exclusive_collection_lock(lock_path),
            ):
                self.fail("overlapping lock should not be acquired")

    def test_insight_refresh_is_key_gated_and_fingerprint_aware(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(
                refresh_insights_if_configured("unused.duckdb"),
                {"status": "not_configured"},
            )

        completed = InsightRunSummary(
            agent_run_id="run-1",
            research_memo_id="memo-1",
            status="skipped_unchanged",
            data_fingerprint="fingerprint-1",
            headline="Current memo",
            overall_confidence="medium",
            session_path="sessions/insights/run-1.events.jsonl",
            output_path="sessions/insights/run-1.output.json",
        )
        with (
            patch.dict("os.environ", {"CODEX_API_KEY": "test-key"}, clear=True),
            patch(
                "pipeline_pulse.scheduler.generate_tgp_research_memo",
                return_value=completed,
            ) as generate,
        ):
            result = refresh_insights_if_configured("test.duckdb")

        self.assertEqual(result["status"], "skipped_unchanged")
        generate.assert_called_once_with(
            "test.duckdb",
            skip_if_unchanged=True,
        )

    def test_refresh_mode_collects_latest_sources_then_rebuilds_once(self) -> None:
        result = FakeCollectionResult()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            patches = (
                patch(
                    "pipeline_pulse.scheduler.collect_tgp_critical_index",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.collect_tgp_notice_details",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.collect_tgp_operational_capacity",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.collect_eia_storage",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.collect_henry_hub_spot",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.collect_nws_degree_days",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.build_tgp_alerts",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.build_tgp_transport_impacts",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.export_curated_notice_index",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.export_tgp_mvp_tables",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.export_tgp_dataset_status",
                    return_value=result,
                ),
                patch(
                    "pipeline_pulse.scheduler.refresh_insights_if_configured",
                    return_value={"status": "not_configured"},
                ),
            )
            with ExitStack() as stack:
                entered = [stack.enter_context(context) for context in patches]
                summary = run_scheduled_collection(
                    mode="refresh",
                    database_path=root / "pipeline.duckdb",
                    raw_root=root / "raw",
                    lock_path=root / "refresh.lock",
                    curated_output_path=root / "curated" / "notices.csv",
                )

        self.assertEqual(summary.status, "completed")
        self.assertEqual(
            set(summary.collection or {}),
            {
                "index",
                "details",
                "capacity",
                "eia_storage",
                "henry_hub_spot",
                "nws_degree_days",
                "alerts",
                "transport_impacts",
                "dataset_status",
                "insights",
            },
        )
        for collector in entered[:6]:
            collector.assert_called_once()

    def test_refresh_manager_rejects_overlap_and_reports_completion(self) -> None:
        entered = Event()
        release = Event()
        calls: list[dict[str, object]] = []

        def runner(**kwargs: object) -> ScheduledCollectionSummary:
            calls.append(kwargs)
            entered.set()
            release.wait(timeout=2)
            return ScheduledCollectionSummary(
                mode="refresh",
                status="completed",
                started_at="2026-08-31T17:00:00Z",
                completed_at="2026-08-31T17:01:00Z",
                collection={"insights": {"status": "not_configured"}},
                curated_output_path="data/curated/notices.csv",
                curated_row_count=1,
            )

        manager = RefreshManager("data/pipeline.duckdb", runner=runner)
        started, running = manager.start()
        self.assertTrue(started)
        self.assertEqual(running["status"], "running")
        self.assertTrue(entered.wait(timeout=1))
        second_started, _ = manager.start()
        self.assertFalse(second_started)
        release.set()
        for _ in range(100):
            if manager.status()["status"] != "running":
                break
            time.sleep(0.01)

        completed = manager.status()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["insights_status"], "not_configured")
        self.assertEqual(calls[0]["mode"], "refresh")


if __name__ == "__main__":
    unittest.main()
