"""The durable listing store (data/listings.json).

Keyed by a stable per-source id (e.g. "flatfox-33819"). Scout upserts only the
fields it scrapes; pipeline/user fields (status, transit_*, condition_*, bucket)
persist across runs. Change detection flags price/availability moves so the
digest can re-surface a listing the user has already seen.
"""
from __future__ import annotations

import json
from typing import Any

from . import config, paths
from .text import norm, parse_float

# Fields owned by the scraper — refreshed every run.
SCRAPED_FIELDS = [
    "source", "url", "title", "rent_net", "rent_gross", "rent_charges",
    "size_sqm", "rooms", "street", "zipcode", "city", "address",
    "latitude", "longitude", "amenities", "photos", "year_built",
    "year_renovated", "availability", "source_published", "blurb",
    "is_furnished", "hood_name", "hood_category",
]

# Defaults for fields the pipeline/user own (set once, then persisted).
PIPELINE_DEFAULTS: dict[str, Any] = {
    "has_parking": None,
    "has_balcony": None,
    "condition_kitchen": None,   # modern | acceptable | dated | condition_unknown
    "condition_bath": None,
    "condition_reason": None,
    "transit_min": None,
    "transit_route": None,
    "transit_arrival": None,
    "transit_status": "pending",  # pending | ok | rejected | transit_unknown
    "gate_status": "pending",     # pending | passed | rejected
    "reject_reason": None,
    "manual_check": [],
    "flags": [],                  # transparency notes, e.g. "rent uses gross"
    "bucket": None,               # "A" | "B" | None
    "bucket_gap": None,
    "status": "new",              # new -> contacted -> replied -> viewing -> closed/rejected
    # User decision that controls digest visibility. Until a decision is made a
    # listing keeps showing in its bucket every run (it does NOT drop off just
    # because it was seen before). "outreach" / "deprioritized" move it to its own
    # digest list. Set via scripts/decide.py; outreach also implied by status>=contacted.
    "decision": None,             # None (undecided) | "outreach" | "deprioritized"
    "decision_at": None,
    "decision_note": None,
    "dupe_of": None,
    # Outreach channel (how we can reach out for THIS posting), detected by
    # verify_listings.py. Drives cross-site dedup winner selection.
    "outreach_channel": "channel_unknown",  # email | onsite_now | onsite_windowed | channel_unknown
    "outreach_email": None,       # exposed contact email, if any
    "outreach_window": None,      # e.g. "applications open 2026-06-10" / wait note
    "outreach_detected_at": None, # date the channel was last detected
    "crosspost_sources": None,    # on a winner: [{source, url}] of the hidden duplicate copies
    "verified_at": None,          # date the rooms/size/rent were last cross-checked against the page
    "verification_notes": None,   # human-readable summary of any API→page corrections
    # Gmail reply matching (set by apply_replies.py / the UI; see the reply spec).
    "confirmation": None,           # auto-captured form-submission receipt ("request delivered"): {thread_id, gmail_link, from, subject, snippet, received_at, reviewer_reason, captured_at}
    "reply": None,                  # confirmed personal reply: {thread_id, gmail_link, from, subject, snippet, received_at, matched_by, confidence, reviewer_reason, confirmed_at}
    "reply_candidate": None,        # pending personal-reply match awaiting the user's confirm (same shape, no confirmed_at)
    "reply_dismissed_threads": [],  # gmail thread ids rejected as "not a match" — never re-surface
    "draft": None,                  # reserved; the live draft is stored inside reply/reply_candidate
}


