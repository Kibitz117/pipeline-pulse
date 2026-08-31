from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from pipeline_pulse.collector import (
    TGP_CRITICAL_INDEX_URL,
    TGP_NOTICE_DETAIL_URL,
    collect_tgp_critical_index,
    collect_tgp_notice_details,
    reprocess_tgp_critical_indexes,
)
from pipeline_pulse.http import FetchResult
from pipeline_pulse.quality import build_notice_index_quality_report
from pipeline_pulse.curated import export_curated_notice_index


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "kinder_morgan"
    / "critical_index_page_0.html"
)
DETAIL_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "kinder_morgan"
    / "notice_403767.html"
)


class _FixtureClient:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def fetch(self, url: str, *, accept: str = "*/*") -> FetchResult:
        if url != TGP_CRITICAL_INDEX_URL or accept != "text/html":
            raise AssertionError("collector requested an unexpected resource")
        return FetchResult(
            canonical_url=url,
            status_code=200,
            sent_ts_ns=1_777_000_000_000_000_000,
            headers_received_ts_ns=1_777_000_000_010_000_000,
            received_ts_ns=1_777_000_000_020_000_000,
            body=self.body,
            content_type="text/html; charset=utf-8",
        )


class _DetailFixtureClient:
    def fetch(self, url: str, *, accept: str = "*/*") -> FetchResult:
        expected_url = TGP_NOTICE_DETAIL_URL.format(notice_id="403824")
        if url != expected_url or accept != "text/html":
            raise AssertionError("detail collector requested an unexpected resource")
        body = DETAIL_FIXTURE.read_text(encoding="utf-8").replace(
            "403767", "403824"
        ).encode("utf-8")
        return FetchResult(
            canonical_url=url,
            status_code=200,
            sent_ts_ns=1_777_000_001_000_000_000,
            headers_received_ts_ns=1_777_000_001_010_000_000,
            received_ts_ns=1_777_000_001_020_000_000,
            body=body,
            content_type="text/html; charset=utf-8",
        )


class _RevisionFixtureClient:
    def __init__(self, body: bytes, received_ts_ns: int) -> None:
        self.body = body
        self.received_ts_ns = received_ts_ns

    def fetch(self, url: str, *, accept: str = "*/*") -> FetchResult:
        expected_url = TGP_NOTICE_DETAIL_URL.format(notice_id="403824")
        if url != expected_url or accept != "text/html":
            raise AssertionError("revision collector requested an unexpected resource")
        return FetchResult(
            canonical_url=url,
            status_code=200,
            sent_ts_ns=self.received_ts_ns - 20_000_000,
            headers_received_ts_ns=self.received_ts_ns - 10_000_000,
            received_ts_ns=self.received_ts_ns,
            body=self.body,
            content_type="text/html; charset=utf-8",
        )


