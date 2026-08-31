from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import pendulum

from .database import connect_database, initialize_database
from .market_state import build_tgp_daily_market_state
from .quality import build_tgp_quality_report


DEFAULT_INSIGHT_MODEL = "gpt-5.6-terra"
_RAW_EVIDENCE_TOKEN = re.compile(
    r"(?:km_|eia_|nws_|fred_|yahoo_)[a-z0-9_]*:",
    re.IGNORECASE,
)


INSIGHT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "plain_english_summary": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "facts": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["claim", "evidence_ids"],
                "additionalProperties": False,
            },
        },
        "watch_items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "market_channel": {"type": "string"},
                    "scenario": {"type": "string"},
                    "confirmation_needed": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "invalidation": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "research_status": {
                        "type": "string",
                        "enum": [
                            "no_trade_mapping",
                            "monitor",
                            "research_scenario",
                        ],
                    },
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "title",
                    "market_channel",
                    "scenario",
                    "confirmation_needed",
                    "invalidation",
                    "confidence",
                    "research_status",
                    "evidence_ids",
                ],
                "additionalProperties": False,
            },
        },
        "counterevidence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "missing_data": {
            "type": "array",
            "items": {"type": "string"},
        },
        "glossary": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "definition": {"type": "string"},
                },
                "required": ["term", "definition"],
                "additionalProperties": False,
            },
        },
        "overall_confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
    },
    "required": [
        "headline",
        "plain_english_summary",
        "why_it_matters",
        "facts",
        "watch_items",
        "counterevidence",
        "missing_data",
        "glossary",
        "overall_confidence",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class InsightRunSummary:
    agent_run_id: str
    research_memo_id: str
    status: str
    data_fingerprint: str
    headline: str
    overall_confidence: str
    session_path: str
    output_path: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _rows(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[object] | None = None,
) -> list[dict[str, object]]:
    result = connection.execute(query, parameters or [])
    columns = tuple(description[0] for description in result.description)
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def _json_value(value: object) -> object:
    if isinstance(value, (pendulum.Date, pendulum.DateTime)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


_FINGERPRINT_OMITTED_KEYS = {
    "alert_id",
    "artifact_ids",
    "capacity_evidence_id",
    "decision_at_utc",
    "evidence_id",
    "evidence_ids",
    "event_id",
    "observed_at_utc",
    "outage_evidence_id",
    "assessment_id",
    "capacity_artifact_id",
    "report_artifact_id",
}


def _stable_fingerprint_value(value: object) -> object:
    """Remove capture-specific identifiers while retaining economic evidence."""
    if isinstance(value, dict):
        return {
            key: _stable_fingerprint_value(item)
            for key, item in sorted(value.items())
            if key not in _FINGERPRINT_OMITTED_KEYS
        }
    if isinstance(value, list):
        return [_stable_fingerprint_value(item) for item in value]
    return _json_value(value)


def _compact_market_state(value: dict[str, object]) -> dict[str, object]:
    """Keep the agent packet focused while the API retains drill-down rows."""
    day_fields = (
        "date",
        "horizon",
        "active_maintenance_row_count",
        "affected_segment_count",
        "affected_zone_count",
        "modeled_conflict_row_count",
        "modeled_conflict_segment_count",
        "largest_single_reduction_dth_per_day",
        "largest_conditional_shortfall_dth_per_day",
        "peak_station_label",
        "peak_segment_id",
        "peak_zone",
        "peak_direction",
        "peak_segment_states",
        "screen_state",
    )

    def compact_day(day: object) -> dict[str, object] | None:
        if not isinstance(day, dict):
            return None
        return {field: day.get(field) for field in day_fields}

    summary = value.get("summary")
    compact_summary: dict[str, object] | None = None
    if isinstance(summary, dict):
        compact_summary = {
            "headline": summary.get("headline"),
            "explanation": summary.get("explanation"),
            "current_day": compact_day(summary.get("current_day")),
            "near_term_peak": compact_day(summary.get("near_term_peak")),
            "forward_peak": compact_day(summary.get("forward_peak")),
            "peak_day": compact_day(summary.get("peak_day")),
        }
    compact_days = []
    for day in value.get("days", []):
        compact = compact_day(day)
        if compact is not None:
            compact_days.append(compact)
    return {
        key: item
        for key, item in value.items()
        if key not in {"summary", "days", "corridors"}
    } | {
        "summary": compact_summary,
        "days": compact_days,
        "corridors": list(value.get("corridors", []))[:6],
    }


def build_tgp_research_packet(
    database_path: str | Path,
    *,
    decision_at: pendulum.DateTime | None = None,
) -> dict[str, object]:
    decision_time = (decision_at or pendulum.now("UTC")).in_timezone("UTC")
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        latest_run = connection.execute(
            """
            SELECT artifact.run_id
            FROM capacity_exports AS export
            JOIN source_artifacts AS artifact USING (artifact_id)
            WHERE export.pipeline_id = 'TGP'
              AND export.observed_at <= ?
            ORDER BY export.observed_at DESC, export.artifact_id DESC
            LIMIT 1
            """,
            [decision_time],
        ).fetchone()
        if latest_run is None:
            raise RuntimeError("no TGP operational-capacity bundle is available")
        capacity_exports = _rows(
            connection,
            """
            SELECT
                export.artifact_id AS evidence_id,
                export.capacity_kind,
                export.point_role,
                CAST(export.gas_day AS VARCHAR) AS gas_day,
                export.cycle,
                strftime(
                    export.source_posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS source_posted_at_utc,
                strftime(
                    export.observed_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS observed_at_utc,
                export.parsed_row_count,
                export.comments AS operator_caveats
            FROM capacity_exports AS export
            JOIN source_artifacts AS artifact USING (artifact_id)
            WHERE artifact.run_id = ?
            ORDER BY export.capacity_kind, export.point_role
            """,
            [latest_run[0]],
        )
        artifact_ids = [str(row["evidence_id"]) for row in capacity_exports]
        placeholders = ",".join("?" for _ in artifact_ids)
        capacity_stats = _rows(
            connection,
            f"""
            SELECT
                count(*) AS row_count,
                count(*) FILTER (
                    WHERE capacity_kind = 'segment'
                ) AS segment_row_count,
                count(DISTINCT operator_segment_id) FILTER (
                    WHERE capacity_kind = 'segment'
                ) AS segment_count,
                count(*) FILTER (
                    WHERE operating_capacity_dth_per_day
                        < design_capacity_dth_per_day
                ) AS operating_below_design_count,
                count(*) FILTER (
                    WHERE available_capacity_dth_per_day = 0
                ) AS zero_available_count,
                count(*) FILTER (
                    WHERE operating_capacity_dth_per_day > 0
                      AND scheduled_quantity_dth_per_day
                          >= operating_capacity_dth_per_day
                ) AS scheduled_at_or_above_operating_count,
                count(*) FILTER (
                    WHERE available_reconciles = false
                ) AS reconciliation_mismatch_count,
                count(*) FILTER (
                    WHERE capacity_kind = 'point' AND facility_id IS NOT NULL
                ) AS matched_point_rows,
                count(*) FILTER (
                    WHERE capacity_kind = 'point'
                ) AS point_rows,
                count(*) FILTER (WHERE segment_id IS NOT NULL)
                    AS matched_segment_rows
            FROM capacity_observations
            WHERE artifact_id IN ({placeholders})
            """,
            artifact_ids,
        )[0]
        selected_report = connection.execute(
            """
            SELECT artifact_id
            FROM tgp_outage_report_summary
            WHERE observed_at <= ?
            ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
            LIMIT 1
            """,
            [decision_time],
        ).fetchone()
        if selected_report is None:
            raise RuntimeError("no TGP outage-impact report is available")
        report_summary = _rows(
            connection,
            """
            SELECT
                artifact_id AS evidence_id,
                notice_id,
                CAST(report_updated_on AS VARCHAR) AS report_updated_on,
                CAST(first_forecast_date AS VARCHAR) AS first_forecast_date,
                CAST(last_forecast_date AS VARCHAR) AS last_forecast_date,
                station_count,
                station_period_rows,
                max_reduction_dth_per_day,
                reduction_mismatch_count
            FROM tgp_outage_report_summary
            WHERE artifact_id = ?
            """,
            [selected_report[0]],
        )[0]
        segment_watchlist = _rows(
            connection,
            f"""
            WITH outage AS (
                SELECT
                    operator_segment_id,
                    max(calculated_reduction_dth_per_day)
                        AS max_planned_reduction_dth_per_day,
                    min(period_start) AS first_planned_date,
                    max(period_end) AS last_planned_date,
                    arg_max(station_label, calculated_reduction_dth_per_day)
                        AS station_label,
                    arg_max(outage_description, calculated_reduction_dth_per_day)
                        AS outage_description,
                    max(artifact_id) AS outage_evidence_id
                FROM outage_impact_observations
                WHERE artifact_id = ?
                  AND operator_segment_id IS NOT NULL
                  AND calculated_reduction_dth_per_day > 0
                GROUP BY operator_segment_id
            ), capacity AS (
                SELECT
                    operator_segment_id,
                    location_name,
                    flow_direction,
                    CAST(design_capacity_dth_per_day AS BIGINT)
                        AS design_capacity_dth_per_day,
                    CAST(operating_capacity_dth_per_day AS BIGINT)
                        AS operating_capacity_dth_per_day,
                    CAST(scheduled_quantity_dth_per_day AS BIGINT)
                        AS scheduled_quantity_dth_per_day,
                    CAST(available_capacity_dth_per_day AS BIGINT)
                        AS available_capacity_dth_per_day,
                    artifact_id AS capacity_evidence_id
                FROM capacity_observations
                WHERE artifact_id IN ({placeholders})
                  AND capacity_kind = 'segment'
            )
            SELECT
                capacity.operator_segment_id,
                capacity.location_name AS capacity_location_name,
                capacity.flow_direction AS capacity_flow_indicator,
                capacity.design_capacity_dth_per_day,
                capacity.operating_capacity_dth_per_day,
                capacity.scheduled_quantity_dth_per_day,
                capacity.available_capacity_dth_per_day,
                outage.station_label AS maintenance_station,
                outage.max_planned_reduction_dth_per_day,
                CAST(outage.first_planned_date AS VARCHAR) AS first_planned_date,
                CAST(outage.last_planned_date AS VARCHAR) AS last_planned_date,
                outage.outage_description,
                capacity.capacity_evidence_id,
                outage.outage_evidence_id
            FROM capacity
            JOIN outage USING (operator_segment_id)
            ORDER BY
                outage.max_planned_reduction_dth_per_day DESC,
                capacity.available_capacity_dth_per_day,
                capacity.operator_segment_id,
                capacity.flow_direction
            LIMIT 16
            """,
            [selected_report[0], *artifact_ids],
        )
        overlap_stats = _rows(
            connection,
            f"""
            WITH outage_segments AS (
                SELECT DISTINCT operator_segment_id
                FROM outage_impact_observations
                WHERE artifact_id = ?
                  AND operator_segment_id IS NOT NULL
                  AND calculated_reduction_dth_per_day > 0
            ), capacity_segments AS (
                SELECT DISTINCT operator_segment_id
                FROM capacity_observations
                WHERE artifact_id IN ({placeholders})
                  AND capacity_kind = 'segment'
            )
            SELECT count(*) AS overlapping_segment_count
            FROM outage_segments
            JOIN capacity_segments USING (operator_segment_id)
            """,
            [selected_report[0], *artifact_ids],
        )[0]
        latest_revisions = _rows(
            connection,
            """
            SELECT
                notice_id,
                prior_report_notice_id,
                station_label,
                operator_segment_id,
                flow_direction,
                CAST(period_start AS VARCHAR) AS period_start,
                CAST(period_end AS VARCHAR) AS period_end,
                operating_capacity_change_dth_per_day,
                outage_description,
                artifact_id AS evidence_id
            FROM tgp_outage_capacity_revisions
            WHERE artifact_id = ?
              AND operating_capacity_change_dth_per_day != 0
            ORDER BY abs(operating_capacity_change_dth_per_day) DESC
            LIMIT 8
            """,
            [selected_report[0]],
        )
        material_alerts = _rows(
            connection,
            """
            WITH ranked AS (
                SELECT
                    alert.alert_id,
                    alert.event_id,
                    event.event_type,
                    event.current_status,
                    event.impact_channel,
                    alert.change_type,
                    alert.severity_score,
                    CAST(alert.score_components AS VARCHAR)
                        AS score_components,
                    alert.headline,
                    alert.explanation,
                    alert.confidence,
                    CAST(alert.evidence AS VARCHAR) AS evidence,
                    strftime(
                        alert.decision_at AT TIME ZONE 'UTC',
                        '%Y-%m-%dT%H:%M:%S.%fZ'
                    ) AS decision_at_utc,
                    row_number() OVER (
                        PARTITION BY event.event_type
                        ORDER BY alert.decision_at DESC,
                                 alert.severity_score DESC,
                                 alert.alert_id
                    ) AS source_rank
                FROM alerts AS alert
                JOIN events AS event USING (event_id)
                WHERE event.pipeline_id = 'TGP'
                  AND alert.decision_at <= ?
            )
            SELECT * EXCLUDE (source_rank)
            FROM ranked
            WHERE source_rank <= 4
            ORDER BY severity_score DESC, decision_at_utc DESC, alert_id
            """,
            [decision_time],
        )
        for alert in material_alerts:
            alert["score_components"] = json.loads(
                str(alert["score_components"])
            )
            alert["evidence"] = json.loads(str(alert["evidence"]))
        transport_impacts = _rows(
            connection,
            """
            SELECT
                assessment_id,
                report_artifact_id,
                capacity_artifact_id,
                report_notice_id,
                CAST(report_updated_on AS VARCHAR) AS report_updated_on,
                CAST(period_start AS VARCHAR) AS period_start,
                CAST(period_end AS VARCHAR) AS period_end,
                station_label,
                operator_segment_id,
                outage_flow_direction,
                capacity_flow_direction,
                tgp_zone,
                capacity_location_name,
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
                baseline_timing,
                match_method,
                research_status,
                price_mapping_status,
                price_mapping_reason,
                benchmark_reference_url,
                CAST(unresolved_reasons AS VARCHAR) AS unresolved_reasons,
                CAST(evidence AS VARCHAR) AS evidence
            FROM tgp_transport_impact_assessments
            WHERE report_artifact_id = ?
              AND calculated_at <= ?
            QUALIFY row_number() OVER (
                PARTITION BY source_table_index, source_row_index,
                             period_start, period_end
                ORDER BY baseline_source_posted_at DESC NULLS LAST,
                         calculated_at DESC, assessment_id DESC
            ) = 1
            ORDER BY
                CASE research_status
                    WHEN 'research_scenario' THEN 1
                    WHEN 'monitor' THEN 2
                    ELSE 3
                END,
                conditional_scheduled_shortfall_dth_per_day DESC NULLS LAST,
                gross_reduction_dth_per_day DESC,
                period_start, station_label
            LIMIT 24
            """,
            [selected_report[0], decision_time],
        )
        for impact in transport_impacts:
            impact["unresolved_reasons"] = json.loads(
                str(impact["unresolved_reasons"])
            )
            impact["evidence"] = json.loads(str(impact["evidence"]))
        transport_impact_summary = _rows(
            connection,
            """
            WITH latest_assessment AS (
                SELECT assessment.*
                FROM tgp_transport_impact_assessments AS assessment
                WHERE report_artifact_id = ?
                  AND calculated_at <= ?
                QUALIFY row_number() OVER (
                    PARTITION BY source_table_index, source_row_index,
                                 period_start, period_end
                    ORDER BY baseline_source_posted_at DESC NULLS LAST,
                             calculated_at DESC, assessment_id DESC
                ) = 1
            )
            SELECT
                count(*) AS positive_forecast_row_count,
                count(*) FILTER (
                    WHERE research_status = 'research_scenario'
                ) AS research_scenario_row_count,
                count(DISTINCT operator_segment_id) FILTER (
                    WHERE research_status = 'research_scenario'
                ) AS research_scenario_segment_count,
                count(DISTINCT station_label) FILTER (
                    WHERE research_status = 'research_scenario'
                ) AS research_scenario_station_count,
                count(DISTINCT tgp_zone) FILTER (
                    WHERE research_status = 'research_scenario'
                ) AS research_scenario_zone_count,
                count(*) FILTER (
                    WHERE research_status = 'monitor'
                ) AS monitor_row_count,
                count(*) FILTER (
                    WHERE research_status = 'no_trade_mapping'
                ) AS no_trade_mapping_row_count,
                max(gross_reduction_dth_per_day)
                    AS largest_gross_reduction_dth_per_day,
                max(conditional_scheduled_shortfall_dth_per_day)
                    AS largest_conditional_shortfall_dth_per_day,
                min(period_start) FILTER (
                    WHERE research_status = 'research_scenario'
                ) AS first_scenario_date,
                max(period_end) FILTER (
                    WHERE research_status = 'research_scenario'
                ) AS last_scenario_date,
                string_agg(DISTINCT tgp_zone, ', ' ORDER BY tgp_zone) FILTER (
                    WHERE research_status = 'research_scenario'
                      AND tgp_zone IS NOT NULL
                ) AS scenario_zones
            FROM latest_assessment
            """,
            [selected_report[0], decision_time],
        )[0]
        for field in ("first_scenario_date", "last_scenario_date"):
            if transport_impact_summary[field] is not None:
                transport_impact_summary[field] = str(
                    transport_impact_summary[field]
                )
        transport_impact_horizons = _rows(
            connection,
            """
            WITH latest_assessment AS (
                SELECT assessment.*
                FROM tgp_transport_impact_assessments AS assessment
                WHERE report_artifact_id = ?
                  AND calculated_at <= ?
                QUALIFY row_number() OVER (
                    PARTITION BY source_table_index, source_row_index,
                                 period_start, period_end
                    ORDER BY baseline_source_posted_at DESC NULLS LAST,
                             calculated_at DESC, assessment_id DESC
                ) = 1
            ), scenario AS (
                SELECT
                    *,
                    CASE
                        WHEN period_start <= CAST(? AS DATE) + INTERVAL 7 DAY
                            THEN 'next_7_days'
                        WHEN period_start <= CAST(? AS DATE) + INTERVAL 30 DAY
                            THEN 'days_8_to_30'
                        ELSE 'after_30_days'
                    END AS horizon
                FROM latest_assessment
                WHERE research_status = 'research_scenario'
                  AND period_end >= CAST(? AS DATE)
            ), ranked AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY horizon
                        ORDER BY
                            conditional_scheduled_shortfall_dth_per_day DESC,
                            period_start,
                            period_end,
                            station_label,
                            operator_segment_id
                    ) AS peak_rank
                FROM scenario
            )
            SELECT
                horizon,
                count(*) AS scenario_row_count,
                count(DISTINCT operator_segment_id) AS segment_count,
                count(DISTINCT station_label) AS station_count,
                count(DISTINCT tgp_zone) AS zone_count,
                string_agg(DISTINCT tgp_zone, ', ' ORDER BY tgp_zone)
                    AS zones,
                max(conditional_scheduled_shortfall_dth_per_day)
                    AS largest_conditional_shortfall_dth_per_day,
                max(station_label) FILTER (WHERE peak_rank = 1)
                    AS peak_station_label,
                max(tgp_zone) FILTER (WHERE peak_rank = 1) AS peak_zone,
                max(outage_flow_direction) FILTER (WHERE peak_rank = 1)
                    AS peak_direction,
                CAST(max(period_start) FILTER (WHERE peak_rank = 1) AS VARCHAR)
                    AS peak_period_start,
                CAST(max(period_end) FILTER (WHERE peak_rank = 1) AS VARCHAR)
                    AS peak_period_end
            FROM ranked
            GROUP BY horizon
            """,
            [
                selected_report[0],
                decision_time,
                str(decision_time.date()),
                str(decision_time.date()),
                str(decision_time.date()),
            ],
        )
        transport_impact_summary["horizons"] = {
            str(row.pop("horizon")): row for row in transport_impact_horizons
        }
        storage_context = _rows(
            connection,
            """
            SELECT
                series_code,
                metric,
                geography,
                value,
                unit,
                vintage,
                strftime(
                    period_start AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS period_start_utc,
                strftime(
                    available_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS available_at_utc,
                artifact_id AS evidence_id
            FROM market_observations
            WHERE series_code LIKE 'EIA_WNGSR:%'
              AND available_at <= ?
              AND geography IN ('Lower 48', 'East', 'South Central')
              AND metric IN (
                  'Working gas storage', 'Weekly storage change',
                  'Storage vs 5-year average'
              )
            QUALIFY row_number() OVER (
                PARTITION BY series_code, geography
                ORDER BY available_at DESC, period_start DESC
            ) = 1
            ORDER BY geography, metric
            """,
            [decision_time],
        )
        benchmark_context = _rows(
            connection,
            """
            SELECT
                series_code,
                provider,
                observation_type,
                metric,
                geography,
                value,
                unit,
                vintage,
                strftime(
                    period_start AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS period_start_utc,
                strftime(
                    source_published_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS source_published_at_utc,
                strftime(
                    available_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS available_at_utc,
                artifact_id AS evidence_id
            FROM market_observations
            WHERE observation_type IN ('physical_spot', 'futures_proxy')
              AND available_at <= ?
            QUALIFY row_number() OVER (
                PARTITION BY series_code, geography
                ORDER BY available_at DESC, period_start DESC
            ) = 1
            ORDER BY observation_type, series_code
            """,
            [decision_time],
        )
        weather_context = _rows(
            connection,
            """
            WITH eligible AS (
                SELECT
                    *,
                    max(available_at) OVER (PARTITION BY geography)
                        AS latest_available_at
                FROM market_observations
                WHERE observation_type = 'weather_forecast'
                  AND available_at <= ?
                  AND period_end > ?
                  AND period_start < ?
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
                strftime(
                    period_start AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS period_start_utc,
                strftime(
                    period_end AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS period_end_utc,
                strftime(
                    source_published_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS source_published_at_utc,
                strftime(
                    available_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS available_at_utc,
                artifact_id AS evidence_id
            FROM eligible
            WHERE available_at = latest_available_at
            ORDER BY geography, period_start, metric
            """,
            [decision_time, decision_time, decision_time.add(days=8)],
        )
    finally:
        connection.close()

    source_posted_values = [
        pendulum.parse(str(row["source_posted_at_utc"]))
        for row in capacity_exports
    ]
    latest_source_posted = max(source_posted_values)
    source_age_hours = max(
        0.0,
        (decision_time - latest_source_posted).total_seconds() / 3600,
    )
    daily_market_state = _compact_market_state(
        build_tgp_daily_market_state(
            database_path,
            decision_at=decision_time,
        )
    )
    weather_summary: list[dict[str, object]] = []
    for geography in sorted({str(row["geography"]) for row in weather_context}):
        geography_rows = [
            row for row in weather_context if row["geography"] == geography
        ]
        temperature_values = [
            float(row["value"])
            for row in geography_rows
            if row["metric"] == "Forecast mean temperature"
        ]
        hdd_values = [
            float(row["value"])
            for row in geography_rows
            if row["metric"] == "Forecast HDD"
        ]
        cdd_values = [
            float(row["value"])
            for row in geography_rows
            if row["metric"] == "Forecast CDD"
        ]
        period_starts = sorted(
            {
                str(row["period_start_utc"])
                for row in geography_rows
                if row.get("period_start_utc")
            }
        )
        period_ends = sorted(
            {
                str(row["period_end_utc"])
                for row in geography_rows
                if row.get("period_end_utc")
            }
        )
        weather_summary.append(
            {
                "geography": geography,
                "complete_day_count": max(len(hdd_values), len(cdd_values)),
                "forecast_start_utc": period_starts[0] if period_starts else None,
                "forecast_end_utc": period_ends[-1] if period_ends else None,
                "mean_temperature_f": (
                    round(sum(temperature_values) / len(temperature_values), 2)
                    if temperature_values
                    else None
                ),
                "total_hdd_65": round(sum(hdd_values), 2),
                "total_cdd_65": round(sum(cdd_values), 2),
                "provider": geography_rows[0].get("provider"),
                "source_published_at_utc": geography_rows[0].get(
                    "source_published_at_utc"
                ),
                "available_at_utc": geography_rows[0].get("available_at_utc"),
                "evidence_ids": sorted(
                    {
                        str(row["evidence_id"])
                        for row in geography_rows
                        if row.get("evidence_id")
                    }
                ),
            }
        )
    evidence_ids = sorted(
        {
            *artifact_ids,
            str(report_summary["evidence_id"]),
            *(
                str(row["evidence_id"])
                for row in latest_revisions
                if row["evidence_id"]
            ),
            *(
                str(artifact_id)
                for alert in material_alerts
                for artifact_id in alert["evidence"].get("artifact_ids", [])
            ),
            *(
                str(value)
                for impact in transport_impacts
                for value in (
                    impact.get("report_artifact_id"),
                    impact.get("capacity_artifact_id"),
                )
                if value
            ),
            *(
                str(row["evidence_id"])
                for row in storage_context
                if row.get("evidence_id")
            ),
            *(
                str(row["evidence_id"])
                for row in benchmark_context
                if row.get("evidence_id")
            ),
            *(
                str(row["evidence_id"])
                for row in weather_context
                if row.get("evidence_id")
            ),
        }
    )
    evidence_payload = {
        "capacity_exports": capacity_exports,
        "capacity_stats": capacity_stats,
        "outage_report": report_summary,
        "overlap_stats": overlap_stats,
        "segment_watchlist": segment_watchlist,
        "latest_report_revisions": latest_revisions,
        "material_alerts": material_alerts,
        "transport_impacts": transport_impacts,
        "transport_impact_summary": transport_impact_summary,
        "daily_market_state": daily_market_state,
        "eia_storage_context": storage_context,
        "benchmark_context": benchmark_context,
        "weather_context": weather_context,
        "weather_summary": weather_summary,
        "supply_demand_translation": {
            "transport_vs_supply": (
                "The calculation tests pipeline transportation fit. It does not "
                "establish a loss of U.S. production or end-user demand."
            ),
            "aggregation_warning": (
                "Do not sum station-period shortfalls: periods overlap and the "
                "same gas can traverse multiple TGP segments."
            ),
            "primary_market_channel": (
                "Regional transport availability and basis are more direct than "
                "Henry Hub flat price."
            ),
            "national_price_mapping": "unresolved",
            "missing_denominator": (
                "No point-in-time regional supply, demand, measured flow, or "
                "rerouting denominator is available."
            ),
        },
        "evidence_ids": evidence_ids,
    }
    is_stale = source_age_hours > 30
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                # The agent reruns for facts that crossed a deterministic
                # materiality threshold, not for every harmless source refresh.
                "material_alerts": _stable_fingerprint_value(material_alerts),
                "transport_impacts": _stable_fingerprint_value(transport_impacts),
                "transport_impact_summary": _stable_fingerprint_value(
                    transport_impact_summary
                ),
                "daily_market_state": _stable_fingerprint_value(
                    daily_market_state
                ),
                "eia_storage_context": _stable_fingerprint_value(storage_context),
                "benchmark_context": _stable_fingerprint_value(benchmark_context),
                "weather_context": _stable_fingerprint_value(weather_context),
                # Crossing the stale-data boundary materially changes the memo even
                # when the operator has not published a new file.
                "is_stale": is_stale,
                "packet_version": "tgp_research_packet_v8",
            },
            sort_keys=True,
            separators=(",", ":"),
            default=_json_value,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "packet_version": "tgp_research_packet_v8",
        "pipeline_id": "TGP",
        "decision_at_utc": decision_time.to_iso8601_string(),
        "data_fingerprint": fingerprint,
        "freshness": {
            "latest_capacity_source_posted_at_utc": (
                latest_source_posted.to_iso8601_string()
            ),
            "capacity_source_age_hours": round(source_age_hours, 2),
            "is_stale": is_stale,
            "stale_after_hours": 30,
        },
        "interpretation_limits": [
            "Operationally available capacity is transportation capacity, not measured physical flow.",
            "A scheduled quantity is net directional and can show zero in the opposite direction.",
            "Operating capacity can differ from design for maintenance and other operating conditions.",
            "Forecast FH/BH is aligned to capacity TD1/TD2 using TGP's documented default-direction convention.",
            "The conditional shortfall assumes the captured net schedule is unchanged; it is not measured flow or confirmed curtailment.",
            "EIA storage is national and regional balance context, not proof that a TGP event caused a storage or price change.",
            "Henry Hub spot is a national physical benchmark; it is not TGP regional cash basis.",
            "NWS degree days are named-anchor demand proxies, not measured gas consumption.",
            "The optional rolling futures quote must retain its contract label and cannot establish a TGP price effect.",
            "No regional basis, production, measured-flow, or rerouting observations are available.",
            "The evidence supports watch scenarios, not a causal price claim or trade recommendation.",
        ],
        **evidence_payload,
    }


