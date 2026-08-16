# ZRH Apartments — desktop UI (design)

**Date:** 2026-06-15
**Status:** approved for implementation
**Author:** original author + Claude (brainstorm)

## Goal

A local desktop app that shows the apartment pipeline visually (buckets,
thumbnails), lets the user reach out with a pre-formulated message in one click,
decline with a reason, and **auto-retunes the fit score** from those decisions.
It is a richer window onto the *same* `data/listings.json` the morning job uses —
the morning digest keeps working unchanged.

## Decisions locked during brainstorm

| Topic | Decision |
|---|---|
| Layout | **Stacked sections** (digest-style), buckets down the page, roomy cards |
| Reach out | **Copy message + open URL**, mark `contacted` immediately, with **undo** |
| Scope | **Everything, with filters**; gate-rejected + closed **hidden by default** |
| Decline | **Reason chips + optional note** (structured taste signal) |
| Tech | **Local Python web app** + double-click `.command` launcher |
| Learning | **Auto-retune scoring** (bounded, reversible) — applies system-wide |

## Non-negotiable constraints

- **Never sends anything.** Reach-out only copies text and opens a URL. Honors
  `outreach.auto_send: false`.
- **Never corrupts the store.** All writes go through the existing atomic
  `Store.save()` (temp-file + `replace`).
- **Never overwrites `criteria.yaml`.** Learned adjustments live in a separate
  overlay file.
- **localhost only.** Server binds to 127.0.0.1.
- **Reuse, don't reinvent.** Use existing `outreach.render`, `scoring`,
  `decide`/`set_status` logic; no duplicated business rules.

## Architecture

### Backend — thin Python web server (stdlib only, no new deps)
Imports existing modules from `scripts/applib`. Serves a JSON API + the static
frontend. New file: `scripts/serve_ui.py` (+ small helpers in `applib`).

Endpoints:
- `GET  /api/listings` — filtered/sorted listings. Loads `Store`, computes score
  via `score_listing(lst, effective_criteria)`. Query params for filters
  (bucket, status, hood, rent_min/max, score_min, source, q, include_rejected,
  tab). Default excludes `gate_status == "rejected"` and `status == "closed"`.
- `GET  /api/listing/<id>` — full detail for one listing.
- `GET  /api/message/<id>` — `{subject, body, channel, url, email}` via existing
  `outreach.render(lst)`.
- `POST /api/reach-out/<id>` — sets `decision="outreach"`, `status="contacted"`
  (reusing decide/set_status logic + status_log), then triggers retune.
- `POST /api/decline/<id>` — body `{reasons:[...], note:""}`; sets
  `decision="deprioritized"`, `decision_note` = structured reasons + note, stores
  `decline_reasons` list on the listing; then triggers retune.
- `POST /api/reset/<id>` — clears decision/status back (undo).
- `GET  /api/learning` — current overlay vs baseline deltas, reason tally,
  paused flag, log tail.
- `POST /api/learning/{pause|resume|reset}` — control learning.
- `GET  /api/photo/<id>/<n>` — serves local cached photo
  `data/photos/<id>/NN.jpg`; falls back to redirect to the remote photo URL;
  else a placeholder.

### Frontend — single `index.html` + vanilla JS/CSS (no build step)
- Stacked sections per bucket (A / B / Maybe), sorted by score.
- Filter bar: bucket, status, hood, rent slider, score, source, search;
  toggle to reveal gate-rejected/closed.
- Tabs: **To-triage** (default) · Contacted · Declined · All.
- Card: thumbnail (photo nav, score badge, channel tag), street title,
  rooms/m²/net rent/hood/commute/availability, condition tags (from vision
  verdicts), **Reach out** / **Decline**.
- Decline → inline reason chips (Too €€ · Dated K/B · Wrong area · Too far ·
  Too small · Ugly bldg · Bad layout) + optional note → Confirm.
- Reach out → clipboard copy (localhost is a secure context) + `window.open`
  the form URL (or Gmail compose for email-channel) + POST → card flips to
  "Reached out · <date>" with **undo**.
- **Taste / Learning panel**: baseline→learned deltas, reason tally, Pause /
  Reset.

