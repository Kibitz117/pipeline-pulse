from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pendulum

from pipeline_pulse.alerts import build_tgp_alerts, normalize_alert_semantics
from pipeline_pulse.artifacts import StoredArtifact
from pipeline_pulse.database import (
    connect_database,
    finish_fetch_run,
    initialize_database,
    start_fetch_run,
    store_artifact_record,
    store_capacity_export,
    store_notice_version,
)
from pipeline_pulse.sources.kinder_morgan import KinderMorganNotice
from pipeline_pulse.sources.kinder_morgan_capacity import (
    TgpCapacityExport,
    TgpCapacityRow,
)
from pipeline_pulse.web import TgpReadModel


def store_segment_snapshot(
    database_path: Path,
    raw_path: Path,
    *,
    suffix: str,
    observed_at: pendulum.DateTime,
    operating: int,
    gas_day: pendulum.Date | None = None,
    cycle: str = "EVENING",
) -> None:
    artifact = StoredArtifact(
        artifact_id=f"capacity:{suffix}",
        source_code="km_tgp_segment_capacity",
        canonical_url="https://example.test/tgp-segments",
        content_sha256=suffix * 64,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        http_status=200,
        requested_at=observed_at.subtract(seconds=2),
        received_at=observed_at,
        recorded_at=observed_at,
        raw_path=raw_path.as_posix(),
        size_bytes=raw_path.stat().st_size,
        etag=None,
        last_modified=None,
        content_disposition=None,
    )
    scheduled = 600_000
    export = TgpCapacityExport(
        capacity_kind="segment",
        point_role=None,
        tsp_number="1939164",
        tsp_name="TENNESSEE GAS PIPELINE",
        effective_at=pendulum.datetime(2026, 8, 30, 14, 0, tz="America/Chicago"),
        gas_day=gas_day or pendulum.date(2026, 8, 30),
        cycle=cycle,
        location_purpose="Segment",
        measurement_basis="Dth",
        source_posted_at=observed_at.subtract(minutes=10),
        quantity_description="Best Available",
        source_footer_row_count=1,
        schema_sha256="f" * 64,
        comments="Test fixture",
        rows=(
            TgpCapacityRow(
                row_position=1,
                operator_location_id=None,
                operator_segment_id="307",
                location_name="SEGMENT 307",
                zone="Zone 4",
                design_capacity_dth_per_day=1_000_000,
                operating_capacity_dth_per_day=operating,
                scheduled_quantity_dth_per_day=scheduled,
                available_capacity_dth_per_day=max(0, operating - scheduled),
                interruptible_scheduled=False,
                flow_indicator="TD1",
                all_quantity_available=False,
                quantity_reason=None,
                available_reconciles=True,
            ),
        ),
    )
    connection = connect_database(database_path)
    run_id = start_fetch_run(
        connection,
        "tgp_operational_capacity",
        requested_at=artifact.requested_at,
    )
    store_artifact_record(connection, run_id, artifact)
    store_capacity_export(connection, artifact, export)
    finish_fetch_run(connection, run_id)
    connection.close()


def store_notice_observation(
    database_path: Path,
    raw_path: Path,
    *,
    suffix: str,
    observed_at: pendulum.DateTime,
    notice_text: str,
    required_response: str | None = None,
    response_at: pendulum.DateTime | None = None,
) -> object:
    raw_path.write_text(notice_text, encoding="utf-8")
    artifact = StoredArtifact(
        artifact_id=f"notice:403900:{suffix}",
        source_code="km_tgp_notice_detail",
        canonical_url="https://example.test/403900",
        content_sha256=suffix * 64,
        mime_type="text/html",
        http_status=200,
        requested_at=observed_at.subtract(seconds=2),
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
        notice_id="403900",
        critical=True,
        notice_type_primary="MAINTENANCE",
        notice_type_secondary="MAINTENANCE",
        status_description="INITIATE",
        prior_notice_id=None,
        subject="Station 100 maintenance",
        notice_text=notice_text,
        posted_at=pendulum.datetime(2026, 8, 30, 9, 0, tz="America/Chicago"),
        effective_start=pendulum.datetime(2026, 9, 2, 9, 0, tz="America/Chicago"),
        effective_end=pendulum.datetime(2026, 9, 3, 9, 0, tz="America/Chicago"),
        required_response=required_response,
        response_at=response_at,
    )
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = start_fetch_run(
        connection,
        "km_tgp_notice_detail",
        requested_at=artifact.requested_at,
    )
    store_artifact_record(connection, run_id, artifact)
    result = store_notice_version(connection, artifact, notice)
    finish_fetch_run(connection, run_id)
    connection.close()
    return result


