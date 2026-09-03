from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode

import pendulum

from .artifacts import ArtifactStore, StoredArtifact
from .database import (
    connect_database,
    finish_fetch_run,
    geocode_locations_to_counties,
    initialize_database,
    start_fetch_run,
    store_artifact_record,
    store_capacity_export,
    store_county_references,
    store_eia_storage_release,
    store_henry_hub_spot,
    store_location_export,
    store_map_reference_layer,
    store_notice_index_export,
    store_notice_index_page,
    store_notice_version,
    store_nws_degree_day_forecast,
    store_outage_impact_observations,
    store_yahoo_front_month_quote,
)
from .http import ReadOnlyHTTPClient
from .pipelines import get_pipeline_config
from .sources.census import parse_county_gazetteer
from .sources.eia_spot import parse_eia_henry_hub_spot_html
from .sources.eia_storage import parse_eia_storage_release
from .sources.kinder_morgan import (
    build_location_export_form,
    build_notice_export_form,
    parse_notice_detail,
    parse_notice_index,
    parse_notice_index_export,
)
from .sources.kinder_morgan_capacity import (
    TgpCapacityExport,
    build_capacity_export_form,
    parse_kinder_morgan_capacity_export,
)
from .sources.kinder_morgan_locations import (
    parse_kinder_morgan_location_export,
)
from .sources.kinder_morgan_tables import parse_tgp_outage_impact_report
from .sources.nws_weather import (
    WeatherAnchor,
    parse_nws_hourly_forecast,
    parse_nws_points,
)
from .sources.yahoo_futures import parse_yahoo_front_month_quote

_TGP_CONFIG = get_pipeline_config("TGP")
TGP_CRITICAL_INDEX_URL = _TGP_CONFIG.critical_index_url
TGP_NOTICE_DETAIL_URL = _TGP_CONFIG.notice_detail_url("{notice_id}")
TGP_LOCATION_URL = _TGP_CONFIG.location_url
TGP_POINT_CAPACITY_URL = _TGP_CONFIG.point_capacity_url
TGP_SEGMENT_CAPACITY_URL = _TGP_CONFIG.segment_capacity_url
CENSUS_COUNTY_GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2025_Gazetteer/2025_Gaz_counties_national.zip"
)
CENSUS_LEGACY_COUNTY_GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
    "2021_Gazetteer/2021_Gaz_counties_national.zip"
)
_TGP_STATES = (
    "AL",
    "AR",
    "CT",
    "KY",
    "LA",
    "MA",
    "MS",
    "NH",
    "NJ",
    "NY",
    "OH",
    "PA",
    "RI",
    "TN",
    "TX",
    "WV",
)


def _census_state_boundaries_url(states: tuple[str, ...]) -> str:
    state_where = "STUSAB IN (" + ",".join(f"'{state}'" for state in states) + ")"
    return (
        "https://tigerweb.geo.census.gov/arcgis/rest/services/"
        "Generalized_ACS2025/State_County/MapServer/9/query?"
        + urlencode(
            {
                "where": state_where,
                "outFields": "GEOID,STUSAB,NAME",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
            }
        )
    )


CENSUS_TGP_STATE_BOUNDARIES_URL = _census_state_boundaries_url(_TGP_STATES)
EIA_WNGSR_URL = "https://ir.eia.gov/ngs/wngsr.json"
EIA_HENRY_HUB_SPOT_URL = "https://www.eia.gov/dnav/ng/hist/rngwhhdD.htm"
YAHOO_NG_FRONT_MONTH_URL = (
    "https://query2.finance.yahoo.com/v8/finance/chart/NG%3DF"
    "?interval=1d&range=10d&events=div%2Csplits"
)
NWS_WEATHER_ANCHORS = (
    WeatherAnchor(
        code="new_york",
        name="New York City",
        latitude=40.7128,
        longitude=-74.0060,
        market_role="TGP Northeast delivery-market demand proxy",
    ),
    WeatherAnchor(
        code="boston",
        name="Boston",
        latitude=42.3601,
        longitude=-71.0589,
        market_role="TGP New England delivery-market demand proxy",
    ),
)


