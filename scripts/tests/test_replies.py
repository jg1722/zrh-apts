from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import apply_replies  # noqa: E402
import reply_context  # noqa: E402
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
        ctx = reply_context.build_context(Store.load())
        self.assertEqual({c["id"] for c in ctx}, {"c"})

    def test_context_item_has_expected_fields(self):
        ctx = reply_context.build_context(Store.load())
        item = ctx[0]
        for k in ("id", "url", "channel", "email", "subject", "decision_at",
                  "street", "zipcode", "city"):
            self.assertIn(k, item)
        self.assertEqual(item["email"], "agent@example.ch")
        self.assertTrue(item["subject"])  # rendered, non-empty


class ApplyReplies(unittest.TestCase):
    def setUp(self):
        use_temp_data(self)
        write_store({
            "a": {"id": "a", "status": "contacted"},
            "b": {"id": "b", "status": "contacted"},
        })

    def _rep(self, **kw):
        base = {"thread_id": "T1", "gmail_link": "https://mail/T1", "from": "x@y.ch",
                "subject": "Re: Anfrage", "snippet": "Hallo", "received_at": "2026-06-20T09:00:00",
                "matched_by": "email", "confidence": 0.9, "reviewer_reason": "sender matches",
                "automated": False, "summary": "Agency invites a viewing.",
                "next_steps": "Reply to book a slot."}
        base.update(kw)
        return {"reply": base}

    def _conf(self, **kw):
        base = {"thread_id": "C1", "gmail_link": "https://mail/C1", "from": "no-reply@portal.ch",
                "subject": "Bestätigung zum Versand Ihrer Kontaktanfrage", "snippet": "Eingegangen",
                "received_at": "2026-06-20T08:00:00", "reviewer_reason": "receipt for this address"}
        base.update(kw)
        return {"confirmation": base}

    def test_adds_candidate(self):
        s = Store.load()
        res = apply_replies.apply_matches(s, {"a": self._rep()})
        self.assertEqual(res["replies"], 1)
        self.assertEqual(Store.load().listings["a"]["reply_candidate"]["thread_id"], "T1")

    def test_idempotent_same_thread_not_readded(self):
        apply_replies.apply_matches(Store.load(), {"a": self._rep()})
        res = apply_replies.apply_matches(Store.load(), {"a": self._rep()})
        self.assertEqual(res["replies"], 0)

    def test_thread_collision_highest_confidence_wins(self):
        res = apply_replies.apply_matches(Store.load(), {
            "a": self._rep(confidence=0.7),
            "b": self._rep(confidence=0.95),
        })
        self.assertEqual(res["replies"], 1)
        loaded = Store.load().listings
        self.assertIsNotNone(loaded["b"]["reply_candidate"])
        self.assertIsNone(loaded["a"].get("reply_candidate"))

    def test_skips_dismissed_thread(self):
        s = Store.load()
        s.listings["a"]["reply_dismissed_threads"] = ["T1"]
        s.save()
        res = apply_replies.apply_matches(Store.load(), {"a": self._rep()})
        self.assertEqual(res["replies"], 0)

    def test_skips_already_confirmed_thread(self):
        s = Store.load()
        s.listings["a"]["reply"] = {"thread_id": "T1"}
        s.save()
        res = apply_replies.apply_matches(Store.load(), {"b": self._rep()})
        self.assertEqual(res["replies"], 0)

    def test_unknown_listing_skipped(self):
        res = apply_replies.apply_matches(Store.load(), {"zzz": self._rep()})
        self.assertEqual(res, {"confirmations": 0, "replies": 0, "enriched": 0, "skipped": 1})

    def test_reply_carries_summary_and_next_steps(self):
        apply_replies.apply_matches(Store.load(), {"a": self._rep()})
        cand = Store.load().listings["a"]["reply_candidate"]
        self.assertEqual(cand["summary"], "Agency invites a viewing.")
        self.assertEqual(cand["next_steps"], "Reply to book a slot.")
        self.assertEqual(cand["automated"], False)

    def test_enriches_existing_candidate_missing_summary(self):
        s = Store.load()
        s.listings["a"]["reply_candidate"] = {"thread_id": "T1", "from": "x@y.ch"}  # pre-summary
        s.save()
        res = apply_replies.apply_matches(Store.load(), {"a": self._rep()})
        self.assertEqual(res["enriched"], 1)
        self.assertEqual(res["replies"], 0)
        cand = Store.load().listings["a"]["reply_candidate"]
        self.assertEqual(cand["summary"], "Agency invites a viewing.")

    def test_enriches_existing_confirmed_reply_missing_summary(self):
        s = Store.load()
        s.listings["a"]["reply"] = {"thread_id": "T1", "from": "x@y.ch"}  # confirmed, pre-summary
        s.save()
        res = apply_replies.apply_matches(Store.load(), {"a": self._rep()})
        self.assertEqual(res["enriched"], 1)
        self.assertEqual(Store.load().listings["a"]["reply"]["next_steps"], "Reply to book a slot.")

    def test_confirmation_auto_captured(self):
        res = apply_replies.apply_matches(Store.load(), {"a": self._conf()})
        self.assertEqual(res["confirmations"], 1)
        conf = Store.load().listings["a"]["confirmation"]
        self.assertEqual(conf["thread_id"], "C1")
        self.assertTrue(conf["captured_at"])

    def test_confirmation_idempotent(self):
        apply_replies.apply_matches(Store.load(), {"a": self._conf()})
        res = apply_replies.apply_matches(Store.load(), {"a": self._conf()})
        self.assertEqual(res["confirmations"], 0)

    def test_confirmation_set_once_not_overwritten_by_other_thread(self):
        apply_replies.apply_matches(Store.load(), {"a": self._conf(thread_id="C1")})
        res = apply_replies.apply_matches(Store.load(), {"a": self._conf(thread_id="C2")})
        self.assertEqual(res["confirmations"], 0)
        self.assertEqual(Store.load().listings["a"]["confirmation"]["thread_id"], "C1")

    def test_confirmation_and_reply_same_apartment(self):
        m = {"a": {"confirmation": self._conf()["confirmation"],
                   "reply": self._rep()["reply"]}}
        res = apply_replies.apply_matches(Store.load(), m)
        self.assertEqual(res["confirmations"], 1)
        self.assertEqual(res["replies"], 1)
        loaded = Store.load().listings["a"]
        self.assertEqual(loaded["confirmation"]["thread_id"], "C1")
        self.assertEqual(loaded["reply_candidate"]["thread_id"], "T1")


if __name__ == "__main__":
    unittest.main()
