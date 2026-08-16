from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applib import learning  # noqa: E402
from applib import paths  # noqa: E402
from applib.store import Store  # noqa: E402
import serve_ui  # noqa: E402


def use_temp_data(testcase) -> Path:
    """Point applib.paths at a fresh temp data dir for the duration of a test."""
    tmp = Path(tempfile.mkdtemp())
    testcase.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
    saved = {k: getattr(paths, k) for k in
             ("DATA_DIR", "LISTINGS_FILE", "LEARNED_PREFS_FILE",
              "LEARNING_LOG_FILE", "PHOTOS_DIR")}
    testcase.addCleanup(lambda: [setattr(paths, k, v) for k, v in saved.items()])
    paths.DATA_DIR = tmp
    paths.LISTINGS_FILE = tmp / "listings.json"
    paths.LEARNED_PREFS_FILE = tmp / ".learned_prefs.json"
    paths.LEARNING_LOG_FILE = tmp / ".learning_log.jsonl"
    paths.PHOTOS_DIR = tmp / "photos"
    return tmp


def write_store(listings: dict):
    paths.LISTINGS_FILE.write_text(
        json.dumps({"meta": {"schema_version": 1}, "listings": listings}),
        encoding="utf-8")


def FL(**kw):
    base = {"id": "x", "status": "new", "gate_status": "passed", "bucket": "A",
            "decision": None, "hood_category": "hipsters", "transit_min": 20,
            "rent_net": 2000, "size_sqm": 60, "rooms": 2.5, "city": "Zürich",
            "street": "Teststr", "zipcode": "8000", "photos": [], "title": "t",
            "url": "https://example.com"}
    base.update(kw)
    return base


class ListEndpoint(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({
            "a": FL(id="a", bucket="A"),
            "r": FL(id="r", gate_status="rejected"),
            "c": FL(id="c", status="closed"),
            "b": FL(id="b", bucket="B", hood_category="suits"),
        })

    def test_default_excludes_rejected_and_closed(self):
        out = serve_ui.api_listings(Store.load(), {})
        ids = {l["id"] for l in out["listings"]}
        self.assertEqual(ids, {"a", "b"})

    def test_include_rejected_param(self):
        out = serve_ui.api_listings(Store.load(), {"include_rejected": "1"})
        self.assertIn("r", {l["id"] for l in out["listings"]})

    def test_each_listing_has_score(self):
        out = serve_ui.api_listings(Store.load(), {})
        self.assertTrue(all("score" in l for l in out["listings"]))

    def test_filter_by_bucket(self):
        out = serve_ui.api_listings(Store.load(), {"bucket": "B"})
        self.assertEqual({l["id"] for l in out["listings"]}, {"b"})


class MessageEndpoint(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({"a": FL(id="a", street="Seefeldstrasse", zipcode="8008",
                             outreach_channel="onsite_now")})

    def test_message_has_subject_body_and_channel(self):
        out = serve_ui.api_message(Store.load(), "a")
        self.assertTrue(out["subject"])
        self.assertTrue(out["body"])
        self.assertEqual(out["channel"], "onsite_now")
        self.assertEqual(out["url"], "https://example.com")

    def test_unknown_id_returns_none(self):
        self.assertIsNone(serve_ui.api_message(Store.load(), "nope"))


class Mutations(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({"a": FL(id="a")})

    def test_reach_out_marks_contacted_and_decided(self):
        serve_ui.api_reach_out(Store.load(), "a")
        l = Store.load().listings["a"]
        self.assertEqual(l["decision"], "outreach")
        self.assertEqual(l["status"], "contacted")
        self.assertTrue(l.get("status_log"))

    def test_decline_records_reasons_and_note(self):
        serve_ui.api_decline(Store.load(), "a", ["too_expensive", "wrong_area"], "busy street")
        l = Store.load().listings["a"]
        self.assertEqual(l["decision"], "deprioritized")
        self.assertEqual(l["decline_reasons"], ["too_expensive", "wrong_area"])
        self.assertIn("busy street", l["decision_note"])

    def test_reset_clears_decision(self):
        serve_ui.api_reach_out(Store.load(), "a")
        serve_ui.api_reset(Store.load(), "a")
        l = Store.load().listings["a"]
        self.assertIsNone(l["decision"])
        self.assertEqual(l["status"], "new")

    def test_decline_triggers_retune_overlay_after_threshold(self):
        write_store({f"n{i}": FL(id=f"n{i}") for i in range(10)})
        for i in range(10):
            serve_ui.api_decline(Store.load(), f"n{i}", ["too_expensive"], "")
        self.assertIn("scoring", learning._load())

    def test_reset_after_decline_clears_reasons_no_spurious_log(self):
        serve_ui.api_decline(Store.load(), "a", ["too_expensive"], "x")
        serve_ui.api_reset(Store.load(), "a")
        l = Store.load().listings["a"]
        self.assertIsNone(l["decline_reasons"])
        self.assertIsNone(l["decision_note"])
        # declining never set status to contacted, so reset must not log a revert
        self.assertFalse(l.get("status_log"))

    def test_unknown_id_returns_error(self):
        for fn in (serve_ui.api_reach_out, serve_ui.api_reset):
            self.assertEqual(fn(Store.load(), "nope"),
                             {"ok": False, "error": "unknown id"})
        self.assertEqual(serve_ui.api_decline(Store.load(), "nope", [], ""),
                         {"ok": False, "error": "unknown id"})


class LearningEndpoint(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({"a": FL(id="a")})

    def test_status_shape(self):
        out = serve_ui.api_learning(Store.load())
        self.assertIn("baseline", out)
        self.assertIn("paused", out)

    def test_pause_and_reset(self):
        serve_ui.api_learning_control("pause")
        self.assertTrue(learning.is_paused())
        serve_ui.api_learning_control("resume")
        self.assertFalse(learning.is_paused())
        serve_ui.api_learning_control("reset")
        self.assertFalse(paths.LEARNED_PREFS_FILE.exists())


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


class Photos(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({"a": FL(id="a", photos=["https://cdn.example/x.jpg"])})

    def test_local_file_preferred(self):
        d = paths.PHOTOS_DIR / "a"; d.mkdir(parents=True)
        (d / "01.jpg").write_bytes(b"JPEGDATA")
        kind, payload = serve_ui.resolve_photo(Store.load(), "a", 0)
        self.assertEqual(kind, "file")
        self.assertTrue(str(payload).endswith("01.jpg"))

    def test_remote_url_fallback(self):
        kind, payload = serve_ui.resolve_photo(Store.load(), "a", 0)
        self.assertEqual(kind, "redirect")
        self.assertEqual(payload, "https://cdn.example/x.jpg")

    def test_missing_returns_none(self):
        kind, payload = serve_ui.resolve_photo(Store.load(), "a", 5)
        self.assertEqual(kind, "none")


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


if __name__ == "__main__":
    unittest.main()
