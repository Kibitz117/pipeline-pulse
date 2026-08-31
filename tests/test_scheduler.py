from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline_pulse.__main__ import build_parser
from pipeline_pulse.insights import InsightRunSummary
from pipeline_pulse.scheduler import (
    CollectionAlreadyRunning,
    exclusive_collection_lock,
    refresh_insights_if_configured,
)


class SchedulerLockTests(unittest.TestCase):
    def test_cli_exposes_one_command_bootstrap(self) -> None:
        args = build_parser().parse_args(["scheduled-collect", "--mode", "bootstrap"])

        self.assertEqual(args.mode, "bootstrap")
        self.assertEqual(args.bootstrap_detail_limit, 100)

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


if __name__ == "__main__":
    unittest.main()
