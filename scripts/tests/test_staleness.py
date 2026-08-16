from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import digest  # noqa: E402
import verify_listings  # noqa: E402
from applib.store import Store  # noqa: E402

TODAY = "2026-07-02"


def _lst(lid: str, **over) -> dict:
    base = {"id": lid, "status": "new", "decision": None,
            "first_seen": "2026-06-01", "last_seen": "2026-06-01",
            "verified_at": None}
    base.update(over)
    return base


class StalenessNoteTests(unittest.TestCase):
    def test_fresh_listing_gets_no_note(self):
        lst = _lst("flatfox-1", verified_at="2026-07-01")
        self.assertIsNone(digest._staleness_note(lst, TODAY, stale_days=2))

    def test_stale_listing_gets_age_note(self):
        lst = _lst("flatfox-1", verified_at="2026-06-25")
        note = digest._staleness_note(lst, TODAY, stale_days=2)
        self.assertIsNotNone(note)
        self.assertIn("7 d ago", note)

    def test_exactly_at_threshold_is_not_stale(self):
        lst = _lst("flatfox-1", verified_at="2026-06-30")
        self.assertIsNone(digest._staleness_note(lst, TODAY, stale_days=2))

    def test_never_verified_falls_back_to_last_seen(self):
        lst = _lst("flatfox-1", verified_at=None, last_seen="2026-06-20")
        note = digest._staleness_note(lst, TODAY, stale_days=2)
        self.assertIn("12 d ago", note)

    def test_no_dates_at_all_gets_no_note(self):
        lst = _lst("flatfox-1", verified_at=None, last_seen=None, first_seen=None)
        self.assertIsNone(digest._staleness_note(lst, TODAY, stale_days=2))


class ExpireStaleTests(unittest.TestCase):
    def _store(self, *listings) -> Store:
        return Store({"listings": {l["id"]: l for l in listings}})

    def test_old_unverifiable_new_listing_expires(self):
        store = self._store(_lst("flatfox-1", verified_at="2026-06-10"))
        expired = verify_listings.expire_stale(store, TODAY, expire_after_days=14)
        self.assertEqual(expired, ["flatfox-1"])
        lst = store.listings["flatfox-1"]
        self.assertEqual(lst["status"], "closed")
        self.assertIn("expired", lst["verification_notes"])

    def test_recently_verified_listing_survives(self):
        store = self._store(_lst("flatfox-1", verified_at="2026-07-01"))
        self.assertEqual(verify_listings.expire_stale(store, TODAY, expire_after_days=14), [])
        self.assertEqual(store.listings["flatfox-1"]["status"], "new")

    def test_decided_outreach_listing_is_never_expired(self):
        store = self._store(_lst("flatfox-1", verified_at="2026-06-01", decision="outreach"))
        self.assertEqual(verify_listings.expire_stale(store, TODAY, expire_after_days=14), [])
        self.assertEqual(store.listings["flatfox-1"]["status"], "new")

    def test_non_new_status_is_untouched(self):
        store = self._store(_lst("flatfox-1", verified_at="2026-06-01", status="replied"))
        self.assertEqual(verify_listings.expire_stale(store, TODAY, expire_after_days=14), [])
        self.assertEqual(store.listings["flatfox-1"]["status"], "replied")

    def test_never_verified_uses_last_seen(self):
        store = self._store(_lst("flatfox-1", verified_at=None, last_seen="2026-06-10"))
        expired = verify_listings.expire_stale(store, TODAY, expire_after_days=14)
        self.assertEqual(expired, ["flatfox-1"])

    def test_zero_or_negative_threshold_disables_expiry(self):
        store = self._store(_lst("flatfox-1", verified_at="2026-01-01"))
        self.assertEqual(verify_listings.expire_stale(store, TODAY, expire_after_days=0), [])


if __name__ == "__main__":
    unittest.main()
