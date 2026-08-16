# Workflow 2 — Must-have filter + Transit gate (the knockouts)

**Goal:** drop everything that fails a hard requirement, cheaply, before any
vision work. This is where most listings die.

## Run
```
.venv/bin/python scripts/gate.py
```

## What it does (per active listing)
1. **Nice-to-haves** (parking, balcony) are detected from amenities + title +
   blurb using the German synonyms in `config/criteria.yaml`. These never reject;
   they only affect bucketing later.
2. **Must-haves** from `config/criteria.yaml`:
   - rent (by `rent.basis`, default `net`) within `min`–`max`;
   - size ≥ `size.min_sqm`;
   - rooms ≥ `rooms.min`;
   - hoodmaps category NOT in `hood.exclude_categories` (default `[crime]`;
     listings outside the ZH map have no category and are not rejected).
   - A value **out of range and known** → hard reject (`gate_status = rejected`,
     logged, not shown).
   - A value **unknown** → routed to manual-check (never guessed).
3. **Transit knockout** (only if no hard fail): calls `scripts/transit_check.py`
   → `transport.opendata.ch`, picks the fastest connection arriving ≤ `arrive_by`
   on the next weekday. Raw door-to-door minutes are cached; the `max_minutes`
   threshold is applied each run, so changing the cap re-buckets without new
   API calls. PLZ-only (street-less) addresses are flagged as centroid-based.
   - ≤ `max_minutes` → `transit_status = ok`, stores `transit_min` + route;
   - > `max_minutes` → `gate_status = rejected` (transit);
   - unresolved / API error → `transit_status = transit_unknown` → manual-check.

## Outcomes
- `passed` — all must-haves verified + transit ok → goes to vision scoring.
- `manual` — no hard fail, but something is unknown (`transit_unknown`,
  `rent_unknown`, …) → surfaced in the digest's Manual-check section.
- `rejected` — a hard fail or transit over the cap → logged, not shown.

## Spot-check transit resolution (do this on the first few runs)
```
.venv/bin/python scripts/transit_check.py "Birmensdorferstrasse 100, 8003 Zürich"
.venv/bin/python scripts/transit_check.py "Hardturmstrasse 1, 8005 Zürich" --json
```
Confirm the resolved stops and minutes look sane. If addresses fail to resolve,
they correctly become `transit_unknown` — never paper over it with a guess.
