# First-contact template

> This is the *reference* for the copy that `scripts/outreach.py` generates. The
> script renders it from config — you do not fill this file in by hand.
> **Facts** come from `config/applicant.yaml` (gitignored), **timing wording**
> from `config/criteria.yaml` → `outreach.timing`, **apartment details** from the
> listing itself.
>
> If you drop your own application-email sample into `templates/`, MIRROR its
> wording, tone and structure instead of the skeleton below. **Pick the language
> of the listing** (German if the listing is German, English if English —
> detected by `scripts/outreach.py`, default German).

**Rules**
- German uses formal "Sie", but the tone is approachable: greet with "Guten Tag" / "Hello" — never "Sehr geehrte Damen und Herren" / "Dear Sir or Madam".
- ASK for a viewing (wording lives in `config/criteria.yaml` → `outreach.timing`).
- No "I'm particularly drawn to…" / "Besonders reizvoll finde ich…" filler line.
- Opener IDENTIFIES the apartment (street + rooms + size) — **no rent figure**.
- One sentence on who the applicant is (relocation, role, life situation), built from `config/applicant.yaml` → `profile`.
- Mention the Bewerbungsdossier / application packet is ready on request — do NOT attach it.
- No fluff. Skip any placeholder whose value is missing (don't leave `{...}` in the draft).

---

## German version (default)

**Subject:** Anfrage Mietwohnung – {street_or_area}, {plz} {city}

Guten Tag

Mit grossem Interesse habe ich Ihr Inserat für die {rooms}-Zimmer-Wohnung mit {size} m² an der {street_or_area} gesehen.

Zu meiner Person: Ich bin {age}, {status_de} und ziehe als Einzelperson nach Zürich. Am {job_start} trete ich eine Stelle als {role_de} an ({employment_note_de}). Von der Wohnung wären es nur rund {commute} Minuten mit dem ÖV zu meinem künftigen Arbeitsort. Wäre eine Besichtigung {viewing_window_de} möglich? {viewing_note_de} Beziehen könnte ich die Wohnung {move_in_window_de}.

Ein vollständiges Bewerbungsdossier (inkl. Betreibungsauszug, Lohnnachweis bzw. Arbeitsvertrag und Ausweis) stelle ich Ihnen auf Wunsch gerne umgehend zu.

Über eine kurze Rückmeldung freue ich mich.

Freundliche Grüsse
{name}
{phone}
{email}

---

## English version (only if the listing is in English)

**Subject:** Apartment enquiry – {street_or_area}, {plz} {city}

Hello,

I came across your listing for the {rooms}-room apartment of {size} m² at {street_or_area} and would like to enquire about it.

A little about me: I am {age}, {status_en}, and moving to Zurich on my own. On {job_start} I will start a role as a {role_en} ({employment_note_en}). The apartment is only about {commute} minutes by public transport from my future workplace. Would a viewing {viewing_window_en} be possible? {viewing_note_en} I would aim to move in {move_in_window_en}.

A full application packet (extract from the debt-collection register / Betreibungsauszug, payslips or employment contract, and ID) is ready on request.

I look forward to hearing from you.

Kind regards
{name}
{phone}
{email}

---

## Where each placeholder comes from

**From the listing:**
- `{street_or_area}` — `street` if present, else "{plz} {city}".
- `{plz}`, `{city}` — straight from the listing.
- `{rooms}` — e.g. "2.5" or "3.0" (German renders "2,5").
- `{size}` — m² number; if missing, the "mit {size} m²" / "of {size} m²" fragment is dropped entirely.
- `{commute}` — the gate's door-to-door minutes, rounded to the nearest 5 so it reads like a person ("rund 35 Minuten"). The whole sentence is dropped when the commute is unknown.

**From `config/applicant.yaml` → `profile`** (gitignored — your personal data):
- `{name}`, `{phone}`, `{email}` — the signature block, from `identity`. A blank field drops its line.
- `{age}`, `{status_de}` / `{status_en}`, `{role_de}` / `{role_en}`, `{job_start}`, `{employment_note_de}` / `{employment_note_en}`.
- Every one of these is optional. Missing values drop their clause rather than
  falling back to a default — a hard-coded default here would state something
  untrue about you in a mail to a landlord.
- `single_occupant: false` swaps "als Einzelperson nach Zürich" → "nach Zürich".
- `job_start: null` (already employed) swaps the whole sentence to "Ich arbeite als {role_de}." / "I work as a {role_en}."

**From `config/criteria.yaml` → `outreach.timing`:**
- `{viewing_window_de}` / `{viewing_window_en}` — an **adverbial** that fits "Wäre eine Besichtigung … möglich?" (e.g. "kurzfristig"). A value containing sentence punctuation produces broken German; there is a test asserting this.
- `{viewing_note_de}` / `{viewing_note_en}` — an optional extra sentence appended after the viewing ask. Empty string drops it.
- `{move_in_window_de}` / `{move_in_window_en}` — the move-in window in prose. **Must describe the same window as the `move_in` block** in the same file; they drifted apart once and every draft then contradicted the filter. `test_outreach.py` asserts `move_in_latest` matches `move_in.latest`.

**Adaptive behaviour:** if the listing is available only *after* `move_in_latest`, the draft drops the standard window and aligns to the listing's own date instead ("Den Einzug richte ich gerne nach Ihrem Termin per 1. Dezember 2027.").
