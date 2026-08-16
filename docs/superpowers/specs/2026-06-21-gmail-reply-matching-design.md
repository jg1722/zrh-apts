# Gmail reply matching → "Replied" overview

**Date:** 2026-06-21
**Status:** Approved (design), pending implementation plan

## Problem

We reach out to apartments two ways — email-channel (we send from the user's Gmail
to a known `outreach_email`, subject `"Anfrage Mietwohnung – <address>"`) and
form-channel (the user pastes our message into the listing's contact form; the
agency reply comes from an address we don't know in advance). Either way, replies
land in **you@example.com**. Today nothing connects those replies back to
the listing, so there's no overview of which apartments have responded. The
listing `status` flow already anticipates a `replied` state, but nothing sets it.

## Goal

Once a day, check Gmail for responses to apartments we've reached out to, match
each reply to the right listing, and surface confirmed responses in the local UI
so the reach-out overview stays current.

## Decisions (from brainstorming)

- **Gmail access:** Claude + Gmail connector, run as a `claude -p` step in the
  morning pipeline (mirrors the existing vision step). No new credentials.
- **Match scope:** email-channel **and** form-channel (best-effort).
- **Confirmation model:** two gates. A matcher proposes a candidate; a **separate,
  fresh-context reviewer sub-agent** independently validates it on the evidence
  alone; only survivors reach the UI, where the user still clicks confirm. The
  reviewer is a quality filter so the UI never shows junk; the user is the final gate.
- **UI:** a new "Replied" tab + a "Replied" badge on cards + a deep link to the
  Gmail thread.

## Architecture

Follows the repo's established pattern (vision step): deterministic Python owns
the durable store; Claude writes only to a scratch file that Python then applies.

```
reply_context.py (Python)  →  claude -p matcher + reviewer subagent  →  apply_replies.py (Python)  →  UI confirm
        │                                  │                                      │
  data/.outreach_context.json     data/.reply_matches.json            listings.json (reply_candidate)
```

### Components

1. **`scripts/reply_context.py` (prep, deterministic)**
   Emits `data/.outreach_context.json`: only `contacted` listings, each with the
   signals needed for matching — `id`, address parts (street/zip/city), `url`,
   `outreach_channel`, `outreach_email`, our sent subject
   (`"Anfrage Mietwohnung – <address>"` / English variant), and `decision_at`.
   A cheap deterministic pre-filter that keeps the LLM step small. Reuses the
   address/subject helpers already in `scripts/outreach.py`.

2. **Matcher (`claude -p` step, in `bin/run_morning.sh`)**
   Reads `data/.outreach_context.json`. For each contacted listing, Gmail-searches
   for replies received **on/after `decision_at`**. For each candidate reply it
   **dispatches an independent reviewer sub-agent** given *only* the evidence
   (email from/subject/snippet/date vs. apartment address/`outreach_email`/subject)
   → `{verdict: yes|no, reason, confidence}`. Only reviewer-approved matches are
   written to scratch `data/.reply_matches.json`, shape:

   ```json
   {
     "<listing_id>": {
       "thread_id": "...", "gmail_link": "https://mail.google.com/mail/u/0/#inbox/...",
       "from": "...", "subject": "...", "snippet": "...", "received_at": "2026-06-20T09:12:00Z",
       "matched_by": "email" | "form", "confidence": 0.0-1.0, "reviewer_reason": "..."
     }
   }
   ```

   Read-only: the step never sends mail and never labels/modifies Gmail.

3. **`scripts/apply_replies.py` (apply, deterministic)**
   Merges `data/.reply_matches.json` into `listings.json` as `reply_candidate`.
   Idempotent. Rules: a thread maps to at most one listing (highest confidence
   wins on collision); skip a thread already in a listing's confirmed `reply` or
   in any listing's `reply_dismissed_threads`; never overwrite an existing
   unconfirmed `reply_candidate` for the same thread.

### Matching signals

- **Email-channel** (known `outreach_email`): strong — same Gmail thread as our
  sent message, or sender == `outreach_email`, or subject matches our
  `"Anfrage Mietwohnung – <address>"`. High confidence.
- **Form-channel** (unknown sender): best-effort — street/zip/city appears in the
  subject or body, received within the window after `decision_at`, agency phrasing
  referencing the listing. Lower confidence → leans hardest on the reviewer gate
  and the user's confirm.
- **Guards:** ignore mail dated before `decision_at`; skip no-reply / automated
  acknowledgements (reviewer rejects these); one thread → at most one apartment.

## Data model (`scripts/applib/store.py` PIPELINE_DEFAULTS)

New fields (set once, then persisted):

- `reply_candidate`: `null` | `{thread_id, gmail_link, from, subject, snippet,
  received_at, matched_by, confidence, reviewer_reason}` — awaiting confirm.
- `reply`: `null` | the same shape **+ `confirmed_at`** — the confirmed reply.
- `reply_dismissed_threads`: `[]` — thread ids the user rejected, so they never
  re-surface.
- `status`: finally uses its existing `"replied"` value, set on confirm.

These are pipeline/user-owned fields; scout never touches them.

## UI (`web/index.html`, `web/app.js`, `web/style.css`, `scripts/serve_ui.py`)

- **New "Replied" tab** with two zones:
  - **Needs confirm** (top, amber cards): reply `snippet` + "Open in Gmail"
    (`gmail_link`) + **✓ Confirm reply** / **✗ Not a match** buttons.
  - **Confirmed** (below): green **"Replied · <date>"** badge + Gmail thread link.
- **Count badge** on the "Replied" tab when candidates await confirm.
- Cards in other tabs gain a **"Replied" badge** once confirmed.
- New endpoints in `serve_ui.py`:
  - `POST /api/reply/confirm/<id>` — move `reply_candidate` → `reply`
    (+ `confirmed_at`), set `status` = `"replied"`.
  - `POST /api/reply/reject/<id>` — clear `reply_candidate`, append its
    `thread_id` to `reply_dismissed_threads`.
- `/api/listings` includes the new reply fields; the tab filter shows listings
  with a `reply_candidate` or a confirmed `reply`.

## Scheduling & error handling (`bin/run_morning.sh`)

- New block after `digest.py`, gated on ≥1 `contacted` listing existing.
- Runs `reply_context.py`, then the `claude -p` matcher step
  (`--permission-mode acceptEdits`, requires the Gmail connector + sub-agent/Task
  tools), then `apply_replies.py`.
- **Graceful degradation:** if the connector or sub-agent tooling is unavailable,
  the step logs a WARN and the pipeline continues — no candidates are added that
  day, nothing else breaks.
- **Manual fallback:** the user can ask Claude to run the same check interactively in
  a normal session (where the connector is known-available).

## Testing (`scripts/tests/`)

Deterministic pieces are unit-tested:

- `reply_context.py` emits the expected fields for `contacted` listings and
  excludes others.
- `apply_replies.py`: idempotency, 1-thread-→-1-listing collision resolution,
  skip-if-confirmed, skip-if-dismissed, no-overwrite-of-existing-candidate.
- Confirm/reject endpoints: status transition to `replied`, dismissed-thread
  recording.

The LLM matching itself is non-deterministic and not unit-tested; it is bounded
by the independent reviewer gate plus the user's manual confirm.

## Out of scope (YAGNI)

- Sending or auto-replying to any email (the pipeline stays read-only on Gmail).
- Writing Gmail labels/stars (possible later; not needed for the overview).
- Threading beyond "this listing got a reply" — we track the thread link and
  latest snippet, not a full conversation view.
- A web-triggered "check now" button (manual fallback via Claude session suffices
  for v1).
