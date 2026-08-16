#!/usr/bin/env python3
"""Render a first-contact outreach packet for a listing.

Detects the listing's language (de/en) from title + blurb, renders the
language-appropriate subject + body per `templates/first_contact.md`, and
surfaces the contact channels Flatfox exposes (application form, agency
website). Prints either human-readable text (default) or JSON (--json) so
the caller (Claude, when creating the Gmail draft via the MCP) has every
field it needs.

This script NEVER sends mail. It just prepares the draft contents.

    python scripts/outreach.py flatfox-86067072            # human-readable
    python scripts/outreach.py flatfox-86067072 --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import config  # noqa: E402
from applib.store import Store  # noqa: E402
from applib.text import detect_language  # noqa: E402

# The signature is read from config/applicant.yaml at render time (not import
# time) so importing this module never touches the config — the tests and the
# --json caller both rely on that.


def _address_parts(lst: dict) -> tuple[str | None, str]:
    """Return (street_or_None, locality_string)."""
    raw = (lst.get("street") or "").strip()
    # Some Flatfox street fields carry a stray comma ("Albisriederstrasse, 183").
    import re as _re
    street = _re.sub(r",\s*", " ", raw).strip() if raw else None
    z = (lst.get("zipcode") or "").strip()
    c = (lst.get("city") or "").strip().replace("Zurich", "Zürich")
    locality = " ".join(p for p in (z, c) if p) or "Zürich"
    return street, locality


def _subject(street: str | None, locality: str, lang: str) -> str:
    head = "Anfrage Mietwohnung – " if lang == "de" else "Apartment enquiry – "
    return head + (f"{street}, {locality}" if street else locality)


def _address_phrase(street: str | None, locality: str, lang: str) -> str:
    """Grammatically-correct location phrase to embed mid-sentence."""
    if lang == "de":
        return f"an der {street}" if street else f"in {locality}"
    return f"at {street}" if street else f"in {locality}"


def _size_clause(lst: dict, lang: str) -> str:
    size = lst.get("size_sqm")
    if size is None:
        return ""
    return f" mit {size} m²" if lang == "de" else f" of {size} m²"


def _rooms_str(lst: dict, lang: str) -> str:
    r = lst.get("rooms")
    if r is None:
        return "?"
    try:
        n = float(r)
        s = f"{n:g}"  # "2.5" or "3"
    except Exception:
        s = str(r)
    return s.replace(".", ",") if lang == "de" else s


_MONTHS_DE = ["", "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
              "August", "September", "Oktober", "November", "Dezember"]
_MONTHS_EN = ["", "January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]


def _fmt_date(iso, lang: str) -> str | None:
    try:
        y, m, d = (int(x) for x in str(iso).split("-")[:3])
        mo = _MONTHS_DE[m] if lang == "de" else _MONTHS_EN[m]
    except (ValueError, IndexError):
        return None
    return f"{d}. {mo} {y}" if lang == "de" else f"{d} {mo} {y}"


def _commute_clause(lst: dict, lang: str) -> str:
    try:
        t = float(lst.get("transit_min"))
    except (TypeError, ValueError):
        return ""
    # approximate to the nearest 5 min so it reads like a person ("rund 35 Minuten")
    t = max(5, int(round(t / 5.0)) * 5)
    if lang == "de":
        return (f" Von der Wohnung wären es nur rund {t} Minuten mit dem ÖV zu "
                "meinem künftigen Arbeitsort.")
    return (f" The apartment is only about {t} minutes by public transport from my "
            "future workplace.")


def _sentence(text) -> str:
    """An optional configured sentence, ready to append after another one.
    Empty/missing → "" so the surrounding copy closes up with no double space."""
    s = str(text or "").strip()
    return f" {s}" if s else ""


def _bio(lang: str, app: dict) -> str:
    """The "who I am" sentence, assembled from config/applicant.yaml → profile.

    Every fact is optional and every clause drops out cleanly when its value is
    missing — there are deliberately NO built-in defaults for age, status or
    role. A hard-coded fallback here would state something untrue about the
    applicant in a mail to a landlord, which is worse than saying nothing.
    """
    age, solo = app.get("age"), bool(app.get("single_occupant", True))
    job_start = _fmt_date(app.get("job_start"), lang)

    if lang == "de":
        status, role = app.get("status_de"), app.get("role_de")
        note = str(app.get("employment_note_de") or "").strip()
        attrs = [str(a) for a in (age, status) if a]
        lead = f"Ich bin {', '.join(attrs)} und " if attrs else "Ich "
        move = "ziehe als Einzelperson nach Zürich" if solo else "ziehe nach Zürich"
        bio = f"Zu meiner Person: {lead}{move}."
        if role:
            if job_start:
                bio += f" Am {job_start} trete ich eine Stelle als {role} an"
                bio += f" ({note})." if note else "."
            else:
                bio += f" Ich arbeite als {role}."
        return bio

    status, role = app.get("status_en"), app.get("role_en")
    note = str(app.get("employment_note_en") or "").strip()
    attrs = [str(a) for a in (age, status) if a]
    lead = f"I am {', '.join(attrs)}, and " if attrs else "I am "
    move = "moving to Zurich on my own" if solo else "moving to Zurich"
    bio = f"A little about me: {lead}{move}."
    if role:
        if job_start:
            bio += f" On {job_start} I will start a role as a {role}"
            bio += f" ({note})." if note else "."
        else:
            bio += f" I work as a {role}."
    return bio


def _who_and_dates(lst: dict, lang: str, app: dict, tim: dict) -> tuple[str, str]:
    """Return (bio_sentence, dates_sentence). Dates adapt: if the listing is only
    available after move_in_latest, align to the listing's own date."""
    avail = (lst.get("availability") or "").strip()
    latest = str(tim.get("move_in_latest") or "")
    aligned = bool(avail and latest and avail > latest)
    avail_fmt = _fmt_date(avail, lang) if aligned else None
    bio = _bio(lang, app)
    if lang == "de":
        view = tim.get("viewing_window_de", "kurzfristig")
        note = _sentence(tim.get("viewing_note_de"))
        if aligned:
            dates = (f" Wäre eine Besichtigung {view} möglich?{note} Den Einzug richte "
                     f"ich gerne nach Ihrem Termin per {avail_fmt}.")
        else:
            mv = tim.get("move_in_window_de", "")
            dates = (f" Wäre eine Besichtigung {view} möglich?{note} Beziehen könnte "
                     f"ich die Wohnung {mv}.")
    else:
        view = tim.get("viewing_window_en", "at short notice")
        note = _sentence(tim.get("viewing_note_en"))
        if aligned:
            dates = (f" Would a viewing {view} be possible?{note} I'd be glad to move in "
                     f"on your stated date of {avail_fmt}.")
        else:
            mv = tim.get("move_in_window_en", "")
            dates = (f" Would a viewing {view} be possible?{note} I would aim to move "
                     f"in {mv}.")
    return bio, dates


