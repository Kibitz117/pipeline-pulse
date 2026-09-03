from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pendulum

from .artifacts import StoredArtifact
from .pipelines import KINDER_MORGAN_PIPELINES
from .sources.census import CountyReference, normalize_county_name
from .sources.eia_storage import EiaStorageRelease
from .sources.fred_spot import HenryHubSpotObservation
from .sources.kinder_morgan import (
    KinderMorganNotice,
    KinderMorganNoticeIndexExport,
    KinderMorganNoticeIndexPage,
)
from .sources.kinder_morgan_capacity import TgpCapacityExport
from .sources.kinder_morgan_locations import TgpLocationExport
from .sources.kinder_morgan_tables import TgpOutageImpactRow
from .sources.nws_weather import NwsHourlyForecast, WeatherAnchor
from .sources.yahoo_futures import FrontMonthFuturesQuote


@dataclass(frozen=True)
class NoticeVersionStoreResult:
    version_sha256: str
    prior_version_sha256: str | None
    is_new_version: bool
    is_revision_observation: bool


def _notice_timestamp(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, pendulum.DateTime):
        timestamp = value
    else:
        timestamp = pendulum.instance(value)  # type: ignore[arg-type]
    return timestamp.in_timezone("UTC").to_iso8601_string()


def notice_semantic_sha256(notice: KinderMorganNotice) -> str:
    """Hash parsed notice meaning, excluding volatile HTML wrapper fields."""
    payload = {
        "critical": notice.critical,
        "notice_type_primary": notice.notice_type_primary,
        "notice_type_secondary": notice.notice_type_secondary,
        "status_description": notice.status_description,
        "prior_notice_id": notice.prior_notice_id,
        "subject": notice.subject,
        "notice_text": notice.notice_text,
        "posted_at": _notice_timestamp(notice.posted_at),
        "effective_start": _notice_timestamp(notice.effective_start),
        "effective_end": _notice_timestamp(notice.effective_end),
        "required_response": notice.required_response,
        "response_at": _notice_timestamp(notice.response_at),
    }
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def connect_database(path: str | Path) -> duckdb.DuckDBPyConnection:
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(database_path))


