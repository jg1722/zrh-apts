# Listing score — design (2026-06-11)

Approved in chat 2026-06-11. Adds a 0–100 score to every displayed listing so
the digest reads as a ranked overview. Bucket membership (A/B/manual) is
unchanged — gates and gaps still decide it; the score orders within sections.

## Components (each 0–1)

| Component   | Weight | Computation |
|-------------|--------|-------------|
| commute     | 40 | 1.0 at ≤15 min → 0.0 at ≥35 min, linear; transit unknown → unknown_value |
| condition   | 30 | mean of 4 subparts: parking (1/0), balcony (1/0), kitchen, bath (modern 1.0 / acceptable 0.6 / dated 0.2 / unknown → unknown_value) |
| hood        | 15 | preference map: hipsters 1.0 · rich 0.8 · normies 0.6 · suits 0.3 · tourists 0.3; outside map → unknown_value |
| value       | 15 | CHF/m² linear from 25 (=1.0) to 50 (=0.0), clamped; missing size → unknown_value |

`unknown_value = 0.4` — a deliberate slight penalty below the 0.5 midpoint so
verified-good flats outrank unverified ones (user decision).

Total = round(Σ weight·component). Weights sum to 100.

## Where things live

- **Config:** `scoring:` section in `config/criteria.yaml` — weights, commute
  ramp, hood preference map, value bounds, unknown_value. Tuning = YAML edit.
- **Logic:** `scripts/applib/scoring.py`, pure function
  `score_listing(lst, crit) -> tuple[int, dict]` (score, per-component parts).
- **Wiring:** `bucket.py` stores `score` / `score_parts` on displayed listings;
  `digest.py` sorts each section by score desc (ties: commute asc, rent asc)
  and renders `**[87]** <id> · …`.
- **Tests:** `scripts/tests/test_scoring.py` (stdlib unittest, run directly).

## Out of scope

Auto-tuning weights, cross-day score history, score-based bucket assignment.
