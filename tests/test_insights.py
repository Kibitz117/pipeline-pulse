from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

import pendulum

from pipeline_pulse.database import connect_database, initialize_database
from pipeline_pulse.insights import (
    _stable_fingerprint_value,
    _validate_memo,
    rebuild_session_manifest,
)


def memo(evidence_id: str) -> dict[str, object]:
    return {
        "headline": "Maintenance warrants monitoring",
        "plain_english_summary": "The source reports a future reduction.",
        "why_it_matters": "Transport flexibility may change.",
        "facts": [
            {"claim": "The report contains a reduction.", "evidence_ids": [evidence_id]}
        ],
        "watch_items": [
            {
                "title": "Segment watch",
                "market_channel": "transport",
                "scenario": "Watch for confirmation in a newer cycle.",
                "confirmation_needed": ["New operator data"],
                "invalidation": ["Restriction removed"],
                "confidence": "low",
                "research_status": "monitor",
                "evidence_ids": [evidence_id],
            }
        ],
        "counterevidence": ["No physical-flow data"],
        "missing_data": ["Current capacity"],
        "glossary": [{"term": "Dth/day", "definition": "Dekatherms per day"}],
        "overall_confidence": "low",
    }


class InsightValidationTests(unittest.TestCase):
    def test_rebuilds_checksum_manifest_from_agent_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "test.duckdb"
            insights_directory = root / "sessions" / "insights"
            insights_directory.mkdir(parents=True)
            design_directory = root / "sessions" / "design"
            design_directory.mkdir(parents=True)
            design_export = design_directory / "build.jsonl.gz"
            design_export.write_bytes(gzip.compress(b'{"type":"session_meta"}\n'))
            (design_directory / "build.metadata.json").write_text(
                json.dumps(
                    {
                        "session_id": "design-1",
                        "role": "design_and_implementation",
                        "export_path": "design/build.jsonl.gz",
                    }
                ),
                encoding="utf-8",
            )
            prefix = insights_directory / "run"
            events_path = Path(prefix.as_posix() + ".events.jsonl")
            events_path.write_text('{"type":"done"}\n', encoding="utf-8")
            started_at = pendulum.datetime(2026, 8, 30, 12, tz="UTC")
            connection = connect_database(database_path)
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO agent_runs(
                    agent_run_id, role, model, started_at, completed_at,
                    status, input_artifact_ids, session_path, validation
                ) VALUES (
                    'run-1', 'tgp_research_analyst', 'test-model', ?, ?,
                    'completed', '["artifact-1"]', ?, '{"valid":true}'
                )
                """,
                [started_at, started_at.add(minutes=1), events_path.as_posix()],
            )
            connection.close()

            manifest_path = rebuild_session_manifest(
                database_path,
                sessions_directory=insights_directory,
                codex_binary="/missing/codex",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(len(manifest["sessions"]), 1)
        self.assertEqual(len(manifest["design_sessions"]), 1)
        self.assertEqual(manifest["design_sessions"][0]["session_id"], "design-1")
        self.assertEqual(
            manifest["design_sessions"][0]["file"]["path"],
            "design/build.jsonl.gz",
        )
        self.assertEqual(
            len(manifest["design_sessions"][0]["file"]["sha256"]),
            64,
        )
        self.assertEqual(manifest["sessions"][0]["model"], "test-model")
        self.assertEqual(manifest["sessions"][0]["files"][0]["path"], "insights/run.events.jsonl")
        self.assertEqual(len(manifest["sessions"][0]["files"][0]["sha256"]), 64)

    def test_fingerprint_ignores_capture_identity_but_retains_values(self) -> None:
        first = {
            "evidence_id": "artifact:first",
            "observed_at_utc": "2026-08-30T12:00:00Z",
            "scheduled_quantity": 100,
            "nested": {"capacity_evidence_id": "artifact:first"},
        }
        repeated_capture = {
            "evidence_id": "artifact:second",
            "observed_at_utc": "2026-08-30T13:00:00Z",
            "scheduled_quantity": 100,
            "nested": {"capacity_evidence_id": "artifact:second"},
        }
        changed_value = {**repeated_capture, "scheduled_quantity": 101}

        self.assertEqual(
            _stable_fingerprint_value(first),
            _stable_fingerprint_value(repeated_capture),
        )
        self.assertNotEqual(
            _stable_fingerprint_value(first),
            _stable_fingerprint_value(changed_value),
        )

    def test_accepts_only_packet_evidence_ids(self) -> None:
        validation = _validate_memo(
            memo("artifact:known"),
            valid_evidence_ids={"artifact:known", "artifact:unused"},
        )

        self.assertTrue(validation["valid"])
        self.assertEqual(validation["cited_evidence_count"], 1)

    def test_rejects_unknown_evidence_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown evidence IDs"):
            _validate_memo(
                memo("artifact:invented"),
                valid_evidence_ids={"artifact:known"},
            )

    def test_rejects_raw_artifact_ids_in_investor_prose(self) -> None:
        value = memo("km_tgp_notice_detail:known")
        value["plain_english_summary"] = (
            "Maintenance is active. Evidence: km_tgp_notice_detail:known"
        )

        with self.assertRaisesRegex(ValueError, "raw evidence ID in prose"):
            _validate_memo(
                value,
                valid_evidence_ids={"km_tgp_notice_detail:known"},
            )


if __name__ == "__main__":
    unittest.main()
