from __future__ import annotations

import hashlib
import socket
import time
from http.cookiejar import CookieJar
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


class SourceFetchError(RuntimeError):
    """A read-only public-data request failed."""


@dataclass(frozen=True)
class FetchResult:
    canonical_url: str
    status_code: int
    sent_ts_ns: int
    headers_received_ts_ns: int
    received_ts_ns: int
    body: bytes
    etag: str | None = None
    last_modified: str | None = None
    content_type: str | None = None
    content_disposition: str | None = None

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def text(self) -> str:
        return self.body.decode("utf-8-sig")

    @property
    def latency_ns(self) -> int:
        return self.received_ts_ns - self.sent_ts_ns


def _safe_endpoint(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class ReadOnlyHTTPClient:
    def __init__(
        self,
        *,
        contact_email: str | None = None,
        timeout_seconds: float = 120.0,
        opener: Callable[..., Any] | None = None,
        time_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.contact_email = contact_email
        self.timeout_seconds = timeout_seconds
        self._opener = opener or build_opener(
            HTTPCookieProcessor(CookieJar())
        ).open
        self._time_ns = time_ns

    def fetch(self, url: str, *, accept: str = "*/*") -> FetchResult:
        return self._request(url, accept=accept)

    def post_form(
        self,
        url: str,
        fields: list[tuple[str, str]],
        *,
        accept: str = "*/*",
        referer: str | None = None,
    ) -> FetchResult:
        return self._request(
            url,
            accept=accept,
            data=urlencode(fields).encode("utf-8"),
            content_type="application/x-www-form-urlencoded",
            referer=referer,
        )

    def _request(
        self,
        url: str,
        *,
        accept: str,
        data: bytes | None = None,
        content_type: str | None = None,
        referer: str | None = None,
    ) -> FetchResult:
        if urlsplit(url).scheme != "https":
            raise ValueError("public-data URLs must use HTTPS")
        user_agent = "PipelinePulse/0.1"
        if self.contact_email:
            user_agent += f" (contact: {self.contact_email})"
        request = Request(
            url,
            headers={
                "Accept": accept,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "User-Agent": user_agent,
            },
            data=data,
            method="POST" if data is not None else "GET",
        )
        if content_type:
            request.add_header("Content-Type", content_type)
        if referer:
            request.add_header("Referer", referer)
        sent_ts_ns = self._time_ns()
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                headers_received_ts_ns = self._time_ns()
                body = response.read()
                received_ts_ns = self._time_ns()
                status_code = int(response.status)
                headers = response.headers
        except HTTPError as exc:
            raise SourceFetchError(
                f"public source returned HTTP {exc.code}: {_safe_endpoint(url)}"
            ) from exc
        except (TimeoutError, socket.timeout, URLError, OSError) as exc:
            raise SourceFetchError(
                f"public source request failed: {_safe_endpoint(url)}"
            ) from exc

        return FetchResult(
            canonical_url=url,
            status_code=status_code,
            sent_ts_ns=sent_ts_ns,
            headers_received_ts_ns=headers_received_ts_ns,
            received_ts_ns=received_ts_ns,
            body=body,
            etag=headers.get("ETag"),
            last_modified=headers.get("Last-Modified"),
            content_type=headers.get("Content-Type"),
            content_disposition=headers.get("Content-Disposition"),
        )
