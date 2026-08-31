from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .database import connect_database, initialize_database


_HEADERS = (
    "pipeline_id",
    "notice_id",
    "notice_type_primary",
    "notice_type_secondary",
    "subject",
    "subject_missing",
    "detail_available",
    "critical",
    "status_description",
    "prior_notice_id",
    "notice_text",
    "detail_version_sha256",
    "posted_at_utc",
    "posted_at_operator_local",
    "effective_start_utc",
    "effective_end_utc",
    "observed_at_utc",
    "source_content_sha256",
)


@dataclass(frozen=True)
class CuratedExportSummary:
    output_path: str
    row_count: int


@dataclass(frozen=True)
class TgpMvpExportSummary:
    output_directory: str
    maintenance_notice_rows: int
    notice_version_history_rows: int
    outage_report_rows: int
    latest_capacity_rows: int
    capacity_revision_rows: int
    location_rows: int
    operational_capacity_rows: int
    operational_capacity_capture_rows: int
    alert_rows: int
    daily_market_state_rows: int
    transport_impact_rows: int
    market_context_rows: int


def export_curated_notice_index(
    database_path: str | Path,
    output_path: str | Path,
) -> CuratedExportSummary:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        rows = connection.execute(
            """
            WITH latest_detail AS (
                SELECT *
                FROM current_notice_versions
            )
            SELECT
                current.pipeline_id,
                current.notice_id,
                current.notice_type_primary,
                current.notice_type_secondary,
                current.subject,
                current.subject IS NULL,
                detail.notice_id IS NOT NULL,
                detail.critical,
                detail.status_description,
                detail.prior_notice_id,
                detail.notice_text,
                detail.version_sha256,
                strftime(
                    current.posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ),
                strftime(
                    current.posted_at AT TIME ZONE 'America/Chicago',
                    '%Y-%m-%dT%H:%M:%S'
                ),
                strftime(
                    current.effective_start AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ),
                strftime(
                    current.effective_end AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ),
                strftime(
                    current.observed_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ),
                artifact.content_sha256
            FROM current_notice_index AS current
            JOIN source_artifacts AS artifact
              ON artifact.artifact_id = current.artifact_id
            LEFT JOIN latest_detail AS detail
              ON detail.pipeline_id = current.pipeline_id
             AND detail.notice_id = current.notice_id
            ORDER BY current.posted_at DESC, current.notice_id DESC
            """
        ).fetchall()
    finally:
        connection.close()

    temporary_output = output.with_suffix(output.suffix + ".tmp")
    try:
        with temporary_output.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(_HEADERS)
            writer.writerows(rows)
        temporary_output.replace(output)
    finally:
        temporary_output.unlink(missing_ok=True)
    return CuratedExportSummary(
        output_path=output.as_posix(),
        row_count=len(rows),
    )


def _export_query(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    output: Path,
) -> int:
    result = connection.execute(query)
    headers = tuple(description[0] for description in result.description)
    rows = result.fetchall()
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    try:
        with temporary_output.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(headers)
            writer.writerows(rows)
        temporary_output.replace(output)
    finally:
        temporary_output.unlink(missing_ok=True)
    return len(rows)


