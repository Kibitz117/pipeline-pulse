from __future__ import annotations

from dataclasses import dataclass

KINDER_MORGAN_POSTINGS_ROOT = "https://pipeline2.kindermorgan.com"


@dataclass(frozen=True)
class PipelineConfig:
    """Stable identity and source capabilities for one pipeline system."""

    pipeline_id: str
    pipeline_name: str
    operator_id: str
    operator_name: str
    parent_company: str
    ticker: str | None
    portal_code: str
    portal_tsp_code: str
    tsp_number: str
    ferc_cid: str
    timezone: str
    supports_outage_impact_report: bool = False

    @property
    def slug(self) -> str:
        return self.pipeline_id.lower()

    @property
    def critical_index_url(self) -> str:
        return (
            f"{KINDER_MORGAN_POSTINGS_ROOT}/Notices/Notices.aspx"
            f"?code={self.portal_code}&type=C"
        )

    def notice_detail_url(self, notice_id: str) -> str:
        return (
            f"{KINDER_MORGAN_POSTINGS_ROOT}/Notices/NoticeDetail.aspx"
            f"?code={self.portal_code}&notc_nbr={notice_id}"
        )

    @property
    def location_url(self) -> str:
        return (
            f"{KINDER_MORGAN_POSTINGS_ROOT}/LocationDataDownload/"
            f"LocDataDwnld.aspx?code={self.portal_code}"
        )

    @property
    def point_capacity_url(self) -> str:
        return (
            f"{KINDER_MORGAN_POSTINGS_ROOT}/Capacity/OpAvailPoint.aspx"
            f"?code={self.portal_code}"
        )

    @property
    def segment_capacity_url(self) -> str:
        return (
            f"{KINDER_MORGAN_POSTINGS_ROOT}/Capacity/OpAvailSegment.aspx"
            f"?code={self.portal_code}"
        )

    @property
    def portal_url(self) -> str:
        return (
            "https://pipeportal.kindermorgan.com/PortalUI/DefaultKM.aspx"
            f"?TSP={self.portal_tsp_code}"
        )


KINDER_MORGAN_PIPELINES: dict[str, PipelineConfig] = {
    "TGP": PipelineConfig(
        pipeline_id="TGP",
        pipeline_name="Tennessee Gas Pipeline",
        operator_id="km",
        operator_name="Kinder Morgan",
        parent_company="Kinder Morgan, Inc.",
        ticker="KMI",
        portal_code="TGP",
        portal_tsp_code="TGPD",
        tsp_number="1939164",
        ferc_cid="C000020",
        timezone="America/Chicago",
        supports_outage_impact_report=True,
    ),
    "NGPL": PipelineConfig(
        pipeline_id="NGPL",
        pipeline_name="Natural Gas Pipeline Company of America",
        operator_id="km",
        operator_name="Kinder Morgan",
        parent_company="Kinder Morgan, Inc.",
        ticker="KMI",
        portal_code="NGPL",
        portal_tsp_code="NGPL",
        tsp_number="6931794",
        ferc_cid="C002096",
        timezone="America/Chicago",
    ),
}


def get_pipeline_config(pipeline_id: str) -> PipelineConfig:
    normalized = pipeline_id.strip().upper()
    try:
        return KINDER_MORGAN_PIPELINES[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(KINDER_MORGAN_PIPELINES))
        raise ValueError(
            f"unsupported Kinder Morgan pipeline {pipeline_id!r}; "
            f"choose one of: {supported}"
        ) from exc
