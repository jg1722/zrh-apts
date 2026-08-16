#!/usr/bin/env python3
"""Steps 2/3 (knockouts) — must-have filter, then the transit gate.

Reads thresholds from config/criteria.yaml every run (never hard-coded). For
each active listing:
  * checks rent (by the configured basis), size, rooms;
  * a value that is OUT of range and KNOWN => hard reject (logged, not shown);
  * a value that is UNKNOWN => routed to manual-check, never guessed;
  * survivors get the transit knockout via scripts/transit_check.py.

gate_status outcomes:  passed | manual | rejected | pending
Vision style scoring then runs only on `passed` survivors (cheap-API-first).

Run:  python scripts/gate.py
"""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import transit_check  # noqa: E402
from applib import config, paths  # noqa: E402
from applib.store import Store  # noqa: E402
from applib.text import (  # noqa: E402
    detect_nice_to_haves, is_age_restricted_listing, is_exchange_listing,
    is_room_only_listing, lease_term_months, match_hard_temp_keyword,
    parse_float)


def _as_date(value) -> dt.date | None:
    """ISO date at the head of `value`, else None. Never guesses: free-text
    availability ("nach Vereinbarung", "ab sofort") returns None and is handled
    as unknown by the caller."""
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def move_in_verdict(availability, mcfg: dict) -> tuple[str, str]:
    """Judge a listing's availability date against the move_in window.

    Returns (verdict, detail) where verdict is:
      ok       — inside the window, or no window configured;
      rejected — a KNOWN date outside the window (hard fail);
      unknown  — no parseable date; caller routes to manual-check.
    """
    if not mcfg:
        return "ok", ""
    lo = _as_date(mcfg.get("earliest"))
    hi = _as_date(mcfg.get("latest"))
    if lo is None and hi is None:
        return "ok", ""

    avail = _as_date(availability)
    if avail is None:
        if not mcfg.get("unknown_to_manual", True):
            return "ok", ""
        raw = str(availability).strip() if availability else ""
        return "unknown", f"movein_unknown ({raw})" if raw else "movein_unknown"

    window = f"{lo.isoformat() if lo else '…'}..{hi.isoformat() if hi else '…'}"
    if lo and avail < lo:
        return "rejected", f"available {avail.isoformat()} before move-in window {window}"
    if hi and avail > hi:
        return "rejected", f"available {avail.isoformat()} after move-in window {window}"
    return "ok", ""