def initialize_database(connection: duckdb.DuckDBPyConnection) -> None:
    schema_path = Path(__file__).parents[2] / "sql" / "schema.sql"
    connection.execute(schema_path.read_text(encoding="utf-8"))
    connection.execute(
        """
        ALTER TABLE notice_index_exports
        ADD COLUMN IF NOT EXISTS source_footer_row_count INTEGER
        """
    )
    connection.execute(
        """
        ALTER TABLE notice_index_exports
        ADD COLUMN IF NOT EXISTS index_advertised_row_count INTEGER
        """
    )
    for column_definition in (
        "coordinate_method VARCHAR",
        "coordinate_precision VARCHAR",
        "coordinate_artifact_id VARCHAR",
        "receipt_zone VARCHAR",
        "delivery_zone VARCHAR",
    ):
        connection.execute(
            f"ALTER TABLE facilities ADD COLUMN IF NOT EXISTS {column_definition}"
        )
    connection.execute(
        """
        ALTER TABLE location_observations
        ADD COLUMN IF NOT EXISTS normalized_county_name VARCHAR
        """
    )
    for column_definition in (
        "source_column_count INTEGER",
        "schema_sha256 VARCHAR",
        "parser_version VARCHAR",
    ):
        connection.execute(
            "ALTER TABLE location_exports ADD COLUMN IF NOT EXISTS " + column_definition
        )
    for column_definition in (
        "capacity_kind VARCHAR",
        "point_role VARCHAR",
        "source_row_position INTEGER",
        "operator_location_id VARCHAR",
        "operator_segment_id VARCHAR",
        "location_name VARCHAR",
        "zone VARCHAR",
        "effective_at TIMESTAMPTZ",
        "design_capacity_dth_per_day BIGINT",
        "interruptible_scheduled BOOLEAN",
        "all_quantity_available BOOLEAN",
        "quantity_reason VARCHAR",
        "available_reconciles BOOLEAN",
        "source_posted_at TIMESTAMPTZ",
    ):
        connection.execute(
            "ALTER TABLE capacity_observations ADD COLUMN IF NOT EXISTS "
            + column_definition
        )
    for column_definition in (
        "price_mapping_status VARCHAR DEFAULT 'unresolved'",
        (
            "price_mapping_reason VARCHAR DEFAULT "
            "'No exact regional price location or contract mapping is loaded.'"
        ),
        "benchmark_reference_url VARCHAR",
    ):
        connection.execute(
            "ALTER TABLE tgp_transport_impact_assessments "
            "ADD COLUMN IF NOT EXISTS " + column_definition
        )
    for column_definition in (
        "provider VARCHAR",
        "observation_type VARCHAR",
        "source_published_at TIMESTAMPTZ",
    ):
        connection.execute(
            "ALTER TABLE market_observations ADD COLUMN IF NOT EXISTS "
            + column_definition
        )
    connection.execute(
        """
        UPDATE market_observations
        SET provider = coalesce(provider, 'U.S. EIA'),
            observation_type = coalesce(observation_type, 'storage'),
            source_published_at = coalesce(source_published_at, available_at)
        WHERE series_code LIKE 'EIA_WNGSR:%'
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_capacity_gas_day
        ON capacity_observations(pipeline_id, gas_day, cycle)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_capacity_segment
        ON capacity_observations(
            pipeline_id, operator_segment_id, gas_day, effective_at
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_capacity_location
        ON capacity_observations(
            pipeline_id, operator_location_id, gas_day, effective_at
        )
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW latest_tgp_capacity AS
        SELECT observation.*
        FROM capacity_observations AS observation
        WHERE observation.pipeline_id = 'TGP'
        QUALIFY row_number() OVER (
            PARTITION BY
                capacity_kind,
                coalesce(point_role, ''),
                coalesce(operator_location_id, ''),
                operator_segment_id,
                location_name,
                flow_direction,
                gas_day
            ORDER BY effective_at DESC, source_posted_at DESC, observed_at DESC,
                     artifact_id DESC
        ) = 1
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW tgp_capacity_capture_summary AS
        SELECT
            export.artifact_id,
            export.capacity_kind,
            export.point_role,
            export.gas_day,
            export.effective_at,
            export.cycle,
            export.source_posted_at,
            export.source_footer_row_count,
            export.parsed_row_count,
            count(*) FILTER (WHERE observation.available_reconciles = false)
                AS available_reconciliation_mismatch_count,
            count(*) FILTER (WHERE observation.facility_id IS NOT NULL)
                AS matched_facility_rows,
            count(*) FILTER (WHERE observation.segment_id IS NOT NULL)
                AS matched_segment_rows,
            export.observed_at
        FROM capacity_exports AS export
        LEFT JOIN capacity_observations AS observation
          ON observation.artifact_id = export.artifact_id
        WHERE export.pipeline_id = 'TGP'
        GROUP BY ALL
        """
    )
    operators = {
        (
            pipeline.operator_id,
            pipeline.operator_name,
            pipeline.parent_company,
            pipeline.ticker,
        )
        for pipeline in KINDER_MORGAN_PIPELINES.values()
    }
    connection.executemany(
        """
        INSERT INTO operators(
            operator_id, operator_name, parent_company, ticker, source_url
        ) VALUES (?, ?, ?, ?, 'https://pipeportal.kindermorgan.com/')
        ON CONFLICT (operator_id) DO NOTHING
        """,
        list(operators),
    )
    connection.executemany(
        """
        INSERT INTO pipeline_systems(
            pipeline_id, operator_id, pipeline_name, source_code,
            tsp_number, ferc_cid, timezone
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (pipeline_id) DO NOTHING
        """,
        [
            [
                pipeline.pipeline_id,
                pipeline.operator_id,
                pipeline.pipeline_name,
                pipeline.portal_code,
                pipeline.tsp_number,
                pipeline.ferc_cid,
                pipeline.timezone,
            ]
            for pipeline in KINDER_MORGAN_PIPELINES.values()
        ],
    )


