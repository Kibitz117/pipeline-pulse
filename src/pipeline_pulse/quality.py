from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pendulum

from .database import connect_database, initialize_database
from .sources.kinder_morgan_capacity import (
    EXPECTED_POINT_CAPACITY_SCHEMA_SHA256,
    EXPECTED_SEGMENT_CAPACITY_SCHEMA_SHA256,
)
from .sources.kinder_morgan_locations import EXPECTED_LOCATION_SCHEMA_SHA256


class DatasetEmptyError(RuntimeError):
    """The requested source has no successful parsed capture yet."""


@dataclass(frozen=True)
class NoticeIndexQualityReport:
    pipeline_id: str
    fetch_runs_total: int
    fetch_runs_completed: int
    fetch_runs_failed: int
    artifacts_total: int
    index_page_captures_total: int
    index_export_captures_total: int
    distinct_notices_total: int
    latest_capture_kind: str
    latest_source_reported_row_count: int
    latest_row_count_mismatch: bool
    latest_observed_at: str
    latest_page_index: int
    advertised_page_count: int
    advertised_row_count: int
    latest_parsed_row_count: int
    latest_page_fill_ratio: float
    latest_advertised_row_coverage_ratio: float
    earliest_posted_at: str
    latest_posted_at: str
    missing_effective_start_count: int
    missing_effective_end_count: int
    missing_subject_count: int
    current_missing_subject_count: int
    notices_with_detail_count: int
    notices_missing_detail_count: int
    detail_observation_count: int
    semantic_notice_version_count: int
    notice_revision_observation_count: int
    detail_fetch_runs_total: int
    detail_fetch_runs_completed: int
    detail_fetch_runs_failed: int
    unresolved_detail_failure_count: int
    maintenance_notice_count: int
    maintenance_detail_count: int
    outage_report_vintage_count: int
    outage_impact_row_count: int
    outage_reduction_mismatch_count: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class LocationQualityReport:
    pipeline_id: str
    status: str
    collection_runs_total: int
    collection_runs_completed: int
    collection_runs_failed: int
    unresolved_collection_failures: int
    latest_collection_status: str
    capture_count: int
    latest_artifact_id: str
    latest_observed_at: str
    latest_source_as_of: str
    previous_source_as_of: str | None
    source_clock_regressed: bool
    source_clock_regression_count: int
    parser_version: str
    source_column_count: int
    schema_sha256: str
    expected_schema_sha256: str
    schema_matches_expected: bool
    location_count: int
    previous_location_count: int | None
    location_count_change: int | None
    unique_location_count: int
    state_count: int
    county_or_area_count: int
    segment_count: int
    missing_name_count: int
    missing_county_count: int
    missing_segment_count: int
    missing_zone_count: int
    invalid_flow_role_count: int
    invalid_state_count: int
    invalid_effective_interval_count: int
    geocoded_location_count: int
    geocoded_location_ratio: float
    unmapped_location_count: int
    unmapped_geographies: tuple[str, ...]
    outage_segment_count: int
    outage_segment_location_match_count: int
    outage_segment_location_match_ratio: float
    findings: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class CapacityQualityReport:
    pipeline_id: str
    status: str
    collection_runs_total: int
    collection_runs_completed: int
    collection_runs_failed: int
    unresolved_collection_failures: int
    latest_collection_status: str
    capture_count: int
    latest_bundle_capture_count: int
    latest_observed_at: str
    latest_source_posted_at: str
    gas_days: tuple[str, ...]
    cycles: tuple[str, ...]
    gas_day_and_cycle_aligned: bool
    point_delivery_rows: int
    point_receipt_rows: int
    segment_rows: int
    footer_count_mismatch_count: int
    schema_mismatch_count: int
    available_reconciliation_mismatch_count: int
    point_rows: int
    matched_facility_rows: int
    facility_match_ratio: float
    capacity_rows: int
    matched_segment_rows: int
    segment_match_ratio: float
    findings: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class TgpQualityReport:
    overall_status: str
    agent_input_ready: bool
    notice_index: NoticeIndexQualityReport
    locations: LocationQualityReport | None
    capacity: CapacityQualityReport | None
    artifacts: ArtifactIntegrityReport
    findings: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class DatasetStatusExport:
    output_path: str
    generated_at_utc: str
    overall_status: str
    agent_input_ready: bool