def _evaluate_must_haves(lst: dict, crit: dict) -> tuple[list[str], list[str], list[str]]:
    """Return (hard_fails, unknowns, flags) as human-readable strings."""
    hard_fails: list[str] = []
    unknowns: list[str] = []
    flags: list[str] = []

    rcfg = crit["rent"]
    basis = rcfg.get("basis", "net")
    other = "gross" if basis == "net" else "net"
    rmin, rmax = rcfg["min"], rcfg["max"]

    rent = parse_float(lst.get(f"rent_{basis}"))
    if rent is None and rcfg.get("fallback_to_other_basis", True):
        alt = parse_float(lst.get(f"rent_{other}"))
        if alt is not None:
            rent = alt
            flags.append(f"rent uses {other} CHF {int(alt)} ({basis} not published)")
    if rent is None:
        unknowns.append(f"rent_unknown ({basis})")
    elif not (rmin <= rent <= rmax):
        hard_fails.append(f"rent {int(rent)} outside {rmin}-{rmax} CHF")

    smin = crit["size"]["min_sqm"]
    size = parse_float(lst.get("size_sqm"))
    if size is None:
        unknowns.append("size_unknown")
    elif size < smin:
        hard_fails.append(f"size {size:g}m² < {smin}m²")

    rmin_rooms = crit["rooms"]["min"]
    rooms = parse_float(lst.get("rooms"))
    if rooms is None:
        unknowns.append("rooms_unknown")
    elif rooms < rmin_rooms:
        hard_fails.append(f"rooms {rooms:g} < {rmin_rooms}")

    # Move-in window knockout: the listing's own availability date must fall
    # inside criteria move_in.earliest..latest. A known date outside the window
    # is a hard fail; a missing/unparseable one goes to manual-check.
    mi_verdict, mi_detail = move_in_verdict(lst.get("availability"), crit.get("move_in", {}))
    if mi_verdict == "rejected":
        hard_fails.append(mi_detail)
    elif mi_verdict == "unknown":
        unknowns.append(mi_detail)

    # Hoodmaps category knockout (same status as rent out of range).
    excluded = [str(x).lower() for x in (crit.get("hood", {}).get("exclude_categories") or [])]
    hcat = (lst.get("hood_category") or "").lower()
    if hcat and hcat in excluded:
        hard_fails.append(f"hood category '{hcat}' excluded ({lst.get('hood_name') or '?'})")

    # Text knockouts from title + blurb: apartment swap, room-only/WG, age-gated.
    tb = " ".join(str(x) for x in (lst.get("title"), lst.get("blurb")) if x)
    if is_exchange_listing(tb, crit.get("exchange", {}).get("reject_synonyms")):
        hard_fails.append("Tauschwohnung (apartment swap required)")
    if is_room_only_listing(tb, crit.get("room_only", {}).get("reject_synonyms")):
        hard_fails.append("room-only / WG (not the whole apartment)")
    if is_age_restricted_listing(tb, crit.get("age_restricted", {}).get("reject_synonyms")):
        hard_fails.append("age-restricted / senior housing")

    # Short-term / temporary lease knockouts. A listing that SPECIFIES a rental
    # term under lease.min_months is rejected; a FIXED term of any length (the
    # lease ends — "befristet auf 12 Monate", date ranges) is rejected when
    # lease.reject_fixed_term is on; hard keywords (Zwischenmiete, sublet, …)
    # reject outright. Remaining temporary wording with no stated term goes to
    # manual-check (never a silent kill, never guessed).
    lcfg = crit.get("lease", {})
    min_months = lcfg.get("min_months")
    if min_months:
        text = " ".join(str(x) for x in (lst.get("title"), lst.get("blurb")) if x)
        term, temp_kw, fixed = lease_term_months(text)
        kw_hit = match_hard_temp_keyword(text, lcfg.get("hard_reject_keywords"))
        # ~1 week grace: a date-counted term slightly under the threshold (an
        # exactly-N-month lease reads as N-0.02 via days/30.44) shouldn't be kicked.
        if term is not None and term < float(min_months) - 0.25:
            hard_fails.append(f"temporary lease ~{term:.0f} mo (< {min_months} min)")
        elif fixed and lcfg.get("reject_fixed_term", True):
            hard_fails.append(f"fixed-term lease ~{term:.0f} mo (ends, not open-ended)")
        elif kw_hit:
            hard_fails.append(f"temporary rental ('{kw_hit}')")
        elif temp_kw and term is None:
            unknowns.append("temporary_lease?")

    return hard_fails, unknowns, flags


def _haystack(lst: dict) -> str:
    return " ".join(str(x) for x in [
        " ".join(lst.get("amenities") or []),
        lst.get("title") or "",
        lst.get("blurb") or "",
    ])


# Stop calling the transit API for the rest of the run after this many listings
# in a row exhaust their 429 backoff; the skipped ones stay transit_unknown and
# are fetched on the next run.
RATE_LIMIT_TRIP = 3


