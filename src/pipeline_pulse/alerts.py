from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pendulum

from .database import connect_database, initialize_database


NOTICE_URL = (
    "https://pipeline2.kindermorgan.com/Notices/NoticeDetail.aspx"
    "?code=TGP&notc_nbr={notice_id}"
)
CAPACITY_URL = (
    "https://pipeline2.kindermorgan.com/Capacity/"
    "OpAvailSegment.aspx?code=TGP"
)


@dataclass(frozen=True)
class AlertBuildSummary:
    pipeline_id: str
    notice_alert_count: int
    notice_revision_alert_count: int
    outage_revision_alert_count: int
    capacity_alert_count: int
    total_alert_count: int
    latest_decision_at_utc: str | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True)
class _AlertSpec:
    event_id: str
    alert_id: str
    event_type: str
    current_status: str
    title: str
    effective_start: pendulum.DateTime | None
    effective_end: pendulum.DateTime | None
    impact_channel: str
    summary: str
    extraction_confidence: float
    decision_at: pendulum.DateTime
    change_type: str
    severity_score: float
    score_components: dict[str, object]
    explanation: str
    evidence: dict[str, object]
    notice_link: dict[str, object] | None = None
    prior_notice_link: dict[str, object] | None = None


def _rows(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[object] | None = None,
) -> list[dict[str, object]]:
    result = connection.execute(query, parameters or [])
    columns = tuple(description[0] for description in result.description)
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def _json_value(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _utc(value: object) -> pendulum.DateTime:
    if isinstance(value, pendulum.DateTime):
        return value.in_timezone("UTC")
    if isinstance(value, datetime):
        return pendulum.instance(value).in_timezone("UTC")
    parsed = pendulum.parse(str(value), strict=False)
    if not isinstance(parsed, pendulum.DateTime):
        raise ValueError(f"expected timestamp, got {value!r}")
    return parsed.in_timezone("UTC")


def _gas_day(value: object | None, *, end: bool = False) -> pendulum.DateTime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        output = pendulum.instance(value).in_timezone("America/Chicago")
    elif isinstance(value, date):
        output = pendulum.datetime(
            value.year,
            value.month,
            value.day,
            tz="America/Chicago",
        )
    else:
        parsed = pendulum.parse(str(value), strict=False, tz="America/Chicago")
        if isinstance(parsed, pendulum.DateTime):
            output = parsed.in_timezone("America/Chicago")
        else:
            output = pendulum.datetime(
                parsed.year,
                parsed.month,
                parsed.day,
                tz="America/Chicago",
            )
    return (output.end_of("day") if end else output.start_of("day")).in_timezone(
        "UTC"
    )


def _identifier(event_type: str, values: list[object]) -> tuple[str, str]:
    material = json.dumps(
        [event_type, *values],
        sort_keys=True,
        separators=(",", ":"),
        default=_json_value,
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"TGP:{event_type}:{digest}", f"TGP:alert:{digest}"


def _capacity_label(value: float | int | None) -> str:
    if value is None:
        return "unknown"
    magnitude = abs(float(value))
    if magnitude >= 1_000_000:
        return f"{magnitude / 1_000_000:.2f}m"
    return f"{magnitude / 1_000:.0f}k"


def _magnitude_points(value: float) -> int:
    magnitude = abs(value)
    if magnitude >= 500_000:
        return 40
    if magnitude >= 250_000:
        return 32
    if magnitude >= 100_000:
        return 24
    if magnitude >= 50_000:
        return 16
    if magnitude >= 25_000:
        return 10
    return 5


def _utilization(scheduled: object, operating: object) -> float | None:
    operating_value = float(operating or 0)
    if operating_value <= 0:
        return None
    return round(float(scheduled or 0) * 100.0 / operating_value, 1)


def _crossed(value: float | None, prior: float | None, threshold: float) -> bool:
    if value is None or prior is None:
        return False
    return (prior < threshold <= value) or (value < threshold <= prior)


def _notice_alerts(connection: duckdb.DuckDBPyConnection) -> list[_AlertSpec]:
    rows = _rows(
        connection,
        """
        WITH pages AS (
            SELECT
                artifact_id,
                observed_at,
                dense_rank() OVER (ORDER BY observed_at DESC, artifact_id DESC)
                    AS capture_rank
            FROM notice_index_pages
            WHERE pipeline_id = 'TGP'
        ), page_count AS (
            SELECT count(*) AS value
            FROM pages
        ), current_rows AS (
            SELECT observation.*, page.observed_at AS capture_observed_at
            FROM notice_index_observations AS observation
            JOIN pages AS page USING (artifact_id)
            WHERE page.capture_rank = 1
        ), prior_rows AS (
            SELECT observation.notice_id
            FROM notice_index_observations AS observation
            JOIN pages AS page USING (artifact_id)
            WHERE page.capture_rank = 2
        ), first_detail_observation AS (
            SELECT *
            FROM notice_version_observations
            WHERE pipeline_id = 'TGP'
            QUALIFY row_number() OVER (
                PARTITION BY notice_id
                ORDER BY observed_at, artifact_id
            ) = 1
        ), first_detail AS (
            SELECT
                version.* EXCLUDE (artifact_id),
                observation.artifact_id,
                observation.observed_at AS detail_observed_at
            FROM first_detail_observation AS observation
            JOIN notice_versions AS version
              ON version.pipeline_id = observation.pipeline_id
             AND version.notice_id = observation.notice_id
             AND version.version_sha256 = observation.version_sha256
        )
        SELECT
            current.artifact_id AS index_artifact_id,
            current.notice_id,
            current.notice_type_primary,
            current.notice_type_secondary,
            current.subject AS index_subject,
            current.posted_at AS index_posted_at,
            current.effective_start AS index_effective_start,
            current.effective_end AS index_effective_end,
            current.capture_observed_at,
            detail.version_sha256,
            detail.artifact_id AS detail_artifact_id,
            detail.detail_observed_at,
            detail.status_description,
            detail.prior_notice_id,
            detail.subject AS detail_subject,
            detail.notice_text AS detail_notice_text,
            detail.effective_start AS detail_effective_start,
            detail.effective_end AS detail_effective_end
        FROM current_rows AS current
        LEFT JOIN prior_rows AS prior USING (notice_id)
        LEFT JOIN first_detail AS detail USING (notice_id)
        WHERE prior.notice_id IS NULL
          AND (SELECT value FROM page_count) >= 2
        ORDER BY current.posted_at DESC, current.notice_id DESC
        """,
    )
    alerts: list[_AlertSpec] = []
    for row in rows:
        subject = str(row["detail_subject"] or row["index_subject"] or "New notice")
        status = str(row["status_description"] or "NEW").upper()
        notice_id = str(row["notice_id"])
        decision_at = max(
            _utc(row["capture_observed_at"]),
            _utc(row["detail_observed_at"])
            if row["detail_observed_at"] is not None
            else _utc(row["capture_observed_at"]),
        )
        effective_start = _utc(
            row["detail_effective_start"] or row["index_effective_start"]
        ) if (row["detail_effective_start"] or row["index_effective_start"]) else None
        effective_end = _utc(
            row["detail_effective_end"] or row["index_effective_end"]
        ) if (row["detail_effective_end"] or row["index_effective_end"]) else None
        lowered = " ".join(
            str(value or "").lower()
            for value in (
                row["notice_type_primary"],
                row["notice_type_secondary"],
                subject,
            )
        )
        subject_points = 30 if any(
            term in lowered
            for term in (
                "force majeure",
                "curtail",
                "emergency",
                "emergent",
                "ofo",
            )
        ) else 20 if any(
            term in lowered for term in ("restriction", "constraint")
        ) else 15 if "maintenance" in lowered else 8
        imminence_points = 0
        if effective_start is not None:
            hours = (effective_start - decision_at).total_seconds() / 3600
            imminence_points = 20 if hours <= 24 else 12 if hours <= 168 else 5
        score_components = {
            "critical_notice": 35,
            "subject_materiality": subject_points,
            "effective_immediacy": imminence_points,
        }
        severity = min(100.0, float(sum(score_components.values())))
        event_id, alert_id = _identifier(
            "critical_notice",
            [row["index_artifact_id"], notice_id],
        )
        artifact_ids = [str(row["index_artifact_id"])]
        if row["detail_artifact_id"]:
            artifact_ids.append(str(row["detail_artifact_id"]))
        evidence = {
            "schema_version": "tgp_alert_evidence_v1",
            "change_source": "critical_notice_index",
            "subject": {
                "notice_id": notice_id,
                "notice_type_primary": row["notice_type_primary"],
                "notice_type_secondary": row["notice_type_secondary"],
                "prior_notice_id": row["prior_notice_id"],
            },
            "before": None,
            "after": {
                "status": status,
                "subject": subject,
                "notice_text": row["detail_notice_text"],
                "posted_at": row["index_posted_at"],
                "effective_start": effective_start,
                "effective_end": effective_end,
            },
            "artifact_ids": artifact_ids,
            "source_url": NOTICE_URL.format(notice_id=notice_id),
            "comparison_warning": None,
        }
        notice_link = None
        if row["version_sha256"]:
            notice_link = {
                "notice_id": notice_id,
                "version_sha256": str(row["version_sha256"]),
                "link_role": "new_notice",
                "confidence": 1.0,
                "evidence": {"artifact_ids": artifact_ids},
            }
        alerts.append(
            _AlertSpec(
                event_id=event_id,
                alert_id=alert_id,
                event_type="critical_notice",
                current_status=status.lower(),
                title=f"{status} · Notice {notice_id} · {subject}",
                effective_start=effective_start,
                effective_end=effective_end,
                impact_channel="operator_notice",
                summary="A critical notice appeared in the newest TGP index capture.",
                extraction_confidence=1.0 if notice_link else 0.85,
                decision_at=decision_at,
                change_type="new_critical_notice",
                severity_score=severity,
                score_components=score_components,
                explanation=(
                    "This is a newly observed operator notice. "
                    + (
                        f"The operator links it to prior notice "
                        f"{row['prior_notice_id']}. "
                        if row["prior_notice_id"]
                        else ""
                    )
                    + (
                        "Its archived detail text is attached; subsequent capacity "
                        "data are still required to confirm the operating effect."
                        if row["detail_notice_text"]
                        else "Materiality depends on the detail text and subsequent "
                        "capacity confirmation."
                    )
                ),
                evidence=evidence,
                notice_link=notice_link,
            )
        )
    return alerts


def _notice_content_revision_alerts(
    connection: duckdb.DuckDBPyConnection,
) -> list[_AlertSpec]:
    rows = _rows(
        connection,
        """
        WITH ordered AS (
            SELECT
                observation.*,
                lag(observation.version_sha256) OVER history
                    AS prior_version_sha256,
                lag(observation.artifact_id) OVER history
                    AS prior_artifact_id
            FROM notice_version_observations AS observation
            WHERE observation.pipeline_id = 'TGP'
            WINDOW history AS (
                PARTITION BY observation.pipeline_id, observation.notice_id
                ORDER BY observation.observed_at, observation.artifact_id
            )
        )
        SELECT
            ordered.notice_id,
            ordered.artifact_id AS current_artifact_id,
            ordered.prior_artifact_id,
            ordered.version_sha256,
            ordered.prior_version_sha256,
            ordered.observed_at,
            current_version.notice_type_primary AS current_notice_type_primary,
            prior_version.notice_type_primary AS prior_notice_type_primary,
            current_version.notice_type_secondary AS current_notice_type_secondary,
            prior_version.notice_type_secondary AS prior_notice_type_secondary,
            current_version.status_description AS current_status_description,
            prior_version.status_description AS prior_status_description,
            current_version.prior_notice_id AS current_prior_notice_id,
            prior_version.prior_notice_id AS prior_prior_notice_id,
            current_version.subject AS current_subject,
            prior_version.subject AS prior_subject,
            current_version.notice_text AS current_notice_text,
            prior_version.notice_text AS prior_notice_text,
            current_version.posted_at AS current_posted_at,
            prior_version.posted_at AS prior_posted_at,
            current_version.effective_start AS current_effective_start,
            prior_version.effective_start AS prior_effective_start,
            current_version.effective_end AS current_effective_end,
            prior_version.effective_end AS prior_effective_end,
            current_version.required_response AS current_required_response,
            prior_version.required_response AS prior_required_response,
            current_version.response_at AS current_response_at,
            prior_version.response_at AS prior_response_at
        FROM ordered
        JOIN notice_versions AS current_version
          ON current_version.pipeline_id = ordered.pipeline_id
         AND current_version.notice_id = ordered.notice_id
         AND current_version.version_sha256 = ordered.version_sha256
        JOIN notice_versions AS prior_version
          ON prior_version.pipeline_id = ordered.pipeline_id
         AND prior_version.notice_id = ordered.notice_id
         AND prior_version.version_sha256 = ordered.prior_version_sha256
        WHERE ordered.prior_version_sha256 IS NOT NULL
          AND ordered.version_sha256 != ordered.prior_version_sha256
        ORDER BY ordered.observed_at, ordered.notice_id
        """,
    )
    field_pairs = (
        ("notice type", "notice_type_primary"),
        ("notice subtype", "notice_type_secondary"),
        ("status", "status_description"),
        ("prior notice link", "prior_notice_id"),
        ("subject", "subject"),
        ("operator text", "notice_text"),
        ("posted time", "posted_at"),
        ("effective start", "effective_start"),
        ("effective end", "effective_end"),
        ("required response", "required_response"),
        ("response deadline", "response_at"),
    )
    alerts: list[_AlertSpec] = []
    for row in rows:
        changed_fields = [
            label
            for label, field in field_pairs
            if row[f"prior_{field}"] != row[f"current_{field}"]
        ]
        timing_changed = any(
            field in changed_fields
            for field in (
                "posted time", "effective start", "effective end",
                "response deadline",
            )
        )
        lifecycle_changed = any(
            field in changed_fields for field in ("status", "prior notice link")
        )
        score_components = {
            "notice_revision": 20,
            "timing_changed": 25 if timing_changed else 0,
            "lifecycle_changed": 30 if lifecycle_changed else 0,
            "subject_or_type_changed": 15
            if any(
                field in changed_fields
                for field in ("notice type", "notice subtype", "subject")
            )
            else 0,
            "operator_text_changed": 10
            if "operator text" in changed_fields
            else 0,
            "response_requirement_changed": 20
            if "required response" in changed_fields
            else 0,
        }
        severity = min(100.0, float(sum(score_components.values())))
        status_value = str(row["current_status_description"] or "REVISED").upper()
        current_status = (
            "terminated"
            if status_value == "TERMINATE"
            else "superseded"
            if status_value == "SUPERSEDE"
            else "revised"
        )
        decision_at = _utc(row["observed_at"])
        event_id, alert_id = _identifier(
            "notice_content_revision",
            [
                row["notice_id"],
                row["prior_version_sha256"],
                row["version_sha256"],
                row["current_artifact_id"],
            ],
        )
        evidence = {
            "schema_version": "tgp_alert_evidence_v1",
            "change_source": "same_notice_content_revision",
            "subject": {"notice_id": row["notice_id"]},
            "changed_fields": changed_fields,
            "before": {
                "version_sha256": row["prior_version_sha256"],
                "status": row["prior_status_description"],
                "prior_notice_id": row["prior_prior_notice_id"],
                "subject": row["prior_subject"],
                "notice_text_excerpt": str(row["prior_notice_text"] or "")[:700],
                "posted_at": row["prior_posted_at"],
                "effective_start": row["prior_effective_start"],
                "effective_end": row["prior_effective_end"],
                "required_response": row["prior_required_response"],
                "response_at": row["prior_response_at"],
            },
            "after": {
                "version_sha256": row["version_sha256"],
                "status": row["current_status_description"],
                "prior_notice_id": row["current_prior_notice_id"],
                "subject": row["current_subject"],
                "notice_text_excerpt": str(row["current_notice_text"] or "")[:700],
                "posted_at": row["current_posted_at"],
                "effective_start": row["current_effective_start"],
                "effective_end": row["current_effective_end"],
                "required_response": row["current_required_response"],
                "response_at": row["current_response_at"],
            },
            "artifact_ids": [
                str(row["prior_artifact_id"]),
                str(row["current_artifact_id"]),
            ],
            "source_url": NOTICE_URL.format(notice_id=row["notice_id"]),
            "comparison_warning": (
                "Revision availability is the collection time, which may be "
                "later than the operator's edit time. Backtests before this "
                "timestamp must use the preceding version."
            ),
        }
        changed_label = ", ".join(changed_fields[:3])
        if len(changed_fields) > 3:
            changed_label += f" +{len(changed_fields) - 3} more"
        link_evidence = {"artifact_ids": evidence["artifact_ids"]}
        alerts.append(
            _AlertSpec(
                event_id=event_id,
                alert_id=alert_id,
                event_type="notice_content_revision",
                current_status=current_status,
                title=f"Notice {row['notice_id']} revised · {changed_label}",
                effective_start=(
                    _utc(row["current_effective_start"])
                    if row["current_effective_start"] is not None
                    else None
                ),
                effective_end=(
                    _utc(row["current_effective_end"])
                    if row["current_effective_end"] is not None
                    else None
                ),
                impact_channel="operator_notice",
                summary=(
                    "Kinder Morgan republished the same notice ID with changed "
                    "investor-relevant content."
                ),
                extraction_confidence=1.0,
                decision_at=decision_at,
                change_type="notice_content_revision",
                severity_score=severity,
                score_components=score_components,
                explanation=(
                    f"Changed fields: {', '.join(changed_fields)}. The new "
                    f"version first became available to this system at "
                    f"{decision_at.to_iso8601_string()}; earlier point-in-time "
                    "analysis must retain the preceding version."
                ),
                evidence=evidence,
                notice_link={
                    "notice_id": str(row["notice_id"]),
                    "version_sha256": str(row["version_sha256"]),
                    "link_role": "revised_notice_version",
                    "confidence": 1.0,
                    "evidence": link_evidence,
                },
                prior_notice_link={
                    "notice_id": str(row["notice_id"]),
                    "version_sha256": str(row["prior_version_sha256"]),
                    "link_role": "preceding_notice_version",
                    "confidence": 1.0,
                    "evidence": link_evidence,
                },
            )
        )
    return alerts


def _outage_revision_alerts(
    connection: duckdb.DuckDBPyConnection,
) -> list[_AlertSpec]:
    rows = _rows(
        connection,
        """
        WITH latest_report AS (
            SELECT artifact_id
            FROM tgp_outage_report_summary
            ORDER BY report_updated_on DESC, posted_at DESC, observed_at DESC
            LIMIT 1
        )
        SELECT
            revision.artifact_id AS current_artifact_id,
            arg_max(
                revision.prior_artifact_id,
                abs(revision.operating_capacity_change_dth_per_day)
            ) AS prior_artifact_id,
            revision.notice_id,
            arg_max(
                revision.prior_report_notice_id,
                abs(revision.operating_capacity_change_dth_per_day)
            ) AS prior_notice_id,
            revision.operator_segment_id,
            revision.station_label,
            revision.flow_direction,
            count(*) AS changed_period_count,
            min(revision.period_start) AS first_changed_period,
            max(revision.period_end) AS last_changed_period,
            arg_max(
                revision.period_start,
                abs(revision.operating_capacity_change_dth_per_day)
            ) AS representative_period_start,
            arg_max(
                revision.period_end,
                abs(revision.operating_capacity_change_dth_per_day)
            ) AS representative_period_end,
            arg_max(
                revision.prior_operating_capacity_dth_per_day,
                abs(revision.operating_capacity_change_dth_per_day)
            ) AS prior_operating_capacity_dth_per_day,
            arg_max(
                revision.operating_capacity_dth_per_day,
                abs(revision.operating_capacity_change_dth_per_day)
            ) AS operating_capacity_dth_per_day,
            arg_max(
                revision.operating_capacity_change_dth_per_day,
                abs(revision.operating_capacity_change_dth_per_day)
            ) AS operating_capacity_change_dth_per_day,
            arg_max(
                revision.outage_description,
                abs(revision.operating_capacity_change_dth_per_day)
            ) AS outage_description,
            max(revision.report_updated_on) AS report_updated_on,
            max(revision.report_posted_at) AS report_posted_at,
            max(revision.observed_at) AS observed_at,
            max(detail.version_sha256) AS version_sha256
        FROM tgp_outage_capacity_revisions AS revision
        JOIN latest_report USING (artifact_id)
        LEFT JOIN notice_versions AS detail
          ON detail.artifact_id = revision.artifact_id
        WHERE revision.operating_capacity_change_dth_per_day != 0
          AND revision.operator_segment_id IS NOT NULL
        GROUP BY
            revision.artifact_id,
            revision.notice_id,
            revision.operator_segment_id,
            revision.station_label,
            revision.flow_direction
        ORDER BY max(abs(revision.operating_capacity_change_dth_per_day)) DESC
        """,
    )
    alerts: list[_AlertSpec] = []
    for row in rows:
        delta = float(row["operating_capacity_change_dth_per_day"] or 0)
        if abs(delta) < 25_000:
            continue
        prior_capacity = float(row["prior_operating_capacity_dth_per_day"] or 0)
        current_capacity = float(row["operating_capacity_dth_per_day"] or 0)
        delta_pct = round(delta * 100.0 / prior_capacity, 1) if prior_capacity else None
        decision_at = _utc(row["observed_at"])
        effective_start = _gas_day(row["first_changed_period"])
        effective_end = _gas_day(row["last_changed_period"], end=True)
        days_until = (
            (effective_start - decision_at).total_seconds() / 86_400
            if effective_start
            else None
        )
        imminence = (
            20 if days_until is not None and days_until <= 7
            else 12 if days_until is not None and days_until <= 30
            else 6 if days_until is not None and days_until <= 60
            else 0
        )
        percentage_points = min(25, int(abs(delta_pct or 0)))
        score_components = {
            "absolute_change": _magnitude_points(delta),
            "percentage_change": percentage_points,
            "effective_immediacy": imminence,
            "explicit_report_revision": 10,
        }
        severity = min(100.0, float(sum(score_components.values())))
        worsened = delta < 0
        status = "worsened" if worsened else "improved"
        verb = "cut" if worsened else "raised"
        segment = str(row["operator_segment_id"])
        current_artifact_id = str(row["current_artifact_id"])
        prior_artifact_id = str(row["prior_artifact_id"])
        event_id, alert_id = _identifier(
            "outage_capacity_revision",
            [
                current_artifact_id,
                prior_artifact_id,
                segment,
                row["station_label"],
                row["flow_direction"],
            ],
        )
        evidence = {
            "schema_version": "tgp_alert_evidence_v1",
            "change_source": "outage_report_revision",
            "subject": {
                "operator_segment_id": segment,
                "station_label": row["station_label"],
                "flow_direction": row["flow_direction"],
            },
            "before": {
                "notice_id": row["prior_notice_id"],
                "operating_capacity_dth_per_day": prior_capacity,
            },
            "after": {
                "notice_id": row["notice_id"],
                "report_updated_on": row["report_updated_on"],
                "operating_capacity_dth_per_day": current_capacity,
                "changed_period_count": row["changed_period_count"],
                "first_changed_period": row["first_changed_period"],
                "last_changed_period": row["last_changed_period"],
            },
            "delta": {
                "operating_capacity_dth_per_day": delta,
                "operating_capacity_pct": delta_pct,
            },
            "operator_explanation": row["outage_description"],
            "artifact_ids": [prior_artifact_id, current_artifact_id],
            "source_url": NOTICE_URL.format(notice_id=row["notice_id"]),
            "comparison_warning": None,
        }
        alerts.append(
            _AlertSpec(
                event_id=event_id,
                alert_id=alert_id,
                event_type="outage_capacity_revision",
                current_status=status,
                title=(
                    f"Segment {segment} forecast capacity {verb} "
                    f"{_capacity_label(delta)} Dth/day"
                ),
                effective_start=effective_start,
                effective_end=effective_end,
                impact_channel="forward_transport_capacity",
                summary=(
                    f"The latest outage report {status} forecast operating "
                    f"capacity for {row['changed_period_count']} comparable period(s)."
                ),
                extraction_confidence=0.95,
                decision_at=decision_at,
                change_type=(
                    "forecast_capacity_decrease"
                    if worsened
                    else "forecast_capacity_increase"
                ),
                severity_score=severity,
                score_components=score_components,
                explanation=(
                    f"{row['station_label']} changed from "
                    f"{_capacity_label(prior_capacity)} to "
                    f"{_capacity_label(current_capacity)} Dth/day in the most "
                    "material comparable forecast row. This is a report revision, "
                    "not proof that physical flow changed."
                ),
                evidence=evidence,
                notice_link=(
                    {
                        "notice_id": str(row["notice_id"]),
                        "version_sha256": str(row["version_sha256"]),
                        "link_role": "revised_outage_report",
                        "confidence": 1.0,
                        "evidence": {
                            "artifact_ids": [prior_artifact_id, current_artifact_id]
                        },
                    }
                    if row["version_sha256"]
                    else None
                ),
            )
        )
    return alerts


def _capacity_alerts(connection: duckdb.DuckDBPyConnection) -> list[_AlertSpec]:
    rows = _rows(
        connection,
        """
        WITH bundles AS (
            SELECT
                artifact.run_id,
                min(export.observed_at) AS observed_at,
                dense_rank() OVER (
                    ORDER BY min(export.observed_at) DESC, artifact.run_id DESC
                ) AS capture_rank
            FROM capacity_exports AS export
            JOIN source_artifacts AS artifact USING (artifact_id)
            WHERE export.pipeline_id = 'TGP'
            GROUP BY artifact.run_id
        ), observations AS (
            SELECT
                bundle.capture_rank,
                artifact.run_id,
                observation.*
            FROM capacity_observations AS observation
            JOIN source_artifacts AS artifact USING (artifact_id)
            JOIN bundles AS bundle USING (run_id)
            WHERE bundle.capture_rank <= 2
              AND observation.capacity_kind = 'segment'
        )
        SELECT
            current.run_id AS current_run_id,
            prior.run_id AS prior_run_id,
            current.artifact_id AS current_artifact_id,
            prior.artifact_id AS prior_artifact_id,
            current.operator_segment_id,
            current.location_name,
            current.flow_direction,
            current.gas_day,
            prior.gas_day AS prior_gas_day,
            current.cycle,
            prior.cycle AS prior_cycle,
            current.effective_at,
            prior.effective_at AS prior_effective_at,
            current.source_posted_at,
            prior.source_posted_at AS prior_source_posted_at,
            current.observed_at,
            prior.observed_at AS prior_observed_at,
            current.operating_capacity_dth_per_day,
            prior.operating_capacity_dth_per_day
                AS prior_operating_capacity_dth_per_day,
            current.scheduled_quantity_dth_per_day,
            prior.scheduled_quantity_dth_per_day
                AS prior_scheduled_quantity_dth_per_day,
            current.available_capacity_dth_per_day,
            prior.available_capacity_dth_per_day
                AS prior_available_capacity_dth_per_day
        FROM observations AS current
        JOIN observations AS prior
          ON prior.capture_rank = 2
         AND current.capture_rank = 1
         AND prior.operator_segment_id = current.operator_segment_id
         AND prior.location_name = current.location_name
         AND coalesce(prior.flow_direction, '') = coalesce(current.flow_direction, '')
        ORDER BY current.operator_segment_id, current.flow_direction
        """,
    )
    alerts: list[_AlertSpec] = []
    for row in rows:
        current_operating = float(row["operating_capacity_dth_per_day"] or 0)
        prior_operating = float(row["prior_operating_capacity_dth_per_day"] or 0)
        current_scheduled = float(row["scheduled_quantity_dth_per_day"] or 0)
        prior_scheduled = float(row["prior_scheduled_quantity_dth_per_day"] or 0)
        current_available = float(row["available_capacity_dth_per_day"] or 0)
        prior_available = float(row["prior_available_capacity_dth_per_day"] or 0)
        operating_delta = current_operating - prior_operating
        scheduled_delta = current_scheduled - prior_scheduled
        available_delta = current_available - prior_available
        current_tightness = _utilization(current_scheduled, current_operating)
        prior_tightness = _utilization(prior_scheduled, prior_operating)
        crossed_80 = _crossed(current_tightness, prior_tightness, 80)
        crossed_95 = _crossed(current_tightness, prior_tightness, 95)
        crossed_zero = (current_available == 0) != (prior_available == 0)
        material = (
            abs(operating_delta) >= 50_000
            or crossed_80
            or crossed_95
            or crossed_zero
            or (
                abs(available_delta) >= 100_000
                and max(current_tightness or 0, prior_tightness or 0) >= 80
            )
        )
        if not material:
            continue
        magnitude = operating_delta if operating_delta else available_delta
        threshold_points = 25 if crossed_zero else 20 if crossed_95 else 10 if crossed_80 else 0
        current_pressure_points = (
            15 if (current_tightness or 0) >= 95
            else 8 if (current_tightness or 0) >= 80
            else 0
        )
        score_components = {
            "absolute_change": _magnitude_points(magnitude),
            "threshold_crossing": threshold_points,
            "current_scheduling_pressure": current_pressure_points,
            "operating_capacity_changed": 10 if operating_delta else 0,
        }
        severity = min(100.0, float(sum(score_components.values())))
        comparable = (
            row["gas_day"] == row["prior_gas_day"]
            and row["cycle"] == row["prior_cycle"]
        )
        comparison_warning = None if comparable else (
            "Snapshots use different gas days or nomination cycles; the change "
            "is descriptive and may reflect normal scheduling evolution."
        )
        if operating_delta:
            improved = operating_delta > 0
            status = "improved" if improved else "worsened"
            change_type = (
                "operating_capacity_increase"
                if improved
                else "operating_capacity_decrease"
            )
            title = (
                f"Segment {row['operator_segment_id']} "
                f"operating capacity {'rose' if improved else 'fell'} "
                f"{_capacity_label(operating_delta)} Dth/day"
            )
            impact_channel = "operating_capacity"
        else:
            tightened = (
                (current_tightness or 0) > (prior_tightness or 0)
                or current_available < prior_available
            )
            status = "tightened" if tightened else "relieved"
            change_type = "scheduling_tightened" if tightened else "scheduling_relieved"
            title = (
                f"Segment {row['operator_segment_id']} scheduling "
                f"{status} in {row['flow_direction'] or 'reported direction'}"
            )
            impact_channel = "scheduling_pressure"
        event_id, alert_id = _identifier(
            "capacity_snapshot_change",
            [
                row["current_run_id"],
                row["prior_run_id"],
                row["operator_segment_id"],
                row["location_name"],
                row["flow_direction"],
            ],
        )
        current_artifact_id = str(row["current_artifact_id"])
        prior_artifact_id = str(row["prior_artifact_id"])
        evidence = {
            "schema_version": "tgp_alert_evidence_v1",
            "change_source": "operational_capacity_snapshot",
            "subject": {
                "operator_segment_id": str(row["operator_segment_id"]),
                "location_name": row["location_name"],
                "flow_direction": row["flow_direction"],
            },
            "before": {
                "gas_day": row["prior_gas_day"],
                "cycle": row["prior_cycle"],
                "source_posted_at": row["prior_source_posted_at"],
                "operating_capacity_dth_per_day": prior_operating,
                "scheduled_quantity_dth_per_day": prior_scheduled,
                "available_capacity_dth_per_day": prior_available,
                "scheduled_pct_of_operating": prior_tightness,
            },
            "after": {
                "gas_day": row["gas_day"],
                "cycle": row["cycle"],
                "source_posted_at": row["source_posted_at"],
                "operating_capacity_dth_per_day": current_operating,
                "scheduled_quantity_dth_per_day": current_scheduled,
                "available_capacity_dth_per_day": current_available,
                "scheduled_pct_of_operating": current_tightness,
            },
            "delta": {
                "operating_capacity_dth_per_day": operating_delta,
                "scheduled_quantity_dth_per_day": scheduled_delta,
                "available_capacity_dth_per_day": available_delta,
                "scheduled_pct_of_operating": (
                    round((current_tightness or 0) - (prior_tightness or 0), 1)
                    if current_tightness is not None and prior_tightness is not None
                    else None
                ),
            },
            "thresholds": {
                "crossed_80_pct": crossed_80,
                "crossed_95_pct": crossed_95,
                "crossed_zero_available": crossed_zero,
            },
            "artifact_ids": [prior_artifact_id, current_artifact_id],
            "source_url": CAPACITY_URL,
            "comparison_warning": comparison_warning,
        }
        alerts.append(
            _AlertSpec(
                event_id=event_id,
                alert_id=alert_id,
                event_type="capacity_snapshot_change",
                current_status=status,
                title=title,
                effective_start=_utc(row["effective_at"]),
                effective_end=_gas_day(row["gas_day"], end=True),
                impact_channel=impact_channel,
                summary=(
                    f"{row['location_name']} moved from "
                    f"{prior_tightness if prior_tightness is not None else 'unknown'}% "
                    f"to {current_tightness if current_tightness is not None else 'unknown'}% "
                    "scheduled relative to operating capacity."
                ),
                extraction_confidence=0.9 if comparable else 0.7,
                decision_at=_utc(row["observed_at"]),
                change_type=change_type,
                severity_score=severity,
                score_components=score_components,
                explanation=(
                    f"Available capacity changed from {_capacity_label(prior_available)} "
                    f"to {_capacity_label(current_available)} Dth/day. "
                    + (comparison_warning or "The snapshots share a gas day and cycle.")
                ),
                evidence=evidence,
            )
        )
    return alerts


def _upsert_alert(
    connection: duckdb.DuckDBPyConnection,
    alert: _AlertSpec,
) -> None:
    evidence_json = json.dumps(
        alert.evidence,
        sort_keys=True,
        default=_json_value,
    )
    connection.execute(
        """
        INSERT INTO events(
            event_id, pipeline_id, event_type, current_status, title,
            effective_start, effective_end, impact_channel, summary,
            extraction_confidence, first_seen_at, last_changed_at
        ) VALUES (?, 'TGP', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (event_id) DO NOTHING
        """,
        [
            alert.event_id,
            alert.event_type,
            alert.current_status,
            alert.title,
            alert.effective_start,
            alert.effective_end,
            alert.impact_channel,
            alert.summary,
            alert.extraction_confidence,
            alert.decision_at,
            alert.decision_at,
        ],
    )
    connection.execute(
        """
        INSERT INTO alerts(
            alert_id, event_id, decision_at, change_type, severity_score,
            score_components, headline, explanation, confidence, evidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (alert_id) DO UPDATE SET
            severity_score = excluded.severity_score,
            score_components = excluded.score_components,
            headline = excluded.headline,
            explanation = excluded.explanation,
            confidence = excluded.confidence,
            evidence = excluded.evidence
        """,
        [
            alert.alert_id,
            alert.event_id,
            alert.decision_at,
            alert.change_type,
            alert.severity_score,
            json.dumps(alert.score_components, sort_keys=True),
            alert.title,
            alert.explanation,
            alert.extraction_confidence,
            evidence_json,
        ],
    )
    for link in (alert.notice_link, alert.prior_notice_link):
        if link is None:
            continue
        connection.execute(
            """
            INSERT INTO event_notice_links(
                event_id, pipeline_id, notice_id, version_sha256, link_role,
                confidence, evidence
            ) VALUES (?, 'TGP', ?, ?, ?, ?, ?)
            ON CONFLICT (
                event_id, pipeline_id, notice_id, version_sha256
            ) DO UPDATE SET
                link_role = excluded.link_role,
                confidence = excluded.confidence,
                evidence = excluded.evidence
            """,
            [
                alert.event_id,
                link["notice_id"],
                link["version_sha256"],
                link["link_role"],
                link["confidence"],
                json.dumps(link["evidence"], sort_keys=True),
            ],
        )


def build_tgp_alerts(
    database_path: str | Path,
) -> AlertBuildSummary:
    connection = connect_database(database_path)
    initialize_database(connection)
    try:
        connection.execute("BEGIN TRANSACTION")
        notice_alerts = _notice_alerts(connection)
        notice_revision_alerts = _notice_content_revision_alerts(connection)
        outage_alerts = _outage_revision_alerts(connection)
        capacity_alerts = _capacity_alerts(connection)
        alerts = [
            *notice_alerts,
            *notice_revision_alerts,
            *outage_alerts,
            *capacity_alerts,
        ]
        for alert in alerts:
            _upsert_alert(connection, alert)
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    latest_decision = max((alert.decision_at for alert in alerts), default=None)
    return AlertBuildSummary(
        pipeline_id="TGP",
        notice_alert_count=len(notice_alerts),
        notice_revision_alert_count=len(notice_revision_alerts),
        outage_revision_alert_count=len(outage_alerts),
        capacity_alert_count=len(capacity_alerts),
        total_alert_count=len(alerts),
        latest_decision_at_utc=(
            latest_decision.to_iso8601_string() if latest_decision else None
        ),
    )