@dataclass(frozen=True)
class CollectionSummary:
    run_id: str
    artifact_id: str
    source_row_count: int
    page_row_count: int
    page_index: int
    page_count: int
    newest_notice_id: str
    oldest_notice_id: str
    raw_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class ReprocessSummary:
    artifacts_found: int
    artifacts_reprocessed: int
    rows_reprocessed: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class ExportCollectionSummary:
    run_id: str
    index_artifact_id: str
    export_artifact_id: str
    index_footer_row_count: int
    export_footer_row_count: int
    exported_row_count: int
    newest_notice_id: str
    oldest_notice_id: str
    index_raw_path: str
    export_raw_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class DetailCollectionSummary:
    notice_type: str | None
    requested: int
    completed: int
    failed: int
    remaining: int
    new_detail_notice_ids: tuple[str, ...]
    rechecked_notice_ids: tuple[str, ...]
    revised_notice_ids: tuple[str, ...]
    unchanged_notice_ids: tuple[str, ...]
    completed_notice_ids: tuple[str, ...]
    failed_notice_ids: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class DetailReprocessSummary:
    artifacts_found: int
    artifacts_reprocessed: int
    outage_report_artifacts: int
    impact_rows_reprocessed: int
    failed_artifact_ids: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class LocationCollectionSummary:
    run_id: str
    location_rows: int
    segment_count: int
    county_count: int
    geocoded_rows: int
    ungeocoded_rows: int
    state_boundary_features: int
    location_raw_path: str
    county_reference_raw_path: str
    legacy_county_reference_raw_path: str
    state_boundaries_raw_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class CapacityCollectionSummary:
    run_id: str
    gas_days: tuple[str, ...]
    cycles: tuple[str, ...]
    point_delivery_rows: int
    point_receipt_rows: int
    segment_rows: int
    available_reconciliation_mismatch_count: int
    matched_facility_rows: int
    matched_segment_rows: int
    raw_paths: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class EiaStorageCollectionSummary:
    run_id: str
    artifact_id: str
    release_date: str
    current_week: str
    available_at_utc: str
    geography_count: int
    observation_count: int
    raw_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class HenryHubSpotCollectionSummary:
    run_id: str
    artifact_id: str
    release_date: str
    latest_observation_date: str
    latest_price_usd_per_mmbtu: float
    observation_count: int
    available_at_utc: str
    raw_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class DegreeDayCollectionSummary:
    run_id: str
    anchors: tuple[str, ...]
    forecast_day_count: int
    observation_count: int
    generated_at_utc: tuple[str, ...]
    raw_paths: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class FuturesCollectionSummary:
    run_id: str
    artifact_id: str
    symbol: str
    contract_label: str
    contract_expiration: str | None
    quote_at_utc: str
    price_usd_per_mmbtu: float
    raw_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def collect_henry_hub_spot(
    *,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
    history_days: int = 730,
) -> HenryHubSpotCollectionSummary:
    if history_days < 30:
        raise ValueError("Henry Hub spot history_days must be at least 30")
    earliest_date = pendulum.today("UTC").subtract(days=history_days).date()
    url = EIA_HENRY_HUB_SPOT_URL
    http_client = client or ReadOnlyHTTPClient(timeout_seconds=45.0)
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = start_fetch_run(
        connection,
        "eia_henry_hub_spot",
        requested_at=pendulum.now("UTC"),
        config={
            "url": url,
            "series": "DHHNGSP",
            "history_days": history_days,
            "provider": "U.S. EIA",
        },
    )
    try:
        fetch = http_client.fetch(url, accept="text/html")
        artifact = ArtifactStore(raw_root).store("eia_henry_hub_spot", fetch)
        store_artifact_record(connection, run_id, artifact)
        release = parse_eia_henry_hub_spot_html(fetch.body)
        observations = tuple(
            observation
            for observation in release.observations
            if observation.observation_date >= earliest_date
        )
        if not observations:
            raise ValueError(
                "EIA Henry Hub page has no observations in requested history"
            )
        count = store_henry_hub_spot(
            connection,
            artifact,
            observations,
            provider="U.S. EIA",
            series_code="EIA:DHHNGSP",
            source_published_at=None,
            vintage=release.release_date.to_date_string(),
        )
        finish_fetch_run(connection, run_id)
    except Exception as exc:
        finish_fetch_run(connection, run_id, error=exc)
        raise
    finally:
        connection.close()
    latest = observations[-1]
    return HenryHubSpotCollectionSummary(
        run_id=run_id,
        artifact_id=artifact.artifact_id,
        release_date=release.release_date.to_date_string(),
        latest_observation_date=latest.observation_date.to_date_string(),
        latest_price_usd_per_mmbtu=latest.price_usd_per_mmbtu,
        observation_count=count,
        available_at_utc=artifact.received_at.to_iso8601_string(),
        raw_path=artifact.raw_path,
    )


def collect_nws_degree_days(
    *,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
    anchors: tuple[WeatherAnchor, ...] = NWS_WEATHER_ANCHORS,
) -> DegreeDayCollectionSummary:
    http_client = client or ReadOnlyHTTPClient(timeout_seconds=180.0)
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = start_fetch_run(
        connection,
        "nws_tgp_degree_days",
        requested_at=pendulum.now("UTC"),
        config={
            "base_temperature_f": 65,
            "anchors": [asdict(anchor) for anchor in anchors],
        },
    )
    parsed_forecasts: list[tuple[WeatherAnchor, StoredArtifact, object]] = []
    raw_paths: list[str] = []
    try:
        for anchor in anchors:
            points_url = (
                f"https://api.weather.gov/points/{anchor.latitude},{anchor.longitude}"
            )
            points_fetch = http_client.fetch(points_url, accept="application/geo+json")
            points_artifact = ArtifactStore(raw_root).store(
                "nws_forecast_points", points_fetch
            )
            store_artifact_record(connection, run_id, points_artifact)
            raw_paths.append(points_artifact.raw_path)
            forecast_url = parse_nws_points(points_fetch.body)

            forecast_fetch = http_client.fetch(
                forecast_url, accept="application/geo+json"
            )
            forecast_artifact = ArtifactStore(raw_root).store(
                "nws_hourly_forecast", forecast_fetch
            )
            store_artifact_record(connection, run_id, forecast_artifact)
            raw_paths.append(forecast_artifact.raw_path)
            parsed_forecasts.append(
                (
                    anchor,
                    forecast_artifact,
                    parse_nws_hourly_forecast(forecast_fetch.body),
                )
            )
        observation_count = sum(
            store_nws_degree_day_forecast(connection, artifact, anchor, forecast)
            for anchor, artifact, forecast in parsed_forecasts
        )
        finish_fetch_run(connection, run_id)
    except Exception as exc:
        finish_fetch_run(connection, run_id, error=exc)
        raise
    finally:
        connection.close()
    return DegreeDayCollectionSummary(
        run_id=run_id,
        anchors=tuple(anchor.name for anchor, _, _ in parsed_forecasts),
        forecast_day_count=sum(
            len(forecast.days) for _, _, forecast in parsed_forecasts
        ),
        observation_count=observation_count,
        generated_at_utc=tuple(
            forecast.generated_at.to_iso8601_string()
            for _, _, forecast in parsed_forecasts
        ),
        raw_paths=tuple(raw_paths),
    )