def gate_one(lst: dict, crit: dict, log: list[str], fetch_state: dict | None = None) -> None:
    fetch_state = fetch_state if fetch_state is not None else {}
    # Nice-to-haves (cheap, always refreshed; they don't reject).
    nth = detect_nice_to_haves(_haystack(lst), crit.get("nice_to_haves", {}))
    lst["has_parking"] = nth.get("parking")
    lst["has_balcony"] = nth.get("balcony")

    lst["manual_check"] = []
    lst["flags"] = []
    lst["bucket"] = None
    lst["reject_reason"] = None

    hard_fails, unknowns, flags = _evaluate_must_haves(lst, crit)
    lst["flags"] = flags
    if hard_fails:
        lst["gate_status"] = "rejected"
        lst["reject_reason"] = "; ".join(hard_fails)
        return

    # No hard fail. PLZ-only (street-less) addresses geocode to the locality
    # centroid, so the door-to-door figure is approximate — flag it.
    if not lst.get("street"):
        lst["flags"].append("PLZ-only address — commute is centroid-based (approx.)")

    tcfg = crit.get("transit", {})
    max_min = tcfg.get("max_minutes", 35)
    addr = lst.get("address")

    # Fetch raw door-to-door minutes only when we don't have them yet (or the
    # address was previously unresolved). The threshold is applied separately
    # below, so changing max_minutes re-buckets WITHOUT new API calls.
    need_fetch = lst.get("transit_min") is None and \
        lst.get("transit_status") in ("pending", "transit_unknown", None) and \
        not fetch_state.get("rate_limit_tripped")
    if need_fetch:
        if not addr:
            lst["transit_min"] = None
            lst["transit_route"] = None
            lst["transit_arrival"] = None
        else:
            res = transit_check.check(
                addr,
                to_addr=tcfg.get("office"),
                arrive_by=tcfg.get("arrive_by", "08:00"),
                max_minutes=max_min,
                n=tcfg.get("connections_to_check", 6),
            )
            lst["transit_min"] = res.get("transit_min")
            lst["transit_route"] = res.get("route")
            lst["transit_arrival"] = res.get("arrival")
            log.append(f"{lst['id']}: transit fetch — {res.get('detail','')}")
            if res.get("rate_limited"):
                hits = fetch_state.get("rate_limit_hits", 0) + 1
                fetch_state["rate_limit_hits"] = hits
                if hits >= RATE_LIMIT_TRIP:
                    fetch_state["rate_limit_tripped"] = True
                    log.append(f"transit: {hits} listings in a row rate-limited even "
                               "after backoff — skipping remaining fetches this run "
                               "(they stay transit_unknown and retry next run)")
            else:
                fetch_state["rate_limit_hits"] = 0
            time.sleep(0.25)  # be gentle on the free opendata API

    # Apply the current threshold from the cached raw minutes.
    tmin = lst.get("transit_min")
    if tmin is None:
        lst["transit_status"] = "transit_unknown"
    elif tmin <= max_min:
        lst["transit_status"] = "ok"
    else:
        lst["transit_status"] = "rejected"

    if lst["transit_status"] == "rejected":
        lst["gate_status"] = "rejected"
        lst["reject_reason"] = f"transit {tmin} min > {max_min} min"
        return
    if lst["transit_status"] == "transit_unknown":
        unknowns.append("transit_unknown")

    if unknowns:
        lst["gate_status"] = "manual"
        lst["manual_check"] = unknowns
    else:
        lst["gate_status"] = "passed"  # all must-haves verified + transit ok


def main() -> int:
    crit = config.criteria()
    store = Store.load()
    log: list[str] = []

    # Re-gate every active listing each run: must-have checks are cheap and the
    # transit API is only called when raw minutes are missing, so this keeps
    # buckets correct after any criteria change (e.g. a raised commute cap)
    # without re-hitting the API for already-resolved listings.
    candidates = store.active()
    fetch_state: dict = {}
    for lst in candidates:
        gate_one(lst, crit, log, fetch_state)

    store.save()

    tally = {"passed": 0, "manual": 0, "rejected": 0}
    for l in store.active():
        gs = l.get("gate_status")
        if gs in tally:
            tally[gs] += 1
    print(f"gate: {tally['passed']} passed, {tally['manual']} manual-check, "
          f"{tally['rejected']} rejected (of {len(candidates)} evaluated)")
    for line in log:
        print("  -", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
