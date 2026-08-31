from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pendulum


class EventStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ChangeType(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    EXTENDED = "extended"
    ESCALATED = "escalated"
    REDUCED = "reduced"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class ImpactChannel(StrEnum):
    UPSTREAM_RECEIPT = "upstream_receipt"
    DOWNSTREAM_DELIVERY = "downstream_delivery"
    SEGMENT_TRANSPORT = "segment_transport"
    STORAGE = "storage"
    SYSTEM_BALANCE = "system_balance"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NoticeKey:
    pipeline_id: str
    notice_id: str

    def __post_init__(self) -> None:
        if not self.pipeline_id.strip() or not self.notice_id.strip():
            raise ValueError("pipeline_id and notice_id are required")


@dataclass(frozen=True)
class CapacityImpact:
    nominal_dth_per_day: float | None
    operating_dth_per_day: float | None

    def __post_init__(self) -> None:
        for value in (self.nominal_dth_per_day, self.operating_dth_per_day):
            if value is not None and value < 0:
                raise ValueError("capacity cannot be negative")

    @property
    def reduction_dth_per_day(self) -> float | None:
        if self.nominal_dth_per_day is None or self.operating_dth_per_day is None:
            return None
        return self.nominal_dth_per_day - self.operating_dth_per_day

    @property
    def reduction_pct(self) -> float | None:
        reduction = self.reduction_dth_per_day
        if reduction is None or self.nominal_dth_per_day in (None, 0):
            return None
        return reduction / self.nominal_dth_per_day


@dataclass(frozen=True)
class AuditClocks:
    source_published_at: pendulum.DateTime | None
    received_at: pendulum.DateTime
    processed_at: pendulum.DateTime
    available_at: pendulum.DateTime

    def __post_init__(self) -> None:
        clocks = [self.received_at, self.processed_at, self.available_at]
        if any(not isinstance(value, pendulum.DateTime) for value in clocks):
            raise TypeError("audit clocks must be Pendulum DateTime values")
        if self.source_published_at is not None and not isinstance(
            self.source_published_at, pendulum.DateTime
        ):
            raise TypeError("source_published_at must be a Pendulum DateTime")
        if self.processed_at < self.received_at:
            raise ValueError("processed_at cannot precede received_at")
        if self.available_at < self.received_at:
            raise ValueError("available_at cannot precede receipt")