@dataclass(frozen=True)
class ArtifactIntegrityReport:
    status: str
    artifact_count: int
    files_checked: int
    missing_file_count: int
    hash_mismatch_count: int
    size_mismatch_count: int
    unprocessed_artifact_count: int
    invalid_clock_count: int
    missing_artifact_ids: tuple[str, ...]
    hash_mismatch_artifact_ids: tuple[str, ...]
    size_mismatch_artifact_ids: tuple[str, ...]


def build_notice_index_quality_report(
    database_path: str | Path,
    *,
    pipeline_id: str = "TGP",
    source_code: str = "km_tgp_critical",
) -> NoticeIndexQualityReport:
    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        run_counts = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE status = 'completed'),
                count(*) FILTER (WHERE status = 'failed')
            FROM fetch_runs
            WHERE source_code = ?
            """,
            [source_code],
        ).fetchone()
        artifact_count = connection.execute(
            "SELECT count(*) FROM source_artifacts WHERE source_code = ?",
            [source_code],
        ).fetchone()[0]
        page_count = connection.execute(
            """
            SELECT count(*)
            FROM notice_index_pages
            WHERE pipeline_id = ?
            """,
            [pipeline_id],
        ).fetchone()[0]
        latest_portal_page_count = connection.execute(
            """
            SELECT page_count
            FROM notice_index_pages
            WHERE pipeline_id = ?
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            [pipeline_id],
        ).fetchone()[0]
        export_count = connection.execute(
            """
            SELECT count(*)
            FROM notice_index_exports
            WHERE pipeline_id = ?
            """,
            [pipeline_id],
        ).fetchone()[0]
        distinct_notice_count = connection.execute(
            """
            SELECT count(DISTINCT notice_id)
            FROM notice_index_observations
            WHERE pipeline_id = ?
            """,
            [pipeline_id],
        ).fetchone()[0]
        latest_page = connection.execute(
            """
            SELECT
                artifact_id,
                strftime(
                    observed_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ),
                capture_kind, page_index, page_size, page_count,
                advertised_row_count, parsed_row_count,
                source_reported_row_count
            FROM (
                SELECT
                    artifact_id, observed_at, 'page' AS capture_kind,
                    page_index, page_size, page_count,
                    total_row_count AS advertised_row_count,
                    parsed_row_count,
                    total_row_count AS source_reported_row_count
                FROM notice_index_pages
                WHERE pipeline_id = ?
                UNION ALL
                SELECT
                    artifact_id, observed_at, 'xlsx_summary_all' AS capture_kind,
                    0 AS page_index, total_row_count AS page_size,
                    1 AS page_count, index_advertised_row_count,
                    parsed_row_count, source_footer_row_count
                FROM notice_index_exports
                WHERE pipeline_id = ?
            ) AS capture
            ORDER BY observed_at DESC
            LIMIT 1
            """,
            [pipeline_id, pipeline_id],
        ).fetchone()
        if latest_page is None:
            raise DatasetEmptyError(
                f"no parsed notice-index capture exists for {pipeline_id}"
            )
        artifact_id = latest_page[0]
        observation_stats = connection.execute(
            """
            SELECT
                strftime(
                    min(posted_at) AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ),
                strftime(
                    max(posted_at) AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ),
                count(*) FILTER (WHERE effective_start IS NULL),
                count(*) FILTER (WHERE effective_end IS NULL),
                count(*) FILTER (WHERE nullif(trim(subject), '') IS NULL)
            FROM notice_index_observations
            WHERE artifact_id = ?
            """,
            [artifact_id],
        ).fetchone()
        current_missing_subject_count = connection.execute(
            """
            SELECT count(*) FILTER (WHERE subject IS NULL)
            FROM current_notice_index
            WHERE pipeline_id = ?
            """,
            [pipeline_id],
        ).fetchone()[0]
        detail_counts = connection.execute(
            """
            SELECT
                count(DISTINCT version.notice_id),
                count(DISTINCT current.notice_id) -
                    count(DISTINCT version.notice_id)
            FROM current_notice_index AS current
            LEFT JOIN notice_versions AS version
              ON version.pipeline_id = current.pipeline_id
             AND version.notice_id = current.notice_id
            WHERE current.pipeline_id = ?
            """,
            [pipeline_id],
        ).fetchone()
        revision_counts = connection.execute(
            """
            SELECT
                count(*),
                count(DISTINCT notice_id || ':' || version_sha256),
                count(*) FILTER (WHERE is_revision_observation)
            FROM tgp_notice_version_timeline
            """
        ).fetchone()
        detail_run_counts = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE status = 'completed'),
                count(*) FILTER (WHERE status = 'failed')
            FROM fetch_runs
            WHERE source_code = 'km_tgp_notice_detail'
            """
        ).fetchone()
        unresolved_detail_failures = connection.execute(
            """
            WITH attempts AS (
                SELECT
                    json_extract_string(config, '$.notice_id') AS notice_id,
                    bool_or(status = 'failed') AS has_failure,
                    bool_or(status = 'completed') AS has_success
                FROM fetch_runs
                WHERE source_code = 'km_tgp_notice_detail'
                GROUP BY 1
            )
            SELECT count(*)
            FROM attempts
            WHERE has_failure AND NOT has_success
            """
        ).fetchone()[0]
        maintenance_counts = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE detail.notice_id IS NOT NULL)
            FROM current_notice_index AS current
            LEFT JOIN tgp_maintenance_notices AS detail
              ON detail.pipeline_id = current.pipeline_id
             AND detail.notice_id = current.notice_id
            WHERE current.pipeline_id = ?
              AND current.notice_type_primary = 'MAINTENANCE'
            """,
            [pipeline_id],
        ).fetchone()
        outage_counts = connection.execute(
            """
            SELECT
                (
                    SELECT count(DISTINCT notice_id || ':' || version_sha256)
                    FROM tgp_outage_report_summary
                ),
                count(*),
                count(*) FILTER (WHERE reduction_reconciles = false)
            FROM outage_impact_observations
            WHERE pipeline_id = ?
            """,
            [pipeline_id],
        ).fetchone()
    finally:
        connection.close()

    parsed_row_count = latest_page[7]
    page_size = latest_page[4]
    total_row_count = latest_page[6]
    return NoticeIndexQualityReport(
        pipeline_id=pipeline_id,
        fetch_runs_total=run_counts[0],
        fetch_runs_completed=run_counts[1],
        fetch_runs_failed=run_counts[2],
        artifacts_total=artifact_count,
        index_page_captures_total=page_count,
        index_export_captures_total=export_count,
        distinct_notices_total=distinct_notice_count,
        latest_observed_at=latest_page[1],
        latest_capture_kind=latest_page[2],
        latest_source_reported_row_count=latest_page[8],
        latest_row_count_mismatch=latest_page[8] != parsed_row_count,
        latest_page_index=latest_page[3],
        advertised_page_count=latest_portal_page_count,
        advertised_row_count=total_row_count,
        latest_parsed_row_count=parsed_row_count,
        latest_page_fill_ratio=parsed_row_count / page_size,
        latest_advertised_row_coverage_ratio=parsed_row_count / total_row_count,
        earliest_posted_at=observation_stats[0],
        latest_posted_at=observation_stats[1],
        missing_effective_start_count=observation_stats[2],
        missing_effective_end_count=observation_stats[3],
        missing_subject_count=observation_stats[4],
        current_missing_subject_count=current_missing_subject_count,
        notices_with_detail_count=detail_counts[0],
        notices_missing_detail_count=detail_counts[1],
        detail_observation_count=revision_counts[0],
        semantic_notice_version_count=revision_counts[1],
        notice_revision_observation_count=revision_counts[2],
        detail_fetch_runs_total=detail_run_counts[0],
        detail_fetch_runs_completed=detail_run_counts[1],
        detail_fetch_runs_failed=detail_run_counts[2],
        unresolved_detail_failure_count=unresolved_detail_failures,
        maintenance_notice_count=maintenance_counts[0],
        maintenance_detail_count=maintenance_counts[1],
        outage_report_vintage_count=outage_counts[0],
        outage_impact_row_count=outage_counts[1],
        outage_reduction_mismatch_count=outage_counts[2],
    )


