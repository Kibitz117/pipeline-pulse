CREATE TABLE IF NOT EXISTS fetch_runs (
    run_id VARCHAR PRIMARY KEY,
    source_code VARCHAR NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    config JSON NOT NULL,
    error JSON
);

CREATE TABLE IF NOT EXISTS source_artifacts (
    artifact_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL REFERENCES fetch_runs(run_id),
    source_code VARCHAR NOT NULL,
    canonical_url VARCHAR NOT NULL,
    content_sha256 VARCHAR NOT NULL,
    mime_type VARCHAR,
    http_status INTEGER,
    source_published_at TIMESTAMPTZ,
    requested_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL,
    raw_path VARCHAR NOT NULL,
    metadata JSON NOT NULL,
    UNIQUE (source_code, canonical_url, received_at, content_sha256)
);

CREATE TABLE IF NOT EXISTS operators (
    operator_id VARCHAR PRIMARY KEY,
    operator_name VARCHAR NOT NULL,
    parent_company VARCHAR,
    ticker VARCHAR,
    source_url VARCHAR
);

CREATE TABLE IF NOT EXISTS pipeline_systems (
    pipeline_id VARCHAR PRIMARY KEY,
    operator_id VARCHAR NOT NULL REFERENCES operators(operator_id),
    pipeline_name VARCHAR NOT NULL,
    source_code VARCHAR NOT NULL UNIQUE,
    tsp_number VARCHAR,
    ferc_cid VARCHAR,
    timezone VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS facilities (
    facility_id VARCHAR PRIMARY KEY,
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    operator_location_id VARCHAR,
    facility_name VARCHAR NOT NULL,
    facility_type VARCHAR,
    zone VARCHAR,
    state VARCHAR,
    county VARCHAR,
    latitude DOUBLE,
    longitude DOUBLE,
    coordinate_method VARCHAR,
    coordinate_precision VARCHAR,
    coordinate_artifact_id VARCHAR REFERENCES source_artifacts(artifact_id),
    receipt_delivery_role VARCHAR,
    receipt_zone VARCHAR,
    delivery_zone VARCHAR,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    artifact_id VARCHAR REFERENCES source_artifacts(artifact_id),
    UNIQUE (pipeline_id, operator_location_id, valid_from)
);

CREATE TABLE IF NOT EXISTS segments (
    segment_id VARCHAR PRIMARY KEY,
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    operator_segment_id VARCHAR,
    segment_name VARCHAR NOT NULL,
    zone VARCHAR,
    flow_direction VARCHAR,
    nominal_capacity_dth_per_day DOUBLE,
    artifact_id VARCHAR REFERENCES source_artifacts(artifact_id),
    UNIQUE (pipeline_id, operator_segment_id)
);

CREATE TABLE IF NOT EXISTS location_exports (
    artifact_id VARCHAR PRIMARY KEY REFERENCES source_artifacts(artifact_id),
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    tsp_number VARCHAR NOT NULL,
    tsp_name VARCHAR NOT NULL,
    tsp_ferc_cid VARCHAR NOT NULL,
    source_as_of TIMESTAMPTZ NOT NULL,
    comments VARCHAR,
    source_column_count INTEGER NOT NULL,
    schema_sha256 VARCHAR NOT NULL,
    parser_version VARCHAR NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count > 0),
    observed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS location_observations (
    artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    row_position INTEGER NOT NULL,
    operator_location_id VARCHAR NOT NULL,
    location_name VARCHAR NOT NULL,
    flow_role VARCHAR NOT NULL,
    county_name VARCHAR NOT NULL,
    normalized_county_name VARCHAR NOT NULL,
    state_abbreviation VARCHAR NOT NULL,
    location_type VARCHAR NOT NULL,
    receipt_zone VARCHAR,
    delivery_zone VARCHAR,
    operator_segment_id VARCHAR,
    nomination_indicator VARCHAR,
    status_indicator VARCHAR,
    effective_date DATE,
    inactive_date DATE,
    interconnect_indicator BOOLEAN,
    counterparty_name VARCHAR,
    counterparty_id VARCHAR,
    counterparty_property_id VARCHAR,
    counterparty_ferc_indicator BOOLEAN,
    counterparty_ferc_cid VARCHAR,
    counterparty_location_id VARCHAR,
    counterparty_location_name VARCHAR,
    counterparty_location_id_2 VARCHAR,
    counterparty_location_name_2 VARCHAR,
    source_updated_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (artifact_id, operator_location_id),
    UNIQUE (artifact_id, row_position)
);

CREATE TABLE IF NOT EXISTS county_reference_observations (
    artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    geoid VARCHAR NOT NULL,
    state_abbreviation VARCHAR NOT NULL,
    county_name VARCHAR NOT NULL,
    normalized_county_name VARCHAR NOT NULL,
    latitude DOUBLE NOT NULL,
    longitude DOUBLE NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (artifact_id, geoid)
);

CREATE TABLE IF NOT EXISTS location_coordinate_observations (
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    operator_location_id VARCHAR NOT NULL,
    location_artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    coordinate_artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    latitude DOUBLE NOT NULL,
    longitude DOUBLE NOT NULL,
    coordinate_method VARCHAR NOT NULL,
    coordinate_precision VARCHAR NOT NULL,
    matched_geography_id VARCHAR,
    matched_geography_name VARCHAR,
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (
        pipeline_id, operator_location_id, location_artifact_id,
        coordinate_artifact_id, coordinate_method
    ),
    CHECK (latitude BETWEEN -90 AND 90),
    CHECK (longitude BETWEEN -180 AND 180)
);

CREATE TABLE IF NOT EXISTS map_reference_layers (
    artifact_id VARCHAR PRIMARY KEY REFERENCES source_artifacts(artifact_id),
    layer_code VARCHAR NOT NULL,
    source_vintage VARCHAR,
    feature_count INTEGER NOT NULL CHECK (feature_count > 0),
    geojson JSON NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS notice_versions (
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    notice_id VARCHAR NOT NULL,
    version_sha256 VARCHAR NOT NULL,
    artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    critical BOOLEAN,
    notice_type_primary VARCHAR,
    notice_type_secondary VARCHAR,
    status_description VARCHAR,
    prior_notice_id VARCHAR,
    subject VARCHAR NOT NULL,
    notice_text VARCHAR,
    posted_at TIMESTAMPTZ NOT NULL,
    effective_start TIMESTAMPTZ,
    effective_end TIMESTAMPTZ,
    required_response VARCHAR,
    response_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (pipeline_id, notice_id, version_sha256)
);

ALTER TABLE notice_versions
ADD COLUMN IF NOT EXISTS required_response VARCHAR;

ALTER TABLE notice_versions
ADD COLUMN IF NOT EXISTS response_at TIMESTAMPTZ;

-- A version is the normalized, investor-relevant notice content. An observation
-- records when that version was actually available to this system. Keeping the
-- two separate is required for point-in-time replay: repeated unchanged checks
-- must be visible without becoming revisions, and a notice can revert to a
-- previously seen version.
CREATE TABLE IF NOT EXISTS notice_version_observations (
    artifact_id VARCHAR PRIMARY KEY REFERENCES source_artifacts(artifact_id),
    pipeline_id VARCHAR NOT NULL,
    notice_id VARCHAR NOT NULL,
    version_sha256 VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (pipeline_id, notice_id, version_sha256)
        REFERENCES notice_versions(pipeline_id, notice_id, version_sha256)
);

-- Backfill databases created before the observation ledger existed. These rows
-- retain the original collection time; they do not pretend that a historical
-- notice was observed at its operator publication time.
INSERT INTO notice_version_observations(
    artifact_id, pipeline_id, notice_id, version_sha256, observed_at
)
SELECT
    artifact_id, pipeline_id, notice_id, version_sha256, first_seen_at
FROM notice_versions
ON CONFLICT (artifact_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS notice_index_pages (
    artifact_id VARCHAR PRIMARY KEY REFERENCES source_artifacts(artifact_id),
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    page_index INTEGER NOT NULL,
    page_size INTEGER NOT NULL,
    page_count INTEGER NOT NULL,
    total_row_count INTEGER NOT NULL,
    parsed_row_count INTEGER NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    CHECK (page_index >= 0),
    CHECK (page_size > 0),
    CHECK (page_count > 0),
    CHECK (total_row_count >= 0),
    CHECK (parsed_row_count >= 0)
);

CREATE TABLE IF NOT EXISTS notice_index_exports (
    artifact_id VARCHAR PRIMARY KEY REFERENCES source_artifacts(artifact_id),
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    export_format VARCHAR NOT NULL,
    total_row_count INTEGER NOT NULL,
    source_footer_row_count INTEGER,
    index_advertised_row_count INTEGER NOT NULL,
    parsed_row_count INTEGER NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    CHECK (total_row_count >= 0),
    CHECK (source_footer_row_count IS NULL OR source_footer_row_count >= 0),
    CHECK (index_advertised_row_count >= 0),
    CHECK (parsed_row_count >= 0)
);

CREATE TABLE IF NOT EXISTS notice_index_observations (
    artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    page_index INTEGER NOT NULL,
    row_position INTEGER NOT NULL,
    notice_id VARCHAR NOT NULL,
    notice_type_primary VARCHAR,
    notice_type_secondary VARCHAR,
    subject VARCHAR NOT NULL,
    posted_at TIMESTAMPTZ NOT NULL,
    effective_start TIMESTAMPTZ,
    effective_end TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (artifact_id, notice_id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    agent_run_id VARCHAR PRIMARY KEY,
    role VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    input_artifact_ids JSON NOT NULL,
    session_path VARCHAR NOT NULL,
    validation JSON
);

CREATE TABLE IF NOT EXISTS events (
    event_id VARCHAR PRIMARY KEY,
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    event_type VARCHAR NOT NULL,
    current_status VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    effective_start TIMESTAMPTZ,
    effective_end TIMESTAMPTZ,
    impact_channel VARCHAR NOT NULL,
    summary VARCHAR,
    extraction_confidence DOUBLE CHECK (extraction_confidence BETWEEN 0 AND 1),
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_changed_at TIMESTAMPTZ NOT NULL,
    agent_run_id VARCHAR REFERENCES agent_runs(agent_run_id)
);

CREATE TABLE IF NOT EXISTS event_notice_links (
    event_id VARCHAR NOT NULL REFERENCES events(event_id),
    pipeline_id VARCHAR NOT NULL,
    notice_id VARCHAR NOT NULL,
    version_sha256 VARCHAR NOT NULL,
    link_role VARCHAR NOT NULL,
    confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence JSON NOT NULL,
    PRIMARY KEY (event_id, pipeline_id, notice_id, version_sha256),
    FOREIGN KEY (pipeline_id, notice_id, version_sha256)
        REFERENCES notice_versions(pipeline_id, notice_id, version_sha256)
);

CREATE TABLE IF NOT EXISTS event_impacts (
    event_impact_id VARCHAR PRIMARY KEY,
    event_id VARCHAR NOT NULL REFERENCES events(event_id),
    facility_id VARCHAR REFERENCES facilities(facility_id),
    segment_id VARCHAR REFERENCES segments(segment_id),
    gas_day DATE,
    flow_direction VARCHAR,
    nominal_capacity_dth_per_day DOUBLE,
    operating_capacity_dth_per_day DOUBLE,
    reduction_dth_per_day DOUBLE,
    reduction_pct DOUBLE,
    impact_band VARCHAR,
    extraction_method VARCHAR NOT NULL,
    confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    evidence JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS capacity_exports (
    artifact_id VARCHAR PRIMARY KEY REFERENCES source_artifacts(artifact_id),
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    capacity_kind VARCHAR NOT NULL CHECK (capacity_kind IN ('point', 'segment')),
    point_role VARCHAR CHECK (point_role IN ('receipt', 'delivery')),
    tsp_number VARCHAR NOT NULL,
    tsp_name VARCHAR NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    gas_day DATE NOT NULL,
    cycle VARCHAR NOT NULL,
    location_purpose VARCHAR NOT NULL,
    measurement_basis VARCHAR NOT NULL,
    source_posted_at TIMESTAMPTZ NOT NULL,
    quantity_description VARCHAR NOT NULL,
    source_footer_row_count INTEGER NOT NULL,
    parsed_row_count INTEGER NOT NULL,
    schema_sha256 VARCHAR NOT NULL,
    parser_version VARCHAR NOT NULL,
    comments VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS capacity_observations (
    capacity_observation_id VARCHAR PRIMARY KEY,
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    capacity_kind VARCHAR NOT NULL CHECK (capacity_kind IN ('point', 'segment')),
    point_role VARCHAR CHECK (point_role IN ('receipt', 'delivery')),
    source_row_position INTEGER NOT NULL,
    operator_location_id VARCHAR,
    operator_segment_id VARCHAR NOT NULL,
    location_name VARCHAR NOT NULL,
    zone VARCHAR NOT NULL,
    facility_id VARCHAR REFERENCES facilities(facility_id),
    segment_id VARCHAR REFERENCES segments(segment_id),
    gas_day DATE NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    cycle VARCHAR NOT NULL,
    flow_direction VARCHAR,
    design_capacity_dth_per_day BIGINT NOT NULL,
    operating_capacity_dth_per_day BIGINT NOT NULL,
    scheduled_quantity_dth_per_day BIGINT NOT NULL,
    available_capacity_dth_per_day BIGINT NOT NULL,
    interruptible_scheduled BOOLEAN NOT NULL,
    all_quantity_available BOOLEAN NOT NULL,
    quantity_reason VARCHAR,
    available_reconciles BOOLEAN NOT NULL,
    source_posted_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    UNIQUE (artifact_id, source_row_position),
    CHECK (design_capacity_dth_per_day >= 0),
    CHECK (operating_capacity_dth_per_day >= 0),
    CHECK (scheduled_quantity_dth_per_day >= 0),
    CHECK (available_capacity_dth_per_day >= 0)
);

CREATE TABLE IF NOT EXISTS outage_impact_observations (
    artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    notice_id VARCHAR NOT NULL,
    report_kind VARCHAR NOT NULL CHECK (report_kind IN ('seven_day', 'monthly')),
    report_label VARCHAR NOT NULL,
    report_updated_on DATE NOT NULL,
    period_label VARCHAR NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    station_label VARCHAR NOT NULL,
    operator_segment_id VARCHAR,
    flow_direction VARCHAR,
    nominal_capacity_text VARCHAR NOT NULL,
    capacity_text VARCHAR NOT NULL,
    nominal_capacity_dth_per_day BIGINT,
    operating_capacity_dth_per_day BIGINT,
    reported_reduction_dth_per_day BIGINT,
    calculated_reduction_dth_per_day BIGINT,
    reduction_reconciles BOOLEAN,
    outage_description VARCHAR NOT NULL,
    source_table_index INTEGER NOT NULL,
    source_row_index INTEGER NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (
        artifact_id, source_table_index, source_row_index,
        period_start, period_end
    ),
    CHECK (period_end >= period_start),
    CHECK (nominal_capacity_dth_per_day IS NULL OR nominal_capacity_dth_per_day >= 0),
    CHECK (operating_capacity_dth_per_day IS NULL OR operating_capacity_dth_per_day >= 0)
);

CREATE TABLE IF NOT EXISTS market_observations (
    market_observation_id VARCHAR PRIMARY KEY,
    series_code VARCHAR NOT NULL,
    provider VARCHAR,
    observation_type VARCHAR,
    metric VARCHAR NOT NULL,
    geography VARCHAR NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ,
    value DOUBLE NOT NULL,
    unit VARCHAR NOT NULL,
    source_published_at TIMESTAMPTZ,
    available_at TIMESTAMPTZ NOT NULL,
    vintage VARCHAR,
    artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    UNIQUE (series_code, geography, period_start, available_at)
);

CREATE TABLE IF NOT EXISTS tgp_transport_impact_assessments (
    assessment_id VARCHAR PRIMARY KEY,
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    report_artifact_id VARCHAR NOT NULL REFERENCES source_artifacts(artifact_id),
    report_notice_id VARCHAR NOT NULL,
    report_updated_on DATE NOT NULL,
    source_table_index INTEGER NOT NULL,
    source_row_index INTEGER NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    station_label VARCHAR NOT NULL,
    operator_segment_id VARCHAR,
    outage_flow_direction VARCHAR,
    capacity_flow_direction VARCHAR,
    direction_mapping_method VARCHAR NOT NULL,
    tgp_zone VARCHAR,
    capacity_observation_id VARCHAR REFERENCES capacity_observations(capacity_observation_id),
    capacity_artifact_id VARCHAR REFERENCES source_artifacts(artifact_id),
    capacity_location_name VARCHAR,
    baseline_gas_day DATE,
    baseline_cycle VARCHAR,
    baseline_source_posted_at TIMESTAMPTZ,
    baseline_operating_capacity_dth_per_day BIGINT,
    baseline_scheduled_quantity_dth_per_day BIGINT,
    baseline_available_capacity_dth_per_day BIGINT,
    forecast_nominal_capacity_dth_per_day BIGINT,
    forecast_operating_capacity_dth_per_day BIGINT,
    gross_reduction_dth_per_day BIGINT,
    conditional_scheduled_shortfall_dth_per_day BIGINT,
    forecast_headroom_vs_baseline_schedule_dth_per_day BIGINT,
    baseline_timing VARCHAR NOT NULL CHECK (
        baseline_timing IN ('pre_event', 'same_day', 'post_event', 'unmatched')
    ),
    match_method VARCHAR NOT NULL CHECK (
        match_method IN (
            'normalized_name', 'station_number', 'unique_segment_direction',
            'ambiguous', 'unmatched'
        )
    ),
    research_status VARCHAR NOT NULL CHECK (
        research_status IN ('no_trade_mapping', 'monitor', 'research_scenario')
    ),
    price_mapping_status VARCHAR NOT NULL CHECK (
        price_mapping_status IN ('unresolved', 'mapped')
    ),
    price_mapping_reason VARCHAR NOT NULL,
    benchmark_reference_url VARCHAR,
    unresolved_reasons JSON NOT NULL,
    evidence JSON NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL,
    CHECK (period_end >= period_start),
    CHECK (
        conditional_scheduled_shortfall_dth_per_day IS NULL
        OR conditional_scheduled_shortfall_dth_per_day >= 0
    ),
    CHECK (
        forecast_headroom_vs_baseline_schedule_dth_per_day IS NULL
        OR forecast_headroom_vs_baseline_schedule_dth_per_day >= 0
    )
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id VARCHAR PRIMARY KEY,
    event_id VARCHAR NOT NULL REFERENCES events(event_id),
    decision_at TIMESTAMPTZ NOT NULL,
    change_type VARCHAR NOT NULL,
    severity_score DOUBLE NOT NULL CHECK (severity_score BETWEEN 0 AND 100),
    score_components JSON NOT NULL,
    headline VARCHAR NOT NULL,
    explanation VARCHAR NOT NULL,
    confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence JSON NOT NULL,
    agent_run_id VARCHAR REFERENCES agent_runs(agent_run_id)
);

CREATE TABLE IF NOT EXISTS research_memos (
    research_memo_id VARCHAR PRIMARY KEY,
    pipeline_id VARCHAR NOT NULL REFERENCES pipeline_systems(pipeline_id),
    decision_at TIMESTAMPTZ NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    data_fingerprint VARCHAR NOT NULL,
    headline VARCHAR NOT NULL,
    plain_english_summary VARCHAR NOT NULL,
    why_it_matters VARCHAR NOT NULL,
    overall_confidence VARCHAR NOT NULL
        CHECK (overall_confidence IN ('low', 'medium', 'high')),
    memo JSON NOT NULL,
    agent_run_id VARCHAR NOT NULL UNIQUE REFERENCES agent_runs(agent_run_id)
);

CREATE OR REPLACE VIEW current_notice_index AS
WITH latest_export_artifact AS (
    SELECT pipeline_id, artifact_id
    FROM notice_index_exports
    QUALIFY row_number() OVER (
        PARTITION BY pipeline_id ORDER BY observed_at DESC
    ) = 1
),
latest_page_artifact AS (
    SELECT pipeline_id, artifact_id
    FROM notice_index_pages
    QUALIFY row_number() OVER (
        PARTITION BY pipeline_id ORDER BY observed_at DESC
    ) = 1
),
candidate_rows AS (
    SELECT observation.*, 1 AS capture_priority
    FROM notice_index_observations AS observation
    JOIN latest_page_artifact AS latest
      ON latest.artifact_id = observation.artifact_id
    UNION ALL
    SELECT observation.*, 2 AS capture_priority
    FROM notice_index_observations AS observation
    JOIN latest_export_artifact AS latest
      ON latest.artifact_id = observation.artifact_id
),
current_rows AS (
    SELECT *
    FROM candidate_rows
    QUALIFY row_number() OVER (
        PARTITION BY pipeline_id, notice_id
        ORDER BY capture_priority, observed_at DESC
    ) = 1
),
subject_candidates AS (
    SELECT pipeline_id, notice_id, subject, observed_at
    FROM notice_index_observations
    UNION ALL
    SELECT pipeline_id, notice_id, subject, last_seen_at AS observed_at
    FROM notice_versions
),
best_subject AS (
    SELECT
        pipeline_id,
        notice_id,
        arg_max(subject, observed_at) FILTER (
            WHERE nullif(trim(subject), '') IS NOT NULL
        ) AS subject
    FROM subject_candidates
    GROUP BY pipeline_id, notice_id
)
SELECT
    current_rows.artifact_id,
    current_rows.pipeline_id,
    current_rows.row_position,
    current_rows.notice_id,
    current_rows.notice_type_primary,
    current_rows.notice_type_secondary,
    coalesce(nullif(trim(current_rows.subject), ''), best_subject.subject) AS subject,
    current_rows.posted_at,
    current_rows.effective_start,
    current_rows.effective_end,
    current_rows.observed_at
FROM current_rows
LEFT JOIN best_subject
  ON best_subject.pipeline_id = current_rows.pipeline_id
 AND best_subject.notice_id = current_rows.notice_id;

CREATE OR REPLACE VIEW current_notice_versions AS
WITH latest_observation AS (
    SELECT *
    FROM notice_version_observations
    QUALIFY row_number() OVER (
        PARTITION BY pipeline_id, notice_id
        ORDER BY observed_at DESC, artifact_id DESC
    ) = 1
)
SELECT
    version.* EXCLUDE (artifact_id),
    observation.artifact_id,
    observation.observed_at AS version_observed_at
FROM latest_observation AS observation
JOIN notice_versions AS version
  ON version.pipeline_id = observation.pipeline_id
 AND version.notice_id = observation.notice_id
 AND version.version_sha256 = observation.version_sha256;

CREATE OR REPLACE VIEW tgp_notice_version_timeline AS
WITH ordered AS (
    SELECT
        observation.*,
        lag(observation.version_sha256) OVER (
            PARTITION BY observation.pipeline_id, observation.notice_id
            ORDER BY observation.observed_at, observation.artifact_id
        ) AS prior_version_sha256
    FROM notice_version_observations AS observation
    WHERE observation.pipeline_id = 'TGP'
)
SELECT
    ordered.pipeline_id,
    ordered.notice_id,
    ordered.version_sha256,
    ordered.prior_version_sha256,
    ordered.prior_version_sha256 IS NULL AS is_first_observation,
    ordered.prior_version_sha256 IS NOT NULL
        AND ordered.version_sha256 != ordered.prior_version_sha256
        AS is_revision_observation,
    ordered.observed_at AS available_at,
    version.critical,
    version.notice_type_primary,
    version.notice_type_secondary,
    version.status_description,
    version.prior_notice_id,
    version.subject,
    version.notice_text,
    version.posted_at,
    version.effective_start,
    version.effective_end,
    version.required_response,
    version.response_at,
    artifact.content_sha256 AS raw_content_sha256,
    artifact.canonical_url,
    artifact.raw_path,
    ordered.artifact_id
FROM ordered
JOIN notice_versions AS version
  ON version.pipeline_id = ordered.pipeline_id
 AND version.notice_id = ordered.notice_id
 AND version.version_sha256 = ordered.version_sha256
JOIN source_artifacts AS artifact
  ON artifact.artifact_id = ordered.artifact_id;

CREATE OR REPLACE VIEW tgp_maintenance_notices AS
SELECT
    detail.*,
    artifact.canonical_url,
    artifact.raw_path,
    artifact.received_at,
    artifact.processed_at
FROM current_notice_versions AS detail
JOIN source_artifacts AS artifact
  ON artifact.artifact_id = detail.artifact_id
WHERE detail.pipeline_id = 'TGP'
  AND detail.notice_type_primary = 'MAINTENANCE';

CREATE OR REPLACE VIEW current_pipeline_locations AS
WITH latest_export AS (
    SELECT pipeline_id, artifact_id
    FROM location_exports
    QUALIFY row_number() OVER (
        PARTITION BY pipeline_id
        -- The KM export clock can move backward between captures. "Current"
        -- means the newest archived snapshot; source_as_of remains preserved
        -- for audit and regression checks.
        ORDER BY observed_at DESC, source_as_of DESC, artifact_id DESC
    ) = 1
)
SELECT location.*
FROM location_observations AS location
JOIN latest_export
  ON latest_export.pipeline_id = location.pipeline_id
 AND latest_export.artifact_id = location.artifact_id;

CREATE OR REPLACE VIEW current_location_coordinates AS
SELECT coordinate.*
FROM location_coordinate_observations AS coordinate
JOIN current_pipeline_locations AS location
  ON location.pipeline_id = coordinate.pipeline_id
 AND location.operator_location_id = coordinate.operator_location_id
 AND location.artifact_id = coordinate.location_artifact_id
QUALIFY row_number() OVER (
    PARTITION BY coordinate.pipeline_id, coordinate.operator_location_id
    ORDER BY
        CASE coordinate.coordinate_precision
            WHEN 'exact' THEN 1
            WHEN 'facility' THEN 2
            WHEN 'locality' THEN 3
            WHEN 'county' THEN 4
            ELSE 5
        END,
        coordinate.observed_at DESC
) = 1;

CREATE OR REPLACE VIEW tgp_location_map AS
SELECT
    location.*,
    coordinate.latitude,
    coordinate.longitude,
    coordinate.coordinate_method,
    coordinate.coordinate_precision,
    coordinate.matched_geography_id,
    coordinate.matched_geography_name,
    coordinate.coordinate_artifact_id
FROM current_pipeline_locations AS location
LEFT JOIN current_location_coordinates AS coordinate
  ON coordinate.pipeline_id = location.pipeline_id
 AND coordinate.operator_location_id = location.operator_location_id
WHERE location.pipeline_id = 'TGP';

CREATE OR REPLACE VIEW tgp_outage_report_summary AS
SELECT
    impact.artifact_id,
    impact.notice_id,
    notice.version_sha256,
    notice.posted_at,
    impact.report_updated_on,
    min(impact.period_start) AS first_forecast_date,
    max(impact.period_end) AS last_forecast_date,
    count(*) AS station_period_rows,
    count(DISTINCT impact.station_label) AS station_count,
    count(*) FILTER (
        WHERE impact.operating_capacity_dth_per_day IS NOT NULL
    ) AS populated_capacity_rows,
    max(impact.calculated_reduction_dth_per_day) AS max_reduction_dth_per_day,
    count(*) FILTER (
        WHERE impact.reduction_reconciles = false
    ) AS reduction_mismatch_count,
    max(impact.observed_at) AS observed_at
FROM outage_impact_observations AS impact
JOIN notice_version_observations AS version_observation
  ON version_observation.artifact_id = impact.artifact_id
JOIN notice_versions AS notice
  ON notice.pipeline_id = version_observation.pipeline_id
 AND notice.notice_id = version_observation.notice_id
 AND notice.version_sha256 = version_observation.version_sha256
GROUP BY
    impact.artifact_id,
    impact.notice_id,
    notice.version_sha256,
    notice.posted_at,
    impact.report_updated_on;

CREATE OR REPLACE VIEW latest_tgp_outage_capacity AS
WITH latest_report AS (
    SELECT artifact_id
    FROM tgp_outage_report_summary
    ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
    LIMIT 1
)
SELECT
    impact.*,
    notice.posted_at AS report_posted_at,
    notice.subject AS report_subject
FROM outage_impact_observations AS impact
JOIN latest_report
  ON latest_report.artifact_id = impact.artifact_id
JOIN notice_version_observations AS version_observation
  ON version_observation.artifact_id = impact.artifact_id
JOIN notice_versions AS notice
  ON notice.pipeline_id = version_observation.pipeline_id
 AND notice.notice_id = version_observation.notice_id
 AND notice.version_sha256 = version_observation.version_sha256;

CREATE OR REPLACE VIEW tgp_outage_capacity_revisions AS
WITH ordered AS (
    SELECT
        impact.*,
        notice.posted_at AS report_posted_at,
        lag(impact.operating_capacity_dth_per_day) OVER history AS prior_operating_capacity_dth_per_day,
        lag(impact.artifact_id) OVER history AS prior_artifact_id,
        lag(impact.notice_id) OVER history AS prior_report_notice_id
    FROM outage_impact_observations AS impact
    JOIN notice_version_observations AS version_observation
      ON version_observation.artifact_id = impact.artifact_id
    JOIN notice_versions AS notice
      ON notice.pipeline_id = version_observation.pipeline_id
     AND notice.notice_id = version_observation.notice_id
     AND notice.version_sha256 = version_observation.version_sha256
    WINDOW history AS (
        PARTITION BY
            impact.pipeline_id,
            impact.report_kind,
            impact.station_label,
            impact.period_start,
            impact.period_end
        ORDER BY
            impact.report_updated_on,
            notice.posted_at,
            impact.observed_at,
            impact.artifact_id
    )
)
SELECT
    ordered.*,
    CASE
        WHEN operating_capacity_dth_per_day IS NOT NULL
         AND prior_operating_capacity_dth_per_day IS NOT NULL
        THEN operating_capacity_dth_per_day - prior_operating_capacity_dth_per_day
    END AS operating_capacity_change_dth_per_day
FROM ordered;

CREATE OR REPLACE VIEW latest_tgp_daily_market_state AS
WITH latest_report AS (
    SELECT artifact_id, notice_id, report_updated_on
    FROM tgp_outage_report_summary
    ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
    LIMIT 1
), latest_assessment AS (
    SELECT assessment.*
    FROM tgp_transport_impact_assessments AS assessment
    JOIN latest_report
      ON latest_report.artifact_id = assessment.report_artifact_id
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
            DISTINCT state_abbreviation, ', ' ORDER BY state_abbreviation
        ) FILTER (WHERE state_abbreviation IS NOT NULL) AS segment_states
    FROM tgp_location_map
    WHERE operator_segment_id IS NOT NULL
    GROUP BY operator_segment_id
), calendar AS (
    SELECT
        current_date + CAST(day_offset AS INTEGER) AS gas_day,
        CAST(day_offset AS INTEGER) AS day_offset,
        latest_report.notice_id AS report_notice_id,
        latest_report.report_updated_on
    FROM latest_report, range(30) AS offsets(day_offset)
), active AS (
    SELECT
        calendar.*,
        assessment.* EXCLUDE (report_notice_id, report_updated_on),
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
    gas_day,
    CASE
        WHEN day_offset = 0 THEN 'today'
        WHEN day_offset <= 6 THEN 'next_7_days'
        ELSE 'days_8_to_30'
    END AS horizon,
    report_notice_id,
    report_updated_on,
    CASE
        WHEN count(assessment_id) = 0 THEN 'no_planned_maintenance'
        WHEN count(*) FILTER (
            WHERE research_status = 'research_scenario'
        ) = 0 THEN 'maintenance_with_headroom'
        WHEN count(DISTINCT operator_segment_id) FILTER (
            WHERE research_status = 'research_scenario'
        ) = 1 THEN 'localized_schedule_conflict'
        ELSE 'multi_segment_schedule_conflict'
    END AS transport_state,
    CASE
        WHEN coalesce(max(conditional_scheduled_shortfall_dth_per_day), 0) = 0
            THEN 'no_modeled_gap'
        WHEN max(conditional_scheduled_shortfall_dth_per_day) < 50000
            THEN 'below_50000_dth_per_day_review_threshold'
        ELSE 'active_review'
    END AS screen_state,
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
    coalesce(max(gross_reduction_dth_per_day), 0)
        AS largest_single_reduction_dth_per_day,
    coalesce(max(conditional_scheduled_shortfall_dth_per_day), 0)
        AS largest_conditional_shortfall_dth_per_day,
    max(station_label) FILTER (WHERE peak_rank = 1) AS peak_station_label,
    max(operator_segment_id) FILTER (WHERE peak_rank = 1) AS peak_segment_id,
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
GROUP BY gas_day, day_offset, report_notice_id, report_updated_on
ORDER BY gas_day;

CREATE INDEX IF NOT EXISTS idx_artifacts_source_received
    ON source_artifacts(source_code, received_at);
CREATE INDEX IF NOT EXISTS idx_notices_posted
    ON notice_versions(pipeline_id, posted_at);
CREATE INDEX IF NOT EXISTS idx_notice_index_posted
    ON notice_index_observations(pipeline_id, posted_at);
CREATE INDEX IF NOT EXISTS idx_notice_index_pages_observed
    ON notice_index_pages(pipeline_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_notice_index_exports_observed
    ON notice_index_exports(pipeline_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_events_changed
    ON events(pipeline_id, last_changed_at);
CREATE INDEX IF NOT EXISTS idx_outage_impact_period
    ON outage_impact_observations(pipeline_id, period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_outage_impact_notice
    ON outage_impact_observations(pipeline_id, notice_id);
CREATE INDEX IF NOT EXISTS idx_location_segment
    ON location_observations(pipeline_id, operator_segment_id);
CREATE INDEX IF NOT EXISTS idx_location_geography
    ON location_observations(pipeline_id, state_abbreviation, county_name);
CREATE INDEX IF NOT EXISTS idx_location_coordinate
    ON location_coordinate_observations(pipeline_id, operator_location_id);
CREATE INDEX IF NOT EXISTS idx_market_available
    ON market_observations(series_code, geography, available_at);
CREATE INDEX IF NOT EXISTS idx_tgp_transport_impact_period
    ON tgp_transport_impact_assessments(period_start, research_status);
CREATE INDEX IF NOT EXISTS idx_tgp_transport_impact_segment
    ON tgp_transport_impact_assessments(operator_segment_id, period_start);
CREATE INDEX IF NOT EXISTS idx_alerts_decision
    ON alerts(decision_at, severity_score);
