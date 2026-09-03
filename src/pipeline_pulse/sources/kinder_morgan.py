from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import pendulum

_FIELD_IDS = {
    "lblTSP",
    "lblCritical",
    "lblType",
    "lblSubType",
    "lblEffDate",
    "lblEndDate",
    "lblPostDate",
    "lblID",
    "lblReqRsp",
    "lblReqRspDate",
    "lblStatus",
    "lblPriorNotice",
    "lblSubject",
    "Label12",
}
_BLOCK_TAGS = {"br", "div", "li", "p", "table", "tr"}


class KinderMorganParseError(ValueError):
    """Raised when a required field is absent from a KM notice detail page."""


@dataclass(frozen=True)
class KinderMorganNotice:
    tsp_number: str
    tsp_name: str
    notice_id: str
    critical: bool
    notice_type_primary: str
    notice_type_secondary: str
    status_description: str
    prior_notice_id: str | None
    subject: str
    notice_text: str
    posted_at: pendulum.DateTime
    effective_start: pendulum.DateTime | None
    effective_end: pendulum.DateTime | None
    required_response: str | None
    response_at: pendulum.DateTime | None


@dataclass(frozen=True)
class KinderMorganNoticeIndexRow:
    row_position: int
    notice_id: str
    notice_type_primary: str
    notice_type_secondary: str
    subject: str
    posted_at: pendulum.DateTime
    effective_start: pendulum.DateTime | None
    effective_end: pendulum.DateTime | None


@dataclass(frozen=True)
class KinderMorganNoticeIndexPage:
    page_index: int
    page_size: int
    page_count: int
    total_row_count: int
    rows: tuple[KinderMorganNoticeIndexRow, ...]


@dataclass(frozen=True)
class KinderMorganNoticeIndexExport:
    total_row_count: int
    source_footer_row_count: int
    rows: tuple[KinderMorganNoticeIndexRow, ...]


class _FieldSpanParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, str] = {}
        self._active_field: str | None = None
        self._nested_span_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id", "")
        suffix = element_id.rsplit("_", 1)[-1]
        if tag == "span" and suffix in _FIELD_IDS and self._active_field is None:
            self._active_field = suffix
            self._nested_span_depth = 1
            self._parts = []
            return
        if self._active_field is None:
            return
        if tag == "span":
            self._nested_span_depth += 1
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._active_field is None:
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")
        if tag != "span":
            return
        self._nested_span_depth -= 1
        if self._nested_span_depth == 0:
            self.fields[self._active_field] = "".join(self._parts)
            self._active_field = None
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._active_field is not None:
            self._parts.append(data)


class _NoticeIndexTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raw_rows: list[tuple[str, list[str]]] = []
        self._notice_id: str | None = None
        self._row_depth = 0
        self._cells: list[str] = []
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            if self._notice_id is None:
                row_key = attributes.get("data-rowkey", "")
                match = re.fullmatch(r"\[(\d+)\]", row_key)
                if match:
                    self._notice_id = match.group(1)
                    self._row_depth = 1
                    self._cells = []
            else:
                self._row_depth += 1
            return
        if tag == "td" and self._notice_id is not None and self._row_depth == 2:
            if self._cell_parts is not None:
                raise KinderMorganParseError("nested data cell in notice index")
            self._cell_parts = []
            return
        if self._cell_parts is not None and tag in _BLOCK_TAGS:
            self._cell_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._notice_id is None:
            return
        if tag == "td" and self._cell_parts is not None:
            self._cells.append(_one_line("".join(self._cell_parts)))
            self._cell_parts = None
            return
        if tag != "tr":
            return
        if self._row_depth == 1:
            self.raw_rows.append((self._notice_id, self._cells))
            self._notice_id = None
            self._row_depth = 0
            self._cells = []
        else:
            self._row_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)


