from __future__ import annotations

import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from pipeline_pulse.sources.kinder_morgan import (
    KinderMorganParseError,
    build_location_export_form,
    build_notice_export_form,
    parse_notice_detail,
    parse_notice_index,
    parse_notice_index_export,
)


FIXTURES = Path(__file__).parent / "fixtures" / "kinder_morgan"


class NoticeDetailParserTests(unittest.TestCase):
    def test_parses_real_page_structure(self) -> None:
        html = (FIXTURES / "notice_403767.html").read_text(encoding="utf-8")

        notice = parse_notice_detail(html)

        self.assertEqual(notice.tsp_number, "1939164")
        self.assertEqual(notice.tsp_name, "TENNESSEE GAS PIPELINE")
        self.assertEqual(notice.notice_id, "403767")
        self.assertTrue(notice.critical)
        self.assertEqual(notice.status_description, "TERMINATE")
        self.assertEqual(notice.prior_notice_id, "403735")
        self.assertEqual(notice.posted_at.isoformat(), "2026-08-26T06:56:53-05:00")
        self.assertEqual(notice.effective_end.isoformat(), "2026-08-27T09:00:00-05:00")
        self.assertIn("STA 542 (Segment 539 BH)", notice.notice_text)

    def test_rejects_missing_required_fields(self) -> None:
        with self.assertRaisesRegex(KinderMorganParseError, "lblTSP"):
            parse_notice_detail("<html><body>not a notice</body></html>")


class NoticeIndexParserTests(unittest.TestCase):
    def test_parses_index_rows_and_pagination(self) -> None:
        html = (FIXTURES / "critical_index_page_0.html").read_text(encoding="utf-8")

        page = parse_notice_index(html)

        self.assertEqual(page.page_index, 0)
        self.assertEqual(page.page_size, 75)
        self.assertEqual(page.page_count, 8)
        self.assertEqual(page.total_row_count, 597)
        self.assertEqual(len(page.rows), 2)
        self.assertEqual(page.rows[0].notice_id, "403824")
        self.assertEqual(page.rows[1].notice_type_secondary, "OFO")
        self.assertEqual(
            page.rows[1].subject,
            "OFO DAILY CD1 DS219 EFF 8-29 LIFTED 8-31",
        )

    def test_rejects_index_without_pagination(self) -> None:
        with self.assertRaisesRegex(KinderMorganParseError, "pagination"):
            parse_notice_index("<html><body>not an index</body></html>")

    def test_builds_complete_export_form(self) -> None:
        html = """
        <form>
          <input type="hidden" name="__VIEWSTATE" value="state" />
          <input type="hidden" name="__EVENTVALIDATION" value="validation" />
          <input type="hidden" name="ctl00$hdnIsDownload" value="false" />
          <select name="ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$ddlDownloadType">
            <option selected="selected" value="EXCEL-Summary (All)">Excel</option>
          </select>
        </form>
        """

        fields = dict(build_notice_export_form(html))

        self.assertIn("__VIEWSTATE", fields)
        self.assertEqual(fields["ctl00$hdnIsDownload"], "true")
        self.assertEqual(
            fields[
                "ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$ddlDownloadType"
            ],
            "EXCEL-Summary (All)",
        )

    def test_builds_location_csv_export_form(self) -> None:
        html = """
        <form>
          <input type="hidden" name="__VIEWSTATE" value="state" />
          <input type="hidden" name="__EVENTVALIDATION" value="validation" />
          <input type="hidden" name="ctl00$hdnIsDownload" value="false" />
          <select name="ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$HeaderBTN1$DownloadDDL">
            <option selected="selected" value="CSV">CSV</option>
          </select>
        </form>
        """

        fields = dict(build_location_export_form(html))

        self.assertEqual(fields["ctl00$hdnIsDownload"], "true")
        self.assertEqual(
            fields[
                "ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$HeaderBTN1$DownloadDDL"
            ],
            "CSV",
        )
        self.assertIn(
            "ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$HeaderBTN1$btnDownload.x",
            fields,
        )


class NoticeIndexExportParserTests(unittest.TestCase):
    def test_parses_complete_xlsx_summary(self) -> None:
        values = [
            "TSP",
            "1939164",
            "Notice Type Desc (1)",
            "Notice Type Desc (2)",
            "Post Date/Time",
            "Notice Effective Date/Time",
            "Notice End Date/Time",
            "Notice ID",
            "PIPELINE CONDITIONS",
            "CURRENT PIPELINE CONDITIONS",
            "08/28/2026 9:27:08AM",
            "08/28/2026 9:27:08AM",
            "08/29/2026 9:00:00AM",
            "403824",
            "OPERATIONAL FLOW ORDER",
            "OFO",
            "08/28/2026 6:48:40AM",
            "08/28/2026 6:48:40AM",
            "08/29/2026 9:00:00AM",
            "403823",
            "Row Count: 2",
        ]
        shared_strings = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + "".join(f"<si><t>{value}</t></si>" for value in values)
            + "</sst>"
        )
        sheet = """<?xml version="1.0" encoding="UTF-8"?>
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <sheetData>
            <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>
            <row r="4"><c r="A4" t="s"><v>2</v></c><c r="B4" t="s"><v>3</v></c><c r="C4" t="s"><v>4</v></c><c r="D4" t="s"><v>5</v></c><c r="E4" t="s"><v>6</v></c><c r="F4" t="s"><v>7</v></c></row>
            <row r="5"><c r="A5" t="s"><v>8</v></c><c r="B5" t="s"><v>9</v></c><c r="C5" t="s"><v>10</v></c><c r="D5" t="s"><v>11</v></c><c r="E5" t="s"><v>12</v></c><c r="F5" t="s"><v>13</v></c></row>
            <row r="6"><c r="A6" t="s"><v>14</v></c><c r="B6" t="s"><v>15</v></c><c r="C6" t="s"><v>16</v></c><c r="D6" t="s"><v>17</v></c><c r="E6" t="s"><v>18</v></c><c r="F6" t="s"><v>19</v></c></row>
            <row r="7"><c r="A7" t="s"><v>20</v></c></row>
          </sheetData>
        </worksheet>"""
        output = BytesIO()
        with ZipFile(output, "w") as archive:
            archive.writestr("xl/sharedStrings.xml", shared_strings)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)

        export = parse_notice_index_export(output.getvalue())

        self.assertEqual(export.total_row_count, 2)
        self.assertEqual(export.source_footer_row_count, 2)
        self.assertEqual(len(export.rows), 2)
        self.assertEqual(export.rows[0].notice_id, "403824")
        self.assertEqual(export.rows[-1].notice_type_secondary, "OFO")


if __name__ == "__main__":
    unittest.main()