class TgpCollectionTests(unittest.TestCase):
    def test_rechecks_known_notice_and_ignores_nonsemantic_html_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline_pulse.duckdb"
            collect_tgp_critical_index(
                database_path=database_path,
                raw_root=root / "raw",
                client=_FixtureClient(FIXTURE.read_bytes()),
            )
            original = DETAIL_FIXTURE.read_text(encoding="utf-8").replace(
                "403767", "403824"
            )
            first = collect_tgp_notice_details(
                database_path=database_path,
                raw_root=root / "raw",
                limit=1,
                revision_check_limit=0,
                notice_type="storage",
                minimum_interval_seconds=0,
                client=_RevisionFixtureClient(
                    original.encode("utf-8"),
                    1_777_000_001_020_000_000,
                ),
                sleep=lambda _: None,
            )
            revised_body = original.replace(
                "scheduled up to capacity", "scheduled below capacity"
            )
            revised = collect_tgp_notice_details(
                database_path=database_path,
                raw_root=root / "raw",
                limit=1,
                revision_check_limit=1,
                notice_type="storage",
                minimum_interval_seconds=0,
                client=_RevisionFixtureClient(
                    revised_body.encode("utf-8"),
                    1_777_000_101_020_000_000,
                ),
                sleep=lambda _: None,
            )
            wrapper_only = revised_body.replace(
                "<body>", '<body><input type="hidden" value="volatile">', 1
            )
            unchanged = collect_tgp_notice_details(
                database_path=database_path,
                raw_root=root / "raw",
                limit=1,
                revision_check_limit=1,
                notice_type="storage",
                minimum_interval_seconds=0,
                client=_RevisionFixtureClient(
                    wrapper_only.encode("utf-8"),
                    1_777_000_201_020_000_000,
                ),
                sleep=lambda _: None,
            )
            connection = duckdb.connect(str(database_path), read_only=True)
            try:
                version_count = connection.execute(
                    "SELECT count(*) FROM notice_versions WHERE notice_id = '403824'"
                ).fetchone()[0]
                observation_count = connection.execute(
                    "SELECT count(*) FROM notice_version_observations "
                    "WHERE notice_id = '403824'"
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(first.new_detail_notice_ids, ("403824",))
        self.assertEqual(revised.rechecked_notice_ids, ("403824",))
        self.assertEqual(revised.revised_notice_ids, ("403824",))
        self.assertEqual(unchanged.unchanged_notice_ids, ("403824",))
        self.assertEqual(version_count, 2)
        self.assertEqual(observation_count, 3)

    def test_archives_then_loads_normalized_index_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline_pulse.duckdb"
            summary = collect_tgp_critical_index(
                database_path=database_path,
                raw_root=root / "raw",
                client=_FixtureClient(FIXTURE.read_bytes()),
            )

            self.assertEqual(summary.source_row_count, 597)
            self.assertEqual(summary.page_row_count, 2)
            self.assertEqual(summary.newest_notice_id, "403824")
            self.assertTrue(Path(summary.raw_path).is_file())

            connection = duckdb.connect(str(database_path), read_only=True)
            try:
                artifact_count = connection.execute(
                    "SELECT count(*) FROM source_artifacts"
                ).fetchone()[0]
                observation_count = connection.execute(
                    "SELECT count(*) FROM notice_index_observations"
                ).fetchone()[0]
                run_status = connection.execute(
                    "SELECT status FROM fetch_runs"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(artifact_count, 1)
            self.assertEqual(observation_count, 2)
            self.assertEqual(run_status, "completed")
            current_connection = duckdb.connect(str(database_path), read_only=True)
            try:
                current_count = current_connection.execute(
                    "SELECT count(*) FROM current_notice_index"
                ).fetchone()[0]
            finally:
                current_connection.close()
            self.assertEqual(current_count, 2)

            quality = build_notice_index_quality_report(database_path)
            self.assertEqual(quality.advertised_page_count, 8)
            self.assertEqual(quality.advertised_row_count, 597)
            self.assertEqual(quality.latest_parsed_row_count, 2)
            self.assertAlmostEqual(
                quality.latest_advertised_row_coverage_ratio,
                2 / 597,
            )
            curated_path = root / "curated.csv"
            curated = export_curated_notice_index(database_path, curated_path)
            self.assertEqual(curated.row_count, 2)
            self.assertEqual(len(curated_path.read_text().splitlines()), 3)

    def test_reprocesses_normalized_rows_from_raw_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline_pulse.duckdb"
            collect_tgp_critical_index(
                database_path=database_path,
                raw_root=root / "raw",
                client=_FixtureClient(FIXTURE.read_bytes()),
            )
            connection = duckdb.connect(str(database_path))
            try:
                connection.execute("DELETE FROM notice_index_observations")
                connection.execute("DELETE FROM notice_index_pages")
            finally:
                connection.close()

            summary = reprocess_tgp_critical_indexes(database_path)

            self.assertEqual(summary.artifacts_found, 1)
            self.assertEqual(summary.artifacts_reprocessed, 1)
            self.assertEqual(summary.rows_reprocessed, 2)
            self.assertEqual(
                build_notice_index_quality_report(database_path).latest_parsed_row_count,
                2,
            )

    def test_fetches_newest_missing_detail_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline_pulse.duckdb"
            collect_tgp_critical_index(
                database_path=database_path,
                raw_root=root / "raw",
                client=_FixtureClient(FIXTURE.read_bytes()),
            )

            summary = collect_tgp_notice_details(
                database_path=database_path,
                raw_root=root / "raw",
                limit=1,
                minimum_interval_seconds=0,
                client=_DetailFixtureClient(),
                sleep=lambda _: None,
            )

            self.assertEqual(summary.completed_notice_ids, ("403824",))
            self.assertEqual(summary.failed, 0)
            self.assertEqual(summary.remaining, 1)
            quality = build_notice_index_quality_report(database_path)
            self.assertEqual(quality.notices_with_detail_count, 1)
            self.assertEqual(quality.notices_missing_detail_count, 1)

    def test_filters_detail_backfill_by_primary_notice_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline_pulse.duckdb"
            collect_tgp_critical_index(
                database_path=database_path,
                raw_root=root / "raw",
                client=_FixtureClient(FIXTURE.read_bytes()),
            )

            summary = collect_tgp_notice_details(
                database_path=database_path,
                raw_root=root / "raw",
                limit=1,
                notice_type="storage",
                minimum_interval_seconds=0,
                client=_DetailFixtureClient(),
                sleep=lambda _: None,
            )

            self.assertEqual(summary.notice_type, "storage")
            self.assertEqual(summary.completed_notice_ids, ("403824",))
            self.assertEqual(summary.remaining, 0)


if __name__ == "__main__":
    unittest.main()
