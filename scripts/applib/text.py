"""Text + value normalisation. The cardinal rule: a value we cannot determine
is None (rendered as "unknown" downstream). We never guess."""
from __future__ import annotations

import math
import re
import unicodedata

UNKNOWN = "unknown"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two WGS84 points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def norm(s: str | None) -> str:
    """Lowercase, strip accents/extra whitespace — for substring matching."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# German transliteration newhome uses in its SEO slugs (ü→ue, not bare "u").
_DE_TRANSLIT = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "ae", "Ö": "oe", "Ü": "ue",
})


def slugify_de(s: str | None) -> str:
    """newhome-style slug: German umlauts spelled out, accents stripped, lowercased,
    non-alphanumerics collapsed to single hyphens."""
    if not s:
        return ""
    s = s.translate(_DE_TRANSLIT)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s


# newhome's detail route renders ONLY when the URL carries a search-context query
# string; with the path alone it shows "not found". The query is NOT validated
# against the listing (a real working URL for listing 6113804 in Zürich carried
# location=1;2560, which is a *different* location than the listing's own 2;2566,
# and still rendered) — the detail component loads by the immocode in the path and
# treats the query as carried search context. propertyType=2 (apartment) and
# offerType=2 (rent) are constant across our entire dataset. We therefore append a
# fixed, known-good query verbatim. (Confirmed 2026-06-15 against a user-supplied
# live URL; newhome blocks automated rendering so this can't be CI-verified.)
_NEWHOME_DETAIL_QUERY = (
    "?propertyType=2&offerType=2&location=1;2560"
    "&skipCount=0&rowCount=20&propertySubtypes=201"
)


# newhome image URLs embed the listing's canonical slug:
#   /res/{immocode}/ort-{city}/{street}/{type}/{subtype}-{photoid}-{variant}.jpg
# The detail route ENFORCES this {type}/{subtype}/ort-{city} slug (a wrong subtype
# 404s, e.g. a maisonette served as plain "wohnung"), so we extract it from a
# photo rather than guess. Returns (city_slug, type, subtype) or None.
_NEWHOME_PHOTO_SLUG = re.compile(
    r"/res/\d+/(ort-[^/]+)/[^/]+/([^/]+)/([^/]+?)-\d")


def _newhome_slug_from_photo(photo_url: str | None):
    if not photo_url:
        return None
    m = _NEWHOME_PHOTO_SLUG.search(photo_url)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def newhome_detail_url(immocode,
                       *,
                       city: str | None = None,
                       rooms=None,
                       photo_url: str | None = None,
                       ptype: str = "wohnung",
                       subtype: str = "wohnung") -> str | None:
    """Build newhome's public SEO detail URL (path + required context query).

    Route: /de/mieten/immobilien/{type}/{subtype}/ort-{city}/{rooms}-zimmer/detail/{immocode}
    plus a fixed query string (see _NEWHOME_DETAIL_QUERY) without which the SPA
    renders a not-found page. The {type}/{subtype}/ort-{city} slug is enforced, so
    prefer the canonical slug embedded in a listing photo URL; fall back to the
    city field + plain "wohnung" only when no photo is parseable.
    Returns None when there is no immocode (we never fabricate a link).
    """
    if immocode in (None, ""):
        return None
    parts = _newhome_slug_from_photo(photo_url)
    if parts:
        city_seg, typ, sub = parts  # already slugified by newhome (city_seg incl. "ort-")
    else:
        city_seg = "ort-" + (slugify_de(city) or "schweiz")
        typ, sub = slugify_de(ptype) or "wohnung", slugify_de(subtype) or "wohnung"
    rooms_f = parse_float(rooms)
    rooms_seg = f"{rooms_f:g}-zimmer" if rooms_f is not None else "wohnung"
    return ("https://www.newhome.ch/de/mieten/immobilien/"
            f"{typ}/{sub}/{city_seg}/{rooms_seg}/detail/{immocode}"
            + _NEWHOME_DETAIL_QUERY)


def detect_nice_to_haves(haystack: str, nice_to_haves: dict) -> dict[str, bool]:
    """Return {feature: True/False} by substring-matching German synonyms.
    haystack should bundle amenities + title + description."""
    h = norm(haystack)
    out: dict[str, bool] = {}
    for feature, spec in (nice_to_haves or {}).items():
        syns = spec.get("synonyms", []) if isinstance(spec, dict) else []
        out[feature] = any(norm(syn) in h for syn in syns)
    return out


# --- Tauschwohnung (apartment-swap) detection ------------------------------- #
# Swaps where the lister only rents in exchange for a flat in return. Must catch
# non-adjacent phrasing ("Willst du deine Wohnung … tauschen?"), the reverse
# ("Wir tauschen unsere Wohnung … gegen …"), bare swap phrases, AND English
# ("open to exchanges for a bigger apartment"). Must NOT catch renovation/fixture
# wording ("Fenster tauschen", "Küchentausch") or "Austausch mit den Nachbarn".
_TAUSCH_STEM = r"tausch(?:e|en|t|st|s)?"          # standalone verb/noun stem
_NEAR_DE = r"(?:\W+\w+){0,12}?\W+"                # up to ~12 words between
_EXCHANGE_DE = re.compile(
    r"\bwohnung\w*" + _NEAR_DE + r"\b" + _TAUSCH_STEM + r"\b"          # wohnung … tausch
    + r"|\b" + _TAUSCH_STEM + r"\b" + _NEAR_DE + r"\w*wohnung\w*\b"     # tausch … wohnung
    + r"|\bzum tausch\b|\bim tausch\b|\btausch gegen\b|\bgegen tausch\b"
    + r"|\btausch nach\b|\blass uns " + _TAUSCH_STEM + r"\b"
    + r"|\bwohnungstausch\b|\btauschwohnung\b|\btauschpartner\b|\btauschobjekt\b")
_EXCHANGE_EN = re.compile(
    r"\b(?:exchange|swap)\w*(?:\W+\w+){0,8}?\W+(?:apartment|flat|home|place|wohnung)\b"
    r"|\b(?:apartment|flat|home|place)\b(?:\W+\w+){0,8}?\W+(?:exchange|swap)\w*\b")
# Fixture replacement — strip these "<fixture> tauschen" hits before the DE test
# so renovation wording can't trigger the wohnung-proximity rule.
_RENOVATION_TAUSCH = re.compile(
    r"\b(?:fenster|kuche|kuchen|boden|teppich|heizung|boiler|herd|backofen|"
    r"kuhlschrank|geschirrspuler|leitung\w*|sanitar\w*|gerat\w*|lampe\w*|"
    r"armatur\w*)\W+(?:aus|um)?" + _TAUSCH_STEM)


def is_exchange_listing(text: str | None, synonyms=None) -> bool:
    """True if the text marks a Tauschwohnung (apartment swap). Works on accent-
    stripped, lowercased text. `synonyms` (optional, from criteria.yaml) are extra
    explicit substrings; the regexes below do the heavy lifting (DE + EN), with a
    renovation-wording guard to avoid false positives."""
    h = norm(text)
    if not h:
        return False
    if synonyms and any(norm(s) in h for s in synonyms):
        return True
    # neutralise "<fixture> tauschen" so renovation copy can't trigger the rule
    h_de = _RENOVATION_TAUSCH.sub(" ", h)
    return bool(_EXCHANGE_DE.search(h_de) or _EXCHANGE_EN.search(h))


# --- room-only / WG (not the whole apartment) ------------------------------- #
# Must catch genuine room/flatmate listings ("Mitbewohner:in gesucht", "WG-Zimmer",
# "Zimmer in einer WG", "4er WG", "room in a shared flat") WITHOUT flagging whole
# flats merely advertised as suitable for a WG ("ideal für Singles, Paare oder eine
# WG", "auch als WG geeignet", "(no WG)") — so bare "WG"/"Wohngemeinschaft" alone
# is NOT enough; we require room/flatmate-seeking intent.
_ROOM_ONLY = re.compile(
    r"\bmitbewohner\w*\b(?:\W+\w+){0,4}?\W+gesucht\b"          # Mitbewohner:in … gesucht
    r"|\bsuche\b(?:\W+\w+){0,5}?\W+mitbewohner"                # (ich) suche … Mitbewohner
    r"|\b\d+er[\s-]?wg\b"                                       # "4er WG" — an N-person WG
    r"|\bwg[\s-]?zimmer\b"                                      # WG-Zimmer
    r"|\bzimmer\s+in\s+(?:einer\s+|der\s+)?(?:wg|wohngemeinschaft)"  # Zimmer in (einer) WG
    r"|\bmobliertes\s+zimmer\b"                                 # möbliertes Zimmer
    r"|\broom\s+in\s+a\s+shared\b|\bflatshare\b|\bflatmate\b"
    r"|\bspare\s+room\b|\bshared\s+room\b")


def is_room_only_listing(text: str | None, synonyms=None) -> bool:
    """True if the listing rents a single ROOM / WG place rather than the whole
    flat. `synonyms` (from criteria.yaml) are extra unambiguous substrings; the
    regex requires room/flatmate-seeking intent so "suitable for a WG" whole flats
    are not falsely rejected."""
    h = norm(text)
    if not h:
        return False
    if synonyms and any(norm(s) in h for s in synonyms):
        return True
    return bool(_ROOM_ONLY.search(h))


# --- age-restricted / senior / assisted living ------------------------------ #
# Numeric age gate: "55+", "60 plus", "ab 55 Jahren", "für Menschen ab 60".
# Guarded so "ab 60 m²" or a bare "altersgerecht" (accessibility, not a gate)
# do NOT trigger — an age number needs +/plus or a "Jahr"/"Menschen ab" context.
_AGE_RESTRICT = re.compile(
    r"\b(?:5[5-9]|6\d|7\d)\s*(?:\+|plus\b)"
    r"|\bab\s*(?:5[5-9]|6\d|7\d)\s*jahr"
    r"|\b(?:menschen|personen|mieter\w*|bewohner\w*)\s+ab\s*(?:5[5-9]|6\d|7\d)\b")


def is_age_restricted_listing(text: str | None, synonyms=None) -> bool:
    """True for age-gated / senior / assisted-living housing. `synonyms` carries
    the explicit terms (Alterswohnung, betreutes Wohnen, …); the regex catches
    the numeric age patterns ("ab 55 Jahren", "55+")."""
    h = norm(text)
    if not h:
        return False
    if synonyms and any(norm(s) in h for s in synonyms):
        return True
    return bool(_AGE_RESTRICT.search(h))


def parse_float(value) -> float | None:
    """Best-effort numeric parse. Returns None (unknown) if not parseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"\d+(?:[.,]\d+)?", str(value).replace("'", "").replace("’", ""))
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def fmt_money(value) -> str:
    n = parse_float(value)
    return UNKNOWN if n is None else f"{int(round(n)):,}".replace(",", "'")


