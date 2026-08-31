from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import pendulum

from .database import connect_database, initialize_database


TGP_DIRECTION_MAP = {"FH": "TD1", "BH": "TD2"}
TGP_DIRECTION_NOTICE_URL = (
    "https://pipeline2.kindermorgan.com/Notices/NoticeDetail.aspx"
    "?code=TGP&notc_nbr=363144"
)
TGP_SEGMENT_MAP_URL = (
    "https://pipeline2.kindermorgan.com/Documents/TGP/"
    "TGP_Segment_-_Pin_Map-20260721125239.pdf"
)
CME_HENRY_HUB_URL = (
    "https://www.cmegroup.com/markets/energy/natural-gas/natural-gas.html"
)
PRICE_MAPPING_REASON = (
    "No licensed regional basis observations or exact TGP leg-to-contract "
    "mapping are loaded. Henry Hub NG is a national benchmark, not direct "
    "evidence of this TGP event's price exposure."
)


@dataclass(frozen=True)
class ImpactBuildSummary:
    report_notice_id: str
    report_artifact_id: str
    capacity_run_id: str
    assessment_count: int
    matched_count: int
    no_trade_mapping_count: int
    monitor_count: int
    research_scenario_count: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _normalize_station(value: str) -> str:
    without_segment = re.sub(r"\([^)]*\bseg(?:ment)?\b[^)]*\)", "", value, flags=re.I)
    expanded = re.sub(r"\bSTATION\b", "STA", without_segment, flags=re.I)
    return re.sub(r"[^A-Z0-9]", "", expanded.upper())


def _station_numbers(value: str) -> set[str]:
    return {
        str(int(match))
        for match in re.findall(r"\b(?:STATION|STA|MLV)\s*0*(\d+)\b", value, flags=re.I)
    }


def _match_capacity_row(
    station_label: str,
    candidates: list[dict[str, object]],
) -> tuple[dict[str, object] | None, str]:
    if not candidates:
        return None, "unmatched"
    target = _normalize_station(station_label)
    normalized_matches = [
        row
        for row in candidates
        if (
            target == _normalize_station(str(row["location_name"]))
            or (
                min(len(target), len(_normalize_station(str(row["location_name"])))) >= 5
                and (
                    target in _normalize_station(str(row["location_name"]))
                    or _normalize_station(str(row["location_name"])) in target
                )
            )
        )
    ]
    if len(normalized_matches) == 1:
        return normalized_matches[0], "normalized_name"

    target_numbers = _station_numbers(station_label)
    number_matches = [
        row
        for row in candidates
        if target_numbers.intersection(_station_numbers(str(row["location_name"])))
    ]
    if len(number_matches) == 1:
        return number_matches[0], "station_number"
    if len(candidates) == 1:
        return candidates[0], "unique_segment_direction"
    return None, "ambiguous"


def _normalize_zone(value: object) -> str | None:
    zone = str(value or "").strip().upper()
    if not zone:
        return None
    if zone.startswith("Z"):
        return zone
    if zone == "L":
        return "ZL"
    if zone.isdigit():
        return f"Z{int(zone)}"
    return f"Z{zone}"


