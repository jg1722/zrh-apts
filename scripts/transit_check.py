#!/usr/bin/env python3
"""Transit knockout gate — the one rule that can reject any listing.

Computes door-to-door public-transport time from a listing address to the office
using the free Swiss API (transport.opendata.ch, no key). Picks the fastest
connection ARRIVING AT OR BEFORE the target time on the next weekday.

NEVER invents a time. If the address can't be resolved or the API errors, the
result status is "transit_unknown" so the listing is surfaced for manual review.

CLI (for spot-checking resolution on early runs):
    python scripts/transit_check.py "Birmensdorferstrasse 100, 8003 Zürich"
    python scripts/transit_check.py "Bahnhofstrasse 1, 8001 Zürich" --to "Manessestrasse 2, 8003 Zürich" --arrive 08:00
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

import requests

try:  # allow running as a module or as a bare script
    from applib import config
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from applib import config

API_URL = "https://transport.opendata.ch/v1/connections"

# The free API throttles bursts with HTTP 429; back off and retry before
# giving up on a listing (2026-06-11: a cold run 429'd ~80 listings at once).
RATE_LIMIT_ATTEMPTS = 4
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0


def _get_with_backoff(params: dict) -> "requests.Response":
    """GET API_URL, retrying on 429 with Retry-After/exponential backoff.

    Raises requests.HTTPError (status 429) if every attempt is throttled.
    """
    delay = BACKOFF_BASE_S
    for attempt in range(RATE_LIMIT_ATTEMPTS):
        resp = requests.get(API_URL, params=params, timeout=25,
                            headers={"User-Agent": "zrh-apts-transit/1.0"})
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        if attempt < RATE_LIMIT_ATTEMPTS - 1:
            try:
                wait = max(float(resp.headers.get("Retry-After", "")), delay)
            except (TypeError, ValueError):
                wait = delay
            time.sleep(min(wait, BACKOFF_CAP_S))
            delay *= 2
    resp.raise_for_status()
    return resp  # pragma: no cover — raise_for_status always raises on 429


def next_weekday(from_date: dt.date | None = None) -> dt.date:
    """The soonest Mon–Fri on or after from_date (default today)."""
    d = from_date or dt.date.today()
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d += dt.timedelta(days=1)
    return d


def parse_duration(s: str | None) -> int | None:
    """'00d00:14:00' (DDd HH:MM:SS) -> total minutes. None if unparseable."""
    if not s or "d" not in s or ":" not in s:
        return None
    try:
        days_part, hms = s.split("d", 1)
        hh, mm, ss = (hms.split(":") + ["0", "0"])[:3]
        return int(days_part) * 1440 + int(hh) * 60 + int(mm) + round(int(ss) / 60)
    except (ValueError, TypeError):
        return None


def _route_summary(conn: dict) -> str:
    products = [p for p in (conn.get("products") or []) if p and p.strip()]
    if products:
        return ", ".join(p.strip() for p in products)
    legs = []
    for sec in conn.get("sections", []):
        j = sec.get("journey")
        if j and j.get("name"):
            legs.append(j.get("category", "") + str(j.get("number") or j.get("name")))
    return " → ".join(legs) if legs else "walk"


def check(from_addr: str,
          to_addr: str | None = None,
          arrive_by: str = "08:00",
          max_minutes: int = 30,
          date: str | None = None,
          n: int = 6) -> dict:
    """Return a result dict describing the best qualifying connection."""
    crit = {}
    try:
        crit = (config.criteria() or {}).get("transit", {})
    except Exception:
        pass  # CLI use without a config is fine
    to_addr = to_addr or crit.get("office") or "Manessestrasse 2, 8003 Zürich"
    target_date = date or next_weekday().isoformat()

    result = {
        "status": "transit_unknown",
        "transit_min": None,
        "arrival": None,
        "route": None,
        "date": target_date,
        "arrive_by": arrive_by,
        "from": from_addr,
        "to": to_addr,
        "detail": "",
        "rate_limited": False,
    }

    params = {
        "from": from_addr,
        "to": to_addr,
        "date": target_date,
        "time": arrive_by,
        "isArrivalTime": 1,
        "limit": n,
    }
    try:
        resp = _get_with_backoff(params)
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429:
            result["rate_limited"] = True
            result["detail"] = "API rate limited (HTTP 429) — retries exhausted"
        else:
            result["detail"] = f"API error: {exc}"
        return result

    conns = payload.get("connections") or []
    # Address resolution check: opendata echoes the requested name back even when
    # it can't geocode it, but a totally unresolved address yields no connections.
    if not conns:
        result["detail"] = "no connections returned (address may be unresolvable)"
        return result

    best = None  # (minutes, arrival_str, route)
    for c in conns:
        arrival = (c.get("to") or {}).get("arrival")
        minutes = parse_duration(c.get("duration"))
        if not arrival or minutes is None:
            continue
        arr_date, arr_time = arrival[0:10], arrival[11:16]
        # Must arrive on the target weekday, at or before the cutoff.
        if arr_date != target_date or arr_time > arrive_by:
            continue
        if best is None or minutes < best[0]:
            best = (minutes, arrival, _route_summary(c))

    if best is None:
        result["detail"] = f"no connection arrives by {arrive_by} on {target_date}"
        return result

    minutes, arrival, route = best
    result.update({"transit_min": minutes, "arrival": arrival, "route": route})
    if minutes <= max_minutes:
        result["status"] = "ok"
        result["detail"] = f"{minutes} min door-to-door, arr {arrival[11:16]}"
    else:
        result["status"] = "rejected"
        result["detail"] = f"{minutes} min > {max_minutes} min cap"
    return result


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Transit gate check to the office.")
    ap.add_argument("address", help="listing address, e.g. 'Street 1, 8003 Zürich'")
    ap.add_argument("--to", dest="to_addr", default=None, help="override office address")
    ap.add_argument("--arrive", default=None, help="arrive-by time, default from criteria.yaml")
    ap.add_argument("--max", type=int, default=None, help="max minutes, default from criteria.yaml")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD, default next weekday")
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    args = ap.parse_args()

    crit = {}
    try:
        crit = (config.criteria() or {}).get("transit", {})
    except Exception:
        pass
    res = check(
        args.address,
        to_addr=args.to_addr,
        arrive_by=args.arrive or crit.get("arrive_by", "08:00"),
        max_minutes=args.max if args.max is not None else crit.get("max_minutes", 30),
        date=args.date,
        n=crit.get("connections_to_check", 6),
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        icon = {"ok": "PASS", "rejected": "FAIL", "transit_unknown": "UNKNOWN"}.get(res["status"], "?")
        print(f"[{icon}] {res['from']}  ->  {res['to']}")
        print(f"  date {res['date']} arrive-by {res['arrive_by']}")
        print(f"  {res['detail']}")
        if res["route"]:
            print(f"  route: {res['route']}  (arr {res['arrival'][11:16] if res['arrival'] else '—'})")
    return 0 if res["status"] == "ok" else (2 if res["status"] == "rejected" else 3)


if __name__ == "__main__":
    raise SystemExit(_cli())
