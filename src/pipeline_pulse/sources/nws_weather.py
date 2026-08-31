from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import pendulum


@dataclass(frozen=True)
class WeatherAnchor:
    code: str
    name: str
    latitude: float
    longitude: float
    market_role: str


@dataclass(frozen=True)
class DegreeDayForecast:
    local_date: pendulum.Date
    period_start: pendulum.DateTime
    period_end: pendulum.DateTime
    mean_temperature_f: float
    hdd_65: float
    cdd_65: float
    hour_count: int


@dataclass(frozen=True)
class NwsHourlyForecast:
    generated_at: pendulum.DateTime
    updated_at: pendulum.DateTime
    timezone: str
    days: tuple[DegreeDayForecast, ...]


def parse_nws_points(payload: bytes | str) -> str:
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload.lstrip("\ufeff")
    document = json.loads(text)
    forecast_url = str(document.get("properties", {}).get("forecastHourly") or "")
    parts = urlsplit(forecast_url)
    if parts.scheme != "https" or parts.netloc != "api.weather.gov":
        raise ValueError("NWS points response lacks a safe hourly forecast URL")
    return forecast_url


def _temperature_f(value: object, unit: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("NWS hourly temperature must be numeric")
    if unit == "F":
        result = float(value)
    elif unit == "C":
        result = float(value) * 9 / 5 + 32
    else:
        raise ValueError(f"unsupported NWS temperature unit: {unit}")
    if result < -100 or result > 150:
        raise ValueError(f"implausible NWS temperature: {result}")
    return result


def parse_nws_hourly_forecast(payload: bytes | str) -> NwsHourlyForecast:
    """Derive complete local-calendar-day HDD/CDD values from NWS hours."""
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload.lstrip("\ufeff")
    document = json.loads(text)
    properties = document.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("NWS hourly forecast lacks properties")
    generated_at = pendulum.parse(str(properties.get("generatedAt") or ""), strict=True)
    updated_at = pendulum.parse(str(properties.get("updateTime") or ""), strict=True)
    periods = properties.get("periods")
    if not isinstance(periods, list) or not periods:
        raise ValueError("NWS hourly forecast contains no periods")

    grouped: dict[pendulum.Date, list[tuple[pendulum.DateTime, pendulum.DateTime, float]]] = {}
    timezone: str | None = None
    for period in periods:
        if not isinstance(period, dict):
            raise ValueError("NWS hourly period must be an object")
        start = pendulum.parse(str(period.get("startTime") or ""), strict=True)
        end = pendulum.parse(str(period.get("endTime") or ""), strict=True)
        if end <= start:
            raise ValueError("NWS hourly period has a non-positive duration")
        timezone = timezone or start.timezone_name
        grouped.setdefault(start.date(), []).append(
            (
                start,
                end,
                _temperature_f(period.get("temperature"), period.get("temperatureUnit")),
            )
        )

    complete_days: list[DegreeDayForecast] = []
    for local_date, hours in sorted(grouped.items()):
        hours.sort(key=lambda item: item[0])
        first_start = hours[0][0]
        day_start = first_start.start_of("day")
        day_end = day_start.add(days=1)
        if first_start != day_start or hours[-1][1] != day_end:
            continue
        for previous, current in zip(hours, hours[1:]):
            if previous[1] != current[0]:
                raise ValueError(f"NWS hourly periods are not contiguous on {local_date}")
        durations = [(end - start).total_seconds() / 3600 for start, end, _ in hours]
        total_hours = sum(durations)
        if total_hours < 23 or total_hours > 25:
            raise ValueError(f"unexpected NWS forecast day duration: {total_hours}")
        mean_temperature = sum(
            temperature * duration
            for (_, _, temperature), duration in zip(hours, durations)
        ) / total_hours
        complete_days.append(
            DegreeDayForecast(
                local_date=local_date,
                period_start=day_start.in_timezone("UTC"),
                period_end=day_end.in_timezone("UTC"),
                mean_temperature_f=round(mean_temperature, 2),
                hdd_65=round(max(65 - mean_temperature, 0), 2),
                cdd_65=round(max(mean_temperature - 65, 0), 2),
                hour_count=len(hours),
            )
        )
    if not complete_days:
        raise ValueError("NWS hourly forecast contains no complete local days")
    return NwsHourlyForecast(
        generated_at=generated_at.in_timezone("UTC"),
        updated_at=updated_at.in_timezone("UTC"),
        timezone=timezone or "UTC",
        days=tuple(complete_days),
    )
