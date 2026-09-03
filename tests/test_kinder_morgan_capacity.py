from __future__ import annotations

import hashlib
import tempfile
import unittest
from html import escape
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pendulum

from pipeline_pulse.artifacts import StoredArtifact
from pipeline_pulse.database import (
    connect_database,
    initialize_database,
    start_fetch_run,
    store_artifact_record,
    store_capacity_export,
)
from pipeline_pulse.sources.kinder_morgan import KinderMorganParseError
from pipeline_pulse.sources.kinder_morgan_capacity import (
    EXPECTED_POINT_CAPACITY_SCHEMA_SHA256,
    build_capacity_export_form,
    parse_kinder_morgan_capacity_export,
    parse_tgp_capacity_export,
)
from pipeline_pulse.web import TgpReadModel

METADATA_HEADERS = [
    "TSP",
    "TSP Name",
    "Eff Gas Day/Eff Time",
    "CycleDesc",
    "Loc Purp Desc",
    "Meas Basis Desc",
    "Post Date/Post Time",
    "Loc/QTI Desc",
]
POINT_HEADERS = [
    "Loc",
    "Loc Name",
    "Loc Zn",
    "Loc (Segment)",
    "Design Capacity",
    "Operating Capacity",
    "Total Scheduled Quantity",
    "Operationally Available Capacity",
    "IT",
    "Flow Ind",
    "All Qty Avail",
    "Qty Reason",
]


def xlsx_rows(rows: list[list[str]]) -> bytes:
    values = [value for row in rows for value in row]
    shared_strings = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{escape(value)}</t></si>" for value in values)
        + "</sst>"
    )
    value_index = 0
    sheet_rows: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells: list[str] = []
        for column_number, _ in enumerate(row, start=1):
            column = ""
            remaining = column_number
            while remaining:
                remaining, remainder = divmod(remaining - 1, 26)
                column = chr(ord("A") + remainder) + column
            cells.append(f'<c r="{column}{row_number}" t="s"><v>{value_index}</v></c>')
            value_index += 1
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main"><sheetData>'
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    )
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared_strings)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def point_capacity_xlsx(
    *,
    footer_count: int = 1,
    tsp_number: str = "1939164",
    tsp_name: str = "TENNESSEE GAS PIPELINE",
) -> bytes:
    return xlsx_rows(
        [
            METADATA_HEADERS,
            [
                tsp_number,
                tsp_name,
                "8/28/2026 02:00 PM CCT",
                "INTRADAY 1",
                "Delivery Location",
                "Dth",
                "08/28/2026 12:47 PM CCT",
                "Best Available",
            ],
            POINT_HEADERS,
            [
                "100",
                "TEST DELIVERY",
                "Zone 4",
                "204",
                "120",
                "90",
                "92",
                "0",
                "N",
                "D",
                "Y",
                "Scheduled exceeds operating",
            ],
            [f"Row Count: {footer_count}"],
            ["Comments:"],
            ["Operating capacity may change with maintenance."],
        ]
    )


def stored_artifact(root: Path, body: bytes) -> StoredArtifact:
    raw_path = root / "capacity.xlsx"
    raw_path.write_bytes(body)
    received_at = pendulum.datetime(2026, 8, 28, 18, 2, tz="UTC")
    digest = hashlib.sha256(body).hexdigest()
    return StoredArtifact(
        artifact_id=f"capacity:{digest}",
        source_code="km_tgp_point_delivery_capacity",
        canonical_url="https://example.test/capacity",
        content_sha256=digest,
        mime_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        http_status=200,
        requested_at=received_at.subtract(seconds=2),
        received_at=received_at,
        recorded_at=received_at,
        raw_path=raw_path.as_posix(),
        size_bytes=len(body),
        etag=None,
        last_modified=None,
        content_disposition=None,
    )