def fmt_num(value, suffix: str = "") -> str:
    n = parse_float(value)
    if n is None:
        return UNKNOWN
    s = str(int(n)) if n == int(n) else f"{n:g}"
    return f"{s}{suffix}"


_GERMAN_TOKENS = (
    "zimmer", "wohnung", "möbliert", "mieten", "monat", "stockwerk",
    "mietzins", "nettomiete", "bruttomiete", "ab sofort", "nach vereinbarung",
    "sehr geehrte", "besichtigung", "küche", "bad", "balkon", "auf wunsch",
    "vermieter", "mieter", "unbefristet",
)
_ENGLISH_TOKENS = (
    "apartment", "rooms", "furnished", "monthly", "available", "near",
    "close to", "floor", "rent", "viewing", "kind regards", "kitchen",
    "bathroom", "balcony", "landlord", "tenant", "please", "located",
)


def detect_language(text: str | None) -> str:
    """Return 'de' or 'en' by keyword voting. Defaults to 'de' on tie / empty
    (the Zürich market is German by default)."""
    t = norm(text)
    if not t:
        return "de"
    de = sum(1 for k in _GERMAN_TOKENS if k in t)
    en = sum(1 for k in _ENGLISH_TOKENS if k in t)
    return "en" if en > de else "de"


