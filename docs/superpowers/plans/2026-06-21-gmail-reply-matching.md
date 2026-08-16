# Gmail Reply Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Once a day, match Gmail replies to apartments we've reached out to and surface confirmed responses in a new "Replied" tab in the local UI.

**Architecture:** Mirror the existing vision-step pattern — deterministic Python owns `listings.json`; a contained `claude -p` step writes only a scratch file. Flow: `reply_context.py` (prep) → `claude -p` matcher + independent reviewer sub-agent → `apply_replies.py` (apply) → UI confirm. The matcher proposes candidates, a fresh-context reviewer sub-agent validates each on evidence alone, survivors become `reply_candidate`s, and the user confirms each in the UI (final gate).

**Tech Stack:** Python 3 stdlib (no new deps), `unittest`, the existing `http.server`-based `serve_ui.py`, vanilla JS frontend, Claude CLI (`claude -p`) with the Gmail connector.

## Global Constraints

- Python owns the durable store; `claude -p` writes ONLY to a scratch file (`data/.reply_matches.json`). Verbatim repo rule from `store.py` docstring and the vision step in `bin/run_morning.sh`.
- The Gmail step is **read-only**: never send, reply, label, star, or modify any email.
- No new third-party dependencies; stdlib only.
- New per-listing fields are pipeline/user-owned — scout never touches them. Existing listings will NOT have the new keys (defaults only apply to newly-upserted listings), so all readers MUST use `.get()` with a safe default; never index a new key directly.
- Connector dependency: the morning `claude -p` reply step requires the Gmail connector + `Task` tool. If unavailable, the step logs a WARN and the pipeline continues — nothing else breaks.
- Test style: `unittest`, using the `use_temp_data` / `write_store` / `FL` helpers already in `scripts/tests/test_serve_ui.py`. Run tests with `.venv/bin/python -m pytest`.

---

### Task 1: Store schema + paths constants

**Files:**
- Modify: `scripts/applib/store.py:26-62` (PIPELINE_DEFAULTS)
- Modify: `scripts/applib/paths.py` (add two scratch-file constants)
- Test: `scripts/tests/test_replies.py` (new)

**Interfaces:**
- Produces: three new listing fields — `reply` (None | dict), `reply_candidate` (None | dict), `reply_dismissed_threads` (list[str]); `status` may take value `"replied"`. Two path constants: `paths.OUTREACH_CONTEXT_FILE`, `paths.REPLY_MATCHES_FILE`.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_replies.py`:

```python
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib import paths  # noqa: E402
from applib.store import Store, PIPELINE_DEFAULTS  # noqa: E402


def use_temp_data(testcase) -> Path:
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
    saved = {k: getattr(paths, k) for k in
             ("DATA_DIR", "LISTINGS_FILE", "OUTREACH_CONTEXT_FILE", "REPLY_MATCHES_FILE")}
    testcase.addCleanup(lambda: [setattr(paths, k, v) for k, v in saved.items()])
    paths.DATA_DIR = tmp
    paths.LISTINGS_FILE = tmp / "listings.json"
    paths.OUTREACH_CONTEXT_FILE = tmp / ".outreach_context.json"
    paths.REPLY_MATCHES_FILE = tmp / ".reply_matches.json"
    return tmp


def write_store(listings: dict):
    paths.LISTINGS_FILE.write_text(
        json.dumps({"meta": {"schema_version": 1}, "listings": listings}),
        encoding="utf-8")


