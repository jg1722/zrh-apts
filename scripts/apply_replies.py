#!/usr/bin/env python3
"""Apply the Gmail matcher's scratch output (data/.reply_matches.json) onto the
durable store.

Per apartment the matcher may return a `confirmation` (the form-submission
receipt — "request delivered") and/or a `reply` (a substantive personal/agency
response). Confirmations are AUTO-captured (reviewer-validated, no manual
confirm — they're our own self-receipts). Replies become reply_candidates
awaiting the user's confirm in the UI.

Idempotent and conservative: replies are one-thread -> at most one listing,
never re-added once confirmed or dismissed, never overwriting a pending
candidate for the same thread; confirmations are set once per thread.

    python scripts/apply_replies.py        # reads REPLY_MATCHES_FILE, applies
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import paths  # noqa: E402
from applib.store import Store  # noqa: E402

_REPLY_FIELDS = ("thread_id", "gmail_link", "from", "subject", "snippet",
                 "received_at", "matched_by", "confidence", "reviewer_reason",
                 "automated", "summary", "next_steps")
_CONFIRMATION_FIELDS = ("thread_id", "gmail_link", "from", "subject", "snippet",
                        "received_at", "reviewer_reason")
# Fields a re-run may backfill onto an existing candidate/confirmed reply.
_ENRICH_FIELDS = ("automated", "summary", "next_steps")


def _claimed_threads(store: Store) -> set[str]:
    """Reply thread ids already confirmed or dismissed on ANY listing."""
    claimed: set[str] = set()
    for lst in store.listings.values():
        rep = lst.get("reply")
        if rep and rep.get("thread_id"):
            claimed.add(rep["thread_id"])
        for tid in (lst.get("reply_dismissed_threads") or []):
            claimed.add(tid)
    return claimed


def _enrich(target: dict, rep: dict) -> bool:
    """Backfill summary/next_steps/automated onto an existing reply or candidate
    that predates those fields. Returns True if anything was filled in."""
    filled = False
    for k in _ENRICH_FIELDS:
        if target.get(k) in (None, "") and rep.get(k) not in (None, ""):
            target[k] = rep.get(k)
            filled = True
    return filled


def apply_matches(store: Store, matches: dict) -> dict:
    confirmations = replies = enriched = skipped = 0
    changed = False

    # Confirmations: auto-capture, idempotent by thread id.
    for lid, m in (matches or {}).items():
        if lid not in store.listings:
            skipped += 1
            continue
        conf = (m or {}).get("confirmation")
        if conf and conf.get("thread_id"):
            lst = store.listings[lid]
            # Set-once: the first receipt we see is enough proof of delivery.
            # Don't let later runs thrash the slot between equivalent receipts.
            if not lst.get("confirmation"):
                rec = {k: conf.get(k) for k in _CONFIRMATION_FIELDS}
                rec["captured_at"] = paths.now_iso()
                lst["confirmation"] = rec
                confirmations += 1
                changed = True

    # Replies: 1 thread -> 1 listing, on collision keep the highest confidence.
    by_thread: dict[str, tuple[str, dict]] = {}
    for lid, m in (matches or {}).items():
        rep = (m or {}).get("reply")
        if lid not in store.listings or not (rep or {}).get("thread_id"):
            continue
        tid = rep["thread_id"]
        cur = by_thread.get(tid)
        if cur is None or (rep.get("confidence") or 0) > (cur[1].get("confidence") or 0):
            by_thread[tid] = (lid, rep)

    claimed = _claimed_threads(store)
    for tid, (lid, rep) in by_thread.items():
        lst = store.listings[lid]
        # Same thread already confirmed or pending on THIS listing -> enrich it
        # (backfill summary/next_steps) rather than skip or duplicate.
        confirmed = lst.get("reply")
        if confirmed and confirmed.get("thread_id") == tid:
            if _enrich(confirmed, rep):
                enriched += 1
                changed = True
            else:
                skipped += 1
            continue
        existing = lst.get("reply_candidate")
        if existing and existing.get("thread_id") == tid:
            if _enrich(existing, rep):
                enriched += 1
                changed = True
            else:
                skipped += 1
            continue
        if tid in claimed:
            skipped += 1
            continue
        lst["reply_candidate"] = {k: rep.get(k) for k in _REPLY_FIELDS}
        replies += 1
        changed = True

    if changed:
        store.save()
    return {"confirmations": confirmations, "replies": replies,
            "enriched": enriched, "skipped": skipped}


def main() -> int:
    if not paths.REPLY_MATCHES_FILE.exists():
        print("apply_replies: no matches file — nothing to do")
        return 0
    try:
        matches = json.loads(paths.REPLY_MATCHES_FILE.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        print("apply_replies: matches file is not valid JSON — skipping")
        return 0
    res = apply_matches(Store.load(), matches if isinstance(matches, dict) else {})
    print(f"apply_replies: {res['confirmations']} confirmation(s), "
          f"{res['replies']} reply candidate(s), {res['enriched']} enriched, "
          f"skipped {res['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