def export_tgp_mvp_tables(
    database_path: str | Path,
    output_directory: str | Path,
) -> TgpMvpExportSummary:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        maintenance_count = _export_query(
            connection,
            """
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
                strftime(received_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS received_at_utc
            FROM tgp_maintenance_notices
            ORDER BY posted_at DESC, notice_id DESC
            """,
            output / "tgp_maintenance_notices.csv",
        )
        notice_version_history_count = _export_query(
            connection,
            """
            SELECT
                pipeline_id,
                notice_id,
                version_sha256,
                prior_version_sha256,
                is_first_observation,
                is_revision_observation,
                strftime(
                    available_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS available_at_utc,
                notice_type_primary,
                notice_type_secondary,
                status_description,
                prior_notice_id,
                subject,
                notice_text,
                strftime(
                    posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS posted_at_utc,
                strftime(
                    effective_start AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS effective_start_utc,
                strftime(
                    effective_end AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS effective_end_utc,
                required_response,
                strftime(
                    response_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS response_at_utc,
                raw_content_sha256,
                canonical_url,
                raw_path,
                artifact_id
            FROM tgp_notice_version_timeline
            ORDER BY available_at, notice_id, artifact_id
            """,
            output / "tgp_notice_version_history.csv",
        )
        report_count = _export_query(
            connection,
            """
            SELECT
                notice_id,
                report_updated_on,
                strftime(posted_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS posted_at_utc,
                first_forecast_date,
                last_forecast_date,
                station_period_rows,
                station_count,
                populated_capacity_rows,
                max_reduction_dth_per_day,
                reduction_mismatch_count,
                artifact_id,
                strftime(observed_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS observed_at_utc
            FROM tgp_outage_report_summary
            ORDER BY report_updated_on DESC, posted_at DESC
            """,
            output / "tgp_outage_report_summary.csv",
        )
        latest_capacity_count = _export_query(
            connection,
            """
            SELECT
                notice_id,
                report_kind,
                report_updated_on,
                period_label,
                period_start,
                period_end,
                station_label,
                operator_segment_id,
                flow_direction,
                nominal_capacity_dth_per_day,
                operating_capacity_dth_per_day,
                reported_reduction_dth_per_day,
                calculated_reduction_dth_per_day,
                reduction_reconciles,
                outage_description,
                artifact_id
            FROM latest_tgp_outage_capacity
            ORDER BY period_start, station_label, report_kind
            """,
            output / "tgp_latest_outage_capacity.csv",
        )
        revision_count = _export_query(
            connection,
            """
            SELECT
                notice_id,
                prior_report_notice_id,
                report_kind,
                report_updated_on,
                report_posted_at,
                period_start,
                period_end,
                station_label,
                operator_segment_id,
                flow_direction,
                prior_operating_capacity_dth_per_day,
                operating_capacity_dth_per_day,
                operating_capacity_change_dth_per_day,
                calculated_reduction_dth_per_day,
                outage_description,
                artifact_id,
                prior_artifact_id
            FROM tgp_outage_capacity_revisions
            WHERE operating_capacity_change_dth_per_day != 0
            ORDER BY report_posted_at DESC, abs(operating_capacity_change_dth_per_day) DESC
            """,
            output / "tgp_outage_capacity_revisions.csv",
        )
        location_count = _export_query(
            connection,
            """
            SELECT
                operator_location_id,
                location_name,
                flow_role,
                CASE flow_role
                    WHEN 'R' THEN 'receipt'
                    WHEN 'D' THEN 'delivery'
                    WHEN 'B' THEN 'bidirectional'
                    ELSE flow_role
                END AS flow_role_label,
                location_type,
                county_name,
                state_abbreviation,
                receipt_zone,
                delivery_zone,
                operator_segment_id,
                interconnect_indicator,
                counterparty_name,
                counterparty_ferc_cid,
                latitude,
                longitude,
                coordinate_method,
                coordinate_precision,
                matched_geography_id,
                matched_geography_name,
                artifact_id AS location_artifact_id,
                coordinate_artifact_id,
                strftime(observed_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ') AS observed_at_utc
            FROM tgp_location_map
            ORDER BY state_abbreviation, county_name, location_name,
                     operator_location_id
            """,
            output / "tgp_locations.csv",
        )
        operational_capacity_count = _export_query(
            connection,
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
                capacity.capacity_kind,
                capacity.point_role,
                capacity.gas_day,
                strftime(
                    capacity.effective_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS effective_at_utc,
                strftime(
                    capacity.effective_at AT TIME ZONE 'America/Chicago',
                    '%Y-%m-%dT%H:%M:%S'
                ) AS effective_at_operator_local,
                capacity.cycle,
                capacity.operator_location_id,
                capacity.operator_segment_id,
                capacity.location_name,
                capacity.zone,
                capacity.facility_id,
                capacity.segment_id,
                capacity.flow_direction,
                capacity.design_capacity_dth_per_day,
                capacity.operating_capacity_dth_per_day,
                capacity.scheduled_quantity_dth_per_day,
                capacity.available_capacity_dth_per_day,
                capacity.interruptible_scheduled,
                capacity.all_quantity_available,
                capacity.quantity_reason,
                capacity.available_reconciles,
                strftime(
                    capacity.source_posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS source_posted_at_utc,
                strftime(
                    capacity.observed_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS observed_at_utc,
                strftime(
                    capacity.available_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS available_at_utc,
                export.measurement_basis,
                artifact.content_sha256,
                artifact.canonical_url,
                artifact.raw_path,
                capacity.artifact_id
            FROM capacity_observations AS capacity
            JOIN capacity_exports AS export USING (artifact_id)
            JOIN source_artifacts AS artifact USING (artifact_id)
            JOIN latest_run USING (run_id)
            ORDER BY
                capacity.capacity_kind,
                capacity.point_role,
                capacity.operator_segment_id,
                capacity.operator_location_id,
                capacity.flow_direction
            """,
            output / "tgp_latest_operational_capacity.csv",
        )
        operational_capacity_capture_count = _export_query(
            connection,
            """
            SELECT
                summary.capacity_kind,
                summary.point_role,
                summary.gas_day,
                strftime(
                    summary.effective_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS effective_at_utc,
                summary.cycle,
                strftime(
                    summary.source_posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS source_posted_at_utc,
                summary.source_footer_row_count,
                summary.parsed_row_count,
                summary.available_reconciliation_mismatch_count,
                summary.matched_facility_rows,
                summary.matched_segment_rows,
                export.schema_sha256,
                export.parser_version,
                export.measurement_basis,
                export.quantity_description,
                export.comments AS source_comments,
                artifact.content_sha256,
                artifact.canonical_url,
                artifact.raw_path,
                summary.artifact_id,
                strftime(
                    summary.observed_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS observed_at_utc
            FROM tgp_capacity_capture_summary AS summary
            JOIN capacity_exports AS export USING (artifact_id)
            JOIN source_artifacts AS artifact USING (artifact_id)
            ORDER BY summary.observed_at DESC, summary.capacity_kind,
                     summary.point_role
            """,
            output / "tgp_capacity_capture_summary.csv",
        )
        alert_count = _export_query(
            connection,
            """
            SELECT
                alert.alert_id,
                alert.event_id,
                event.event_type,
                event.current_status,
                event.impact_channel,
                strftime(
                    alert.decision_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS decision_at_utc,
                alert.change_type,
                alert.severity_score,
                CAST(alert.score_components AS VARCHAR) AS score_components_json,
                alert.headline,
                alert.explanation,
                alert.confidence,
                strftime(
                    event.effective_start AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS effective_start_utc,
                strftime(
                    event.effective_end AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS effective_end_utc,
                CAST(alert.evidence AS VARCHAR) AS evidence_json
            FROM alerts AS alert
            JOIN events AS event USING (event_id)
            WHERE event.pipeline_id = 'TGP'
            ORDER BY alert.decision_at DESC, alert.severity_score DESC,
                     alert.alert_id
            """,
            output / "tgp_alerts.csv",
        )
        daily_market_state_count = _export_query(
            connection,
            """
            SELECT
                gas_day,
                horizon,
                report_notice_id,
                report_updated_on,
                transport_state,
                screen_state,
                active_maintenance_row_count,
                affected_segment_count,
                affected_zone_count,
                modeled_conflict_row_count,
                modeled_conflict_segment_count,
                largest_single_reduction_dth_per_day,
                largest_conditional_shortfall_dth_per_day,
                peak_station_label,
                peak_segment_id,
                peak_zone,
                peak_direction,
                peak_segment_states,
                affected_zones,
                strftime(
                    capacity_source_posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS capacity_source_posted_at_utc,
                strftime(
                    calculated_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS calculated_at_utc
            FROM latest_tgp_daily_market_state
            ORDER BY gas_day
            """,
            output / "tgp_daily_market_state.csv",
        )
        transport_impact_count = _export_query(
            connection,
            """
            WITH selected_report AS (
                SELECT artifact_id
                FROM tgp_outage_report_summary
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
                assessment_id, report_notice_id, report_updated_on,
                period_start, period_end, station_label, operator_segment_id,
                outage_flow_direction, capacity_flow_direction, tgp_zone,
                capacity_location_name, baseline_gas_day, baseline_cycle,
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
                CAST(unresolved_reasons AS VARCHAR) AS unresolved_reasons_json,
                CAST(evidence AS VARCHAR) AS evidence_json,
                strftime(
                    calculated_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS calculated_at_utc,
                report_artifact_id, capacity_artifact_id
            FROM latest_assessment
            ORDER BY conditional_scheduled_shortfall_dth_per_day DESC NULLS LAST,
                     gross_reduction_dth_per_day DESC, period_start, station_label
            """,
            output / "tgp_transport_impacts.csv",
        )
        market_context_count = _export_query(
            connection,
            """
            SELECT
                observation.series_code,
                observation.provider,
                observation.observation_type,
                observation.metric,
                observation.geography,
                strftime(
                    observation.period_start AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS period_start_utc,
                strftime(
                    observation.period_end AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS period_end_utc,
                observation.value,
                observation.unit,
                strftime(
                    observation.source_published_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS source_published_at_utc,
                strftime(
                    observation.available_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS available_at_utc,
                observation.vintage,
                artifact.canonical_url AS source_url,
                observation.artifact_id
            FROM market_observations AS observation
            JOIN source_artifacts AS artifact USING (artifact_id)
            ORDER BY observation.available_at DESC,
                     observation.observation_type, observation.geography,
                     observation.period_start, observation.metric
            """,
            output / "gas_market_context.csv",
        )
        _export_query(
            connection,
            """
            SELECT
                series_code, provider, observation_type, metric, geography,
                CAST(period_start AS VARCHAR) AS period_start_utc,
                CAST(period_end AS VARCHAR) AS period_end_utc,
                value, unit,
                strftime(
                    source_published_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS source_published_at_utc,
                strftime(
                    available_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ) AS available_at_utc,
                vintage, artifact_id
            FROM market_observations
            WHERE series_code LIKE 'EIA_WNGSR:%'
            ORDER BY available_at DESC, geography, metric
            """,
            output / "eia_weekly_storage.csv",
        )
    finally:
        connection.close()
    return TgpMvpExportSummary(
        output_directory=output.as_posix(),
        maintenance_notice_rows=maintenance_count,
        notice_version_history_rows=notice_version_history_count,
        outage_report_rows=report_count,
        latest_capacity_rows=latest_capacity_count,
        capacity_revision_rows=revision_count,
        location_rows=location_count,
        operational_capacity_rows=operational_capacity_count,
        operational_capacity_capture_rows=operational_capacity_capture_count,
        alert_rows=alert_count,
        daily_market_state_rows=daily_market_state_count,
        transport_impact_rows=transport_impact_count,
        market_context_rows=market_context_count,
    )
