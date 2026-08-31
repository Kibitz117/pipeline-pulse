from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from pipeline_pulse.artifacts import ArtifactStore
from pipeline_pulse.http import FetchResult


class ArtifactStoreTests(unittest.TestCase):
    def test_archives_content_by_hash_and_preserves_capture_time(self) -> None:
        body = b"<html>public source</html>"
        first_fetch = FetchResult(
            canonical_url="https://example.com/notices",
            status_code=200,
            sent_ts_ns=1_777_000_000_000_000_000,
            headers_received_ts_ns=1_777_000_000_010_000_000,
            received_ts_ns=1_777_000_000_020_000_000,
            body=body,
            content_type="text/html; charset=utf-8",
        )
        second_fetch = FetchResult(
            canonical_url=first_fetch.canonical_url,
            status_code=200,
            sent_ts_ns=first_fetch.sent_ts_ns + 1_000_000_000,
            headers_received_ts_ns=first_fetch.headers_received_ts_ns + 1_000_000_000,
            received_ts_ns=first_fetch.received_ts_ns + 1_000_000_000,
            body=body,
            content_type=first_fetch.content_type,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            store = ArtifactStore(temporary_directory)
            first = store.store("km_tgp_critical", first_fetch)
            second = store.store("km_tgp_critical", second_fetch)

            self.assertEqual(first.content_sha256, hashlib.sha256(body).hexdigest())
            self.assertEqual(first.raw_path, second.raw_path)
            self.assertNotEqual(first.artifact_id, second.artifact_id)
            self.assertEqual(Path(first.raw_path).read_bytes(), body)


if __name__ == "__main__":
    unittest.main()
