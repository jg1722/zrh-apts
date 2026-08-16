"""Learned-preferences overlay + auto-retune of the fit score.

The overlay lives in data/.learned_prefs.json and is merged on top of
criteria.yaml by config.effective_criteria(). criteria.yaml is never written.
"""
from __future__ import annotations
import datetime as _dt
import json
from . import paths
from .text import norm


def _load() -> dict:
    if paths.LEARNED_PREFS_FILE.exists():
        return json.loads(paths.LEARNED_PREFS_FILE.read_text(encoding="utf-8"))
    return {}


def scoring_overlay() -> dict:
    """The {'scoring': {...}} fragment to merge, or {} when nothing learned."""
    data = _load()
    sc = data.get("scoring")
    return {"scoring": sc} if sc else {}


REASON_DIMENSION = {
    "too_expensive": "value",
    "too_small": "value",
    "dated": "condition",
    "ugly_building": "condition",
    "bad_layout": "condition",
    "wrong_area": "hood",
    "too_far": "commute",
}

# Free-text decline notes also feed learning: scan the note for these keywords
# (accent-stripped, lowercased) and attribute to a scoring dimension. Counted
# per-listing (a set), so a note doesn't double-count with its own chips.
NOTE_KEYWORDS = {
    "value": ["expensive", "pricey", "overpriced", "teuer", "ueberteuert", "price",
              "too small", "small", "tiny", "cramped", "klein", "winzig", "eng"],
    "condition": ["dated", "outdated", "old", "alt", "veraltet", "renovation",
                  "renovier", "sanierung", "ugly", "haesslich", "run down",
                  "rundown", "worn", "abgenutzt", "layout", "grundriss", "dark",
                  "dunkel", "shabby"],
    "hood": ["area", "neighbourhood", "neighborhood", "location", "lage", "gegend",
             "quartier", "noisy", "loud", "laut", "busy", "traffic", "verkehr",
             "main road", "hauptstrasse", "sketchy"],
    "commute": ["far", "weit", "commute", "distance", "entfernt", "abgelegen"],
}


def _note_dimensions(note: str | None) -> set[str]:
    h = norm(note)
    if not h:
        return set()
    return {dim for dim, kws in NOTE_KEYWORDS.items() if any(k in h for k in kws)}


def collect_signal(listings: dict) -> dict:
    pos = [l for l in listings.values() if l.get("decision") == "outreach"]
    neg = [l for l in listings.values() if l.get("decision") == "deprioritized"]
    dim_counts: dict[str, int] = {}
    for l in neg:
        # one vote per dimension per listing, from chips AND free-text note
        dims: set[str] = set()
        for r in (l.get("decline_reasons") or []):
            d = REASON_DIMENSION.get(r)
            if d:
                dims.add(d)
        dims |= _note_dimensions(l.get("decision_note"))
        for d in dims:
            dim_counts[d] = dim_counts.get(d, 0) + 1
    return {
        "positives": pos, "negatives": neg,
        "n_positive": len(pos), "n_negative": len(neg),
        "n_total": len(pos) + len(neg),
        "dimension_counts": dim_counts,
    }


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


# ---------------------------------------------------------------------------
# Orchestration: save, pause/reset controls, retune, status
# ---------------------------------------------------------------------------

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
    from .scoring import DEFAULTS
    base = dict(DEFAULTS)
    base.update(sc)
    return base


def retune(listings: dict) -> dict:
    """Recompute the overlay from all decisions. No-op while paused."""
    if is_paused():
        return _load()
    sig = collect_signal(listings)
    if sig["n_total"] < COLD_START_MIN:
        # cold start — nothing learned yet; ensure no stale overlay is applied
        data = _load()
        if "scoring" in data:
            data.pop("scoring", None)
            _save(data)
        return data
    base = _baseline_scoring()
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
