# ZRH Apartments Desktop UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web app to browse the apartment pipeline in buckets, reach out with a pre-formulated message in one click, decline with a reason, and auto-retune the fit score from those decisions.

**Architecture:** Thin Python `http.server` backend that reuses existing `scripts/applib` modules (`Store`, `scoring`, `outreach`) and serves a vanilla HTML/JS frontend. A new `applib/learning.py` maintains a learned-preferences overlay merged on top of `criteria.yaml` at scoring time; both the app and the morning digest read the merged ("effective") criteria.

**Tech Stack:** Python 3 stdlib (`http.server`, `json`), existing `applib`, vanilla HTML/CSS/JS (no build step). Tests: stdlib `unittest`, run with `.venv/bin/python scripts/tests/test_X.py`.

**Spec:** `docs/superpowers/specs/2026-06-15-apartment-desktop-ui-design.md`

---

## File Structure

- `scripts/applib/paths.py` — **modify**: add `LEARNED_PREFS_FILE`, `LEARNING_LOG_FILE`, `WEB_DIR`.
- `scripts/applib/config.py` — **modify**: add `deep_merge()` and `effective_criteria()`.
- `scripts/applib/learning.py` — **create**: overlay storage, signal collection, retune algorithm, pause/reset.
- `scripts/serve_ui.py` — **create**: pure request-handler functions + `http.server` wiring + static serving.
- `web/index.html`, `web/style.css`, `web/app.js` — **create**: the frontend.
- `scripts/digest.py`, `scripts/bucket.py` — **modify**: read `config.effective_criteria()` instead of `config.criteria()`.
- `ZRH Apartments.command` — **create**: launcher.
- `scripts/tests/test_learning.py`, `scripts/tests/test_serve_ui.py` — **create**: tests.

### Conventions (match existing code)
- Every script/test inserts the scripts dir on the path:
  `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))` (tests use `parents[1]`; scripts in `scripts/` use `parents[0]`... see existing files — `scout.py` uses `Path(__file__).resolve().parent`).
- Tests are `unittest.TestCase`, run directly (`if __name__ == "__main__": unittest.main()`).
- Store writes go through `Store.save()` only.

### Test isolation helper (used by both test files)
Both `learning` and the store read module-level paths in `applib.paths`. Tests redirect those paths to a temp dir and reload a fresh store. Pattern used in every test file below:

```python
import json, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

def write_store(listings: dict):
    paths.LISTINGS_FILE.write_text(
        json.dumps({"meta": {"schema_version": 1}, "listings": listings}),
        encoding="utf-8")
```

---

## Task 1: Paths + effective-criteria merge

**Files:**
- Modify: `scripts/applib/paths.py`
- Modify: `scripts/applib/config.py`
- Test: `scripts/tests/test_learning.py`

- [ ] **Step 1: Add path constants**

In `scripts/applib/paths.py`, after `SUMMARY_FILE`:

```python
LEARNED_PREFS_FILE = DATA_DIR / ".learned_prefs.json"  # learning overlay (gitignored)
LEARNING_LOG_FILE = DATA_DIR / ".learning_log.jsonl"   # append-only retune log
WEB_DIR = ROOT / "web"                                 # frontend static files
```

- [ ] **Step 2: Write the failing test for `deep_merge` + `effective_criteria`**

Create `scripts/tests/test_learning.py` with the isolation helper above, then add:

```python
from applib import config  # noqa: E402

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

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run it, verify it fails**

Run: `.venv/bin/python scripts/tests/test_learning.py DeepMerge -v`
Expected: FAIL — `AttributeError: module 'applib.config' has no attribute 'deep_merge'`.

- [ ] **Step 4: Implement in `config.py`**

Add to `scripts/applib/config.py`:

```python
def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay onto a copy of base (base is never mutated)."""
    out = dict(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def effective_criteria() -> dict:
    """criteria.yaml with the learned-preferences overlay merged on top."""
    from . import learning  # local import to avoid a cycle at module load
    base = criteria()
    overlay = learning.scoring_overlay()
    return deep_merge(base, overlay) if overlay else base
```

- [ ] **Step 5: Add a minimal `scoring_overlay` stub so the import resolves**

Create `scripts/applib/learning.py`:

```python
"""Learned-preferences overlay + auto-retune of the fit score.

