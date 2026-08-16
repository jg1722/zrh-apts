# Reply draft assistant: check-now, auto-draft, edit-learning

**Date:** 2026-06-23
**Status:** Approved (design), pending implementation plan

## Problem

The Gmail reply matcher only runs in the morning, so a reply that arrives mid-day
(e.g. the Eulenweg agency asking for a viewing) sits unregistered until tomorrow.
When a genuine personal reply does land, the user has to hand-write a response each
time. And the manual draft (like the one written for Eulenweg) doesn't get better
over time — nothing learns from how the user edits drafts before sending.

## Goal

1. A **"Check now"** button to run the reply pipeline on demand.
2. **Auto-draft** a ready, custom reply into Gmail Drafts whenever a personal reply
   is detected (never sent — the user reviews and sends).
3. **Edit-learning**: diff what the user actually sent against our draft, accumulate
   the lessons, and feed them into future drafts so they keep improving.

## Decisions (from brainstorming)

- **Draft availability** comes from the existing `outreach.timing` config
  (mid-to-late July); no new config field. Drift (e.g. travel dates) is picked up
  by the edit-learning from the user's edits.
- **Draft on detection** of personal replies (`automated == false`). Semi-automated
  replies whose next step is a phone call / portal get no email draft.
- **Edit-learning is automatic and viewable**, mirroring the Taste tab
  (pause / resume / reset); no per-change approval.
- Build all three in one plan, via subagent-driven development with review. Keep it
  lean — no speculative generality.

## Architecture

Extends the existing reply pipeline (`reply_context.py` → `claude -p` matcher →
`apply_replies.py`). The same prep → `claude -p` → apply shape is reused for the
two new steps. Python owns the store; each `claude -p` writes only a scratch file.

```
check_replies.py (orchestrator — used by BOTH the morning run and the button)
  step 1  reply_context.py  → claude -p matcher        → apply_replies.py
  step 2  draft_replies.py --prep → claude -p drafter   → draft_replies.py --apply
  step 3  draft_learn.py   --prep → claude -p learner   → draft_learn.py --apply
```

Each step **skips its `claude -p` call when its prep finds no jobs**, so a typical
run invokes only the matcher; drafting runs only when a new personal reply exists;
learning runs only when the user has sent an edited draft.

### Component A — "Check now" button

- `serve_ui.py` gains `POST /api/check-replies` (start) and
  `GET /api/check-replies/status` (poll). Start runs `check_replies.run()` in a
  **background thread**, guarded by a `threading.Lock` so only one run happens at a
  time; it returns immediately. Status returns `{running, started_at, finished_at,
  summary, error}`.
- `run_morning.sh`'s inline reply block is **replaced by a single call** to
  `check_replies.py` (DRY — the button and the cron run the identical pipeline).
- UI: a header **"⟳ Check now"** button → POST start → shows "Checking Gmail…" →
  polls status every 3 s → on `running:false`, reloads listings + toasts the summary.

### Component B — auto-draft on personal reply

- **`scripts/draft_replies.py`**
  - `build_jobs(store) -> list[dict]`: reply candidates/confirmed replies with
    `automated == false` and no `draft` yet. Each job carries the listing
    (street, rooms, size, url), the incoming reply (`from`, `subject`, `snippet`),
    `thread_id`, the `outreach.applicant`/`timing` config, the detected `language`,
    and the current learned style notes. `--prep` writes the list to
    `data/.draft_jobs.json`.
  - `apply_drafts(store, results) -> dict`: `--apply` reads
    `data/.draft_results.json` (`{id: {draft_id, text}}`) and records
    `draft = {draft_id, text, created_at, learned_from: false}` on each reply.
    Idempotent (skips a reply that already has a `draft`).