def build_location_quality_report(
    database_path: str | Path,
    *,
    pipeline_id: str = "TGP",
) -> LocationQualityReport:
    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        captures = connection.execute(
            """
            SELECT
                artifact_id,
                strftime(observed_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ'),
                strftime(source_as_of AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ'),
                row_count,
                source_column_count,
                schema_sha256,
                parser_version
            FROM location_exports
            WHERE pipeline_id = ?
            ORDER BY observed_at DESC, artifact_id DESC
            LIMIT 2
            """,
            [pipeline_id],
        ).fetchall()
        if not captures:
            raise DatasetEmptyError(
                f"no parsed location capture exists for {pipeline_id}"
            )
        capture_count = connection.execute(
            "SELECT count(*) FROM location_exports WHERE pipeline_id = ?",
            [pipeline_id],
        ).fetchone()[0]
        source_clock_regression_count = connection.execute(
            """
            WITH ordered AS (
                SELECT
                    source_as_of,
                    lag(source_as_of) OVER (
                        ORDER BY observed_at, artifact_id
                    ) AS previous_source_as_of
                FROM location_exports
                WHERE pipeline_id = ?
            )
            SELECT count(*)
            FROM ordered
            WHERE source_as_of < previous_source_as_of
            """,
            [pipeline_id],
        ).fetchone()[0]
        latest = captures[0]
        previous = captures[1] if len(captures) > 1 else None
        run_counts = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE status = 'completed'),
                count(*) FILTER (WHERE status = 'failed')
            FROM fetch_runs
            WHERE source_code = 'tgp_location_reference'
            """
        ).fetchone()
        latest_run = connection.execute(
            """
            SELECT status
            FROM fetch_runs
            WHERE source_code = 'tgp_location_reference'
            ORDER BY requested_at DESC
            LIMIT 1
            """
        ).fetchone()
        unresolved_failures = connection.execute(
            """
            WITH last_success AS (
                SELECT max(requested_at) AS requested_at
                FROM fetch_runs
                WHERE source_code = 'tgp_location_reference'
                  AND status = 'completed'
            )
            SELECT count(*)
            FROM fetch_runs, last_success
            WHERE source_code = 'tgp_location_reference'
              AND status = 'failed'
              AND (
                  last_success.requested_at IS NULL
                  OR fetch_runs.requested_at > last_success.requested_at
              )
            """
        ).fetchone()[0]
        stats = connection.execute(
            """
            SELECT
                count(*),
                count(DISTINCT operator_location_id),
                count(DISTINCT state_abbreviation),
                count(DISTINCT state_abbreviation || ':' || county_name),
                count(DISTINCT operator_segment_id),
                count(*) FILTER (WHERE nullif(trim(location_name), '') IS NULL),
                count(*) FILTER (WHERE nullif(trim(county_name), '') IS NULL),
                count(*) FILTER (WHERE nullif(trim(operator_segment_id), '') IS NULL),
                count(*) FILTER (
                    WHERE nullif(trim(receipt_zone), '') IS NULL
                      AND nullif(trim(delivery_zone), '') IS NULL
                ),
                count(*) FILTER (WHERE flow_role NOT IN ('R', 'D', 'B')),
                count(*) FILTER (
                    WHERE NOT regexp_full_match(state_abbreviation, '[A-Z]{2}')
                ),
                count(*) FILTER (
                    WHERE inactive_date IS NOT NULL
                      AND effective_date IS NOT NULL
                      AND inactive_date < effective_date
                ),
                count(*) FILTER (WHERE latitude IS NOT NULL)
            FROM tgp_location_map
            """
        ).fetchone()
        unmapped_rows = connection.execute(
            """
            SELECT
                state_abbreviation || ':' || county_name || ' (' ||
                    CAST(count(*) AS VARCHAR) || ')' AS geography
            FROM tgp_location_map
            WHERE latitude IS NULL
            GROUP BY state_abbreviation, county_name
            ORDER BY count(*) DESC, state_abbreviation, county_name
            """
        ).fetchall()
        segment_stats = connection.execute(
            """
            WITH outage_segments AS (
                SELECT DISTINCT operator_segment_id
                FROM outage_impact_observations
                WHERE pipeline_id = ?
                  AND operator_segment_id IS NOT NULL
            ),
            location_segments AS (
                SELECT DISTINCT operator_segment_id
                FROM current_pipeline_locations
                WHERE pipeline_id = ?
                  AND operator_segment_id IS NOT NULL
            )
            SELECT
                count(*),
                count(*) FILTER (
                    WHERE location_segments.operator_segment_id IS NOT NULL
                )
            FROM outage_segments
            LEFT JOIN location_segments USING (operator_segment_id)
            """,
            [pipeline_id, pipeline_id],
        ).fetchone()
    finally:
        connection.close()

    location_count = int(stats[0])
    geocoded_count = int(stats[12])
    geocoded_ratio = geocoded_count / location_count if location_count else 0.0
    outage_segment_count = int(segment_stats[0])
    outage_segment_match_count = int(segment_stats[1])
    segment_match_ratio = (
        outage_segment_match_count / outage_segment_count
        if outage_segment_count
        else 1.0
    )
    previous_count = int(previous[3]) if previous is not None else None
    count_change = (
        int(latest[3]) - previous_count if previous_count is not None else None
    )
    source_clock_regressed = bool(
        previous is not None and str(latest[2]) < str(previous[2])
    )
    schema_matches = latest[5] == EXPECTED_LOCATION_SCHEMA_SHA256

    findings: list[str] = []
    hard_failure = False
    if latest_run is not None and latest_run[0] == "failed":
        findings.append("latest location collection failed")
        hard_failure = True
    if unresolved_failures:
        findings.append(f"{unresolved_failures} unresolved collection failure(s)")
        hard_failure = True
    if not schema_matches:
        findings.append("location export schema differs from the tested contract")
        hard_failure = True
    if stats[0] != stats[1]:
        findings.append("operator location IDs are not unique")
        hard_failure = True
    if stats[9] or stats[10] or stats[11]:
        findings.append("one or more normalized location invariants failed")
        hard_failure = True
    if geocoded_ratio < 0.90:
        findings.append("fewer than 90% of locations have auditable coordinates")
        hard_failure = True
    if source_clock_regressed:
        findings.append("operator source_as_of clock regressed between captures")
    elif source_clock_regression_count:
        findings.append(
            f"operator source_as_of clock regressed in "
            f"{source_clock_regression_count} historical capture(s)"
        )
    if previous_count and abs(count_change or 0) / previous_count > 0.10:
        findings.append("location row count changed by more than 10%")
    if stats[5] or stats[6] or stats[7] or stats[8]:
        findings.append(
            "one or more location identity, segment, or zone fields are missing"
        )
    if geocoded_ratio < 0.98:
        findings.append("fewer than 98% of locations have auditable coordinates")
    if segment_match_ratio < 0.90:
        findings.append("fewer than 90% of outage-report segments match location data")
    status = "failed" if hard_failure else "warning" if findings else "passed"

    return LocationQualityReport(
        pipeline_id=pipeline_id,
        status=status,
        collection_runs_total=int(run_counts[0]),
        collection_runs_completed=int(run_counts[1]),
        collection_runs_failed=int(run_counts[2]),
        unresolved_collection_failures=int(unresolved_failures),
        latest_collection_status=(latest_run[0] if latest_run else "not_recorded"),
        capture_count=int(capture_count),
        latest_artifact_id=str(latest[0]),
        latest_observed_at=str(latest[1]),
        latest_source_as_of=str(latest[2]),
        previous_source_as_of=(str(previous[2]) if previous else None),
        source_clock_regressed=source_clock_regressed,
        source_clock_regression_count=int(source_clock_regression_count),
        parser_version=str(latest[6] or "unknown"),
        source_column_count=int(latest[4] or 0),
        schema_sha256=str(latest[5] or ""),
        expected_schema_sha256=EXPECTED_LOCATION_SCHEMA_SHA256,
        schema_matches_expected=schema_matches,
        location_count=location_count,
        previous_location_count=previous_count,
        location_count_change=count_change,
        unique_location_count=int(stats[1]),
        state_count=int(stats[2]),
        county_or_area_count=int(stats[3]),
        segment_count=int(stats[4]),
        missing_name_count=int(stats[5]),
        missing_county_count=int(stats[6]),
        missing_segment_count=int(stats[7]),
        missing_zone_count=int(stats[8]),
        invalid_flow_role_count=int(stats[9]),
        invalid_state_count=int(stats[10]),
        invalid_effective_interval_count=int(stats[11]),
        geocoded_location_count=geocoded_count,
        geocoded_location_ratio=round(geocoded_ratio, 6),
        unmapped_location_count=location_count - geocoded_count,
        unmapped_geographies=tuple(row[0] for row in unmapped_rows),
        outage_segment_count=outage_segment_count,
        outage_segment_location_match_count=outage_segment_match_count,
        outage_segment_location_match_ratio=round(segment_match_ratio, 6),
        findings=tuple(findings),
    )


def build_capacity_quality_report(
    database_path: str | Path,
    *,
    pipeline_id: str = "TGP",
) -> CapacityQualityReport:
    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        run_counts = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE status = 'completed'),
                count(*) FILTER (WHERE status = 'failed')
            FROM fetch_runs
            WHERE source_code = 'tgp_operational_capacity'
            """
        ).fetchone()
        latest_run = connection.execute(
            """
            SELECT status
            FROM fetch_runs
            WHERE source_code = 'tgp_operational_capacity'
            ORDER BY requested_at DESC, run_id DESC
            LIMIT 1
            """
        ).fetchone()
        unresolved_failures = connection.execute(
            """
            WITH last_success AS (
                SELECT max(requested_at) AS requested_at
                FROM fetch_runs
                WHERE source_code = 'tgp_operational_capacity'
                  AND status = 'completed'
            )
            SELECT count(*)
            FROM fetch_runs, last_success
            WHERE source_code = 'tgp_operational_capacity'
              AND status = 'failed'
              AND (
                  last_success.requested_at IS NULL
                  OR fetch_runs.requested_at > last_success.requested_at
              )
            """
        ).fetchone()[0]
        capture_count = connection.execute(
            "SELECT count(*) FROM capacity_exports WHERE pipeline_id = ?",
            [pipeline_id],
        ).fetchone()[0]
        latest_bundle = connection.execute(
            """
            SELECT artifact.run_id
            FROM capacity_exports AS export
            JOIN source_artifacts AS artifact USING (artifact_id)
            WHERE export.pipeline_id = ?
            ORDER BY export.observed_at DESC, export.artifact_id DESC
            LIMIT 1
            """,
            [pipeline_id],
        ).fetchone()
        if latest_bundle is None:
            raise DatasetEmptyError(
                f"no parsed operational-capacity capture exists for {pipeline_id}"
            )
        captures = connection.execute(
            """
            SELECT
                export.artifact_id,
                export.capacity_kind,
                export.point_role,
                CAST(export.gas_day AS VARCHAR),
                export.cycle,
                strftime(
                    export.source_posted_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ),
                strftime(
                    export.observed_at AT TIME ZONE 'UTC',
                    '%Y-%m-%dT%H:%M:%S.%fZ'
                ),
                export.source_footer_row_count,
                export.parsed_row_count,
                export.schema_sha256
            FROM capacity_exports AS export
            JOIN source_artifacts AS artifact USING (artifact_id)
            WHERE export.pipeline_id = ?
              AND artifact.run_id = ?
            ORDER BY export.capacity_kind, export.point_role
            """,
            [pipeline_id, latest_bundle[0]],
        ).fetchall()
        artifact_ids = [str(row[0]) for row in captures]
        placeholders = ", ".join("?" for _ in artifact_ids)
        observation_stats = connection.execute(
            f"""
            SELECT
                count(*),
                count(*) FILTER (WHERE capacity_kind = 'point'),
                count(*) FILTER (
                    WHERE capacity_kind = 'point' AND facility_id IS NOT NULL
                ),
                count(*) FILTER (WHERE segment_id IS NOT NULL),
                count(*) FILTER (WHERE available_reconciles = false)
            FROM capacity_observations
            WHERE artifact_id IN ({placeholders})
            """,
            artifact_ids,
        ).fetchone()
    finally:
        connection.close()

    expected_schemas = {
        "point": EXPECTED_POINT_CAPACITY_SCHEMA_SHA256,
        "segment": EXPECTED_SEGMENT_CAPACITY_SCHEMA_SHA256,
    }
    footer_mismatches = sum(row[7] != row[8] for row in captures)
    schema_mismatches = sum(
        row[9] != expected_schemas.get(str(row[1])) for row in captures
    )
    gas_days = tuple(sorted({str(row[3]) for row in captures}))
    cycles = tuple(sorted({str(row[4]) for row in captures}))
    aligned = len(gas_days) == 1 and len(cycles) == 1
    row_counts = {
        (str(row[1]), str(row[2]) if row[2] is not None else None): int(row[8])
        for row in captures
    }
    capacity_rows = int(observation_stats[0])
    point_rows = int(observation_stats[1])
    matched_facility_rows = int(observation_stats[2])
    matched_segment_rows = int(observation_stats[3])
    reconciliation_mismatches = int(observation_stats[4])
    facility_match_ratio = matched_facility_rows / point_rows if point_rows else 0.0
    segment_match_ratio = matched_segment_rows / capacity_rows if capacity_rows else 0.0

    findings: list[str] = []
    hard_failure = False
    if latest_run is not None and latest_run[0] == "failed":
        findings.append("latest operational-capacity collection failed")
        hard_failure = True
    if unresolved_failures:
        findings.append(f"{unresolved_failures} unresolved collection failure(s)")
        hard_failure = True
    if len(captures) != 3 or set(row_counts) != {
        ("point", "delivery"),
        ("point", "receipt"),
        ("segment", None),
    }:
        findings.append("latest capacity bundle is missing an expected export")
        hard_failure = True
    if footer_mismatches:
        findings.append("capacity export footer counts do not reconcile")
        hard_failure = True
    if schema_mismatches:
        findings.append("capacity export schema differs from the tested contract")
        hard_failure = True
    if reconciliation_mismatches:
        findings.append(
            "published operationally available capacity differs from the "
            "simple operating-minus-scheduled check on one or more rows; "
            "operator caveats permit netting and other intraday adjustments"
        )
    if facility_match_ratio < 0.90:
        findings.append("fewer than 90% of point rows match the location reference")
        hard_failure = True
    if segment_match_ratio < 0.90:
        findings.append("fewer than 90% of capacity rows match a native segment")
        hard_failure = True
    if not aligned:
        findings.append("capacity bundle spans more than one gas day or cycle")
    status = "failed" if hard_failure else "warning" if findings else "passed"

    return CapacityQualityReport(
        pipeline_id=pipeline_id,
        status=status,
        collection_runs_total=int(run_counts[0]),
        collection_runs_completed=int(run_counts[1]),
        collection_runs_failed=int(run_counts[2]),
        unresolved_collection_failures=int(unresolved_failures),
        latest_collection_status=(latest_run[0] if latest_run else "not_recorded"),
        capture_count=int(capture_count),
        latest_bundle_capture_count=len(captures),
        latest_observed_at=max(str(row[6]) for row in captures),
        latest_source_posted_at=max(str(row[5]) for row in captures),
        gas_days=gas_days,
        cycles=cycles,
        gas_day_and_cycle_aligned=aligned,
        point_delivery_rows=row_counts.get(("point", "delivery"), 0),
        point_receipt_rows=row_counts.get(("point", "receipt"), 0),
        segment_rows=row_counts.get(("segment", None), 0),
        footer_count_mismatch_count=int(footer_mismatches),
        schema_mismatch_count=int(schema_mismatches),
        available_reconciliation_mismatch_count=reconciliation_mismatches,
        point_rows=point_rows,
        matched_facility_rows=matched_facility_rows,
        facility_match_ratio=round(facility_match_ratio, 6),
        capacity_rows=capacity_rows,
        matched_segment_rows=matched_segment_rows,
        segment_match_ratio=round(segment_match_ratio, 6),
        findings=tuple(findings),
    )