The overlay lives in data/.learned_prefs.json and is merged on top of
criteria.yaml by config.effective_criteria(). criteria.yaml is never written.
"""
from __future__ import annotations
import json
from . import paths


def _load() -> dict:
    if paths.LEARNED_PREFS_FILE.exists():
        return json.loads(paths.LEARNED_PREFS_FILE.read_text(encoding="utf-8"))
    return {}


def scoring_overlay() -> dict:
    """The {'scoring': {...}} fragment to merge, or {} when nothing learned."""
    data = _load()
    sc = data.get("scoring")
    return {"scoring": sc} if sc else {}
```

- [ ] **Step 6: Run tests, verify pass**

Run: `.venv/bin/python scripts/tests/test_learning.py DeepMerge -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add scripts/applib/paths.py scripts/applib/config.py scripts/applib/learning.py scripts/tests/test_learning.py
git commit -m "feat(learning): effective-criteria overlay merge + paths"
```

---

## Task 2: Signal collection + reason map

**Files:**
- Modify: `scripts/applib/learning.py`
- Test: `scripts/tests/test_learning.py`

- [ ] **Step 1: Write failing test**

Append to `test_learning.py`:

```python
from applib import learning  # noqa: E402

def L(**kw):
    base = {"decision": None, "decline_reasons": None, "hood_category": None,
            "rent_net": None, "size_sqm": None}
    base.update(kw); return base

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
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python scripts/tests/test_learning.py Signal -v`
Expected: FAIL — `AttributeError: ... 'collect_signal'`.

- [ ] **Step 3: Implement**

Add to `learning.py`:

```python
REASON_DIMENSION = {
    "too_expensive": "value",
    "too_small": "value",
    "dated": "condition",
    "ugly_building": "condition",
    "bad_layout": "condition",
    "wrong_area": "hood",
    "too_far": "commute",
}


def collect_signal(listings: dict) -> dict:
    pos = [l for l in listings.values() if l.get("decision") == "outreach"]
    neg = [l for l in listings.values() if l.get("decision") == "deprioritized"]
    dim_counts: dict[str, int] = {}
    for l in neg:
        for r in (l.get("decline_reasons") or []):
            dim = REASON_DIMENSION.get(r)
            if dim:
                dim_counts[dim] = dim_counts.get(dim, 0) + 1
    return {
        "positives": pos, "negatives": neg,
        "n_positive": len(pos), "n_negative": len(neg),
        "n_total": len(pos) + len(neg),
        "dimension_counts": dim_counts,
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python scripts/tests/test_learning.py Signal -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/applib/learning.py scripts/tests/test_learning.py
git commit -m "feat(learning): signal collection + reason->dimension map"
```

---

## Task 3: Weight retune (cold-start, step cap, clamp, renormalize)

**Files:**
- Modify: `scripts/applib/learning.py`
- Test: `scripts/tests/test_learning.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
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

    def test_clamp_caps_growth_at_2x(self):
        sig = {"n_total": 50, "dimension_counts": {"value": 50}}
        out = learning.retune_weights(BASE_W, sig)
        self.assertLessEqual(out["value"], 2 * BASE_W["value"])
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python scripts/tests/test_learning.py Weights -v`
Expected: FAIL — no `retune_weights`.

- [ ] **Step 3: Implement**

Add to `learning.py`:

```python
COLD_START_MIN = 8       # no learning until this many decisions
DIM_MIN_SIGNAL = 3       # a dimension needs this many signals to move
WEIGHT_STEP = 3.0        # raw points added per qualifying dimension before renorm


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def retune_weights(base_w: dict, sig: dict) -> dict:
    if sig["n_total"] < COLD_START_MIN:
        return dict(base_w)
    qualifying = [d for d, c in sig["dimension_counts"].items()
                  if c >= DIM_MIN_SIGNAL and d in base_w]
    if not qualifying:
        return dict(base_w)
    raw = {d: float(base_w[d]) for d in base_w}
    for d in qualifying:
        raw[d] += WEIGHT_STEP
    total = sum(raw.values())
    norm = {d: v * 100.0 / total for d, v in raw.items()}
    clamped = {d: _clamp(norm[d], 0.5 * base_w[d], 2.0 * base_w[d]) for d in norm}
    total2 = sum(clamped.values())
    scaled = {d: v * 100.0 / total2 for d, v in clamped.items()}
    out = {d: int(round(v)) for d, v in scaled.items()}
    # fix rounding drift so the result sums to exactly 100
    drift = 100 - sum(out.values())
    if drift:
        biggest = max(out, key=out.get)
        out[biggest] += drift
    return out
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python scripts/tests/test_learning.py Weights -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/applib/learning.py scripts/tests/test_learning.py
git commit -m "feat(learning): bounded weight retune"
```

---

## Task 4: Hood-preference + price-threshold retune

**Files:**
- Modify: `scripts/applib/learning.py`
- Test: `scripts/tests/test_learning.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
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
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python scripts/tests/test_learning.py HoodsAndPrice -v`
Expected: FAIL — no `retune_hoods`.

- [ ] **Step 3: Implement**

Add to `learning.py`:

```python
HOOD_MIN_DECISIONS = 3
HOOD_STEP = 0.3
PRICE_MIN_SIGNAL = 3
PRICE_PERCENTILE = 0.25  # 25th percentile of declined CHF/m2 becomes the pull target


def _chf_m2(l: dict):
    rent = l.get("rent_net") or l.get("rent_gross")
    size = l.get("size_sqm")
    try:
        rent, size = float(rent), float(size)
    except (TypeError, ValueError):
        return None
    return rent / size if size > 0 else None


def retune_hoods(base_prefs: dict, positives: list, negatives: list) -> dict:
    counts: dict[str, dict] = {}
    for l in positives:
        h = l.get("hood_category")
        if h:
            counts.setdefault(h, {"pos": 0, "neg": 0})["pos"] += 1
    for l in negatives:
        if "wrong_area" in (l.get("decline_reasons") or []):
            h = l.get("hood_category")
            if h:
                counts.setdefault(h, {"pos": 0, "neg": 0})["neg"] += 1
    out = dict(base_prefs)
    for h, c in counts.items():
        n = c["pos"] + c["neg"]
        if n < HOOD_MIN_DECISIONS or h not in base_prefs:
            continue
        decline_rate = c["neg"] / n
        accept_rate = c["pos"] / n
        adj = HOOD_STEP * (accept_rate - decline_rate)
        out[h] = _clamp(base_prefs[h] + adj, 0.0, 1.0)
    return out


def _percentile(values: list, q: float) -> float:
    s = sorted(values)
    if not s:
        raise ValueError("empty")
    idx = int(q * (len(s) - 1))
    return s[idx]


def retune_price(base_best: float, base_worst: float, negatives: list) -> float:
    vals = [v for l in negatives
            if "too_expensive" in (l.get("decline_reasons") or [])
            for v in [_chf_m2(l)] if v is not None]
    if len(vals) < PRICE_MIN_SIGNAL:
        return base_worst
    target = _percentile(vals, PRICE_PERCENTILE)
    new_worst = (base_worst + target) / 2.0  # move halfway toward observed ceiling
    return _clamp(new_worst, base_best + 1.0, base_worst)
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python scripts/tests/test_learning.py HoodsAndPrice -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/applib/learning.py scripts/tests/test_learning.py
git commit -m "feat(learning): hood + price retune"
```

---

## Task 5: Orchestration — `retune()`, pause/reset, logging

**Files:**
- Modify: `scripts/applib/learning.py`
- Test: `scripts/tests/test_learning.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
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
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python scripts/tests/test_learning.py Orchestrate -v`
Expected: FAIL — no `retune`.

- [ ] **Step 3: Implement**

Add to `learning.py` (uses `config` lazily to avoid import cycle):

```python
import datetime as _dt


def _save(data: dict) -> None:
    paths.LEARNED_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.LEARNED_PREFS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(paths.LEARNED_PREFS_FILE)


def is_paused() -> bool:
    return bool(_load().get("paused"))


def set_paused(flag: bool) -> None:
    data = _load()
    data["paused"] = bool(flag)
    _save(data)


def reset() -> None:
    paths.LEARNED_PREFS_FILE.unlink(missing_ok=True)


def _baseline_scoring() -> dict:
    from . import config
    sc = dict(config.criteria().get("scoring") or {})
    # fall back to the same DEFAULTS scoring.py uses
    from .scoring import DEFAULTS
    base = dict(DEFAULTS)
    base.update(sc)
    return base


def retune(listings: dict) -> dict:
    """Recompute the overlay from all decisions. No-op while paused."""
    if is_paused():
        return _load()
    base = _baseline_scoring()
    sig = collect_signal(listings)
    weights = retune_weights(base["weights"], sig)
    hoods = retune_hoods(base["hood_preferences"], sig["positives"], sig["negatives"])
    worst = retune_price(float(base["value_best_chf_m2"]),
                         float(base["value_worst_chf_m2"]), sig["negatives"])
    overlay = {"weights": weights, "hood_preferences": hoods,
               "value_worst_chf_m2": round(worst, 1)}
    data = _load()
    data["scoring"] = overlay
    data["updated_at"] = _dt.datetime.now().replace(microsecond=0).isoformat()
    data["sample_counts"] = {"positive": sig["n_positive"], "negative": sig["n_negative"],
                             "dimensions": sig["dimension_counts"]}
    _save(data)
    _append_log(sig, overlay)
    return data


def _append_log(sig: dict, overlay: dict) -> None:
    entry = {"at": _dt.datetime.now().replace(microsecond=0).isoformat(),
             "n_total": sig["n_total"], "dimension_counts": sig["dimension_counts"],
             "weights": overlay["weights"], "value_worst_chf_m2": overlay["value_worst_chf_m2"]}
    paths.LEARNING_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(paths.LEARNING_LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def status(listings: dict) -> dict:
    base = _baseline_scoring()
    sig = collect_signal(listings)
    sc = _load().get("scoring") or {}
    return {
        "baseline": {"weights": base["weights"],
                     "hood_preferences": base["hood_preferences"],
                     "value_worst_chf_m2": base["value_worst_chf_m2"]},
        "learned": sc,
        "paused": is_paused(),
        "dimension_counts": sig["dimension_counts"],
        "n_total": sig["n_total"],
        "cold_start_remaining": max(0, COLD_START_MIN - sig["n_total"]),
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python scripts/tests/test_learning.py -v`
Expected: PASS (all classes).

- [ ] **Step 5: Commit**

```bash
git add scripts/applib/learning.py scripts/tests/test_learning.py
git commit -m "feat(learning): retune orchestration, pause/reset, logging, status"
```

---

## Task 6: API — listing list with filters

**Files:**
- Create: `scripts/serve_ui.py`
- Test: `scripts/tests/test_serve_ui.py`

Pure handler functions take a `Store` + a params dict and return JSON-able data; HTTP wiring comes in Task 11. This keeps the API testable without sockets.

- [ ] **Step 1: Write failing test**

Create `scripts/tests/test_serve_ui.py` (reuse the isolation helper from the top of this plan — copy `use_temp_data`/`write_store` in):

```python
# (paste use_temp_data + write_store here, plus:)
from applib.store import Store  # noqa: E402
import serve_ui  # noqa: E402

def FL(**kw):
    base = {"id": "x", "status": "new", "gate_status": "passed", "bucket": "A",
            "decision": None, "hood_category": "hipsters", "transit_min": 20,
            "rent_net": 2000, "size_sqm": 60, "rooms": 2.5, "city": "Zürich",
            "street": "Teststr", "zipcode": "8000", "photos": [], "title": "t",
            "url": "https://example.com"}
    base.update(kw); return base

class ListEndpoint(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({
            "a": FL(id="a", bucket="A"),
            "r": FL(id="r", gate_status="rejected"),
            "c": FL(id="c", status="closed"),
            "b": FL(id="b", bucket="B", hood_category="suits"),
        })

    def test_default_excludes_rejected_and_closed(self):
        out = serve_ui.api_listings(Store.load(), {})
        ids = {l["id"] for l in out["listings"]}
        self.assertEqual(ids, {"a", "b"})

    def test_include_rejected_param(self):
        out = serve_ui.api_listings(Store.load(), {"include_rejected": "1"})
        self.assertIn("r", {l["id"] for l in out["listings"]})

    def test_each_listing_has_score(self):
        out = serve_ui.api_listings(Store.load(), {})
        self.assertTrue(all("score" in l for l in out["listings"]))

    def test_filter_by_bucket(self):
        out = serve_ui.api_listings(Store.load(), {"bucket": "B"})
        self.assertEqual({l["id"] for l in out["listings"]}, {"b"})
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py ListEndpoint -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'serve_ui'`.

- [ ] **Step 3: Implement the listings handler**

Create `scripts/serve_ui.py`:

```python
#!/usr/bin/env python3
"""Local web app for the apartment pipeline. Reuses applib for all logic.
NEVER sends mail / submits forms — reach-out only copies text + opens a URL.

    .venv/bin/python scripts/serve_ui.py            # serve on 127.0.0.1:8765
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import config, learning, paths  # noqa: E402
from applib.scoring import score_listing  # noqa: E402
from applib.store import Store  # noqa: E402


