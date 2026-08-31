from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import pendulum

from pipeline_pulse.artifacts import StoredArtifact
from pipeline_pulse.database import (
    connect_database,
    finish_fetch_run,
    initialize_database,
    start_fetch_run,
    store_artifact_record,
)
from pipeline_pulse.quality import build_artifact_integrity_report


class ArtifactIntegrityTests(unittest.TestCase):
    def test_detects_missing_or_modified_raw_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline.duckdb"
            raw_path = root / "artifact.bin"
            body = b"immutable public evidence"
            raw_path.write_bytes(body)
            observed_at = pendulum.datetime(2026, 8, 28, 18, 0, tz="UTC")
            artifact = StoredArtifact(
                artifact_id="quality:artifact:v1",
                source_code="quality_fixture",
                canonical_url="https://example.test/evidence",
                content_sha256=hashlib.sha256(body).hexdigest(),
                mime_type="application/octet-stream",
                http_status=200,
                requested_at=observed_at.subtract(seconds=1),
                received_at=observed_at,
                recorded_at=observed_at,
                raw_path=raw_path.as_posix(),
                size_bytes=len(body),
                etag=None,
                last_modified=None,
                content_disposition=None,
            )
            connection = connect_database(database_path)
            initialize_database(connection)
            run_id = start_fetch_run(
                connection,
                "quality_fixture",
                requested_at=artifact.requested_at,
            )
            store_artifact_record(connection, run_id, artifact)
            connection.execute(
                "UPDATE source_artifacts SET processed_at = ? WHERE artifact_id = ?",
                [observed_at, artifact.artifact_id],
            )
            finish_fetch_run(connection, run_id)
            connection.close()

            valid = build_artifact_integrity_report(database_path)
            self.assertEqual(valid.status, "passed")
            self.assertEqual(valid.files_checked, 1)

            raw_path.write_bytes(b"modified")
            modified = build_artifact_integrity_report(database_path)
            self.assertEqual(modified.status, "failed")
            self.assertEqual(modified.hash_mismatch_count, 1)
            self.assertEqual(modified.size_mismatch_count, 1)

            raw_path.unlink()
            missing = build_artifact_integrity_report(database_path)
            self.assertEqual(missing.status, "failed")
            self.assertEqual(missing.missing_file_count, 1)


if __name__ == "__main__":
    unittest.main()