def zip_in_scope(zipcode: str | None, prefixes: list[str]) -> bool:
    z = (zipcode or "").strip()
    return any(z.startswith(p) for p in prefixes)


# --- temporary / short-term lease detection --------------------------------- #
# norm() strips accents, so month names here are accent-free ("marz", not "März").
_DE_MONTHS = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "marz": 3, "mar": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10,
    "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}
# Note: "mietdauer" is deliberately NOT here — it's a neutral field label ("rental
# period") that appears in long-term listings too. Explicit durations next to it
# are still read by _FIXED_TERM_RE / _MIN_COMMIT_RE / _RANGE_RE below.
_TEMP_KEYWORDS = re.compile(
    r"zwischenmiete|zwischennutzung|zwischenmietverhaltnis|(?<!un)befristet|temporar|"
    r"mobliert auf zeit|auf zeit|untermiete|temporary|fixed[- ]?term|"
    r"limited (?:period|duration|term)|sublet|short[- ]?term")
# A start date carries a year (numeric 15.07.2026 or name 15. juli 2026); the end
# of a range may omit the year ("8. September 2026 bis 27. September") — we inherit
# the start's year. Post-norm, so month names are accent-free.
_DATE_FULL = r"\d{1,2}\.\s*(?:\d{1,2}\.\s*\d{2,4}|[a-z]+\s+\d{4})"
_DATE_ANY = r"\d{1,2}\.\s*(?:\d{1,2}\.?\s*\d{0,4}|[a-z]+(?:\s+\d{4})?)"
_RANGE_RE = re.compile(r"(" + _DATE_FULL + r")\s*(?:bis|to|until|[-–])\s*(" + _DATE_ANY + r")")
# Minimum-commitment phrasing ("min. 5 Monate", "Mindestmietdauer 12 Monate") —
# a floor on an open-ended lease. Counted against lease.min_months but does NOT
# mark the listing fixed-term.
_MIN_COMMIT_RE = re.compile(
    r"(?:mind(?:estens)?\.?|min\.?|minimum|mindestmietdauer(?:\s+von)?)\s*"
    r"(\d{1,2})\s*(?:monat\w*|months?|mte?)\b")
