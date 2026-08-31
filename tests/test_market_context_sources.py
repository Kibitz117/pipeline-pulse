from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pendulum

from pipeline_pulse.artifacts import StoredArtifact
from pipeline_pulse.database import (
    connect_database,
    initialize_database,
    start_fetch_run,
    store_artifact_record,
    store_henry_hub_spot,
    store_nws_degree_day_forecast,
    store_yahoo_front_month_quote,
)
from pipeline_pulse.sources.fred_spot import parse_henry_hub_spot_csv
from pipeline_pulse.sources.eia_spot import parse_eia_henry_hub_spot_html
from pipeline_pulse.sources.nws_weather import (
    WeatherAnchor,
    parse_nws_hourly_forecast,
    parse_nws_points,
)
from pipeline_pulse.sources.yahoo_futures import parse_yahoo_front_month_quote


class MarketContextSourceTests(unittest.TestCase):
    def _artifact(
        self,
        root: Path,
        *,
        artifact_id: str,
        source_code: str,
        canonical_url: str,
        body: str,
        received_at: pendulum.DateTime,
    ) -> StoredArtifact:
        raw_path = root / f"{source_code}.json"
        raw_path.write_text(body, encoding="utf-8")
        return StoredArtifact(
            artifact_id=artifact_id,
            source_code=source_code,
            canonical_url=canonical_url,
            content_sha256="a" * 64,
            mime_type="application/json",
            http_status=200,
            requested_at=received_at.subtract(seconds=1),
            received_at=received_at,
            recorded_at=received_at,
            raw_path=raw_path.as_posix(),
            size_bytes=raw_path.stat().st_size,
            etag=None,
            last_modified=None,
            content_disposition=None,
        )

    def test_fred_parser_omits_missing_rows(self) -> None:
        observations = parse_henry_hub_spot_csv(
            "observation_date,DHHNGSP\n"
            "2026-08-24,2.83\n"
            "2026-08-25,2.70\n"
            "2026-08-26,.\n"
        )
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[-1].observation_date.to_date_string(), "2026-08-25")
        self.assertEqual(observations[-1].price_usd_per_mmbtu, 2.70)

    def test_eia_spot_parser_preserves_release_and_daily_dates(self) -> None:
        release = parse_eia_henry_hub_spot_html(
            """
            <table SUMMARY='Henry Hub Natural Gas Spot Price (Dollars per Million Btu)'>
              <tr><th>Week Of</th><th>Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th></tr>
              <tr><td>2026 Aug-24 to Aug-28</td><td>2.83</td><td>2.70</td><td></td><td></td><td></td></tr>
            </table>
            <td>Release Date: 8/26/2026</td>
            """
        )
        self.assertEqual(release.release_date.to_date_string(), "2026-08-26")
        self.assertEqual(len(release.observations), 2)
        self.assertEqual(
            release.observations[-1].observation_date.to_date_string(),
            "2026-08-25",
        )

    def test_nws_parser_derives_complete_day_degree_days(self) -> None:
        self.assertEqual(
            parse_nws_points(
                json.dumps(
                    {
                        "properties": {
                            "forecastHourly": "https://api.weather.gov/gridpoints/OKX/33,42/forecast/hourly"
                        }
                    }
                )
            ),
            "https://api.weather.gov/gridpoints/OKX/33,42/forecast/hourly",
        )
        start = pendulum.datetime(2026, 8, 31, tz="America/New_York")
        periods = []
        for hour in range(48):
            period_start = start.add(hours=hour)
            periods.append(
                {
                    "startTime": period_start.to_iso8601_string(),
                    "endTime": period_start.add(hours=1).to_iso8601_string(),
                    "temperature": 80 if hour < 24 else 50,
                    "temperatureUnit": "F",
                }
            )
        forecast = parse_nws_hourly_forecast(
            json.dumps(
                {
                    "properties": {
                        "generatedAt": "2026-08-30T19:00:41Z",
                        "updateTime": "2026-08-30T18:25:12Z",
                        "periods": periods,
                    }
                }
            )
        )
        self.assertEqual(len(forecast.days), 2)
        self.assertEqual(forecast.days[0].cdd_65, 15)
        self.assertEqual(forecast.days[0].hdd_65, 0)
        self.assertEqual(forecast.days[1].hdd_65, 15)

    def test_yahoo_parser_preserves_contract_and_quote_time(self) -> None:
        quote = parse_yahoo_front_month_quote(
            json.dumps(
                {
                    "chart": {
                        "error": None,
                        "result": [
                            {
                                "meta": {
                                    "symbol": "NG=F",
                                    "currency": "USD",
                                    "shortName": "Natural Gas Oct 26",
                                    "regularMarketPrice": 2.95,
                                    "regularMarketTime": 1788116400,
                                    "expireDate": 1790121600,
                                }
                            }
                        ],
                    }
                }
            )
        )
        self.assertEqual(quote.symbol, "NG=F")
        self.assertEqual(quote.contract_label, "Natural Gas Oct 26")
        self.assertIn("expires", quote.vintage)

    def test_stores_explicit_provider_type_and_source_clocks(self) -> None:
        received = pendulum.datetime(2026, 8, 30, 20, 0, tz="UTC")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            connection = connect_database(root / "test.duckdb")
            initialize_database(connection)

            fred_body = "observation_date,DHHNGSP\n2026-08-25,2.70\n"
            fred = self._artifact(
                root,
                artifact_id="fred-1",
                source_code="fred_dhhngsp",
                canonical_url="https://fred.stlouisfed.org/graph/fredgraph.csv?id=DHHNGSP",
                body=fred_body,
                received_at=received,
            )
            run_id = start_fetch_run(connection, fred.source_code, requested_at=fred.requested_at)
            store_artifact_record(connection, run_id, fred)
            store_henry_hub_spot(connection, fred, parse_henry_hub_spot_csv(fred_body))

            yahoo_body = json.dumps(
                {
                    "chart": {
                        "error": None,
                        "result": [
                            {
                                "meta": {
                                    "symbol": "NG=F",
                                    "currency": "USD",
                                    "shortName": "Natural Gas Oct 26",
                                    "regularMarketPrice": 2.95,
                                    "regularMarketTime": 1788116400,
                                }
                            }
                        ],
                    }
                }
            )
            yahoo = self._artifact(
                root,
                artifact_id="yahoo-1",
                source_code="yahoo_ng_front_month",
                canonical_url="https://query2.finance.yahoo.com/v8/finance/chart/NG%3DF",
                body=yahoo_body,
                received_at=received,
            )
            yahoo_run = start_fetch_run(connection, yahoo.source_code, requested_at=yahoo.requested_at)
            store_artifact_record(connection, yahoo_run, yahoo)
            store_yahoo_front_month_quote(
                connection, yahoo, parse_yahoo_front_month_quote(yahoo_body)
            )

            rows = connection.execute(
                """
                SELECT provider, observation_type, source_published_at,
                       available_at, artifact_id
                FROM market_observations
                ORDER BY observation_type
                """
            ).fetchall()
            connection.close()

        self.assertEqual(rows[0][0], "Yahoo Finance")
        self.assertEqual(rows[0][1], "futures_proxy")
        self.assertIsNotNone(rows[0][2])
        self.assertEqual(rows[1][0], "U.S. EIA")
        self.assertEqual(rows[1][1], "physical_spot")
        self.assertIsNone(rows[1][2])
        self.assertEqual(pendulum.instance(rows[1][3]).in_timezone("UTC"), received)


if __name__ == "__main__":
    unittest.main()
