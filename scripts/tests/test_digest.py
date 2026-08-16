from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import digest  # noqa: E402


class MoveInHeaderTests(unittest.TestCase):
    """The digest header states every knockout the gate applied, so the move-in
    window has to show up there too."""

    def test_both_bounds(self):
        crit = {"move_in": {"earliest": "2026-08-15", "latest": "2026-09-15"}}
        self.assertEqual(digest._move_in_header(crit), " · frei ab 15.08.–15.09.")

    def test_one_sided_windows(self):
        self.assertEqual(
            digest._move_in_header({"move_in": {"latest": "2026-09-15"}}),
            " · frei bis 15.09.")
        self.assertEqual(
            digest._move_in_header({"move_in": {"earliest": "2026-08-15"}}),
            " · frei ab 15.08.")

    def test_no_window_adds_nothing(self):
        for crit in ({}, {"move_in": {}}, {"move_in": {"earliest": None, "latest": None}}):
            self.assertEqual(digest._move_in_header(crit), "", crit)

    def test_unparseable_bound_is_not_rendered(self):
        crit = {"move_in": {"earliest": "nach Vereinbarung", "latest": "2026-09-15"}}
        self.assertEqual(digest._move_in_header(crit), " · frei bis 15.09.")


class HeaderPunctuationTests(unittest.TestCase):
    def test_no_doubled_period_after_a_date_fragment(self):
        """"…frei ab 15.08.–15.09." already ends the sentence — appending
        another period rendered "15.09.._"."""
        head = "Office: X · ≥2 Zi" + digest._move_in_header(CRIT)
        closed = f"_{head}{'' if head.endswith('.') else '.'}_"
        self.assertTrue(closed.endswith("15.09._"), closed)
        self.assertNotIn(".._", closed)

    def test_sentence_still_closed_without_a_window(self):
        head = "Office: X · ≥2 Zi" + digest._move_in_header({})
        closed = f"_{head}{'' if head.endswith('.') else '.'}_"
        self.assertTrue(closed.endswith("Zi._"), closed)


class AvailabilityCellTests(unittest.TestCase):
    def test_iso_date_is_shortened(self):
        self.assertEqual(digest._availability({"availability": "2026-09-01"}), "frei ab 01.09.")

    def test_timestamp_is_read_as_a_date(self):
        self.assertEqual(digest._availability({"availability": "2026-08-15T00:00:00"}),
                         "frei ab 15.08.")

    def test_missing_is_marked_unknown_not_guessed(self):
        for lst in ({}, {"availability": None}, {"availability": "   "}):
            self.assertEqual(digest._availability(lst), "frei ab ?", lst)

    def test_free_text_is_shown_verbatim(self):
        # Hard rule #5: never invent a date, but don't hide what the listing says.
        self.assertEqual(digest._availability({"availability": "nach Vereinbarung"}),
                         "frei ab nach Vereinbarung")

    def test_line_carries_availability(self):
        line = digest._line({"id": "flatfox-1", "availability": "2026-09-01"}, "net")
        self.assertIn("frei ab 01.09.", line)


CRIT = {"move_in": {"earliest": "2026-08-15", "latest": "2026-09-15"}}


def R(reason, availability="2026-08-01"):
    return {"id": "x", "gate_status": "rejected", "reject_reason": reason,
            "availability": availability}


class NearMissFooterTests(unittest.TestCase):
    """The window's cost has to stay visible — rejected listings otherwise
    disappear from the digest entirely."""

    def test_counts_both_sides(self):
        active = [R("available 2026-08-01 before move-in window 2026-08-15..2026-09-15"),
                  R("available 2026-10-01 after move-in window 2026-08-15..2026-09-15",
                    "2026-10-01")]
        out = "\n".join(digest._move_in_near_misses(active, CRIT))
        self.assertIn("**1 free too early** (before 15.08.)", out)
        self.assertIn("**1 free too late** (after 15.09.)", out)

    def test_listing_with_another_hard_fail_is_not_a_near_miss(self):
        active = [R("rent 3400 > 2750; available 2026-08-01 before move-in window "
                    "2026-08-15..2026-09-15")]
        self.assertEqual(digest._move_in_near_misses(active, CRIT), [])

    def test_passing_and_manual_listings_are_ignored(self):
        active = [{"id": "a", "gate_status": "passed", "reject_reason": None},
                  {"id": "b", "gate_status": "manual", "reject_reason": None}]
        self.assertEqual(digest._move_in_near_misses(active, CRIT), [])

    def test_no_footer_without_a_window(self):
        active = [R("available 2026-08-01 before move-in window 2026-08-15..2026-09-15")]
        self.assertEqual(digest._move_in_near_misses(active, {"move_in": {}}), [])

    def test_dates_summary_ranks_and_truncates(self):
        rows = [R("x", "2026-08-01") for _ in range(26)] + \
               [R("x", "2026-07-01") for _ in range(4)] + \
               [R("x", "2026-06-15"), R("x", "2026-06-01")]
        s = digest._dates_summary(rows)
        self.assertTrue(s.startswith("26× 01.08., 4× 01.07."), s)
        self.assertIn("+1 other", s)

    def test_dates_summary_survives_free_text_availability(self):
        self.assertEqual(digest._dates_summary([R("x", "nach Vereinbarung")]),
                         "no parseable dates")


if __name__ == "__main__":
    unittest.main()
