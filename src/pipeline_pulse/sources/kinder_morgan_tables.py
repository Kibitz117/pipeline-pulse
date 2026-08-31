from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

import pendulum


_BODY_FIELD_ID = "WebSplitter1_tmpl1_ContentPlaceHolder1_Label12"
_BLOCK_TAGS = {"br", "div", "li", "p"}


@dataclass(frozen=True)
class SourceTableCell:
    text: str
    rowspan: int = 1
    colspan: int = 1


@dataclass(frozen=True)
class SourceTable:
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class TgpOutageImpactRow:
    report_kind: str
    report_label: str
    report_updated_on: pendulum.Date
    period_label: str
    period_start: pendulum.Date
    period_end: pendulum.Date
    station_label: str
    operator_segment_id: str | None
    flow_direction: str | None
    nominal_capacity_text: str
    capacity_text: str
    nominal_capacity_dth_per_day: int | None
    operating_capacity_dth_per_day: int | None
    reported_reduction_dth_per_day: int | None
    calculated_reduction_dth_per_day: int | None
    reduction_reconciles: bool | None
    outage_description: str
    source_table_index: int
    source_row_index: int


def _clean_text(parts: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


class _NoticeBodyTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.body_depth = 0
        self.table_depth = 0
        self.current_table: list[list[SourceTableCell]] | None = None
        self.current_row: list[SourceTableCell] | None = None
        self.current_cell_attributes: tuple[int, int] | None = None
        self.current_cell_parts: list[str] = []
        self.tables: list[list[list[SourceTableCell]]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id") == _BODY_FIELD_ID:
            self.body_depth = 1
            return
        if not self.body_depth:
            return
        self.body_depth += 1
        if tag == "table":
            self.table_depth += 1
            if self.table_depth == 1:
                self.current_table = []
        elif tag == "tr" and self.table_depth == 1:
            self.current_row = []
        elif tag in {"td", "th"} and self.table_depth == 1:
            self.current_cell_attributes = (
                _positive_int(attributes.get("rowspan")),
                _positive_int(attributes.get("colspan")),
            )
            self.current_cell_parts = []
        elif tag in _BLOCK_TAGS and self.current_cell_attributes is not None:
            self.current_cell_parts.append(" ")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.body_depth and self.current_cell_attributes is not None:
            self.current_cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.body_depth:
            return
        if tag in {"td", "th"} and self.current_cell_attributes is not None:
            rowspan, colspan = self.current_cell_attributes
            if self.current_row is not None:
                self.current_row.append(
                    SourceTableCell(
                        text=_clean_text(self.current_cell_parts),
                        rowspan=rowspan,
                        colspan=colspan,
                    )
                )
            self.current_cell_attributes = None
            self.current_cell_parts = []
        elif tag == "tr" and self.table_depth == 1:
            if self.current_table is not None and self.current_row is not None:
                self.current_table.append(self.current_row)
            self.current_row = None
        elif tag == "table":
            if self.table_depth == 1 and self.current_table is not None:
                self.tables.append(self.current_table)
                self.current_table = None
            self.table_depth -= 1
        self.body_depth -= 1


def _positive_int(value: str | None) -> int:
    if value is None:
        return 1
    try:
        parsed = int(value)
    except ValueError:
        return 1
    return max(parsed, 1)


def _expand_merged_cells(rows: list[list[SourceTableCell]]) -> SourceTable:
    expanded: list[tuple[str, ...]] = []
    pending: dict[int, tuple[int, str]] = {}
    for source_row in rows:
        output_row: list[str] = []
        column = 0

        def append_pending() -> None:
            nonlocal column
            while column in pending:
                remaining_rows, text = pending[column]
                output_row.append(text)
                if remaining_rows == 1:
                    del pending[column]
                else:
                    pending[column] = (remaining_rows - 1, text)
                column += 1

        for cell in source_row:
            append_pending()
            for offset in range(cell.colspan):
                output_row.append(cell.text)
                if cell.rowspan > 1:
                    pending[column + offset] = (cell.rowspan - 1, cell.text)
            column += cell.colspan
        while pending and column <= max(pending):
            if column in pending:
                append_pending()
            else:
                output_row.append("")
                column += 1
        expanded.append(tuple(output_row))
    return SourceTable(rows=tuple(expanded))


def parse_notice_body_tables(html: str) -> tuple[SourceTable, ...]:
    parser = _NoticeBodyTableParser()
    parser.feed(html)
    parser.close()
    return tuple(_expand_merged_cells(rows) for rows in parser.tables)


_UPDATED_PATTERN = re.compile(r"updated\s+(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
_SEGMENT_PATTERN = re.compile(r"\(segment\s+([\w.-]+)\s+(BH|FH)\)", re.I)
_CAPACITY_PATTERN = re.compile(
    r"^\s*([\d,]+)(?:\s*\(([\d,]+)\))?\s*$"
)
_DAILY_PERIOD_PATTERN = re.compile(r"\((\d{1,2})/(\d{1,2})\)")
_WEEKLY_PERIOD_PATTERN = re.compile(
    r"\((\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})\)"
)


def _report_label(rows: tuple[tuple[str, ...], ...]) -> tuple[str, pendulum.Date]:
    for row in rows:
        for value in row:
            match = _UPDATED_PATTERN.search(value)
            if match:
                date_text = match.group(1)
                date_format = "MM/DD/YYYY" if len(date_text.rsplit("/", 1)[1]) == 4 else "MM/DD/YY"
                return value, pendulum.from_format(
                    date_text,
                    date_format,
                    tz="America/Chicago",
                ).date()
    raise ValueError("outage impact table has no updated date")


def _nearest_date(month: int, day: int, anchor: pendulum.Date) -> pendulum.Date:
    candidates = (
        pendulum.date(anchor.year - 1, month, day),
        pendulum.date(anchor.year, month, day),
        pendulum.date(anchor.year + 1, month, day),
    )
    return min(candidates, key=lambda value: abs((value - anchor).days))


def _period_dates(
    period_label: str, report_updated_on: pendulum.Date
) -> tuple[pendulum.Date, pendulum.Date]:
    weekly = _WEEKLY_PERIOD_PATTERN.search(period_label)
    if weekly:
        start = _nearest_date(
            int(weekly.group(1)), int(weekly.group(2)), report_updated_on
        )
        end_year = start.year + (
            1 if int(weekly.group(3)) < int(weekly.group(1)) else 0
        )
        end = pendulum.date(
            end_year,
            int(weekly.group(3)),
            int(weekly.group(4)),
        )
        return start, end
    daily = _DAILY_PERIOD_PATTERN.search(period_label)
    if daily:
        value = _nearest_date(
            int(daily.group(1)), int(daily.group(2)), report_updated_on
        )
        return value, value
    raise ValueError(f"unsupported outage impact period: {period_label!r}")


def _capacity(value: str) -> tuple[int | None, int | None]:
    if not value.strip():
        return None, None
    match = _CAPACITY_PATTERN.match(value)
    if not match:
        raise ValueError(f"unsupported outage impact capacity: {value!r}")
    operating = int(match.group(1).replace(",", "")) * 1_000
    reported_reduction = (
        int(match.group(2).replace(",", "")) * 1_000
        if match.group(2)
        else None
    )
    return operating, reported_reduction


def parse_tgp_outage_impact_report(html: str) -> tuple[TgpOutageImpactRow, ...]:
    output: list[TgpOutageImpactRow] = []
    for table_index, table in enumerate(parse_notice_body_tables(html)):
        header_index = next(
            (
                index
                for index, row in enumerate(table.rows)
                if row and row[0] in {"Station / Seg", "Station # / Lateral"}
            ),
            None,
        )
        if header_index is None:
            continue
        header = table.rows[header_index]
        if len(header) < 4:
            continue
        label, updated_on = _report_label(table.rows)
        report_kind = "seven_day" if "Seven Day" in label else "monthly"
        periods = tuple(header[2:-1])
        if not periods:
            continue
        period_dates = tuple(_period_dates(value, updated_on) for value in periods)

        for row_index, row in enumerate(table.rows[header_index + 2 :], header_index + 2):
            if len(row) != len(header) or not row[0].startswith(("Station", "MLV")):
                continue
            segment_match = _SEGMENT_PATTERN.search(row[0])
            nominal, _ = _capacity(row[1])
            for column_offset, (period_label, dates) in enumerate(
                zip(periods, period_dates, strict=True),
                start=2,
            ):
                operating, reported_reduction = _capacity(row[column_offset])
                calculated_reduction = (
                    nominal - operating
                    if nominal is not None and operating is not None
                    else None
                )
                reduction_reconciles = (
                    calculated_reduction == reported_reduction
                    if reported_reduction is not None
                    and calculated_reduction is not None
                    else None
                )
                output.append(
                    TgpOutageImpactRow(
                        report_kind=report_kind,
                        report_label=label,
                        report_updated_on=updated_on,
                        period_label=period_label,
                        period_start=dates[0],
                        period_end=dates[1],
                        station_label=row[0],
                        operator_segment_id=(
                            segment_match.group(1) if segment_match else None
                        ),
                        flow_direction=(
                            segment_match.group(2).upper() if segment_match else None
                        ),
                        nominal_capacity_text=row[1],
                        capacity_text=row[column_offset],
                        nominal_capacity_dth_per_day=nominal,
                        operating_capacity_dth_per_day=operating,
                        reported_reduction_dth_per_day=reported_reduction,
                        calculated_reduction_dth_per_day=calculated_reduction,
                        reduction_reconciles=reduction_reconciles,
                        outage_description=row[-1],
                        source_table_index=table_index,
                        source_row_index=row_index,
                    )
                )
    return tuple(output)