def start_fetch_run(
    connection: duckdb.DuckDBPyConnection,
    source_code: str,
    *,
    requested_at: pendulum.DateTime,
    config: dict[str, object] | None = None,
) -> str:
    run_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO fetch_runs(run_id, source_code, requested_at, status, config)
        VALUES (?, ?, ?, 'running', ?)
        """,
        [run_id, source_code, requested_at, json.dumps(config or {}, sort_keys=True)],
    )
    return run_id


def finish_fetch_run(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    *,
    error: Exception | None = None,
) -> None:
    connection.execute(
        """
        UPDATE fetch_runs
        SET completed_at = ?, status = ?, error = ?
        WHERE run_id = ?
        """,
        [
            pendulum.now("UTC"),
            "failed" if error else "completed",
            json.dumps(
                {"type": type(error).__name__, "message": str(error)},
                sort_keys=True,
            )
            if error
            else None,
            run_id,
        ],
    )


def store_artifact_record(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    artifact: StoredArtifact,
) -> None:
    connection.execute(
        """
        INSERT INTO source_artifacts(
            artifact_id, run_id, source_code, canonical_url, content_sha256,
            mime_type, http_status, requested_at, received_at, recorded_at,
            raw_path, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (artifact_id) DO NOTHING
        """,
        [
            artifact.artifact_id,
            run_id,
            artifact.source_code,
            artifact.canonical_url,
            artifact.content_sha256,
            artifact.mime_type,
            artifact.http_status,
            artifact.requested_at,
            artifact.received_at,
            artifact.recorded_at,
            artifact.raw_path,
            json.dumps(
                {
                    "size_bytes": artifact.size_bytes,
                    "etag": artifact.etag,
                    "last_modified": artifact.last_modified,
                    "content_disposition": artifact.content_disposition,
                },
                sort_keys=True,
            ),
        ],
    )


def store_notice_index_page(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    page: KinderMorganNoticeIndexPage,
    *,
    pipeline_id: str = "TGP",
) -> None:
    connection.execute(
        """
        INSERT INTO notice_index_pages(
            artifact_id, pipeline_id, page_index, page_size, page_count,
            total_row_count, parsed_row_count, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (artifact_id) DO NOTHING
        """,
        [
            artifact.artifact_id,
            pipeline_id,
            page.page_index,
            page.page_size,
            page.page_count,
            page.total_row_count,
            len(page.rows),
            artifact.received_at,
        ],
    )
    parameters: Iterable[list[object]] = (
        [
            artifact.artifact_id,
            pipeline_id,
            page.page_index,
            row.row_position,
            row.notice_id,
            row.notice_type_primary,
            row.notice_type_secondary,
            row.subject,
            row.posted_at,
            row.effective_start,
            row.effective_end,
            artifact.received_at,
        ]
        for row in page.rows
    )
    connection.executemany(
        """
        INSERT INTO notice_index_observations(
            artifact_id, pipeline_id, page_index, row_position, notice_id,
            notice_type_primary, notice_type_secondary, subject, posted_at,
            effective_start, effective_end, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (artifact_id, notice_id) DO NOTHING
        """,
        parameters,
    )
    _mark_artifact_processed(connection, artifact.artifact_id)


def store_notice_version(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    notice: KinderMorganNotice,
    *,
    pipeline_id: str = "TGP",
) -> NoticeVersionStoreResult:
    existing_observation = connection.execute(
        """
        SELECT version_sha256
        FROM notice_version_observations
        WHERE artifact_id = ?
        """,
        [artifact.artifact_id],
    ).fetchone()
    if existing_observation is not None:
        # Reprocessing a legacy artifact enriches newly introduced normalized
        # fields without manufacturing a new observation or revision.
        existing_version_sha256 = str(existing_observation[0])
        connection.execute(
            """
            UPDATE notice_versions
            SET required_response = ?, response_at = ?
            WHERE pipeline_id = ?
              AND notice_id = ?
              AND version_sha256 = ?
            """,
            [
                notice.required_response,
                notice.response_at,
                pipeline_id,
                notice.notice_id,
                existing_version_sha256,
            ],
        )
        _mark_artifact_processed(connection, artifact.artifact_id)
        return NoticeVersionStoreResult(
            version_sha256=existing_version_sha256,
            prior_version_sha256=existing_version_sha256,
            is_new_version=False,
            is_revision_observation=False,
        )

    prior_row = connection.execute(
        """
        SELECT observation.version_sha256
        FROM notice_version_observations AS observation
        WHERE observation.pipeline_id = ?
          AND observation.notice_id = ?
        ORDER BY observation.observed_at DESC, observation.artifact_id DESC
        LIMIT 1
        """,
        [pipeline_id, notice.notice_id],
    ).fetchone()
    prior_version_sha256 = str(prior_row[0]) if prior_row else None

    # Match legacy rows by their parsed fields. Older databases used the raw
    # HTML hash as version_sha256; selecting the semantic match avoids creating
    # a false revision the first time a known notice is rechecked.
    matching_row = connection.execute(
        """
        SELECT version_sha256
        FROM notice_versions
        WHERE pipeline_id = ?
          AND notice_id = ?
          AND critical IS NOT DISTINCT FROM ?
          AND notice_type_primary IS NOT DISTINCT FROM ?
          AND notice_type_secondary IS NOT DISTINCT FROM ?
          AND status_description IS NOT DISTINCT FROM ?
          AND prior_notice_id IS NOT DISTINCT FROM ?
          AND subject IS NOT DISTINCT FROM ?
          AND notice_text IS NOT DISTINCT FROM ?
          AND posted_at IS NOT DISTINCT FROM ?
          AND effective_start IS NOT DISTINCT FROM ?
          AND effective_end IS NOT DISTINCT FROM ?
          AND required_response IS NOT DISTINCT FROM ?
          AND response_at IS NOT DISTINCT FROM ?
        ORDER BY first_seen_at, version_sha256
        LIMIT 1
        """,
        [
            pipeline_id,
            notice.notice_id,
            notice.critical,
            notice.notice_type_primary,
            notice.notice_type_secondary,
            notice.status_description,
            notice.prior_notice_id,
            notice.subject,
            notice.notice_text,
            notice.posted_at,
            notice.effective_start,
            notice.effective_end,
            notice.required_response,
            notice.response_at,
        ],
    ).fetchone()
    is_new_version = matching_row is None
    version_sha256 = (
        notice_semantic_sha256(notice) if matching_row is None else str(matching_row[0])
    )
    connection.execute(
        """
        INSERT INTO notice_versions(
            pipeline_id, notice_id, version_sha256, artifact_id, critical,
            notice_type_primary, notice_type_secondary, status_description,
            prior_notice_id, subject, notice_text, posted_at, effective_start,
            effective_end, required_response, response_at, first_seen_at,
            last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (pipeline_id, notice_id, version_sha256)
        DO UPDATE SET
            first_seen_at = least(notice_versions.first_seen_at, excluded.first_seen_at),
            last_seen_at = greatest(notice_versions.last_seen_at, excluded.last_seen_at)
        """,
        [
            pipeline_id,
            notice.notice_id,
            version_sha256,
            artifact.artifact_id,
            notice.critical,
            notice.notice_type_primary,
            notice.notice_type_secondary,
            notice.status_description,
            notice.prior_notice_id,
            notice.subject,
            notice.notice_text,
            notice.posted_at,
            notice.effective_start,
            notice.effective_end,
            notice.required_response,
            notice.response_at,
            artifact.received_at,
            artifact.received_at,
        ],
    )
    connection.execute(
        """
        INSERT INTO notice_version_observations(
            artifact_id, pipeline_id, notice_id, version_sha256, observed_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (artifact_id) DO NOTHING
        """,
        [
            artifact.artifact_id,
            pipeline_id,
            notice.notice_id,
            version_sha256,
            artifact.received_at,
        ],
    )
    _mark_artifact_processed(connection, artifact.artifact_id)
    return NoticeVersionStoreResult(
        version_sha256=version_sha256,
        prior_version_sha256=prior_version_sha256,
        is_new_version=is_new_version,
        is_revision_observation=(
            prior_version_sha256 is not None and prior_version_sha256 != version_sha256
        ),
    )


def store_notice_index_export(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    export: KinderMorganNoticeIndexExport,
    *,
    pipeline_id: str = "TGP",
    index_advertised_row_count: int | None = None,
) -> None:
    advertised_row_count = index_advertised_row_count or export.total_row_count
    connection.execute(
        """
        INSERT INTO notice_index_exports(
            artifact_id, pipeline_id, export_format, total_row_count,
            source_footer_row_count, index_advertised_row_count,
            parsed_row_count, observed_at
        ) VALUES (?, ?, 'xlsx_summary_all', ?, ?, ?, ?, ?)
        ON CONFLICT (artifact_id) DO NOTHING
        """,
        [
            artifact.artifact_id,
            pipeline_id,
            export.total_row_count,
            export.source_footer_row_count,
            advertised_row_count,
            len(export.rows),
            artifact.received_at,
        ],
    )
    parameters: Iterable[list[object]] = (
        [
            artifact.artifact_id,
            pipeline_id,
            0,
            row.row_position,
            row.notice_id,
            row.notice_type_primary,
            row.notice_type_secondary,
            row.subject,
            row.posted_at,
            row.effective_start,
            row.effective_end,
            artifact.received_at,
        ]
        for row in export.rows
    )
    connection.executemany(
        """
        INSERT INTO notice_index_observations(
            artifact_id, pipeline_id, page_index, row_position, notice_id,
            notice_type_primary, notice_type_secondary, subject, posted_at,
            effective_start, effective_end, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (artifact_id, notice_id) DO NOTHING
        """,
        parameters,
    )
    _mark_artifact_processed(connection, artifact.artifact_id)


def store_outage_impact_observations(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    notice_id: str,
    rows: Iterable[TgpOutageImpactRow],
    *,
    pipeline_id: str = "TGP",
) -> int:
    materialized_rows = tuple(rows)
    if not materialized_rows:
        return 0
    parameters: Iterable[list[object]] = (
        [
            artifact.artifact_id,
            pipeline_id,
            notice_id,
            row.report_kind,
            row.report_label,
            row.report_updated_on,
            row.period_label,
            row.period_start,
            row.period_end,
            row.station_label,
            row.operator_segment_id,
            row.flow_direction,
            row.nominal_capacity_text,
            row.capacity_text,
            row.nominal_capacity_dth_per_day,
            row.operating_capacity_dth_per_day,
            row.reported_reduction_dth_per_day,
            row.calculated_reduction_dth_per_day,
            row.reduction_reconciles,
            row.outage_description,
            row.source_table_index,
            row.source_row_index,
            artifact.received_at,
        ]
        for row in materialized_rows
    )
    connection.executemany(
        """
        INSERT INTO outage_impact_observations(
            artifact_id, pipeline_id, notice_id, report_kind, report_label,
            report_updated_on, period_label, period_start, period_end,
            station_label, operator_segment_id, flow_direction,
            nominal_capacity_text, capacity_text,
            nominal_capacity_dth_per_day, operating_capacity_dth_per_day,
            reported_reduction_dth_per_day, calculated_reduction_dth_per_day,
            reduction_reconciles, outage_description, source_table_index,
            source_row_index, observed_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT (
            artifact_id, source_table_index, source_row_index,
            period_start, period_end
        ) DO NOTHING
        """,
        parameters,
    )
    return len(materialized_rows)


def store_location_export(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    export: TgpLocationExport,
    *,
    pipeline_id: str = "TGP",
) -> int:
    connection.execute(
        """
        INSERT INTO location_exports(
            artifact_id, pipeline_id, tsp_number, tsp_name, tsp_ferc_cid,
            source_as_of, comments, source_column_count, schema_sha256,
            parser_version, row_count, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (artifact_id) DO NOTHING
        """,
        [
            artifact.artifact_id,
            pipeline_id,
            export.tsp_number,
            export.tsp_name,
            export.tsp_ferc_cid,
            export.source_as_of,
            export.comments,
            export.source_column_count,
            export.schema_sha256,
            "tgp_locations_v1",
            len(export.rows),
            artifact.received_at,
        ],
    )
    parameters = [
        [
            artifact.artifact_id,
            pipeline_id,
            row.row_position,
            row.operator_location_id,
            row.location_name,
            row.flow_role,
            row.county_name,
            normalize_county_name(row.county_name),
            row.state_abbreviation,
            row.location_type,
            row.receipt_zone,
            row.delivery_zone,
            row.operator_segment_id,
            row.nomination_indicator,
            row.status_indicator,
            row.effective_date,
            row.inactive_date,
            row.interconnect_indicator,
            row.counterparty_name,
            row.counterparty_id,
            row.counterparty_property_id,
            row.counterparty_ferc_indicator,
            row.counterparty_ferc_cid,
            row.counterparty_location_id,
            row.counterparty_location_name,
            row.counterparty_location_id_2,
            row.counterparty_location_name_2,
            row.source_updated_at,
            artifact.received_at,
        ]
        for row in export.rows
    ]
    connection.executemany(
        """
        INSERT INTO location_observations(
            artifact_id, pipeline_id, row_position, operator_location_id,
            location_name, flow_role, county_name, normalized_county_name,
            state_abbreviation,
            location_type, receipt_zone, delivery_zone, operator_segment_id,
            nomination_indicator, status_indicator, effective_date,
            inactive_date, interconnect_indicator, counterparty_name,
            counterparty_id, counterparty_property_id,
            counterparty_ferc_indicator, counterparty_ferc_cid,
            counterparty_location_id, counterparty_location_name,
            counterparty_location_id_2, counterparty_location_name_2,
            source_updated_at, observed_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT (artifact_id, operator_location_id) DO NOTHING
        """,
        parameters,
    )
    facility_parameters = [
        [
            f"{pipeline_id}:{row.operator_location_id}",
            pipeline_id,
            row.operator_location_id,
            row.location_name,
            row.location_type,
            row.receipt_zone if row.receipt_zone == row.delivery_zone else None,
            row.state_abbreviation,
            row.county_name,
            {"R": "receipt", "D": "delivery", "B": "bidirectional"}.get(
                row.flow_role,
                row.flow_role,
            ),
            row.receipt_zone,
            row.delivery_zone,
            row.effective_date,
            row.inactive_date,
            artifact.artifact_id,
        ]
        for row in export.rows
    ]
    connection.executemany(
        """
        INSERT INTO facilities(
            facility_id, pipeline_id, operator_location_id, facility_name,
            facility_type, zone, state, county, receipt_delivery_role,
            receipt_zone, delivery_zone, valid_from, valid_to, artifact_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (facility_id) DO UPDATE SET
            facility_name = excluded.facility_name,
            facility_type = excluded.facility_type,
            zone = excluded.zone,
            state = excluded.state,
            county = excluded.county,
            receipt_delivery_role = excluded.receipt_delivery_role,
            receipt_zone = excluded.receipt_zone,
            delivery_zone = excluded.delivery_zone,
            valid_from = excluded.valid_from,
            valid_to = excluded.valid_to,
            artifact_id = excluded.artifact_id
        """,
        facility_parameters,
    )
    segment_ids = sorted(
        {row.operator_segment_id for row in export.rows if row.operator_segment_id}
    )
    connection.executemany(
        """
        INSERT INTO segments(
            segment_id, pipeline_id, operator_segment_id, segment_name,
            artifact_id
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (segment_id) DO UPDATE SET artifact_id = excluded.artifact_id
        """,
        [
            [
                f"{pipeline_id}:SEG:{segment_id}",
                pipeline_id,
                segment_id,
                f"{pipeline_id} segment {segment_id}",
                artifact.artifact_id,
            ]
            for segment_id in segment_ids
        ],
    )
    _mark_artifact_processed(connection, artifact.artifact_id)
    return len(export.rows)


def store_county_references(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    rows: Iterable[CountyReference],
) -> int:
    materialized = tuple(rows)
    connection.executemany(
        """
        INSERT INTO county_reference_observations(
            artifact_id, geoid, state_abbreviation, county_name,
            normalized_county_name, latitude, longitude, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (artifact_id, geoid) DO NOTHING
        """,
        [
            [
                artifact.artifact_id,
                row.geoid,
                row.state_abbreviation,
                row.county_name,
                normalize_county_name(row.county_name),
                row.latitude,
                row.longitude,
                artifact.received_at,
            ]
            for row in materialized
        ],
    )
    _mark_artifact_processed(connection, artifact.artifact_id)
    return len(materialized)


def geocode_locations_to_counties(
    connection: duckdb.DuckDBPyConnection,
    location_artifact: StoredArtifact,
    county_artifact: StoredArtifact,
    *,
    pipeline_id: str = "TGP",
) -> tuple[int, int]:
    connection.execute(
        """
        INSERT INTO location_coordinate_observations(
            pipeline_id, operator_location_id, location_artifact_id,
            coordinate_artifact_id, latitude, longitude, coordinate_method,
            coordinate_precision, matched_geography_id,
            matched_geography_name, observed_at
        )
        SELECT
            location.pipeline_id,
            location.operator_location_id,
            location.artifact_id,
            county.artifact_id,
            county.latitude,
            county.longitude,
            'census_county_internal_point',
            'county',
            county.geoid,
            county.county_name,
            greatest(location.observed_at, county.observed_at)
        FROM location_observations AS location
        JOIN county_reference_observations AS county
          ON county.artifact_id = ?
         AND county.state_abbreviation = location.state_abbreviation
         AND county.normalized_county_name = location.normalized_county_name
        WHERE location.artifact_id = ?
          AND location.pipeline_id = ?
          AND NOT EXISTS (
              SELECT 1
              FROM location_coordinate_observations AS existing
              WHERE existing.pipeline_id = location.pipeline_id
                AND existing.operator_location_id = location.operator_location_id
                AND existing.location_artifact_id = location.artifact_id
                AND existing.coordinate_method = 'census_county_internal_point'
          )
        ON CONFLICT DO NOTHING
        """,
        [county_artifact.artifact_id, location_artifact.artifact_id, pipeline_id],
    )
    matched = connection.execute(
        """
        SELECT count(*)
        FROM location_coordinate_observations
        WHERE pipeline_id = ?
          AND location_artifact_id = ?
          AND coordinate_method = 'census_county_internal_point'
        """,
        [pipeline_id, location_artifact.artifact_id],
    ).fetchone()[0]
    total = connection.execute(
        """
        SELECT count(*) FROM location_observations
        WHERE pipeline_id = ? AND artifact_id = ?
        """,
        [pipeline_id, location_artifact.artifact_id],
    ).fetchone()[0]
    connection.execute(
        """
        UPDATE facilities AS facility
        SET
            latitude = coordinate.latitude,
            longitude = coordinate.longitude,
            coordinate_method = coordinate.coordinate_method,
            coordinate_precision = coordinate.coordinate_precision,
            coordinate_artifact_id = coordinate.coordinate_artifact_id
        FROM location_coordinate_observations AS coordinate
        WHERE facility.pipeline_id = coordinate.pipeline_id
          AND facility.operator_location_id = coordinate.operator_location_id
          AND coordinate.location_artifact_id = ?
          AND coordinate.coordinate_artifact_id = ?
          AND coordinate.coordinate_method = 'census_county_internal_point'
        """,
        [location_artifact.artifact_id, county_artifact.artifact_id],
    )
    return matched, total - matched


def store_map_reference_layer(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    *,
    layer_code: str,
    source_vintage: str,
    geojson: dict[str, object],
) -> int:
    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("map reference GeoJSON contains no features")
    connection.execute(
        """
        INSERT INTO map_reference_layers(
            artifact_id, layer_code, source_vintage, feature_count,
            geojson, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (artifact_id) DO NOTHING
        """,
        [
            artifact.artifact_id,
            layer_code,
            source_vintage,
            len(features),
            json.dumps(geojson, separators=(",", ":"), sort_keys=True),
            artifact.received_at,
        ],
    )
    _mark_artifact_processed(connection, artifact.artifact_id)
    return len(features)


def store_capacity_export(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    export: TgpCapacityExport,
    *,
    pipeline_id: str = "TGP",
) -> int:
    connection.execute(
        """
        INSERT INTO capacity_exports(
            artifact_id, pipeline_id, capacity_kind, point_role,
            tsp_number, tsp_name, effective_at, gas_day, cycle,
            location_purpose, measurement_basis, source_posted_at,
            quantity_description, source_footer_row_count, parsed_row_count,
            schema_sha256, parser_version, comments, observed_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT (artifact_id) DO NOTHING
        """,
        [
            artifact.artifact_id,
            pipeline_id,
            export.capacity_kind,
            export.point_role,
            export.tsp_number,
            export.tsp_name,
            export.effective_at,
            export.gas_day,
            export.cycle,
            export.location_purpose,
            export.measurement_basis,
            export.source_posted_at,
            export.quantity_description,
            export.source_footer_row_count,
            len(export.rows),
            export.schema_sha256,
            "tgp_capacity_v1",
            export.comments,
            artifact.received_at,
        ],
    )
    known_locations = {
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT operator_location_id
            FROM facilities
            WHERE pipeline_id = ?
              AND operator_location_id IS NOT NULL
            """,
            [pipeline_id],
        ).fetchall()
    }
    known_segments = {
        row[0]
        for row in connection.execute(
            """
            SELECT operator_segment_id
            FROM segments
            WHERE pipeline_id = ?
            """,
            [pipeline_id],
        ).fetchall()
    }
    connection.executemany(
        """
        INSERT INTO capacity_observations(
            capacity_observation_id, pipeline_id, capacity_kind, point_role,
            source_row_position, operator_location_id, operator_segment_id,
            location_name, zone, facility_id, segment_id, gas_day,
            effective_at, cycle, flow_direction,
            design_capacity_dth_per_day, operating_capacity_dth_per_day,
            scheduled_quantity_dth_per_day, available_capacity_dth_per_day,
            interruptible_scheduled, all_quantity_available, quantity_reason,
            available_reconciles, source_posted_at, observed_at, available_at,
            artifact_id
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT (capacity_observation_id) DO NOTHING
        """,
        [
            [
                f"{artifact.artifact_id}:{row.row_position}",
                pipeline_id,
                export.capacity_kind,
                export.point_role,
                row.row_position,
                row.operator_location_id,
                row.operator_segment_id,
                row.location_name,
                row.zone,
                (
                    f"{pipeline_id}:{row.operator_location_id}"
                    if row.operator_location_id in known_locations
                    else None
                ),
                (
                    f"{pipeline_id}:SEG:{row.operator_segment_id}"
                    if row.operator_segment_id in known_segments
                    else None
                ),
                export.gas_day,
                export.effective_at,
                export.cycle,
                row.flow_indicator,
                row.design_capacity_dth_per_day,
                row.operating_capacity_dth_per_day,
                row.scheduled_quantity_dth_per_day,
                row.available_capacity_dth_per_day,
                row.interruptible_scheduled,
                row.all_quantity_available,
                row.quantity_reason,
                row.available_reconciles,
                export.source_posted_at,
                artifact.received_at,
                artifact.received_at,
                artifact.artifact_id,
            ]
            for row in export.rows
        ],
    )
    _mark_artifact_processed(connection, artifact.artifact_id)
    return len(export.rows)


def store_eia_storage_release(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    release: EiaStorageRelease,
) -> int:
    period_start = pendulum.datetime(
        release.current_week.year,
        release.current_week.month,
        release.current_week.day,
        tz="UTC",
    )
    period_end = period_start.add(days=7)
    metrics = (
        ("working_gas", "Working gas storage", "Bcf", "working_gas_bcf"),
        ("weekly_change", "Weekly storage change", "Bcf", "weekly_change_bcf"),
        (
            "five_year_average",
            "Five-year average storage",
            "Bcf",
            "five_year_average_bcf",
        ),
        (
            "vs_five_year_average_pct",
            "Storage vs 5-year average",
            "%",
            "pct_vs_five_year_average",
        ),
        (
            "vs_year_ago_pct",
            "Storage vs year ago",
            "%",
            "pct_vs_year_ago",
        ),
    )
    parameters: list[list[object]] = []
    for series in release.series:
        for suffix, metric, unit, attribute in metrics:
            series_code = f"EIA_WNGSR:{series.series_id}:{suffix}"
            parameters.append(
                [
                    f"{artifact.artifact_id}:{series.series_id}:{suffix}",
                    series_code,
                    "U.S. EIA",
                    "storage",
                    metric,
                    series.geography,
                    period_start,
                    period_end,
                    getattr(series, attribute),
                    unit,
                    release.available_at,
                    release.available_at,
                    release.release_date.to_date_string(),
                    artifact.artifact_id,
                ]
            )
    connection.executemany(
        """
        INSERT INTO market_observations(
            market_observation_id, series_code, provider, observation_type,
            metric, geography, period_start, period_end, value, unit,
            source_published_at, available_at, vintage, artifact_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        parameters,
    )
    _mark_artifact_processed(connection, artifact.artifact_id)
    return len(parameters)


def store_henry_hub_spot(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    observations: tuple[HenryHubSpotObservation, ...],
    *,
    provider: str = "U.S. EIA",
    series_code: str = "EIA:DHHNGSP",
    source_published_at: pendulum.DateTime | None = None,
    vintage: str | None = None,
) -> int:
    parameters: list[list[object]] = []
    for observation in observations:
        period_start = pendulum.datetime(
            observation.observation_date.year,
            observation.observation_date.month,
            observation.observation_date.day,
            tz="America/Chicago",
        ).in_timezone("UTC")
        parameters.append(
            [
                f"{artifact.artifact_id}:{observation.observation_date}",
                series_code,
                provider,
                "physical_spot",
                "Henry Hub physical spot",
                "Henry Hub",
                period_start,
                period_start.add(days=1),
                observation.price_usd_per_mmbtu,
                "USD/MMBtu",
                source_published_at,
                artifact.received_at,
                vintage or artifact.received_at.to_iso8601_string(),
                artifact.artifact_id,
            ]
        )
    connection.executemany(
        """
        INSERT INTO market_observations(
            market_observation_id, series_code, provider, observation_type,
            metric, geography, period_start, period_end, value, unit,
            source_published_at, available_at, vintage, artifact_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        parameters,
    )
    _mark_artifact_processed(connection, artifact.artifact_id)
    return len(parameters)


def store_nws_degree_day_forecast(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    anchor: WeatherAnchor,
    forecast: NwsHourlyForecast,
) -> int:
    metrics = (
        ("mean_temperature", "Forecast mean temperature", "degF", "mean_temperature_f"),
        ("hdd_65", "Forecast HDD", "degree-days", "hdd_65"),
        ("cdd_65", "Forecast CDD", "degree-days", "cdd_65"),
    )
    parameters: list[list[object]] = []
    for day in forecast.days:
        for suffix, metric, unit, attribute in metrics:
            parameters.append(
                [
                    f"{artifact.artifact_id}:{anchor.code}:{day.local_date}:{suffix}",
                    f"NWS:{anchor.code}:{suffix}",
                    "NOAA / National Weather Service",
                    "weather_forecast",
                    metric,
                    anchor.name,
                    day.period_start,
                    day.period_end,
                    getattr(day, attribute),
                    unit,
                    forecast.generated_at,
                    artifact.received_at,
                    forecast.generated_at.to_iso8601_string(),
                    artifact.artifact_id,
                ]
            )
    connection.executemany(
        """
        INSERT INTO market_observations(
            market_observation_id, series_code, provider, observation_type,
            metric, geography, period_start, period_end, value, unit,
            source_published_at, available_at, vintage, artifact_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        parameters,
    )
    _mark_artifact_processed(connection, artifact.artifact_id)
    return len(parameters)


def store_yahoo_front_month_quote(
    connection: duckdb.DuckDBPyConnection,
    artifact: StoredArtifact,
    quote: FrontMonthFuturesQuote,
) -> int:
    connection.execute(
        """
        INSERT INTO market_observations(
            market_observation_id, series_code, provider, observation_type,
            metric, geography, period_start, period_end, value, unit,
            source_published_at, available_at, vintage, artifact_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        [
            f"{artifact.artifact_id}:{quote.quote_at.int_timestamp}",
            "YAHOO:NG=F",
            "Yahoo Finance",
            "futures_proxy",
            "Front-month futures proxy",
            "Henry Hub",
            quote.quote_at,
            None,
            quote.price_usd_per_mmbtu,
            "USD/MMBtu",
            quote.quote_at,
            artifact.received_at,
            quote.vintage,
            artifact.artifact_id,
        ],
    )
    _mark_artifact_processed(connection, artifact.artifact_id)
    return 1


def _mark_artifact_processed(
    connection: duckdb.DuckDBPyConnection,
    artifact_id: str,
) -> None:
    connection.execute(
        """
        UPDATE source_artifacts
        SET processed_at = ?
        WHERE artifact_id = ?
        """,
        [pendulum.now("UTC"), artifact_id],
    )