def _dict_rows(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[object] | None = None,
) -> list[dict[str, object]]:
    result = connection.execute(query, parameters or [])
    columns = tuple(item[0] for item in result.description)
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def build_tgp_transport_impacts(
    database_path: str | Path,
    *,
    calculated_at: pendulum.DateTime | None = None,
) -> ImpactBuildSummary:
    calculation_time = (calculated_at or pendulum.now("UTC")).in_timezone("UTC")
    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        report_rows = _dict_rows(
            connection,
            """
            SELECT artifact_id, notice_id, report_updated_on
            FROM tgp_outage_report_summary
            ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
            LIMIT 1
            """,
        )
        if not report_rows:
            raise RuntimeError("no TGP outage-impact report is available")
        report = report_rows[0]
        capacity_runs = _dict_rows(
            connection,
            """
            SELECT artifact.run_id
            FROM capacity_exports AS export
            JOIN source_artifacts AS artifact USING (artifact_id)
            WHERE export.pipeline_id = 'TGP'
            ORDER BY export.observed_at DESC, export.artifact_id DESC
            LIMIT 1
            """,
        )
        if not capacity_runs:
            raise RuntimeError("no TGP operational-capacity bundle is available")
        capacity_run_id = str(capacity_runs[0]["run_id"])
        capacity_rows = _dict_rows(
            connection,
            """
            SELECT
                observation.capacity_observation_id,
                observation.artifact_id,
                observation.operator_segment_id,
                observation.location_name,
                observation.zone,
                observation.flow_direction,
                observation.gas_day,
                observation.cycle,
                observation.operating_capacity_dth_per_day,
                observation.scheduled_quantity_dth_per_day,
                observation.available_capacity_dth_per_day,
                observation.source_posted_at,
                export.comments AS operator_caveats
            FROM capacity_observations AS observation
            JOIN source_artifacts AS artifact USING (artifact_id)
            JOIN capacity_exports AS export USING (artifact_id)
            WHERE artifact.run_id = ?
              AND observation.capacity_kind = 'segment'
            ORDER BY observation.operator_segment_id, observation.flow_direction,
                     observation.location_name
            """,
            [capacity_run_id],
        )
        by_segment_direction: dict[tuple[str, str], list[dict[str, object]]] = {}
        for row in capacity_rows:
            key = (str(row["operator_segment_id"]), str(row["flow_direction"] or ""))
            by_segment_direction.setdefault(key, []).append(row)

        outage_rows = _dict_rows(
            connection,
            """
            SELECT *
            FROM outage_impact_observations
            WHERE artifact_id = ?
              AND calculated_reduction_dth_per_day > 0
            ORDER BY period_start, source_table_index, source_row_index
            """,
            [report["artifact_id"]],
        )
        parameters: list[list[object]] = []
        statuses: list[str] = []
        matched_count = 0
        for outage in outage_rows:
            outage_direction = str(outage["flow_direction"] or "").upper()
            capacity_direction = TGP_DIRECTION_MAP.get(outage_direction)
            segment = str(outage["operator_segment_id"] or "")
            candidates = (
                by_segment_direction.get((segment, capacity_direction), [])
                if capacity_direction and segment
                else []
            )
            capacity, match_method = _match_capacity_row(
                str(outage["station_label"]), candidates
            )
            unresolved: list[str] = []
            if capacity_direction is None:
                unresolved.append("Forecast direction is not FH or BH, so no TD1/TD2 mapping is available.")
            if not segment:
                unresolved.append("The outage row does not contain a native segment ID.")
            if match_method == "ambiguous":
                unresolved.append("Multiple capacity rows share this segment and direction; the station match is ambiguous.")
            elif capacity is None:
                unresolved.append("No current segment-capacity row matches the mapped segment and direction.")

            baseline_timing = "unmatched"
            conditional_shortfall: int | None = None
            forecast_headroom: int | None = None
            research_status = "no_trade_mapping"
            source_age_hours: float | None = None
            if capacity is not None:
                matched_count += 1
                gas_day = capacity["gas_day"]
                period_start = outage["period_start"]
                baseline_timing = (
                    "pre_event" if gas_day < period_start
                    else "same_day" if gas_day == period_start
                    else "post_event"
                )
                forecast_operating = int(outage["operating_capacity_dth_per_day"])
                baseline_scheduled = int(capacity["scheduled_quantity_dth_per_day"])
                conditional_shortfall = max(0, baseline_scheduled - forecast_operating)
                forecast_headroom = max(0, forecast_operating - baseline_scheduled)
                source_posted = pendulum.instance(capacity["source_posted_at"]).in_timezone("UTC")
                source_age_hours = max(
                    0.0, (calculation_time - source_posted).total_seconds() / 3600
                )
                unresolved.append(
                    "Future nominations and rerouting are not observed; the shortfall assumes the captured schedule is unchanged."
                )
                if baseline_timing == "post_event":
                    unresolved.append("The capacity gas day is after the forecast period began, so it is not a pre-event baseline.")
                if source_age_hours > 30:
                    unresolved.append("The operating-capacity source was more than 30 hours old when this assessment was built.")
                if (
                    conditional_shortfall > 0
                    and baseline_timing in {"pre_event", "same_day"}
                    and source_age_hours <= 30
                ):
                    research_status = "research_scenario"
                else:
                    research_status = "monitor"
            statuses.append(research_status)
            capacity_artifact_id = str(capacity["artifact_id"]) if capacity else None
            identity = "|".join(
                [
                    str(report["artifact_id"]),
                    str(outage["source_table_index"]),
                    str(outage["source_row_index"]),
                    str(outage["period_start"]),
                    str(outage["period_end"]),
                    capacity_artifact_id or capacity_run_id,
                ]
            )
            assessment_id = "tgp-impact:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            evidence = {
                "report_artifact_id": report["artifact_id"],
                "capacity_artifact_id": capacity_artifact_id,
                "capacity_run_id": capacity_run_id,
                "direction_mapping": {
                    "forecast": outage_direction or None,
                    "capacity": capacity_direction,
                    "method": "FH=operator default direction=TD1; BH=opposite direction=TD2",
                    "operator_direction_notice_url": TGP_DIRECTION_NOTICE_URL,
                    "operator_segment_map_url": TGP_SEGMENT_MAP_URL,
                },
                "calculation": (
                    "max(0, baseline scheduled quantity - forecast operating capacity)"
                ),
                "interpretation": (
                    "Conditional scheduled shortfall if the captured net schedule holds; "
                    "not physical flow, curtailment, or a price prediction."
                ),
                "operator_capacity_caveat": (
                    str(capacity["operator_caveats"]) if capacity else None
                ),
                "capacity_source_age_hours_at_calculation": (
                    round(source_age_hours, 2) if source_age_hours is not None else None
                ),
            }
            parameters.append(
                [
                    assessment_id,
                    "TGP",
                    report["artifact_id"],
                    report["notice_id"],
                    report["report_updated_on"],
                    outage["source_table_index"],
                    outage["source_row_index"],
                    outage["period_start"],
                    outage["period_end"],
                    outage["station_label"],
                    outage["operator_segment_id"],
                    outage["flow_direction"],
                    capacity_direction,
                    "operator_default_direction",
                    _normalize_zone(capacity["zone"]) if capacity else None,
                    capacity["capacity_observation_id"] if capacity else None,
                    capacity_artifact_id,
                    capacity["location_name"] if capacity else None,
                    capacity["gas_day"] if capacity else None,
                    capacity["cycle"] if capacity else None,
                    capacity["source_posted_at"] if capacity else None,
                    capacity["operating_capacity_dth_per_day"] if capacity else None,
                    capacity["scheduled_quantity_dth_per_day"] if capacity else None,
                    capacity["available_capacity_dth_per_day"] if capacity else None,
                    outage["nominal_capacity_dth_per_day"],
                    outage["operating_capacity_dth_per_day"],
                    outage["calculated_reduction_dth_per_day"],
                    conditional_shortfall,
                    forecast_headroom,
                    baseline_timing,
                    match_method,
                    research_status,
                    "unresolved",
                    PRICE_MAPPING_REASON,
                    CME_HENRY_HUB_URL,
                    json.dumps(unresolved, sort_keys=True),
                    json.dumps(evidence, sort_keys=True, default=str),
                    calculation_time,
                ]
            )
        if parameters:
            connection.executemany(
                """
                INSERT INTO tgp_transport_impact_assessments(
                    assessment_id, pipeline_id, report_artifact_id,
                    report_notice_id, report_updated_on, source_table_index,
                    source_row_index, period_start, period_end, station_label,
                    operator_segment_id, outage_flow_direction,
                    capacity_flow_direction, direction_mapping_method, tgp_zone,
                    capacity_observation_id, capacity_artifact_id,
                    capacity_location_name, baseline_gas_day, baseline_cycle,
                    baseline_source_posted_at,
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
                    benchmark_reference_url, unresolved_reasons, evidence,
                    calculated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (assessment_id) DO UPDATE SET
                    price_mapping_status = excluded.price_mapping_status,
                    price_mapping_reason = excluded.price_mapping_reason,
                    benchmark_reference_url = excluded.benchmark_reference_url
                """,
                parameters,
            )
    finally:
        connection.close()
    return ImpactBuildSummary(
        report_notice_id=str(report["notice_id"]),
        report_artifact_id=str(report["artifact_id"]),
        capacity_run_id=capacity_run_id,
        assessment_count=len(parameters),
        matched_count=matched_count,
        no_trade_mapping_count=statuses.count("no_trade_mapping"),
        monitor_count=statuses.count("monitor"),
        research_scenario_count=statuses.count("research_scenario"),
    )
