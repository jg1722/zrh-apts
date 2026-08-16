#!/usr/bin/env python3
"""Verify listing facts against the actual detail page.

The Flatfox API and detail page can disagree (observed: API returns inflated
rooms / size / price for the same `pk`). The page is what the user clicks
through to, so it is the truth. This script parses the page HTML for each
listing and OVERRIDES the stored API values when they differ. Sets
`verified_at` + `verification_notes` so we don't re-verify the same thing every
day, and so the digest can show that values are page-confirmed.

Scope:
  --scope all        verify every active listing not yet verified today (default)
  --scope displayed  verify only listings currently in a bucket / manual-check
                     (fast: ~10s; use for an immediate fix)

Run:  .venv/bin/python scripts/verify_listings.py [--scope all|displayed]
"""
from __future__ import annotations

import argparse
import re
import socket
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import browser, config, http, paths  # noqa: E402
from applib.store import Store  # noqa: E402
from applib.text import norm, parse_float  # noqa: E402

# A contact email exposed on the listing → the best outreach channel (tier 1).
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Sites/domains that are never a landlord contact (analytics, image CDNs, etc.).
_EMAIL_NOISE = ("example.", "sentry.", "wixpress.", "googleapis.", "cloudflare",
                "newhome.ch", "flatfox.ch", "@2x", ".png", ".jpg", ".webp")
# A future application window ("apply from …") → tier 3 (onsite_windowed).
RE_WINDOW = re.compile(
    r"(bewerbung(?:en)?\s+(?:ab|m[öo]glich\s+ab|start)|vermietung\s+ab|"
    r"erstvermietung|bewerbungsstart|applications?\s+(?:open|from|start)|"
    r"warteliste|waiting\s*list)\b[^.\n]{0,60}", re.I)


def _clean_email(html: str) -> str | None:
    for m in RE_EMAIL.finditer(html or ""):
        addr = m.group(0)
        low = addr.lower()
        if any(n in low for n in _EMAIL_NOISE):
            continue
        return addr
    return None

# Flatfox "soft 404": the page returns HTTP 200 but shows a banner that the
# listing is gone (expired sublet, withdrawn, rented). Must be kicked.
RE_UNAVAILABLE = re.compile(
    r"this listing is currently not available|listing is no longer available|"
    r"nicht mehr verf[üu]gbar|zurzeit nicht verf[üu]gbar", re.I)
# og:title looks like: 'Rent a 1 ½ rooms apartment in Zürich | Flatfox'
RE_ROOMS = re.compile(r"Rent a ([\d\s½]+?)\s*rooms? apartment", re.I)
# Page title also includes the price (and address):
#   'Müllerstrasse 76, 8004 Zürich - CHF 1’680'
RE_TITLE_PRICE = re.compile(
    r"\d{4}\s+[A-Za-zÄÖÜäöüéèêà ./\-]+?\s*-\s*CHF\s*([\d'’]+)"
)
# Fallback price patterns.
RE_PRICE_PER_MONTH = re.compile(r"CHF\s*([\d'’]+)\s*per month", re.I)
# Living area: first "NN m²" on the page is the listing's surface_living.
RE_SIZE = re.compile(r"(\d{2,3})\s*m²")


def _parse_rooms(html: str) -> float | None:
    m = RE_ROOMS.search(html)
    if not m:
        return None
    txt = m.group(1).strip()
    nums = re.findall(r"\d+", txt)
    if not nums:
        return None
    return float(nums[0]) + (0.5 if "½" in txt else 0.0)


def _parse_size(html: str) -> int | None:
    m = RE_SIZE.search(html)
    return int(m.group(1)) if m else None


def _parse_price(html: str) -> int | None:
    for rx in (RE_TITLE_PRICE, RE_PRICE_PER_MONTH):
        m = rx.search(html)
        if m:
            return int(m.group(1).replace("’", "").replace("'", ""))
    return None


