from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pendulum

from pipeline_pulse.database import connect_database, initialize_database
from pipeline_pulse.impacts import build_tgp_transport_impacts


class TransportImpactTests(unittest.TestCase):
    def test_maps_native_directions_and_calculates_conditional_shortfall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "test.duckdb"
            connection = connect_database(database_path)
            initialize_database(connection)
            connection.execute(
                """
                INSERT INTO fetch_runs VALUES
                    ('report-run', 'km_tgp_notice_detail', '2026-08-30T12:00:00Z',
                     '2026-08-30T12:01:00Z', 'completed', '{}', NULL),
                    ('capacity-run', 'km_tgp_capacity', '2026-08-30T13:00:00Z',
                     '2026-08-30T13:01:00Z', 'completed', '{}', NULL)
                """
            )
            connection.execute(
                """
                INSERT INTO source_artifacts(
                    artifact_id, run_id, source_code, canonical_url,
                    content_sha256, mime_type, http_status, requested_at,
                    received_at, processed_at, recorded_at, raw_path, metadata
                ) VALUES
                    ('report-art', 'report-run', 'km_tgp_notice_detail',
                     'https://example.test/report', repeat('a', 64), 'text/html', 200,
                     '2026-08-30T12:00:00Z', '2026-08-30T12:01:00Z',
                     '2026-08-30T12:01:00Z', '2026-08-30T12:01:00Z',
                     '/tmp/report.html', '{}'),
                    ('capacity-art', 'capacity-run', 'km_tgp_capacity',
                     'https://example.test/capacity', repeat('b', 64), 'text/csv', 200,
                     '2026-08-30T13:00:00Z', '2026-08-30T13:01:00Z',
                     '2026-08-30T13:01:00Z', '2026-08-30T13:01:00Z',
                     '/tmp/capacity.csv', '{}')
                """
            )
            connection.execute(
                """
                INSERT INTO notice_versions(
                    pipeline_id, notice_id, version_sha256, artifact_id,
                    critical, notice_type_primary, notice_type_secondary,
                    status_description, subject, notice_text, posted_at,
                    effective_start, effective_end, first_seen_at, last_seen_at
                ) VALUES (
                    'TGP', 'test-report', repeat('a', 64), 'report-art', true,
                    'MAINTENANCE', 'MAINTENANCE', 'INITIATE',
                    'TGP Outage Impact Report', 'test', '2026-08-30T12:00:00Z',
                    '2026-08-30T12:00:00Z', '2026-09-02T12:00:00Z',
                    '2026-08-30T12:01:00Z', '2026-08-30T12:01:00Z'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO outage_impact_observations VALUES (
                    'report-art', 'TGP', 'test-report', 'seven_day',
                    'Seven Day Forecast', '2026-08-30', 'Monday',
                    '2026-08-31', '2026-08-31', 'Station 860 (segment 548 BH)',
                    '548', 'BH', '1,400', '600 (800)', 1400000, 600000,
                    800000, 800000, true, 'Compressor maintenance', 1, 1,
                    '2026-08-30T12:01:00Z'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO capacity_exports VALUES (
                    'capacity-art', 'TGP', 'segment', NULL, '1939164',
                    'TENNESSEE GAS PIPELINE', '2026-08-30T09:00:00Z',
                    '2026-08-30', 'Evening', 'segment', 'Dth/day',
                    '2026-08-30T12:30:00Z', 'Net scheduled quantity', 1, 1,
                    repeat('c', 64), 'test',
                    'TD1 is map direction; TD2 is opposite; schedules are net.',
                    '2026-08-30T13:01:00Z'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO capacity_observations(
                    capacity_observation_id, pipeline_id, capacity_kind,
                    source_row_position, operator_segment_id, location_name,
                    zone, gas_day, effective_at, cycle, flow_direction,
                    design_capacity_dth_per_day,
                    operating_capacity_dth_per_day,
                    scheduled_quantity_dth_per_day,
                    available_capacity_dth_per_day, interruptible_scheduled,
                    all_quantity_available, available_reconciles,
                    source_posted_at, observed_at, available_at, artifact_id
                ) VALUES (
                    'capacity-row', 'TGP', 'segment', 1, '548', 'STA 860', '01',
                    '2026-08-30', '2026-08-30T09:00:00Z', 'Evening', 'TD2',
                    1400000, 1400000, 900000, 500000, false, false, true,
                    '2026-08-30T12:30:00Z', '2026-08-30T13:01:00Z',
                    '2026-08-30T13:01:00Z', 'capacity-art'
                )
                """
            )
            connection.close()

            summary = build_tgp_transport_impacts(
                database_path,
                calculated_at=pendulum.datetime(2026, 8, 30, 14, tz="UTC"),
            )
            connection = connect_database(database_path)
            row = connection.execute(
                """
                SELECT capacity_flow_direction, tgp_zone,
                       conditional_scheduled_shortfall_dth_per_day,
                       forecast_headroom_vs_baseline_schedule_dth_per_day,
                       baseline_timing, match_method, research_status
                FROM tgp_transport_impact_assessments
                """
            ).fetchone()
            connection.close()

        self.assertEqual(summary.matched_count, 1)
        self.assertEqual(row[0], "TD2")
        self.assertEqual(row[1], "Z1")
        self.assertEqual(row[2], 300_000)
        self.assertEqual(row[3], 0)
        self.assertEqual(row[4], "pre_event")
        self.assertEqual(row[5], "normalized_name")
        self.assertEqual(row[6], "research_scenario")


if __name__ == "__main__":
    unittest.main()