def render(lst: dict) -> dict:
    """Return a ready-to-send packet for the given listing."""
    haystack = " ".join(str(x) for x in (lst.get("title"), lst.get("blurb")) if x)
    lang = detect_language(haystack)

    street, locality = _address_parts(lst)
    rooms = _rooms_str(lst, lang)
    size_frag = _size_clause(lst, lang)
    subject = _subject(street, locality, lang)
    place_phrase = _address_phrase(street, locality, lang)

    ocfg = config.criteria().get("outreach") or {}
    # Timing (viewing/move-in windows) is a criteria concern; WHO is applying
    # lives in the gitignored config/applicant.yaml and never enters the repo.
    tim = ocfg.get("timing") or {}
    app = config.applicant().get("profile") or {}
    signature = config.signature()
    bio, dates = _who_and_dates(lst, lang, app, tim)
    commute = _commute_clause(lst, lang)

    if lang == "de":
        body = (
            "Guten Tag\n\n"
            f"Mit grossem Interesse habe ich Ihr Inserat für die {rooms}-Zimmer-"
            f"Wohnung{size_frag} {place_phrase} gesehen.\n\n"
            f"{bio}{commute}{dates}\n\n"
            "Ein vollständiges Bewerbungsdossier (inkl. Betreibungsauszug, "
            "Lohnnachweis bzw. Arbeitsvertrag und Ausweis) stelle ich Ihnen auf "
            "Wunsch gerne umgehend zu.\n\n"
            "Über eine kurze Rückmeldung freue ich mich.\n\n"
            "Freundliche Grüsse\n"
            f"{signature}\n"
        )
    else:
        body = (
            "Hello,\n\n"
            f"I came across your listing for the {rooms}-room apartment{size_frag} "
            f"{place_phrase} and would like to enquire about it.\n\n"
            f"{bio}{commute}{dates}\n\n"
            "A full application packet (extract from the debt-collection register "
            "/ Betreibungsauszug, payslips or employment contract, and ID) is "
            "ready on request.\n\n"
            "I look forward to hearing from you.\n\n"
            "Kind regards\n"
            f"{signature}\n"
        )

    pk = lst["id"].split("-", 1)[-1] if "-" in lst["id"] else ""
    submit_url = f"https://flatfox.ch/en/listing/{pk}/submit/" if pk else None

    return {
        "id": lst["id"],
        "language": lang,
        "subject": subject,
        "body": body,
        "to": None,                 # Flatfox doesn't expose landlord email
        "to_self": config.applicant_email(),   # your own mailbox, from applicant.yaml
        "submit_url": submit_url,   # the Flatfox application form
        "listing_url": lst.get("url"),
        "bucket": lst.get("bucket"),
        "address": lst.get("address"),
        "rooms": lst.get("rooms"),
        "size_sqm": lst.get("size_sqm"),
        "rent": lst.get("rent_gross") or lst.get("rent_net"),
        "transit_min": lst.get("transit_min"),
        "hood": (f"{lst.get('hood_name')}/{lst.get('hood_category')}"
                 if lst.get("hood_category") else None),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("listing_id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    store = Store.load()
    lst = store.listings.get(args.listing_id)
    if not lst:
        print(f"outreach: unknown listing {args.listing_id}", file=sys.stderr)
        return 1
    packet = render(lst)
    if args.json:
        print(json.dumps(packet, ensure_ascii=False, indent=2))
        return 0

    print(f"--- {packet['id']}  [{packet['language'].upper()}]  bucket {packet['bucket']} ---")
    print(f"address:   {packet['address']}  ·  {packet['rooms']} Zi  ·  {packet['size_sqm']} m²  ·  CHF {packet['rent']}  ·  {packet['transit_min']} min  ·  {packet['hood'] or '-'}")
    print(f"listing:   {packet['listing_url']}")
    print(f"submit:    {packet['submit_url']}    (Flatfox form — no landlord email is exposed)")
    print()
    print(f"Subject: {packet['subject']}")
    print()
    print(packet["body"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
