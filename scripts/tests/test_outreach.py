#!/usr/bin/env python3
"""Unit tests for scripts/outreach.render — run: .venv/bin/python scripts/tests/test_outreach.py"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib import config  # noqa: E402
from outreach import _fmt_date, render  # noqa: E402

TIMING = (config.criteria().get("outreach") or {}).get("timing") or {}
# Personal facts come from config/applicant.yaml (gitignored) with
# applicant.example.yaml as the fallback. Assert against whatever is configured,
# never against a frozen name/age/role — those differ per user by design.
PROFILE = config.applicant().get("profile") or {}


def L(**kw):
    base = {"id": "flatfox-1", "title": "Schöne Wohnung",
            "blurb": "Helle 3.5-Zimmer-Wohnung mit Küche und Bad in Zürich",
            "street": "Hardstrasse 10", "zipcode": "8005", "city": "Zürich",
            "rooms": 3.5, "size_sqm": 80, "hood_name": None, "has_balcony": False,
            "has_parking": False, "transit_min": None, "availability": None,
            "rent_net": 2200}
    base.update(kw)
    return base


class Render(unittest.TestCase):
    def test_de_bio_commute_dates(self):
        p = render(L(transit_min=33, availability="2026-08-01"))
        b = p["body"]
        self.assertEqual(p["language"], "de")
        self.assertIn("Guten Tag", b)                   # casual greeting
        self.assertNotIn("Sehr geehrte", b)
        self.assertIn(str(PROFILE["age"]), b)           # age
        self.assertIn(PROFILE["role_de"], b)            # role
        self.assertIn(_fmt_date(PROFILE["job_start"], "de"), b)   # job start
        self.assertIn("rund 35 Minuten", b)             # commute, rounded to nearest 5
        self.assertIn("Wäre eine Besichtigung", b)      # explicit viewing ask
        # Copy comes from config — assert against it, not a frozen string, so
        # rewording the window doesn't break the test.
        self.assertIn(TIMING["move_in_window_de"], b)   # standard move-in window
        self.assertIn(TIMING["viewing_note_de"], b)     # flexibility sentence

    def test_de_adapts_to_later_availability(self):
        """A flat free only AFTER the configured window: the draft drops the
        standard window and names the listing's own date instead. Derived from
        config so the test doesn't go stale when the window moves."""
        later = f"{int(str(TIMING['move_in_latest'])[:4]) + 1}-12-01"
        b = render(L(hood_name="Oerlikon", availability=later))["body"]
        self.assertIn("1. Dezember", b)
        self.assertNotIn(TIMING["move_in_window_de"], b)

    def test_no_personal_line_even_when_hood_and_feature_known(self):
        b = render(L(hood_name="Wiedikon", has_balcony=True))["body"]
        self.assertNotIn("Besonders", b)

    def test_no_commute_when_unknown(self):
        b = render(L(transit_min=None))["body"]
        self.assertNotIn("Minuten mit dem", b)

    def test_english_listing(self):
        b = render(L(title="Bright apartment",
                     blurb="Lovely apartment near the lake, available now, great location",
                     hood_name="Seefeld", has_balcony=True, transit_min=18))["body"]
        self.assertIn("Hello,", b)                      # casual greeting
        self.assertNotIn("Dear Sir or Madam", b)
        self.assertNotIn("particularly drawn", b)
        self.assertIn(PROFILE["role_en"], b)
        self.assertIn(_fmt_date(PROFILE["job_start"], "en"), b)
        self.assertIn("Would a viewing", b)             # explicit viewing ask
        self.assertIn(TIMING["viewing_note_en"], b)
        self.assertIn("about 20 minutes", b)            # 18 -> nearest 5 = 20

    def test_draft_move_in_copy_matches_the_gate_window(self):
        """The prose window in drafts and the move_in knockout are the same
        window — they drifted apart once (drafts said August while the gate
        allowed to 15.09) and every draft then contradicted the filter."""
        self.assertEqual(TIMING["move_in_latest"],
                         config.criteria()["move_in"]["latest"])

    def test_viewing_window_is_an_adverbial_not_a_sentence(self):
        """It renders into "Wäre eine Besichtigung … möglich?" — a value with
        sentence punctuation would produce broken German."""
        for key in ("viewing_window_de", "viewing_window_en"):
            self.assertNotIn(".", TIMING[key], key)
            self.assertNotIn(",", TIMING[key], key)


if __name__ == "__main__":
    unittest.main()
