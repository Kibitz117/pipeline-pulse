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
from pipeline_pulse.market_state import build_tgp_daily_market_state
from pipeline_pulse.sources.kinder_morgan import KinderMorganNotice
from pipeline_pulse.sources.kinder_morgan_tables import (
    parse_tgp_outage_impact_report,
)


OUTAGE_HTML = """
<span id="WebSplitter1_tmpl1_ContentPlaceHolder1_Label12">
  <table>
    <tr><td></td><td colspan="2">Seven Day Forecast (updated 08/30/26)</td><td></td></tr>
    <tr><td>Station / Seg</td><td>Est Nominal Operating Capacity (Thousand Dth)</td><td>Monday (8/31)</td><td>Primary Outage(s) that may Impact Throughput</td></tr>
    <tr><td>Station / Seg</td><td>capacity</td><td>capacity</td><td>Primary Outage(s)</td></tr>
    <tr><td>Station 9 (segment 109 BH)</td><td>1,027</td><td>640 (387)</td><td>Pig Run</td></tr>
  </table>
</span>
"""


class DailyMarketStateTests(unittest.TestCase):
    def test_uses_largest_single_constraint_instead_of_summing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "pipeline_pulse.duckdb"
            raw_path = root / "notice.html"
            raw_path.write_text(OUTAGE_HTML, encoding="utf-8")
            observed_at = pendulum.datetime(2026, 8, 30, 20, 0, tz="UTC")
            artifact = StoredArtifact(
                artifact_id="detail:market-state:v1",
                source_code="km_tgp_notice_detail",
                canonical_url="https://example.test/market-state",
                content_sha256="c" * 64,
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
                notice_id="403999",
                critical=True,
                notice_type_primary="MAINTENANCE",
                notice_type_secondary="MAINTENANCE",
                status_description="INITIATE",
                prior_notice_id=None,
                subject="TGP Outage Impact Report",
                notice_text="Two overlapping constraints",
                posted_at=pendulum.datetime(
                    2026, 8, 30, 14, 0, tz="America/Chicago"
                ),
                effective_start=pendulum.datetime(
                    2026, 8, 30, 14, 0, tz="America/Chicago"
                ),
                effective_end=pendulum.datetime(
                    2026, 9, 1, 9, 0, tz="America/Chicago"
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
            for index, (segment, gross_reduction, shortfall) in enumerate(
                (("109", 500_000, 100_000), ("110", 700_000, 200_000)),
                start=1,
            ):
                connection.execute(
                    """
                    INSERT INTO tgp_transport_impact_assessments(
                        assessment_id, pipeline_id, report_artifact_id,
                        report_notice_id, report_updated_on, source_table_index,
                        source_row_index, period_start, period_end, station_label,
                        operator_segment_id, outage_flow_direction,
                        capacity_flow_direction, direction_mapping_method,
                        tgp_zone, capacity_artifact_id, capacity_location_name,
                        baseline_gas_day, baseline_cycle,
                        baseline_source_posted_at,
                        baseline_operating_capacity_dth_per_day,
                        baseline_scheduled_quantity_dth_per_day,
                        baseline_available_capacity_dth_per_day,
                        forecast_nominal_capacity_dth_per_day,
                        forecast_operating_capacity_dth_per_day,
                        gross_reduction_dth_per_day,
                        conditional_scheduled_shortfall_dth_per_day,
                        forecast_headroom_vs_baseline_schedule_dth_per_day,
                        baseline_timing, match_method, research_status,
                        price_mapping_status, price_mapping_reason,
                        benchmark_reference_url, unresolved_reasons, evidence,
                        calculated_at
                    ) VALUES (
                        ?, 'TGP', ?, '403999', '2026-08-30', ?, ?,
                        '2026-09-02', '2026-09-02', ?, ?, 'FH', 'TD1',
                        'operator_default_direction', 'Z1', ?, ?,
                        '2026-08-31', 'EVE', '2026-08-31T01:00:00Z',
                        1000000, ?, 0, 1000000, ?, ?, ?, 0,
                        'pre_event', 'unique_segment_direction',
                        'research_scenario', 'unresolved',
                        'No regional cash-price mapping.', NULL, '[]', '{}',
                        '2026-08-31T02:00:00Z'
                    )
                    """,
                    [
                        f"assessment-{index}",
                        artifact.artifact_id,
                        index,
                        index,
                        f"Station {index}",
                        segment,
                        artifact.artifact_id,
                        f"Segment {segment}",
                        1_000_000 - shortfall,
                        1_000_000 - gross_reduction,
                        gross_reduction,
                        shortfall,
                    ],
                )
            connection.close()

            market_state = build_tgp_daily_market_state(
                database_path,
                decision_at=pendulum.datetime(2026, 8, 31, 12, 0, tz="UTC"),
            )

            target_day = next(
                day for day in market_state["days"] if day["date"] == "2026-09-02"
            )
            self.assertEqual(len(market_state["days"]), 30)
            self.assertEqual(target_day["affected_segment_count"], 2)
            self.assertEqual(
                target_day["largest_single_reduction_dth_per_day"], 700_000
            )
            self.assertEqual(
                target_day["largest_conditional_shortfall_dth_per_day"], 200_000
            )
            self.assertNotEqual(
                target_day["largest_conditional_shortfall_dth_per_day"], 300_000
            )
            self.assertEqual(target_day["screen_state"], "active_review")
            self.assertEqual(
                market_state["tradable_market_picture"]["status"],
                "unconfirmed_regional_basis_watch",
            )
            self.assertFalse(
                market_state["tradable_market_picture"]["demand_overlap"]
            )


if __name__ == "__main__":
    unittest.main()
