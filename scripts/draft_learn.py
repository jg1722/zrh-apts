#!/usr/bin/env python3
"""Edit-learning step of the reply pipeline.

--prep  : write data/.learn_jobs.json for replies whose draft hasn't been learned
          from yet (skipped entirely if draft-style learning is paused). The
          claude -p learner diffs the user's sent message vs our draft and writes
          lessons to data/.learn_results.json.
--apply : append lessons to the draft-style store and mark drafts learned_from.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import draft_style, paths  # noqa: E402
from applib.store import Store  # noqa: E402

_SLOTS = ("reply", "reply_candidate")


def _drafted_reply(lst: dict):
    for slot in _SLOTS:
        r = lst.get(slot)
        d = r and r.get("draft")
        if d and not d.get("learned_from"):
            return r
    return None


def build_jobs(store: Store) -> list[dict]:
    if draft_style.is_paused():
        return []
    jobs: list[dict] = []
    for lid, lst in store.listings.items():
        r = _drafted_reply(lst)
        if not r:
            continue
        jobs.append({"id": lid, "thread_id": r.get("thread_id"),
                     "draft_text": (r.get("draft") or {}).get("text")})
    return jobs


def apply_learnings(store: Store, results: dict) -> dict:
    learned = notes_added = 0
    changed = False
    for lid, res in (results or {}).items():
        lessons = (res or {}).get("lessons") or []
        if lessons:
            notes_added += draft_style.add_notes(lessons, source=lid)
        if (res or {}).get("learned"):
            lst = store.listings.get(lid)
            r = _drafted_reply(lst) if lst else None
            if r:
                r["draft"]["learned_from"] = True
                learned += 1
                changed = True
    if changed:
        store.save()
    return {"learned": learned, "notes_added": notes_added}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prep", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.prep:
        jobs = build_jobs(Store.load())
        paths.LEARN_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        paths.LEARN_JOBS_FILE.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"draft_learn: {len(jobs)} job(s)")
    if args.apply:
        results = {}
        if paths.LEARN_RESULTS_FILE.exists():
            try:
                results = json.loads(paths.LEARN_RESULTS_FILE.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError:
                results = {}
        res = apply_learnings(Store.load(), results if isinstance(results, dict) else {})
        print(f"draft_learn: learned {res['learned']}, notes added {res['notes_added']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