class Schema(unittest.TestCase):
    def test_new_listing_has_reply_fields(self):
        for key, default in (("reply", None), ("reply_candidate", None),
                             ("reply_dismissed_threads", [])):
            self.assertIn(key, PIPELINE_DEFAULTS)
            self.assertEqual(PIPELINE_DEFAULTS[key], default)

    def test_paths_scratch_constants_exist(self):
        self.assertTrue(str(paths.OUTREACH_CONTEXT_FILE).endswith(".outreach_context.json"))
        self.assertTrue(str(paths.REPLY_MATCHES_FILE).endswith(".reply_matches.json"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest scripts/tests/test_replies.py -v`
Expected: FAIL — `AttributeError: module 'applib.paths' has no attribute 'OUTREACH_CONTEXT_FILE'` (and/or missing PIPELINE_DEFAULTS keys).

- [ ] **Step 3: Add the path constants**

In `scripts/applib/paths.py`, after the `LEARNING_LOG_FILE` line, add:

```python
OUTREACH_CONTEXT_FILE = DATA_DIR / ".outreach_context.json"  # prep for the gmail reply matcher (gitignored)
REPLY_MATCHES_FILE = DATA_DIR / ".reply_matches.json"        # claude -p scratch output (gitignored)
```

- [ ] **Step 4: Add the store fields**

In `scripts/applib/store.py`, inside `PIPELINE_DEFAULTS`, immediately after the `"verification_notes": None,` line, add:

```python
    # Gmail reply matching (set by apply_replies.py / the UI; see the reply spec).
    "reply": None,                  # confirmed reply: {thread_id, gmail_link, from, subject, snippet, received_at, matched_by, confidence, reviewer_reason, confirmed_at}
    "reply_candidate": None,        # pending match awaiting the user's confirm (same shape, no confirmed_at)
    "reply_dismissed_threads": [],  # gmail thread ids rejected as "not a match" — never re-surface
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest scripts/tests/test_replies.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Add the scratch files to .gitignore**

In `.gitignore`, add these lines (check they aren't already covered):

```
data/.outreach_context.json
data/.reply_matches.json
```

- [ ] **Step 7: Commit**

```bash
git add scripts/applib/store.py scripts/applib/paths.py scripts/tests/test_replies.py .gitignore
git commit -m "feat(replies): store fields + scratch paths for gmail reply matching

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `reply_context.py` — deterministic prep

**Files:**
- Create: `scripts/reply_context.py`
- Test: `scripts/tests/test_replies.py` (add `ReplyContext` class)

**Interfaces:**
- Consumes: `Store`, `outreach.render(lst)["subject"]`, `paths.OUTREACH_CONTEXT_FILE`.
- Produces: `build_context(store: Store) -> list[dict]` — one dict per `contacted` listing with keys `id, url, channel, email, subject, decision_at, street, zipcode, city`. CLI writes the list (JSON) to `paths.OUTREACH_CONTEXT_FILE` and prints the count; exit 0 always.

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_replies.py` (and add `import reply_context` near the top imports):

```python
class ReplyContext(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({
            "c": {"id": "c", "status": "contacted", "decision": "outreach",
                  "decision_at": "2026-06-15T08:00:00", "outreach_channel": "email",
                  "outreach_email": "agent@example.ch", "url": "https://x/c",
                  "street": "Seefeldstrasse 10", "zipcode": "8008", "city": "Zürich",
                  "title": "Wohnung", "blurb": ""},
            "n": {"id": "n", "status": "new", "decision": None, "city": "Zürich"},
        })

    def test_only_contacted_listings_included(self):
        import reply_context
        from applib.store import Store
        ctx = reply_context.build_context(Store.load())
        self.assertEqual({c["id"] for c in ctx}, {"c"})

    def test_context_item_has_expected_fields(self):
        import reply_context
        from applib.store import Store
        ctx = reply_context.build_context(Store.load())
        item = ctx[0]
        for k in ("id", "url", "channel", "email", "subject", "decision_at",
                  "street", "zipcode", "city"):
            self.assertIn(k, item)
        self.assertEqual(item["email"], "agent@example.ch")
        self.assertTrue(item["subject"])  # rendered, non-empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest scripts/tests/test_replies.py::ReplyContext -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reply_context'`.

- [ ] **Step 3: Create the script**

Create `scripts/reply_context.py`:

```python
#!/usr/bin/env python3
"""Prep step for the daily Gmail reply matcher.

Emits a small JSON list (data/.outreach_context.json) of the listings we've
reached out to, with just the signals the matcher needs. Keeps the LLM step
cheap and deterministic-bounded. Never touches Gmail.

    python scripts/reply_context.py        # writes the context file, prints count
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import outreach  # noqa: E402  (scripts/outreach.py)
from applib import paths  # noqa: E402
from applib.store import Store  # noqa: E402


def _is_contacted(lst: dict) -> bool:
    return lst.get("status") == "contacted" or lst.get("decision") == "outreach"


def build_context(store: Store) -> list[dict]:
    """One dict per contacted listing with the matcher's input signals."""
    out: list[dict] = []
    for lid, lst in store.listings.items():
        if not _is_contacted(lst):
            continue
        try:
            subject = outreach.render(lst).get("subject")
        except Exception:
            subject = None
        out.append({
            "id": lid,
            "url": lst.get("url"),
            "channel": lst.get("outreach_channel"),
            "email": lst.get("outreach_email"),
            "subject": subject,
            "decision_at": lst.get("decision_at"),
            "street": lst.get("street"),
            "zipcode": lst.get("zipcode"),
            "city": lst.get("city"),
        })
    return out


def main() -> int:
    ctx = build_context(Store.load())
    paths.OUTREACH_CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.OUTREACH_CONTEXT_FILE.write_text(
        json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"reply_context: {len(ctx)} contacted listing(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest scripts/tests/test_replies.py::ReplyContext -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Smoke-run against real data**

Run: `.venv/bin/python scripts/reply_context.py`
Expected: prints `reply_context: N contacted listing(s)` and writes `data/.outreach_context.json`.

- [ ] **Step 6: Commit**

```bash
git add scripts/reply_context.py scripts/tests/test_replies.py
git commit -m "feat(replies): reply_context.py prep step for the gmail matcher

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `apply_replies.py` — deterministic apply

**Files:**
- Create: `scripts/apply_replies.py`
- Test: `scripts/tests/test_replies.py` (add `ApplyReplies` class)

**Interfaces:**
- Consumes: `Store`, `paths.REPLY_MATCHES_FILE`.
- Produces: `apply_matches(store: Store, matches: dict) -> dict` returning `{"added": int, "skipped": int}`. Sets `lst["reply_candidate"]` for accepted matches and calls `store.save()` when anything changed. CLI reads `REPLY_MATCHES_FILE` and applies.

Rules: a thread maps to at most one listing (highest `confidence` wins on collision); skip a thread already confirmed (`reply.thread_id`) or dismissed (`reply_dismissed_threads`) on ANY listing; never overwrite an existing `reply_candidate` with the same `thread_id`.

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_replies.py`:

```python
class ApplyReplies(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({
            "a": {"id": "a", "status": "contacted"},
            "b": {"id": "b", "status": "contacted"},
        })

    def _m(self, **kw):
        base = {"thread_id": "T1", "gmail_link": "https://mail/T1", "from": "x@y.ch",
                "subject": "Re: Anfrage", "snippet": "Hallo", "received_at": "2026-06-20T09:00:00",
                "matched_by": "email", "confidence": 0.9, "reviewer_reason": "sender matches"}
        base.update(kw)
        return base

    def test_adds_candidate(self):
        import apply_replies
        from applib.store import Store
        s = Store.load()
        res = apply_replies.apply_matches(s, {"a": self._m()})
        self.assertEqual(res["added"], 1)
        self.assertEqual(Store.load().listings["a"]["reply_candidate"]["thread_id"], "T1")

    def test_idempotent_same_thread_not_readded(self):
        import apply_replies
        from applib.store import Store
        apply_replies.apply_matches(Store.load(), {"a": self._m()})
        res = apply_replies.apply_matches(Store.load(), {"a": self._m()})
        self.assertEqual(res["added"], 0)

    def test_thread_collision_highest_confidence_wins(self):
        import apply_replies
        from applib.store import Store
        res = apply_replies.apply_matches(Store.load(), {
            "a": self._m(confidence=0.7),
            "b": self._m(confidence=0.95),
        })
        self.assertEqual(res["added"], 1)
        loaded = Store.load().listings
        self.assertIsNotNone(loaded["b"]["reply_candidate"])
        self.assertIsNone(loaded["a"].get("reply_candidate"))

    def test_skips_dismissed_thread(self):
        import apply_replies
        from applib.store import Store
        s = Store.load()
        s.listings["a"]["reply_dismissed_threads"] = ["T1"]
        s.save()
        res = apply_replies.apply_matches(Store.load(), {"a": self._m()})
        self.assertEqual(res["added"], 0)

    def test_skips_already_confirmed_thread(self):
        import apply_replies
        from applib.store import Store
        s = Store.load()
        s.listings["a"]["reply"] = {"thread_id": "T1"}
        s.save()
        res = apply_replies.apply_matches(Store.load(), {"b": self._m()})
        self.assertEqual(res["added"], 0)

    def test_unknown_listing_skipped(self):
        import apply_replies
        from applib.store import Store
        res = apply_replies.apply_matches(Store.load(), {"zzz": self._m()})
        self.assertEqual(res, {"added": 0, "skipped": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest scripts/tests/test_replies.py::ApplyReplies -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apply_replies'`.

- [ ] **Step 3: Create the script**

Create `scripts/apply_replies.py`:

```python
#!/usr/bin/env python3
"""Apply the Gmail matcher's scratch output (data/.reply_matches.json) onto the
durable store as reply_candidates awaiting the user's confirm in the UI.

Idempotent and conservative: one thread -> at most one listing, never re-adds a
thread already confirmed or dismissed anywhere, never overwrites a pending
candidate for the same thread.

    python scripts/apply_replies.py        # reads REPLY_MATCHES_FILE, applies
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import paths  # noqa: E402
from applib.store import Store  # noqa: E402

_CANDIDATE_FIELDS = ("thread_id", "gmail_link", "from", "subject", "snippet",
                     "received_at", "matched_by", "confidence", "reviewer_reason")


def _claimed_threads(store: Store) -> set[str]:
    """Thread ids already confirmed or dismissed on ANY listing."""
    claimed: set[str] = set()
    for lst in store.listings.values():
        rep = lst.get("reply")
        if rep and rep.get("thread_id"):
            claimed.add(rep["thread_id"])
        for tid in (lst.get("reply_dismissed_threads") or []):
            claimed.add(tid)
    return claimed


def apply_matches(store: Store, matches: dict) -> dict:
    added = skipped = 0

    # 1 thread -> 1 listing: on collision keep the highest-confidence proposal.
    by_thread: dict[str, tuple[str, dict]] = {}
    for lid, m in (matches or {}).items():
        if lid not in store.listings or not (m or {}).get("thread_id"):
            skipped += 1
            continue
        tid = m["thread_id"]
        cur = by_thread.get(tid)
        if cur is None or (m.get("confidence") or 0) > (cur[1].get("confidence") or 0):
            if cur is not None:
                skipped += 1  # the loser of the collision
            by_thread[tid] = (lid, m)
        else:
            skipped += 1

    claimed = _claimed_threads(store)
    changed = False
    for tid, (lid, m) in by_thread.items():
        lst = store.listings[lid]
        existing = lst.get("reply_candidate")
        if tid in claimed or (existing and existing.get("thread_id") == tid):
            skipped += 1
            continue
        lst["reply_candidate"] = {k: m.get(k) for k in _CANDIDATE_FIELDS}
        added += 1
        changed = True

    if changed:
        store.save()
    return {"added": added, "skipped": skipped}


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
    print(f"apply_replies: added {res['added']}, skipped {res['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest scripts/tests/test_replies.py::ApplyReplies -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/apply_replies.py scripts/tests/test_replies.py
git commit -m "feat(replies): apply_replies.py merges matcher output into the store

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: serve_ui confirm/reject endpoints

**Files:**
- Modify: `scripts/serve_ui.py` (add two API functions near `api_reset`, add two POST routes in `do_POST`)
- Test: `scripts/tests/test_serve_ui.py` (add `ReplyMutations` class)

**Interfaces:**
- Consumes: `Store`, `paths.now_iso()`.
- Produces: `api_reply_confirm(store, listing_id) -> dict`, `api_reply_reject(store, listing_id) -> dict`. Routes `POST /api/reply/confirm/<id>` and `POST /api/reply/reject/<id>`.

- [ ] **Step 1: Write the failing test**

Add to `scripts/tests/test_serve_ui.py`:

```python
class ReplyMutations(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        cand = {"thread_id": "T9", "gmail_link": "https://mail/T9", "from": "a@b.ch",
                "subject": "Re", "snippet": "hi", "received_at": "2026-06-20T09:00:00",
                "matched_by": "email", "confidence": 0.9, "reviewer_reason": "ok"}
        write_store({"a": FL(id="a", status="contacted", reply_candidate=cand,
                             reply=None, reply_dismissed_threads=[])})

    def test_confirm_moves_candidate_to_reply_and_sets_status(self):
        serve_ui.api_reply_confirm(Store.load(), "a")
        l = Store.load().listings["a"]
        self.assertIsNone(l["reply_candidate"])
        self.assertEqual(l["reply"]["thread_id"], "T9")
        self.assertTrue(l["reply"]["confirmed_at"])
        self.assertEqual(l["status"], "replied")

    def test_reject_clears_candidate_and_records_dismissed(self):
        serve_ui.api_reply_reject(Store.load(), "a")
        l = Store.load().listings["a"]
        self.assertIsNone(l["reply_candidate"])
        self.assertIn("T9", l["reply_dismissed_threads"])

    def test_confirm_without_candidate_errors(self):
        write_store({"b": FL(id="b")})
        self.assertEqual(serve_ui.api_reply_confirm(Store.load(), "b"),
                         {"ok": False, "error": "no candidate"})

    def test_unknown_id_errors(self):
        self.assertEqual(serve_ui.api_reply_confirm(Store.load(), "nope"),
                         {"ok": False, "error": "unknown id"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest scripts/tests/test_serve_ui.py::ReplyMutations -v`
Expected: FAIL — `AttributeError: module 'serve_ui' has no attribute 'api_reply_confirm'`.

- [ ] **Step 3: Add the API functions**

In `scripts/serve_ui.py`, immediately after the `api_reset` function (before `def api_learning`), add:

```python
def api_reply_confirm(store: Store, listing_id: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    cand = lst.get("reply_candidate")
    if not cand:
        return {"ok": False, "error": "no candidate"}
    confirmed = dict(cand)
    confirmed["confirmed_at"] = paths.now_iso()
    lst["reply"] = confirmed
    lst["reply_candidate"] = None
    prev = lst.get("status")
    lst["status"] = "replied"
    lst.setdefault("status_log", []).append(
        {"at": paths.now_iso(), "from": prev, "to": "replied", "note": "reply confirmed via UI"})
    store.save()
    return {"ok": True}


def api_reply_reject(store: Store, listing_id: str) -> dict:
    lst = store.listings.get(listing_id)
    if not lst:
        return {"ok": False, "error": "unknown id"}
    cand = lst.get("reply_candidate")
    if not cand:
        return {"ok": False, "error": "no candidate"}
    tid = cand.get("thread_id")
    if tid:
        dismissed = lst.setdefault("reply_dismissed_threads", [])
        if tid not in dismissed:
            dismissed.append(tid)
    lst["reply_candidate"] = None
    store.save()
    return {"ok": True}
```

- [ ] **Step 4: Add the POST routes**

In `scripts/serve_ui.py`, in `do_POST`, immediately after the `/api/reset/` route block, add:

```python
            if u.path.startswith("/api/reply/confirm/"):
                return self._send_json(api_reply_confirm(Store.load(), u.path.split("/")[-1]))
            if u.path.startswith("/api/reply/reject/"):
                return self._send_json(api_reply_reject(Store.load(), u.path.split("/")[-1]))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest scripts/tests/test_serve_ui.py::ReplyMutations -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add scripts/serve_ui.py scripts/tests/test_serve_ui.py
git commit -m "feat(replies): confirm/reject reply endpoints in serve_ui

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: "Replied" tab + card UI

**Files:**
- Modify: `web/index.html` (add tab button)
- Modify: `web/app.js` (tabFilter, load, renderReplied, renderState, confirmReply, rejectReply, badge)
- Modify: `web/style.css` (reply styles)

**Interfaces:**
- Consumes: listing objects from `/api/listings` now carry `reply` / `reply_candidate`; endpoints `POST /api/reply/confirm/<id>` and `/api/reply/reject/<id>` from Task 4.
- Produces: a "Replied" tab with "Needs confirm" + "Confirmed" zones, a count badge, per-card reply rendering.

This task is UI; it's verified by serving the page and exercising it, not by unit tests (the server reads `web/` from disk on every request, so no restart needed).

- [ ] **Step 1: Add the tab button**

In `web/index.html`, between the Contacted and Declined buttons, add:

```html
      <button data-tab="replied">Replied <span id="reply-badge" hidden></span></button>
```

- [ ] **Step 2: Extend tabFilter**

In `web/app.js`, in `tabFilter`, add this line as the first check inside the function (before the `contacted` check):

```javascript
  if (TAB === "replied") return !!(l.reply_candidate || l.reply || l.status === "replied");
```

- [ ] **Step 3: Track the full set + badge, and branch the Replied tab in load()**

In `web/app.js`, add a module-level variable near the top (after `let TAB = "triage";`):

```javascript
let LAST = [];
```

Then in `load()`, replace this block:

```javascript
  const rows = data.listings.filter(tabFilter);
  const groups = {};
```

with:

```javascript
  LAST = data.listings;
  const rows = data.listings.filter(tabFilter);
  const content0 = $("#content");
  if (TAB === "replied"){ renderReplied(rows, content0); refreshReplyBadge(); return; }
  const groups = {};
```

And at the very end of `load()` (after the `for (const k of order)` loop closes), add:

```javascript
  refreshReplyBadge();
```

- [ ] **Step 4: Add renderReplied + refreshReplyBadge**

In `web/app.js`, add after the `load()` function:

```javascript
function refreshReplyBadge(){
  const b = $("#reply-badge");
  if (!b) return;
  const n = LAST.filter(l => l.reply_candidate && !l.reply).length;
  b.textContent = n ? String(n) : "";
  b.hidden = !n;
}

function renderReplied(rows, content){
  content.innerHTML = "";
  const pending = rows.filter(l => l.reply_candidate && !l.reply);
  const done = rows.filter(l => l.reply);
  if (!pending.length && !done.length){ content.innerHTML = '<p class="muted">No replies yet.</p>'; return; }
  const sec = (title, list) => {
    if (!list.length) return;
    const s = document.createElement("div"); s.className = "section";
    s.innerHTML = `<h2>${title} <span class="muted">${list.length}</span></h2>`;
    const g = document.createElement("div"); g.className = "grid";
    list.forEach(l => g.appendChild(card(l)));
    s.appendChild(g); content.appendChild(s);
  };
  sec("📨 Needs confirm", pending);
  sec("✓ Confirmed", done);
}
```

- [ ] **Step 5: Render reply state on cards**

In `web/app.js`, in `renderState`, add these two blocks at the very top of the function (right after `const box = el.querySelector(".state");`), so reply state takes priority:

```javascript
  if (l.reply){
    box.innerHTML = `<div class="done">✓ Replied${l.reply.received_at ? (" · " + fmtDate(l.reply.received_at)) : ""}</div>
      <a class="link" href="${esc(l.reply.gmail_link || "#")}" target="_blank" rel="noopener">open in Gmail</a>`;
    return;
  }
  if (l.reply_candidate){
    const c = l.reply_candidate;
    box.innerHTML = `<div class="reply-cand">
        <div class="meta"><b>Reply found</b>${c.from ? (" · " + esc(c.from)) : ""} — confirm?</div>
        <div class="snip">${esc(c.snippet || "")}</div>
        <a class="link" href="${esc(c.gmail_link || "#")}" target="_blank" rel="noopener">open in Gmail</a>
        <div class="acts" style="margin-top:6px">
          <div class="btn ghost act-noreply" style="flex:0 0 42%">✗ Not a match</div>
          <div class="btn primary act-confirmreply">✓ Confirm reply</div></div></div>`;
    box.querySelector(".act-confirmreply").onclick = () => confirmReply(l, el);
    box.querySelector(".act-noreply").onclick = () => rejectReply(l, el);
    return;
  }
```

- [ ] **Step 6: Add confirmReply + rejectReply**

In `web/app.js`, add after the `confirmReached` function:

```javascript
async function confirmReply(l, el){
  await api("/api/reply/confirm/" + l.id, {method:"POST"});
  l.reply = Object.assign({confirmed_at: new Date().toISOString()}, l.reply_candidate);
  l.reply_candidate = null; l.status = "replied";
  if (TAB === "replied") load(); else { renderState(el, l); refreshReplyBadge(); }
  toast("Reply confirmed");
}

async function rejectReply(l, el){
  await api("/api/reply/reject/" + l.id, {method:"POST"});
  l.reply_candidate = null;
  if (TAB === "replied") load(); else { renderState(el, l); refreshReplyBadge(); }
  toast("Dismissed");
}
```

- [ ] **Step 7: Add styles**

In `web/style.css`, after the `.note` rule (and the `.msg-*` rules added earlier), add:

```css
.reply-cand { border-left:3px solid #d97706; padding-left:8px; }
.snip { font-size:11px; color:var(--muted); background:#f4f4f6; border-radius:6px; padding:6px 8px; margin:4px 0; max-height:54px; overflow:hidden; }
#reply-badge { background:#d97706; color:#fff; border-radius:999px; font-size:10px; padding:1px 6px; margin-left:2px; }
#reply-badge[hidden] { display:none; }
```

- [ ] **Step 8: Verify JS parses and manually exercise**

Run: `node --check web/app.js` → expect no output (exit 0).

Then seed a candidate and check the page by hand:

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "scripts")
from applib.store import Store
s = Store.load()
lid = next((i for i, l in s.listings.items() if l.get("status") == "contacted"), None)
print("seeding into:", lid)
if lid:
    s.listings[lid]["reply_candidate"] = {
        "thread_id": "SEED1", "gmail_link": "https://mail.google.com/",
        "from": "test-agent@example.ch", "subject": "Re: Anfrage Mietwohnung",
        "snippet": "Guten Tag, die Wohnung ist noch verfügbar — möchten Sie einen Besichtigungstermin?",
        "received_at": "2026-06-20T09:00:00", "matched_by": "email",
        "confidence": 0.9, "reviewer_reason": "sender + subject match"}
    s.save(); print("seeded — open the Replied tab")
PY
```

Expected: with the server running (`http://127.0.0.1:8765`), the **Replied** tab shows a "📨 Needs confirm" section with the card, an amber left-border, the snippet, an "open in Gmail" link, and Confirm / Not a match buttons; the tab shows a count badge. Clicking **Confirm reply** moves it to "✓ Confirmed"; clicking **Not a match** removes it. Clean up the seed afterward by clicking "Not a match", or re-run with `reply_candidate=None`.

- [ ] **Step 9: Commit**

```bash
git add web/index.html web/app.js web/style.css
git commit -m "feat(replies): Replied tab, candidate cards, confirm/reject UI

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Wire the daily check into the morning run

**Files:**
- Modify: `bin/run_morning.sh` (add a reply-check block after `digest.py`)

**Interfaces:**
- Consumes: `scripts/reply_context.py`, `scripts/apply_replies.py`, the Gmail connector + `Task` tool via `claude -p`.
- Produces: a daily best-effort reply-matching step. Read-only on Gmail; degrades gracefully.

No unit test (shells out to Claude + Gmail). Verified by a manual dry run.

- [ ] **Step 1: Add the reply-check block**

In `bin/run_morning.sh`, immediately after the `"$PY" scripts/digest.py` line and before `echo "=== done $(date) ==="`, insert:

```bash
  # --- daily Gmail reply check (best-effort, read-only) ---
  "$PY" scripts/reply_context.py
  if "$PY" - <<'PYEOF'
import json, sys, pathlib
p = pathlib.Path("data/.outreach_context.json")
ctx = json.loads(p.read_text()) if p.exists() else []
sys.exit(0 if ctx else 1)
PYEOF
  then
    echo "--- gmail reply matching via Claude ---"
    rm -f "$ROOT/data/.reply_matches.json"
    claude -p "Match Gmail replies to apartment outreach. Read data/.outreach_context.json (a JSON list; each item is one apartment we contacted, with id, subject, email, channel, street, zipcode, city, decision_at, url). For EACH apartment, use the Gmail search tool to find candidate reply threads received on or after its decision_at: for email-channel items search by the sender email and/or our subject line; for form-channel items (email is null) search by the street plus locality. For EACH candidate, dispatch a SEPARATE independent reviewer subagent (Task tool, subagent_type general-purpose) giving it ONLY the email's from/subject/snippet/received-date and the apartment's address/email/subject/decision_at, asking it to answer strictly whether that email is a genuine reply to THAT apartment outreach (yes/no, one-line reason, confidence 0..1). Keep ONLY matches the reviewer approves with confidence >= 0.6. Ignore automated or no-reply acknowledgements. Build gmail_link as https://mail.google.com/mail/u/0/#all/<thread_id>. Write the approved matches as a JSON object mapping apartment id -> {thread_id, gmail_link, from, subject, snippet, received_at, matched_by:'email'|'form', confidence, reviewer_reason} to data/.reply_matches.json (write {} if none). Do NOT send, reply to, label, star, or modify any email. Do nothing else." \
      --allowedTools "mcp__claude_ai_Gmail__search_threads" "mcp__claude_ai_Gmail__get_thread" "Task" "Read" "Write" \
      --permission-mode acceptEdits \
      && "$PY" scripts/apply_replies.py \
      || echo "WARN: gmail reply step failed; continuing"
  else
    echo "no contacted listings — skipping reply check"
  fi
```

- [ ] **Step 2: Dry-run the deterministic halves**

Run:
```bash
.venv/bin/python scripts/reply_context.py
.venv/bin/python scripts/apply_replies.py
```
Expected: `reply_context: N contacted listing(s)` then `apply_replies: no matches file — nothing to do` (or `added 0, skipped 0` if a stale matches file exists).

- [ ] **Step 3: Verify the shell script parses**

Run: `bash -n bin/run_morning.sh`
Expected: no output (exit 0).

- [ ] **Step 4: Live end-to-end check of the matcher (optional, real Gmail)**

Run the exact `claude -p ...` command from Step 1 in the project dir, then `.venv/bin/python scripts/apply_replies.py`.
Expected: writes `data/.reply_matches.json` (likely `{}` today, since there's no email outreach yet) and `apply_replies` reports `added 0`. No errors. If the connector is unavailable the command prints a WARN and the pipeline-equivalent continues.

- [ ] **Step 5: Commit**

```bash
git add bin/run_morning.sh
git commit -m "feat(replies): daily gmail reply check in the morning run

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Full test sweep + docs note

**Files:**
- Modify: `CLAUDE.md` (one line documenting the reply step, if a pipeline section exists)

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/python -m pytest scripts/tests/ -q`
Expected: all tests pass (existing + the new `test_replies.py` and `ReplyMutations`).

- [ ] **Step 2: Add a CLAUDE.md note**

If `CLAUDE.md` documents the morning pipeline steps, add one line after the digest step describing: "Gmail reply check — `reply_context.py` → `claude -p` matcher + independent reviewer subagent → `apply_replies.py`; surfaces `reply_candidate`s in the UI's Replied tab; read-only on Gmail." Match the surrounding format.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note the daily gmail reply check in the pipeline

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Daily Gmail check via `claude -p` + connector → Task 6. ✓
- Email + form best-effort matching → matcher prompt (Task 6) + signals documented. ✓
- Independent reviewer agent → Task 6 prompt dispatches a separate Task subagent per candidate; confidence gate ≥0.6. ✓
- Always-confirm (user is final gate) → `reply_candidate` never flips `status`; only `api_reply_confirm` does (Tasks 3, 4, 5). ✓
- New "Replied" tab + badge + thread link → Task 5. ✓
- Data model (`reply`, `reply_candidate`, `reply_dismissed_threads`, `status="replied"`) → Task 1. ✓
- Python-owns-store / Claude writes scratch only → Tasks 2/3 deterministic; Task 6 writes only `.reply_matches.json`. ✓
- Idempotency / dedup / dismissed-skip → Task 3 + tests. ✓
- Graceful degradation + manual fallback → Task 6 `|| echo WARN`; manual = run the `claude -p` block by hand. ✓
- Tests for deterministic pieces + endpoints → Tasks 1–4. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**Type consistency:** `reply_candidate` shape is identical across `apply_matches` (`_CANDIDATE_FIELDS`), `api_reply_confirm` (copies + adds `confirmed_at`), and the JS renderer (`thread_id`/`gmail_link`/`from`/`subject`/`snippet`/`received_at`/`confidence`/`reviewer_reason`). `build_context` keys (`id/url/channel/email/subject/decision_at/street/zipcode/city`) match what the matcher prompt consumes. Function names (`build_context`, `apply_matches`, `api_reply_confirm`, `api_reply_reject`, `renderReplied`, `refreshReplyBadge`, `confirmReply`, `rejectReply`) are referenced consistently. ✓
