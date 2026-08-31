from __future__ import annotations

import unittest

from pipeline_pulse.sources.kinder_morgan_tables import (
    parse_notice_body_tables,
    parse_tgp_outage_impact_report,
)


class NoticeBodyTableTests(unittest.TestCase):
    def test_reads_only_notice_body_and_expands_merged_cells(self) -> None:
        html = """
        <table><tr><td>navigation</td></tr></table>
        <span id="WebSplitter1_tmpl1_ContentPlaceHolder1_Label12">
          <table>
            <tr><th rowspan="2">Station</th><th colspan="2">Capacity</th></tr>
            <tr><th>Monday</th><th>Tuesday</th></tr>
            <tr><td>Station 9</td><td>640</td><td>640</td></tr>
          </table>
        </span>
        """

        tables = parse_notice_body_tables(html)

        self.assertEqual(len(tables), 1)
        self.assertEqual(
            tables[0].rows,
            (
                ("Station", "Capacity", "Capacity"),
                ("Station", "Monday", "Tuesday"),
                ("Station 9", "640", "640"),
            ),
        )

    def test_parses_daily_capacity_impacts_without_guessing_missing_values(self) -> None:
        html = """
        <span id="WebSplitter1_tmpl1_ContentPlaceHolder1_Label12">
          <table>
            <tr><td></td><td colspan="2">Seven Day Forecast (updated 08/06/26)</td><td></td></tr>
            <tr><td>Station / Seg</td><td>Est Nominal Operating Capacity (Thousand Dth)</td><td>Monday (8/10)</td><td>Primary Outage(s) that may Impact Throughput</td></tr>
            <tr><td>Station / Seg</td><td>Est Nominal Operating Capacity (Thousand Dth)</td><td>Est. Operational Capacity (Operational Impact) - Thousand Dth</td><td>Primary Outage(s) that may Impact Throughput</td></tr>
            <tr><td>Station 9 (segment 109 BH)</td><td>1,027</td><td>640 (387)</td><td>Pig Run</td></tr>
            <tr><td>Station 17 (segment 117 FH)</td><td>1,122</td><td></td><td>Pending</td></tr>
          </table>
        </span>
        """

        rows = parse_tgp_outage_impact_report(html)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].report_kind, "seven_day")
        self.assertEqual(rows[0].period_start.to_date_string(), "2026-08-10")
        self.assertEqual(rows[0].operator_segment_id, "109")
        self.assertEqual(rows[0].flow_direction, "BH")
        self.assertEqual(rows[0].nominal_capacity_dth_per_day, 1_027_000)
        self.assertEqual(rows[0].operating_capacity_dth_per_day, 640_000)
        self.assertEqual(rows[0].reported_reduction_dth_per_day, 387_000)
        self.assertTrue(rows[0].reduction_reconciles)
        self.assertIsNone(rows[1].operating_capacity_dth_per_day)

    def test_parses_week_that_crosses_year_end(self) -> None:
        html = """
        <span id="WebSplitter1_tmpl1_ContentPlaceHolder1_Label12">
          <table>
            <tr><td colspan="4">January 2027 (updated 12/28/26)</td></tr>
            <tr><td>Station # / Lateral</td><td>Est Nominal Operating Capacity (Thousand Dth)</td><td>Week 1 (12/28 - 1/3)</td><td>Primary Outage(s)</td></tr>
            <tr><td>Station # / Lateral</td><td>capacity</td><td>capacity</td><td>Primary Outage(s)</td></tr>
            <tr><td>Station 1 (segment 101 BH)</td><td>399</td><td>300 (99)</td><td>Work</td></tr>
          </table>
        </span>
        """

        row = parse_tgp_outage_impact_report(html)[0]

        self.assertEqual(row.period_start.to_date_string(), "2026-12-28")
        self.assertEqual(row.period_end.to_date_string(), "2027-01-03")


if __name__ == "__main__":
    unittest.main()
