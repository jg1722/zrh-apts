"""Listing score — a 0–100 rank for displayed listings (design: docs/2026-06-11-scoring-design.md).

Pure function of (listing, criteria); no I/O. Bucket membership is decided
elsewhere — the score only orders within sections and quantifies fit. All
tuning knobs live in criteria.yaml under `scoring:`. Unknown data scores
`unknown_value` (slightly below the 0.5 midpoint by design): verified-good
flats outrank unverified ones, and a flat is never *rewarded* for missing data.
"""
from __future__ import annotations

DEFAULTS = {
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

_UNKNOWN_CONDITIONS = (None, "", "condition_unknown", "unknown")


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _commute(lst: dict, cfg: dict) -> float:
    t = _num(lst.get("transit_min"))
    if t is None:
        return cfg["unknown_value"]
    full, zero = float(cfg["commute_full_minutes"]), float(cfg["commute_zero_minutes"])
    if t <= full:
        return 1.0
    if t >= zero:
        return 0.0
    return (zero - t) / (zero - full)


def _condition(lst: dict, cfg: dict) -> float:
    unknown = cfg["unknown_value"]
    grades = cfg["condition_grades"]

    def boolean(v) -> float:
        return 1.0 if v is True else (0.0 if v is False else unknown)

    def room(v) -> float:
        if v in _UNKNOWN_CONDITIONS:
            return unknown
        return float(grades.get(v, unknown))

    parts = [boolean(lst.get("has_parking")), boolean(lst.get("has_balcony")),
             room(lst.get("condition_kitchen")), room(lst.get("condition_bath"))]
    return sum(parts) / len(parts)


def _hood(lst: dict, cfg: dict) -> float:
    cat = lst.get("hood_category")
    prefs = cfg["hood_preferences"]
    if cat in prefs:
        return float(prefs[cat])
    return cfg["unknown_value"]  # outside the hoodmaps area, or a new category


def _value(lst: dict, cfg: dict) -> float:
    rent = _num(lst.get("rent_net")) or _num(lst.get("rent_gross"))
    size = _num(lst.get("size_sqm"))
    if not rent or not size or size <= 0:
        return cfg["unknown_value"]
    chf_m2 = rent / size
    best, worst = float(cfg["value_best_chf_m2"]), float(cfg["value_worst_chf_m2"])
    if chf_m2 <= best:
        return 1.0
    if chf_m2 >= worst:
        return 0.0
    return (worst - chf_m2) / (worst - best)


def score_listing(lst: dict, crit: dict) -> tuple[int, dict]:
    """Return (score 0–100, per-component parts each 0–1)."""
    cfg = {**DEFAULTS, **((crit or {}).get("scoring") or {})}
    parts = {
        "commute": _commute(lst, cfg),
        "condition": _condition(lst, cfg),
        "hood": _hood(lst, cfg),
        "value": _value(lst, cfg),
    }
    weights = {**DEFAULTS["weights"], **(cfg.get("weights") or {})}
    total = round(sum(float(weights[k]) * parts[k] for k in parts))
    return total, {k: round(v, 3) for k, v in parts.items()}