class Store:
    def __init__(self, data: dict):
        self.data = data
        self.listings: dict[str, dict] = data.setdefault("listings", {})
        data.setdefault("meta", {})

    # ---- load / save -----------------------------------------------------
    @classmethod
    def load(cls) -> "Store":
        if paths.LISTINGS_FILE.exists():
            with open(paths.LISTINGS_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        else:
            data = {"meta": {"schema_version": 1}, "listings": {}}
        return cls(data)

    def save(self) -> None:
        paths.LISTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = paths.LISTINGS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(paths.LISTINGS_FILE)

    # ---- per-run bookkeeping --------------------------------------------
    def begin_run(self, today: str) -> None:
        """Reset the per-run change flags. Call once at the start of scout."""
        self.data["meta"]["last_run"] = today
        for lst in self.listings.values():
            lst["changed"] = False
            lst["change_notes"] = []

    # ---- cross-post dedup ------------------------------------------------
    def _crosspost_key(self, lst: dict) -> tuple | None:
        """Coarse "same building + layout" key: street+number, postcode, rounded
        rooms. Size is NOT in the key — rounding it into buckets splits the same
        flat across a boundary (77 vs 78 m²); instead recompute_crossposts()
        clusters by a size TOLERANCE within each key group. Rent is excluded too
        (sites disagree on net vs gross). None when a component is missing (then
        the listing is left unique — we never risk a false merge)."""
        street = norm(lst.get("street"))
        plz = (str(lst.get("zipcode")).strip() if lst.get("zipcode") else "")
        rooms = parse_float(lst.get("rooms"))
        size = parse_float(lst.get("size_sqm"))
        if not street or not plz or rooms is None or size is None:
            return None
        rstep = float(config.criteria().get("dedup", {}).get("rooms_round", 0.5)) or 0.5
        return (street, plz, round(rooms / rstep) * rstep)

    def _size_clusters(self, ids: list[str], tol: float) -> list[list[str]]:
        """Within one key group, split ids into clusters whose living areas are
        within `tol` m² of the cluster's first member — so the same flat (77≈78)
        merges but two differently-sized flats in the same building do not."""
        ordered = sorted(ids, key=lambda i: parse_float(self.listings[i].get("size_sqm")) or 0)
        clusters: list[list[str]] = []
        for lid in ordered:
            size = parse_float(self.listings[lid].get("size_sqm")) or 0
            for c in clusters:
                anchor = parse_float(self.listings[c[0]].get("size_sqm")) or 0
                if abs(size - anchor) <= tol:
                    c.append(lid)
                    break
            else:
                clusters.append([lid])
        return clusters

    def _completeness(self, lst: dict) -> int:
        return sum(1 for f in SCRAPED_FIELDS if lst.get(f) not in (None, "", []))

    def _winner(self, ids: list[str], tier_order: list[str]) -> str:
        """Pick the cross-post group's keeper: best outreach tier, then most
        complete data, then earliest first_seen, then id (deterministic)."""
        worst = len(tier_order)

        def rank(lid: str):
            lst = self.listings[lid]
            tier = lst.get("outreach_channel") or "channel_unknown"
            tier_rank = tier_order.index(tier) if tier in tier_order else worst
            return (tier_rank, -self._completeness(lst),
                    lst.get("first_seen") or "9999", lid)

        return min(ids, key=rank)

    def recompute_crossposts(self) -> int:
        """Group all in-play listings by the fuzzy key and, within each group,
        keep the best-outreach copy active while marking the rest dupe_of=winner.
        Idempotent — safe to run every scout/verify. Returns the number of
        listings currently suppressed as duplicates."""
        crit = config.criteria()
        tier_order = (crit.get("outreach", {}).get("tier_order")
                      or ["email", "onsite_now", "onsite_windowed", "channel_unknown"])
        tol = float(crit.get("dedup", {}).get("m2_bucket", 5)) or 5
        groups: dict[tuple, list[str]] = {}
        for lid, lst in self.listings.items():
            if lst.get("status") in ("rejected", "closed"):
                # Closed/rejected postings don't claim or block a group; clear any
                # stale dupe pointer so the live twin can stand alone.
                lst["dupe_of"] = None
                lst["crosspost_sources"] = None
                continue
            key = self._crosspost_key(lst)
            if key is None:
                lst["dupe_of"] = None
                lst["crosspost_sources"] = None
                continue
            groups.setdefault(key, []).append(lid)

        suppressed = 0
        for ids in groups.values():
            for cluster in self._size_clusters(ids, tol):
                if len(cluster) == 1:
                    only = self.listings[cluster[0]]
                    only["dupe_of"] = None
                    only["crosspost_sources"] = None
                    continue
                win = self._winner(cluster, tier_order)
                losers = [i for i in cluster if i != win]
                w = self.listings[win]
                w["dupe_of"] = None
                w["crosspost_sources"] = [
                    {"source": self.listings[i].get("source"),
                     "url": self.listings[i].get("url")}
                    for i in losers
                ]
                for i in losers:
                    self.listings[i]["dupe_of"] = win
                    self.listings[i]["crosspost_sources"] = None
                    suppressed += 1
        return suppressed

    # ---- upsert ----------------------------------------------------------
    def upsert(self, raw: dict, today: str) -> str:
        """Insert or update a scraped listing. Returns its id.
        Sets changed=True + change_notes when price/availability moved."""
        lid = raw["id"]
        existing = self.listings.get(lid)

        if existing is None:
            lst = {"id": lid, "first_seen": today, "changed": False,
                   "change_notes": []}
            lst.update(PIPELINE_DEFAULTS)
            for f in SCRAPED_FIELDS:
                lst[f] = raw.get(f)
            lst["last_seen"] = today
            self.listings[lid] = lst
            return lid

        # Update existing: detect changes, then refresh scraped fields.
        notes: list[str] = []
        old_rent = parse_float(existing.get("rent_net")) or parse_float(existing.get("rent_gross"))
        new_rent = parse_float(raw.get("rent_net")) or parse_float(raw.get("rent_gross"))
        if old_rent is not None and new_rent is not None and int(old_rent) != int(new_rent):
            notes.append(f"price {int(old_rent)} -> {int(new_rent)} CHF")
        if norm(existing.get("availability")) != norm(raw.get("availability")) \
                and raw.get("availability"):
            notes.append(f"availability -> {raw.get('availability')}")

        # If the address changed, transit must be recomputed.
        if norm(existing.get("address")) != norm(raw.get("address")):
            existing["transit_status"] = "pending"
            existing["transit_min"] = None

        for f in SCRAPED_FIELDS:
            existing[f] = raw.get(f)
        existing["last_seen"] = today
        if notes:
            existing["changed"] = True
            existing["change_notes"] = notes
        return lid

    # ---- queries ---------------------------------------------------------
    def active(self) -> list[dict]:
        """Listings still in play (not rejected/closed, not a cross-post dupe)."""
        return [l for l in self.listings.values()
                if l.get("status") not in ("rejected", "closed")
                and not l.get("dupe_of")]
