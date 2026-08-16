from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib import config  # noqa: E402
from applib import learning  # noqa: E402
from applib import paths  # noqa: E402


def use_temp_data(testcase) -> Path:
    """Point applib.paths at a fresh temp data dir for the duration of a test."""
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
    saved = {k: getattr(paths, k) for k in
             ("DATA_DIR", "LISTINGS_FILE", "LEARNED_PREFS_FILE",
              "LEARNING_LOG_FILE", "PHOTOS_DIR")}
    testcase.addCleanup(lambda: [setattr(paths, k, v) for k, v in saved.items()])
    paths.DATA_DIR = tmp
    paths.LISTINGS_FILE = tmp / "listings.json"
    paths.LEARNED_PREFS_FILE = tmp / ".learned_prefs.json"
    paths.LEARNING_LOG_FILE = tmp / ".learning_log.jsonl"
    paths.PHOTOS_DIR = tmp / "photos"
    return tmp

def write_store(listings: dict):  # used by later test classes (Tasks 6+)
    paths.LISTINGS_FILE.write_text(
        json.dumps({"meta": {"schema_version": 1}, "listings": listings}),
        encoding="utf-8")


class DeepMerge(unittest.TestCase):
    def test_deep_merge_nested_dicts(self):
        base = {"scoring": {"weights": {"commute": 40, "hood": 15}, "value_worst_chf_m2": 50}}
        overlay = {"scoring": {"weights": {"hood": 25}}}
        out = config.deep_merge(base, overlay)
        self.assertEqual(out["scoring"]["weights"]["hood"], 25)
        self.assertEqual(out["scoring"]["weights"]["commute"], 40)  # untouched
        self.assertEqual(out["scoring"]["value_worst_chf_m2"], 50)  # untouched
        self.assertEqual(base["scoring"]["weights"]["hood"], 15)    # base not mutated

    def test_effective_criteria_no_overlay_returns_baseline(self):
        use_temp_data(self)
        eff = config.effective_criteria()
        self.assertEqual(eff["scoring"]["weights"], config.criteria()["scoring"]["weights"])


def L(**kw):
    base = {"decision": None, "decline_reasons": None, "hood_category": None,
            "rent_net": None, "size_sqm": None}
    base.update(kw)
    return base


class Signal(unittest.TestCase):
    def test_collects_positive_and_negative(self):
        listings = {
            "a": L(decision="outreach", hood_category="hipsters"),
            "b": L(decision="deprioritized", decline_reasons=["too_expensive"], rent_net=3000, size_sqm=50),
            "c": L(decision=None),
        }
        sig = learning.collect_signal(listings)
        self.assertEqual(sig["n_positive"], 1)
        self.assertEqual(sig["n_negative"], 1)
        self.assertEqual(sig["n_total"], 2)
        self.assertEqual(sig["dimension_counts"]["value"], 1)  # too_expensive -> value

    def test_free_text_note_feeds_dimensions(self):
        listings = {
            "a": L(decision="deprioritized", decline_reasons=[],
                   decision_note="way too expensive for the size"),
            "b": L(decision="deprioritized", decline_reasons=[],
                   decision_note="dated kitchen and ugly building"),
            "c": L(decision="deprioritized", decline_reasons=[],
                   decision_note="great flat but the area is too noisy"),
        }
        dc = learning.collect_signal(listings)["dimension_counts"]
        self.assertEqual(dc.get("value"), 1)       # "expensive" -> value
        self.assertEqual(dc.get("condition"), 1)   # "dated"/"ugly" -> condition
        self.assertEqual(dc.get("hood"), 1)        # "area"/"noisy" -> hood

    def test_chip_and_note_counted_once_per_listing(self):
        listings = {"a": L(decision="deprioritized", decline_reasons=["dated"],
                           decision_note="dated, ugly_building")}
        dc = learning.collect_signal(listings)["dimension_counts"]
        self.assertEqual(dc.get("condition"), 1)   # not double-counted


BASE_W = {"commute": 40, "condition": 30, "hood": 15, "value": 15}


class Weights(unittest.TestCase):
    def test_cold_start_no_change(self):
        sig = {"n_total": 5, "dimension_counts": {"value": 4}}  # below COLD_START_MIN
        self.assertEqual(learning.retune_weights(BASE_W, sig), BASE_W)

    def test_below_dim_threshold_no_change(self):
        sig = {"n_total": 10, "dimension_counts": {"value": 2}}  # dim < DIM_MIN_SIGNAL
        self.assertEqual(learning.retune_weights(BASE_W, sig), BASE_W)

    def test_qualifying_dimension_gains_weight_sum_100(self):
        sig = {"n_total": 10, "dimension_counts": {"value": 6}}
        out = learning.retune_weights(BASE_W, sig)
        self.assertGreater(out["value"], BASE_W["value"])     # value emphasised
        self.assertLess(out["commute"], BASE_W["commute"])    # others reduced
        self.assertEqual(sum(out.values()), 100)              # renormalized + rounded

    def test_pipeline_respects_2x_ceiling(self):
        sig = {"n_total": 50, "dimension_counts": {"value": 50}}
        out = learning.retune_weights(BASE_W, sig)
        self.assertLessEqual(out["value"], 2 * BASE_W["value"])

    def test_clamp_enforces_bounds_directly(self):
        # the pipeline's clamp is unreachable with WEIGHT_STEP=3, so test the
        # guarantee at its source: a value above the ceiling is pulled to it.
        self.assertEqual(learning._clamp(50, 7.5, 30), 30)
        self.assertEqual(learning._clamp(2, 7.5, 30), 7.5)
        self.assertEqual(learning._clamp(20, 7.5, 30), 20)