# --------------------------------------------------------------------------- #
# Outreach-channel detection. Records HOW we can reach out for this posting so
# the cross-site dedup can keep the best-reachable copy. We only RECORD it here —
# we never contact anyone (Hard Rule 2). Unsure → channel_unknown + manual-check.
# --------------------------------------------------------------------------- #
def _set_channel(lst: dict, channel: str, *, email: str | None = None,
                 window: str | None = None) -> None:
    lst["outreach_channel"] = channel
    lst["outreach_email"] = email
    lst["outreach_window"] = window
    lst["outreach_detected_at"] = paths.today_iso()
    if channel == "channel_unknown":
        mc = lst.get("manual_check") or []
        if "outreach_channel_unknown" not in mc:
            lst["manual_check"] = mc + ["outreach_channel_unknown"]


def _detect_channel_flatfox(html: str, lst: dict) -> None:
    email = _clean_email(html)
    if email:
        _set_channel(lst, "email", email=email)
        return
    win = RE_WINDOW.search(html) or RE_WINDOW.search(lst.get("blurb") or "")
    if win:
        _set_channel(lst, "onsite_windowed", window=win.group(0).strip()[:80])
        return
    # An active Flatfox listing always carries an on-platform enquiry form.
    if re.search(r"(send\s+message|nachricht|kontaktier|anfrage|application)", html, re.I):
        _set_channel(lst, "onsite_now")
        return
    _set_channel(lst, "channel_unknown")


def _detect_channel_newhome(detail: dict, lst: dict) -> None:
    blob = " ".join(str(detail.get(k) or "") for k in
                    ("descriptionText", "descriptionTextPlain"))
    email = _clean_email(blob)
    if email:
        _set_channel(lst, "email", email=email)
        return
    if detail.get("isChiffre"):
        _set_channel(lst, "channel_unknown")   # anonymous listing — no direct contact
        return
    if detail.get("showContactForm"):
        _set_channel(lst, "onsite_now")
        return
    _set_channel(lst, "channel_unknown")


# --------------------------------------------------------------------------- #
# Per-source verification.
# --------------------------------------------------------------------------- #
def verify_flatfox(lst: dict) -> tuple[bool, list[str]]:
    """Fetch the Flatfox detail page, override rooms/size/price when they differ
    from the API, and detect the outreach channel from the same HTML."""
    url = lst.get("url")
    if not url:
        return False, ["no url"]
    try:
        resp = http.get(url, accept="text/html", referer="https://flatfox.ch/")
    except Exception as exc:
        return False, [f"fetch error: {exc}"]
    if resp.status_code in (404, 410):
        lst["status"] = "closed"
        lst["verified_at"] = paths.today_iso()
        lst["verification_notes"] = f"page returned HTTP {resp.status_code} — listing removed"
        return True, [f"removed from source (HTTP {resp.status_code}); marked closed"]
    if resp.status_code != 200:
        return False, [f"http {resp.status_code}"]
    html = resp.text or ""

    # Soft 404: page loads (200) but the listing is gone. Kick it.
    if RE_UNAVAILABLE.search(html):
        lst["status"] = "closed"
        lst["verified_at"] = paths.today_iso()
        lst["verification_notes"] = "page marks listing no longer available — closed"
        return True, ["no longer available; marked closed"]

    page_rooms = _parse_rooms(html)
    page_size = _parse_size(html)
    page_price = _parse_price(html)
    if page_rooms is None and page_size is None and page_price is None:
        return False, ["page parse failed (layout changed?)"]

    notes: list[str] = []
    old_rooms = parse_float(lst.get("rooms"))
    old_size = parse_float(lst.get("size_sqm"))
    old_rent = parse_float(lst.get("rent_gross")) or parse_float(lst.get("rent_net"))

    if page_rooms is not None and (old_rooms is None or float(page_rooms) != old_rooms):
        notes.append(f"rooms {old_rooms}→{page_rooms}")
        lst["rooms"] = page_rooms
    if page_size is not None and (old_size is None or int(page_size) != int(old_size or -1)):
        notes.append(f"size {old_size}→{page_size}m²")
        lst["size_sqm"] = page_size
    if page_price is not None and (old_rent is None or int(page_price) != int(old_rent or -1)):
        notes.append(f"price {old_rent}→{page_price} CHF")
        lst["rent_gross"] = page_price        # page is the canonical figure
        lst["rent_net"] = None                # net wasn't in the page header

    _detect_channel_flatfox(html, lst)
    lst["verified_at"] = paths.today_iso()
    lst["verification_notes"] = "; ".join(notes) if notes else "page matches API"
    return True, notes


