from __future__ import annotations

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
    store_notice_version,
    store_outage_impact_observations,
)
from pipeline_pulse.sources.kinder_morgan import KinderMorganNotice
from pipeline_pulse.sources.kinder_morgan_tables import (
    parse_tgp_outage_impact_report,
)
from pipeline_pulse.web import TgpReadModel


OUTAGE_HTML = """
<span id="WebSplitter1_tmpl1_ContentPlaceHolder1_Label12">
  <table>
    <tr><td></td><td colspan="2">Seven Day Forecast (updated 08/06/26)</td><td></td></tr>
    <tr><td>Station / Seg</td><td>Est Nominal Operating Capacity (Thousand Dth)</td><td>Monday (8/10)</td><td>Primary Outage(s) that may Impact Throughput</td></tr>
    <tr><td>Station / Seg</td><td>capacity</td><td>capacity</td><td>Primary Outage(s)</td></tr>
    <tr><td>Station 9 (segment 109 BH)</td><td>1,027</td><td>640 (387)</td><td>Pig Run</td></tr>
  </table>
</span>
"""


class TgpReadModelTests(unittest.TestCase):
    def test_reads_overview_constraints_and_notice_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline_pulse.duckdb"
            raw_path = root / "notice.html"
            raw_path.write_text(OUTAGE_HTML, encoding="utf-8")
            observed_at = pendulum.datetime(2026, 8, 6, 20, 0, tz="UTC")
            artifact = StoredArtifact(
                artifact_id="detail:403321:v1",
                source_code="km_tgp_notice_detail",
                canonical_url="https://example.test/403321",
                content_sha256="a" * 64,
                mime_type="text/html",
                http_status=200,
                requested_at=observed_at.subtract(seconds=1),
                received_at=observed_at,
                recorded_at=observed_at,
                raw_path=raw_path.as_posix(),
                size_bytes=raw_path.stat().st_size,
                etag=None,
                last_modified=None,
                content_disposition=None,
            )
            notice = KinderMorganNotice(
                tsp_number="1939164",
                tsp_name="TENNESSEE GAS PIPELINE",
                notice_id="403321",
                critical=True,
                notice_type_primary="MAINTENANCE",
                notice_type_secondary="MAINTENANCE",
                status_description="INITIATE",
                prior_notice_id=None,
                subject="TGP Outage Impact Report",
                notice_text="Station 9 Pig Run",
                posted_at=pendulum.datetime(
                    2026, 8, 6, 15, 57, 49, tz="America/Chicago"
                ),
                effective_start=pendulum.datetime(
                    2026, 8, 6, 15, 57, 49, tz="America/Chicago"
                ),
                effective_end=pendulum.datetime(
                    2026, 8, 7, 9, 0, tz="America/Chicago"
                ),
                required_response=None,
                response_at=None,
            )
            connection = connect_database(database_path)
            initialize_database(connection)
            run_id = start_fetch_run(
                connection,
                "km_tgp_notice_detail",
                requested_at=artifact.requested_at,
            )
            store_artifact_record(connection, run_id, artifact)
            store_notice_version(connection, artifact, notice)
            store_outage_impact_observations(
                connection,
                artifact,
                notice.notice_id,
                parse_tgp_outage_impact_report(OUTAGE_HTML),
            )
            finish_fetch_run(connection, run_id)

            revised_html = OUTAGE_HTML.replace(
                "updated 08/06/26", "updated 08/13/26"
            ).replace("640 (387)", "500 (527)")
            revised_raw_path = root / "notice-revised.html"
            revised_raw_path.write_text(revised_html, encoding="utf-8")
            revised_observed_at = pendulum.datetime(2026, 8, 13, 20, 0, tz="UTC")
            revised_artifact = StoredArtifact(
                artifact_id="detail:403400:v1",
                source_code="km_tgp_notice_detail",
                canonical_url="https://example.test/403400",
                content_sha256="b" * 64,
                mime_type="text/html",
                http_status=200,
                requested_at=revised_observed_at.subtract(seconds=1),
                received_at=revised_observed_at,
                recorded_at=revised_observed_at,
                raw_path=revised_raw_path.as_posix(),
                size_bytes=revised_raw_path.stat().st_size,
                etag=None,
                last_modified=None,
                content_disposition=None,
            )
            revised_notice = KinderMorganNotice(
                tsp_number="1939164",
                tsp_name="TENNESSEE GAS PIPELINE",
                notice_id="403400",
                critical=True,
                notice_type_primary="MAINTENANCE",
                notice_type_secondary="MAINTENANCE",
                status_description="SUPERSEDE",
                prior_notice_id="403321",
                subject="TGP Outage Impact Report",
                notice_text="Station 9 Pig Run revised",
                posted_at=pendulum.datetime(
                    2026, 8, 13, 15, 57, 49, tz="America/Chicago"
                ),
                effective_start=pendulum.datetime(
                    2026, 8, 13, 15, 57, 49, tz="America/Chicago"
                ),
                effective_end=pendulum.datetime(
                    2026, 8, 14, 9, 0, tz="America/Chicago"
                ),
                required_response=None,
                response_at=None,
            )
            revised_run_id = start_fetch_run(
                connection,
                "km_tgp_notice_detail",
                requested_at=revised_artifact.requested_at,
            )
            store_artifact_record(connection, revised_run_id, revised_artifact)
            store_notice_version(connection, revised_artifact, revised_notice)
            store_outage_impact_observations(
                connection,
                revised_artifact,
                revised_notice.notice_id,
                parse_tgp_outage_impact_report(revised_html),
            )
            finish_fetch_run(connection, revised_run_id)
            connection.execute(
                """
                INSERT INTO market_observations(
                    market_observation_id, series_code, metric, geography,
                    period_start, value, unit, available_at, vintage, artifact_id
                ) VALUES
                    ('hh-1', 'TEST_HH', 'spot_price', 'Henry Hub',
                     '2026-08-05T00:00:00Z', 2.50, 'USD/MMBtu',
                     '2026-08-05T23:00:00Z', '2026-08-05', ?),
                    ('hh-2', 'TEST_HH', 'spot_price', 'Henry Hub',
                     '2026-08-12T00:00:00Z', 3.10, 'USD/MMBtu',
                     '2026-08-12T23:00:00Z', '2026-08-12', ?)
                """,
                [artifact.artifact_id, revised_artifact.artifact_id],
            )
            connection.close()

            model = TgpReadModel(database_path)
            overview = model.overview()
            constraints = model.constraints(report_notice_id="403321")
            latest_constraints = model.constraints()
            revisions = model.revisions(report_notice_id="403400")
            map_data = model.map_data(report_notice_id="403400")
            notices = model.notices()
            lifecycle_history = model.notice_history("403321")
            historical_as_of = pendulum.datetime(2026, 8, 7, tz="UTC")
            historical_lifecycle = model.notice_history(
                "403321", as_of=historical_as_of
            )
            historical_context = model.market_context(as_of=historical_as_of)
            market_state = model.market_state(
                as_of=pendulum.datetime(2026, 8, 31, tz="UTC")
            )
            catalog = model.data_catalog()

            self.assertEqual(overview["maintenance_notice_count"], 2)
            self.assertEqual(overview["latest_max_reduction_dth_per_day"], 527_000)
            self.assertEqual(len(constraints), 1)
            self.assertEqual(constraints[0]["operator_segment_id"], "109")
            self.assertEqual(constraints[0]["reduction_pct"], 37.7)
            self.assertEqual(latest_constraints[0]["reduction_pct"], 51.3)
            self.assertEqual(len(revisions), 1)
            self.assertEqual(
                revisions[0]["operating_capacity_change_dth_per_day"],
                -140_000,
            )
            self.assertEqual(map_data["segments"], [])
            self.assertEqual(map_data["coverage"]["segment_anchor_count"], 0)
            self.assertIn(
                "TGP_Segment_-_Pin_Map",
                map_data["sources"]["operator_segment_pin_map"],
            )
            self.assertEqual(len(notices), 2)
            self.assertEqual(
                lifecycle_history["related_operator_updates"][0]["notice_id"],
                "403400",
            )
            self.assertEqual(
                historical_lifecycle["related_operator_updates"],
                [],
            )
            self.assertEqual(len(model.notices(as_of=historical_as_of)), 1)
            self.assertEqual(model.notice("403321")["notice_text"], "Station 9 Pig Run")
            self.assertIsNone(model.notice("403400", as_of=historical_as_of))
            self.assertIsNone(model.notice("missing"))
            self.assertEqual(historical_context["selected"][0]["value"], 2.5)
            self.assertEqual(historical_context["latest"][0]["value"], 3.1)
            self.assertEqual(len(market_state["days"]), 30)
            self.assertEqual(
                market_state["aggregation_method"],
                "largest_single_constraint_no_sum",
            )
            self.assertEqual(catalog["access"], "read_only_allowlisted")
            self.assertFalse(catalog["arbitrary_sql_over_http"])
            dataset_ids = {
                dataset["dataset_id"] for dataset in catalog["datasets"]
            }
            self.assertIn("market-context", dataset_ids)
            self.assertIn("daily-market-state", dataset_ids)
            self.assertIn("transport-impacts", dataset_ids)


if __name__ == "__main__":
    unittest.main()
