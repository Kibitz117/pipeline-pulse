from __future__ import annotations

import json
from dataclasses import dataclass

import pendulum


@dataclass(frozen=True)
class EiaStorageSeries:
    series_id: str
    geography: str
    working_gas_bcf: float
    weekly_change_bcf: float
    five_year_average_bcf: float
    pct_vs_five_year_average: float
    pct_vs_year_ago: float


@dataclass(frozen=True)
class EiaStorageRelease:
    release_name: str
    release_date: pendulum.Date
    available_at: pendulum.DateTime
    current_week: pendulum.Date
    five_year_average_label: str
    series: tuple[EiaStorageSeries, ...]


_GEOGRAPHIES = {
    "total lower 48 states": "Lower 48",
    "east region": "East",
    "midwest region": "Midwest",
    "mountain region": "Mountain",
    "pacific region": "Pacific",
    "south central region": "South Central",
    "south central salt region": "South Central Salt",
    "south central nonsalt region": "South Central Nonsalt",
}


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"EIA WNGSR {field} must be numeric")
    return float(value)


def parse_eia_storage_release(payload: bytes | str) -> EiaStorageRelease:
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload.lstrip("\ufeff")
    document = json.loads(text)
    if document.get("release_name") != "Weekly Natural Gas Storage Report":
        raise ValueError("unexpected EIA WNGSR release name")

    release_timestamp = pendulum.from_format(
        str(document["release_date"]),
        "YYYY-MMM-DD HH:mm:ss",
        tz="America/New_York",
        locale="en",
    )
    release_date = release_timestamp.date()
    # EIA publishes the WNGSR at 10:30 a.m. Eastern on its scheduled release day.
    available_at = pendulum.datetime(
        release_date.year,
        release_date.month,
        release_date.day,
        10,
        30,
        tz="America/New_York",
    ).in_timezone("UTC")
    current_week = pendulum.parse(str(document["current_week"]), strict=True).date()

    parsed: list[EiaStorageSeries] = []
    seen_ids: set[str] = set()
    raw_series = document.get("series")
    if not isinstance(raw_series, list) or not raw_series:
        raise ValueError("EIA WNGSR contains no series")
    for item in raw_series:
        if not isinstance(item, dict):
            raise ValueError("EIA WNGSR series item must be an object")
        series_id = str(item.get("series_id") or "").strip()
        if not series_id or series_id in seen_ids:
            raise ValueError("EIA WNGSR series IDs must be present and unique")
        seen_ids.add(series_id)
        source_name = str(item.get("name") or "").strip().lower()
        geography = _GEOGRAPHIES.get(source_name)
        if geography is None:
            raise ValueError(f"unknown EIA WNGSR geography: {source_name}")
        data = item.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], list):
            raise ValueError(f"EIA WNGSR {series_id} lacks current-week data")
        if str(data[0][0]) != current_week.to_date_string():
            raise ValueError(f"EIA WNGSR {series_id} current week does not reconcile")
        calculated = item.get("calculated")
        if not isinstance(calculated, dict):
            raise ValueError(f"EIA WNGSR {series_id} lacks calculated fields")
        parsed.append(
            EiaStorageSeries(
                series_id=series_id,
                geography=geography,
                working_gas_bcf=_number(data[0][1], "working gas"),
                weekly_change_bcf=_number(calculated.get("net_change"), "net change"),
                five_year_average_bcf=_number(calculated.get("5yr-avg"), "five-year average"),
                pct_vs_five_year_average=_number(
                    calculated.get("pct-chg_5yr-avg"),
                    "percent versus five-year average",
                ),
                pct_vs_year_ago=_number(
                    calculated.get("pct-change_yrago"),
                    "percent versus year ago",
                ),
            )
        )

    expected = set(_GEOGRAPHIES.values())
    actual = {item.geography for item in parsed}
    if actual != expected:
        missing = sorted(expected.difference(actual))
        raise ValueError(f"EIA WNGSR missing expected geographies: {missing}")
    return EiaStorageRelease(
        release_name=str(document["release_name"]),
        release_date=release_date,
        available_at=available_at,
        current_week=current_week,
        five_year_average_label=str(document["5yr_avg"]),
        series=tuple(parsed),
    )
