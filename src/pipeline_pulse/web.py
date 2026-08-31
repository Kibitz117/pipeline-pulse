from __future__ import annotations

import json
import mimetypes
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import parse_qs, urlparse

import duckdb
import pendulum

from .alerts import normalize_alert_semantics
from .insights import build_tgp_research_packet, latest_tgp_research_memo
from .market_state import build_tgp_daily_market_state
from .scheduler import ScheduledCollectionSummary, run_scheduled_collection

UI_ROOT = Path(__file__).parents[2] / "ui"
NOTICE_URL = (
    "https://pipeline2.kindermorgan.com/Notices/NoticeDetail.aspx"
    "?code=TGP&notc_nbr={notice_id}"
)
DOWNLOAD_DATASETS = {
    "daily-market-state": {
        "filename": "tgp_daily_market_state.csv",
        "table": "latest_tgp_daily_market_state",
        "description": "Thirty-day overlap-safe TGP maintenance and schedule-gap calendar.",
        "json_endpoint": "/api/market-state",
    },
    "maintenance-notices": {
        "filename": "tgp_maintenance_notices.csv",
        "table": "tgp_maintenance_notices",
        "description": "Normalized TGP maintenance notice versions and source evidence.",
        "json_endpoint": "/api/notices",
    },
    "notice-history": {
        "filename": "tgp_notice_version_history.csv",
        "table": "tgp_notice_version_timeline",
        "description": "Point-in-time notice checks, semantic revisions, and availability timestamps.",
        "json_endpoint": "/api/notices/{notice_id}/history",
    },
    "transport-impacts": {
        "filename": "tgp_transport_impacts.csv",
        "table": "tgp_transport_impact_assessments",
        "description": "Direction-matched maintenance limits and conditional scheduled shortfalls.",
        "json_endpoint": "/api/transport-impacts",
    },
    "operational-capacity": {
        "filename": "tgp_latest_operational_capacity.csv",
        "table": "latest_tgp_capacity",
        "description": "Latest TGP point and segment operating, scheduled, and available capacity.",
        "json_endpoint": "/api/operational-capacity",
    },
    "alerts": {
        "filename": "tgp_alerts.csv",
        "table": "alerts",
        "description": "Deterministic material-change alerts with auditable evidence.",
        "json_endpoint": "/api/alerts",
    },
    "locations": {
        "filename": "tgp_locations.csv",
        "table": "tgp_location_map",
        "description": "TGP locations with county-level coordinate provenance.",
        "json_endpoint": "/api/map",
    },
    "market-context": {
        "filename": "gas_market_context.csv",
        "table": "market_observations",
        "description": "Point-in-time storage, physical spot, weather, and optional futures observations.",
        "json_endpoint": "/api/market-context",
    },
}


def _bounded_limit(value: str | None, *, default: int = 100) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return min(max(parsed, 1), 500)


def _parse_as_of(value: str | None) -> pendulum.DateTime | None:
    if value is None or not value.strip():
        return None
    parsed = pendulum.parse(value, strict=False)
    if not isinstance(parsed, pendulum.DateTime):
        raise ValueError("as_of must be an ISO-8601 date or timestamp")  # noqa: TRY004
    return parsed.in_timezone("UTC")


