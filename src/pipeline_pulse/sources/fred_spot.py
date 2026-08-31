from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import pendulum


@dataclass(frozen=True)
class HenryHubSpotObservation:
    observation_date: pendulum.Date
    price_usd_per_mmbtu: float


def parse_henry_hub_spot_csv(
    payload: bytes | str,
) -> tuple[HenryHubSpotObservation, ...]:
    """Parse FRED's keyless DHHNGSP CSV export.

    Missing observations are omitted. The collector assigns ``available_at``
    from receipt time because the CSV does not publish a per-row release clock.
    """
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != ["observation_date", "DHHNGSP"]:
        raise ValueError(f"unexpected FRED DHHNGSP columns: {reader.fieldnames}")
    observations: list[HenryHubSpotObservation] = []
    seen_dates: set[pendulum.Date] = set()
    for row in reader:
        raw_value = str(row.get("DHHNGSP") or "").strip()
        if raw_value in {"", "."}:
            continue
        observation_date = pendulum.parse(
            str(row.get("observation_date") or ""), strict=True
        ).date()
        if observation_date in seen_dates:
            raise ValueError(f"duplicate FRED DHHNGSP date: {observation_date}")
        value = float(raw_value)
        if value < 0 or value > 100:
            raise ValueError(f"implausible Henry Hub spot value: {value}")
        seen_dates.add(observation_date)
        observations.append(
            HenryHubSpotObservation(
                observation_date=observation_date,
                price_usd_per_mmbtu=value,
            )
        )
    if not observations:
        raise ValueError("FRED DHHNGSP contains no usable observations")
    return tuple(sorted(observations, key=lambda item: item.observation_date))