def collect_yahoo_front_month_futures(
    *,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> FuturesCollectionSummary:
    http_client = client or ReadOnlyHTTPClient(timeout_seconds=60.0)
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = start_fetch_run(
        connection,
        "yahoo_ng_front_month",
        requested_at=pendulum.now("UTC"),
        config={
            "url": YAHOO_NG_FRONT_MONTH_URL,
            "symbol": "NG=F",
            "usage": "optional local research proxy",
        },
    )
    try:
        fetch = http_client.fetch(YAHOO_NG_FRONT_MONTH_URL, accept="application/json")
        artifact = ArtifactStore(raw_root).store("yahoo_ng_front_month", fetch)
        store_artifact_record(connection, run_id, artifact)
        quote = parse_yahoo_front_month_quote(fetch.body)
        store_yahoo_front_month_quote(connection, artifact, quote)
        finish_fetch_run(connection, run_id)
    except Exception as exc:
        finish_fetch_run(connection, run_id, error=exc)
        raise
    finally:
        connection.close()
    return FuturesCollectionSummary(
        run_id=run_id,
        artifact_id=artifact.artifact_id,
        symbol=quote.symbol,
        contract_label=quote.contract_label,
        contract_expiration=(
            quote.contract_expiration.to_date_string()
            if quote.contract_expiration is not None
            else None
        ),
        quote_at_utc=quote.quote_at.to_iso8601_string(),
        price_usd_per_mmbtu=quote.price_usd_per_mmbtu,
        raw_path=artifact.raw_path,
    )


def collect_eia_storage(
    *,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> EiaStorageCollectionSummary:
    http_client = client or ReadOnlyHTTPClient(timeout_seconds=180.0)
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = start_fetch_run(
        connection,
        "eia_wngsr",
        requested_at=pendulum.now("UTC"),
        config={"url": EIA_WNGSR_URL, "release": "weekly_natural_gas_storage"},
    )
    try:
        fetch = http_client.fetch(EIA_WNGSR_URL, accept="application/json")
        artifact = ArtifactStore(raw_root).store("eia_wngsr", fetch)
        store_artifact_record(connection, run_id, artifact)
        release = parse_eia_storage_release(fetch.body)
        observation_count = store_eia_storage_release(connection, artifact, release)
        finish_fetch_run(connection, run_id)
    except Exception as exc:
        finish_fetch_run(connection, run_id, error=exc)
        raise
    finally:
        connection.close()
    return EiaStorageCollectionSummary(
        run_id=run_id,
        artifact_id=artifact.artifact_id,
        release_date=release.release_date.to_date_string(),
        current_week=release.current_week.to_date_string(),
        available_at_utc=release.available_at.to_iso8601_string(),
        geography_count=len(release.series),
        observation_count=observation_count,
        raw_path=artifact.raw_path,
    )


def collect_kinder_morgan_critical_index(
    *,
    pipeline_id: str,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> CollectionSummary:
    pipeline = get_pipeline_config(pipeline_id)
    source_code = f"km_{pipeline.slug}_critical"
    http_client = client or ReadOnlyHTTPClient(timeout_seconds=180.0)
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = start_fetch_run(
        connection,
        source_code,
        requested_at=pendulum.now("UTC"),
        config={
            "pipeline_id": pipeline.pipeline_id,
            "url": pipeline.critical_index_url,
            "page": 0,
        },
    )
    try:
        fetch = http_client.fetch(pipeline.critical_index_url, accept="text/html")
        artifact = ArtifactStore(raw_root).store(source_code, fetch)
        store_artifact_record(connection, run_id, artifact)
        page = parse_notice_index(fetch.text, timezone_name=pipeline.timezone)
        store_notice_index_page(
            connection,
            artifact,
            page,
            pipeline_id=pipeline.pipeline_id,
        )
        finish_fetch_run(connection, run_id)
    except Exception as exc:
        finish_fetch_run(connection, run_id, error=exc)
        raise
    finally:
        connection.close()

    return CollectionSummary(
        run_id=run_id,
        artifact_id=artifact.artifact_id,
        source_row_count=page.total_row_count,
        page_row_count=len(page.rows),
        page_index=page.page_index,
        page_count=page.page_count,
        newest_notice_id=page.rows[0].notice_id,
        oldest_notice_id=page.rows[-1].notice_id,
        raw_path=artifact.raw_path,
    )


def collect_tgp_critical_index(
    *,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> CollectionSummary:
    return collect_kinder_morgan_critical_index(
        pipeline_id="TGP",
        database_path=database_path,
        raw_root=raw_root,
        client=client,
    )


def collect_kinder_morgan_locations(
    *,
    pipeline_id: str,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> LocationCollectionSummary:
    """Archive KM locations and attach explicit county-level coordinates."""
    pipeline = get_pipeline_config(pipeline_id)
    source_prefix = f"km_{pipeline.slug}"
    http_client = client or ReadOnlyHTTPClient(timeout_seconds=180.0)
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = start_fetch_run(
        connection,
        f"{pipeline.slug}_location_reference",
        requested_at=pendulum.now("UTC"),
        config={
            "pipeline_id": pipeline.pipeline_id,
            "location_url": pipeline.location_url,
            "county_reference_url": CENSUS_COUNTY_GAZETTEER_URL,
            "legacy_county_reference_url": CENSUS_LEGACY_COUNTY_GAZETTEER_URL,
            "state_boundary_strategy": "states observed in location export",
            "coordinate_precision": "county",
        },
    )
    try:
        page_fetch = http_client.fetch(pipeline.location_url, accept="text/html")
        page_artifact = ArtifactStore(raw_root).store(
            f"{source_prefix}_locations_page",
            page_fetch,
        )
        store_artifact_record(connection, run_id, page_artifact)

        location_fetch = http_client.post_form(
            pipeline.location_url,
            build_location_export_form(page_fetch.text),
            accept="text/csv",
            referer=pipeline.location_url,
        )
        location_artifact = ArtifactStore(raw_root).store(
            f"{source_prefix}_locations",
            location_fetch,
        )
        store_artifact_record(connection, run_id, location_artifact)
        location_export = parse_kinder_morgan_location_export(
            location_fetch.body,
            expected_tsp_number=pipeline.tsp_number,
            expected_ferc_cid=pipeline.ferc_cid,
            pipeline_label=pipeline.pipeline_id,
        )
        states = tuple(
            sorted(
                {
                    row.state_abbreviation
                    for row in location_export.rows
                    if row.state_abbreviation
                }
            )
        )
        boundary_url = _census_state_boundaries_url(states)

        county_fetch = http_client.fetch(
            CENSUS_COUNTY_GAZETTEER_URL,
            accept="application/zip",
        )
        county_artifact = ArtifactStore(raw_root).store(
            "census_county_gazetteer",
            county_fetch,
        )
        store_artifact_record(connection, run_id, county_artifact)

        legacy_county_fetch = http_client.fetch(
            CENSUS_LEGACY_COUNTY_GAZETTEER_URL,
            accept="application/zip",
        )
        legacy_county_artifact = ArtifactStore(raw_root).store(
            "census_legacy_county_gazetteer",
            legacy_county_fetch,
        )
        store_artifact_record(connection, run_id, legacy_county_artifact)

        boundary_fetch = http_client.fetch(
            boundary_url,
            accept="application/geo+json,application/json",
        )
        boundary_artifact = ArtifactStore(raw_root).store(
            f"census_{pipeline.slug}_state_boundaries",
            boundary_fetch,
        )
        store_artifact_record(connection, run_id, boundary_artifact)

        county_rows = parse_county_gazetteer(county_fetch.body)
        legacy_county_rows = parse_county_gazetteer(legacy_county_fetch.body)
        boundary_geojson = json.loads(boundary_fetch.text)
        location_count = store_location_export(
            connection,
            location_artifact,
            location_export,
            pipeline_id=pipeline.pipeline_id,
        )
        store_county_references(connection, county_artifact, county_rows)
        store_county_references(
            connection,
            legacy_county_artifact,
            legacy_county_rows,
        )
        geocode_locations_to_counties(
            connection,
            location_artifact,
            county_artifact,
            pipeline_id=pipeline.pipeline_id,
        )
        geocoded_count, ungeocoded_count = geocode_locations_to_counties(
            connection,
            location_artifact,
            legacy_county_artifact,
            pipeline_id=pipeline.pipeline_id,
        )
        boundary_count = store_map_reference_layer(
            connection,
            boundary_artifact,
            layer_code=f"census_{pipeline.slug}_states_20m",
            source_vintage="ACS2025",
            geojson=boundary_geojson,
        )
        connection.execute(
            "UPDATE source_artifacts SET processed_at = ? WHERE artifact_id = ?",
            [pendulum.now("UTC"), page_artifact.artifact_id],
        )
        finish_fetch_run(connection, run_id)
    except Exception as exc:
        finish_fetch_run(connection, run_id, error=exc)
        raise
    finally:
        connection.close()

    return LocationCollectionSummary(
        run_id=run_id,
        location_rows=location_count,
        segment_count=len({row.operator_segment_id for row in location_export.rows}),
        county_count=len(
            {(row.state_abbreviation, row.county_name) for row in location_export.rows}
        ),
        geocoded_rows=geocoded_count,
        ungeocoded_rows=ungeocoded_count,
        state_boundary_features=boundary_count,
        location_raw_path=location_artifact.raw_path,
        county_reference_raw_path=county_artifact.raw_path,
        legacy_county_reference_raw_path=legacy_county_artifact.raw_path,
        state_boundaries_raw_path=boundary_artifact.raw_path,
    )


def collect_tgp_locations(
    *,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> LocationCollectionSummary:
    return collect_kinder_morgan_locations(
        pipeline_id="TGP",
        database_path=database_path,
        raw_root=raw_root,
        client=client,
    )


def collect_kinder_morgan_operational_capacity(
    *,
    pipeline_id: str,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> CapacityCollectionSummary:
    """Archive best-available KM point and segment operational capacity."""
    pipeline = get_pipeline_config(pipeline_id)
    source_prefix = f"km_{pipeline.slug}"
    http_client = client or ReadOnlyHTTPClient(timeout_seconds=240.0)
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = start_fetch_run(
        connection,
        f"{pipeline.slug}_operational_capacity",
        requested_at=pendulum.now("UTC"),
        config={
            "pipeline_id": pipeline.pipeline_id,
            "point_url": pipeline.point_capacity_url,
            "segment_url": pipeline.segment_capacity_url,
            "cycle_selection": "BEST AVAILABLE",
            "point_roles": ["delivery", "receipt"],
        },
    )
    specifications = (
        (
            "point_delivery",
            pipeline.point_capacity_url,
            "point",
            "delivery",
        ),
        (
            "point_receipt",
            pipeline.point_capacity_url,
            "point",
            "receipt",
        ),
        (
            "segment",
            pipeline.segment_capacity_url,
            "segment",
            None,
        ),
    )
    collected: list[tuple[str, StoredArtifact, TgpCapacityExport]] = []
    page_artifacts: list[StoredArtifact] = []
    try:
        for label, url, capacity_kind, point_role in specifications:
            page_fetch = http_client.fetch(url, accept="text/html")
            page_artifact = ArtifactStore(raw_root).store(
                f"{source_prefix}_{label}_capacity_page",
                page_fetch,
            )
            store_artifact_record(connection, run_id, page_artifact)
            page_artifacts.append(page_artifact)
            export_fetch = http_client.post_form(
                url,
                build_capacity_export_form(
                    page_fetch.text,
                    point_role=point_role,
                ),
                accept=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet,application/ms-excel"
                ),
                referer=url,
            )
            export_artifact = ArtifactStore(raw_root).store(
                f"{source_prefix}_{label}_capacity",
                export_fetch,
            )
            store_artifact_record(connection, run_id, export_artifact)
            capacity_export = parse_kinder_morgan_capacity_export(
                export_fetch.body,
                capacity_kind=capacity_kind,
                expected_tsp_number=pipeline.tsp_number,
                pipeline_label=pipeline.pipeline_id,
            )
            store_capacity_export(
                connection,
                export_artifact,
                capacity_export,
                pipeline_id=pipeline.pipeline_id,
            )
            collected.append((label, export_artifact, capacity_export))
            connection.execute(
                """
                UPDATE source_artifacts
                SET processed_at = ?
                WHERE artifact_id = ?
                """,
                [pendulum.now("UTC"), page_artifact.artifact_id],
            )
        finish_fetch_run(connection, run_id)
        stats = connection.execute(
            """
            SELECT
                count(*) FILTER (WHERE available_reconciles = false),
                count(*) FILTER (WHERE facility_id IS NOT NULL),
                count(*) FILTER (WHERE segment_id IS NOT NULL)
            FROM capacity_observations
            WHERE pipeline_id = ?
              AND artifact_id IN (
                SELECT artifact_id
                FROM source_artifacts
                WHERE run_id = ?
            )
            """,
            [pipeline.pipeline_id, run_id],
        ).fetchone()
    except Exception as exc:
        finish_fetch_run(connection, run_id, error=exc)
        raise
    finally:
        connection.close()

    exports = {label: export for label, _, export in collected}
    return CapacityCollectionSummary(
        run_id=run_id,
        gas_days=tuple(sorted({str(export.gas_day) for export in exports.values()})),
        cycles=tuple(sorted({export.cycle for export in exports.values()})),
        point_delivery_rows=len(exports["point_delivery"].rows),
        point_receipt_rows=len(exports["point_receipt"].rows),
        segment_rows=len(exports["segment"].rows),
        available_reconciliation_mismatch_count=int(stats[0]),
        matched_facility_rows=int(stats[1]),
        matched_segment_rows=int(stats[2]),
        raw_paths=tuple(artifact.raw_path for _, artifact, _ in collected),
    )


def collect_tgp_operational_capacity(
    *,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> CapacityCollectionSummary:
    return collect_kinder_morgan_operational_capacity(
        pipeline_id="TGP",
        database_path=database_path,
        raw_root=raw_root,
        client=client,
    )


def collect_kinder_morgan_critical_export(
    *,
    pipeline_id: str,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> ExportCollectionSummary:
    """Archive KM's complete XLSX index export and normalize every notice row."""
    pipeline = get_pipeline_config(pipeline_id)
    source_code = f"km_{pipeline.slug}_critical"
    http_client = client or ReadOnlyHTTPClient(timeout_seconds=180.0)
    connection = connect_database(database_path)
    initialize_database(connection)
    run_id = start_fetch_run(
        connection,
        source_code,
        requested_at=pendulum.now("UTC"),
        config={
            "pipeline_id": pipeline.pipeline_id,
            "url": pipeline.critical_index_url,
            "capture": "xlsx_summary_all",
        },
    )
    try:
        index_fetch = http_client.fetch(
            pipeline.critical_index_url,
            accept="text/html",
        )
        index_artifact = ArtifactStore(raw_root).store(
            source_code,
            index_fetch,
        )
        store_artifact_record(connection, run_id, index_artifact)
        index_page = parse_notice_index(
            index_fetch.text,
            timezone_name=pipeline.timezone,
        )
        store_notice_index_page(
            connection,
            index_artifact,
            index_page,
            pipeline_id=pipeline.pipeline_id,
        )

        form_fields = build_notice_export_form(index_fetch.text)
        export_fetch = http_client.post_form(
            pipeline.critical_index_url,
            form_fields,
            accept=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            referer="https://pipeline2.kindermorgan.com/",
        )
        export_artifact = ArtifactStore(raw_root).store(
            source_code,
            export_fetch,
        )
        store_artifact_record(connection, run_id, export_artifact)
        expected_count, expected_range = _export_count_expectation(index_page)
        export = parse_notice_index_export(
            export_fetch.body,
            expected_row_count=expected_count,
            expected_row_count_range=expected_range,
            timezone_name=pipeline.timezone,
        )
        store_notice_index_export(
            connection,
            export_artifact,
            export,
            pipeline_id=pipeline.pipeline_id,
            index_advertised_row_count=index_page.total_row_count,
        )
        finish_fetch_run(connection, run_id)
    except Exception as exc:
        finish_fetch_run(connection, run_id, error=exc)
        raise
    finally:
        connection.close()

    return ExportCollectionSummary(
        run_id=run_id,
        index_artifact_id=index_artifact.artifact_id,
        export_artifact_id=export_artifact.artifact_id,
        index_footer_row_count=index_page.total_row_count,
        export_footer_row_count=export.source_footer_row_count,
        exported_row_count=len(export.rows),
        newest_notice_id=export.rows[0].notice_id,
        oldest_notice_id=export.rows[-1].notice_id,
        index_raw_path=index_artifact.raw_path,
        export_raw_path=export_artifact.raw_path,
    )


def collect_tgp_critical_export(
    *,
    database_path: str | Path,
    raw_root: str | Path,
    client: ReadOnlyHTTPClient | None = None,
) -> ExportCollectionSummary:
    return collect_kinder_morgan_critical_export(
        pipeline_id="TGP",
        database_path=database_path,
        raw_root=raw_root,
        client=client,
    )


def _export_count_expectation(
    page: object,
) -> tuple[int | None, tuple[int, int] | None]:
    page_count = page.page_count
    page_size = page.page_size
    reported_count = page.total_row_count
    minimum = (page_count - 1) * page_size + 1
    maximum = page_count * page_size
    if minimum <= reported_count <= maximum:
        return reported_count, None
    return None, (minimum, maximum)


def collect_kinder_morgan_notice_details(
    *,
    pipeline_id: str,
    database_path: str | Path,
    raw_root: str | Path,
    limit: int = 3,
    revision_check_limit: int = 3,
    notice_type: str | None = None,
    minimum_interval_seconds: float = 2.0,
    client: ReadOnlyHTTPClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> DetailCollectionSummary:
    if limit <= 0:
        raise ValueError("detail collection limit must be positive")
    if revision_check_limit < 0:
        raise ValueError("revision check limit cannot be negative")
    if minimum_interval_seconds < 0:
        raise ValueError("minimum interval cannot be negative")
    pipeline = get_pipeline_config(pipeline_id)
    source_code = f"km_{pipeline.slug}_notice_detail"
    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        new_notice_ids = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT current.notice_id
                FROM current_notice_index AS current
                LEFT JOIN (
                    SELECT
                        json_extract_string(config, '$.notice_id') AS notice_id,
                        count(*) AS attempt_count
                    FROM fetch_runs
                    WHERE source_code = ?
                    GROUP BY 1
                ) AS attempts
                  ON attempts.notice_id = current.notice_id
                WHERE current.pipeline_id = ?
                  AND (
                      ? IS NULL
                      OR upper(current.notice_type_primary) = upper(?)
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM notice_versions AS version
                      WHERE version.pipeline_id = current.pipeline_id
                        AND version.notice_id = current.notice_id
                  )
                ORDER BY
                    coalesce(attempts.attempt_count, 0),
                    current.posted_at DESC,
                    current.notice_id DESC
                LIMIT ?
                """,
                [
                    source_code,
                    pipeline.pipeline_id,
                    notice_type,
                    notice_type,
                    limit,
                ],
            ).fetchall()
        )
        rechecked_notice_ids = (
            tuple(
                row[0]
                for row in connection.execute(
                    """
                WITH last_check AS (
                    SELECT
                        pipeline_id,
                        notice_id,
                        max(observed_at) AS last_observed_at
                    FROM notice_version_observations
                    GROUP BY pipeline_id, notice_id
                )
                SELECT current.notice_id
                FROM current_notice_index AS current
                JOIN last_check
                  ON last_check.pipeline_id = current.pipeline_id
                 AND last_check.notice_id = current.notice_id
                WHERE current.pipeline_id = ?
                  AND (
                      ? IS NULL
                      OR upper(current.notice_type_primary) = upper(?)
                  )
                  AND (
                      current.effective_end IS NULL
                      OR current.effective_end >= current_timestamp - INTERVAL '1 day'
                      OR current.posted_at >= current_timestamp - INTERVAL '30 days'
                  )
                ORDER BY
                    last_check.last_observed_at,
                    current.posted_at DESC,
                    current.notice_id DESC
                LIMIT ?
                """,
                    [
                        pipeline.pipeline_id,
                        notice_type,
                        notice_type,
                        revision_check_limit,
                    ],
                ).fetchall()
            )
            if revision_check_limit
            else ()
        )
    finally:
        connection.close()

    notice_ids = (*new_notice_ids, *rechecked_notice_ids)
    rechecked_set = set(rechecked_notice_ids)
    http_client = client or ReadOnlyHTTPClient(timeout_seconds=180.0)
    completed_ids: list[str] = []
    failed_ids: list[str] = []
    revised_ids: list[str] = []
    unchanged_ids: list[str] = []
    for position, notice_id in enumerate(notice_ids):
        if position:
            sleep(minimum_interval_seconds)
        detail_url = pipeline.notice_detail_url(notice_id)
        connection = connect_database(database_path)
        initialize_database(connection)
        try:
            run_id = start_fetch_run(
                connection,
                source_code,
                requested_at=pendulum.now("UTC"),
                config={
                    "url": detail_url,
                    "pipeline_id": pipeline.pipeline_id,
                    "notice_id": notice_id,
                    "collection_reason": (
                        "revision_check" if notice_id in rechecked_set else "new_detail"
                    ),
                },
            )
        finally:
            connection.close()

        try:
            fetch = http_client.fetch(detail_url, accept="text/html")
            artifact = ArtifactStore(raw_root).store(
                source_code,
                fetch,
            )
            connection = connect_database(database_path)
            initialize_database(connection)
            try:
                store_artifact_record(connection, run_id, artifact)
            finally:
                connection.close()

            notice = parse_notice_detail(
                fetch.text,
                timezone_name=pipeline.timezone,
            )
            if notice.notice_id != notice_id:
                raise ValueError(
                    f"detail response {notice.notice_id} does not match {notice_id}"
                )
            if notice.tsp_number != pipeline.tsp_number:
                raise ValueError(
                    f"detail response TSP {notice.tsp_number} does not identify "
                    f"{pipeline.pipeline_id} ({pipeline.tsp_number})"
                )
            connection = connect_database(database_path)
            initialize_database(connection)
            try:
                version_result = store_notice_version(
                    connection,
                    artifact,
                    notice,
                    pipeline_id=pipeline.pipeline_id,
                )
                if pipeline.supports_outage_impact_report:
                    impact_rows = parse_tgp_outage_impact_report(fetch.text)
                    store_outage_impact_observations(
                        connection,
                        artifact,
                        notice.notice_id,
                        impact_rows,
                        pipeline_id=pipeline.pipeline_id,
                    )
                finish_fetch_run(connection, run_id)
            finally:
                connection.close()
            completed_ids.append(notice_id)
            if notice_id in rechecked_set:
                if version_result.is_revision_observation:
                    revised_ids.append(notice_id)
                else:
                    unchanged_ids.append(notice_id)
        except Exception as exc:  # noqa: BLE001 - isolate each notice fetch
            connection = connect_database(database_path)
            initialize_database(connection)
            try:
                finish_fetch_run(connection, run_id, error=exc)
            finally:
                connection.close()
            failed_ids.append(notice_id)

    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        remaining = connection.execute(
            """
            SELECT count(*)
            FROM current_notice_index AS current
            WHERE current.pipeline_id = ?
              AND (
                  ? IS NULL
                  OR upper(current.notice_type_primary) = upper(?)
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM notice_versions AS version
                  WHERE version.pipeline_id = current.pipeline_id
                    AND version.notice_id = current.notice_id
              )
            """,
            [pipeline.pipeline_id, notice_type, notice_type],
        ).fetchone()[0]
    finally:
        connection.close()
    return DetailCollectionSummary(
        notice_type=notice_type,
        requested=len(notice_ids),
        completed=len(completed_ids),
        failed=len(failed_ids),
        remaining=remaining,
        new_detail_notice_ids=tuple(new_notice_ids),
        rechecked_notice_ids=tuple(rechecked_notice_ids),
        revised_notice_ids=tuple(revised_ids),
        unchanged_notice_ids=tuple(unchanged_ids),
        completed_notice_ids=tuple(completed_ids),
        failed_notice_ids=tuple(failed_ids),
    )


def collect_tgp_notice_details(
    *,
    database_path: str | Path,
    raw_root: str | Path,
    limit: int = 3,
    revision_check_limit: int = 3,
    notice_type: str | None = None,
    minimum_interval_seconds: float = 2.0,
    client: ReadOnlyHTTPClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> DetailCollectionSummary:
    return collect_kinder_morgan_notice_details(
        pipeline_id="TGP",
        database_path=database_path,
        raw_root=raw_root,
        limit=limit,
        revision_check_limit=revision_check_limit,
        notice_type=notice_type,
        minimum_interval_seconds=minimum_interval_seconds,
        client=client,
        sleep=sleep,
    )


def reprocess_tgp_notice_details(
    database_path: str | Path,
) -> DetailReprocessSummary:
    """Replay archived TGP detail HTML into notice and outage-impact tables."""
    connection = connect_database(database_path)
    initialize_database(connection)
    artifact_rows = connection.execute(
        """
        SELECT
            artifact_id,
            source_code,
            canonical_url,
            content_sha256,
            mime_type,
            http_status,
            CAST(requested_at AS VARCHAR),
            CAST(received_at AS VARCHAR),
            CAST(recorded_at AS VARCHAR),
            raw_path
        FROM source_artifacts
        WHERE source_code = 'km_tgp_notice_detail'
        ORDER BY received_at
        """
    ).fetchall()
    reprocessed = 0
    report_artifacts = 0
    impact_rows_reprocessed = 0
    failed_artifact_ids: list[str] = []
    try:
        for row in artifact_rows:
            raw_path = Path(row[9])
            artifact = StoredArtifact(
                artifact_id=row[0],
                source_code=row[1],
                canonical_url=row[2],
                content_sha256=row[3],
                mime_type=row[4],
                http_status=row[5],
                requested_at=pendulum.parse(row[6]),
                received_at=pendulum.parse(row[7]),
                recorded_at=pendulum.parse(row[8]),
                raw_path=raw_path.as_posix(),
                size_bytes=raw_path.stat().st_size,
                etag=None,
                last_modified=None,
                content_disposition=None,
            )
            try:
                html = raw_path.read_text(encoding="utf-8-sig")
                notice = parse_notice_detail(html)
                store_notice_version(connection, artifact, notice)
                impact_rows = parse_tgp_outage_impact_report(html)
                if impact_rows:
                    report_artifacts += 1
                    impact_rows_reprocessed += store_outage_impact_observations(
                        connection,
                        artifact,
                        notice.notice_id,
                        impact_rows,
                    )
                reprocessed += 1
            except Exception:  # noqa: BLE001 - report bad legacy artifacts by ID
                failed_artifact_ids.append(artifact.artifact_id)
    finally:
        connection.close()
    return DetailReprocessSummary(
        artifacts_found=len(artifact_rows),
        artifacts_reprocessed=reprocessed,
        outage_report_artifacts=report_artifacts,
        impact_rows_reprocessed=impact_rows_reprocessed,
        failed_artifact_ids=tuple(failed_artifact_ids),
    )


def reprocess_tgp_critical_indexes(
    database_path: str | Path,
) -> ReprocessSummary:
    """Rebuild missing normalized pages or exports from immutable artifacts."""
    connection = connect_database(database_path)
    initialize_database(connection)
    artifact_rows = connection.execute(
        """
        SELECT
            artifact_id,
            source_code,
            canonical_url,
            content_sha256,
            mime_type,
            http_status,
            CAST(requested_at AS VARCHAR),
            CAST(received_at AS VARCHAR),
            CAST(recorded_at AS VARCHAR),
            raw_path,
            run_id
        FROM source_artifacts AS artifact
        WHERE source_code = 'km_tgp_critical'
          AND NOT EXISTS (
              SELECT 1
              FROM notice_index_pages AS page
              WHERE page.artifact_id = artifact.artifact_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM notice_index_exports AS export
              WHERE export.artifact_id = artifact.artifact_id
          )
        ORDER BY received_at
        """
    ).fetchall()
    reprocessed = 0
    rows_reprocessed = 0
    try:
        for row in artifact_rows:
            raw_path = Path(row[9])
            artifact = StoredArtifact(
                artifact_id=row[0],
                source_code=row[1],
                canonical_url=row[2],
                content_sha256=row[3],
                mime_type=row[4],
                http_status=row[5],
                requested_at=pendulum.parse(row[6]),
                received_at=pendulum.parse(row[7]),
                recorded_at=pendulum.parse(row[8]),
                raw_path=raw_path.as_posix(),
                size_bytes=raw_path.stat().st_size,
                etag=None,
                last_modified=None,
                content_disposition=None,
            )
            if raw_path.suffix.lower() == ".xlsx":
                advertised_row = connection.execute(
                    """
                    SELECT max(page.total_row_count)
                    FROM notice_index_pages AS page
                    JOIN source_artifacts AS page_artifact
                      ON page_artifact.artifact_id = page.artifact_id
                    WHERE page_artifact.run_id = ?
                    """,
                    [row[10]],
                ).fetchone()[0]
                page_metadata = connection.execute(
                    """
                    SELECT page.page_count, page.page_size
                    FROM notice_index_pages AS page
                    JOIN source_artifacts AS page_artifact
                      ON page_artifact.artifact_id = page.artifact_id
                    WHERE page_artifact.run_id = ?
                    ORDER BY page.observed_at DESC
                    LIMIT 1
                    """,
                    [row[10]],
                ).fetchone()
                if page_metadata is None:
                    expected_count = advertised_row
                    expected_range = None
                else:
                    minimum = (page_metadata[0] - 1) * page_metadata[1] + 1
                    maximum = page_metadata[0] * page_metadata[1]
                    if (
                        advertised_row is not None
                        and minimum <= advertised_row <= maximum
                    ):
                        expected_count = advertised_row
                        expected_range = None
                    else:
                        expected_count = None
                        expected_range = (minimum, maximum)
                export = parse_notice_index_export(
                    raw_path.read_bytes(),
                    expected_row_count=expected_count,
                    expected_row_count_range=expected_range,
                )
                store_notice_index_export(
                    connection,
                    artifact,
                    export,
                    index_advertised_row_count=export.total_row_count,
                )
                normalized_row_count = len(export.rows)
            else:
                page = parse_notice_index(raw_path.read_text(encoding="utf-8-sig"))
                store_notice_index_page(connection, artifact, page)
                normalized_row_count = len(page.rows)
            reprocessed += 1
            rows_reprocessed += normalized_row_count
    finally:
        connection.close()

    return ReprocessSummary(
        artifacts_found=len(artifact_rows),
        artifacts_reprocessed=reprocessed,
        rows_reprocessed=rows_reprocessed,
    )
