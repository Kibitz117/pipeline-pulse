from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline_pulse.scheduler import (
    CollectionAlreadyRunning,
    exclusive_collection_lock,
)
from pipeline_pulse.__main__ import build_parser


class SchedulerLockTests(unittest.TestCase):
    def test_cli_exposes_one_command_bootstrap(self) -> None:
        args = build_parser().parse_args(
            ["scheduled-collect", "--mode", "bootstrap"]
        )

        self.assertEqual(args.mode, "bootstrap")
        self.assertEqual(args.bootstrap_detail_limit, 100)

    def test_rejects_overlapping_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            lock_path = Path(temporary_directory) / "collector.lock"
            with exclusive_collection_lock(lock_path):
                with self.assertRaises(CollectionAlreadyRunning):
                    with exclusive_collection_lock(lock_path):
                        self.fail("overlapping lock should not be acquired")


if __name__ == "__main__":
    unittest.main()
