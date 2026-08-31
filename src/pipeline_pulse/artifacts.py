from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pendulum

from .http import FetchResult


_EXTENSIONS = {
    "application/json": ".json",
    "application/geo+json": ".geojson",
    "application/zip": ".zip",
    "application/ms-excel": ".xlsx",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/plain": ".txt",
}


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    source_code: str
    canonical_url: str
    content_sha256: str
    mime_type: str | None
    http_status: int
    requested_at: pendulum.DateTime
    received_at: pendulum.DateTime
    recorded_at: pendulum.DateTime
    raw_path: str
    size_bytes: int
    etag: str | None
    last_modified: str | None
    content_disposition: str | None


def _utc_from_ns(value: int) -> pendulum.DateTime:
    return pendulum.from_timestamp(value / 1_000_000_000, tz="UTC")


class ArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def store(self, source_code: str, fetch: FetchResult) -> StoredArtifact:
        if not source_code.strip():
            raise ValueError("source_code is required")
        content_sha256 = hashlib.sha256(fetch.body).hexdigest()
        received_at = _utc_from_ns(fetch.received_ts_ns)
        mime_type = (fetch.content_type or "").split(";", 1)[0].strip() or None
        extension = _EXTENSIONS.get(mime_type, ".bin")
        raw_path = (
            self.root
            / source_code
            / f"{received_at:%Y}"
            / f"{received_at:%m}"
            / f"{received_at:%d}"
            / f"{content_sha256}{extension}"
        )
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if not raw_path.exists():
            raw_path.write_bytes(fetch.body)
        recorded_at = pendulum.now("UTC")
        return StoredArtifact(
            artifact_id=f"{source_code}:{fetch.received_ts_ns}:{content_sha256}",
            source_code=source_code,
            canonical_url=fetch.canonical_url,
            content_sha256=content_sha256,
            mime_type=mime_type,
            http_status=fetch.status_code,
            requested_at=_utc_from_ns(fetch.sent_ts_ns),
            received_at=received_at,
            recorded_at=recorded_at,
            raw_path=raw_path.as_posix(),
            size_bytes=len(fetch.body),
            etag=fetch.etag,
            last_modified=fetch.last_modified,
            content_disposition=fetch.content_disposition,
        )