### Files added/changed
- `scripts/serve_ui.py` — the server (route table, JSON helpers).
- `web/index.html`, `web/app.js`, `web/style.css` — the frontend.
- `scripts/applib/learning.py` — overlay load/merge + retune algorithm.
- `scripts/applib/scoring.py` — unchanged signature; callers pass effective
  criteria via new `applib.config` helper `load_effective_criteria()`.
- `data/.learned_prefs.json`, `data/.learning_log.jsonl` — learned state (new,
  gitignored).
- `ZRH Apartments.command` — launcher (starts venv server, opens browser).
- Morning digest (`scripts/digest.py` / wherever scoring is invoked) switched to
  `load_effective_criteria()` so it reflects learned taste too.

## Auto-retune (the learning layer)

### Storage
`data/.learned_prefs.json` holds a `scoring:` overlay (adjusted `weights`,
`hood_preferences`, `value_best_chf_m2`, `value_worst_chf_m2`). At scoring time
`load_effective_criteria()` deep-merges it over `criteria.yaml` (deep-merge is
required because `score_listing` shallow-merges nested dicts). `criteria.yaml`
stays pristine as the baseline.

### Signal
- **Positive:** listings with `decision == "outreach"` (and/or `status >=
  contacted`).
- **Negative:** listings with `decision == "deprioritized"`, attributed by their
  `decline_reasons`.

### Reason → dimension map
| Chip | Dimension acted on |
|---|---|
| Too €€, Too small | `value` (raise weight; tighten price thresholds) |
| Dated K/B, Ugly bldg, Bad layout | `condition` (raise weight) |
| Wrong area | `hood` (lower that hood's preference; raise hood weight) |
| Too far | `commute` (raise weight) |

### Algorithm (bounded, deterministic)
1. **Cold-start guard:** make no change until ≥ 8 total decisions, and adjust a
   given dimension only once it has ≥ 3 supporting signals. Until then the
   overlay equals the baseline.
2. **Weights:** compute each dimension's share of decline signal; shift weights
   toward high-signal dimensions and away from others; renormalize to sum 100.
   Clamp each weight to `[0.5×, 2×]` its baseline and cap movement at ±3 points
   per retune (anti-oscillation).
3. **Hood preferences:** per hood, nudge preference down by a bounded factor of
   its decline rate (floor 0.0); reach-outs nudge it up (ceil 1.0).
4. **Price thresholds:** from "Too €€" declines, estimate an effective CHF/m²
   ceiling (a percentile of declined values) and pull `value_worst_chf_m2`
   toward it within bounds.
5. **Log:** append a `data/.learning_log.jsonl` entry — timestamp, trigger,
   sample counts, before/after values.

### Trigger
After each reach-out/decline (debounced) and on app start. Skipped entirely when
learning is paused.

### Control & transparency
- Learning panel shows baseline vs learned for every knob and the change log.
- **Pause learning** (overlay frozen, still readable).
- **Reset learning** (delete overlay → back to `criteria.yaml`).

## Testing

- `scripts/tests/test_learning.py` — cold-start guard; reason→dimension mapping;
  weight renormalization + clamps + step cap; hood/price nudging bounds; overlay
  deep-merge; reset/pause.
- `scripts/tests/test_serve_ui.py` — endpoint contracts against a temp store
  copy: filtering/default exclusions, reach-out/decline/reset mutate correctly
  and atomically, message rendering, photo fallback, **no-send guarantee**.
- Manual verification: launch against the real (backed-up) `listings.json`,
  read-only sanity first, then exercise one decision and confirm `Store.save`
  round-trips and the morning digest still runs.

## Build sequence

1. `applib/learning.py` + `load_effective_criteria()` (+ tests) — pure logic, no UI.
2. `scripts/serve_ui.py` API over a temp-store harness (+ tests).
3. Frontend `web/*` (layout B, cards, filters, tabs).
4. Reach-out + decline + undo wiring.
5. Learning panel + controls.
6. `.command` launcher; switch digest to effective criteria.
7. Review passes: **code-reviewer agent** on backend/learning (atomicity,
   no-send, bounds), **frontend/UX review** on the UI vs this spec.
8. Verify end-to-end against real data.

## Out of scope (v1)

- Sending email/submitting forms automatically (forbidden by hard rule).
- Native SwiftUI / Electron packaging (a `.app` wrapper is a possible fast-follow).
- Editing listing data by hand in the UI.