def latest_tgp_research_memo(
    database_path: str | Path,
    *,
    data_fingerprint: str | None = None,
) -> dict[str, object] | None:
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        rows = _rows(
            connection,
            """
            SELECT
                research_memo_id,
                strftime(
                    decision_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS decision_at_utc,
                strftime(
                    generated_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS generated_at_utc,
                data_fingerprint,
                headline,
                plain_english_summary,
                why_it_matters,
                overall_confidence,
                CAST(memo AS VARCHAR) AS memo,
                memo.agent_run_id,
                agent.session_path
            FROM research_memos AS memo
            LEFT JOIN agent_runs AS agent USING (agent_run_id)
            WHERE memo.pipeline_id = 'TGP'
            ORDER BY memo.generated_at DESC, memo.research_memo_id DESC
            LIMIT 1
            """,
        )
    finally:
        connection.close()
    if not rows:
        return None
    memo = rows[0]
    memo["memo"] = json.loads(str(memo["memo"]))
    memo["is_current"] = (
        data_fingerprint is None
        or memo["data_fingerprint"] == data_fingerprint
    )
    return memo


def _validate_memo(
    memo: dict[str, object],
    *,
    valid_evidence_ids: set[str],
) -> dict[str, object]:
    required = set(INSIGHT_SCHEMA["required"])
    missing = required.difference(memo)
    if missing:
        raise ValueError(f"insight memo is missing fields: {sorted(missing)}")
    if memo["overall_confidence"] not in {"low", "medium", "high"}:
        raise ValueError("insight memo has invalid overall confidence")

    def reject_ids_in_prose(value: object, *, field: str = "memo") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence_ids":
                    continue
                reject_ids_in_prose(item, field=f"{field}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                reject_ids_in_prose(item, field=f"{field}[{index}]")
        elif isinstance(value, str) and _RAW_EVIDENCE_TOKEN.search(value):
            raise ValueError(
                f"insight memo {field} contains a raw evidence ID in prose"
            )

    reject_ids_in_prose(memo)
    cited: set[str] = set()
    for section_name in ("facts", "watch_items"):
        section = memo[section_name]
        if not isinstance(section, list) or not section:
            raise ValueError(f"insight memo {section_name} must be a nonempty list")
        for item in section:
            if not isinstance(item, dict):
                raise ValueError(f"insight memo {section_name} item must be an object")
            evidence = item.get("evidence_ids")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError(f"insight memo {section_name} item lacks evidence")
            cited.update(str(value) for value in evidence)
    unknown = cited.difference(valid_evidence_ids)
    if unknown:
        raise ValueError(f"insight memo cites unknown evidence IDs: {sorted(unknown)}")
    return {
        "valid": True,
        "cited_evidence_count": len(cited),
        "available_evidence_count": len(valid_evidence_ids),
        "unknown_evidence_ids": [],
    }


def _prompt(packet: dict[str, object]) -> str:
    return """You are the research agent for Pipeline Pulse, a natural-gas pipeline intelligence tool.

Analyze only the evidence packet below. Return the requested JSON object and nothing else.

Rules:
- Separate direct facts from watch scenarios.
- Treat material_alerts as the authoritative change ledger. Do not invent a
  change that the deterministic alert layer did not identify.
- Explain why a material alert may matter, but do not alter its score, status,
  before/after values, or comparison warning.
- Use plain language suitable for an intelligent reader who is new to natural gas.
- Write as an analyst addressing an investor. Never mention the product's
  implementation state or use terms such as MVP, packet, database, collector,
  adapter, configured series, loaded data, or missing feature. State the
  underlying market fact directly—for example, that a regional price or flow
  observation is unavailable.
- Lead with the system-level supply/demand interpretation before station detail: this is transport capacity, whether inventory is above or below recent norms, the likely regional-basis channel, and the missing denominator that prevents a national supply/demand share.
- Use daily_market_state as the authoritative big-picture calendar. Lead with its current-day, near-term, and forward peaks before discussing individual rows. Preserve its no-sum method: the largest single constraint is an exposure screen, not aggregate lost supply.
- Use daily_market_state.tradable_market_picture as the evidence-gate summary. Preserve every gate status, distinguish a regional-basis watch from a trade-ready setup, and do not supply a directional price sign or exact contract while those mappings remain unresolved.
- When weather_summary is present, state whether the named-anchor HDD/CDD outlook reinforces or counters the transport setup. Describe it as demand pressure only; never convert degree days into gas volume without a supplied model.
- When benchmark_context is present, label physical spot separately from any rolling futures proxy and explain whether it is merely context or confirms a move. It cannot confirm regional basis.
- Every fact and watch item must cite one or more exact evidence IDs from evidence_ids.
- Put evidence IDs only in the structured evidence_ids arrays. Never append artifact IDs, an "Evidence:" clause, or source keys to headlines, summaries, claims, scenarios, counterevidence, missing-data prose, or glossary definitions.
- Do not invent prices, flows, production losses, geography, or causal effects.
- Do not call operationally available capacity physical flow.
- Use transport_impacts as the deterministic direction and capacity alignment.
- Preserve each impact's research_status. Use no_trade_mapping when the event cannot be linked, monitor when evidence is insufficient, and research_scenario only for a conditional setup with a calculated shortfall.
- EIA storage, Henry Hub spot, NWS degree days, and any optional front-month quote are regime context, not event attribution. Preserve each provider and vintage. Henry Hub is not TGP regional basis, degree days are demand proxies, and a rolling futures symbol is not one immutable contract.
- A watch item may explain an upstream-basis, downstream-basis, transport, storage, or system-balance channel as a conditional scenario, never as an established price prediction.
- Include counterevidence, invalidation conditions, missing data, and calibrated confidence.
- If the capacity snapshot is stale, make that prominent and lower confidence.
- If an alert warns that gas day or nomination cycle changed, preserve that
  caveat and do not present the comparison as like-for-like.
- Define only the gas-market terms actually used in the memo.

Evidence packet:
""" + json.dumps(packet, indent=2, sort_keys=True, default=_json_value)


def rebuild_session_manifest(
    database_path: str | Path,
    *,
    sessions_directory: str | Path = "sessions/insights",
    codex_binary: str | None = None,
) -> Path:
    """Rebuild a checksum manifest from durable agent-run records and files."""
    output_directory = Path(sessions_directory)
    sessions_root = output_directory.parent
    manifest_path = sessions_root / "manifest.json"
    executable = codex_binary or shutil.which("codex")
    runtime = "codex-cli unknown"
    if executable:
        try:
            version = subprocess.run(
                [executable, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            if version:
                runtime = version
        except (OSError, subprocess.SubprocessError):
            pass

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        runs = _rows(
            connection,
            """
            SELECT
                run.agent_run_id,
                run.role,
                run.model,
                run.started_at,
                run.completed_at,
                run.status,
                CAST(run.input_artifact_ids AS VARCHAR)
                    AS input_artifact_ids,
                run.session_path,
                CAST(run.validation AS VARCHAR) AS validation,
                memo.data_fingerprint
            FROM agent_runs AS run
            LEFT JOIN research_memos AS memo USING (agent_run_id)
            ORDER BY run.started_at, run.agent_run_id
            """,
        )
    finally:
        connection.close()

    entries: list[dict[str, object]] = []
    suffixes = (
        ".packet.json",
        ".prompt.txt",
        ".schema.json",
        ".events.jsonl",
        ".output.json",
        ".stderr.log",
        ".validation.json",
    )
    for run in runs:
        events_path = Path(str(run["session_path"]))
        prefix = Path(
            events_path.as_posix().removesuffix(".events.jsonl")
        )
        files: list[dict[str, str]] = []
        for suffix in suffixes:
            file_path = Path(prefix.as_posix() + suffix)
            if not file_path.exists():
                continue
            try:
                relative_path = file_path.relative_to(sessions_root).as_posix()
            except ValueError:
                relative_path = file_path.as_posix()
            files.append(
                {
                    "path": relative_path,
                    "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
                }
            )
        started = pendulum.instance(run["started_at"]).in_timezone("UTC")
        completed = (
            pendulum.instance(run["completed_at"]).in_timezone("UTC")
            if run["completed_at"] is not None
            else None
        )
        entries.append(
            {
                "agent_run_id": run["agent_run_id"],
                "role": run["role"],
                "runtime": runtime,
                "model": run["model"],
                "started_at_utc": started.to_iso8601_string(),
                "completed_at_utc": (
                    completed.to_iso8601_string() if completed else None
                ),
                "status": run["status"],
                "code_commit": None,
                "data_fingerprint": run["data_fingerprint"],
                "input_artifact_ids": json.loads(
                    str(run["input_artifact_ids"])
                ),
                "validation": (
                    json.loads(str(run["validation"]))
                    if run["validation"] is not None
                    else None
                ),
                "files": files,
            }
        )
    design_sessions: list[dict[str, object]] = []
    design_directory = sessions_root / "design"
    if design_directory.exists():
        for metadata_path in sorted(design_directory.glob("*.metadata.json")):
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            export_relative_path = str(metadata.pop("export_path"))
            export_path = sessions_root / export_relative_path
            if not export_path.is_file():
                raise FileNotFoundError(
                    f"design-session export is missing: {export_path}"
                )
            design_sessions.append(
                {
                    **metadata,
                    "file": {
                        "path": export_relative_path,
                        "bytes": export_path.stat().st_size,
                        "sha256": hashlib.sha256(export_path.read_bytes()).hexdigest(),
                    },
                }
            )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = manifest_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(
            {"design_sessions": design_sessions, "sessions": entries},
            indent=2,
            sort_keys=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(manifest_path)
    return manifest_path


def generate_tgp_research_memo(
    database_path: str | Path,
    *,
    sessions_directory: str | Path = "sessions/insights",
    codex_binary: str | None = None,
    model: str | None = DEFAULT_INSIGHT_MODEL,
    skip_if_unchanged: bool = False,
) -> InsightRunSummary:
    connection = connect_database(database_path)
    initialize_database(connection)
    connection.close()
    quality = build_tgp_quality_report(database_path)
    if not quality.agent_input_ready:
        raise RuntimeError("quality gate blocked the insight agent")
    packet = build_tgp_research_packet(database_path)
    packet["quality_gate"] = {
        "agent_input_ready": quality.agent_input_ready,
        "overall_status": quality.overall_status,
        "findings": list(quality.findings),
    }

    if skip_if_unchanged:
        current_memo = latest_tgp_research_memo(
            database_path,
            data_fingerprint=str(packet["data_fingerprint"]),
        )
        if current_memo is not None and current_memo["is_current"]:
            session_path = str(current_memo.get("session_path") or "")
            output_path = (
                session_path.removesuffix(".events.jsonl") + ".output.json"
                if session_path
                else ""
            )
            return InsightRunSummary(
                agent_run_id=str(current_memo["agent_run_id"]),
                research_memo_id=str(current_memo["research_memo_id"]),
                status="skipped_unchanged",
                data_fingerprint=str(packet["data_fingerprint"]),
                headline=str(current_memo["headline"]),
                overall_confidence=str(current_memo["overall_confidence"]),
                session_path=session_path,
                output_path=output_path,
            )

    executable = codex_binary or shutil.which("codex")
    if not executable:
        raise FileNotFoundError("codex CLI is not available")
    run_id = str(uuid.uuid4())
    memo_id = str(uuid.uuid4())
    started_at = pendulum.now("UTC")
    timestamp = started_at.format("YYYYMMDDTHHmmss[Z]")
    output_directory = Path(sessions_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    prefix = output_directory / f"{timestamp}_{run_id}"
    packet_path = prefix.with_suffix(".packet.json")
    prompt_path = prefix.with_suffix(".prompt.txt")
    schema_path = prefix.with_suffix(".schema.json")
    events_path = prefix.with_suffix(".events.jsonl")
    stderr_path = prefix.with_suffix(".stderr.log")
    output_path = prefix.with_suffix(".output.json")
    validation_path = prefix.with_suffix(".validation.json")
    packet_path.write_text(
        json.dumps(packet, indent=2, sort_keys=True, default=_json_value) + "\n",
        encoding="utf-8",
    )
    prompt_text = _prompt(packet)
    prompt_path.write_text(prompt_text, encoding="utf-8")
    schema_path.write_text(
        json.dumps(INSIGHT_SCHEMA, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    selected_model = model or DEFAULT_INSIGHT_MODEL
    connection = connect_database(database_path)
    connection.execute(
        """
        INSERT INTO agent_runs(
            agent_run_id, role, model, started_at, status,
            input_artifact_ids, session_path
        ) VALUES (?, 'tgp_research_analyst', ?, ?, 'running', ?, ?)
        """,
        [
            run_id,
            selected_model,
            started_at,
            json.dumps(packet["evidence_ids"], sort_keys=True),
            events_path.as_posix(),
        ],
    )
    connection.close()

    command = [
        executable,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        "--output-schema",
        schema_path.as_posix(),
        "--output-last-message",
        output_path.as_posix(),
    ]
    command.extend(["--model", selected_model])
    command.append("-")
    try:
        with events_path.open("wb") as events_file, stderr_path.open("wb") as error_file:
            completed = subprocess.run(
                command,
                input=prompt_text.encode("utf-8"),
                stdout=events_file,
                stderr=error_file,
                cwd=Path(__file__).parents[2],
                check=False,
                timeout=900,
            )
        if completed.returncode != 0:
            raise RuntimeError(
                f"codex insight agent exited with status {completed.returncode}"
            )
        memo = json.loads(output_path.read_text(encoding="utf-8"))
        validation = _validate_memo(
            memo,
            valid_evidence_ids=set(str(value) for value in packet["evidence_ids"]),
        )
        validation["quality_gate_status"] = quality.overall_status
        validation_path.write_text(
            json.dumps(validation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        completed_at = pendulum.now("UTC")
        connection = connect_database(database_path)
        connection.execute(
            """
            UPDATE agent_runs
            SET completed_at = ?, status = 'completed', validation = ?
            WHERE agent_run_id = ?
            """,
            [completed_at, json.dumps(validation, sort_keys=True), run_id],
        )
        connection.execute(
            """
            INSERT INTO research_memos(
                research_memo_id, pipeline_id, decision_at, generated_at,
                data_fingerprint, headline, plain_english_summary,
                why_it_matters, overall_confidence, memo, agent_run_id
            ) VALUES (?, 'TGP', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                memo_id,
                pendulum.parse(str(packet["decision_at_utc"])),
                completed_at,
                packet["data_fingerprint"],
                memo["headline"],
                memo["plain_english_summary"],
                memo["why_it_matters"],
                memo["overall_confidence"],
                json.dumps(memo, sort_keys=True),
                run_id,
            ],
        )
        connection.close()
    except Exception as exc:
        connection = connect_database(database_path)
        connection.execute(
            """
            UPDATE agent_runs
            SET completed_at = ?, status = 'failed', validation = ?
            WHERE agent_run_id = ?
            """,
            [
                pendulum.now("UTC"),
                json.dumps(
                    {"valid": False, "error": str(exc)},
                    sort_keys=True,
                ),
                run_id,
            ],
        )
        connection.close()
        raise

    rebuild_session_manifest(
        database_path,
        sessions_directory=output_directory,
        codex_binary=executable,
    )

    return InsightRunSummary(
        agent_run_id=run_id,
        research_memo_id=memo_id,
        status="completed",
        data_fingerprint=str(packet["data_fingerprint"]),
        headline=str(memo["headline"]),
        overall_confidence=str(memo["overall_confidence"]),
        session_path=events_path.as_posix(),
        output_path=output_path.as_posix(),
    )
