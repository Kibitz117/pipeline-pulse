from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

import pendulum

from .fred_spot import HenryHubSpotObservation


@dataclass(frozen=True)
class EiaHenryHubSpotRelease:
    release_date: pendulum.Date
    observations: tuple[HenryHubSpotObservation, ...]


class _DailySpotTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_target_table = False
        self.target_depth = 0
        self.in_row = False
        self.in_cell = False
        self.cell_text: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value for key, value in attrs}
        if tag.lower() == "table":
            if self.in_target_table:
                self.target_depth += 1
            elif attributes.get("summary") == (
                "Henry Hub Natural Gas Spot Price (Dollars per Million Btu)"
            ):
                self.in_target_table = True
                self.target_depth = 1
            return
        if not self.in_target_table:
            return
        if tag.lower() == "tr":
            self.in_row = True
            self.current_row = []
        elif tag.lower() in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.cell_text = []

    def handle_endtag(self, tag: str) -> None:
        if not self.in_target_table:
            return
        if tag.lower() in {"td", "th"} and self.in_cell:
            self.current_row.append(" ".join("".join(self.cell_text).split()))
            self.in_cell = False
        elif tag.lower() == "tr" and self.in_row:
            self.rows.append(self.current_row)
            self.in_row = False
        elif tag.lower() == "table":
            self.target_depth -= 1
            if self.target_depth == 0:
                self.in_target_table = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_text.append(data)


_WEEK_PATTERN = re.compile(
    r"^(?P<year>\d{4})\s+(?P<month>[A-Z][a-z]{2})-\s*(?P<day>\d{1,2})\s+to\s+"
    r"(?P<end_month>[A-Z][a-z]{2})-\s*(?P<end_day>\d{1,2})$"
)
_RELEASE_PATTERN = re.compile(r"Release Date:\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)


def parse_eia_henry_hub_spot_html(payload: bytes | str) -> EiaHenryHubSpotRelease:
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload.lstrip("\ufeff")
    release_match = _RELEASE_PATTERN.search(text)
    if release_match is None:
        raise ValueError("EIA Henry Hub page lacks a release date")
    release_date = pendulum.from_format(
        release_match.group(1), "M/D/YYYY", tz="America/New_York"
    ).date()

    parser = _DailySpotTableParser()
    parser.feed(text)
    observations: list[HenryHubSpotObservation] = []
    seen_dates: set[pendulum.Date] = set()
    for row in parser.rows:
        if len(row) != 6:
            continue
        week_match = _WEEK_PATTERN.match(row[0])
        if week_match is None:
            continue
        week_start = pendulum.from_format(
            (
                f"{week_match.group('year')}-"
                f"{week_match.group('month')}-"
                f"{week_match.group('day')}"
            ),
            "YYYY-MMM-D",
            locale="en",
        ).date()
        for day_offset, raw_value in enumerate(row[1:]):
            value_text = raw_value.strip()
            if value_text in {"", "-", "--", "NA", "W"}:
                continue
            observation_date = week_start.add(days=day_offset)
            if observation_date in seen_dates:
                raise ValueError(f"duplicate EIA Henry Hub date: {observation_date}")
            value = float(value_text)
            if value < 0 or value > 100:
                raise ValueError(f"implausible EIA Henry Hub spot value: {value}")
            seen_dates.add(observation_date)
            observations.append(
                HenryHubSpotObservation(
                    observation_date=observation_date,
                    price_usd_per_mmbtu=value,
                )
            )
    if not observations:
        raise ValueError("EIA Henry Hub page contains no daily observations")
    observations.sort(key=lambda item: item.observation_date)
    if observations[-1].observation_date > release_date:
        raise ValueError("EIA Henry Hub observation is newer than the release date")
    return EiaHenryHubSpotRelease(
        release_date=release_date,
        observations=tuple(observations),
    )
