from __future__ import annotations

import unittest

import pendulum

from pipeline_pulse.models import AuditClocks, CapacityImpact, NoticeKey


class CapacityImpactTests(unittest.TestCase):
    def test_calculates_reduction(self) -> None:
        impact = CapacityImpact(1_000_000, 650_000)

        self.assertEqual(impact.reduction_dth_per_day, 350_000)
        self.assertAlmostEqual(impact.reduction_pct or 0, 0.35)

    def test_missing_capacity_stays_unknown(self) -> None:
        impact = CapacityImpact(None, None)

        self.assertIsNone(impact.reduction_dth_per_day)
        self.assertIsNone(impact.reduction_pct)

    def test_rejects_negative_capacity(self) -> None:
        with self.assertRaises(ValueError):
            CapacityImpact(10, -1)


class NoticeKeyTests(unittest.TestCase):
    def test_requires_both_identifiers(self) -> None:
        with self.assertRaises(ValueError):
            NoticeKey("TGP", "")


class AuditClockTests(unittest.TestCase):
    def test_rejects_processing_before_receipt(self) -> None:
        received = pendulum.datetime(2026, 8, 28, 12, tz="UTC")
        with self.assertRaises(ValueError):
            AuditClocks(
                source_published_at=None,
                received_at=received,
                processed_at=received.subtract(seconds=1),
                available_at=received,
            )


if __name__ == "__main__":
    unittest.main()