class AlertBuildTests(unittest.TestCase):
    def test_legacy_cross_period_alert_is_normalized_for_research(self) -> None:
        alert = {
            "event_type": "capacity_snapshot_change",
            "current_status": "worsened",
            "change_type": "operating_capacity_decrease",
            "headline": "Segment 307 operating capacity fell",
            "evidence": {
                "comparison_warning": "Different scheduling periods.",
                "subject": {"operator_segment_id": "307"},
                "delta": {"operating_capacity_dth_per_day": -200_000},
            },
        }

        normalized = normalize_alert_semantics(alert)

        self.assertEqual(normalized["current_status"], "changed")
        self.assertEqual(normalized["change_type"], "operating_capacity_changed")
        self.assertNotIn("fell", normalized["headline"])

    def test_notice_revisions_are_point_in_time_and_reversions_are_preserved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "test.duckdb"
            first_at = pendulum.datetime(2026, 8, 30, 15, 0, tz="UTC")
            revised_at = first_at.add(hours=1)
            unchanged_at = revised_at.add(hours=1)
            reverted_at = unchanged_at.add(hours=1)

            first = store_notice_observation(
                database_path,
                root / "notice-a.html",
                suffix="a",
                observed_at=first_at,
                notice_text="Capacity reduced by 100,000 Dth/day.",
            )
            revised = store_notice_observation(
                database_path,
                root / "notice-b.html",
                suffix="b",
                observed_at=revised_at,
                notice_text="Capacity reduced by 250,000 Dth/day.",
            )
            unchanged = store_notice_observation(
                database_path,
                root / "notice-c.html",
                suffix="c",
                observed_at=unchanged_at,
                notice_text="Capacity reduced by 250,000 Dth/day.",
            )

            first_alerts = build_tgp_alerts(database_path)
            model = TgpReadModel(database_path)
            before_revision = model.notice("403900", as_of=first_at.add(minutes=30))
            after_revision = model.notice("403900", as_of=revised_at.add(minutes=30))
            history_after_unchanged = model.notice_history("403900")
            alerts_before_revision = model.alerts(
                scope="recent", as_of=first_at.add(minutes=30), limit=10
            )
            alerts_after_revision = model.alerts(
                scope="recent", as_of=revised_at.add(minutes=30), limit=10
            )

            reverted = store_notice_observation(
                database_path,
                root / "notice-d.html",
                suffix="d",
                observed_at=reverted_at,
                notice_text="Capacity reduced by 100,000 Dth/day.",
            )
            second_alerts = build_tgp_alerts(database_path)
            replayed_reversion = model.notice(
                "403900", as_of=reverted_at.add(minutes=1)
            )
            final_history = model.notice_history("403900")
            revision_feed = model.alerts(scope="recent", limit=10)
            response_required_at = reverted_at.add(hours=1)
            response_change = store_notice_observation(
                database_path,
                root / "notice-e.html",
                suffix="e",
                observed_at=response_required_at,
                notice_text="Capacity reduced by 100,000 Dth/day.",
                required_response="1",
                response_at=response_required_at.add(hours=2),
            )
            third_alerts = build_tgp_alerts(database_path)
            response_history = model.notice_history("403900")

        self.assertTrue(first.is_new_version)
        self.assertTrue(revised.is_revision_observation)
        self.assertFalse(unchanged.is_revision_observation)
        self.assertTrue(reverted.is_revision_observation)
        self.assertEqual(first_alerts.notice_revision_alert_count, 1)
        self.assertEqual(second_alerts.notice_revision_alert_count, 2)
        self.assertEqual(
            before_revision["notice_text"],
            "Capacity reduced by 100,000 Dth/day.",
        )
        self.assertEqual(
            after_revision["notice_text"],
            "Capacity reduced by 250,000 Dth/day.",
        )
        self.assertEqual(len(history_after_unchanged["versions"]), 2)
        self.assertEqual(alerts_before_revision["alert_count"], 0)
        self.assertEqual(alerts_after_revision["alert_count"], 1)
        self.assertEqual(
            replayed_reversion["notice_text"],
            "Capacity reduced by 100,000 Dth/day.",
        )
        self.assertEqual(len(final_history["versions"]), 3)
        revision_items = [
            item
            for item in revision_feed["items"]
            if item["event_type"] == "notice_content_revision"
        ]
        self.assertEqual(len(revision_items), 2)
        self.assertIn("operator text", revision_items[0]["evidence"]["changed_fields"])
        self.assertTrue(response_change.is_revision_observation)
        self.assertEqual(third_alerts.notice_revision_alert_count, 3)
        self.assertEqual(len(response_history["versions"]), 4)
        self.assertIn(
            "required response",
            response_history["versions"][-1]["changed_fields"],
        )

    def test_capacity_alert_is_material_auditable_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "test.duckdb"
            raw_path = root / "capacity.xlsx"
            raw_path.write_bytes(b"fixture")
            connection = connect_database(database_path)
            initialize_database(connection)
            connection.close()

            store_segment_snapshot(
                database_path,
                raw_path,
                suffix="a",
                observed_at=pendulum.datetime(2026, 8, 30, 14, 0, tz="UTC"),
                operating=1_000_000,
            )
            store_segment_snapshot(
                database_path,
                raw_path,
                suffix="b",
                observed_at=pendulum.datetime(2026, 8, 30, 15, 0, tz="UTC"),
                operating=800_000,
            )

            first = build_tgp_alerts(database_path)
            second = build_tgp_alerts(database_path)
            alert_response = TgpReadModel(database_path).alerts(
                scope="latest",
                limit=10,
            )
            connection = connect_database(database_path)
            stored_count = connection.execute("SELECT count(*) FROM alerts").fetchone()[
                0
            ]
            connection.close()

        self.assertEqual(first.capacity_alert_count, 1)
        self.assertEqual(second.capacity_alert_count, 1)
        self.assertEqual(stored_count, 1)
        self.assertTrue(alert_response["material_change_in_latest_pull"])
        item = alert_response["items"][0]
        self.assertEqual(item["change_type"], "operating_capacity_decrease")
        self.assertEqual(
            item["evidence"]["before"]["operating_capacity_dth_per_day"], 1_000_000
        )
        self.assertEqual(
            item["evidence"]["after"]["operating_capacity_dth_per_day"], 800_000
        )
        self.assertIn("absolute_change", item["score_components"])

    def test_capacity_alert_is_neutral_across_different_scheduling_periods(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "test.duckdb"
            raw_path = root / "capacity.xlsx"
            raw_path.write_bytes(b"fixture")
            connection = connect_database(database_path)
            initialize_database(connection)
            connection.close()

            store_segment_snapshot(
                database_path,
                raw_path,
                suffix="a",
                observed_at=pendulum.datetime(2026, 8, 30, 14, 0, tz="UTC"),
                operating=1_000_000,
            )
            store_segment_snapshot(
                database_path,
                raw_path,
                suffix="b",
                observed_at=pendulum.datetime(2026, 8, 31, 14, 0, tz="UTC"),
                operating=800_000,
                gas_day=pendulum.date(2026, 8, 31),
            )

            build_tgp_alerts(database_path)
            response = TgpReadModel(database_path).alerts(scope="latest", limit=10)

        item = response["items"][0]
        self.assertEqual(item["current_status"], "changed")
        self.assertEqual(item["change_type"], "operating_capacity_changed")
        self.assertIn("different gas days", item["evidence"]["comparison_warning"])
        self.assertNotIn("fell", item["headline"])


if __name__ == "__main__":
    unittest.main()