def build_artifact_integrity_report(
    database_path: str | Path,
) -> ArtifactIntegrityReport:
    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        rows = connection.execute(
            """
            SELECT
                artifact_id,
                raw_path,
                content_sha256,
                try_cast(json_extract_string(metadata, '$.size_bytes') AS BIGINT)
            FROM source_artifacts
            ORDER BY artifact_id
            """
        ).fetchall()
        audit_counts = connection.execute(
            """
            SELECT
                count(*) FILTER (WHERE processed_at IS NULL),
                count(*) FILTER (
                    WHERE received_at < requested_at
                       OR recorded_at < received_at
                       OR (processed_at IS NOT NULL AND processed_at < received_at)
                )
            FROM source_artifacts
            """
        ).fetchone()
    finally:
        connection.close()

    missing: list[str] = []
    hash_mismatches: list[str] = []
    size_mismatches: list[str] = []
    checked = 0
    for artifact_id, raw_path, expected_hash, expected_size in rows:
        path = Path(str(raw_path))
        if not path.is_file():
            missing.append(str(artifact_id))
            continue
        checked += 1
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected_hash:
            hash_mismatches.append(str(artifact_id))
        if expected_size is not None and len(body) != int(expected_size):
            size_mismatches.append(str(artifact_id))
    hard_failure = bool(
        missing or hash_mismatches or size_mismatches or audit_counts[1]
    )
    status = "failed" if hard_failure else "warning" if audit_counts[0] else "passed"
    return ArtifactIntegrityReport(
        status=status,
        artifact_count=len(rows),
        files_checked=checked,
        missing_file_count=len(missing),
        hash_mismatch_count=len(hash_mismatches),
        size_mismatch_count=len(size_mismatches),
        unprocessed_artifact_count=int(audit_counts[0]),
        invalid_clock_count=int(audit_counts[1]),
        missing_artifact_ids=tuple(missing),
        hash_mismatch_artifact_ids=tuple(hash_mismatches),
        size_mismatch_artifact_ids=tuple(size_mismatches),
    )


