# Reply Draft Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Check now" button that runs the reply pipeline on demand, auto-drafts a custom Gmail reply for each new personal reply (never sent), and learns from the user's edits to keep drafts improving.

**Architecture:** Extends the existing reply pipeline (`reply_context` → `claude -p` matcher → `apply_replies`) with two more prep→`claude -p`→apply steps (draft, learn), all behind one orchestrator `check_replies.py` shared by the morning cron and the button. Python owns the store; each `claude -p` writes only a scratch file; each step skips its LLM call when prep yields no jobs. Draft-style lessons live in a Taste-style learned file.

**Tech Stack:** Python 3 stdlib (`unittest`, `threading`, `subprocess`), the existing `http.server` UI, vanilla JS frontend, Claude CLI (`claude -p`) with the Gmail connector.

## Global Constraints

- Python owns `listings.json`; every `claude -p` writes ONLY to a scratch file under `data/`. Verbatim repo rule (store.py docstring + vision/matcher steps).
- Gmail is read-only **except `create_draft`**; drafts are **NEVER sent, labelled, or starred**.
- No new third-party dependencies; stdlib only.
- New per-reply field is pipeline-owned; existing rows lack it → readers use `.get()`.
- Each `claude -p` step degrades gracefully (log + continue); one failing step never aborts the others or the morning run.
- Draft availability comes from existing `outreach.timing` config; no new config field.
- Tests are `unittest`, run with `.venv/bin/python -m unittest` (pytest is NOT installed). Use the `use_temp_data` / `write_store` helpers already in `scripts/tests/`.
- Build via subagent-driven-development (fresh subagent per task, two-stage review). Keep it lean — no speculative generality.

---

### Task 1: Paths, store field, draft-style store

**Files:**
- Modify: `scripts/applib/paths.py` (5 constants)
- Modify: `scripts/applib/store.py` (PIPELINE_DEFAULTS: `draft`)
- Create: `scripts/applib/draft_style.py`
- Modify: `.gitignore`
- Test: `scripts/tests/test_draft_style.py` (new)

**Interfaces:**
- Produces: paths `DRAFT_JOBS_FILE`, `DRAFT_RESULTS_FILE`, `LEARN_JOBS_FILE`, `LEARN_RESULTS_FILE`, `DRAFT_STYLE_FILE`; listing field `draft` (None | dict). Module `draft_style` with `note_texts() -> list[str]`, `add_notes(lessons: list[str], source: str|None) -> int`, `is_paused() -> bool`, `set_paused(bool)`, `reset()`, `status() -> dict`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_draft_style.py`:

```python
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib import draft_style, paths  # noqa: E402


def use_temp(testcase):
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
    saved = paths.DRAFT_STYLE_FILE
    testcase.addCleanup(lambda: setattr(paths, "DRAFT_STYLE_FILE", saved))
    paths.DRAFT_STYLE_FILE = tmp / ".draft_style.json"


