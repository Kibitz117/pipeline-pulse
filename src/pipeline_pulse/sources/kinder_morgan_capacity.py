from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import pendulum

from .kinder_morgan import (
    KinderMorganParseError,
    _FormFieldsParser,
    _spreadsheet_rows,
)


_METADATA_HEADERS = (
    "TSP",
    "TSP Name",
    "Eff Gas Day/Eff Time",
    "CycleDesc",
    "Loc Purp Desc",
    "Meas Basis Desc",
    "Post Date/Post Time",
    "Loc/QTI Desc",
)
_POINT_HEADERS = (
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
)
_SEGMENT_HEADERS = (
    "Loc (Segment)",
    "Loc Name (Segment)",
    "Loc Zn",
    "Design Capacity",
    "Operating Capacity",
    "Total Scheduled Quantity",
    "Operationally Available Capacity",
    "IT",
    "Flow Ind",
    "All Qty Avail",
    "Qty Reason",
)

EXPECTED_POINT_CAPACITY_SCHEMA_SHA256 = hashlib.sha256(
    "\x1f".join(_POINT_HEADERS).encode("utf-8")
).hexdigest()
EXPECTED_SEGMENT_CAPACITY_SCHEMA_SHA256 = hashlib.sha256(
    "\x1f".join(_SEGMENT_HEADERS).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class TgpCapacityRow:
    row_position: int
    operator_location_id: str | None
    operator_segment_id: str
    location_name: str
    zone: str
    design_capacity_dth_per_day: int
    operating_capacity_dth_per_day: int
    scheduled_quantity_dth_per_day: int
    available_capacity_dth_per_day: int
    interruptible_scheduled: bool
    flow_indicator: str
    all_quantity_available: bool
    quantity_reason: str | None
    available_reconciles: bool


@dataclass(frozen=True)
class TgpCapacityExport:
    capacity_kind: str
    point_role: str | None
    tsp_number: str
    tsp_name: str
    effective_at: pendulum.DateTime
    gas_day: pendulum.Date
    cycle: str
    location_purpose: str
    measurement_basis: str
    source_posted_at: pendulum.DateTime
    quantity_description: str
    source_footer_row_count: int
    schema_sha256: str
    comments: str
    rows: tuple[TgpCapacityRow, ...]


def build_capacity_export_form(
    html: str,
    *,
    point_role: str | None = None,
) -> list[tuple[str, str]]:
    if point_role not in {None, "delivery", "receipt"}:
        raise ValueError("point_role must be delivery, receipt, or None")
    parser = _FormFieldsParser()
    parser.feed(html)
    parser.close()
    prefix = "ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$HeaderBTN1$"
    location_name = "ctl00$WebSplitter1$tmpl1$ContentPlaceHolder1$location"
    overrides = {
        "ctl00$hdnIsDownload": "true",
        f"{prefix}DownloadDDL": "EXCEL",
        f"{prefix}btnDownload.x": "1",
        f"{prefix}btnDownload.y": "1",
    }
    if point_role is not None:
        overrides[location_name] = (
            "rbDelivery" if point_role == "delivery" else "rbReceipt"
        )
    fields = [
        (key, value)
        for key, value in parser.fields
        if key not in overrides
    ]
    fields.extend(overrides.items())
    required = {"__VIEWSTATE", "__EVENTVALIDATION", *overrides}
    missing = required.difference(key for key, _ in fields)
    if missing:
        raise KinderMorganParseError(
            f"capacity export form is missing fields: {', '.join(sorted(missing))}"
        )
    return fields


def _timestamp(value: str, *, effective: bool) -> pendulum.DateTime:
    cleaned = re.sub(r"\s+CCT$", "", value.strip())
    patterns = (
        ("M/D/YYYY hh:mm A", "MM/DD/YYYY hh:mm A")
        if effective
        else ("MM/DD/YYYY hh:mm A", "M/D/YYYY hh:mm A")
    )
    for pattern in patterns:
        try:
            return pendulum.from_format(
                cleaned,
                pattern,
                tz="America/Chicago",
            )
        except (ValueError, pendulum.parsing.exceptions.ParserError):
            pass
    raise KinderMorganParseError(f"unsupported capacity timestamp: {value!r}")


def _quantity(value: str, *, row_position: int, label: str) -> int:
    cleaned = value.replace(",", "").strip()
    try:
        result = int(cleaned)
    except ValueError as exc:
        raise KinderMorganParseError(
            f"capacity row {row_position} has invalid {label}: {value!r}"
        ) from exc
    if result < 0:
        raise KinderMorganParseError(
            f"capacity row {row_position} has negative {label}"
        )
    return result


def _indicator(value: str, *, row_position: int, label: str) -> bool:
    if value == "Y":
        return True
    if value == "N":
        return False
    raise KinderMorganParseError(
        f"capacity row {row_position} has invalid {label}: {value!r}"
    )


def parse_tgp_capacity_export(
    body: bytes,
    *,
    capacity_kind: str,
) -> TgpCapacityExport:
    if capacity_kind not in {"point", "segment"}:
        raise ValueError("capacity_kind must be point or segment")
    spreadsheet_rows = _spreadsheet_rows(body)
    if len(spreadsheet_rows) < 5:
        raise KinderMorganParseError("capacity export is unexpectedly short")
    metadata_headers = tuple(spreadsheet_rows[0][: len(_METADATA_HEADERS)])
    if metadata_headers != _METADATA_HEADERS:
        raise KinderMorganParseError("capacity export metadata header changed")
    metadata = spreadsheet_rows[1] + [""] * len(_METADATA_HEADERS)
    if metadata[0].strip() != "1939164":
        raise KinderMorganParseError("capacity export does not identify TGP")
    expected_headers = _POINT_HEADERS if capacity_kind == "point" else _SEGMENT_HEADERS
    source_headers = tuple(spreadsheet_rows[2][: len(expected_headers)])
    if source_headers != expected_headers:
        raise KinderMorganParseError("capacity export data header changed")

    footer_index: int | None = None
    source_footer_row_count: int | None = None
    for index, row in enumerate(spreadsheet_rows[3:], start=3):
        match = re.fullmatch(r"Row Count:\s*(\d+)", row[0].strip())
        if match:
            footer_index = index
            source_footer_row_count = int(match.group(1))
            break
    if footer_index is None or source_footer_row_count is None:
        raise KinderMorganParseError("capacity export row-count footer is missing")
    raw_data_rows = spreadsheet_rows[3:footer_index]
    if len(raw_data_rows) != source_footer_row_count:
        raise KinderMorganParseError(
            f"capacity export parsed {len(raw_data_rows)} rows; "
            f"footer reports {source_footer_row_count}"
        )

    rows: list[TgpCapacityRow] = []
    for row_position, raw_row in enumerate(raw_data_rows, start=1):
        cells = raw_row + [""] * len(expected_headers)
        if capacity_kind == "point":
            operator_location_id = cells[0].strip()
            operator_segment_id = cells[3].strip()
            capacity_offset = 4
        else:
            operator_location_id = None
            operator_segment_id = cells[0].strip()
            capacity_offset = 3
        location_name = cells[1].strip()
        zone = cells[2].strip()
        if not operator_segment_id or not location_name:
            raise KinderMorganParseError(
                f"capacity row {row_position} lacks segment or location name"
            )
        if capacity_kind == "point" and not operator_location_id:
            raise KinderMorganParseError(
                f"point-capacity row {row_position} lacks location ID"
            )
        design = _quantity(
            cells[capacity_offset],
            row_position=row_position,
            label="design capacity",
        )
        operating = _quantity(
            cells[capacity_offset + 1],
            row_position=row_position,
            label="operating capacity",
        )
        scheduled = _quantity(
            cells[capacity_offset + 2],
            row_position=row_position,
            label="scheduled quantity",
        )
        available = _quantity(
            cells[capacity_offset + 3],
            row_position=row_position,
            label="available capacity",
        )
        rows.append(
            TgpCapacityRow(
                row_position=row_position,
                operator_location_id=operator_location_id,
                operator_segment_id=operator_segment_id,
                location_name=location_name,
                zone=zone,
                design_capacity_dth_per_day=design,
                operating_capacity_dth_per_day=operating,
                scheduled_quantity_dth_per_day=scheduled,
                available_capacity_dth_per_day=available,
                interruptible_scheduled=_indicator(
                    cells[capacity_offset + 4].strip(),
                    row_position=row_position,
                    label="IT indicator",
                ),
                flow_indicator=cells[capacity_offset + 5].strip(),
                all_quantity_available=_indicator(
                    cells[capacity_offset + 6].strip(),
                    row_position=row_position,
                    label="all-quantity-available indicator",
                ),
                quantity_reason=(cells[capacity_offset + 7].strip() or None),
                available_reconciles=(
                    available == max(operating - scheduled, 0)
                ),
            )
        )

    effective_at = _timestamp(metadata[2], effective=True)
    location_purpose = metadata[4].strip()
    point_role = None
    if capacity_kind == "point":
        if location_purpose == "Delivery Location":
            point_role = "delivery"
        elif location_purpose == "Receipt Location":
            point_role = "receipt"
        else:
            raise KinderMorganParseError(
                f"unsupported point-capacity purpose: {location_purpose!r}"
            )
    comments = ""
    if footer_index + 2 < len(spreadsheet_rows):
        comments = spreadsheet_rows[footer_index + 2][0].strip()
    return TgpCapacityExport(
        capacity_kind=capacity_kind,
        point_role=point_role,
        tsp_number=metadata[0].strip(),
        tsp_name=metadata[1].strip(),
        effective_at=effective_at,
        gas_day=effective_at.date(),
        cycle=metadata[3].strip(),
        location_purpose=location_purpose,
        measurement_basis=metadata[5].strip(),
        source_posted_at=_timestamp(metadata[6], effective=False),
        quantity_description=metadata[7].strip(),
        source_footer_row_count=source_footer_row_count,
        schema_sha256=hashlib.sha256(
            "\x1f".join(source_headers).encode("utf-8")
        ).hexdigest(),
        comments=comments,
        rows=tuple(rows),
    )
