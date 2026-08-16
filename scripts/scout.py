#!/usr/bin/env python3
"""Step 1 — Scout.

Pull new/changed listings from the enabled sites in config/sites.yaml, normalise
to the listing schema, dedup against data/listings.json, and persist. Gentle by
design (one request at a time, per-host delay). If a site blocks or its layout
changed, it logs and continues — it NEVER fabricates listings.

Run:  python scripts/scout.py
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import browser, config, hoods, http, paths  # noqa: E402
from applib.store import Store  # noqa: E402
from applib.text import (  # noqa: E402
    haversine_km, newhome_detail_url, norm, parse_float, zip_in_scope)


def _cutoff(lookback_days: int) -> str:
    return (dt.date.today() - dt.timedelta(days=lookback_days)).isoformat()


def _office_latlon() -> tuple[float, float] | None:
    t = config.criteria().get("transit", {})
    lat, lon = t.get("office_lat"), t.get("office_lon")
    return (float(lat), float(lon)) if lat is not None and lon is not None else None


def _in_scope(lat, lon, zipcode, city, scope: dict, office) -> bool:
    """Primary: within radius_km of the office (needs coords). Fallback for
    coord-less listings: zip prefix or city match."""
    radius = scope.get("radius_km")
    if radius and office and lat is not None and lon is not None:
        try:
            return haversine_km(float(lat), float(lon), office[0], office[1]) <= float(radius)
        except (TypeError, ValueError):
            pass
    return (zip_in_scope(str(zipcode or ""), scope.get("zip_prefixes", []))
            or norm(city) in [norm(c) for c in scope.get("city_match", [])])


# --------------------------------------------------------------------------- #
# Flatfox — clean public JSON API.
# --------------------------------------------------------------------------- #
def _flatfox_address(r: dict) -> str | None:
    parts = []
    if r.get("street"):
        parts.append(str(r["street"]).strip())
    locality = " ".join(str(x).strip() for x in (r.get("zipcode"), r.get("city")) if x)
    if locality:
        parts.append(locality)
    return ", ".join(parts) if parts else None


def _flatfox_availability(r: dict) -> str | None:
    mdt = (r.get("moving_date_type") or "").upper()
    if mdt in ("IMMEDIATELY", "BY_AGREEMENT") or r.get("moving_date") in (None, ""):
        return {"IMMEDIATELY": "ab sofort", "BY_AGREEMENT": "nach Vereinbarung"}.get(mdt)
    return str(r.get("moving_date"))


def _flatfox_normalise(r: dict, base_url: str) -> dict:
    imgs = [img.get("url") for img in (r.get("images") or []) if isinstance(img, dict) and img.get("url")]
    photos = [base_url + u if u.startswith("/") else u for u in imgs]
    desc = (r.get("description") or "").strip()
    hood_name, hood_cat = hoods.lookup(r.get("latitude"), r.get("longitude"))
    return {
        "id": f"flatfox-{r.get('pk')}",
        "source": "flatfox",
        "url": base_url + r["url"] if r.get("url") else None,
        "title": r.get("public_title") or r.get("short_title"),
        "rent_net": r.get("rent_net"),
        "rent_gross": r.get("rent_gross"),
        "rent_charges": r.get("rent_charges"),
        "size_sqm": r.get("surface_living"),
        "rooms": r.get("number_of_rooms"),
        "street": r.get("street"),
        "zipcode": str(r.get("zipcode")) if r.get("zipcode") is not None else None,
        "city": r.get("city"),
        "address": _flatfox_address(r),
        "latitude": r.get("latitude"),
        "longitude": r.get("longitude"),
        "amenities": [a.get("name") for a in (r.get("attributes") or []) if isinstance(a, dict)],
        "photos": photos,
        "year_built": r.get("year_built"),
        "year_renovated": r.get("year_renovated"),
        "is_furnished": r.get("is_furnished"),
        "availability": _flatfox_availability(r),
        "source_published": (r.get("published") or r.get("created") or "")[:10] or None,
        "blurb": desc[:600],
        "hood_name": hood_name,
        "hood_category": hood_cat,
    }


def scout_flatfox(site: dict, scope: dict, log: list[str]) -> list[dict]:
    """The public-listing endpoint ignores filter params and returns the whole
    catalog ordered oldest-published-first, so the NEWEST listings live at the
    tail. We read `count`, then page backward from the end and filter to scope
    client-side, stopping once a whole page predates the lookback cutoff."""
    base = site["base_url"].rstrip("/")
    api = base + site["api_path"]
    params = dict(site.get("params", {}))
    limit = int(params.get("limit", 50))
    max_pages = int(site.get("max_pages", 25))
    cutoff = _cutoff(int(scope.get("lookback_days", 4)))
    office = _office_latlon()

    try:
        head = http.get_json(api, params={**params, "limit": 1, "offset": 0})
        count = int(head.get("count") or 0)
    except Exception as exc:
        log.append(f"flatfox: could not read catalog count: {exc}")
        return []
    if count <= 0:
        log.append("flatfox: empty catalog")
        return []

    kept: list[dict] = []
    scanned = 0
    for page in range(max_pages):
        offset = count - (page + 1) * limit
        fetch_limit = limit
        if offset < 0:           # clamp the final (oldest) page we touch
            fetch_limit = limit + offset
            offset = 0
        if fetch_limit <= 0:
            break
        try:
            payload = http.get_json(api, params={**params, "limit": fetch_limit, "offset": offset})
        except Exception as exc:
            log.append(f"flatfox: request failed at offset {offset}: {exc}")
            break
        results = payload.get("results") or []
        if not results:
            break
        page_newest = ""
        for r in results:
            scanned += 1
            pub = (r.get("published") or r.get("created") or "")[:10]
            if pub > page_newest:
                page_newest = pub
            if (r.get("offer_type") != "RENT" or r.get("object_category") != "APARTMENT"
                    or r.get("status") != "act"):
                continue
            if not _in_scope(r.get("latitude"), r.get("longitude"),
                             r.get("zipcode"), r.get("city"), scope, office):
                continue
            kept.append(_flatfox_normalise(r, base))
        if offset == 0:
            break
        # Pages get older as we go back; once an entire page predates the
        # cutoff, everything earlier does too.
        if page_newest and page_newest < cutoff:
            break
    log.append(f"flatfox: scanned {scanned} (newest tail), in-scope {len(kept)}")
    return kept


# --------------------------------------------------------------------------- #
# Comparis — Cloudflare-protected aggregator. Best-effort; degrades gracefully.
# --------------------------------------------------------------------------- #
def scout_comparis(site: dict, scope: dict, log: list[str]) -> list[dict]:
    base = site["base_url"].rstrip("/")
    url = base + site.get("search_path", "")
    try:
        resp = http.get(url, accept="text/html", referer=base + "/")
    except Exception as exc:
        log.append(f"comparis: request failed: {exc}")
        return []

    body = resp.text or ""
    blocked = (resp.status_code in (403, 429, 503)
               or "Just a moment" in body
               or "cf-challenge" in body
               or "challenge-platform" in body)
    if blocked:
        log.append(f"comparis: blocked (HTTP {resp.status_code}, Cloudflare challenge) — skipped, no data fabricated")
        return []
    if resp.status_code != 200:
        log.append(f"comparis: unexpected HTTP {resp.status_code} — skipped")
        return []

    # Comparis is a Next.js app; usable data (if served) lives in __NEXT_DATA__.
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
    if not m:
        log.append("comparis: no __NEXT_DATA__ payload found (layout changed?) — skipped, no data fabricated")
        return []
    try:
        json.loads(m.group(1))
    except ValueError:
        log.append("comparis: __NEXT_DATA__ not parseable — skipped")
        return []
    # The current Comparis payload does not expose a stable listing array to
    # anonymous clients. Rather than guess at fields, we surface that and stop.
    log.append("comparis: reached page but no parseable listing array — skipped, no data fabricated")
    return []


# --------------------------------------------------------------------------- #
# newhome — Angular SPA behind Cloudflare; reachable via curl_cffi (browser.py).
# ServiceStack JSON API. We page NEWEST-FIRST and scope by office radius server-
# side. Detail (rent_net, amenities, contact, publish date) is enriched later by
# verify_listings.py — the search entry alone is enough to gate.
# --------------------------------------------------------------------------- #
def _newhome_address(r: dict) -> str | None:
    parts = []
    if r.get("street"):
        parts.append(str(r["street"]).strip())
    locality = " ".join(str(x).strip() for x in (r.get("postalCode"), r.get("city")) if x)
    if locality:
        parts.append(locality)
    return ", ".join(parts) if parts else None


def _newhome_normalise(r: dict) -> dict:
    immocode = r.get("immocode")
    imgs = [img.get("path") for img in (r.get("images") or [])
            if isinstance(img, dict) and img.get("path")]
    # The search entry exposes only the GROSS price (price = net + additional
    # costs; confirmed against the detail's priceNet/priceAdditionalCost). Net is
    # filled in by verify_listings from the detail. Mirrors a gross-only Flatfox.
    price = parse_float(r.get("price"))
    avail = r.get("availabilityDate")
    hood_name, hood_cat = hoods.lookup(r.get("latitude"), r.get("longitude"))
    subtype = r.get("propertySubType") or r.get("propertyType")
    return {
        "id": f"newhome-{immocode}",
        "source": "newhome",
        "url": newhome_detail_url(immocode, city=r.get("city"), rooms=r.get("rooms"),
                                  photo_url=imgs[0] if imgs else None,
                                  subtype=str(subtype) if subtype else "wohnung"),
        "title": r.get("title"),
        "rent_net": None,
        "rent_gross": int(price) if price else None,
        "rent_charges": None,
        "size_sqm": r.get("livingArea"),
        "rooms": r.get("rooms"),
        "street": r.get("street"),
        "zipcode": str(r.get("postalCode")) if r.get("postalCode") is not None else None,
        "city": r.get("city"),
        "address": _newhome_address(r),
        "latitude": r.get("latitude"),
        "longitude": r.get("longitude"),
        "amenities": [],                 # not in the search entry; verify fills from `feature`
        "photos": imgs,
        "year_built": None,
        "year_renovated": None,
        "is_furnished": None,
        "availability": str(avail)[:10] if avail else None,
        "source_published": None,        # publishDate is detail-only; verify fills it
        "blurb": "",                     # descriptionText is detail-only; verify fills it
        "hood_name": hood_name,
        "hood_category": hood_cat,
    }


def scout_newhome(site: dict, scope: dict, log: list[str]) -> list[dict]:
    if not browser.available():
        log.append("newhome: curl_cffi not installed — skipped, no data fabricated")
        return []
    office = _office_latlon()
    if not office:
        log.append("newhome: office coordinates missing in criteria — skipped")
        return []
    url = site["search_url"]
    base_params = dict(site.get("params", {}))
    base_params["radius"] = int(scope.get("radius_km", 15))
    base_params["radiusCenterCoordinate"] = f"{office[0]};{office[1]}"
    row_count = int(base_params.get("rowCount", 50))
    max_pages = int(site.get("max_pages", 6))

    kept: list[dict] = []
    scanned = 0
    for page in range(max_pages):
        params = {**base_params, "skipCount": page * row_count, "rowCount": row_count}
        try:
            payload = browser.get_json(url, params=params)
        except browser.BlockedError as exc:
            log.append(f"newhome: {exc} at page {page} — skipped, no data fabricated")
            break
        entries = payload.get("entries") or []
        if not entries:
            break
        for r in entries:
            scanned += 1
            if not _in_scope(r.get("latitude"), r.get("longitude"),
                             r.get("postalCode"), r.get("city"), scope, office):
                continue
            kept.append(_newhome_normalise(r))
        if len(entries) < row_count:    # last (newest-first) page reached
            break
    log.append(f"newhome: scanned {scanned} (newest first), in-scope {len(kept)}")
    return kept


SCOUTERS = {"flatfox": scout_flatfox, "comparis": scout_comparis,
            "newhome": scout_newhome}


def main() -> int:
    paths.ensure_dirs()
    today = paths.today_iso()
    scope = config.sites().get("scope", {})
    store = Store.load()
    store.begin_run(today)

    log: list[str] = []
    counts = {"new": 0, "updated": 0, "changed": 0}
    for name, site in config.enabled_sites().items():
        scouter = SCOUTERS.get(name)
        if not scouter:
            log.append(f"{name}: enabled but no scraper implemented — skipped")
            continue
        try:
            rows = scouter(site, scope, log)
        except Exception as exc:  # never let one site abort the run
            log.append(f"{name}: unhandled error: {exc} — skipped")
            continue
        for raw in rows:
            if not raw.get("id"):
                continue
            existed = raw["id"] in store.listings
            store.upsert(raw, today)
            lst = store.listings[raw["id"]]
            if not existed:
                counts["new"] += 1
            else:
                counts["updated"] += 1
                if lst.get("changed"):
                    counts["changed"] += 1

    # Cross-site dedup: keep the best-outreach copy of each flat active, hide the
    # rest (dupe_of). Channel-aware once verify_listings has run; on the first
    # sighting it falls back to data-completeness, then re-settles next run.
    suppressed = store.recompute_crossposts()
    store.save()

    summary = (f"scout {today}: +{counts['new']} new, {counts['updated']} updated "
               f"({counts['changed']} changed), {suppressed} cross-post dupes hidden")
    print(summary)
    for line in log:
        print("  -", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