# Map newhome `feature` booleans to German amenity terms so the existing
# nice-to-have synonym matching (config/criteria.yaml) picks them up unchanged.
_NEWHOME_FEATURES = {
    "balconyAvailable": "Balkon", "garageAvailable": "Garage",
    "parkingSpaceAvailable": "Parkplatz", "carChargingStation": "Ladestation",
    "elevatorAvailable": "Lift", "minergie": "Minergie", "hasView": "Aussicht",
    "fireplaceAvailable": "Cheminée", "petsAllowed": "Haustiere erlaubt",
    "wheelcharAccessable": "Rollstuhlgängig", "childFriendly": "Kinderfreundlich",
    "cableTVAvailable": "Kabel-TV", "fiberAvailable": "Glasfaser",
}


def verify_newhome(lst: dict) -> tuple[bool, list[str]]:
    """Enrich a newhome listing from its detail API: net/charges rent, amenities,
    description, publish date, sharper photos, and the outreach channel."""
    site = config.sites().get("sites", {}).get("newhome", {})
    detail_url = site.get("detail_url")
    immocode = (lst.get("id") or "").split("newhome-")[-1]
    if not detail_url or not immocode.isdigit():
        return False, ["no immocode"]
    try:
        payload = browser.get_json(detail_url,
                                   params={"immocode": immocode, "languageIso": "de"})
    except browser.NotFoundError:
        lst["status"] = "closed"
        lst["verified_at"] = paths.today_iso()
        lst["verification_notes"] = "detail 404 — listing removed by newhome"
        return True, ["removed from source (404); marked closed"]
    except browser.BlockedError as exc:
        return False, [f"detail blocked: {exc}"]
    d = (payload or {}).get("detail")
    if not d:
        # ServiceStack returns an empty/!detail body when a listing is gone.
        lst["status"] = "closed"
        lst["verified_at"] = paths.today_iso()
        lst["verification_notes"] = "detail empty — listing removed"
        return True, ["removed from source; marked closed"]

    notes: list[str] = []
    pobj = d.get("price") if isinstance(d.get("price"), dict) else {}
    net = parse_float(pobj.get("priceNet"))
    gross = parse_float(pobj.get("price"))
    charges = parse_float(pobj.get("priceAdditionalCost"))
    if net is not None and parse_float(lst.get("rent_net")) != net:
        notes.append(f"rent_net→{int(net)}")
        lst["rent_net"] = int(net)
    if gross is not None:
        lst["rent_gross"] = int(gross)
    if charges is not None:
        lst["rent_charges"] = int(charges)

    feat = d.get("feature") or {}
    amenities = [term for flag, term in _NEWHOME_FEATURES.items() if feat.get(flag)]
    if amenities:
        lst["amenities"] = amenities
    blurb = (d.get("descriptionTextPlain") or d.get("descriptionText") or "").strip()
    if blurb:
        lst["blurb"] = blurb[:600]
    pub = d.get("publishDate")
    if pub:
        lst["source_published"] = str(pub)[:10]
    # Sharpen photos to full-resolution detail images when available.
    full = []
    for img in (d.get("images") or []):
        for fmt in (img.get("imageFormats") or []):
            if fmt.get("format") == 1 and fmt.get("url"):
                full.append(fmt["url"]); break
    if full:
        lst["photos"] = full
    if d.get("street") and not lst.get("street"):
        lst["street"] = d.get("street")
        lst["address"] = ", ".join(p for p in [
            d.get("street"),
            " ".join(str(x) for x in (lst.get("zipcode"), lst.get("city")) if x)
        ] if p)

    _detect_channel_newhome(d, lst)
    lst["verified_at"] = paths.today_iso()
    lst["verification_notes"] = "; ".join(notes) if notes else "detail enriched"
    return True, notes


def verify_one(lst: dict) -> tuple[bool, list[str]]:
    """Dispatch to the source-specific verifier."""
    if lst.get("source") == "newhome":
        return verify_newhome(lst)
    return verify_flatfox(lst)


# --------------------------------------------------------------------------- #
# Resilience: don't burn a whole pass when the Mac wakes up offline, and don't
# keep dead listings alive forever when they can't be re-verified.
# --------------------------------------------------------------------------- #
def _network_ok(host: str = "flatfox.ch") -> bool:
    """Cheap connectivity probe — DNS resolution only, no HTTP request."""
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