class TgpReadModel:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _query(
        self,
        query: str,
        parameters: list[object] | None = None,
    ) -> list[dict[str, object]]:
        connection = duckdb.connect(str(self.database_path), read_only=True)
        try:
            result = connection.execute(query, parameters or [])
            columns = tuple(description[0] for description in result.description)
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        finally:
            connection.close()

    def data_catalog(self) -> dict[str, object]:
        counts = {
            row["dataset_id"]: int(row["row_count"])
            for row in self._query(
                """
                SELECT 'maintenance-notices' AS dataset_id, count(*) AS row_count
                FROM tgp_maintenance_notices
                UNION ALL
                SELECT 'notice-history', count(*)
                FROM tgp_notice_version_timeline
                UNION ALL
                SELECT 'daily-market-state', count(*)
                FROM latest_tgp_daily_market_state
                UNION ALL
                SELECT 'transport-impacts', count(*)
                FROM tgp_transport_impact_assessments
                UNION ALL
                SELECT 'operational-capacity', count(*)
                FROM latest_tgp_capacity
                UNION ALL
                SELECT 'alerts', count(*)
                FROM alerts
                UNION ALL
                SELECT 'locations', count(*)
                FROM tgp_location_map
                UNION ALL
                SELECT 'market-context', count(*)
                FROM market_observations
                """
            )
        }
        curated_root = self.database_path.parent / "curated"
        datasets = []
        for dataset_id, definition in DOWNLOAD_DATASETS.items():
            datasets.append(
                {
                    "dataset_id": dataset_id,
                    "description": definition["description"],
                    "duckdb_relation": definition["table"],
                    "row_count": counts.get(dataset_id, 0),
                    "json_endpoint": definition["json_endpoint"],
                    "csv_download": f"/api/download/{dataset_id}.csv",
                    "csv_available": (curated_root / definition["filename"]).is_file(),
                }
            )
        return {
            "api_version": "v1",
            "access": "read_only_allowlisted",
            "duckdb": {
                "path_from_repository_root": "data/pipeline_pulse.duckdb",
                "source_clocks": [
                    "source_published_at",
                    "available_at",
                    "received_at",
                    "processed_at",
                ],
                "provenance_fields": [
                    "provider",
                    "observation_type",
                    "artifact_id",
                    "canonical_url",
                ],
            },
            "datasets": datasets,
            "arbitrary_sql_over_http": False,
        }

    def overview(self) -> dict[str, object]:
        rows = self._query(
            """
            WITH latest AS (
                SELECT *
                FROM tgp_outage_report_summary
                ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
                LIMIT 1
            )
            SELECT
                CAST(latest.report_updated_on AS VARCHAR) AS latest_report_date,
                latest.notice_id AS latest_report_notice_id,
                strftime(latest.posted_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS latest_report_posted_at,
                latest.station_count AS latest_station_count,
                latest.station_period_rows AS latest_capacity_rows,
                latest.populated_capacity_rows AS latest_populated_capacity_rows,
                latest.max_reduction_dth_per_day AS latest_max_reduction_dth_per_day,
                latest.reduction_mismatch_count AS latest_reduction_mismatch_count,
                (SELECT count(*) FROM tgp_maintenance_notices) AS maintenance_notice_count,
                (SELECT count(*) FROM current_notice_index
                 WHERE notice_type_primary = 'MAINTENANCE')
                    AS maintenance_index_count,
                (SELECT count(*) FROM tgp_outage_report_summary) AS report_vintage_count,
                (SELECT count(*) FROM outage_impact_observations) AS historical_capacity_rows,
                (SELECT count(*) FROM outage_impact_observations WHERE reduction_reconciles = false) AS historical_reduction_mismatch_count,
                (SELECT count(*) FROM tgp_outage_capacity_revisions WHERE operating_capacity_change_dth_per_day != 0) AS historical_revision_count,
                (SELECT count(*) FROM tgp_location_map) AS location_count,
                (SELECT count(*) FROM tgp_location_map WHERE latitude IS NOT NULL) AS geocoded_location_count,
                (SELECT count(DISTINCT operator_segment_id) FROM tgp_location_map) AS mapped_segment_count
            FROM latest
            """
        )
        if not rows:
            return {}
        output = rows[0]
        output["source_url"] = NOTICE_URL.format(
            notice_id=output["latest_report_notice_id"]
        )
        return output

    def reports(self) -> list[dict[str, object]]:
        return self._query(
            """
            SELECT
                notice_id,
                version_sha256,
                CAST(report_updated_on AS VARCHAR) AS report_updated_on,
                strftime(posted_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS posted_at_utc,
                CAST(first_forecast_date AS VARCHAR) AS first_forecast_date,
                CAST(last_forecast_date AS VARCHAR) AS last_forecast_date,
                station_period_rows,
                station_count,
                populated_capacity_rows,
                max_reduction_dth_per_day,
                reduction_mismatch_count,
                artifact_id,
                strftime(observed_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS observed_at_utc
            FROM tgp_outage_report_summary
            QUALIFY row_number() OVER (
                PARTITION BY notice_id, version_sha256
                ORDER BY observed_at DESC, artifact_id DESC
            ) = 1
            ORDER BY report_updated_on DESC, posted_at DESC
            """
        )

    def constraints(
        self,
        *,
        search: str = "",
        report_kind: str = "all",
        report_notice_id: str | None = None,
        as_of: pendulum.DateTime | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if report_kind not in {"all", "seven_day", "monthly"}:
            report_kind = "all"
        rows = self._query(
            """
            WITH selected_report AS (
                SELECT artifact_id
                FROM tgp_outage_report_summary
                WHERE (? IS NULL OR notice_id = ?)
                  AND (? IS NULL OR observed_at <= ?)
                ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
                LIMIT 1
            )
            SELECT
                impact.notice_id,
                impact.report_kind,
                CAST(impact.report_updated_on AS VARCHAR) AS report_updated_on,
                impact.period_label,
                CAST(impact.period_start AS VARCHAR) AS period_start,
                CAST(impact.period_end AS VARCHAR) AS period_end,
                impact.station_label,
                impact.operator_segment_id,
                impact.flow_direction,
                impact.nominal_capacity_dth_per_day,
                impact.operating_capacity_dth_per_day,
                impact.calculated_reduction_dth_per_day,
                round(
                    impact.calculated_reduction_dth_per_day * 100.0 /
                    nullif(impact.nominal_capacity_dth_per_day, 0),
                    1
                ) AS reduction_pct,
                impact.reduction_reconciles,
                impact.outage_description,
                impact.artifact_id,
                strftime(impact.observed_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS report_observed_at_utc
            FROM outage_impact_observations AS impact
            JOIN selected_report
              ON selected_report.artifact_id = impact.artifact_id
            WHERE impact.calculated_reduction_dth_per_day > 0
              AND (? = 'all' OR impact.report_kind = ?)
              AND (
                  ? = ''
                  OR impact.station_label ILIKE '%' || ? || '%'
                  OR impact.outage_description ILIKE '%' || ? || '%'
              )
            ORDER BY impact.calculated_reduction_dth_per_day DESC,
                     impact.period_start,
                     impact.station_label
            LIMIT ?
            """,
            [
                report_notice_id,
                report_notice_id,
                as_of,
                as_of,
                report_kind,
                report_kind,
                search,
                search,
                search,
                limit,
            ],
        )
        for row in rows:
            row["source_url"] = NOTICE_URL.format(notice_id=row["notice_id"])
        return rows

    def revisions(
        self,
        *,
        search: str = "",
        direction: str = "all",
        report_notice_id: str | None = None,
        as_of: pendulum.DateTime | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if direction not in {"all", "worsened", "improved"}:
            direction = "all"
        rows = self._query(
            """
            WITH selected_report AS (
                SELECT artifact_id
                FROM tgp_outage_report_summary
                WHERE (? IS NULL OR notice_id = ?)
                  AND (? IS NULL OR observed_at <= ?)
                ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
                LIMIT 1
            )
            SELECT
                revision.notice_id,
                revision.prior_report_notice_id,
                revision.report_kind,
                CAST(revision.report_updated_on AS VARCHAR) AS report_updated_on,
                CAST(revision.period_start AS VARCHAR) AS period_start,
                CAST(revision.period_end AS VARCHAR) AS period_end,
                revision.station_label,
                revision.operator_segment_id,
                revision.flow_direction,
                revision.prior_operating_capacity_dth_per_day,
                revision.operating_capacity_dth_per_day,
                revision.operating_capacity_change_dth_per_day,
                revision.calculated_reduction_dth_per_day,
                revision.outage_description,
                revision.artifact_id,
                revision.prior_artifact_id
            FROM tgp_outage_capacity_revisions AS revision
            JOIN selected_report
              ON selected_report.artifact_id = revision.artifact_id
            WHERE revision.operating_capacity_change_dth_per_day != 0
              AND (
                  ? = 'all'
                  OR (? = 'worsened' AND revision.operating_capacity_change_dth_per_day < 0)
                  OR (? = 'improved' AND revision.operating_capacity_change_dth_per_day > 0)
              )
              AND (
                  ? = ''
                  OR revision.station_label ILIKE '%' || ? || '%'
                  OR revision.outage_description ILIKE '%' || ? || '%'
              )
            ORDER BY abs(revision.operating_capacity_change_dth_per_day) DESC,
                     revision.period_start,
                     revision.station_label
            LIMIT ?
            """,
            [
                report_notice_id,
                report_notice_id,
                as_of,
                as_of,
                direction,
                direction,
                direction,
                search,
                search,
                search,
                limit,
            ],
        )
        for row in rows:
            row["source_url"] = NOTICE_URL.format(notice_id=row["notice_id"])
            row["prior_source_url"] = NOTICE_URL.format(
                notice_id=row["prior_report_notice_id"]
            )
        return rows

    def notices(
        self,
        *,
        search: str = "",
        as_of: pendulum.DateTime | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        rows = self._query(
            """
            WITH eligible_observations AS (
                SELECT *
                FROM notice_version_observations
                WHERE pipeline_id = 'TGP'
                  AND (? IS NULL OR observed_at <= ?)
                QUALIFY row_number() OVER (
                    PARTITION BY pipeline_id, notice_id
                    ORDER BY observed_at DESC, artifact_id DESC
                ) = 1
            ), eligible_versions AS (
                SELECT
                    version.*,
                    observation.observed_at AS version_observed_at,
                    artifact.canonical_url,
                    artifact.raw_path,
                    artifact.received_at
                FROM eligible_observations AS observation
                JOIN notice_versions AS version
                  ON version.pipeline_id = observation.pipeline_id
                 AND version.notice_id = observation.notice_id
                 AND version.version_sha256 = observation.version_sha256
                JOIN source_artifacts AS artifact
                  ON artifact.artifact_id = observation.artifact_id
                WHERE version.pipeline_id = 'TGP'
                  AND version.notice_type_primary = 'MAINTENANCE'
            )
            SELECT
                notice_id,
                status_description,
                prior_notice_id,
                subject,
                strftime(posted_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS posted_at_utc,
                strftime(posted_at AT TIME ZONE 'America/Chicago', '%Y-%m-%dT%H:%M:%S') AS posted_at_operator_local,
                strftime(effective_start AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS effective_start_utc,
                strftime(effective_end AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS effective_end_utc,
                required_response,
                strftime(response_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS response_at_utc,
                left(notice_text, 700) AS notice_excerpt,
                version_sha256,
                canonical_url,
                raw_path,
                strftime(version_observed_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS version_observed_at_utc,
                strftime(received_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS received_at_utc
            FROM eligible_versions
            WHERE ? = ''
               OR subject ILIKE '%' || ? || '%'
               OR notice_text ILIKE '%' || ? || '%'
               OR notice_id = ?
            ORDER BY posted_at DESC, notice_id DESC
            LIMIT ?
            """,
            [as_of, as_of, search, search, search, search, limit],
        )
        for row in rows:
            row["source_url"] = NOTICE_URL.format(notice_id=row["notice_id"])
        return rows

    def notice(
        self,
        notice_id: str,
        *,
        as_of: pendulum.DateTime | None = None,
    ) -> dict[str, object] | None:
        rows = self._query(
            """
            WITH eligible_observations AS (
                SELECT *
                FROM notice_version_observations
                WHERE pipeline_id = 'TGP'
                  AND notice_id = ?
                  AND (? IS NULL OR observed_at <= ?)
                ORDER BY observed_at DESC, artifact_id DESC
                LIMIT 1
            ), eligible_versions AS (
                SELECT
                    version.*,
                    observation.observed_at AS version_observed_at,
                    artifact.canonical_url,
                    artifact.raw_path,
                    artifact.received_at
                FROM eligible_observations AS observation
                JOIN notice_versions AS version
                  ON version.pipeline_id = observation.pipeline_id
                 AND version.notice_id = observation.notice_id
                 AND version.version_sha256 = observation.version_sha256
                JOIN source_artifacts AS artifact
                  ON artifact.artifact_id = observation.artifact_id
                WHERE version.pipeline_id = 'TGP'
                  AND version.notice_type_primary = 'MAINTENANCE'
            )
            SELECT
                notice_id,
                status_description,
                prior_notice_id,
                subject,
                strftime(posted_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS posted_at_utc,
                strftime(posted_at AT TIME ZONE 'America/Chicago', '%Y-%m-%dT%H:%M:%S') AS posted_at_operator_local,
                strftime(effective_start AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS effective_start_utc,
                strftime(effective_end AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS effective_end_utc,
                required_response,
                strftime(response_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS response_at_utc,
                notice_text,
                version_sha256,
                canonical_url,
                raw_path,
                strftime(version_observed_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS version_observed_at_utc,
                strftime(received_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS received_at_utc
            FROM eligible_versions
            """,
            [notice_id, as_of, as_of],
        )
        if not rows:
            return None
        rows[0]["source_url"] = NOTICE_URL.format(notice_id=notice_id)
        return rows[0]

    def notice_history(
        self,
        notice_id: str,
        *,
        as_of: pendulum.DateTime | None = None,
    ) -> dict[str, object] | None:
        versions = self._query(
            """
            WITH ordered AS (
                SELECT
                    observation.*,
                    lag(observation.version_sha256) OVER (
                        PARTITION BY observation.pipeline_id, observation.notice_id
                        ORDER BY observation.observed_at, observation.artifact_id
                    ) AS prior_version_sha256
                FROM notice_version_observations AS observation
                WHERE observation.pipeline_id = 'TGP'
                  AND observation.notice_id = ?
                  AND (? IS NULL OR observation.observed_at <= ?)
            ), changes AS (
                SELECT *
                FROM ordered
                WHERE prior_version_sha256 IS NULL
                   OR version_sha256 != prior_version_sha256
            )
            SELECT
                changes.version_sha256,
                changes.prior_version_sha256,
                strftime(changes.observed_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS available_from_utc,
                version.status_description,
                version.prior_notice_id,
                version.notice_type_primary,
                version.notice_type_secondary,
                version.subject,
                version.notice_text,
                strftime(version.posted_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS posted_at_utc,
                strftime(version.effective_start AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS effective_start_utc,
                strftime(version.effective_end AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS effective_end_utc,
                version.required_response,
                strftime(version.response_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS response_at_utc,
                artifact.raw_path
            FROM changes
            JOIN notice_versions AS version
              ON version.pipeline_id = changes.pipeline_id
             AND version.notice_id = changes.notice_id
             AND version.version_sha256 = changes.version_sha256
            JOIN source_artifacts AS artifact
              ON artifact.artifact_id = changes.artifact_id
            ORDER BY changes.observed_at, changes.artifact_id
            """,
            [notice_id, as_of, as_of],
        )
        if not versions:
            return None
        comparison_fields = (
            ("status", "status_description"),
            ("prior notice link", "prior_notice_id"),
            ("notice type", "notice_type_primary"),
            ("notice subtype", "notice_type_secondary"),
            ("subject", "subject"),
            ("operator text", "notice_text"),
            ("posted time", "posted_at_utc"),
            ("effective start", "effective_start_utc"),
            ("effective end", "effective_end_utc"),
            ("required response", "required_response"),
            ("response deadline", "response_at_utc"),
        )
        prior: dict[str, object] | None = None
        for index, version in enumerate(versions):
            version["changed_fields"] = (
                ["first captured version"]
                if prior is None
                else [
                    label
                    for label, field in comparison_fields
                    if prior[field] != version[field]
                ]
            )
            version["is_current_for_cutoff"] = index == len(versions) - 1
            prior = version

        related_updates = self._query(
            """
            WITH eligible AS (
                SELECT *
                FROM notice_version_observations
                WHERE pipeline_id = 'TGP'
                  AND (? IS NULL OR observed_at <= ?)
                QUALIFY row_number() OVER (
                    PARTITION BY pipeline_id, notice_id
                    ORDER BY observed_at DESC, artifact_id DESC
                ) = 1
            )
            SELECT
                version.notice_id,
                version.status_description,
                version.subject,
                strftime(eligible.observed_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS available_from_utc
            FROM eligible
            JOIN notice_versions AS version
              ON version.pipeline_id = eligible.pipeline_id
             AND version.notice_id = eligible.notice_id
             AND version.version_sha256 = eligible.version_sha256
            WHERE version.prior_notice_id = ?
            ORDER BY eligible.observed_at, version.notice_id
            """,
            [as_of, as_of, notice_id],
        )
        return {
            "notice_id": notice_id,
            "as_of_utc": as_of.to_iso8601_string() if as_of else None,
            "versions": versions,
            "related_operator_updates": related_updates,
            "source_url": NOTICE_URL.format(notice_id=notice_id),
            "point_in_time_rule": (
                "Use the final version whose available_from_utc is no later "
                "than the research cutoff. Operator publication time alone "
                "does not establish when this system knew the revision."
            ),
        }

    def _market_rows(
        self,
        *,
        as_of: pendulum.DateTime | None,
    ) -> list[dict[str, object]]:
        return self._query(
            """
            WITH eligible AS (
                SELECT
                    observation.*,
                    artifact.canonical_url AS source_url,
                    max(available_at) OVER (
                        PARTITION BY series_code, geography
                    ) AS latest_available_at,
                    row_number() OVER (
                        PARTITION BY series_code, geography
                        ORDER BY available_at DESC, period_start DESC
                    ) AS latest_row_rank
                FROM market_observations AS observation
                JOIN source_artifacts AS artifact USING (artifact_id)
                WHERE (? IS NULL OR available_at <= ?)
            )
            SELECT
                series_code,
                provider,
                observation_type,
                metric,
                geography,
                value,
                unit,
                vintage,
                strftime(period_start AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS period_start_utc,
                strftime(period_end AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS period_end_utc,
                strftime(available_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS available_at_utc,
                strftime(source_published_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS source_published_at_utc,
                source_url,
                artifact_id
            FROM eligible
            WHERE (
                observation_type = 'weather_forecast'
                AND available_at = latest_available_at
            ) OR (
                coalesce(observation_type, 'other') != 'weather_forecast'
                AND latest_row_rank = 1
            )
            ORDER BY observation_type, metric, geography, period_start
            """,
            [as_of, as_of],
        )

    def market_context(
        self,
        *,
        as_of: pendulum.DateTime | None = None,
    ) -> dict[str, object]:
        return {
            "as_of_utc": as_of.to_iso8601_string() if as_of is not None else None,
            "selected": self._market_rows(as_of=as_of),
            "latest": self._market_rows(as_of=None),
        }

    def market_state(
        self,
        *,
        as_of: pendulum.DateTime | None = None,
        horizon_days: int = 30,
    ) -> dict[str, object]:
        return build_tgp_daily_market_state(
            self.database_path,
            decision_at=as_of,
            horizon_days=horizon_days,
        )

    def transport_impacts(
        self,
        *,
        search: str = "",
        status: str = "all",
        report_notice_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if status not in {"all", "research_scenario", "monitor", "no_trade_mapping"}:
            status = "all"
        rows = self._query(
            """
            WITH selected_report AS (
                SELECT artifact_id
                FROM tgp_outage_report_summary
                WHERE (? IS NULL OR notice_id = ?)
                ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
                LIMIT 1
            ), latest_assessment AS (
                SELECT assessment.*
                FROM tgp_transport_impact_assessments AS assessment
                JOIN selected_report
                  ON selected_report.artifact_id = assessment.report_artifact_id
                QUALIFY row_number() OVER (
                    PARTITION BY source_table_index, source_row_index,
                                 period_start, period_end
                    ORDER BY baseline_source_posted_at DESC NULLS LAST,
                             calculated_at DESC, assessment_id DESC
                ) = 1
            )
            SELECT
                assessment_id, report_notice_id,
                CAST(report_updated_on AS VARCHAR) AS report_updated_on,
                CAST(period_start AS VARCHAR) AS period_start,
                CAST(period_end AS VARCHAR) AS period_end,
                station_label, operator_segment_id, outage_flow_direction,
                capacity_flow_direction, tgp_zone, capacity_location_name,
                CAST(baseline_gas_day AS VARCHAR) AS baseline_gas_day,
                baseline_cycle,
                baseline_operating_capacity_dth_per_day,
                baseline_scheduled_quantity_dth_per_day,
                baseline_available_capacity_dth_per_day,
                forecast_nominal_capacity_dth_per_day,
                forecast_operating_capacity_dth_per_day,
                gross_reduction_dth_per_day,
                conditional_scheduled_shortfall_dth_per_day,
                forecast_headroom_vs_baseline_schedule_dth_per_day,
                baseline_timing, match_method, research_status,
                price_mapping_status, price_mapping_reason,
                benchmark_reference_url,
                CAST(unresolved_reasons AS VARCHAR) AS unresolved_reasons,
                CAST(evidence AS VARCHAR) AS evidence,
                strftime(
                    baseline_source_posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS baseline_source_posted_at_utc,
                strftime(
                    calculated_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS calculated_at_utc,
                report_artifact_id, capacity_artifact_id
            FROM latest_assessment
            WHERE (? = 'all' OR research_status = ?)
              AND (
                  ? = ''
                  OR station_label ILIKE '%' || ? || '%'
                  OR operator_segment_id ILIKE '%' || ? || '%'
                  OR capacity_location_name ILIKE '%' || ? || '%'
                  OR tgp_zone ILIKE '%' || ? || '%'
              )
            ORDER BY
                CASE research_status
                    WHEN 'research_scenario' THEN 1
                    WHEN 'monitor' THEN 2
                    ELSE 3
                END,
                conditional_scheduled_shortfall_dth_per_day DESC NULLS LAST,
                gross_reduction_dth_per_day DESC,
                period_start, station_label
            LIMIT ?
            """,
            [
                report_notice_id,
                report_notice_id,
                status,
                status,
                search,
                search,
                search,
                search,
                search,
                limit,
            ],
        )
        for row in rows:
            row["unresolved_reasons"] = json.loads(str(row["unresolved_reasons"]))
            row["evidence"] = json.loads(str(row["evidence"]))
            row["source_url"] = NOTICE_URL.format(notice_id=row["report_notice_id"])
        return rows

    def research_brief(self) -> dict[str, object]:
        packet = build_tgp_research_packet(self.database_path)
        memo = latest_tgp_research_memo(
            self.database_path,
            data_fingerprint=str(packet["data_fingerprint"]),
        )
        memo_is_current = bool(memo is not None and memo["is_current"])
        return {
            "packet": packet,
            "memo": memo,
            "memo_status": (
                "not_generated"
                if memo is None
                else "current"
                if memo_is_current
                else "stale"
            ),
        }

    def source_freshness(self) -> dict[str, object]:
        now = pendulum.now("UTC")
        memo_fingerprint_rows = self._query(
            """
            SELECT data_fingerprint
            FROM research_memos
            WHERE pipeline_id = 'TGP'
            ORDER BY generated_at DESC, research_memo_id DESC
            LIMIT 1
            """
        )
        memo_fingerprint = (
            str(memo_fingerprint_rows[0]["data_fingerprint"])
            if memo_fingerprint_rows
            else None
        )
        current_fingerprint = (
            str(build_tgp_research_packet(self.database_path)["data_fingerprint"])
            if memo_fingerprint is not None
            else None
        )
        rows = self._query(
            """
            WITH latest_notice AS (
                SELECT
                    page.artifact_id,
                    page.observed_at AS collected_at,
                    max(observation.posted_at) AS source_as_of,
                    page.parsed_row_count AS row_count
                FROM notice_index_pages AS page
                JOIN notice_index_observations AS observation USING (artifact_id)
                WHERE page.pipeline_id = 'TGP'
                GROUP BY page.artifact_id, page.observed_at, page.parsed_row_count
                ORDER BY page.observed_at DESC, page.artifact_id DESC
                LIMIT 1
            ), latest_report AS (
                SELECT
                    artifact_id,
                    observed_at AS collected_at,
                    posted_at AS source_as_of,
                    station_period_rows AS row_count
                FROM tgp_outage_report_summary
                ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
                LIMIT 1
            ), latest_capacity_run AS (
                SELECT artifact.run_id
                FROM capacity_exports AS export
                JOIN source_artifacts AS artifact USING (artifact_id)
                WHERE export.pipeline_id = 'TGP'
                ORDER BY export.observed_at DESC, export.artifact_id DESC
                LIMIT 1
            ), latest_capacity AS (
                SELECT
                    max(export.observed_at) AS collected_at,
                    max(export.source_posted_at) AS source_as_of,
                    sum(export.parsed_row_count) AS row_count
                FROM capacity_exports AS export
                JOIN source_artifacts AS artifact USING (artifact_id)
                JOIN latest_capacity_run USING (run_id)
            ), latest_locations AS (
                SELECT
                    observed_at AS collected_at,
                    source_as_of,
                    row_count
                FROM location_exports
                WHERE pipeline_id = 'TGP'
                ORDER BY observed_at DESC, artifact_id DESC
                LIMIT 1
            ), latest_memo AS (
                SELECT
                    generated_at AS collected_at,
                    decision_at AS source_as_of,
                    1 AS row_count
                FROM research_memos
                WHERE pipeline_id = 'TGP'
                ORDER BY generated_at DESC, research_memo_id DESC
                LIMIT 1
            ), latest_storage AS (
                SELECT
                    observation.artifact_id,
                    artifact.received_at AS collected_at,
                    max(observation.available_at) AS source_as_of,
                    count(*) AS row_count
                FROM market_observations AS observation
                JOIN source_artifacts AS artifact USING (artifact_id)
                WHERE observation.series_code LIKE 'EIA_WNGSR:%'
                GROUP BY observation.artifact_id, artifact.received_at
                ORDER BY artifact.received_at DESC, observation.artifact_id DESC
                LIMIT 1
            ), latest_spot AS (
                SELECT
                    observation.artifact_id,
                    artifact.received_at AS collected_at,
                    max(coalesce(observation.source_published_at, observation.period_start)) AS source_as_of,
                    count(*) AS row_count
                FROM market_observations AS observation
                JOIN source_artifacts AS artifact USING (artifact_id)
                WHERE observation.observation_type = 'physical_spot'
                GROUP BY observation.artifact_id, artifact.received_at
                ORDER BY artifact.received_at DESC, observation.artifact_id DESC
                LIMIT 1
            ), latest_weather_run AS (
                SELECT run_id
                FROM fetch_runs
                WHERE source_code = 'nws_tgp_degree_days'
                  AND status = 'completed'
                ORDER BY requested_at DESC, run_id DESC
                LIMIT 1
            ), latest_weather AS (
                SELECT
                    max(artifact.received_at) AS collected_at,
                    max(coalesce(observation.source_published_at, observation.available_at)) AS source_as_of,
                    count(*) AS row_count
                FROM market_observations AS observation
                JOIN source_artifacts AS artifact USING (artifact_id)
                JOIN latest_weather_run USING (run_id)
                WHERE observation.observation_type = 'weather_forecast'
            ), latest_futures AS (
                SELECT
                    observation.artifact_id,
                    artifact.received_at AS collected_at,
                    max(coalesce(observation.source_published_at, observation.available_at)) AS source_as_of,
                    count(*) AS row_count
                FROM market_observations AS observation
                JOIN source_artifacts AS artifact USING (artifact_id)
                WHERE observation.observation_type = 'futures_proxy'
                GROUP BY observation.artifact_id, artifact.received_at
                ORDER BY artifact.received_at DESC, observation.artifact_id DESC
                LIMIT 1
            )
            SELECT 'critical_notices' AS source_code, 'Critical notices' AS label,
                   collected_at, source_as_of, row_count, 2 AS stale_after_hours,
                   true AS decision_critical
            FROM latest_notice
            UNION ALL
            SELECT 'outage_report', 'Outage report', collected_at, source_as_of,
                   row_count, 192, true
            FROM latest_report
            UNION ALL
            SELECT 'operational_capacity', 'Operating capacity', collected_at,
                   source_as_of, row_count, 30, true
            FROM latest_capacity
            UNION ALL
            SELECT 'location_reference', 'Location reference', collected_at,
                   source_as_of, row_count, 168, false
            FROM latest_locations
            UNION ALL
            SELECT 'eia_storage', 'EIA storage', collected_at, source_as_of,
                   row_count, 192, false
            FROM latest_storage
            UNION ALL
            SELECT 'henry_hub_spot', 'Henry Hub spot', collected_at,
                   source_as_of, row_count, 192, false
            FROM latest_spot
            UNION ALL
            SELECT 'nws_degree_days', 'Northeast weather', collected_at,
                   source_as_of, row_count, 8, false
            FROM latest_weather
            UNION ALL
            SELECT 'front_month_futures', 'Front-month proxy', collected_at,
                   source_as_of, row_count, 24, false
            FROM latest_futures
            UNION ALL
            SELECT 'ai_memo', 'AI research memo', collected_at, source_as_of,
                   row_count, 30, false
            FROM latest_memo
            """
        )
        for row in rows:
            collected = pendulum.instance(row["collected_at"]).in_timezone("UTC")
            source_as_of = pendulum.instance(row["source_as_of"]).in_timezone("UTC")
            age_hours = max(0.0, (now - collected).total_seconds() / 3600)
            row["collected_at_utc"] = collected.to_iso8601_string()
            row["source_as_of_utc"] = source_as_of.to_iso8601_string()
            row["collection_age_hours"] = round(age_hours, 2)
            row["status"] = (
                "stale" if age_hours > float(row["stale_after_hours"]) else "fresh"
            )
            if row["source_code"] == "ai_memo":
                row["evidence_current"] = memo_fingerprint == current_fingerprint
                if not row["evidence_current"]:
                    row["status"] = "stale"
                    row["status_reason"] = "Newer economic evidence is available."
            del row["collected_at"]
            del row["source_as_of"]
        return {
            "generated_at_utc": now.to_iso8601_string(),
            "sources": rows,
            "all_decision_sources_fresh": all(
                row["status"] == "fresh" for row in rows if row["decision_critical"]
            ),
        }

    def alerts(
        self,
        *,
        scope: str = "latest",
        as_of: pendulum.DateTime | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        if scope not in {"latest", "recent"}:
            scope = "latest"
        collection_rows = self._query(
            """
            WITH latest_notice AS (
                SELECT max(collected_at) AS collected_at
                FROM (
                    SELECT observed_at AS collected_at
                    FROM notice_index_pages
                    WHERE pipeline_id = 'TGP'
                      AND (? IS NULL OR observed_at <= ?)
                    UNION ALL
                    SELECT observed_at AS collected_at
                    FROM notice_version_observations
                    WHERE pipeline_id = 'TGP'
                      AND (? IS NULL OR observed_at <= ?)
                )
            ), latest_report AS (
                SELECT max(observed_at) AS collected_at
                FROM tgp_outage_report_summary
                WHERE (? IS NULL OR observed_at <= ?)
            ), latest_capacity AS (
                SELECT max(observed_at) AS collected_at
                FROM capacity_exports
                WHERE pipeline_id = 'TGP'
                  AND (? IS NULL OR observed_at <= ?)
            )
            SELECT greatest(
                latest_notice.collected_at,
                latest_report.collected_at,
                latest_capacity.collected_at
            ) AS latest_collection_at,
            (SELECT max(decision_at) FROM alerts
             WHERE (? IS NULL OR decision_at <= ?)) AS latest_alert_at
            FROM latest_notice, latest_report, latest_capacity
            """,
            [
                as_of,
                as_of,
                as_of,
                as_of,
                as_of,
                as_of,
                as_of,
                as_of,
                as_of,
                as_of,
            ],
        )
        latest_collection_at = (
            collection_rows[0]["latest_collection_at"] if collection_rows else None
        )
        latest_alert_at = (
            collection_rows[0]["latest_alert_at"] if collection_rows else None
        )
        material_change_in_latest_pull = (
            latest_collection_at is not None
            and latest_alert_at is not None
            and latest_collection_at == latest_alert_at
        )
        rows = self._query(
            """
            SELECT
                alert.alert_id,
                alert.event_id,
                strftime(
                    alert.decision_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS decision_at_utc,
                alert.change_type,
                alert.severity_score,
                CAST(alert.score_components AS VARCHAR) AS score_components,
                alert.headline,
                alert.explanation,
                alert.confidence,
                CAST(alert.evidence AS VARCHAR) AS evidence,
                event.event_type,
                event.current_status,
                event.impact_channel,
                event.summary,
                strftime(
                    event.effective_start AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS effective_start_utc,
                strftime(
                    event.effective_end AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS effective_end_utc
            FROM alerts AS alert
            JOIN events AS event USING (event_id)
            WHERE (? IS NULL OR alert.decision_at <= ?)
              AND (? = 'recent' OR alert.decision_at = ?)
            ORDER BY alert.decision_at DESC, alert.severity_score DESC,
                     alert.alert_id
            LIMIT ?
            """,
            [
                as_of,
                as_of,
                scope,
                latest_alert_at if material_change_in_latest_pull else None,
                limit,
            ],
        )
        summary_rows = self._query(
            """
            SELECT
                count(*) AS alert_count,
                count(*) FILTER (
                    WHERE event.event_type = 'critical_notice'
                ) AS critical_notice_count,
                count(*) FILTER (
                    WHERE event.event_type = 'notice_content_revision'
                ) AS notice_content_revision_count,
                count(*) FILTER (
                    WHERE event.event_type = 'outage_capacity_revision'
                ) AS outage_revision_count,
                count(*) FILTER (
                    WHERE event.event_type = 'capacity_snapshot_change'
                ) AS capacity_change_count
            FROM alerts AS alert
            JOIN events AS event USING (event_id)
            WHERE (? IS NULL OR alert.decision_at <= ?)
              AND (? = 'recent' OR alert.decision_at = ?)
            """,
            [
                as_of,
                as_of,
                scope,
                latest_alert_at if material_change_in_latest_pull else None,
            ],
        )
        for row in rows:
            row["score_components"] = json.loads(str(row["score_components"]))
            row["evidence"] = json.loads(str(row["evidence"]))
            normalize_alert_semantics(row)
            row["severity_band"] = (
                "high"
                if row["severity_score"] >= 70
                else "medium"
                if row["severity_score"] >= 40
                else "low"
            )
        summary = summary_rows[0]
        counts = {
            "critical_notice": summary["critical_notice_count"],
            "notice_content_revision": summary["notice_content_revision_count"],
            "outage_capacity_revision": summary["outage_revision_count"],
            "capacity_snapshot_change": summary["capacity_change_count"],
        }
        return {
            "scope": scope,
            "as_of_utc": as_of.to_iso8601_string() if as_of else None,
            "latest_collection_at_utc": (
                pendulum.instance(latest_collection_at)
                .in_timezone("UTC")
                .to_iso8601_string()
                if latest_collection_at is not None
                else None
            ),
            "latest_alert_at_utc": (
                pendulum.instance(latest_alert_at)
                .in_timezone("UTC")
                .to_iso8601_string()
                if latest_alert_at is not None
                else None
            ),
            "material_change_in_latest_pull": material_change_in_latest_pull,
            "alert_count": summary["alert_count"],
            "returned_item_count": len(rows),
            "counts_by_event_type": counts,
            "items": rows,
        }

    def operational_capacity(
        self,
        *,
        search: str = "",
        capacity_kind: str = "all",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if capacity_kind not in {"all", "segment", "delivery", "receipt"}:
            capacity_kind = "all"
        return self._query(
            """
            WITH latest_run AS (
                SELECT artifact.run_id
                FROM capacity_exports AS export
                JOIN source_artifacts AS artifact USING (artifact_id)
                WHERE export.pipeline_id = 'TGP'
                ORDER BY export.observed_at DESC, export.artifact_id DESC
                LIMIT 1
            )
            SELECT
                observation.capacity_kind,
                observation.point_role,
                CAST(observation.gas_day AS VARCHAR) AS gas_day,
                observation.cycle,
                observation.operator_location_id,
                observation.operator_segment_id,
                observation.location_name,
                observation.zone,
                observation.flow_direction,
                CAST(observation.design_capacity_dth_per_day AS BIGINT)
                    AS design_capacity_dth_per_day,
                CAST(observation.operating_capacity_dth_per_day AS BIGINT)
                    AS operating_capacity_dth_per_day,
                CAST(observation.scheduled_quantity_dth_per_day AS BIGINT)
                    AS scheduled_quantity_dth_per_day,
                CAST(observation.available_capacity_dth_per_day AS BIGINT)
                    AS available_capacity_dth_per_day,
                round(
                    observation.scheduled_quantity_dth_per_day * 100.0 /
                    nullif(observation.operating_capacity_dth_per_day, 0),
                    1
                ) AS scheduled_pct_of_operating,
                observation.available_reconciles,
                observation.quantity_reason,
                strftime(
                    observation.source_posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS source_posted_at_utc,
                strftime(
                    observation.observed_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS observed_at_utc,
                observation.artifact_id AS evidence_id
            FROM capacity_observations AS observation
            JOIN source_artifacts AS artifact
              ON artifact.artifact_id = observation.artifact_id
            JOIN latest_run ON latest_run.run_id = artifact.run_id
            WHERE (
                    ? = 'all'
                    OR (? = 'segment' AND observation.capacity_kind = 'segment')
                    OR observation.point_role = ?
                )
              AND (
                    ? = ''
                    OR observation.location_name ILIKE '%' || ? || '%'
                    OR observation.operator_location_id ILIKE '%' || ? || '%'
                    OR observation.operator_segment_id ILIKE '%' || ? || '%'
                    OR observation.zone ILIKE '%' || ? || '%'
                )
            ORDER BY
                observation.available_capacity_dth_per_day ASC,
                observation.scheduled_quantity_dth_per_day DESC,
                observation.operator_segment_id,
                observation.location_name
            LIMIT ?
            """,
            [
                capacity_kind,
                capacity_kind,
                capacity_kind,
                search,
                search,
                search,
                search,
                search,
                limit,
            ],
        )

    def map_data(
        self,
        *,
        report_notice_id: str | None = None,
    ) -> dict[str, object]:
        boundary_rows = self._query(
            """
            SELECT CAST(geojson AS VARCHAR) AS geojson
            FROM map_reference_layers
            WHERE layer_code = 'census_tgp_states_20m'
            ORDER BY observed_at DESC
            LIMIT 1
            """
        )
        state_boundaries = (
            json.loads(str(boundary_rows[0]["geojson"]))
            if boundary_rows
            else {"type": "FeatureCollection", "features": []}
        )
        county_rows = self._query(
            """
            SELECT
                state_abbreviation,
                county_name,
                latitude,
                longitude,
                count(*) AS location_count,
                count(*) FILTER (WHERE flow_role = 'R') AS receipt_count,
                count(*) FILTER (WHERE flow_role = 'D') AS delivery_count,
                count(*) FILTER (WHERE flow_role = 'B') AS bidirectional_count,
                string_agg(DISTINCT coalesce(receipt_zone, delivery_zone), '|') AS zones,
                string_agg(DISTINCT location_type, '|') AS location_types,
                string_agg(DISTINCT operator_segment_id, '|') AS segment_ids,
                string_agg(location_name, '|') AS location_names,
                min(coordinate_precision) AS coordinate_precision,
                min(coordinate_method) AS coordinate_method
            FROM tgp_location_map
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            GROUP BY state_abbreviation, county_name, latitude, longitude
            ORDER BY state_abbreviation, county_name
            """
        )
        for row in county_rows:
            for key in ("zones", "location_types", "segment_ids"):
                row[key] = sorted(
                    value for value in str(row[key] or "").split("|") if value
                )
            names = sorted(
                value
                for value in str(row.pop("location_names") or "").split("|")
                if value
            )
            row["sample_location_names"] = names[:6]

        constraints = self._query(
            """
            WITH selected_report AS (
                SELECT artifact_id, notice_id, report_updated_on, observed_at
                FROM tgp_outage_report_summary
                WHERE (? IS NULL OR notice_id = ?)
                ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
                LIMIT 1
            ),
            segment_impacts AS (
                SELECT
                    impact.operator_segment_id,
                    impact.station_label,
                    impact.flow_direction,
                    max(impact.calculated_reduction_dth_per_day) AS max_reduction_dth_per_day,
                    min(impact.period_start) AS first_period_start,
                    max(impact.period_end) AS last_period_end,
                    count(*) AS constrained_period_count,
                    arg_max(impact.outage_description, impact.calculated_reduction_dth_per_day) AS outage_description,
                    max(selected_report.notice_id) AS notice_id,
                    max(selected_report.report_updated_on) AS report_updated_on
                FROM outage_impact_observations AS impact
                JOIN selected_report
                  ON selected_report.artifact_id = impact.artifact_id
                WHERE impact.calculated_reduction_dth_per_day > 0
                  AND impact.operator_segment_id IS NOT NULL
                GROUP BY
                    impact.operator_segment_id,
                    impact.station_label,
                    impact.flow_direction
            ),
            segment_counties AS (
                SELECT DISTINCT
                    operator_segment_id,
                    state_abbreviation,
                    county_name,
                    latitude,
                    longitude,
                    coalesce(receipt_zone, delivery_zone) AS zone
                FROM tgp_location_map
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            )
            SELECT
                impact.operator_segment_id,
                impact.station_label,
                impact.flow_direction,
                impact.max_reduction_dth_per_day,
                CAST(impact.first_period_start AS VARCHAR) AS first_period_start,
                CAST(impact.last_period_end AS VARCHAR) AS last_period_end,
                impact.constrained_period_count,
                impact.outage_description,
                impact.notice_id,
                CAST(impact.report_updated_on AS VARCHAR) AS report_updated_on,
                avg(county.latitude) AS latitude,
                avg(county.longitude) AS longitude,
                count(county.latitude) AS county_count,
                string_agg(DISTINCT county.state_abbreviation, '|') AS states,
                string_agg(DISTINCT county.county_name, '|') AS counties,
                string_agg(DISTINCT county.zone, '|') AS zones,
                CASE
                    WHEN count(county.latitude) = 0 THEN 'unmapped'
                    WHEN count(county.latitude) = 1 THEN 'county'
                    ELSE 'multi_county_segment_anchor'
                END AS coordinate_precision
            FROM segment_impacts AS impact
            LEFT JOIN segment_counties AS county
              ON county.operator_segment_id = impact.operator_segment_id
            GROUP BY
                impact.operator_segment_id,
                impact.station_label,
                impact.flow_direction,
                impact.max_reduction_dth_per_day,
                impact.first_period_start,
                impact.last_period_end,
                impact.constrained_period_count,
                impact.outage_description,
                impact.notice_id,
                impact.report_updated_on
            ORDER BY impact.max_reduction_dth_per_day DESC
            """,
            [report_notice_id, report_notice_id],
        )
        for row in constraints:
            for key in ("states", "counties", "zones"):
                row[key] = sorted(
                    value for value in str(row[key] or "").split("|") if value
                )
            row["source_url"] = NOTICE_URL.format(notice_id=row["notice_id"])

        segments = self._query(
            """
            WITH selected_report AS (
                SELECT artifact_id, notice_id, report_updated_on
                FROM tgp_outage_report_summary
                WHERE (? IS NULL OR notice_id = ?)
                ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
                LIMIT 1
            ),
            segment_counties AS (
                SELECT DISTINCT
                    operator_segment_id,
                    state_abbreviation,
                    county_name,
                    latitude,
                    longitude,
                    coalesce(receipt_zone, delivery_zone) AS zone
                FROM tgp_location_map
                WHERE operator_segment_id IS NOT NULL
                  AND latitude IS NOT NULL
                  AND longitude IS NOT NULL
            ),
            segment_reference AS (
                SELECT
                    operator_segment_id,
                    avg(latitude) AS latitude,
                    avg(longitude) AS longitude,
                    count(*) AS county_count,
                    string_agg(DISTINCT state_abbreviation, '|') AS states,
                    string_agg(DISTINCT county_name, '|') AS counties,
                    string_agg(DISTINCT zone, '|') AS zones
                FROM segment_counties
                GROUP BY operator_segment_id
            ),
            segment_locations AS (
                SELECT
                    operator_segment_id,
                    count(*) AS location_count,
                    count(DISTINCT state_abbreviation || ':' || county_name)
                        AS reference_county_count,
                    string_agg(DISTINCT state_abbreviation, '|') AS states,
                    string_agg(DISTINCT county_name, '|') AS counties,
                    string_agg(
                        DISTINCT coalesce(receipt_zone, delivery_zone),
                        '|'
                    ) AS zones,
                    string_agg(DISTINCT location_name, '|') AS location_names
                FROM tgp_location_map
                WHERE operator_segment_id IS NOT NULL
                GROUP BY operator_segment_id
            ),
            planned AS (
                SELECT
                    impact.operator_segment_id,
                    max(impact.calculated_reduction_dth_per_day)
                        AS planned_reduction_dth_per_day,
                    arg_max(
                        round(
                            impact.calculated_reduction_dth_per_day * 100.0 /
                            nullif(impact.nominal_capacity_dth_per_day, 0),
                            1
                        ),
                        impact.calculated_reduction_dth_per_day
                    ) AS planned_reduction_pct,
                    arg_max(
                        impact.nominal_capacity_dth_per_day,
                        impact.calculated_reduction_dth_per_day
                    ) AS planned_nominal_capacity_dth_per_day,
                    arg_max(
                        impact.operating_capacity_dth_per_day,
                        impact.calculated_reduction_dth_per_day
                    ) AS planned_operating_capacity_dth_per_day,
                    arg_max(
                        impact.station_label,
                        impact.calculated_reduction_dth_per_day
                    ) AS planned_station_label,
                    arg_max(
                        impact.flow_direction,
                        impact.calculated_reduction_dth_per_day
                    ) AS planned_flow_direction,
                    min(impact.period_start) AS planned_first_period_start,
                    max(impact.period_end) AS planned_last_period_end,
                    arg_max(
                        impact.outage_description,
                        impact.calculated_reduction_dth_per_day
                    ) AS planned_outage_description,
                    max(selected_report.notice_id) AS report_notice_id,
                    max(selected_report.report_updated_on) AS report_updated_on
                FROM outage_impact_observations AS impact
                JOIN selected_report
                  ON selected_report.artifact_id = impact.artifact_id
                WHERE impact.operator_segment_id IS NOT NULL
                  AND impact.calculated_reduction_dth_per_day > 0
                GROUP BY impact.operator_segment_id
            ),
            impact_latest AS (
                SELECT assessment.*
                FROM tgp_transport_impact_assessments AS assessment
                JOIN selected_report
                  ON selected_report.artifact_id = assessment.report_artifact_id
                QUALIFY row_number() OVER (
                    PARTITION BY assessment.source_table_index,
                                 assessment.source_row_index,
                                 assessment.period_start,
                                 assessment.period_end
                    ORDER BY assessment.baseline_source_posted_at DESC NULLS LAST,
                             assessment.calculated_at DESC,
                             assessment.assessment_id DESC
                ) = 1
            ),
            risk_ranked AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY operator_segment_id
                        ORDER BY
                            conditional_scheduled_shortfall_dth_per_day DESC,
                            period_start,
                            period_end,
                            station_label
                    ) AS peak_rank
                FROM impact_latest
                WHERE research_status = 'research_scenario'
            ),
            transport_risk AS (
                SELECT
                    operator_segment_id,
                    max(conditional_scheduled_shortfall_dth_per_day)
                        AS risk_shortfall_dth_per_day,
                    count(*) FILTER (
                        WHERE research_status = 'research_scenario'
                    ) AS risk_period_count,
                    max(station_label) FILTER (WHERE peak_rank = 1)
                        AS risk_station_label,
                    max(tgp_zone) FILTER (WHERE peak_rank = 1) AS risk_zone,
                    max(outage_flow_direction) FILTER (WHERE peak_rank = 1)
                        AS risk_flow_direction,
                    CAST(max(period_start) FILTER (WHERE peak_rank = 1) AS VARCHAR)
                        AS risk_period_start,
                    CAST(max(period_end) FILTER (WHERE peak_rank = 1) AS VARCHAR)
                        AS risk_period_end
                FROM risk_ranked
                GROUP BY operator_segment_id
            ),
            revision AS (
                SELECT
                    item.operator_segment_id,
                    arg_max(
                        item.operating_capacity_change_dth_per_day,
                        abs(item.operating_capacity_change_dth_per_day)
                    ) AS revision_change_dth_per_day,
                    arg_max(
                        round(
                            item.operating_capacity_change_dth_per_day * 100.0 /
                            nullif(item.prior_operating_capacity_dth_per_day, 0),
                            1
                        ),
                        abs(item.operating_capacity_change_dth_per_day)
                    ) AS revision_change_pct,
                    arg_max(
                        item.prior_operating_capacity_dth_per_day,
                        abs(item.operating_capacity_change_dth_per_day)
                    ) AS revision_prior_capacity_dth_per_day,
                    arg_max(
                        item.operating_capacity_dth_per_day,
                        abs(item.operating_capacity_change_dth_per_day)
                    ) AS revision_current_capacity_dth_per_day,
                    arg_max(
                        item.station_label,
                        abs(item.operating_capacity_change_dth_per_day)
                    ) AS revision_station_label,
                    arg_max(
                        item.flow_direction,
                        abs(item.operating_capacity_change_dth_per_day)
                    ) AS revision_flow_direction,
                    arg_max(
                        item.period_start,
                        abs(item.operating_capacity_change_dth_per_day)
                    ) AS revision_period_start,
                    arg_max(
                        item.period_end,
                        abs(item.operating_capacity_change_dth_per_day)
                    ) AS revision_period_end,
                    count(*) FILTER (
                        WHERE item.operating_capacity_change_dth_per_day < 0
                    ) AS worsened_period_count,
                    count(*) FILTER (
                        WHERE item.operating_capacity_change_dth_per_day > 0
                    ) AS improved_period_count
                FROM tgp_outage_capacity_revisions AS item
                JOIN selected_report
                  ON selected_report.artifact_id = item.artifact_id
                WHERE item.operator_segment_id IS NOT NULL
                  AND item.operating_capacity_change_dth_per_day != 0
                GROUP BY item.operator_segment_id
            ),
            latest_capacity_run AS (
                SELECT artifact.run_id
                FROM capacity_exports AS export
                JOIN source_artifacts AS artifact USING (artifact_id)
                WHERE export.pipeline_id = 'TGP'
                ORDER BY export.observed_at DESC, export.artifact_id DESC
                LIMIT 1
            ),
            capacity_direction AS (
                SELECT
                    observation.operator_segment_id,
                    observation.flow_direction,
                    observation.design_capacity_dth_per_day,
                    observation.operating_capacity_dth_per_day,
                    observation.scheduled_quantity_dth_per_day,
                    observation.available_capacity_dth_per_day,
                    observation.scheduled_quantity_dth_per_day * 100.0 /
                        nullif(observation.operating_capacity_dth_per_day, 0)
                        AS tightness_pct,
                    observation.source_posted_at
                FROM capacity_observations AS observation
                JOIN source_artifacts AS artifact USING (artifact_id)
                JOIN latest_capacity_run USING (run_id)
                WHERE observation.capacity_kind = 'segment'
                  AND observation.operator_segment_id IS NOT NULL
                  AND observation.operating_capacity_dth_per_day > 0
            ),
            capacity AS (
                SELECT
                    operator_segment_id,
                    round(max(tightness_pct), 1) AS tightness_pct,
                    arg_max(flow_direction, tightness_pct)
                        AS tightness_flow_direction,
                    arg_max(design_capacity_dth_per_day, tightness_pct)
                        AS tightness_design_capacity_dth_per_day,
                    arg_max(operating_capacity_dth_per_day, tightness_pct)
                        AS tightness_operating_capacity_dth_per_day,
                    arg_max(scheduled_quantity_dth_per_day, tightness_pct)
                        AS tightness_scheduled_quantity_dth_per_day,
                    arg_max(available_capacity_dth_per_day, tightness_pct)
                        AS tightness_available_capacity_dth_per_day,
                    max(source_posted_at) AS capacity_source_posted_at
                FROM capacity_direction
                GROUP BY operator_segment_id
            )
            SELECT
                location.operator_segment_id,
                reference.latitude,
                reference.longitude,
                coalesce(reference.county_count, 0) AS county_count,
                location.reference_county_count,
                location.location_count,
                location.states,
                location.counties,
                location.zones,
                location.location_names,
                planned.planned_reduction_dth_per_day,
                planned.planned_reduction_pct,
                planned.planned_nominal_capacity_dth_per_day,
                planned.planned_operating_capacity_dth_per_day,
                planned.planned_station_label,
                planned.planned_flow_direction,
                CAST(planned.planned_first_period_start AS VARCHAR)
                    AS planned_first_period_start,
                CAST(planned.planned_last_period_end AS VARCHAR)
                    AS planned_last_period_end,
                planned.planned_outage_description,
                planned.report_notice_id,
                CAST(planned.report_updated_on AS VARCHAR) AS report_updated_on,
                transport_risk.risk_shortfall_dth_per_day,
                transport_risk.risk_period_count,
                transport_risk.risk_station_label,
                transport_risk.risk_zone,
                transport_risk.risk_flow_direction,
                transport_risk.risk_period_start,
                transport_risk.risk_period_end,
                (SELECT notice_id FROM selected_report)
                    AS selected_report_notice_id,
                revision.revision_change_dth_per_day,
                revision.revision_change_pct,
                revision.revision_prior_capacity_dth_per_day,
                revision.revision_current_capacity_dth_per_day,
                revision.revision_station_label,
                revision.revision_flow_direction,
                CAST(revision.revision_period_start AS VARCHAR)
                    AS revision_period_start,
                CAST(revision.revision_period_end AS VARCHAR)
                    AS revision_period_end,
                revision.worsened_period_count,
                revision.improved_period_count,
                capacity.tightness_pct,
                capacity.tightness_flow_direction,
                capacity.tightness_design_capacity_dth_per_day,
                capacity.tightness_operating_capacity_dth_per_day,
                capacity.tightness_scheduled_quantity_dth_per_day,
                capacity.tightness_available_capacity_dth_per_day,
                strftime(
                    capacity.capacity_source_posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS capacity_source_posted_at_utc
            FROM segment_locations AS location
            LEFT JOIN segment_reference AS reference USING (operator_segment_id)
            LEFT JOIN planned USING (operator_segment_id)
            LEFT JOIN transport_risk USING (operator_segment_id)
            LEFT JOIN revision USING (operator_segment_id)
            LEFT JOIN capacity USING (operator_segment_id)
            ORDER BY location.operator_segment_id
            """,
            [report_notice_id, report_notice_id],
        )
        for row in segments:
            for key in ("states", "counties", "zones"):
                row[key] = sorted(
                    value for value in str(row[key] or "").split("|") if value
                )
            names = sorted(
                value
                for value in str(row.pop("location_names") or "").split("|")
                if value
            )
            row["sample_location_names"] = names[:6]
            row["coordinate_precision"] = "multi_county_segment_anchor"
            row["source_url"] = (
                NOTICE_URL.format(notice_id=row["selected_report_notice_id"])
                if row["selected_report_notice_id"]
                else None
            )

        coverage_rows = self._query(
            """
            SELECT
                count(*) AS location_count,
                count(*) FILTER (WHERE latitude IS NOT NULL) AS geocoded_location_count,
                count(DISTINCT state_abbreviation || ':' || county_name) AS county_count,
                count(DISTINCT operator_segment_id) AS segment_count,
                count(DISTINCT receipt_zone) AS zone_count
            FROM tgp_location_map
            """
        )
        coverage = coverage_rows[0] if coverage_rows else {}
        coverage["coordinate_precision"] = "county"
        coverage["coordinate_warning"] = (
            "Markers use Census county internal points, not exact facility coordinates. "
            "Segment anchors summarize all mapped counties for that native segment."
        )
        coverage["segment_anchor_count"] = sum(
            row["latitude"] is not None and row["longitude"] is not None
            for row in segments
        )
        coverage["planned_reduction_segment_count"] = sum(
            row["planned_reduction_dth_per_day"] is not None for row in segments
        )
        coverage["revised_segment_count"] = sum(
            row["revision_change_dth_per_day"] is not None for row in segments
        )
        coverage["tightness_segment_count"] = sum(
            row["tightness_pct"] is not None for row in segments
        )
        return {
            "coverage": coverage,
            "state_boundaries": state_boundaries,
            "counties": county_rows,
            "constraints": constraints,
            "segments": segments,
            "sources": {
                "operator_locations": (
                    "https://pipeline2.kindermorgan.com/LocationDataDownload/"
                    "LocDataDwnld.aspx?code=TGP"
                ),
                "operator_system_map": (
                    "https://pipeline2.kindermorgan.com/Documents/PDFView.aspx"
                    "?code=TGP&fname=TGP_System_Map.pdf"
                ),
                "operator_segment_pin_map": (
                    "https://pipeline2.kindermorgan.com/Documents/TGP/"
                    "TGP_Segment_-_Pin_Map-20260721125239.pdf"
                ),
                "county_coordinates": (
                    "https://www.census.gov/geographies/reference-files/"
                    "time-series/geo/gazetteer-files.2025.html"
                ),
            },
        }


class RefreshManager:
    """Run one bounded local refresh without blocking the HTTP request thread."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        runner: Callable[..., ScheduledCollectionSummary] = run_scheduled_collection,
    ) -> None:
        self.database_path = Path(database_path)
        self.runner = runner
        self._lock = Lock()
        self._state: dict[str, object] = {
            "status": "idle",
            "message": "Ready to pull the latest public data.",
            "started_at_utc": None,
            "completed_at_utc": None,
            "source_failures": [],
            "insights_status": None,
        }

    def status(self) -> dict[str, object]:
        with self._lock:
            return dict(self._state)

    def start(self) -> tuple[bool, dict[str, object]]:
        with self._lock:
            if self._state["status"] == "running":
                return False, dict(self._state)
            self._state = {
                "status": "running",
                "message": "Pulling notices, capacity, EIA, and NWS data…",
                "started_at_utc": pendulum.now("UTC").to_iso8601_string(),
                "completed_at_utc": None,
                "source_failures": [],
                "insights_status": None,
            }
            current = dict(self._state)
        Thread(target=self._run, name="pipeline-pulse-refresh", daemon=True).start()
        return True, current

    def _run(self) -> None:
        try:
            summary = self.runner(
                mode="refresh",
                database_path=self.database_path,
                raw_root=self.database_path.parent / "raw",
                lock_path=self.database_path.parent / "pipeline-pulse.lock",
                curated_output_path=(
                    self.database_path.parent
                    / "curated"
                    / "tgp_critical_notice_index.csv"
                ),
            )
            collection = summary.collection or {}
            source_failures = [
                name
                for name in ("eia_storage", "henry_hub_spot", "nws_degree_days")
                if isinstance(collection.get(name), dict)
                and collection[name].get("status") == "failed"
            ]
            insights = collection.get("insights")
            insights_status = (
                insights.get("status") if isinstance(insights, dict) else None
            )
            if summary.status == "skipped_locked":
                status = "skipped_locked"
                message = "Another collection is already running. Try again shortly."
            elif source_failures:
                status = "completed"
                message = (
                    "Pipeline data updated; some market context sources failed: "
                    + ", ".join(source_failures)
                    + "."
                )
            else:
                status = "completed"
                message = "Latest public data and derived analysis are ready."
            with self._lock:
                self._state = {
                    "status": status,
                    "message": message,
                    "started_at_utc": summary.started_at,
                    "completed_at_utc": summary.completed_at,
                    "source_failures": source_failures,
                    "insights_status": insights_status,
                }
        except Exception as exc:  # noqa: BLE001 - background boundary reports failure
            with self._lock:
                self._state = {
                    **self._state,
                    "status": "failed",
                    "message": f"Refresh failed: {type(exc).__name__}: {exc}",
                    "completed_at_utc": pendulum.now("UTC").to_iso8601_string(),
                }


class PipelinePulseHandler(BaseHTTPRequestHandler):
    read_model: TgpReadModel
    refresh_manager: RefreshManager

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, filename: str) -> None:
        path = UI_ROOT / filename
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        content_type = (
            f"{mime_type}; charset=utf-8"
            if mime_type.startswith("text/")
            or mime_type in {"application/javascript", "application/json"}
            else mime_type
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_csv_download(self, dataset_id: str) -> None:
        definition = DOWNLOAD_DATASETS.get(dataset_id)
        if definition is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path = self.read_model.database_path.parent / "curated" / definition["filename"]
        if not path.is_file():
            self._send_json(
                {
                    "error": "export_not_found",
                    "message": "Run pipeline-pulse export-tgp-mvp to create this CSV.",
                },
                HTTPStatus.NOT_FOUND,
            )
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header(
            "Content-Disposition", f'attachment; filename="{definition["filename"]}"'
        )
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        request = urlparse(self.path)
        parameters = parse_qs(request.query)
        search = parameters.get("search", [""])[0].strip()
        limit = _bounded_limit(parameters.get("limit", [None])[0])
        try:
            as_of = _parse_as_of(parameters.get("as_of", [None])[0])
            report_notice_id = parameters.get("report", [None])[0]
            if request.path == "/api/catalog":
                self._send_json(self.read_model.data_catalog())
            elif request.path == "/api/refresh":
                self._send_json(self.refresh_manager.status())
            elif request.path.startswith("/api/download/") and request.path.endswith(
                ".csv"
            ):
                dataset_id = request.path.rsplit("/", 1)[-1][:-4]
                self._send_csv_download(dataset_id)
            elif request.path == "/api/overview":
                self._send_json(self.read_model.overview())
            elif request.path == "/api/reports":
                self._send_json(self.read_model.reports())
            elif request.path == "/api/constraints":
                self._send_json(
                    self.read_model.constraints(
                        search=search,
                        report_kind=parameters.get("kind", ["all"])[0],
                        report_notice_id=report_notice_id,
                        as_of=as_of,
                        limit=limit,
                    )
                )
            elif request.path == "/api/revisions":
                self._send_json(
                    self.read_model.revisions(
                        search=search,
                        direction=parameters.get("direction", ["all"])[0],
                        report_notice_id=report_notice_id,
                        as_of=as_of,
                        limit=limit,
                    )
                )
            elif request.path == "/api/notices":
                self._send_json(
                    self.read_model.notices(search=search, as_of=as_of, limit=limit)
                )
            elif request.path == "/api/market-context":
                self._send_json(self.read_model.market_context(as_of=as_of))
            elif request.path == "/api/market-state":
                raw_days = parameters.get("days", ["30"])[0]
                try:
                    horizon_days = int(raw_days)
                except ValueError as exc:
                    raise ValueError("days must be an integer from 7 to 90") from exc
                self._send_json(
                    self.read_model.market_state(
                        as_of=as_of,
                        horizon_days=horizon_days,
                    )
                )
            elif request.path == "/api/transport-impacts":
                self._send_json(
                    self.read_model.transport_impacts(
                        search=search,
                        status=parameters.get("status", ["all"])[0],
                        report_notice_id=report_notice_id,
                        limit=limit,
                    )
                )
            elif request.path == "/api/research-brief":
                self._send_json(self.read_model.research_brief())
            elif request.path == "/api/source-freshness":
                self._send_json(self.read_model.source_freshness())
            elif request.path == "/api/alerts":
                self._send_json(
                    self.read_model.alerts(
                        scope=parameters.get("scope", ["latest"])[0],
                        as_of=as_of,
                        limit=limit,
                    )
                )
            elif request.path == "/api/operational-capacity":
                self._send_json(
                    self.read_model.operational_capacity(
                        search=search,
                        capacity_kind=parameters.get("kind", ["all"])[0],
                        limit=limit,
                    )
                )
            elif request.path == "/api/map":
                self._send_json(
                    self.read_model.map_data(report_notice_id=report_notice_id)
                )
            elif request.path.startswith("/api/notices/") and request.path.endswith(
                "/history"
            ):
                notice_id = request.path.split("/")[-2]
                history = self.read_model.notice_history(notice_id, as_of=as_of)
                if history is None:
                    self._send_json(
                        {"error": "notice not found"},
                        HTTPStatus.NOT_FOUND,
                    )
                else:
                    self._send_json(history)
            elif request.path.startswith("/api/notices/"):
                notice_id = request.path.rsplit("/", 1)[-1]
                notice = self.read_model.notice(notice_id, as_of=as_of)
                if notice is None:
                    self._send_json(
                        {"error": "notice not found"},
                        HTTPStatus.NOT_FOUND,
                    )
                else:
                    self._send_json(notice)
            elif request.path in {"/", "/index.html"}:
                self._send_static("index.html")
            elif request.path == "/styles.css":
                self._send_static("styles.css")
            elif request.path == "/app.js":
                self._send_static("app.js")
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json(
                {"error": type(exc).__name__, "message": str(exc)},
                HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:  # noqa: BLE001 - HTTP boundary returns JSON errors
            self._send_json(
                {"error": type(exc).__name__, "message": str(exc)},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_POST(self) -> None:
        request = urlparse(self.path)
        if request.path != "/api/refresh":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if (
            not ip_address(self.client_address[0]).is_loopback
            or self.headers.get("X-Pipeline-Pulse") != "refresh"
        ):
            self._send_json(
                {
                    "error": "refresh_forbidden",
                    "message": "Refresh is available only from the local application.",
                },
                HTTPStatus.FORBIDDEN,
            )
            return
        started, state = self.refresh_manager.start()
        self._send_json(
            state,
            HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
        )


def serve(
    database_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    if not Path(database_path).is_file():
        raise FileNotFoundError(f"DuckDB database does not exist: {database_path}")
    handler = type(
        "ConfiguredPipelinePulseHandler",
        (PipelinePulseHandler,),
        {
            "read_model": TgpReadModel(database_path),
            "refresh_manager": RefreshManager(database_path),
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Pipeline Pulse is available at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
