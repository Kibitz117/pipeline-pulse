from __future__ import annotations

from pathlib import Path

import duckdb
import pendulum


DEFAULT_HORIZON_DAYS = 30
REVIEW_THRESHOLD_DTH_PER_DAY = 50_000


def _rows(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[object] | None = None,
) -> list[dict[str, object]]:
    result = connection.execute(query, parameters or [])
    columns = tuple(description[0] for description in result.description)
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def _peak_day(rows: list[dict[str, object]]) -> dict[str, object] | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            int(row["largest_conditional_shortfall_dth_per_day"] or 0),
            int(row["largest_single_reduction_dth_per_day"] or 0),
            -int(str(row["date"]).replace("-", "")),
        ),
    )


def _screen_state(shortfall: int) -> str:
    if shortfall <= 0:
        return "no_modeled_gap"
    if shortfall < REVIEW_THRESHOLD_DTH_PER_DAY:
        return "below_review_threshold"
    return "active_review"


def _display_date(value: object) -> str:
    parsed = pendulum.parse(str(value), strict=False)
    if isinstance(parsed, pendulum.DateTime):
        return parsed.format("MMMM D")
    return str(value)


def build_tgp_daily_market_state(
    database_path: str | Path,
    *,
    decision_at: pendulum.DateTime | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> dict[str, object]:
    """Build an overlap-safe daily view of the latest known TGP outlook.

    Values are never summed across stations or segments. A molecule may traverse
    several constrained locations, so each day reports the largest single
    reduction and largest single conditional schedule gap instead.
    """
    if horizon_days < 7 or horizon_days > 90:
        raise ValueError("horizon_days must be between 7 and 90")
    decision_time = (decision_at or pendulum.now("UTC")).in_timezone("UTC")
    start_date = decision_time.date()
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        selected_report = connection.execute(
            """
            SELECT artifact_id, notice_id, report_updated_on, posted_at
            FROM tgp_outage_report_summary
            WHERE observed_at <= ?
            ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
            LIMIT 1
            """,
            [decision_time],
        ).fetchone()
        if selected_report is None:
            return {
                "pipeline_id": "TGP",
                "decision_at_utc": decision_time.to_iso8601_string(),
                "horizon_days": horizon_days,
                "aggregation_method": "largest_single_constraint_no_sum",
                "review_threshold_dth_per_day": REVIEW_THRESHOLD_DTH_PER_DAY,
                "summary": None,
                "days": [],
                "corridors": [],
            }
        report_artifact_id = str(selected_report[0])
        daily_rows = _rows(
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
            ), segment_geography AS (
                SELECT
                    operator_segment_id,
                    string_agg(
                        DISTINCT state_abbreviation, ', '
                        ORDER BY state_abbreviation
                    ) FILTER (WHERE state_abbreviation IS NOT NULL)
                        AS segment_states
                FROM tgp_location_map
                WHERE operator_segment_id IS NOT NULL
                GROUP BY operator_segment_id
            ), calendar AS (
                SELECT
                    CAST(? AS DATE) + CAST(day_offset AS INTEGER) AS gas_day,
                    CAST(day_offset AS INTEGER) AS day_offset
                FROM range(?) AS offsets(day_offset)
            ), active AS (
                SELECT
                    calendar.gas_day,
                    calendar.day_offset,
                    assessment.*,
                    geography.segment_states,
                    row_number() OVER (
                        PARTITION BY calendar.gas_day
                        ORDER BY
                            coalesce(
                                assessment.conditional_scheduled_shortfall_dth_per_day,
                                -1
                            ) DESC,
                            coalesce(assessment.gross_reduction_dth_per_day, -1) DESC,
                            assessment.station_label,
                            assessment.operator_segment_id
                    ) AS peak_rank
                FROM calendar
                LEFT JOIN latest_assessment AS assessment
                  ON calendar.gas_day BETWEEN assessment.period_start
                                          AND assessment.period_end
                LEFT JOIN segment_geography AS geography
                  ON geography.operator_segment_id = assessment.operator_segment_id
            )
            SELECT
                CAST(gas_day AS VARCHAR) AS date,
                CASE
                    WHEN day_offset = 0 THEN 'today'
                    WHEN day_offset <= 6 THEN 'next_7_days'
                    ELSE 'days_8_to_30'
                END AS horizon,
                count(assessment_id) AS active_maintenance_row_count,
                count(DISTINCT operator_segment_id) FILTER (
                    WHERE assessment_id IS NOT NULL
                ) AS affected_segment_count,
                count(DISTINCT tgp_zone) FILTER (
                    WHERE assessment_id IS NOT NULL
                ) AS affected_zone_count,
                count(*) FILTER (
                    WHERE research_status = 'research_scenario'
                ) AS modeled_conflict_row_count,
                count(DISTINCT operator_segment_id) FILTER (
                    WHERE research_status = 'research_scenario'
                ) AS modeled_conflict_segment_count,
                max(gross_reduction_dth_per_day)
                    AS largest_single_reduction_dth_per_day,
                max(conditional_scheduled_shortfall_dth_per_day)
                    AS largest_conditional_shortfall_dth_per_day,
                max(station_label) FILTER (WHERE peak_rank = 1)
                    AS peak_station_label,
                max(operator_segment_id) FILTER (WHERE peak_rank = 1)
                    AS peak_segment_id,
                max(tgp_zone) FILTER (WHERE peak_rank = 1) AS peak_zone,
                max(outage_flow_direction) FILTER (WHERE peak_rank = 1)
                    AS peak_direction,
                max(segment_states) FILTER (WHERE peak_rank = 1)
                    AS peak_segment_states,
                string_agg(DISTINCT tgp_zone, ', ' ORDER BY tgp_zone) FILTER (
                    WHERE tgp_zone IS NOT NULL
                ) AS affected_zones,
                max(baseline_source_posted_at) AS capacity_source_posted_at,
                max(calculated_at) AS calculated_at
            FROM active
            GROUP BY gas_day, day_offset
            ORDER BY gas_day
            """,
            [report_artifact_id, decision_time, str(start_date), horizon_days],
        )
        contributor_rows = _rows(
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
            ), segment_geography AS (
                SELECT
                    operator_segment_id,
                    string_agg(
                        DISTINCT state_abbreviation, ', '
                        ORDER BY state_abbreviation
                    ) FILTER (WHERE state_abbreviation IS NOT NULL)
                        AS segment_states
                FROM tgp_location_map
                WHERE operator_segment_id IS NOT NULL
                GROUP BY operator_segment_id
            ), expanded AS (
                SELECT
                    CAST(day_value AS DATE) AS gas_day,
                    assessment.*,
                    geography.segment_states,
                    row_number() OVER (
                        PARTITION BY CAST(day_value AS DATE)
                        ORDER BY
                            conditional_scheduled_shortfall_dth_per_day DESC,
                            gross_reduction_dth_per_day DESC,
                            station_label,
                            operator_segment_id
                    ) AS day_rank
                FROM latest_assessment AS assessment
                LEFT JOIN segment_geography AS geography USING (operator_segment_id),
                unnest(generate_series(
                    greatest(period_start, CAST(? AS DATE)),
                    least(
                        period_end,
                        CAST(? AS DATE) + CAST(? - 1 AS INTEGER)
                    ),
                    INTERVAL 1 DAY
                )) AS days(day_value)
                WHERE period_end >= CAST(? AS DATE)
                  AND period_start < CAST(? AS DATE) + CAST(? AS INTEGER)
            )
            SELECT
                CAST(gas_day AS VARCHAR) AS date,
                assessment_id,
                station_label,
                operator_segment_id,
                tgp_zone,
                segment_states,
                outage_flow_direction,
                gross_reduction_dth_per_day,
                conditional_scheduled_shortfall_dth_per_day,
                forecast_operating_capacity_dth_per_day,
                baseline_scheduled_quantity_dth_per_day,
                research_status,
                report_artifact_id,
                capacity_artifact_id
            FROM expanded
            WHERE day_rank <= 3
            ORDER BY gas_day, day_rank
            """,
            [
                report_artifact_id,
                decision_time,
                str(start_date),
                str(start_date),
                horizon_days,
                str(start_date),
                str(start_date),
                horizon_days,
            ],
        )
        corridor_rows = _rows(
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
            ), segment_geography AS (
                SELECT
                    operator_segment_id,
                    string_agg(
                        DISTINCT state_abbreviation, ', '
                        ORDER BY state_abbreviation
                    ) FILTER (WHERE state_abbreviation IS NOT NULL)
                        AS segment_states
                FROM tgp_location_map
                WHERE operator_segment_id IS NOT NULL
                GROUP BY operator_segment_id
            ), expanded AS (
                SELECT
                    CAST(day_value AS DATE) AS gas_day,
                    assessment.*,
                    geography.segment_states
                FROM latest_assessment AS assessment
                LEFT JOIN segment_geography AS geography USING (operator_segment_id),
                unnest(generate_series(
                    greatest(period_start, CAST(? AS DATE)),
                    least(
                        period_end,
                        CAST(? AS DATE) + CAST(? - 1 AS INTEGER)
                    ),
                    INTERVAL 1 DAY
                )) AS days(day_value)
                WHERE period_end >= CAST(? AS DATE)
                  AND period_start < CAST(? AS DATE) + CAST(? AS INTEGER)
            ), ranked AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY operator_segment_id, tgp_zone,
                                     outage_flow_direction
                        ORDER BY
                            conditional_scheduled_shortfall_dth_per_day DESC,
                            gross_reduction_dth_per_day DESC,
                            gas_day,
                            station_label
                    ) AS peak_rank
                FROM expanded
                WHERE operator_segment_id IS NOT NULL
            )
            SELECT
                operator_segment_id,
                tgp_zone,
                outage_flow_direction,
                max(segment_states) AS segment_states,
                CAST(min(gas_day) AS VARCHAR) AS first_active_date,
                CAST(max(gas_day) AS VARCHAR) AS last_active_date,
                count(DISTINCT gas_day) AS active_day_count,
                count(DISTINCT gas_day) FILTER (
                    WHERE research_status = 'research_scenario'
                ) AS modeled_conflict_day_count,
                max(gross_reduction_dth_per_day)
                    AS largest_single_reduction_dth_per_day,
                max(conditional_scheduled_shortfall_dth_per_day)
                    AS largest_conditional_shortfall_dth_per_day,
                max(station_label) FILTER (WHERE peak_rank = 1)
                    AS peak_station_label,
                string_agg(DISTINCT report_artifact_id, ',')
                    AS report_evidence_ids,
                string_agg(DISTINCT capacity_artifact_id, ',') FILTER (
                    WHERE capacity_artifact_id IS NOT NULL
                ) AS capacity_evidence_ids
            FROM ranked
            GROUP BY operator_segment_id, tgp_zone, outage_flow_direction
            ORDER BY
                largest_conditional_shortfall_dth_per_day DESC,
                largest_single_reduction_dth_per_day DESC,
                first_active_date,
                operator_segment_id
            LIMIT 12
            """,
            [
                report_artifact_id,
                decision_time,
                str(start_date),
                str(start_date),
                horizon_days,
                str(start_date),
                str(start_date),
                horizon_days,
            ],
        )
        weather_rows = _rows(
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
                  AND period_end > CAST(? AS DATE)
                  AND period_start < CAST(? AS DATE) + CAST(? AS INTEGER)
            )
            SELECT
                CAST(
                    period_start AT TIME ZONE 'America/New_York' AS DATE
                )::VARCHAR AS date,
                geography,
                max(value) FILTER (WHERE metric = 'Forecast mean temperature')
                    AS mean_temperature_f,
                max(value) FILTER (WHERE metric = 'Forecast HDD') AS hdd_65,
                max(value) FILTER (WHERE metric = 'Forecast CDD') AS cdd_65,
                max(provider) AS provider,
                max(source_published_at) AS source_published_at,
                max(available_at) AS available_at,
                string_agg(DISTINCT artifact_id, ',') AS evidence_ids
            FROM eligible
            WHERE available_at = latest_available_at
            GROUP BY date, geography
            ORDER BY date, geography
            """,
            [decision_time, str(start_date), str(start_date), horizon_days],
        )
        storage_rows = _rows(
            connection,
            """
            SELECT
                geography,
                value,
                unit,
                CAST(period_start AS DATE)::VARCHAR AS period_end_date,
                provider,
                artifact_id AS evidence_id
            FROM market_observations
            WHERE observation_type = 'storage'
              AND metric = 'Storage vs 5-year average'
              AND geography IN ('Lower 48', 'East', 'South Central')
              AND available_at <= ?
            QUALIFY row_number() OVER (
                PARTITION BY geography
                ORDER BY available_at DESC, period_start DESC
            ) = 1
            ORDER BY geography
            """,
            [decision_time],
        )
        spot_rows = _rows(
            connection,
            """
            SELECT
                provider,
                value,
                unit,
                CAST(period_start AS DATE)::VARCHAR AS observation_date,
                artifact_id AS evidence_id
            FROM market_observations
            WHERE observation_type = 'physical_spot'
              AND available_at <= ?
            ORDER BY available_at DESC, period_start DESC
            LIMIT 1
            """,
            [decision_time],
        )
    finally:
        connection.close()

    contributors_by_date: dict[str, list[dict[str, object]]] = {}
    for contributor in contributor_rows:
        contributors_by_date.setdefault(str(contributor.pop("date")), []).append(
            contributor
        )
    for row in daily_rows:
        row["largest_single_reduction_dth_per_day"] = int(
            row["largest_single_reduction_dth_per_day"] or 0
        )
        row["largest_conditional_shortfall_dth_per_day"] = int(
            row["largest_conditional_shortfall_dth_per_day"] or 0
        )
        row["screen_state"] = _screen_state(
            row["largest_conditional_shortfall_dth_per_day"]
        )
        row["top_constraints"] = contributors_by_date.get(str(row["date"]), [])
        for field in ("capacity_source_posted_at", "calculated_at"):
            if row[field] is not None:
                row[field] = (
                    pendulum.instance(row[field])
                    .in_timezone("UTC")
                    .to_iso8601_string()
                )
    for corridor in corridor_rows:
        for field in ("report_evidence_ids", "capacity_evidence_ids"):
            corridor[field] = [
                value
                for value in str(corridor[field] or "").split(",")
                if value
            ]
    weather_by_date: dict[str, list[dict[str, object]]] = {}
    for weather_row in weather_rows:
        for field in ("source_published_at", "available_at"):
            if weather_row[field] is not None:
                weather_row[field] = (
                    pendulum.instance(weather_row[field])
                    .in_timezone("UTC")
                    .to_iso8601_string()
                )
        weather_row["evidence_ids"] = [
            value
            for value in str(weather_row["evidence_ids"] or "").split(",")
            if value
        ]
        weather_by_date.setdefault(str(weather_row.pop("date")), []).append(
            weather_row
        )
    weather_calendar = [
        {"date": date, "anchors": anchors}
        for date, anchors in sorted(weather_by_date.items())
    ]

    current = daily_rows[0] if daily_rows else None
    near_term = _peak_day(daily_rows[:7])
    forward = _peak_day(daily_rows[7:])
    peak = _peak_day(daily_rows)
    current_gap = int(
        current["largest_conditional_shortfall_dth_per_day"] if current else 0
    )
    near_gap = int(
        near_term["largest_conditional_shortfall_dth_per_day"]
        if near_term
        else 0
    )
    forward_gap = int(
        forward["largest_conditional_shortfall_dth_per_day"] if forward else 0
    )
    if forward_gap >= REVIEW_THRESHOLD_DTH_PER_DAY and forward_gap > near_gap * 3:
        headline = (
            "Near-term TGP pressure is limited; the larger modeled constraint "
            f"arrives {_display_date(forward['date'])}"
        )
    elif current_gap >= REVIEW_THRESHOLD_DTH_PER_DAY:
        headline = "A current TGP schedule gap merits active regional monitoring"
    elif near_gap >= REVIEW_THRESHOLD_DTH_PER_DAY:
        headline = (
            "TGP schedule pressure rises within seven days, led by "
            f"{near_term['peak_station_label']}"
        )
    elif forward_gap > 0:
        headline = "Current schedules fit; a modeled TGP gap appears later"
    elif any(int(row["active_maintenance_row_count"]) for row in daily_rows):
        headline = "TGP maintenance reduces flexibility, but captured schedules fit"
    else:
        headline = "No TGP maintenance constraint is active in the 30-day outlook"
    if peak and int(peak["largest_conditional_shortfall_dth_per_day"]) > 0:
        explanation = (
            f"The largest single unchanged-schedule gap is "
            f"{int(peak['largest_conditional_shortfall_dth_per_day']):,} Dth/day "
            f"on {_display_date(peak['date'])} at {peak['peak_station_label']}. "
            "The daily view "
            "does not sum overlapping stations or segments."
        )
    else:
        explanation = (
            "No captured schedule exceeds a matched forecast operating limit in "
            "the selected horizon. Planned reductions still reduce routing room."
        )
    first_review_day = next(
        (
            row
            for row in daily_rows
            if int(row["largest_conditional_shortfall_dth_per_day"])
            >= REVIEW_THRESHOLD_DTH_PER_DAY
        ),
        None,
    )
    weather_dates = {str(row["date"]) for row in weather_calendar}
    demand_overlap = bool(
        first_review_day and str(first_review_day["date"]) in weather_dates
    )
    storage_by_geography = {
        str(row["geography"]): row for row in storage_rows
    }
    lower_48_storage = storage_by_geography.get("Lower 48")
    east_storage = storage_by_geography.get("East")
    spot = spot_rows[0] if spot_rows else None
    transport_setup = bool(first_review_day)
    if transport_setup:
        trade_status = "unconfirmed_regional_basis_watch"
        trade_headline = (
            "Regional basis is the plausible market channel, but the setup is "
            "not trade-ready"
        )
    else:
        trade_status = "no_active_transport_setup"
        trade_headline = (
            "No unchanged-schedule transport gap currently supports a market watch"
        )
    transport_finding = (
        f"The first gap above {REVIEW_THRESHOLD_DTH_PER_DAY:,} Dth/day appears "
        f"{_display_date(first_review_day['date'])} and the largest single gap reaches "
        f"{int(peak['largest_conditional_shortfall_dth_per_day']):,} Dth/day "
        f"at {peak['peak_station_label']}."
        if first_review_day and peak
        else "No day crosses the deterministic transport review screen."
    )
    if demand_overlap:
        demand_finding = (
            "A named NWS demand anchor overlaps the first transport-review day; "
            "inspect its HDD/CDD marker before inferring demand pressure."
        )
    elif first_review_day and weather_calendar:
        demand_finding = (
            f"The NWS anchor forecast ends "
            f"{_display_date(weather_calendar[-1]['date'])}, before "
            f"the first transport-review day on "
            f"{_display_date(first_review_day['date'])}."
        )
    else:
        demand_finding = "No named-anchor weather forecast overlaps the transport setup."
    if lower_48_storage:
        storage_direction = (
            "above" if float(lower_48_storage["value"]) > 0 else "below"
        )
        inventory_finding = (
            f"Lower 48 storage is {abs(float(lower_48_storage['value'])):.1f}% "
            f"{storage_direction} its five-year average"
            + (
                f"; East storage is {abs(float(east_storage['value'])):.1f}% "
                f"{'above' if float(east_storage['value']) > 0 else 'below'}."
                if east_storage
                else "."
            )
        )
    else:
        inventory_finding = "No current storage comparison is available."
    price_finding = (
        f"Henry Hub physical spot is ${float(spot['value']):.2f}/MMBtu, but no "
        "regional TGP cash-basis observation confirms the constraint."
        if spot
        else "No regional TGP cash-basis observation confirms the constraint."
    )
    tradable_market_picture = {
        "status": trade_status,
        "headline": trade_headline,
        "first_review_date": (
            str(first_review_day["date"]) if first_review_day else None
        ),
        "weather_through_date": (
            str(weather_calendar[-1]["date"]) if weather_calendar else None
        ),
        "demand_overlap": demand_overlap,
        "market_channel": "regional_basis" if transport_setup else "none",
        "directional_price_sign": "unresolved",
        "exact_contract_mapping": "unresolved",
        "broad_benchmark_role": "context_only",
        "gates": [
            {
                "gate": "transport_setup",
                "status": "present" if transport_setup else "absent",
                "finding": transport_finding,
            },
            {
                "gate": "demand_overlap",
                "status": "present" if demand_overlap else "not_observed",
                "finding": demand_finding,
            },
            {
                "gate": "inventory_backdrop",
                "status": "available" if lower_48_storage else "not_observed",
                "finding": inventory_finding,
            },
            {
                "gate": "regional_price_confirmation",
                "status": "not_observed",
                "finding": price_finding,
            },
            {
                "gate": "flow_or_rerouting_confirmation",
                "status": "not_observed",
                "finding": (
                    "Measured flow, future nominations, and rerouting are not "
                    "available, so displacement remains conditional."
                ),
            },
        ],
        "market_expression": {
            "primary_watch": "TGP regional basis and adjacent interconnect markets",
            "henry_hub": "Broad benchmark context, not the direct expression",
            "current_conclusion": (
                "Monitor the affected regional basis; do not infer an outright "
                "Henry Hub position from maintenance alone."
            ),
        },
        "context_evidence_ids": sorted(
            {
                *(
                    str(row["evidence_id"])
                    for row in storage_rows
                    if row.get("evidence_id")
                ),
                *(
                    str(value)
                    for item in weather_calendar
                    for row in item["anchors"]
                    for value in row.get("evidence_ids", [])
                ),
                *(
                    [str(spot["evidence_id"])]
                    if spot and spot.get("evidence_id")
                    else []
                ),
            }
        ),
    }
    return {
        "pipeline_id": "TGP",
        "decision_at_utc": decision_time.to_iso8601_string(),
        "report_notice_id": str(selected_report[1]),
        "report_updated_on": str(selected_report[2]),
        "report_posted_at_utc": pendulum.instance(selected_report[3])
        .in_timezone("UTC")
        .to_iso8601_string(),
        "horizon_days": horizon_days,
        "aggregation_method": "largest_single_constraint_no_sum",
        "aggregation_warning": (
            "Station and segment values are not additive because the same gas may "
            "traverse multiple constrained locations."
        ),
        "review_threshold_dth_per_day": REVIEW_THRESHOLD_DTH_PER_DAY,
        "summary": {
            "headline": headline,
            "explanation": explanation,
            "current_day": current,
            "near_term_peak": near_term,
            "forward_peak": forward,
            "peak_day": peak,
        },
        "weather_by_date": weather_calendar,
        "tradable_market_picture": tradable_market_picture,
        "days": daily_rows,
        "corridors": corridor_rows,
    }
