#!/usr/bin/env python3
"""Unit tests for applib.scoring — run: .venv/bin/python scripts/tests/test_scoring.py"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib.scoring import score_listing  # noqa: E402

CRIT = {
    "scoring": {
        "weights": {"commute": 40, "condition": 30, "hood": 15, "value": 15},
        "unknown_value": 0.4,
        "commute_full_minutes": 15,
        "commute_zero_minutes": 35,
        "hood_preferences": {"hipsters": 1.0, "rich": 0.8, "normies": 0.6,
                             "suits": 0.3, "tourists": 0.3},
        "value_best_chf_m2": 25,
        "value_worst_chf_m2": 50,
        "condition_grades": {"modern": 1.0, "acceptable": 0.6, "dated": 0.2},
    }
}


def flat(**kw) -> dict:
    base = {"transit_min": None, "has_parking": None, "has_balcony": None,
            "condition_kitchen": None, "condition_bath": None,
            "hood_category": None, "rent_net": None, "rent_gross": None,
            "size_sqm": None}
    base.update(kw)
    return base


class ScoreListing(unittest.TestCase):
    def test_perfect_flat_scores_100(self):
        score, parts = score_listing(flat(
            transit_min=14, has_parking=True, has_balcony=True,
            condition_kitchen="modern", condition_bath="modern",
            hood_category="hipsters", rent_net=2000, size_sqm=80), CRIT)
        self.assertEqual(score, 100)
        self.assertEqual(parts["commute"], 1.0)

    def test_worst_known_flat(self):
        # commute 0, condition mean(0,0,.2,.2)=.1, hood .3, value 0 -> 7.5 -> 8
        score, _ = score_listing(flat(
            transit_min=35, has_parking=False, has_balcony=False,
            condition_kitchen="dated", condition_bath="dated",
            hood_category="tourists", rent_net=2500, size_sqm=45), CRIT)
        self.assertEqual(score, 8)

    def test_all_unknown_gets_slight_penalty_score(self):
        score, parts = score_listing(flat(), CRIT)
        self.assertEqual(score, 40)  # every component at unknown_value 0.4
        self.assertEqual(set(parts), {"commute", "condition", "hood", "value"})

    def test_commute_linear_midpoint(self):
        _, parts = score_listing(flat(transit_min=25), CRIT)
        self.assertAlmostEqual(parts["commute"], 0.5)

    def test_condition_unknown_label_treated_as_unknown(self):
        _, parts = score_listing(flat(
            has_parking=True, has_balcony=True,
            condition_kitchen="condition_unknown", condition_bath="modern"), CRIT)
        self.assertAlmostEqual(parts["condition"], (1 + 1 + 0.4 + 1.0) / 4)

    def test_value_falls_back_to_gross_rent(self):
        _, parts = score_listing(flat(rent_gross=2500, size_sqm=50), CRIT)
        self.assertAlmostEqual(parts["value"], 0.0)  # 50 CHF/m2 = worst bound

    def test_value_clamped_below_best_bound(self):
        _, parts = score_listing(flat(rent_net=1500, size_sqm=100), CRIT)
        self.assertEqual(parts["value"], 1.0)  # 15 CHF/m2, better than best bound


if __name__ == "__main__":
    unittest.main(verbosity=2)