class CapacityParserTests(unittest.TestCase):
    def test_builds_delivery_and_receipt_download_forms(self) -> None:
        html = """
        <form>
          <input name="__VIEWSTATE" value="state">
          <input name="__EVENTVALIDATION" value="validation">
          <input name="ctl00$hdnIsDownload" value="false">
          <input name="ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$location"
                 value="rbDelivery" checked="checked">
        </form>
        """

        delivery = dict(build_capacity_export_form(html, point_role="delivery"))
        receipt = dict(build_capacity_export_form(html, point_role="receipt"))

        self.assertEqual(delivery["ctl00$hdnIsDownload"], "true")
        self.assertEqual(
            delivery[
                "ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$HeaderBTN1$DownloadDDL"
            ],
            "EXCEL",
        )
        self.assertEqual(
            receipt["ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$location"],
            "rbReceipt",
        )

    def test_parses_point_capacity_and_reconciles_zero_floor(self) -> None:
        export = parse_tgp_capacity_export(
            point_capacity_xlsx(),
            capacity_kind="point",
        )

        self.assertEqual(export.point_role, "delivery")
        self.assertEqual(str(export.gas_day), "2026-08-28")
        self.assertEqual(export.cycle, "INTRADAY 1")
        self.assertEqual(export.schema_sha256, EXPECTED_POINT_CAPACITY_SCHEMA_SHA256)
        self.assertEqual(export.source_footer_row_count, 1)
        self.assertIn("maintenance", export.comments)
        self.assertEqual(export.rows[0].operator_location_id, "100")
        self.assertEqual(export.rows[0].available_capacity_dth_per_day, 0)
        self.assertTrue(export.rows[0].available_reconciles)

    def test_rejects_footer_count_mismatch(self) -> None:
        with self.assertRaisesRegex(KinderMorganParseError, "footer reports 2"):
            parse_tgp_capacity_export(
                point_capacity_xlsx(footer_count=2),
                capacity_kind="point",
            )

    def test_validates_ngpl_identity_on_shared_capacity_schema(self) -> None:
        export = parse_kinder_morgan_capacity_export(
            point_capacity_xlsx(
                tsp_number="6931794",
                tsp_name="NATURAL GAS PIPELINE CO.",
            ),
            capacity_kind="point",
            expected_tsp_number="6931794",
            pipeline_label="NGPL",
        )

        self.assertEqual(export.tsp_number, "6931794")
        self.assertEqual(export.tsp_name, "NATURAL GAS PIPELINE CO.")

    def test_stores_native_ids_and_only_links_known_references(self) -> None:
        body = point_capacity_xlsx()
        export = parse_tgp_capacity_export(body, capacity_kind="point")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            artifact = stored_artifact(root, body)
            connection = connect_database(root / "test.duckdb")
            initialize_database(connection)
            run_id = start_fetch_run(
                connection,
                "tgp_operational_capacity",
                requested_at=artifact.requested_at,
            )
            store_artifact_record(connection, run_id, artifact)
            connection.execute(
                """
                INSERT INTO facilities(
                    facility_id, pipeline_id, operator_location_id, facility_name
                ) VALUES ('TGP:100', 'TGP', '100', 'TEST DELIVERY')
                """
            )
            connection.execute(
                """
                INSERT INTO segments(
                    segment_id, pipeline_id, operator_segment_id, segment_name
                ) VALUES ('TGP:SEG:204', 'TGP', '204', 'TEST SEGMENT')
                """
            )

            stored_count = store_capacity_export(connection, artifact, export)
            row = connection.execute(
                """
                SELECT
                    operator_location_id, operator_segment_id, facility_id,
                    segment_id, available_at, available_reconciles
                FROM latest_tgp_capacity
                """
            ).fetchone()
            capture = connection.execute(
                """
                SELECT parsed_row_count, matched_facility_rows,
                       matched_segment_rows
                FROM tgp_capacity_capture_summary
                """
            ).fetchone()
            connection.close()
            read_rows = TgpReadModel(root / "test.duckdb").operational_capacity()

        self.assertEqual(stored_count, 1)
        self.assertEqual(row[:4], ("100", "204", "TGP:100", "TGP:SEG:204"))
        self.assertEqual(int(row[4].timestamp()), int(artifact.received_at.timestamp()))
        self.assertTrue(row[5])
        self.assertEqual(capture, (1, 1, 1))
        self.assertEqual(len(read_rows), 1)
        self.assertEqual(read_rows[0]["operator_segment_id"], "204")


if __name__ == "__main__":
    unittest.main()