def build_tgp_quality_report(
    database_path: str | Path,
    *,
    pipeline_id: str = "TGP",
) -> TgpQualityReport:
    notice = build_notice_index_quality_report(
        database_path,
        pipeline_id=pipeline_id,
    )
    artifacts = build_artifact_integrity_report(database_path)
    findings: list[str] = []
    if notice.latest_row_count_mismatch:
        findings.append("notice index source-reported row count does not reconcile")
    if notice.unresolved_detail_failure_count:
        findings.append("notice details have unresolved collection failures")
    if notice.maintenance_detail_count != notice.maintenance_notice_count:
        findings.append("maintenance detail coverage is incomplete")
    if notice.outage_reduction_mismatch_count:
        findings.append("operator capacity reductions contain flagged inconsistencies")
    try:
        locations = build_location_quality_report(
            database_path,
            pipeline_id=pipeline_id,
        )
    except DatasetEmptyError:
        locations = None
        findings.append("location reference has no successful parsed capture")
    if locations is not None:
        findings.extend(f"locations: {finding}" for finding in locations.findings)
    try:
        capacity = build_capacity_quality_report(
            database_path,
            pipeline_id=pipeline_id,
        )
    except DatasetEmptyError:
        capacity = None
        findings.append("operational capacity has no successful parsed capture")
    if capacity is not None:
        findings.extend(f"capacity: {finding}" for finding in capacity.findings)
    if artifacts.status == "failed":
        findings.append("one or more raw artifacts failed integrity verification")
    elif artifacts.unprocessed_artifact_count:
        findings.append(
            f"{artifacts.unprocessed_artifact_count} archived artifact(s) are "
            "unprocessed, typically from visible failed parser runs"
        )

    hard_failure = bool(
        notice.unresolved_detail_failure_count
        or notice.maintenance_detail_count != notice.maintenance_notice_count
        or locations is None
        or (locations is not None and locations.status == "failed")
        or capacity is None
        or (capacity is not None and capacity.status == "failed")
        or artifacts.status == "failed"
    )
    overall_status = "failed" if hard_failure else "warning" if findings else "passed"
    return TgpQualityReport(
        overall_status=overall_status,
        agent_input_ready=not hard_failure,
        notice_index=notice,
        locations=locations,
        capacity=capacity,
        artifacts=artifacts,
        findings=tuple(findings),
    )


def export_tgp_dataset_status(
    database_path: str | Path,
    output_path: str | Path,
) -> DatasetStatusExport:
    """Write the current quality snapshot after all derived tables are rebuilt."""
    report = build_tgp_quality_report(database_path)
    generated_at = pendulum.now("UTC")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "tgp_dataset_status_v1",
                "generated_at_utc": generated_at.to_iso8601_string(),
                **asdict(report),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)
    return DatasetStatusExport(
        output_path=destination.as_posix(),
        generated_at_utc=generated_at.to_iso8601_string(),
        overall_status=report.overall_status,
        agent_input_ready=report.agent_input_ready,
    )
