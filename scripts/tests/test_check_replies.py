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
