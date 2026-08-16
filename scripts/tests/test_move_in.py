from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gate  # noqa: E402

WINDOW = {"earliest": "2026-08-15", "latest": "2026-09-15", "unknown_to_manual": True}


def verdict(availability, cfg=None) -> str:
    return gate.move_in_verdict(availability, WINDOW if cfg is None else cfg)[0]


class MoveInWindowTests(unittest.TestCase):
    def test_inside_window_passes(self):
        for a in ("2026-08-15", "2026-09-01", "2026-09-15"):
            self.assertEqual(verdict(a), "ok", a)

    def test_before_window_is_hard_fail(self):
        # The strict reading the original author chose: a flat free earlier is still rejected.
        for a in ("2026-08-01", "2026-08-14", "2026-06-01"):
            self.assertEqual(verdict(a), "rejected", a)

    def test_after_window_is_hard_fail(self):
        for a in ("2026-09-16", "2026-10-01", "2027-01-01"):
            self.assertEqual(verdict(a), "rejected", a)

    def test_missing_date_goes_to_manual(self):
        for a in (None, "", "   "):
            self.assertEqual(verdict(a), "unknown", repr(a))

    def test_free_text_availability_goes_to_manual_not_rejected(self):
        # scout.py emits these for Flatfox IMMEDIATELY / BY_AGREEMENT; we never
        # guess a date from them.
        for a in ("nach Vereinbarung", "ab sofort"):
            self.assertEqual(verdict(a), "unknown", a)

    def test_unknown_detail_carries_the_raw_text(self):
        _, detail = gate.move_in_verdict("nach Vereinbarung", WINDOW)
        self.assertEqual(detail, "movein_unknown (nach Vereinbarung)")
        self.assertEqual(gate.move_in_verdict(None, WINDOW)[1], "movein_unknown")

    def test_reject_detail_names_the_window(self):
        _, detail = gate.move_in_verdict("2026-10-01", WINDOW)
        self.assertIn("2026-10-01", detail)
        self.assertIn("2026-08-15..2026-09-15", detail)

    def test_unknown_to_manual_false_ignores_missing_dates(self):
        cfg = dict(WINDOW, unknown_to_manual=False)
        self.assertEqual(verdict(None, cfg), "ok")
        # A known out-of-window date is still a hard fail.
        self.assertEqual(verdict("2026-10-01", cfg), "rejected")

    def test_open_bounds(self):
        self.assertEqual(verdict("2026-06-01", {"earliest": None, "latest": "2026-09-15"}), "ok")
        self.assertEqual(verdict("2027-01-01", {"earliest": "2026-08-15", "latest": None}), "ok")
        # No window configured at all → the knockout is inert.
        self.assertEqual(verdict(None, {}), "ok")
        self.assertEqual(verdict("2020-01-01", {"earliest": None, "latest": None}), "ok")

    def test_timestamped_availability_is_read_as_a_date(self):
        self.assertEqual(verdict("2026-09-01T00:00:00"), "ok")


if __name__ == "__main__":
    unittest.main()
