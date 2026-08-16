# Workflow 4 — Bucket & digest

**Goal:** sort survivors into A / B and write the morning digest the user reads.

## Run
```
.venv/bin/python scripts/bucket.py
.venv/bin/python scripts/digest.py
```

## Bucketing (`bucket.py`)
Operates on `gate_status = passed` listings:
- **Bucket A** — all nice-to-haves present (parking AND balcony) AND neither
  kitchen nor bath is `dated`. (`condition_unknown` is fine — not penalised.)
- **Bucket B** — missing a nice-to-have OR a `dated` room. The specific gap is
  stored in `bucket_gap` (e.g. "no balcony; dated bath").
- If `condition.reject_on_dated: true`, a `dated` room rejects instead of demoting.

## Digest (`digest.py`)
Writes `data/digests/YYYY-MM-DD.md` with sections:
- **Bucket A — strong match**
- **Bucket B — worth a look**
- **Manual-check** — `transit_unknown` / unverifiable must-have
- **Changed since last seen** — price/availability moved on a known listing

Rules baked into the script:
- Shows **new listings only**, plus any that **changed** since last seen.
- One line of substance per listing: `CHF · m² · Zi · area · commute min ·
  parking/balcony · kitchen/bath condition · link`.
- Sorted by **commute, then rent**.
- Ends with the one command to start outreach (`outreach <id>`).
- Also writes `data/.last_summary.txt` (the launchd notification text).

## After this step
The morning run is done. The user reads the digest (it auto-opens) and replies
`outreach <id>` for any listing they want to contact → `workflows/5-outreach.md`.
Do not draft or send anything automatically.
