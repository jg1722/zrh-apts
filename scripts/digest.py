#!/usr/bin/env python3
"""Step 4b — Write the morning digest (data/digests/YYYY-MM-DD.md).

Sections: Bucket A, Bucket B, Manual-check (transit/field unknown), and
Changed-since-last-seen. Shows NEW listings only, plus any whose price/status
changed this run. Sorted by score, then commute, then rent. Ends with the one command to
start outreach. Also writes data/.last_summary.txt for the launchd notification.

Run:  python scripts/digest.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import config, paths  # noqa: E402
from applib.store import Store  # noqa: E402
from applib.text import UNKNOWN, fmt_money, parse_float  # noqa: E402


def _rent_for(lst: dict, basis: str):
    return lst.get("rent_net") if basis == "net" else lst.get("rent_gross")


def _sort_key(lst: dict):
    s = parse_float(lst.get("score"))
    t = parse_float(lst.get("transit_min"))
    r = parse_float(lst.get("rent_net")) or parse_float(lst.get("rent_gross"))
    return (-(s if s is not None else -1),
            t if t is not None else 9999, r if r is not None else 99999)


def _yesno(v):
    return "yes" if v is True else ("no" if v is False else UNKNOWN)


def _cond(v):
    return v if v else UNKNOWN


def _outreach(lst: dict) -> str:
    ch = lst.get("outreach_channel") or "channel_unknown"
    if ch == "email":
        return f"✉ {lst.get('outreach_email') or 'email'}"
    if ch == "onsite_now":
        return "on-site form"
    if ch == "onsite_windowed":
        win = lst.get("outreach_window")
        return f"⏳ window{(': ' + win) if win else ''}"
    return "contact?"


def _rent_display(lst: dict, basis: str) -> str:
    val = _rent_for(lst, basis)
    if parse_float(val) is not None:
        return fmt_money(val)
    other = "gross" if basis == "net" else "net"
    alt = lst.get(f"rent_{other}")
    if parse_float(alt) is not None:
        return f"{fmt_money(alt)} ({other})"
    return UNKNOWN


def _fmt_day(value) -> str | None:
    """DD.MM. for a date the digest shows inline; None if not a real date."""
    try:
        return date.fromisoformat(str(value).strip()[:10]).strftime("%d.%m.")
    except (ValueError, TypeError):
        return None


def _move_in_header(crit: dict) -> str:
    """The move-in window as a header fragment, so the digest states every
    knockout it applied. Empty when no window is configured."""
    m = crit.get("move_in") or {}
    lo, hi = _fmt_day(m.get("earliest")), _fmt_day(m.get("latest"))
    if lo and hi:
        return f" · frei ab {lo}–{hi}"
    if hi:
        return f" · frei bis {hi}"
    if lo:
        return f" · frei ab {lo}"
    return ""


def _availability(lst: dict) -> str:
    """Availability for the listing line. Now that the move-in window is a
    knockout, the date is load-bearing — show the raw free text when it isn't a
    parseable date rather than hiding it (CLAUDE.md hard rule #5)."""
    raw = str(lst.get("availability") or "").strip()
    if not raw:
        return "frei ab ?"
    day = _fmt_day(raw)
    return f"frei ab {day}" if day else f"frei ab {raw}"


def _move_in_near_misses(active: list[dict], crit: dict) -> list[str]:
    """Footer: listings the move-in window ALONE knocked out — they cleared rent,
    size, rooms, transit, lease and hood. This is the standing price of the
    window, shown so the cost of the bound stays visible instead of vanishing
    into the reject log. Widening the window in criteria brings them straight
    back: gate.py re-gates every active listing and their transit is cached."""
    m = crit.get("move_in") or {}
    if not (m.get("earliest") or m.get("latest")):
        return []
    early, late = [], []
    for lst in active:
        if lst.get("gate_status") != "rejected":
            continue
        reasons = [r.strip() for r in (lst.get("reject_reason") or "").split(";")]
        if len(reasons) != 1:
            continue  # something else rejects it too — not a near miss
        if "before move-in window" in reasons[0]:
            early.append(lst)
        elif "after move-in window" in reasons[0]:
            late.append(lst)
    if not early and not late:
        return []

    lo, hi = _fmt_day(m.get("earliest")), _fmt_day(m.get("latest"))
    out = ["---", "## Near misses — the move-in window alone", "",
           f"These cleared every other must-have and were rejected only on their "
           f"availability date (window: {lo or '…'}–{hi or '…'}). "
           f"Widen `move_in` in `config/criteria.yaml` and they return on the next "
           f"`gate.py` — no new API calls.", ""]
    if early:
        out.append(f"- **{len(early)} free too early** (before {lo}) — "
                   f"{_dates_summary(early)}")
    if late:
        out.append(f"- **{len(late)} free too late** (after {hi}) — "
                   f"{_dates_summary(late)}")
    out.append("")
    return out


def _dates_summary(rows: list[dict], top: int = 3) -> str:
    """The most common availability dates in a near-miss group, e.g.
    "26× 01.09., 4× 15.09." — shows whether one date dominates the loss."""
    counts: dict[str, int] = {}
    for lst in rows:
        day = _fmt_day(lst.get("availability"))
        if day:
            counts[day] = counts.get(day, 0) + 1
    if not counts:
        return "no parseable dates"
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    parts = ", ".join(f"{n}× {day}" for day, n in ranked)
    rest = len(rows) - sum(n for _, n in ranked)
    return parts + (f", +{rest} other" if rest > 0 else "")


def _line(lst: dict, basis: str) -> str:
    rent = _rent_display(lst, basis)
    size = lst.get("size_sqm")
    size_s = f"{size:g} m²" if isinstance(size, (int, float)) else (f"{size} m²" if size else UNKNOWN)
    rooms = lst.get("rooms")
    rooms_s = f"{rooms:g} Zi" if isinstance(rooms, (int, float)) else (f"{rooms} Zi" if rooms else UNKNOWN)
    area = " ".join(str(x) for x in (lst.get("zipcode"), lst.get("city")) if x) or UNKNOWN
    tmin = lst.get("transit_min")
    commute = f"{tmin} min" if tmin is not None else UNKNOWN
    furn = {True: "furnished", False: "unfurnished"}.get(lst.get("is_furnished"), "furnished?")
    if lst.get("hood_category"):
        hood = f"{lst.get('hood_name')}/{lst.get('hood_category')}"
    elif lst.get("latitude") is not None and lst.get("longitude") is not None:
        hood = "outside ZH map"
    else:
        hood = "hood?"
    feats = f"parking {_yesno(lst.get('has_parking'))}, balcony {_yesno(lst.get('has_balcony'))}"
    cond = f"kitchen {_cond(lst.get('condition_kitchen'))} / bath {_cond(lst.get('condition_bath'))}"
    url = lst.get("url")
    link = f"[listing]({url})" if url else UNKNOWN
    score = lst.get("score")
    score_s = f"**[{score}]** " if score is not None else ""
    return (f"- {score_s}**{lst['id']}** · CHF {rent} · {size_s} · {rooms_s} · {area} · "
            f"{commute} · {hood} · {_availability(lst)} · {furn} · {feats} · {cond} · "
            f"{_outreach(lst)} · {link}")


def _staleness_note(lst: dict, today: str, *, stale_days: int) -> str | None:
    """Warn when the pipeline hasn't confirmed the listing recently: in this
    market a flat that wasn't re-verified for a few days is often already gone.
    Falls back to last_seen/first_seen for listings that predate verification."""
    checked = lst.get("verified_at") or lst.get("last_seen") or lst.get("first_seen")
    if not checked:
        return None
    try:
        age = (date.fromisoformat(today) - date.fromisoformat(str(checked)[:10])).days
    except ValueError:
        return None
    if age <= stale_days:
        return None
    return f"⚠ last checked {age} d ago — may already be gone"


def _section(title: str, rows: list[dict], basis: str, *, empty: str = "_None today._",
             today: str | None = None, stale_days: int = 2) -> list[str]:
    out = [f"## {title}", ""]
    if not rows:
        out += [empty, ""]
        return out
    for lst in sorted(rows, key=_sort_key):
        out.append(_line(lst, basis))
        if lst.get("decision") == "outreach" or lst.get("status") in ("contacted", "replied", "viewing"):
            st = lst.get("status")
            out.append(f"    - outreach: {st}" + (f" — {lst['decision_note']}" if lst.get("decision_note") else ""))
        if lst.get("decision") == "deprioritized":
            out.append(f"    - deprioritized" + (f": {lst['decision_note']}" if lst.get("decision_note") else ""))
        if lst.get("changed") and lst.get("change_notes"):
            out.append(f"    - 🔔 changed: {'; '.join(lst['change_notes'])}")
        for cp in lst.get("crosspost_sources") or []:
            src = cp.get("source") or "?"
            cpurl = cp.get("url")
            out.append(f"    - also on: {f'[{src}]({cpurl})' if cpurl else src} "
                       f"(kept this copy — better outreach)")
        if lst.get("bucket_gap"):
            out.append(f"    - gap: {lst['bucket_gap']}")
        if lst.get("condition_reason"):
            out.append(f"    - condition note: {lst['condition_reason']}")
        for flag in lst.get("flags") or []:
            if flag.startswith("rent uses"):
                continue  # already shown inline in the rent column
            out.append(f"    - note: {flag}")
        if lst.get("manual_check"):
            out.append(f"    - manual: {', '.join(lst['manual_check'])}")
        if today:
            stale = _staleness_note(lst, today, stale_days=stale_days)
            if stale:
                out.append(f"    - {stale}")
    out.append("")
    return out


def main() -> int:
    paths.ensure_dirs()
    crit = config.criteria()
    basis = crit["rent"].get("basis", "net")
    today = paths.today_iso()
    store = Store.load()

    active = store.active()
    # Digest visibility is gated by DECISION, not by whether a listing was seen
    # before: an undecided listing keeps appearing in its bucket every run. A
    # decision moves it to its own list.
    def decided_outreach(l):
        return l.get("decision") == "outreach" or l.get("status") in ("contacted", "replied", "viewing")

    def decided_deprio(l):
        return l.get("decision") == "deprioritized"

    def undecided(l):
        return not decided_outreach(l) and not decided_deprio(l)

    a = [l for l in active if l.get("gate_status") == "passed" and l.get("bucket") == "A" and undecided(l)]
    b = [l for l in active if l.get("gate_status") == "passed" and l.get("bucket") == "B" and undecided(l)]
    manual = [l for l in active if l.get("gate_status") == "manual" and undecided(l)]
    out_list = [l for l in active if decided_outreach(l)]
    deprio = [l for l in active if decided_deprio(l)]

    tcfg = crit.get("transit", {})
    # German date fragments already end in a period ("…–15.09."), so close the
    # sentence only when the last segment doesn't do it for us.
    head = (f"Office: {tcfg.get('office')} · gate: arrive by {tcfg.get('arrive_by')} "
            f"in ≤{tcfg.get('max_minutes')} min · rent {crit['rent']['min']}–{crit['rent']['max']} CHF "
            f"({basis}) · ≥{crit['size']['min_sqm']} m² · ≥{crit['rooms']['min']} Zi"
            f"{_move_in_header(crit)}")
    lines: list[str] = [
        f"# Zürich flat digest — {today}",
        "",
        f"_{head}{'' if head.endswith('.') else '.'}_",
        "",
        "Every undecided match stays listed until you decide — `outreach <id>` or "
        "`deprioritize <id>`. 🔔 marks a price/status change since last run. "
        "**[score]** is the 0–100 fit rank; sorted by score, then commute, then rent.",
        "",
    ]
    stale_days = int((crit.get("staleness") or {}).get("stale_after_days", 2))
    lines += _section("Bucket A — strong match", a, basis,
                      today=today, stale_days=stale_days)
    lines += _section("Bucket B — worth a look", b, basis,
                      today=today, stale_days=stale_days)
    lines += _section("Manual-check — needs a human (transit/field unknown)", manual, basis,
                      empty="_None pending._", today=today, stale_days=stale_days)
    lines += _section("Outreach — decided to contact", out_list, basis,
                      empty="_None yet._", today=today, stale_days=stale_days)
    lines += _section("Deprioritized — decided not to contact", deprio, basis,
                      empty="_None yet._", today=today, stale_days=stale_days)

    lines += _move_in_near_misses(active, crit)

    lines += [
        "---",
        "## Decide on a listing",
        "",
        "Tell Claude:  `outreach <id>`  (draft a first-contact email — you approve every send) "
        "or  `deprioritize <id>`  (skip it). Either way it leaves the buckets above and moves "
        "to its list. Until then it keeps showing.",
        f"E.g. `outreach {a[0]['id'] if a else (b[0]['id'] if b else 'flatfox-XXXXX')}`",
        "",
    ]

    out_path = paths.digest_file(today)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    summary = (f"Bucket A: {len(a)} · B: {len(b)} · manual: {len(manual)} · "
               f"outreach: {len(out_list)} · deprioritized: {len(deprio)}")
    paths.SUMMARY_FILE.write_text(summary, encoding="utf-8")

    print(f"digest: wrote {out_path}")
    print(f"  {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
