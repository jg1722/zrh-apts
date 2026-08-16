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