class _FormFieldsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: list[tuple[str, str]] = []
        self._select_name: str | None = None
        self._options: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name"):
            input_type = (attributes.get("type") or "text").lower()
            if input_type in {"button", "file", "image", "reset", "submit"}:
                return
            if input_type in {"checkbox", "radio"} and "checked" not in attributes:
                return
            self.fields.append(
                (attributes["name"] or "", attributes.get("value") or "")
            )
        elif tag == "select" and attributes.get("name"):
            self._select_name = attributes["name"]
            self._options = []
        elif tag == "option" and self._select_name is not None:
            self._options.append(
                (attributes.get("value") or "", "selected" in attributes)
            )

    def handle_endtag(self, tag: str) -> None:
        if tag != "select" or self._select_name is None:
            return
        selected = next(
            (value for value, is_selected in self._options if is_selected),
            self._options[0][0] if self._options else "",
        )
        self.fields.append((self._select_name, selected))
        self._select_name = None
        self._options = []


def _one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _body_text(value: str) -> str:
    lines = [_one_line(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _timestamp(value: str, timezone_name: str) -> pendulum.DateTime | None:
    normalized = _one_line(value).upper()
    if not normalized:
        return None
    normalized = re.sub(r"\s+(AM|PM)$", r"\1", normalized)
    for pattern in ("MM/DD/YYYY h:mm:ssA", "MM/DD/YYYY h:mmA"):
        try:
            return pendulum.from_format(normalized, pattern, tz=timezone_name)
        except (ValueError, pendulum.parsing.exceptions.ParserError):
            pass
    raise KinderMorganParseError(f"unsupported Kinder Morgan timestamp: {value!r}")


def _required(fields: dict[str, str], field_id: str) -> str:
    value = _one_line(fields.get(field_id, ""))
    if not value:
        raise KinderMorganParseError(
            f"missing required Kinder Morgan field: {field_id}"
        )
    return value


def parse_notice_detail(
    html: str, *, timezone_name: str = "America/Chicago"
) -> KinderMorganNotice:
    parser = _FieldSpanParser()
    parser.feed(html)
    parser.close()
    fields = parser.fields

    tsp_value = _required(fields, "lblTSP")
    tsp_parts = tsp_value.split("-", 1)
    if len(tsp_parts) != 2:
        raise KinderMorganParseError(f"unexpected TSP field: {tsp_value!r}")

    critical_value = _required(fields, "lblCritical").upper()
    if critical_value not in {"Y", "N"}:
        raise KinderMorganParseError(f"unexpected critical flag: {critical_value!r}")

    posted_at = _timestamp(_required(fields, "lblPostDate"), timezone_name)
    if posted_at is None:  # pragma: no cover - guarded by _required
        raise KinderMorganParseError("post timestamp is required")

    return KinderMorganNotice(
        tsp_number=tsp_parts[0].strip(),
        tsp_name=tsp_parts[1].strip(),
        notice_id=_required(fields, "lblID"),
        critical=critical_value == "Y",
        notice_type_primary=_required(fields, "lblType"),
        notice_type_secondary=_required(fields, "lblSubType"),
        status_description=_required(fields, "lblStatus"),
        prior_notice_id=_one_line(fields.get("lblPriorNotice", "")) or None,
        subject=_required(fields, "lblSubject"),
        notice_text=_body_text(fields.get("Label12", "")),
        posted_at=posted_at,
        effective_start=_timestamp(fields.get("lblEffDate", ""), timezone_name),
        effective_end=_timestamp(fields.get("lblEndDate", ""), timezone_name),
        required_response=_one_line(fields.get("lblReqRsp", "")) or None,
        response_at=_timestamp(fields.get("lblReqRspDate", ""), timezone_name),
    )


def parse_notice_index(
    html: str, *, timezone_name: str = "America/Chicago"
) -> KinderMorganNoticeIndexPage:
    parser = _NoticeIndexTableParser()
    parser.feed(html)
    parser.close()

    page_match = re.search(r'"pi":(\d+),"ps":(\d+),"pc":(\d+)', html)
    total_matches = tuple(
        int(value) for value in re.findall(r'footTxt":"Row Count:\s*(\d+)"', html)
    )
    if page_match is None or not total_matches:
        raise KinderMorganParseError("notice index pagination metadata is missing")

    rows: list[KinderMorganNoticeIndexRow] = []
    for position, (row_key, cells) in enumerate(parser.raw_rows):
        if len(cells) < 7:
            raise KinderMorganParseError(
                f"notice index row {row_key} has {len(cells)} cells; expected at least 7"
            )
        cell_notice_id = cells[5]
        if row_key != cell_notice_id:
            raise KinderMorganParseError(
                f"notice index row key {row_key} does not match cell {cell_notice_id}"
            )
        posted_at = _timestamp(cells[2], timezone_name)
        if posted_at is None:
            raise KinderMorganParseError(f"notice index row {row_key} lacks post time")
        rows.append(
            KinderMorganNoticeIndexRow(
                row_position=position,
                notice_id=row_key,
                notice_type_primary=cells[0],
                notice_type_secondary=cells[1],
                posted_at=posted_at,
                effective_start=_timestamp(cells[3], timezone_name),
                effective_end=_timestamp(cells[4], timezone_name),
                subject=cells[6],
            )
        )

    if not rows:
        raise KinderMorganParseError("notice index contains no data rows")

    return KinderMorganNoticeIndexPage(
        page_index=int(page_match.group(1)),
        page_size=int(page_match.group(2)),
        page_count=int(page_match.group(3)),
        # A page can embed footer metadata for more than one grid. The notice
        # grid is the largest; using the first footer misread NGPL as one row.
        total_row_count=max(total_matches),
        rows=tuple(rows),
    )


def build_notice_export_form(html: str) -> list[tuple[str, str]]:
    parser = _FormFieldsParser()
    parser.feed(html)
    parser.close()
    overrides = {
        "ctl00$hdnIsDownload": "true",
        "ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$ddlDownloadType": (
            "EXCEL-Summary (All)"
        ),
        "ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$aspBtnDownload": "Download",
    }
    fields = [(key, value) for key, value in parser.fields if key not in overrides]
    fields.extend(overrides.items())
    required = {"__VIEWSTATE", "__EVENTVALIDATION", *overrides}
    missing = required.difference(key for key, _ in fields)
    if missing:
        raise KinderMorganParseError(
            f"notice export form is missing fields: {', '.join(sorted(missing))}"
        )
    return fields


def build_location_export_form(html: str) -> list[tuple[str, str]]:
    parser = _FormFieldsParser()
    parser.feed(html)
    parser.close()
    prefix = "ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$HeaderBTN1$"
    overrides = {
        "ctl00$hdnIsDownload": "true",
        f"{prefix}DownloadDDL": "CSV",
        f"{prefix}btnDownload.x": "1",
        f"{prefix}btnDownload.y": "1",
    }
    overridden_names = {
        "ctl00$hdnIsDownload",
        f"{prefix}DownloadDDL",
    }
    fields = [
        (key, value) for key, value in parser.fields if key not in overridden_names
    ]
    fields.extend(overrides.items())
    required = {"__VIEWSTATE", "__EVENTVALIDATION", *overrides}
    missing = required.difference(key for key, _ in fields)
    if missing:
        raise KinderMorganParseError(
            f"location export form is missing fields: {', '.join(sorted(missing))}"
        )
    return fields


def _column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference)
    if letters is None:
        raise KinderMorganParseError(
            f"invalid spreadsheet cell reference: {cell_reference!r}"
        )
    index = 0
    for character in letters.group(0):
        index = index * 26 + ord(character) - ord("A") + 1
    return index - 1


def _spreadsheet_rows(body: bytes) -> list[list[str]]:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with ZipFile(BytesIO(body)) as archive:
            shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(node.text or "" for node in item.iter(namespace + "t"))
                for item in shared_root
            ]
            sheet_root = ElementTree.fromstring(
                archive.read("xl/worksheets/sheet1.xml")
            )
    except (BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise KinderMorganParseError("invalid Kinder Morgan XLSX export") from exc

    rows: list[list[str]] = []
    for row in sheet_root.iter(namespace + "row"):
        values: dict[int, str] = {}
        for cell in row.findall(namespace + "c"):
            reference = cell.get("r", "")
            value_node = cell.find(namespace + "v")
            value = "" if value_node is None else value_node.text or ""
            if cell.get("t") == "s" and value:
                try:
                    value = shared_strings[int(value)]
                except (IndexError, ValueError) as exc:
                    raise KinderMorganParseError(
                        f"invalid shared string in cell {reference}"
                    ) from exc
            values[_column_index(reference)] = value
        if values:
            width = max(values) + 1
            rows.append([values.get(index, "") for index in range(width)])
    return rows


def parse_notice_index_export(
    body: bytes,
    *,
    expected_row_count: int | None = None,
    expected_row_count_range: tuple[int, int] | None = None,
    timezone_name: str = "America/Chicago",
) -> KinderMorganNoticeIndexExport:
    spreadsheet_rows = _spreadsheet_rows(body)
    expected_header = [
        "Notice Type Desc (1)",
        "Notice Type Desc (2)",
        "Post Date/Time",
        "Notice Effective Date/Time",
        "Notice End Date/Time",
        "Notice ID",
    ]
    try:
        header_index = next(
            index
            for index, row in enumerate(spreadsheet_rows)
            if row[:6] == expected_header
        )
    except StopIteration as exc:
        raise KinderMorganParseError("notice export header is missing") from exc

    footer_index: int | None = None
    total_row_count: int | None = None
    for index in range(header_index + 1, len(spreadsheet_rows)):
        first_cell = _one_line(spreadsheet_rows[index][0])
        match = re.fullmatch(r"Row Count:\s*(\d+)", first_cell)
        if match:
            footer_index = index
            total_row_count = int(match.group(1))
            break
    if footer_index is None or total_row_count is None:
        raise KinderMorganParseError("notice export row count is missing")

    rows: list[KinderMorganNoticeIndexRow] = []
    for position, cells in enumerate(spreadsheet_rows[header_index + 1 : footer_index]):
        cells = cells + [""] * (6 - len(cells))
        notice_id = _one_line(cells[5])
        posted_at = _timestamp(cells[2], timezone_name)
        if not notice_id or posted_at is None:
            raise KinderMorganParseError(
                f"notice export row {position} lacks notice ID or post time"
            )
        rows.append(
            KinderMorganNoticeIndexRow(
                row_position=position,
                notice_id=notice_id,
                notice_type_primary=_one_line(cells[0]),
                notice_type_secondary=_one_line(cells[1]),
                subject="",
                posted_at=posted_at,
                effective_start=_timestamp(cells[3], timezone_name),
                effective_end=_timestamp(cells[4], timezone_name),
            )
        )
    if expected_row_count is not None and len(rows) != expected_row_count:
        raise KinderMorganParseError(
            f"notice export parsed {len(rows)} rows; expected {expected_row_count}"
        )
    if expected_row_count_range is not None:
        minimum, maximum = expected_row_count_range
        if not minimum <= len(rows) <= maximum:
            raise KinderMorganParseError(
                f"notice export parsed {len(rows)} rows; expected {minimum}..{maximum}"
            )
    if (
        expected_row_count is None
        and expected_row_count_range is None
        and len(rows) != total_row_count
    ):
        raise KinderMorganParseError(
            f"notice export parsed {len(rows)} rows; expected {total_row_count}"
        )
    return KinderMorganNoticeIndexExport(
        total_row_count=len(rows),
        source_footer_row_count=total_row_count,
        rows=tuple(rows),
    )
