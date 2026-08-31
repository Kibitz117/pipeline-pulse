from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pendulum

from pipeline_pulse.artifacts import StoredArtifact
from pipeline_pulse.database import (
    connect_database,
    initialize_database,
    start_fetch_run,
    store_artifact_record,
    store_eia_storage_release,
)
from pipeline_pulse.sources.eia_storage import parse_eia_storage_release


class EiaStorageTests(unittest.TestCase):
    def _payload(self) -> str:
        names = (
            "total lower 48 states",
            "east region",
            "midwest region",
            "mountain region",
            "pacific region",
            "south central region",
            "south central salt region",
            "south central nonsalt region",
        )
        series = []
        for index, name in enumerate(names):
            series.append(
                {
                    "series_id": f"series-{index}",
                    "name": name,
                    "calculated": {
                        "5yr-avg": 100 + index,
                        "net_change": -2 + index,
                        "pct-chg_5yr-avg": 5.5,
                        "pct-change_yrago": -0.9,
                    },
                    "data": [["2026-08-21", 120 + index]],
                }
            )
        return "\ufeff" + json.dumps(
            {
                "release_name": "Weekly Natural Gas Storage Report",
                "release_date": "2026-Aug-27 00:00:00",
                "current_week": "2026-08-21",
                "5yr_avg": "5-year (2021-25) average",
                "series": series,
            }
        )

    def test_parses_release_time_and_stores_point_in_time_metrics(self) -> None:
        release = parse_eia_storage_release(self._payload())
        self.assertEqual(release.available_at.to_iso8601_string(), "2026-08-27T14:30:00Z")
        self.assertEqual(release.series[0].geography, "Lower 48")
        self.assertEqual(release.series[0].working_gas_bcf, 120)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "test.duckdb"
            raw_path = root / "wngsr.json"
            raw_path.write_text(self._payload(), encoding="utf-8")
            received = pendulum.datetime(2026, 8, 27, 14, 31, tz="UTC")
            artifact = StoredArtifact(
                artifact_id="eia-artifact",
                source_code="eia_wngsr",
                canonical_url="https://ir.eia.gov/ngs/wngsr.json",
                content_sha256="e" * 64,
                mime_type="application/json",
                http_status=200,
                requested_at=received.subtract(seconds=1),
                received_at=received,
                recorded_at=received,
                raw_path=raw_path.as_posix(),
                size_bytes=raw_path.stat().st_size,
                etag=None,
                last_modified=None,
                content_disposition=None,
            )
            connection = connect_database(database_path)
            initialize_database(connection)
            run_id = start_fetch_run(
                connection, "eia_wngsr", requested_at=artifact.requested_at
            )
            store_artifact_record(connection, run_id, artifact)
            count = store_eia_storage_release(connection, artifact, release)
            rows = connection.execute(
                """
                SELECT metric, geography, value, available_at
                FROM market_observations
                WHERE geography = 'Lower 48'
                ORDER BY metric
                """
            ).fetchall()
            connection.close()

        self.assertEqual(count, 40)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1][0], "Working gas storage")
        self.assertEqual(rows[-1][2], 120)
        self.assertEqual(pendulum.instance(rows[-1][3]).in_timezone("UTC").hour, 14)


if __name__ == "__main__":
    unittest.main()