# Fixed-term phrasing ("befristet auf 4 Monate", "für 8 Monate", "Mietdauer 6
# Monate", "for 3 months") — the lease ENDS after N months.
_FIXED_TERM_RE = re.compile(
    r"(?:fur|for|befristet (?:auf|fur)|"
    r"(?<!mindest)mietdauer(?:\s+von)?|mietzeit(?:\s+von)?)\s*"
    r"(\d{1,2})\s*(?:monat\w*|months?|mte?)\b")
_ANY_MONTHS_RE = re.compile(r"(\d{1,2})\s*(?:monat\w*|months?)\b")


def _parse_date(s: str, default_year: int | None = None):
    import datetime
    s = s.strip()
    d = mo = y = None
    m = re.match(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2,4})", s)        # DD.MM.YYYY
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
    elif re.match(r"(\d{1,2})\.\s*(\d{1,2})\.?\s*$", s):            # DD.MM (no year)
        m = re.match(r"(\d{1,2})\.\s*(\d{1,2})", s)
        d, mo, y = m.group(1), m.group(2), default_year
    elif re.match(r"(\d{1,2})\.\s*([a-z]+)\s+(\d{4})", s):          # DD. month YYYY
        m = re.match(r"(\d{1,2})\.\s*([a-z]+)\s+(\d{4})", s)
        d, mo, y = m.group(1), _DE_MONTHS.get(m.group(2)), m.group(3)
    elif re.match(r"(\d{1,2})\.\s*([a-z]+)\s*$", s):               # DD. month (no year)
        m = re.match(r"(\d{1,2})\.\s*([a-z]+)", s)
        d, mo, y = m.group(1), _DE_MONTHS.get(m.group(2)), default_year
    if d is None or mo is None or y is None:
        return None
    try:
        yr = int(y)
        yr += 2000 if yr < 100 else 0
        return datetime.date(yr, int(mo), int(d))
    except (ValueError, TypeError):
        return None


def lease_term_months(text: str | None) -> tuple[float | None, bool, bool]:
    """Detect a SPECIFIED rental term, for the short/fixed-lease knockouts.

    Returns (term_months, has_temp_keyword, is_fixed_term):
      * term_months — the shortest explicitly-stated term we can read, from a
        date range ("ab X bis Y"), a fixed-term phrase ("befristet auf N
        Monate") or a minimum-commitment phrase ("min. N Monate"); None if
        none is stated. We never guess a term.
      * has_temp_keyword — True if temporary-rental wording is present even
        without a parseable duration (caller may route that to manual-check).
      * is_fixed_term — True when the stated term ENDS the lease (date range /
        "befristet auf N" / temp wording + bare "N Monate"). A pure minimum
        commitment ("Mindestmietdauer 12 Monate") is NOT fixed-term.
    """
    t = norm(text)
    if not t:
        return (None, False, False)
    temp_kw = bool(_TEMP_KEYWORDS.search(t))
    fixed_c: list[float] = []
    min_c: list[float] = []
    bare_c: list[float] = []
    for m in _MIN_COMMIT_RE.finditer(t):
        min_c.append(float(m.group(1)))
    for m in _FIXED_TERM_RE.finditer(t):
        fixed_c.append(float(m.group(1)))
    for a, b in _RANGE_RE.findall(t):
        d1 = _parse_date(a)
        d2 = _parse_date(b, default_year=d1.year if d1 else None)
        if d1 and d2 and d2 > d1:
            fixed_c.append((d2 - d1).days / 30.44)
    # A bare "N Monate" only counts when the listing is flagged temporary, so we
    # don't misread "ab 3 Monaten kündbar" (a long lease) as a 3-month term.
    if temp_kw:
        for m in _ANY_MONTHS_RE.finditer(t):
            bare_c.append(float(m.group(1)))
    candidates = fixed_c + min_c + bare_c
    fixed = bool(fixed_c) or bool(temp_kw and bare_c and not min_c)
    return (min(candidates) if candidates else None, temp_kw, fixed)


def match_hard_temp_keyword(text: str | None, keywords: list[str] | None) -> str | None:
    """First configured hard-reject temp keyword found in the text, or None.

    Matched accent-stripped/lowercased as substrings, so "Zwischenmiete" and
    "Untermieter" hit "zwischenmiete"/"untermiete". The keyword list lives in
    criteria.yaml (lease.hard_reject_keywords) so it stays user-tunable."""
    h = norm(text)
    if not h:
        return None
    for kw in (keywords or []):
        k = norm(kw)
        if k and k in h:
            return kw
    return None