- **`claude -p` drafter**: reads `data/.draft_jobs.json`; for each job reads the
  thread (`get_thread`) to find the latest agency message, composes a German
  "Sie"-form reply (interest + viewing availability from `timing` + dossier offer,
  applying the learned style notes), has an **independent reviewer subagent** verify
  it (correct address, no invented facts, availability present, appropriate tone),
  then `create_draft(replyToMessageId=<latest>, to=<reply.from>, subject="Re: …")`.
  Writes `{id: {draft_id, text}}` to `data/.draft_results.json`. **Never sends,
  labels, stars, or otherwise modifies mail beyond creating the draft.**
- **UI**: reply cards (candidate and confirmed) show **"✎ Draft ready in Gmail"**
  linking to the thread when `draft` is present.

### Component C — edit-learning

- **`scripts/draft_learn.py`**
  - `build_jobs(store) -> list[dict]`: replies whose `draft` exists with
    `learned_from == false`. Each job carries `id`, `thread_id`, and the draft
    `text`. `--prep` writes `data/.learn_jobs.json`. (Honors a paused flag — see
    store below.)
  - `apply_learnings(store, results) -> dict`: `--apply` reads
    `data/.learn_results.json` (`{id: {learned: bool, lessons: [str]}}`); appends
    new lessons to `data/.draft_style.json` and sets the reply's
    `draft.learned_from = true`.
- **`claude -p` learner**: reads `data/.learn_jobs.json`; for each, reads the thread
  (`get_thread`), finds the user's sent message after our draft, diffs it against our
  draft text, and extracts up to 3 concise lessons (or none if unchanged / not
  sent yet). Read-only on Gmail. Writes `data/.learn_results.json`.
- **`data/.draft_style.json`** (gitignored, mirrors `.learned_prefs.json`):
  `{"notes": [{"text", "added_at", "from"}], "paused": false}`. Component B's prep
  injects `notes` into every drafter prompt.
- **UI**: a new **"Draft style ✎" tab** (mirrors the Taste tab) listing the
  accumulated lessons, with **pause / resume** and **reset** controls.

## Data model

- Per reply (`reply` and `reply_candidate`): new field
  `draft`: `null | {draft_id, text, created_at, learned_from}`. Reuses the existing
  `automated` flag to gate drafting. (Existing rows lack the key — readers use
  `.get()`; `confirm` already copies the whole candidate, so a draft carries over.)
- New `paths` constants: `DRAFT_JOBS_FILE`, `DRAFT_RESULTS_FILE`, `LEARN_JOBS_FILE`,
  `LEARN_RESULTS_FILE`, `DRAFT_STYLE_FILE` (all under `data/`, all gitignored).

## Error handling & safety

- Every `claude -p` step logs a WARN and the orchestrator continues on failure —
  one broken step never aborts the others or the morning run.
- Gmail stays read-only except `create_draft`; drafts are **never sent**.
- The check-now lock prevents overlapping runs; a second click while running returns
  `{ok:false, error:"already running"}` and the UI keeps polling.

## Testing

Deterministic pieces get `unittest` coverage:
- `draft_replies.build_jobs` (selects only `automated==false` without a draft) and
  `apply_drafts` (records `draft`, idempotent).
- `draft_learn.build_jobs` (selects drafts not yet learned, respects paused) and
  `apply_learnings` (appends notes, sets `learned_from`).
- `check_replies.run` **gating**: with the `claude -p` helper stubbed, assert each
  step's LLM call is skipped when prep yields no jobs and invoked when it does.
- `serve_ui` check-now start/status (lock prevents a second concurrent start) and the
  draft-style status/pause/reset endpoints.

The `claude -p` compose and diff steps are non-deterministic and not unit-tested;
they are bounded by the reviewer subagent and the fact the user reviews every draft
before sending.

## Out of scope (YAGNI)

- Sending email automatically (drafts only — always).
- A separate availability/context config field (timing config + learning suffice).
- Per-change approval of learned lessons (auto-apply, viewable/pausable instead).
- Deep-linking to the exact Gmail draft compose view (link to the thread, where the
  draft reply appears).
