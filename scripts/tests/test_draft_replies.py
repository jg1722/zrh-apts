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