def _wait_for_network(max_wait_s: float = 1200, poll_s: float = 30) -> bool:
    """Block until DNS works again (launchd fires the moment the lid opens,
    often seconds before Wi-Fi is up). Returns False if the budget runs out."""
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        if _network_ok():
            return True
        print(f"  ! network down — waiting {poll_s:.0f}s for it to return")
        time.sleep(poll_s)
    return _network_ok()


def expire_stale(store: "Store", today: str, *, expire_after_days: int) -> list[str]:
    """Close undecided listings the pipeline could not re-verify for
    `expire_after_days` days: their page has been unreachable/unparsable for so
    long that the listing is dead weight in the digest and the verify queue.
    Decided listings and anything past `new` are never touched. 0 disables."""
    if expire_after_days <= 0:
        return []
    expired: list[str] = []
    for lst in store.listings.values():
        if lst.get("status") != "new" or lst.get("decision"):
            continue
        ref = lst.get("verified_at") or lst.get("last_seen") or lst.get("first_seen")
        if not ref:
            continue
        try:
            age = (date.fromisoformat(today) - date.fromisoformat(str(ref)[:10])).days
        except ValueError:
            continue
        if age > expire_after_days:
            lst["status"] = "closed"
            lst["verification_notes"] = f"expired — not verifiable for {age} days"
            expired.append(lst["id"])
    return expired


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["all", "displayed"], default="all")
    ap.add_argument("--force", action="store_true",
                    help="re-verify even listings already verified today "
                         "(catches sources like Flatfox that get pulled mid-day)")
    args = ap.parse_args()

    store = Store.load()
    today = paths.today_iso()

    if args.scope == "displayed":
        candidates = [l for l in store.active()
                      if l.get("gate_status") in ("passed", "manual")
                      and (args.force or l.get("verified_at") != today)]
    else:
        # Include cross-post dupes (not just active winners) so every copy gets a
        # detected outreach channel and can compete for the keeper slot below.
        # Also re-process any listing still MISSING an outreach channel even if its
        # facts were verified today — self-heals listings that predate channel
        # detection so cross-post winner selection isn't biased by a stale None.
        candidates = [l for l in store.listings.values()
                      if l.get("status") not in ("rejected", "closed")
                      and (args.force or l.get("verified_at") != today
                           or not l.get("outreach_channel"))]

    print(f"verify_listings: scope={args.scope}, {len(candidates)} to verify")
    if not _network_ok() and not _wait_for_network():
        # Offline and it didn't come back: bail out with everything untouched
        # rather than FAILing through the whole list (launchd fires the moment
        # the lid opens, often before Wi-Fi is up).
        print("verify_listings: no network — aborting without changes")
        return 1

    ok = mism = fail = 0
    aborted = False
    for done, lst in enumerate(candidates, start=1):
        success, notes = verify_one(lst)
        if not success and not _network_ok():
            # The failure was the network, not the listing. Wait for it to
            # return and give the listing one more shot; if the network stays
            # down, save progress and stop instead of failing the rest.
            if _wait_for_network():
                success, notes = verify_one(lst)
            else:
                print("verify_listings: network gone — stopping early, progress saved")
                aborted = True
        if not success:
            fail += 1
            print(f"  - {lst['id']}: FAIL {notes}")
        else:
            ok += 1
            if notes:
                mism += 1
                print(f"  - {lst['id']}: corrected — {'; '.join(notes)}")
        if done % 50 == 0:
            store.save()   # a crash/kill mid-run keeps everything verified so far
        if aborted:
            break

    # Undecided listings that stayed unverifiable for weeks are gone — close
    # them so they leave the digest and stop growing the verify queue.
    if not aborted:
        expire_days = int((config.criteria().get("staleness") or {})
                          .get("expire_after_days", 14))
        expired = expire_stale(store, today, expire_after_days=expire_days)
        for lid in expired:
            print(f"  - {lid}: expired — unverifiable for {expire_days}+ days; marked closed")

    # Re-settle cross-post winners now that outreach channels are known.
    suppressed = store.recompute_crossposts()
    store.save()
    print(f"verify_listings: {ok} verified ({mism} corrected), {fail} failed; "
          f"{suppressed} cross-post dupes hidden")
    return 1 if aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
