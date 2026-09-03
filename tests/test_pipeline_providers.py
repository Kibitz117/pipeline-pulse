from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from pipeline_pulse.collector import (
    collect_kinder_morgan_critical_index,
    collect_kinder_morgan_notice_details,
)
from pipeline_pulse.database import connect_database, initialize_database
from pipeline_pulse.http import FetchResult
from pipeline_pulse.pipelines import get_pipeline_config
from pipeline_pulse.web import TgpReadModel

FIXTURES = Path(__file__).parent / "fixtures" / "kinder_morgan"


def fetch_result(url: str, body: bytes, *, received_ts_ns: int) -> FetchResult:
    return FetchResult(
        canonical_url=url,
        status_code=200,
        sent_ts_ns=received_ts_ns - 20_000_000,
        headers_received_ts_ns=received_ts_ns - 10_000_000,
        received_ts_ns=received_ts_ns,
        body=body,
        content_type="text/html; charset=utf-8",
    )


class NgplIndexClient:
    def fetch(self, url: str, *, accept: str = "*/*") -> FetchResult:
        config = get_pipeline_config("NGPL")
        if url != config.critical_index_url or accept != "text/html":
            raise AssertionError("unexpected NGPL index request")
        return fetch_result(
            url,
            (FIXTURES / "critical_index_page_0.html").read_bytes(),
            received_ts_ns=1_777_100_000_020_000_000,
        )


class NgplDetailClient:
    def fetch(self, url: str, *, accept: str = "*/*") -> FetchResult:
        config = get_pipeline_config("NGPL")
        expected_url = config.notice_detail_url("403824")
        if url != expected_url or accept != "text/html":
            raise AssertionError("unexpected NGPL detail request")
        body = (
            (FIXTURES / "notice_403767.html")
            .read_text(encoding="utf-8")
            .replace("403767", "403824")
            .replace(
                "1939164-TENNESSEE GAS PIPELINE",
                "6931794-NATURAL GAS PIPELINE CO.",
            )
            .encode("utf-8")
        )
        return fetch_result(
            url,
            body,
            received_ts_ns=1_777_100_001_020_000_000,
        )


class PipelineProviderTests(unittest.TestCase):
    def test_registry_builds_official_ngpl_endpoints(self) -> None:
        pipeline = get_pipeline_config("ngpl")

        self.assertEqual(pipeline.tsp_number, "6931794")
        self.assertEqual(pipeline.ferc_cid, "C002096")
        self.assertIn("code=NGPL", pipeline.critical_index_url)
        self.assertIn("TSP=NGPL", pipeline.portal_url)
        self.assertFalse(pipeline.supports_outage_impact_report)

    def test_database_registers_both_pipeline_systems(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            connection = connect_database(Path(temporary_directory) / "pipeline.duckdb")
            initialize_database(connection)
            rows = connection.execute(
                """
                SELECT pipeline_id, tsp_number, ferc_cid
                FROM pipeline_systems
                ORDER BY pipeline_id
                """
            ).fetchall()
            connection.close()

        self.assertEqual(
            rows,
            [
                ("NGPL", "6931794", "C002096"),
                ("TGP", "1939164", "C000020"),
            ],
        )

    def test_ngpl_notices_are_isolated_from_tgp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline.duckdb"
            collect_kinder_morgan_critical_index(
                pipeline_id="NGPL",
                database_path=database_path,
                raw_root=root / "raw",
                client=NgplIndexClient(),
            )
            details = collect_kinder_morgan_notice_details(
                pipeline_id="NGPL",
                database_path=database_path,
                raw_root=root / "raw",
                limit=1,
                revision_check_limit=0,
                minimum_interval_seconds=0,
                client=NgplDetailClient(),
                sleep=lambda _: None,
            )
            connection = duckdb.connect(str(database_path), read_only=True)
            counts = connection.execute(
                """
                SELECT pipeline_id, count(*)
                FROM notice_versions
                GROUP BY pipeline_id
                ORDER BY pipeline_id
                """
            ).fetchall()
            tgp_index_count = connection.execute(
                "SELECT count(*) FROM current_notice_index WHERE pipeline_id = 'TGP'"
            ).fetchone()[0]
            connection.close()

            catalog = TgpReadModel(database_path).pipeline_catalog()

        self.assertEqual(details.completed_notice_ids, ("403824",))
        self.assertEqual(counts, [("NGPL", 1)])
        self.assertEqual(tgp_index_count, 0)
        ngpl = next(row for row in catalog["pipelines"] if row["pipeline_id"] == "NGPL")
        self.assertEqual(ngpl["notice_count"], 2)
        self.assertEqual(ngpl["detailed_notice_count"], 1)
        self.assertEqual(ngpl["market_model_status"], "raw_data_only")


if __name__ == "__main__":
    unittest.main()
