from __future__ import annotations

import json
from dataclasses import dataclass

import pendulum


@dataclass(frozen=True)
class FrontMonthFuturesQuote:
    symbol: str
    contract_label: str
    contract_expiration: pendulum.Date | None
    quote_at: pendulum.DateTime
    price_usd_per_mmbtu: float

    @property
    def vintage(self) -> str:
        if self.contract_expiration is None:
            return self.contract_label
        return f"{self.contract_label} · expires {self.contract_expiration}"


def parse_yahoo_front_month_quote(payload: bytes | str) -> FrontMonthFuturesQuote:
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload.lstrip("\ufeff")
    document = json.loads(text)
    chart = document.get("chart")
    if not isinstance(chart, dict) or chart.get("error"):
        raise ValueError(f"Yahoo chart response error: {chart.get('error') if isinstance(chart, dict) else 'missing chart'}")
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ValueError("Yahoo chart response contains no result")
    meta = results[0].get("meta")
    if not isinstance(meta, dict):
        raise ValueError("Yahoo chart response lacks metadata")
    symbol = str(meta.get("symbol") or "")
    if symbol != "NG=F":
        raise ValueError(f"unexpected Yahoo futures symbol: {symbol}")
    if str(meta.get("currency") or "") != "USD":
        raise ValueError("Yahoo NG=F quote is not denominated in USD")
    price = float(meta["regularMarketPrice"])
    if price < 0 or price > 100:
        raise ValueError(f"implausible natural-gas futures value: {price}")
    quote_at = pendulum.from_timestamp(int(meta["regularMarketTime"]), tz="UTC")
    expiration_raw = meta.get("expireDate")
    expiration = (
        pendulum.from_timestamp(int(expiration_raw), tz="UTC").date()
        if expiration_raw is not None
        else None
    )
    contract_label = str(meta.get("shortName") or meta.get("longName") or symbol)
    return FrontMonthFuturesQuote(
        symbol=symbol,
        contract_label=contract_label,
        contract_expiration=expiration,
        quote_at=quote_at,
        price_usd_per_mmbtu=price,
    )
