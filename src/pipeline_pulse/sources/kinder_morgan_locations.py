from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from io import StringIO

import pendulum

from .kinder_morgan import KinderMorganParseError

_HEADERS = (
    "TSP",
    "TSP Name",
    "TSP FERC CID",
    "Date/Time",
    "Comments",
    "Loc",
    "Loc Name",
    "Dir Flo",
    "Loc Cnty",
    "Loc St Abbrev",
    "Loc Type Ind",
    "Loc Zone (Rec)",
    "Loc Zone (Del)",
    "Seg Nbr",
    "Nom Ind",
    "Loc Stat Ind",
    "Eff Date",
    "Inact Date",
    "Up/Dn Ind",
    "Up/Dn Name",
    "Up/Dn ID",
    "Up/Dn ID Prop",
    "Up/Dn FERC CID Ind",
    "Up/Dn FERC CID",
    "Up/Dn Loc",
    "Up/Dn Loc Name",
    "Up/Dn Loc 2",
    "Up/Dn Loc Name2",
    "Update D/T",
)
EXPECTED_LOCATION_SCHEMA_SHA256 = hashlib.sha256(
    "\x1f".join(_HEADERS).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class TgpLocation:
    row_position: int
    operator_location_id: str
    location_name: str
    flow_role: str
    county_name: str
    state_abbreviation: str
    location_type: str
    receipt_zone: str
    delivery_zone: str
    operator_segment_id: str
    nomination_indicator: str
    status_indicator: str
    effective_date: pendulum.Date | None
    inactive_date: pendulum.Date | None
    interconnect_indicator: bool | None
    counterparty_name: str | None
    counterparty_id: str | None
    counterparty_property_id: str | None
    counterparty_ferc_indicator: bool | None
    counterparty_ferc_cid: str | None
    counterparty_location_id: str | None
    counterparty_location_name: str | None
    counterparty_location_id_2: str | None
    counterparty_location_name_2: str | None
    source_updated_at: pendulum.DateTime | None


@dataclass(frozen=True)
class TgpLocationExport:
    tsp_number: str
    tsp_name: str
    tsp_ferc_cid: str
    source_as_of: pendulum.DateTime
    comments: str
    source_column_count: int
    schema_sha256: str
    rows: tuple[TgpLocation, ...]


def _value(row: dict[str, str], key: str) -> str:
    return re.sub(r"\s+", " ", (row.get(key) or "").strip())


def _optional(row: dict[str, str], key: str) -> str | None:
    return _value(row, key) or None


def _date(value: str) -> pendulum.Date | None:
    if not value:
        return None
    try:
        return pendulum.from_format(value, "YYYYMMDD", tz="America/Chicago").date()
    except (ValueError, pendulum.parsing.exceptions.ParserError) as exc:
        raise KinderMorganParseError(
            f"unsupported Kinder Morgan location date: {value!r}"
        ) from exc


def _timestamp(value: str) -> pendulum.DateTime | None:
    if not value:
        return None
    try:
        return pendulum.from_format(
            value,
            "YYYYMMDD HH:mm",
            tz="America/Chicago",
        )
    except (ValueError, pendulum.parsing.exceptions.ParserError) as exc:
        raise KinderMorganParseError(
            f"unsupported Kinder Morgan location timestamp: {value!r}"
        ) from exc


def _indicator(value: str) -> bool | None:
    if not value:
        return None
    if value == "Y":
        return True
    if value == "N":
        return False
    raise KinderMorganParseError(
        f"unsupported Kinder Morgan location indicator: {value!r}"
    )


def parse_kinder_morgan_location_export(
    body: bytes,
    *,
    expected_tsp_number: str,
    expected_ferc_cid: str,
    pipeline_label: str,
) -> TgpLocationExport:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KinderMorganParseError(
            f"{pipeline_label} location export is not UTF-8 CSV"
        ) from exc
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise KinderMorganParseError(f"{pipeline_label} location export has no header")
    missing = set(_HEADERS).difference(reader.fieldnames)
    if missing:
        raise KinderMorganParseError(
            f"{pipeline_label} location export is missing columns: "
            f"{', '.join(sorted(missing))}"
        )

    raw_rows = list(reader)
    if not raw_rows:
        raise KinderMorganParseError(
            f"{pipeline_label} location export has no data rows"
        )
    tsp_numbers = {_value(row, "TSP") for row in raw_rows}
    tsp_names = {_value(row, "TSP Name") for row in raw_rows}
    ferc_cids = {_value(row, "TSP FERC CID") for row in raw_rows}
    source_times = {_value(row, "Date/Time") for row in raw_rows}
    comments = {_value(row, "Comments") for row in raw_rows}
    if any(
        len(values) != 1 for values in (tsp_numbers, tsp_names, ferc_cids, source_times)
    ):
        raise KinderMorganParseError(
            f"{pipeline_label} location export metadata changes within the file"
        )
    if tsp_numbers != {expected_tsp_number} or ferc_cids != {expected_ferc_cid}:
        raise KinderMorganParseError(
            f"location export does not identify {pipeline_label}"
        )

    rows: list[TgpLocation] = []
    seen_ids: set[str] = set()
    for position, row in enumerate(raw_rows, start=1):
        location_id = _value(row, "Loc")
        if not location_id:
            raise KinderMorganParseError(
                f"{pipeline_label} location row {position} has no operator location ID"
            )
        if location_id in seen_ids:
            raise KinderMorganParseError(
                f"duplicate {pipeline_label} operator location ID: {location_id}"
            )
        seen_ids.add(location_id)
        location_name = _value(row, "Loc Name")
        flow_role = _value(row, "Dir Flo")
        county_name = _value(row, "Loc Cnty")
        state_abbreviation = _value(row, "Loc St Abbrev")
        if not location_name or not county_name:
            raise KinderMorganParseError(
                f"{pipeline_label} location row {position} is missing its name or county"
            )
        if flow_role not in {"R", "D", "B"}:
            raise KinderMorganParseError(
                f"{pipeline_label} location row {position} has unsupported flow role: "
                f"{flow_role!r}"
            )
        if state_abbreviation and re.fullmatch(r"[A-Z]{2}", state_abbreviation) is None:
            raise KinderMorganParseError(
                f"{pipeline_label} location row {position} has invalid state: "
                f"{state_abbreviation!r}"
            )
        rows.append(
            TgpLocation(
                row_position=position,
                operator_location_id=location_id,
                location_name=location_name,
                flow_role=flow_role,
                county_name=county_name,
                state_abbreviation=state_abbreviation,
                location_type=_value(row, "Loc Type Ind"),
                receipt_zone=_value(row, "Loc Zone (Rec)"),
                delivery_zone=_value(row, "Loc Zone (Del)"),
                operator_segment_id=_value(row, "Seg Nbr"),
                nomination_indicator=_value(row, "Nom Ind"),
                status_indicator=_value(row, "Loc Stat Ind"),
                effective_date=_date(_value(row, "Eff Date")),
                inactive_date=_date(_value(row, "Inact Date")),
                interconnect_indicator=_indicator(_value(row, "Up/Dn Ind")),
                counterparty_name=_optional(row, "Up/Dn Name"),
                counterparty_id=_optional(row, "Up/Dn ID"),
                counterparty_property_id=_optional(row, "Up/Dn ID Prop"),
                counterparty_ferc_indicator=_indicator(
                    _value(row, "Up/Dn FERC CID Ind")
                ),
                counterparty_ferc_cid=_optional(row, "Up/Dn FERC CID"),
                counterparty_location_id=_optional(row, "Up/Dn Loc"),
                counterparty_location_name=_optional(row, "Up/Dn Loc Name"),
                counterparty_location_id_2=_optional(row, "Up/Dn Loc 2"),
                counterparty_location_name_2=_optional(row, "Up/Dn Loc Name2"),
                source_updated_at=_timestamp(_value(row, "Update D/T")),
            )
        )

    source_as_of = _timestamp(next(iter(source_times)))
    if source_as_of is None:
        raise KinderMorganParseError(
            f"{pipeline_label} location export has no source timestamp"
        )
    return TgpLocationExport(
        tsp_number=next(iter(tsp_numbers)),
        tsp_name=next(iter(tsp_names)),
        tsp_ferc_cid=next(iter(ferc_cids)),
        source_as_of=source_as_of,
        comments=next(iter(comments)),
        source_column_count=len(reader.fieldnames),
        schema_sha256=hashlib.sha256(
            "\x1f".join(reader.fieldnames).encode("utf-8")
        ).hexdigest(),
        rows=tuple(rows),
    )


def parse_tgp_location_export(body: bytes) -> TgpLocationExport:
    """Backward-compatible TGP parser over the shared Kinder Morgan schema."""
    return parse_kinder_morgan_location_export(
        body,
        expected_tsp_number="1939164",
        expected_ferc_cid="C000020",
        pipeline_label="TGP",
    )
