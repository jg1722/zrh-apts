#!/usr/bin/env python3
"""Auto-draft step of the reply pipeline.

--prep  : write data/.draft_jobs.json for personal replies (automated==false)
          that don't have a draft yet. The claude -p drafter composes each reply
          and creates a Gmail draft (never sent), writing data/.draft_results.json.
--apply : record draft={draft_id, text, created_at, learned_from} on each reply.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import config, draft_style, paths  # noqa: E402
from applib.store import Store  # noqa: E402

_SLOTS = ("reply_candidate", "reply")


def _reply_needing_draft(lst: dict):
    for slot in _SLOTS:
        r = lst.get(slot)
        if r and r.get("automated") is False and not r.get("draft"):
            return slot, r
    return None, None


def build_jobs(store: Store) -> list[dict]:
    oc = config.criteria().get("outreach") or {}
    notes = draft_style.note_texts()
    jobs: list[dict] = []
    for lid, lst in store.listings.items():
        slot, r = _reply_needing_draft(lst)
        if not r:
            continue
        jobs.append({
            "id": lid,
            "thread_id": r.get("thread_id"),
            "listing": {"street": lst.get("street"), "rooms": lst.get("rooms"),
                        "size_sqm": lst.get("size_sqm"), "url": lst.get("url")},
            "reply": {"from": r.get("from"), "subject": r.get("subject"),
                      "snippet": r.get("snippet")},
            "applicant": oc.get("applicant") or {},
            "timing": oc.get("timing") or {},
            "notes": notes,
        })
    return jobs


def apply_drafts(store: Store, results: dict) -> dict:
    drafted = skipped = 0
    changed = False
    for lid, res in (results or {}).items():
        lst = store.listings.get(lid)
        if not lst or not (res or {}).get("draft_id"):
            skipped += 1
            continue
        slot, r = _reply_needing_draft(lst)
        if not r:
            skipped += 1
            continue
        r["draft"] = {"draft_id": res.get("draft_id"), "text": res.get("text"),
                      "created_at": paths.now_iso(), "learned_from": False}
        drafted += 1
        changed = True
    if changed:
        store.save()
    return {"drafted": drafted, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prep", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.prep:
        jobs = build_jobs(Store.load())
        paths.DRAFT_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        paths.DRAFT_JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"draft_replies: {len(jobs)} job(s)")
    if args.apply:
        results = {}
        if paths.DRAFT_RESULTS_FILE.exists():
            try:
                results = json.loads(paths.DRAFT_RESULTS_FILE.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError:
                results = {}
        res = apply_drafts(Store.load(), results if isinstance(results, dict) else {})
        print(f"draft_replies: drafted {res['drafted']}, skipped {res['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