def _scored(lst: dict, crit: dict) -> dict:
    score, parts = score_listing(lst, crit)
    out = dict(lst)
    out["score"], out["score_parts"] = score, parts
    return out


def api_listings(store: Store, params: dict) -> dict:
    crit = config.effective_criteria()
    include_rejected = params.get("include_rejected") in ("1", "true", "yes")
    rows = []
    for lst in store.listings.values():
        if lst.get("dupe_of"):
            continue
        if not include_rejected and (lst.get("gate_status") == "rejected"
                                     or lst.get("status") == "closed"):
            continue
        if params.get("bucket") and lst.get("bucket") != params["bucket"]:
            continue
        if params.get("hood") and lst.get("hood_category") != params["hood"]:
            continue
        if params.get("source") and lst.get("source") != params["source"]:
            continue
        if params.get("status") and lst.get("status") != params["status"]:
            continue
        q = (params.get("q") or "").strip().lower()
        if q and q not in json.dumps(lst, ensure_ascii=False).lower():
            continue
        rows.append(_scored(lst, crit))
    try:
        rmin = float(params.get("rent_min")) if params.get("rent_min") else None
        rmax = float(params.get("rent_max")) if params.get("rent_max") else None
        smin = float(params.get("score_min")) if params.get("score_min") else None
    except ValueError:
        rmin = rmax = smin = None
    def rent(l):
        return l.get("rent_net") or l.get("rent_gross") or 0
    if rmin is not None:
        rows = [l for l in rows if rent(l) >= rmin]
    if rmax is not None:
        rows = [l for l in rows if rent(l) <= rmax]
    if smin is not None:
        rows = [l for l in rows if l["score"] >= smin]
    rows.sort(key=lambda l: (-(l["score"]), l.get("transit_min") or 999, rent(l)))
    return {"listings": rows, "count": len(rows)}
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py ListEndpoint -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/serve_ui.py scripts/tests/test_serve_ui.py
git commit -m "feat(ui): listings API with filters + scoring"
```

---

## Task 7: API — reach-out message

**Files:**
- Modify: `scripts/serve_ui.py`
- Test: `scripts/tests/test_serve_ui.py`

- [ ] **Step 1: Write failing test**

Append to `test_serve_ui.py`:

```python
class MessageEndpoint(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({"a": FL(id="a", street="Seefeldstrasse", zipcode="8008",
                             outreach_channel="onsite_now")})

    def test_message_has_subject_body_and_channel(self):
        out = serve_ui.api_message(Store.load(), "a")
        self.assertTrue(out["subject"])
        self.assertTrue(out["body"])
        self.assertEqual(out["channel"], "onsite_now")
        self.assertEqual(out["url"], "https://example.com")

    def test_unknown_id_returns_none(self):
        self.assertIsNone(serve_ui.api_message(Store.load(), "nope"))
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py MessageEndpoint -v`
Expected: FAIL — no `api_message`.

- [ ] **Step 3: Implement (reuse existing `outreach.render`)**

Add to `serve_ui.py`:

```python
import outreach  # noqa: E402  (scripts/outreach.py)


def api_message(store: Store, listing_id: str):
    lst = store.listings.get(listing_id)
    if not lst:
        return None
    packet = outreach.render(lst)  # {id, language, subject, body, ...}
    return {
        "subject": packet["subject"],
        "body": packet["body"],
        "channel": lst.get("outreach_channel"),
        "email": lst.get("outreach_email"),
        "url": lst.get("url"),
    }
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py MessageEndpoint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/serve_ui.py scripts/tests/test_serve_ui.py
git commit -m "feat(ui): reach-out message API via outreach.render"
```

---

## Task 8: API — reach-out / decline / reset mutations (+ retune)

**Files:**
- Modify: `scripts/serve_ui.py`
- Test: `scripts/tests/test_serve_ui.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
class Mutations(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({"a": FL(id="a")})

    def test_reach_out_marks_contacted_and_decided(self):
        serve_ui.api_reach_out(Store.load(), "a")
        l = Store.load().listings["a"]
        self.assertEqual(l["decision"], "outreach")
        self.assertEqual(l["status"], "contacted")
        self.assertTrue(l.get("status_log"))

    def test_decline_records_reasons_and_note(self):
        serve_ui.api_decline(Store.load(), "a", ["too_expensive", "wrong_area"], "busy street")
        l = Store.load().listings["a"]
        self.assertEqual(l["decision"], "deprioritized")
        self.assertEqual(l["decline_reasons"], ["too_expensive", "wrong_area"])
        self.assertIn("busy street", l["decision_note"])

    def test_reset_clears_decision(self):
        serve_ui.api_reach_out(Store.load(), "a")
        serve_ui.api_reset(Store.load(), "a")
        l = Store.load().listings["a"]
        self.assertIsNone(l["decision"])
        self.assertEqual(l["status"], "new")

    def test_decline_triggers_retune_overlay_after_threshold(self):
        # 10 declines for too_expensive -> overlay learned
        write_store({f"n{i}": FL(id=f"n{i}") for i in range(10)})
        for i in range(10):
            serve_ui.api_decline(Store.load(), f"n{i}", ["too_expensive"], "")
        self.assertIn("scoring", learning._load())
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py Mutations -v`
Expected: FAIL — no `api_reach_out`.

- [ ] **Step 3: Implement**

Add to `serve_ui.py`:

```python
def _retune(store: Store) -> None:
    learning.retune(store.listings)


def api_reach_out(store: Store, listing_id: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    lst["decision"] = "outreach"
    lst["decision_at"] = paths.now_iso()
    prev = lst.get("status")
    lst["status"] = "contacted"
    lst.setdefault("status_log", []).append(
        {"at": paths.now_iso(), "from": prev, "to": "contacted", "note": "reached out via UI"})
    store.save()
    _retune(store)
    return {"ok": True}


def api_decline(store: Store, listing_id: str, reasons: list, note: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    lst["decision"] = "deprioritized"
    lst["decision_at"] = paths.now_iso()
    lst["decline_reasons"] = list(reasons or [])
    label = ", ".join(reasons or [])
    lst["decision_note"] = (f"{label}: {note}".strip(": ") if note else label) or None
    store.save()
    _retune(store)
    return {"ok": True}


def api_reset(store: Store, listing_id: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    lst["decision"] = None
    lst["decision_at"] = None
    lst["decision_note"] = None
    lst["decline_reasons"] = None
    prev = lst.get("status")
    if prev == "contacted":
        lst["status"] = "new"
        lst.setdefault("status_log", []).append(
            {"at": paths.now_iso(), "from": prev, "to": "new", "note": "undo via UI"})
    store.save()
    _retune(store)
    return {"ok": True}
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py Mutations -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/serve_ui.py scripts/tests/test_serve_ui.py
git commit -m "feat(ui): reach-out/decline/reset mutations with retune"
```

---

## Task 9: API — learning status + controls

**Files:**
- Modify: `scripts/serve_ui.py`
- Test: `scripts/tests/test_serve_ui.py`

- [ ] **Step 1: Write failing test**

Append:

```python
class LearningEndpoint(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({"a": FL(id="a")})

    def test_status_shape(self):
        out = serve_ui.api_learning(Store.load())
        self.assertIn("baseline", out)
        self.assertIn("paused", out)

    def test_pause_and_reset(self):
        serve_ui.api_learning_control("pause")
        self.assertTrue(learning.is_paused())
        serve_ui.api_learning_control("resume")
        self.assertFalse(learning.is_paused())
        serve_ui.api_learning_control("reset")
        self.assertFalse(paths.LEARNED_PREFS_FILE.exists())
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py LearningEndpoint -v`
Expected: FAIL — no `api_learning`.

- [ ] **Step 3: Implement**

Add to `serve_ui.py`:

```python
def api_learning(store: Store) -> dict:
    return learning.status(store.listings)


def api_learning_control(action: str) -> dict:
    if action == "pause":
        learning.set_paused(True)
    elif action == "resume":
        learning.set_paused(False)
    elif action == "reset":
        learning.reset()
    else:
        return {"ok": False, "error": "unknown action"}
    return {"ok": True}
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py LearningEndpoint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/serve_ui.py scripts/tests/test_serve_ui.py
git commit -m "feat(ui): learning status + control API"
```

---

## Task 10: API — photo resolution

**Files:**
- Modify: `scripts/serve_ui.py`
- Test: `scripts/tests/test_serve_ui.py`

- [ ] **Step 1: Write failing test**

Append:

```python
class Photos(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({"a": FL(id="a", photos=["https://cdn.example/x.jpg"])})

    def test_local_file_preferred(self):
        d = paths.PHOTOS_DIR / "a"; d.mkdir(parents=True)
        (d / "01.jpg").write_bytes(b"JPEGDATA")
        kind, payload = serve_ui.resolve_photo(Store.load(), "a", 0)
        self.assertEqual(kind, "file")
        self.assertTrue(str(payload).endswith("01.jpg"))

    def test_remote_url_fallback(self):
        kind, payload = serve_ui.resolve_photo(Store.load(), "a", 0)
        self.assertEqual(kind, "redirect")
        self.assertEqual(payload, "https://cdn.example/x.jpg")

    def test_missing_returns_none(self):
        kind, payload = serve_ui.resolve_photo(Store.load(), "a", 5)
        self.assertEqual(kind, "none")
```

- [ ] **Step 2: Run, verify fail**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py Photos -v`
Expected: FAIL — no `resolve_photo`.

- [ ] **Step 3: Implement**

Add to `serve_ui.py`:

```python
def resolve_photo(store: Store, listing_id: str, n: int):
    """Return (kind, payload): ('file', Path) | ('redirect', url) | ('none', None)."""
    local_dir = paths.PHOTOS_DIR / listing_id
    if local_dir.is_dir():
        files = sorted(local_dir.glob("*.jpg"))
        if 0 <= n < len(files):
            return "file", files[n]
    lst = store.listings.get(listing_id) or {}
    urls = lst.get("photos") or []
    if 0 <= n < len(urls):
        return "redirect", urls[n]
    return "none", None
```

- [ ] **Step 4: Run, verify pass**

Run: `.venv/bin/python scripts/tests/test_serve_ui.py Photos -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/serve_ui.py scripts/tests/test_serve_ui.py
git commit -m "feat(ui): photo resolution (local then remote)"
```

---

## Task 11: HTTP wiring + static serving

**Files:**
- Modify: `scripts/serve_ui.py`
- Test: manual smoke (HTTP layer is thin glue over tested handlers)

- [ ] **Step 1: Add the request handler + server at the bottom of `serve_ui.py`**

```python
import http.server
import socketserver
import urllib.parse
import webbrowser

HOST, PORT = "127.0.0.1", 8765


def _read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    if not length:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/" or u.path == "/index.html":
            return self._send_file(paths.WEB_DIR / "index.html", "text/html; charset=utf-8")
        if u.path == "/app.js":
            return self._send_file(paths.WEB_DIR / "app.js", "application/javascript")
        if u.path == "/style.css":
            return self._send_file(paths.WEB_DIR / "style.css", "text/css")
        if u.path == "/api/listings":
            return self._send_json(api_listings(Store.load(), params))
        if u.path.startswith("/api/message/"):
            out = api_message(Store.load(), u.path.split("/")[-1])
            return self._send_json(out or {}, 200 if out else 404)
        if u.path == "/api/learning":
            return self._send_json(api_learning(Store.load()))
        if u.path.startswith("/api/photo/"):
            _, _, _, lid, n = u.path.split("/")
            kind, payload = resolve_photo(Store.load(), lid, int(n))
            if kind == "file":
                return self._send_file(payload, "image/jpeg")
            if kind == "redirect":
                self.send_response(302); self.send_header("Location", payload); self.end_headers()
                return
            return self._send_json({"error": "no photo"}, 404)
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = _read_json_body(self)
        if u.path.startswith("/api/reach-out/"):
            return self._send_json(api_reach_out(Store.load(), u.path.split("/")[-1]))
        if u.path.startswith("/api/decline/"):
            return self._send_json(api_decline(Store.load(), u.path.split("/")[-1],
                                               body.get("reasons", []), body.get("note", "")))
        if u.path.startswith("/api/reset/"):
            return self._send_json(api_reset(Store.load(), u.path.split("/")[-1]))
        if u.path.startswith("/api/learning/"):
            return self._send_json(api_learning_control(u.path.split("/")[-1]))
        self._send_json({"error": "not found"}, 404)


def main() -> int:
    with socketserver.TCPServer((HOST, PORT), Handler) as httpd:
        url = f"http://{HOST}:{PORT}"
        print(f"ZRH Apartments UI → {url}  (Ctrl-C to stop)")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke test the server boots and serves the API**

Run (against your real store — read-only path):
```bash
.venv/bin/python -c "import sys; sys.argv=['x']; \
from importlib import import_module; m=import_module('scripts.serve_ui')" 2>/dev/null; \
cd "$(git rev-parse --show-toplevel)" && \
(.venv/bin/python scripts/serve_ui.py &) && sleep 1 && \
curl -s "http://127.0.0.1:8765/api/listings?bucket=A" | head -c 200 ; echo ; \
pkill -f serve_ui.py
```
Expected: a JSON blob beginning `{"listings":[...` and no traceback. (The page won't fully render until Task 12 adds `web/`.)

- [ ] **Step 3: Commit**

```bash
git add scripts/serve_ui.py
git commit -m "feat(ui): http.server wiring + static + photo serving"
```

---

## Task 12: Frontend shell — HTML + CSS (layout B)

**Files:**
- Create: `web/index.html`, `web/style.css`

- [ ] **Step 1: Create `web/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ZRH Apartments</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
  <header>
    <h1>ZRH Apartments</h1>
    <nav id="tabs">
      <button data-tab="triage" class="active">To triage</button>
      <button data-tab="contacted">Contacted</button>
      <button data-tab="declined">Declined</button>
      <button data-tab="all">All</button>
      <button data-tab="learning">Taste ✦</button>
    </nav>
  </header>
  <section id="filters">
    <input id="q" placeholder="search…">
    <select id="bucket"><option value="">all buckets</option><option>A</option><option>B</option></select>
    <input id="rent_max" type="number" placeholder="max rent">
    <input id="score_min" type="number" placeholder="min score">
    <label><input id="include_rejected" type="checkbox"> show rejected/closed</label>
  </section>
  <main id="content"><p class="muted">Loading…</p></main>
  <div id="toast" hidden></div>
  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `web/style.css`**

```css
:root { --line:#e3e3e6; --muted:#6b6b70; --blue:#2563eb; --red:#b91c1c; }
* { box-sizing:border-box; }
body { font:14px -apple-system,system-ui,sans-serif; margin:0; color:#1d1d1f; background:#f6f6f8; }
header { display:flex; align-items:center; gap:20px; padding:14px 22px; background:#fff; border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5; }
header h1 { font-size:17px; margin:0; }
nav button { border:none; background:none; font:inherit; padding:6px 10px; border-radius:8px; cursor:pointer; color:var(--muted); }
nav button.active { background:#eef; color:var(--blue); font-weight:600; }
#filters { display:flex; gap:10px; flex-wrap:wrap; align-items:center; padding:10px 22px; }
#filters input, #filters select { padding:6px 9px; border:1px solid var(--line); border-radius:8px; font:inherit; }
main { padding:18px 22px 60px; }
.section h2 { font-size:15px; margin:18px 0 10px; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:14px; }
.card { background:#fff; border:1px solid var(--line); border-radius:12px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.05); }
.thumb { height:130px; background:linear-gradient(135deg,#d7dde6,#c3cad6); position:relative; cursor:pointer; background-size:cover; background-position:center; }
.thumb .score { position:absolute; top:8px; right:8px; background:#111; color:#fff; font-weight:700; padding:3px 8px; border-radius:8px; }
.thumb .chan { position:absolute; top:8px; left:8px; color:#fff; font-size:10px; font-weight:700; padding:2px 7px; border-radius:6px; background:#ca8a04; }
.thumb .chan.email { background:#16a34a; }
.body { padding:11px 13px; }
.body h4 { margin:0 0 3px; font-size:14px; }
.meta { font-size:12px; color:var(--muted); margin:0 0 8px; line-height:1.5; }
.tags { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:9px; }
.tag { font-size:10px; font-weight:600; padding:2px 7px; border-radius:5px; background:#eef; color:#3344aa; }
.tag.warn { background:#fde8e8; color:#b02a2a; }
.acts { display:flex; gap:6px; }
.btn { flex:1; text-align:center; font-weight:600; padding:8px 0; border-radius:8px; border:1px solid var(--line); cursor:pointer; background:#fff; }
.btn.primary { background:var(--blue); color:#fff; border-color:var(--blue); }
.btn.ghost { color:var(--red); }
.btn.danger { background:var(--red); color:#fff; border-color:var(--red); }
.chips { display:flex; flex-wrap:wrap; gap:5px; margin:6px 0; }
.chip { font-size:11px; padding:4px 8px; border:1px solid var(--line); border-radius:999px; cursor:pointer; background:#fafafa; }
.chip.on { background:var(--red); color:#fff; border-color:var(--red); }
.note { width:100%; font-size:11px; padding:6px 8px; border:1px solid var(--line); border-radius:7px; margin:6px 0; }
.done { color:#15803d; font-weight:700; }
.link { color:var(--blue); cursor:pointer; text-decoration:underline; font-size:11px; }
.muted { color:var(--muted); }
#toast { position:fixed; bottom:18px; left:50%; transform:translateX(-50%); background:#111; color:#fff; padding:9px 16px; border-radius:10px; }
#learning table { border-collapse:collapse; } #learning td,#learning th { border:1px solid var(--line); padding:5px 10px; text-align:left; }
```

- [ ] **Step 3: Verify it serves**

Run: `(.venv/bin/python scripts/serve_ui.py &) && sleep 1 && curl -s http://127.0.0.1:8765/ | head -c 80; echo; pkill -f serve_ui.py`
Expected: starts with `<!DOCTYPE html>`.

- [ ] **Step 4: Commit**

```bash
git add web/index.html web/style.css
git commit -m "feat(ui): frontend shell (layout B) + styles"
```

---

## Task 13: Frontend — fetch, render cards, filters, tabs

**Files:**
- Create: `web/app.js`

- [ ] **Step 1: Create `web/app.js`**

```javascript
const $ = (s, r=document) => r.querySelector(s);
const api = (p, opts) => fetch(p, opts).then(r => r.json());
let TAB = "triage";

const BUCKET_LABEL = { A: "🔥 Bucket A", B: "⭐ Bucket B", null: "🤔 Maybe", "": "🤔 Maybe" };
const CHIPS = [["too_expensive","Too €€"],["dated","Dated K/B"],["wrong_area","Wrong area"],
  ["too_far","Too far"],["too_small","Too small"],["ugly_building","Ugly bldg"],["bad_layout","Bad layout"]];

function chf(n){ return n ? "CHF " + Number(n).toLocaleString("de-CH") : "—"; }

function tabFilter(l){
  if (TAB === "contacted") return l.status === "contacted" || l.decision === "outreach";
  if (TAB === "declined") return l.decision === "deprioritized";
  if (TAB === "triage") return !l.decision;
  return true; // all
}

function params(){
  const p = new URLSearchParams();
  for (const k of ["q","bucket","rent_max","score_min"]) { const v = $("#"+k).value; if (v) p.set(k, v); }
  if ($("#include_rejected").checked) p.set("include_rejected","1");
  return p.toString();
}

async function load(){
  if (TAB === "learning") return renderLearning();
  const data = await api("/api/listings?" + params());
  const rows = data.listings.filter(tabFilter);
  const groups = {};
  for (const l of rows){ const k = l.bucket || ""; (groups[k] ||= []).push(l); }
  const order = ["A","B",""];
  const content = $("#content");
  content.innerHTML = "";
  if (!rows.length){ content.innerHTML = '<p class="muted">Nothing here.</p>'; return; }
  for (const k of order){
    if (!groups[k]) continue;
    const sec = document.createElement("div"); sec.className = "section";
    sec.innerHTML = `<h2>${BUCKET_LABEL[k]} <span class="muted">${groups[k].length}</span></h2>`;
    const grid = document.createElement("div"); grid.className = "grid";
    groups[k].forEach(l => grid.appendChild(card(l)));
    sec.appendChild(grid); content.appendChild(sec);
  }
}

function condTags(l){
  const t = [];
  const c = (k,label) => { const v=l["condition_"+k]; if(v==="modern")t.push(`<span class="tag">modern ${label}</span>`); else if(v==="dated")t.push(`<span class="tag warn">dated ${label}</span>`); };
  c("kitchen","kitchen"); c("bath","bath");
  if (l.has_balcony) t.push('<span class="tag">balcony</span>');
  return t.join("");
}

function card(l){
  const el = document.createElement("div"); el.className = "card"; el.dataset.id = l.id;
  const chan = l.outreach_channel === "email" ? "email" : "form";
  const rent = l.rent_net || l.rent_gross;
  const street = l.street || (l.zipcode+" "+l.city);
  el.innerHTML = `
    <div class="thumb" style="background-image:url('/api/photo/${l.id}/0')">
      <span class="chan ${chan==='email'?'email':''}">${chan}</span>
      <span class="score">${l.score ?? ""}</span>
    </div>
    <div class="body">
      <h4>${street}</h4>
      <p class="meta">${l.rooms ?? "?"} rm · ${l.size_sqm ?? "?"} m² · ${chf(rent)}${l.rent_net?" net":""}<br>
        ${l.hood_name || l.hood_category || ""} ${l.transit_min?("· 🚆 "+l.transit_min+" min"):""}</p>
      <div class="tags">${condTags(l)}</div>
      <div class="state"></div>
    </div>`;
  renderState(el, l);
  el.querySelector(".thumb").onclick = () => window.open(l.url, "_blank");
  return el;
}

function renderState(el, l){ /* filled in Task 14/15 */ 
  const box = el.querySelector(".state");
  box.innerHTML = `<div class="acts">
      <div class="btn primary act-reach">Reach out</div>
      <div class="btn ghost act-decline">Decline</div></div>`;
}

function toast(msg){ const t=$("#toast"); t.textContent=msg; t.hidden=false; setTimeout(()=>t.hidden=true, 2200); }

document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach(x=>x.classList.remove("active"));
  b.classList.add("active"); TAB = b.dataset.tab; load();
});
["q","bucket","rent_max","score_min","include_rejected"].forEach(id =>
  $("#"+id).addEventListener("input", () => load()));

async function renderLearning(){ $("#content").innerHTML = '<p class="muted">Loading…</p>'; } // Task 16
load();
```

- [ ] **Step 2: Manual check — cards render**

Run the server, open `http://127.0.0.1:8765`. Expected: bucket sections with cards, thumbnails (or gradient where no photo), scores, working tabs/filters. Reach/Decline buttons present but inert until next tasks.

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat(ui): render cards, filters, tabs"
```

---

## Task 14: Frontend — reach-out interaction

**Files:**
- Modify: `web/app.js`

- [ ] **Step 1: Replace `renderState` and wire reach-out**

In `web/app.js`, replace the `renderState` stub with:

```javascript
function renderState(el, l){
  const box = el.querySelector(".state");
  if (l.decision === "outreach" || l.status === "contacted"){
    box.innerHTML = `<div class="done">✓ Reached out${l.decision_at?(" · "+l.decision_at.slice(0,10)):""}</div>
      <span class="link act-undo">undo</span>`;
    box.querySelector(".act-undo").onclick = () => reset(l, el);
    return;
  }
  if (l.decision === "deprioritized"){
    box.innerHTML = `<div class="muted">Declined — ${(l.decline_reasons||[]).join(", ")||"no reason"}</div>
      <span class="link act-undo">undo</span>`;
    box.querySelector(".act-undo").onclick = () => reset(l, el);
    return;
  }
  box.innerHTML = `<div class="acts">
      <div class="btn primary act-reach">Reach out</div>
      <div class="btn ghost act-decline">Decline</div></div>`;
  box.querySelector(".act-reach").onclick = () => reachOut(l, el);
  box.querySelector(".act-decline").onclick = () => showDecline(l, el);
}

async function reachOut(l, el){
  const msg = await api("/api/message/" + l.id);
  try { await navigator.clipboard.writeText(`${msg.subject}\n\n${msg.body}`); } catch(e){}
  if (msg.channel === "email" && msg.email){
    window.open(`https://mail.google.com/mail/?view=cm&to=${encodeURIComponent(msg.email)}`
      + `&su=${encodeURIComponent(msg.subject)}&body=${encodeURIComponent(msg.body)}`, "_blank");
  } else {
    window.open(l.url, "_blank");
  }
  await api("/api/reach-out/" + l.id, {method:"POST"});
  toast("Message copied · marked reached out");
  l.decision = "outreach"; l.status = "contacted"; l.decision_at = new Date().toISOString();
  renderState(el, l);
}

async function reset(l, el){
  await api("/api/reset/" + l.id, {method:"POST"});
  l.decision = null; l.status = "new"; l.decline_reasons = null;
  renderState(el, l); toast("Undone");
}
```

- [ ] **Step 2: Manual check**

Click **Reach out** on a card. Expected: a tab/window opens (form URL or Gmail compose), clipboard holds the message, card flips to "✓ Reached out" with undo, and re-loading keeps that state (persisted to `listings.json`). Click **undo** → returns to buttons.

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat(ui): reach-out interaction (copy, open, mark, undo)"
```

---

## Task 15: Frontend — decline interaction

**Files:**
- Modify: `web/app.js`

- [ ] **Step 1: Add the decline UI**

Append to `web/app.js`:

```javascript
function showDecline(l, el){
  const box = el.querySelector(".state");
  const chips = CHIPS.map(([k,label]) => `<span class="chip" data-k="${k}">${label}</span>`).join("");
  box.innerHTML = `<p class="meta">Why are you passing?</p>
    <div class="chips">${chips}</div>
    <input class="note" placeholder="optional note…">
    <div class="acts"><div class="btn ghost act-cancel" style="flex:0 0 38%">Cancel</div>
      <div class="btn danger act-confirm">Confirm decline</div></div>`;
  const picked = new Set();
  box.querySelectorAll(".chip").forEach(c => c.onclick = () => {
    c.classList.toggle("on"); picked.has(c.dataset.k) ? picked.delete(c.dataset.k) : picked.add(c.dataset.k);
  });
  box.querySelector(".act-cancel").onclick = () => renderState(el, l);
  box.querySelector(".act-confirm").onclick = async () => {
    const note = box.querySelector(".note").value;
    await api("/api/decline/" + l.id, {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({reasons:[...picked], note})});
    l.decision = "deprioritized"; l.decline_reasons = [...picked];
    renderState(el, l); toast("Declined — taste updated");
  };
}
```

- [ ] **Step 2: Manual check**

Click **Decline** → chips + note appear. Pick reasons, Confirm. Expected: card flips to "Declined — …", persisted; **Cancel** restores buttons; after enough declines the Taste tab shows learned changes (Task 16).

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat(ui): decline interaction with reason chips"
```

---

## Task 16: Frontend — learning panel

**Files:**
- Modify: `web/app.js`

- [ ] **Step 1: Replace `renderLearning`**

```javascript
async function renderLearning(){
  const s = await api("/api/learning");
  const w = (o)=>o?Object.entries(o).map(([k,v])=>`${k}: ${v}`).join(" · "):"—";
  const rows = Object.keys(s.baseline.weights).map(k =>
    `<tr><td>${k}</td><td>${s.baseline.weights[k]}</td>
       <td>${s.learned.weights ? s.learned.weights[k] : s.baseline.weights[k]}</td></tr>`).join("");
  const counts = Object.entries(s.dimension_counts||{}).map(([k,v])=>`${k}: ${v}`).join(" · ") || "none yet";
  $("#content").innerHTML = `
    <div id="learning">
      <h2>Taste / Learning ${s.paused?'<span class="tag warn">paused</span>':''}</h2>
      <p class="muted">${s.cold_start_remaining>0
        ? `Cold start: ${s.cold_start_remaining} more decision(s) before learning kicks in.`
        : 'Learning active. Scores below reflect your decisions.'}</p>
      <p>Decline signals → ${counts}</p>
      <h3>Weights (baseline → learned)</h3>
      <table><tr><th>dimension</th><th>baseline</th><th>learned</th></tr>${rows}</table>
      <h3>Hood preferences (learned)</h3>
      <p class="muted">${w(s.learned.hood_preferences)}</p>
      <p>Price ceiling (value_worst_chf_m2): baseline ${s.baseline.value_worst_chf_m2}
         → learned ${s.learned.value_worst_chf_m2 ?? s.baseline.value_worst_chf_m2}</p>
      <div class="acts" style="max-width:360px;margin-top:14px">
        <div class="btn act-pause">${s.paused?'Resume':'Pause'} learning</div>
        <div class="btn danger act-reset">Reset learning</div>
      </div>
    </div>`;
  $(".act-pause").onclick = async () => { await api("/api/learning/"+(s.paused?"resume":"pause"),{method:"POST"}); renderLearning(); };
  $(".act-reset").onclick = async () => { if(confirm("Reset all learned preferences?")){ await api("/api/learning/reset",{method:"POST"}); renderLearning(); } };
}
```

- [ ] **Step 2: Manual check**

Open the **Taste ✦** tab. Expected: weights table (baseline vs learned), decline-signal tally, hood/price learned values, working Pause/Reset. Before 8 decisions it shows the cold-start message and learned == baseline.

- [ ] **Step 3: Commit**

```bash
git add web/app.js
git commit -m "feat(ui): learning/taste panel with controls"
```

---

## Task 17: Wire learned criteria into the morning pipeline

**Files:**
- Modify: `scripts/bucket.py:50`, `scripts/digest.py:127`

- [ ] **Step 1: Switch scoring to effective criteria**

In `scripts/bucket.py`, change line ~50 from `crit = config.criteria()` to:

```python
crit = config.effective_criteria()
```

In `scripts/digest.py`, change line ~127 from `crit = config.criteria()` to:

```python
crit = config.effective_criteria()
```

- [ ] **Step 2: Verify the morning scorer still runs**

Run: `.venv/bin/python scripts/bucket.py --help 2>&1 | head -3` (must not error on import) and
`.venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); from applib import config; print(sum(config.effective_criteria()['scoring']['weights'].values()))"`
Expected: prints `100` (no overlay yet → baseline) and no traceback.

- [ ] **Step 3: Commit**

```bash
git add scripts/bucket.py scripts/digest.py
git commit -m "feat(learning): morning pipeline scores with learned criteria"
```

---

## Task 18: Launcher `.command`

**Files:**
- Create: `ZRH Apartments.command`

- [ ] **Step 1: Create the launcher**

```bash
#!/bin/bash
# Double-clickable launcher for the ZRH Apartments desktop UI.
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python scripts/serve_ui.py
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x "ZRH Apartments.command"`

- [ ] **Step 3: Verify**

Double-click in Finder (or run `"./ZRH Apartments.command"`). Expected: terminal prints the URL, browser opens to the populated app. Ctrl-C stops it.

- [ ] **Step 4: Commit**

```bash
git add "ZRH Apartments.command"
git commit -m "feat(ui): double-click .command launcher"
```

---

## Task 19: Review + end-to-end verification

**Files:** none (verification + review)

- [ ] **Step 1: Run the full test suite**

Run:
```bash
.venv/bin/python scripts/tests/test_learning.py
.venv/bin/python scripts/tests/test_serve_ui.py
.venv/bin/python scripts/tests/test_scoring.py
```
Expected: all OK.

- [ ] **Step 2: Back up the store, then exercise a real decision**

```bash
cp data/listings.json "data/listings.backup-$(date +%Y%m%d-%H%M%S).json"
```
Start the app, decline one listing with a reason, reach out on another, then confirm both persisted and the morning scorer still runs:
```bash
.venv/bin/python scripts/bucket.py 2>&1 | tail -5
```
Expected: no errors; declined/contacted listings retain their decision.

- [ ] **Step 3: Code-review agent on the backend**

Dispatch a `feature-dev:code-reviewer` (or `code-review` skill) over `scripts/serve_ui.py` and `scripts/applib/learning.py`. Focus: atomic-write safety, the no-send guarantee (reach-out must never POST to a portal or send mail), learning bound/clamp correctness, path-traversal safety on `/api/photo`. Address high-confidence findings.

- [ ] **Step 4: Frontend/UX review**

Dispatch a review (general `claude` agent) of `web/*` against the spec: layout B fidelity, card states, reach-out/decline/undo flows, learning panel. Address findings.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore(ui): review fixes + verification"
```

---

## Self-Review Notes (author)

- **Spec coverage:** layout B (T12/13), thumbnails+local-cache fallback (T10/13), reach-out copy+open+mark+undo (T8/14), decline chips+note (T8/15), scope default-hide rejected/closed (T6), filters/tabs (T13), auto-retune weights+hoods+price with cold-start/clamps/log (T3–5), system-wide effective criteria (T1/17), pause/reset + transparency panel (T9/16), launcher (T18), no-send & atomic guarantees (T8 tests + T19 review), tests (T1–10), review agents (T19). All spec sections map to a task.
- **No placeholders:** every code step shows full code; the one `renderState` stub in T13 is explicitly replaced in T14.
- **Type/name consistency:** handler names (`api_listings/api_message/api_reach_out/api_decline/api_reset/api_learning/api_learning_control/resolve_photo`), learning names (`scoring_overlay/collect_signal/retune_weights/retune_hoods/retune_price/retune/status/set_paused/is_paused/reset/_load`), reason keys (`too_expensive/too_small/dated/ugly_building/bad_layout/wrong_area/too_far`) are consistent across backend tests, frontend chips, and the reason→dimension map.