class DraftStyle(unittest.TestCase):
    def setUp(self):
        use_temp(self)

    def test_add_and_list_notes(self):
        n = draft_style.add_notes(["Keep it short.", "State exact dates."], source="x")
        self.assertEqual(n, 2)
        self.assertIn("Keep it short.", draft_style.note_texts())

    def test_add_dedups(self):
        draft_style.add_notes(["Keep it short."])
        n = draft_style.add_notes(["Keep it short.", "New one."])
        self.assertEqual(n, 1)

    def test_pause_and_reset(self):
        draft_style.add_notes(["a"])
        draft_style.set_paused(True)
        self.assertTrue(draft_style.is_paused())
        draft_style.reset()
        self.assertEqual(draft_style.status()["count"], 0)
        self.assertFalse(draft_style.is_paused())

    def test_status_shape(self):
        s = draft_style.status()
        self.assertEqual(set(s), {"notes", "paused", "count"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest scripts.tests.test_draft_style -v`
Expected: FAIL — `AttributeError: module 'applib.paths' has no attribute 'DRAFT_STYLE_FILE'`.

- [ ] **Step 3: Add the path constants**

In `scripts/applib/paths.py`, after the `REPLY_MATCHES_FILE` line, add:

```python
DRAFT_JOBS_FILE = DATA_DIR / ".draft_jobs.json"        # drafter input (gitignored)
DRAFT_RESULTS_FILE = DATA_DIR / ".draft_results.json"  # drafter claude -p output (gitignored)
LEARN_JOBS_FILE = DATA_DIR / ".learn_jobs.json"        # learner input (gitignored)
LEARN_RESULTS_FILE = DATA_DIR / ".learn_results.json"  # learner claude -p output (gitignored)
DRAFT_STYLE_FILE = DATA_DIR / ".draft_style.json"      # accumulated draft-style lessons (gitignored)
```

- [ ] **Step 4: Add the store field**

In `scripts/applib/store.py` PIPELINE_DEFAULTS, after `"reply_dismissed_threads": [],`, add:

```python
    "draft": None,                  # set on a reply: {draft_id, text, created_at, learned_from} — the auto-drafted Gmail reply
```

Note: `draft` lives inside the `reply`/`reply_candidate` objects, not at listing top level; this default documents the field. (No code change beyond the comment line + the field appearing in new reply dicts which are built by apply steps.)

Actually place the field as a documented default at listing level too for discoverability:

```python
    "draft": None,                  # reserved; the live draft is stored inside reply/reply_candidate
```

- [ ] **Step 5: Create the draft_style module**

Create `scripts/applib/draft_style.py`:

```python
"""Accumulated draft-style lessons learned from the user's edits to reply drafts.

Lives in data/.draft_style.json; mirrors learning.py's load/save/pause/reset.
Component B (draft_replies) injects these notes into every drafter prompt.
"""
from __future__ import annotations

import datetime as _dt
import json

from . import paths


def _load() -> dict:
    if paths.DRAFT_STYLE_FILE.exists():
        return json.loads(paths.DRAFT_STYLE_FILE.read_text(encoding="utf-8"))
    return {"notes": [], "paused": False}


def _save(data: dict) -> None:
    paths.DRAFT_STYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.DRAFT_STYLE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(paths.DRAFT_STYLE_FILE)


def note_texts() -> list[str]:
    return [n.get("text", "") for n in _load().get("notes", []) if n.get("text")]


def add_notes(lessons: list[str], source: str | None = None) -> int:
    data = _load()
    existing = {n.get("text") for n in data.get("notes", [])}
    now = _dt.datetime.now().replace(microsecond=0).isoformat()
    added = 0
    for raw in lessons or []:
        t = (raw or "").strip()
        if t and t not in existing:
            data.setdefault("notes", []).append({"text": t, "added_at": now, "from": source})
            existing.add(t)
            added += 1
    if added:
        _save(data)
    return added


def is_paused() -> bool:
    return bool(_load().get("paused"))


def set_paused(flag: bool) -> None:
    data = _load()
    data["paused"] = bool(flag)
    _save(data)


def reset() -> None:
    paths.DRAFT_STYLE_FILE.unlink(missing_ok=True)


def status() -> dict:
    d = _load()
    return {"notes": d.get("notes", []), "paused": bool(d.get("paused")),
            "count": len(d.get("notes", []))}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m unittest scripts.tests.test_draft_style -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Gitignore the scratch/notes files**

In `.gitignore`, after the `data/.reply_matches.json` line, add:

```
data/.draft_jobs.json
data/.draft_results.json
data/.learn_jobs.json
data/.learn_results.json
data/.draft_style.json
```

- [ ] **Step 8: Commit**

```bash
git add scripts/applib/paths.py scripts/applib/store.py scripts/applib/draft_style.py scripts/tests/test_draft_style.py .gitignore
git commit -m "feat(drafts): paths, draft field, draft-style learned store

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: draft_replies.py (prep + apply)

**Files:**
- Create: `scripts/draft_replies.py`
- Test: `scripts/tests/test_draft_replies.py` (new)

**Interfaces:**
- Consumes: `Store`, `draft_style.note_texts()`, `config.criteria()['outreach']`, `paths.DRAFT_JOBS_FILE`, `paths.DRAFT_RESULTS_FILE`.
- Produces: `build_jobs(store) -> list[dict]` (jobs for personal replies without a draft); `apply_drafts(store, results: dict) -> dict` returning `{"drafted": int, "skipped": int}`. CLI `--prep` / `--apply`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_draft_replies.py`:

```python
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draft_replies  # noqa: E402
from applib import paths  # noqa: E402
from applib.store import Store  # noqa: E402


def use_temp(testcase):
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
    saved = {k: getattr(paths, k) for k in ("LISTINGS_FILE", "DRAFT_STYLE_FILE")}
    testcase.addCleanup(lambda: [setattr(paths, k, v) for k, v in saved.items()])
    paths.LISTINGS_FILE = tmp / "listings.json"
    paths.DRAFT_STYLE_FILE = tmp / ".draft_style.json"


def write_store(listings):
    paths.LISTINGS_FILE.write_text(json.dumps({"meta": {}, "listings": listings}), encoding="utf-8")


def personal_reply(**kw):
    base = {"thread_id": "T1", "from": "agent@x.ch", "subject": "Re: Anfrage",
            "snippet": "Möchten Sie eine Besichtigung?", "automated": False}
    base.update(kw)
    return base


class BuildJobs(unittest.TestCase):
    def setUp(self):
        use_temp(self)

    def test_includes_personal_reply_without_draft(self):
        write_store({"a": {"id": "a", "street": "Eulenweg 27", "rooms": 3.5,
                           "size_sqm": 80, "url": "u", "reply_candidate": personal_reply()}})
        jobs = draft_replies.build_jobs(Store.load())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "a")
        self.assertEqual(jobs[0]["thread_id"], "T1")
        self.assertIn("timing", jobs[0])

    def test_excludes_automated(self):
        write_store({"a": {"id": "a", "reply_candidate": personal_reply(automated=True)}})
        self.assertEqual(draft_replies.build_jobs(Store.load()), [])

    def test_excludes_already_drafted(self):
        r = personal_reply(); r["draft"] = {"draft_id": "d1"}
        write_store({"a": {"id": "a", "reply_candidate": r}})
        self.assertEqual(draft_replies.build_jobs(Store.load()), [])

    def test_includes_confirmed_reply_slot(self):
        write_store({"a": {"id": "a", "reply": personal_reply()}})
        jobs = draft_replies.build_jobs(Store.load())
        self.assertEqual(len(jobs), 1)


class ApplyDrafts(unittest.TestCase):
    def setUp(self):
        use_temp(self)
        write_store({"a": {"id": "a", "reply_candidate": personal_reply()}})

    def test_records_draft(self):
        res = draft_replies.apply_drafts(Store.load(), {"a": {"draft_id": "d1", "text": "Guten Tag"}})
        self.assertEqual(res["drafted"], 1)
        d = Store.load().listings["a"]["reply_candidate"]["draft"]
        self.assertEqual(d["draft_id"], "d1")
        self.assertFalse(d["learned_from"])

    def test_idempotent(self):
        draft_replies.apply_drafts(Store.load(), {"a": {"draft_id": "d1", "text": "x"}})
        res = draft_replies.apply_drafts(Store.load(), {"a": {"draft_id": "d2", "text": "y"}})
        self.assertEqual(res["drafted"], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest scripts.tests.test_draft_replies -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'draft_replies'`.

- [ ] **Step 3: Create the script**

Create `scripts/draft_replies.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest scripts.tests.test_draft_replies -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/draft_replies.py scripts/tests/test_draft_replies.py
git commit -m "feat(drafts): draft_replies prep/apply for personal replies

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: draft_learn.py (prep + apply)

**Files:**
- Create: `scripts/draft_learn.py`
- Test: `scripts/tests/test_draft_learn.py` (new)

**Interfaces:**
- Consumes: `Store`, `draft_style` (`is_paused`, `add_notes`), `paths.LEARN_JOBS_FILE`, `paths.LEARN_RESULTS_FILE`.
- Produces: `build_jobs(store) -> list[dict]` (drafts not yet learned, skipped if paused); `apply_learnings(store, results: dict) -> dict` returning `{"learned": int, "notes_added": int}`. CLI `--prep` / `--apply`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_draft_learn.py`:

```python
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import draft_learn  # noqa: E402
from applib import draft_style, paths  # noqa: E402
from applib.store import Store  # noqa: E402


def use_temp(testcase):
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
    saved = {k: getattr(paths, k) for k in ("LISTINGS_FILE", "DRAFT_STYLE_FILE")}
    testcase.addCleanup(lambda: [setattr(paths, k, v) for k, v in saved.items()])
    paths.LISTINGS_FILE = tmp / "listings.json"
    paths.DRAFT_STYLE_FILE = tmp / ".draft_style.json"


def write_store(listings):
    paths.LISTINGS_FILE.write_text(json.dumps({"meta": {}, "listings": listings}), encoding="utf-8")


def drafted_reply(learned=False):
    return {"thread_id": "T1", "from": "a@x.ch", "automated": False,
            "draft": {"draft_id": "d1", "text": "Guten Tag", "learned_from": learned}}


class BuildJobs(unittest.TestCase):
    def setUp(self):
        use_temp(self)

    def test_includes_unlearned_draft(self):
        write_store({"a": {"id": "a", "reply": drafted_reply(learned=False)}})
        jobs = draft_learn.build_jobs(Store.load())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["draft_text"], "Guten Tag")

    def test_excludes_learned(self):
        write_store({"a": {"id": "a", "reply": drafted_reply(learned=True)}})
        self.assertEqual(draft_learn.build_jobs(Store.load()), [])

    def test_paused_yields_nothing(self):
        write_store({"a": {"id": "a", "reply": drafted_reply(learned=False)}})
        draft_style.set_paused(True)
        self.assertEqual(draft_learn.build_jobs(Store.load()), [])


class ApplyLearnings(unittest.TestCase):
    def setUp(self):
        use_temp(self)
        write_store({"a": {"id": "a", "reply": drafted_reply(learned=False)}})

    def test_appends_notes_and_marks_learned(self):
        res = draft_learn.apply_learnings(Store.load(),
                                          {"a": {"learned": True, "lessons": ["Keep it short."]}})
        self.assertEqual(res["learned"], 1)
        self.assertEqual(res["notes_added"], 1)
        self.assertIn("Keep it short.", draft_style.note_texts())
        self.assertTrue(Store.load().listings["a"]["reply"]["draft"]["learned_from"])

    def test_learned_without_lessons_still_marks(self):
        res = draft_learn.apply_learnings(Store.load(), {"a": {"learned": True, "lessons": []}})
        self.assertEqual(res["notes_added"], 0)
        self.assertTrue(Store.load().listings["a"]["reply"]["draft"]["learned_from"])

    def test_not_sent_yet_leaves_unlearned(self):
        res = draft_learn.apply_learnings(Store.load(), {"a": {"learned": False, "lessons": []}})
        self.assertEqual(res["learned"], 0)
        self.assertFalse(Store.load().listings["a"]["reply"]["draft"]["learned_from"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest scripts.tests.test_draft_learn -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'draft_learn'`.

- [ ] **Step 3: Create the script**

Create `scripts/draft_learn.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest scripts.tests.test_draft_learn -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/draft_learn.py scripts/tests/test_draft_learn.py
git commit -m "feat(drafts): draft_learn prep/apply (sent-vs-draft lessons)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: check_replies.py orchestrator

**Files:**
- Create: `scripts/check_replies.py`
- Test: `scripts/tests/test_check_replies.py` (new)

**Interfaces:**
- Consumes: `reply_context.build_context`, `apply_replies.apply_matches`, `draft_replies.build_jobs/apply_drafts`, `draft_learn.build_jobs/apply_learnings`, paths.
- Produces: `run() -> dict` summary `{"matched", "drafted", "learned"}`; internal `_run_claude(prompt, tools) -> bool` (stubbable). CLI runs `run()`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_check_replies.py`:

```python
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_replies  # noqa: E402
from applib import paths  # noqa: E402


def use_temp(testcase):
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
    keys = ("DATA_DIR", "LISTINGS_FILE", "OUTREACH_CONTEXT_FILE", "REPLY_MATCHES_FILE",
            "DRAFT_JOBS_FILE", "DRAFT_RESULTS_FILE", "LEARN_JOBS_FILE",
            "LEARN_RESULTS_FILE", "DRAFT_STYLE_FILE", "DIGESTS_DIR", "LOGS_DIR", "PHOTOS_DIR")
    saved = {k: getattr(paths, k) for k in keys}
    testcase.addCleanup(lambda: [setattr(paths, k, v) for k, v in saved.items()])
    paths.DATA_DIR = tmp
    for k in keys[1:]:
        setattr(paths, k, tmp / Path(getattr(paths, k)).name)
    return tmp


def write_store(listings):
    paths.LISTINGS_FILE.write_text(json.dumps({"meta": {}, "listings": listings}), encoding="utf-8")


class Gating(unittest.TestCase):
    def setUp(self):
        self.tmp = use_temp(self)
        self.calls = []

    def _stub(self, results_for=None):
        # Records each claude step and optionally drops a results file so apply works.
        def fake(prompt, tools):
            self.calls.append(prompt[:24])
            return True
        check_replies._run_claude = fake

    def test_no_contacted_no_matcher_call(self):
        write_store({"a": {"id": "a", "status": "new"}})
        self._stub()
        check_replies.run()
        self.assertEqual(self.calls, [])  # nothing to match/draft/learn

    def test_contacted_triggers_matcher(self):
        write_store({"a": {"id": "a", "status": "contacted", "decision_at": "2026-06-20T00:00:00"}})
        self._stub()
        check_replies.run()
        self.assertTrue(self.calls)  # matcher ran

    def test_personal_reply_triggers_drafter(self):
        write_store({"a": {"id": "a", "status": "contacted",
                           "reply_candidate": {"thread_id": "T1", "from": "x@x.ch",
                                               "automated": False}}})
        self._stub()
        check_replies.run()
        # at least the drafter prompt should have been invoked
        self.assertTrue(any("Draft reply" in c or "draft" in c.lower() for c in
                            [p for p in self.calls]) or len(self.calls) >= 2)
```

(Note: the drafter-prompt assertion is loose because prompts are truncated; the key behaviour is that with a personal reply present, more than just the matcher runs.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest scripts.tests.test_check_replies -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'check_replies'`.

- [ ] **Step 3: Create the orchestrator**

Create `scripts/check_replies.py`:

```python
#!/usr/bin/env python3
"""Run the full Gmail reply pipeline: match -> draft -> learn.

Shared by the morning cron (bin/run_morning.sh) and the UI 'Check now' button.
Each claude -p step is skipped when its prep finds no jobs, so a quiet run makes
no LLM calls. Read-only on Gmail except creating drafts; never sends mail.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import apply_replies  # noqa: E402
import draft_learn  # noqa: E402
import draft_replies  # noqa: E402
import reply_context  # noqa: E402
from applib import paths  # noqa: E402
from applib.store import Store  # noqa: E402

MATCHER_TOOLS = ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
                 "Task", "Read", "Write"]
DRAFTER_TOOLS = ["mcp__claude_ai_Gmail__get_thread", "mcp__claude_ai_Gmail__create_draft",
                 "Task", "Read", "Write"]
LEARNER_TOOLS = ["mcp__claude_ai_Gmail__get_thread", "Read", "Write"]

MATCHER_PROMPT = (
    "Match Gmail messages to apartment outreach. Read data/.outreach_context.json (a JSON list; "
    "each item is one apartment we contacted, with id, subject, email, channel, street, zipcode, "
    "city, decision_at, url). For EACH apartment, use the Gmail search tool to find threads "
    "received on or after its decision_at: for email-channel items search by the sender email "
    "and/or our subject line; for form-channel items (email is null) search by the street plus "
    "locality. Classify each found message into one of two kinds: (1) CONFIRMATION — an automated "
    "platform/agency receipt acknowledging that OUR enquiry was sent/received for this apartment "
    "(e.g. 'Bestaetigung zum Versand Ihrer Kontaktanfrage', 'Eingang Ihrer Anfrage', no-reply "
    "submission receipts); (2) REPLY — a substantive personal/agency response that moves things "
    "forward (offers a viewing, asks for documents, gives a contact person, answers questions). "
    "For EACH candidate of EITHER kind, dispatch a SEPARATE independent reviewer subagent (Task "
    "tool, subagent_type general-purpose) given ONLY the email's from/subject/snippet/received-date "
    "and the apartment's address/email/subject/decision_at, asking strictly (a) whether it concerns "
    "THAT apartment, and (b) which kind it is, with a one-line reason and confidence 0..1. Keep "
    "CONFIRMATIONS the reviewer approves with confidence >= 0.7 and REPLIES with confidence >= 0.6; "
    "discard anything about a different apartment or clearly unrelated. For every kept REPLY, read "
    "the thread (Gmail get_thread) and add: automated (boolean — true if it is an automated/system "
    "message, false if written by a person), summary (one sentence, <=25 words), and next_steps "
    "(<=15 words, the concrete action the user must take next; empty string if none). Build gmail_link "
    "as https://mail.google.com/mail/u/0/#all/<thread_id>. Write a JSON object mapping apartment id "
    "-> {confirmation: {thread_id, gmail_link, from, subject, snippet, received_at, reviewer_reason} "
    "| null, reply: {thread_id, gmail_link, from, subject, snippet, received_at, matched_by:"
    "'email'|'form', confidence, reviewer_reason, automated, summary, next_steps} | null} to "
    "data/.reply_matches.json (write {} if nothing). Do NOT send, reply to, label, star, or modify "
    "any email. Do nothing else."
)

DRAFTER_PROMPT = (
    "Draft reply emails for apartment enquiries. Read data/.draft_jobs.json (a JSON list; each item: "
    "id, thread_id, listing {street, rooms, size_sqm, url}, reply {from, subject, snippet}, applicant "
    "{...}, timing {...}, notes [learned style lessons]). For EACH item: use Gmail get_thread on "
    "thread_id to read the latest message from the agency. Compose a concise, polite reply in the "
    "SAME language as that message (Swiss agencies: German, formal 'Sie'). The reply should thank "
    "them, confirm continued interest in the specific apartment, state availability for a viewing "
    "using the timing windows (viewing_window), offer a full application dossier on request, and "
    "answer any direct question. Apply EVERY lesson in notes. Sign as 'Vorname Nachname', phone "
    "+41 79 000 00 00, you@example.com. Then dispatch ONE independent reviewer subagent (Task "
    "tool, subagent_type general-purpose) with the composed draft + the listing address + the "
    "incoming message, asking it to confirm the draft is accurate (correct address, no invented "
    "facts, availability present, appropriate tone) and flag any problem; revise if flagged. Then "
    "create the draft with the Gmail create_draft tool: to = the reply 'from' address, subject = "
    "'Re: ' + the original subject, replyToMessageId = the latest message id you read, body = your "
    "reply. Record {id: {draft_id: <returned id>, text: <the body>}} for each into "
    "data/.draft_results.json. Do NOT send, label, star, or modify any email; only create drafts. "
    "Do nothing else."
)

LEARNER_PROMPT = (
    "Learn from how the user edits reply drafts before sending. Read data/.learn_jobs.json (a JSON list; "
    "each item: id, thread_id, draft_text). For EACH item: use Gmail get_thread on thread_id and find "
    "the most recent message SENT BY the user (from you@example.com) in that thread. If there is "
    "no such sent message yet, set learned=false and lessons=[] (he hasn't sent). If there is one, "
    "compare it to draft_text and extract up to 3 short, general, reusable lessons about how he "
    "changed it (tone, length, content added/removed, phrasing, availability specifics), each phrased "
    "as an instruction for future drafts (e.g. 'Keep it to 3 short sentences'). If he sent it "
    "essentially unchanged, lessons=[]. Set learned=true for any id where a sent message existed. "
    "Write {id: {learned: <bool>, lessons: [<str>]}} for each to data/.learn_results.json. Read-only: "
    "do NOT send, draft, label, or modify any email. Do nothing else."
)


def _run_claude(prompt: str, tools: list[str]) -> bool:
    """Invoke claude -p headless. Returns True on success. Isolated for stubbing."""
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--allowedTools", *tools,
             "--permission-mode", "acceptEdits"],
            stdin=subprocess.DEVNULL, cwd=str(paths.ROOT), timeout=900)
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        print(f"check_replies: claude step failed: {e}")
        return False


def _read_json(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError:
        return {}


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def run() -> dict:
    summary = {"matched": None, "drafted": 0, "learned": 0}

    # 1. match
    ctx = reply_context.build_context(Store.load())
    _write_json(paths.OUTREACH_CONTEXT_FILE, ctx)
    if ctx:
        paths.REPLY_MATCHES_FILE.unlink(missing_ok=True)
        if _run_claude(MATCHER_PROMPT, MATCHER_TOOLS):
            summary["matched"] = apply_replies.apply_matches(Store.load(),
                                                             _read_json(paths.REPLY_MATCHES_FILE))

    # 2. draft
    jobs = draft_replies.build_jobs(Store.load())
    _write_json(paths.DRAFT_JOBS_FILE, jobs)
    if jobs:
        paths.DRAFT_RESULTS_FILE.unlink(missing_ok=True)
        if _run_claude(DRAFTER_PROMPT, DRAFTER_TOOLS):
            summary["drafted"] = draft_replies.apply_drafts(
                Store.load(), _read_json(paths.DRAFT_RESULTS_FILE)).get("drafted", 0)

    # 3. learn
    ljobs = draft_learn.build_jobs(Store.load())
    _write_json(paths.LEARN_JOBS_FILE, ljobs)
    if ljobs:
        paths.LEARN_RESULTS_FILE.unlink(missing_ok=True)
        if _run_claude(LEARNER_PROMPT, LEARNER_TOOLS):
            summary["learned"] = draft_learn.apply_learnings(
                Store.load(), _read_json(paths.LEARN_RESULTS_FILE)).get("learned", 0)

    return summary


def main() -> int:
    print(f"check_replies: {run()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest scripts.tests.test_check_replies -v`
Expected: PASS (3 tests). The stub replaces `_run_claude` so no real Claude/Gmail calls happen.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_replies.py scripts/tests/test_check_replies.py
git commit -m "feat(drafts): check_replies orchestrator (match -> draft -> learn)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: serve_ui endpoints (check-now + draft-style)

**Files:**
- Modify: `scripts/serve_ui.py`
- Test: `scripts/tests/test_serve_ui.py` (add `CheckNow` + `DraftStyleEndpoint` classes)

**Interfaces:**
- Consumes: `check_replies.run`, `draft_style`, `threading`.
- Produces: `api_check_start() -> dict`, `api_check_status() -> dict`, `api_draft_style() -> dict`, `api_draft_style_control(action) -> dict`. Routes: `POST /api/check-replies`, `GET /api/check-replies/status`, `GET /api/draft-style`, `POST /api/draft-style/<action>`.

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_serve_ui.py`:

```python
class CheckNow(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({"a": FL(id="a")})

    def test_status_shape(self):
        s = serve_ui.api_check_status()
        for k in ("running", "started_at", "finished_at", "summary", "error"):
            self.assertIn(k, s)

    def test_start_rejected_when_locked(self):
        # Hold the lock to simulate a run already in progress.
        self.assertTrue(serve_ui._check_lock.acquire(blocking=False))
        try:
            self.assertEqual(serve_ui.api_check_start(), {"ok": False, "error": "already running"})
        finally:
            serve_ui._check_lock.release()


class DraftStyleEndpoint(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        # point the draft-style file into the temp dir
        from applib import paths as _p
        self._saved = _p.DRAFT_STYLE_FILE
        self.addCleanup(lambda: setattr(_p, "DRAFT_STYLE_FILE", self._saved))
        _p.DRAFT_STYLE_FILE = _p.DATA_DIR / ".draft_style.json"

    def test_status_and_control(self):
        out = serve_ui.api_draft_style()
        self.assertIn("notes", out)
        serve_ui.api_draft_style_control("pause")
        self.assertTrue(serve_ui.api_draft_style()["paused"])
        serve_ui.api_draft_style_control("resume")
        self.assertFalse(serve_ui.api_draft_style()["paused"])
        self.assertEqual(serve_ui.api_draft_style_control("bogus"),
                         {"ok": False, "error": "unknown action"})
```

(Note: `use_temp_data` already sets `paths.DATA_DIR`; the DraftStyleEndpoint test repoints `DRAFT_STYLE_FILE` under it.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest scripts.tests.test_serve_ui.CheckNow scripts.tests.test_serve_ui.DraftStyleEndpoint -v`
Expected: FAIL — `AttributeError: module 'serve_ui' has no attribute '_check_lock'`.

- [ ] **Step 3: Add the threading state + API functions**

In `scripts/serve_ui.py`, near the top imports add `import threading` (if not present). After the `api_reply_reject` function, add:

```python
# ---- check-now (background reply pipeline) --------------------------------
_check_lock = threading.Lock()
_check_state = {"running": False, "started_at": None, "finished_at": None,
                "summary": None, "error": None}


def _check_worker():
    try:
        import check_replies
        _check_state["summary"] = check_replies.run()
    except Exception as e:  # noqa: BLE001
        _check_state["error"] = str(e)
    finally:
        _check_state["running"] = False
        _check_state["finished_at"] = paths.now_iso()
        _check_lock.release()


def api_check_start() -> dict:
    if not _check_lock.acquire(blocking=False):
        return {"ok": False, "error": "already running"}
    _check_state.update(running=True, started_at=paths.now_iso(),
                        finished_at=None, summary=None, error=None)
    threading.Thread(target=_check_worker, daemon=True).start()
    return {"ok": True, "started": True}


def api_check_status() -> dict:
    return dict(_check_state)


def api_draft_style() -> dict:
    from applib import draft_style
    return draft_style.status()


def api_draft_style_control(action: str) -> dict:
    from applib import draft_style
    if action == "pause":
        draft_style.set_paused(True)
    elif action == "resume":
        draft_style.set_paused(False)
    elif action == "reset":
        draft_style.reset()
    else:
        return {"ok": False, "error": "unknown action"}
    return {"ok": True}
```

- [ ] **Step 4: Add the routes**

In `do_GET`, after the `/api/learning` route, add:

```python
            if u.path == "/api/check-replies/status":
                return self._send_json(api_check_status())
            if u.path == "/api/draft-style":
                return self._send_json(api_draft_style())
```

In `do_POST`, after the `/api/reply/reject/` route, add:

```python
            if u.path == "/api/check-replies":
                return self._send_json(api_check_start())
            if u.path.startswith("/api/draft-style/"):
                return self._send_json(api_draft_style_control(u.path.split("/")[-1]))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m unittest scripts.tests.test_serve_ui.CheckNow scripts.tests.test_serve_ui.DraftStyleEndpoint -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add scripts/serve_ui.py scripts/tests/test_serve_ui.py
git commit -m "feat(drafts): check-now + draft-style endpoints in serve_ui

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: UI — Check now button, draft link, Draft style tab

**Files:**
- Modify: `web/index.html` (button + tab)
- Modify: `web/app.js` (check-now poll, draft link, renderDraftStyle)
- Modify: `web/style.css` (button)

**Interfaces:**
- Consumes: endpoints from Task 5; `reply.draft` from Task 2.
- Produces: header "⟳ Check now" button, "✎ Draft ready" link on reply cards, "Draft style ✎" tab.

This is UI; verified by serving the page (server reads `web/` fresh per request, and now sends no-cache headers, so a refresh suffices).

- [ ] **Step 1: Add the button and tab**

In `web/index.html`, change the `<h1>` line to add the button after it:

```html
    <h1>ZRH Apartments</h1>
    <button id="check-now">⟳ Check now</button>
```

And add a tab button after the `learning` (Taste) button:

```html
      <button data-tab="draftstyle">Draft style ✎</button>
```

- [ ] **Step 2: Add the check-now logic**

In `web/app.js`, just before the final `load();` line, add:

```javascript
let checkPoll = null;
async function startCheck(){
  const btn = $("#check-now");
  const r = await api("/api/check-replies", {method:"POST"});
  if (r && r.error === "already running"){ toast("Already checking…"); }
  btn.disabled = true; btn.textContent = "Checking…";
  if (checkPoll) clearInterval(checkPoll);
  checkPoll = setInterval(pollCheck, 3000);
}
async function pollCheck(){
  const s = await api("/api/check-replies/status");
  if (!s || s.running) return;
  clearInterval(checkPoll); checkPoll = null;
  const btn = $("#check-now"); btn.disabled = false; btn.textContent = "⟳ Check now";
  const m = s.summary || {};
  toast(s.error ? ("Check failed: " + s.error)
                : ("Checked · " + (m.drafted ? (m.drafted + " new draft(s)") : "up to date")));
  load();
}
$("#check-now").onclick = startCheck;
```

- [ ] **Step 3: Add the draft link to reply cards**

In `web/app.js`, add this helper next to `delivLine`:

```javascript
function draftLine(r){
  return (r && r.draft)
    ? `<div class="deliv">✎ Draft ready in Gmail · <a class="link" href="${esc(r.gmail_link || "#")}" target="_blank" rel="noopener">open thread</a></div>`
    : "";
}
```

Then in `renderState`, in the confirmed-reply block change it to include the draft line, and in the candidate block add it. Confirmed block becomes:

```javascript
  if (l.reply){
    box.innerHTML = `${delivLine(l)}${draftLine(l.reply)}<div class="done">✓ Replied${l.reply.received_at ? (" · " + fmtDate(l.reply.received_at)) : ""} ${replyTag(l.reply)}</div>
      ${replyBody(l.reply)}
      <a class="link" href="${esc(l.reply.gmail_link || "#")}" target="_blank" rel="noopener">open in Gmail</a>`;
    return;
  }
```

In the candidate block, after the `box.querySelector(".reply-cand").insertAdjacentHTML("afterbegin", delivLine(l));` line, add:

```javascript
    box.querySelector(".reply-cand").insertAdjacentHTML("beforeend", draftLine(l.reply_candidate));
```

- [ ] **Step 4: Add the Draft style tab render**

In `web/app.js`, in `load()`, change the first line:

```javascript
  if (TAB === "learning") return renderLearning();
```

to:

```javascript
  if (TAB === "learning") return renderLearning();
  if (TAB === "draftstyle") return renderDraftStyle();
```

And add the function after `renderLearning`:

```javascript
async function renderDraftStyle(){
  const s = await api("/api/draft-style");
  const items = (s.notes || []).map(n =>
    `<li>${esc(n.text)} ${n.from ? `<span class="muted">· ${esc(n.from)}</span>` : ""}</li>`).join("")
    || '<li class="muted">No lessons yet — they appear after you edit & send drafts.</li>';
  $("#content").innerHTML = `
    <div id="learning">
      <h2>Draft style ✎ ${s.paused ? '<span class="tag warn">paused</span>' : ''}</h2>
      <p class="muted">Lessons learned from how you edit drafts before sending — applied to every new draft.</p>
      <ul>${items}</ul>
      <div class="acts" style="max-width:360px;margin-top:14px">
        <div class="btn act-pause">${s.paused ? 'Resume' : 'Pause'} learning</div>
        <div class="btn danger act-reset">Reset</div>
      </div>
    </div>`;
  $(".act-pause").onclick = async () => { await api("/api/draft-style/" + (s.paused ? "resume" : "pause"), {method:"POST"}); renderDraftStyle(); };
  $(".act-reset").onclick = async () => { if (confirm("Reset all learned draft lessons?")) { await api("/api/draft-style/reset", {method:"POST"}); renderDraftStyle(); } };
}
```

- [ ] **Step 5: Style the button**

In `web/style.css`, after the `header h1` rule, add:

```css
#check-now { margin-left:auto; border:1px solid var(--line); background:#fff; border-radius:8px; padding:6px 12px; font:inherit; font-weight:600; cursor:pointer; color:var(--blue); }
#check-now:disabled { opacity:.6; cursor:default; }
```

- [ ] **Step 6: Verify**

Run: `node --check web/app.js` → expect exit 0.
Then with the server running, hard-refresh `http://127.0.0.1:8765`: the header shows "⟳ Check now"; the "Draft style ✎" tab loads (empty state). Clicking "Check now" shows "Checking…", then a toast + reload.

- [ ] **Step 7: Commit**

```bash
git add web/index.html web/app.js web/style.css
git commit -m "feat(drafts): Check now button, draft link, Draft style tab

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Wire the morning run + full sweep + docs

**Files:**
- Modify: `bin/run_morning.sh` (replace inline reply block with the orchestrator)
- Modify: `CLAUDE.md`

- [ ] **Step 1: Replace the inline reply block**

In `bin/run_morning.sh`, replace the entire block that begins with the comment `# --- daily Gmail reply check (best-effort, read-only) ---` and ends at the matching `fi` (the `reply_context.py` + `claude -p` matcher + `apply_replies.py` block) with:

```bash
  # --- daily Gmail reply pipeline: match -> draft -> learn (best-effort) ---
  "$PY" scripts/check_replies.py || echo "WARN: reply pipeline failed; continuing"
```

- [ ] **Step 2: Verify shell parses**

Run: `bash -n bin/run_morning.sh`
Expected: no output (exit 0).

- [ ] **Step 3: Full test sweep**

Run: `.venv/bin/python -m unittest discover -s scripts/tests`
Expected: OK, all tests pass.

- [ ] **Step 4: Update CLAUDE.md**

In `CLAUDE.md`, replace the `reply_context.py` / `apply_replies.py` step-5 lines (added previously) with:

```
.venv/bin/python scripts/check_replies.py   # step 5 -> match Gmail replies, then
#   auto-DRAFT a reply for each new personal reply (claude -p drafter + reviewer
#   subagent; created in Gmail Drafts, never sent), then LEARN from any edits you
#   made to earlier drafts (sent-vs-draft diff -> data/.draft_style.json). The UI
#   "Check now" button runs this same orchestrator on demand.
```

- [ ] **Step 5: Commit**

```bash
git add bin/run_morning.sh CLAUDE.md
git commit -m "feat(drafts): run check_replies orchestrator from the morning job

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Check-now button + background run + status poll → Tasks 5 (endpoints/lock) + 6 (UI). ✓
- One orchestrator shared by morning run + button → Task 4 (`check_replies.run`) + Task 7 (run_morning wiring). ✓
- Auto-draft personal replies, reviewer subagent, never sent → Task 2 (prep/apply) + Task 4 (DRAFTER_PROMPT/TOOLS). ✓
- Draft availability from `outreach.timing` → Task 2 `build_jobs` includes `timing`; DRAFTER_PROMPT uses viewing_window. ✓
- Edit-learning (sent-vs-draft diff, lessons, mark learned) → Task 3 + Task 4 (LEARNER_PROMPT). ✓
- Learned notes fed into future drafts → Task 2 `build_jobs` includes `notes` from `draft_style.note_texts()`. ✓
- Auto-learn, viewable/pausable/resettable (mirror Taste) → Task 1 (`draft_style`) + Task 5 (endpoints) + Task 6 (tab). ✓
- "Draft ready" link on reply cards → Task 6 `draftLine`. ✓
- Skip claude -p when no jobs → Task 4 gating + tests. ✓
- Graceful degradation; Gmail read-only except create_draft; never send → Task 4 `_run_claude` returns bool + prompts; Task 7 `|| echo WARN`. ✓
- Data model `draft` field; new path constants; gitignore → Task 1. ✓
- Tests for deterministic prep/apply/gating/endpoints → Tasks 1–5. ✓

**Placeholder scan:** No TBD/TODO; each code step shows complete code. ✓

**Type consistency:** `draft` shape `{draft_id, text, created_at, learned_from}` is identical in `draft_replies.apply_drafts`, `draft_learn` (reads `text`, sets `learned_from`), and the JS `draftLine`. Job dict keys (`id, thread_id, listing, reply, applicant, timing, notes`) match between `draft_replies.build_jobs` and DRAFTER_PROMPT; learn job keys (`id, thread_id, draft_text`) match between `draft_learn.build_jobs` and LEARNER_PROMPT. Results shapes (`{id:{draft_id,text}}`, `{id:{learned,lessons}}`) match producers/consumers. Endpoint/function names (`api_check_start/status`, `api_draft_style[/_control]`, `_check_lock`, `renderDraftStyle`, `startCheck`, `pollCheck`, `draftLine`) are referenced consistently. ✓