class HoodsAndPrice(unittest.TestCase):
    def test_hood_pref_drops_when_declined(self):
        base_prefs = {"hipsters": 1.0, "normies": 0.6}
        neg = [L(decision="deprioritized", decline_reasons=["wrong_area"], hood_category="normies")
               for _ in range(3)]
        out = learning.retune_hoods(base_prefs, positives=[], negatives=neg)
        self.assertLess(out["normies"], 0.6)
        self.assertGreaterEqual(out["normies"], 0.0)
        self.assertEqual(out["hipsters"], 1.0)  # untouched (no decisions)

    def test_hood_pref_below_min_decisions_unchanged(self):
        base_prefs = {"normies": 0.6}
        neg = [L(decision="deprioritized", decline_reasons=["wrong_area"], hood_category="normies")]
        out = learning.retune_hoods(base_prefs, positives=[], negatives=neg)
        self.assertEqual(out["normies"], 0.6)

    def test_price_ceiling_pulls_worst_down(self):
        base_best, base_worst = 25.0, 50.0
        # three "too expensive" flats around 36 CHF/m2 -> worst should move toward there
        neg = [L(decision="deprioritized", decline_reasons=["too_expensive"], rent_net=r, size_sqm=s)
               for r, s in [(3600, 100), (3500, 100), (3700, 100)]]
        out = learning.retune_price(base_best, base_worst, neg)
        self.assertLess(out, base_worst)
        self.assertGreater(out, base_best)


class Orchestrate(unittest.TestCase):
    def _many(self, n_value=6, n_total=10):
        d = {}
        for i in range(n_value):
            d[f"n{i}"] = L(decision="deprioritized", decline_reasons=["too_expensive"],
                           rent_net=3600, size_sqm=100, hood_category="normies")
        for i in range(n_total - n_value):
            d[f"p{i}"] = L(decision="outreach", hood_category="hipsters")
        return d

    def test_retune_writes_overlay_and_log(self):
        use_temp_data(self)
        learning.retune(self._many())
        ov = learning.scoring_overlay()["scoring"]
        self.assertGreater(ov["weights"]["value"], 15)
        self.assertEqual(sum(ov["weights"].values()), 100)
        self.assertTrue(paths.LEARNING_LOG_FILE.exists())

    def test_paused_skips_update(self):
        use_temp_data(self)
        learning.set_paused(True)
        learning.retune(self._many())
        self.assertEqual(learning.scoring_overlay(), {})  # nothing learned while paused

    def test_reset_clears_overlay(self):
        use_temp_data(self)
        learning.retune(self._many())
        learning.reset()
        self.assertEqual(learning.scoring_overlay(), {})
        self.assertFalse(paths.LEARNED_PREFS_FILE.exists())

    def test_status_reports_deltas(self):
        use_temp_data(self)
        learning.retune(self._many())
        st = learning.status(self._many())
        self.assertIn("baseline", st)
        self.assertIn("learned", st)
        self.assertIn("dimension_counts", st)
        self.assertFalse(st["paused"])

    def test_retune_writes_metadata(self):
        use_temp_data(self)
        learning.retune(self._many())
        data = json.loads(paths.LEARNED_PREFS_FILE.read_text(encoding="utf-8"))
        self.assertIn("updated_at", data)
        self.assertEqual(data["sample_counts"]["negative"], 6)
        self.assertEqual(data["sample_counts"]["positive"], 4)

    def test_cold_start_writes_no_overlay(self):
        use_temp_data(self)
        learning.retune(self._many(n_value=1, n_total=2))  # below COLD_START_MIN
        self.assertEqual(learning.scoring_overlay(), {})
        self.assertFalse(paths.LEARNED_PREFS_FILE.exists())

    def test_dropping_below_cold_start_clears_overlay(self):
        use_temp_data(self)
        learning.retune(self._many())                       # learn (>=8)
        self.assertIn("scoring", learning._load())
        learning.retune(self._many(n_value=1, n_total=2))   # fall back below threshold
        self.assertEqual(learning.scoring_overlay(), {})

    def test_pause_preserves_prior_learning(self):
        use_temp_data(self)
        learning.retune(self._many())          # learn first
        learned = learning.scoring_overlay()["scoring"]
        learning.set_paused(True)              # then pause
        self.assertEqual(learning.scoring_overlay()["scoring"], learned)  # kept
        self.assertTrue(learning.status({})["paused"])
        learning.set_paused(False)            # un-pausing keeps it too
        self.assertEqual(learning.scoring_overlay()["scoring"], learned)


if __name__ == "__main__":
    unittest.main()
