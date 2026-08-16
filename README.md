# ZRH APTS — a Zürich flat-hunting pipeline

Finding a flat in Zürich is a throughput problem: good listings are gone in
days, and the ones worth your time are buried under swaps, WG rooms, temporary
sublets and mass-reposted spam. This runs every morning, scrapes the portals,
throws out everything that fails your hard constraints, gates the rest on
**actual door-to-door commute time** to the office, ranks the survivors, and
hands you a short digest to read over coffee. On your say-so it drafts the
landlord email. It never sends anything by itself.

Built for one person's search in summer 2026 and since **genericised** — every
personal number has been replaced with a flagged default you're expected to
change. It is a working tool, not a polished product: expect rough edges, and
fix them to taste.

> **Status:** unmaintained. Shared as a starting point, not a supported project.
> Python 3.9+, macOS-oriented (launchd scheduling, `open`/notification hooks).

---

## What it actually does

```
        config/criteria.yaml ── what you want (budget, size, commute, weights)
        config/applicant.yaml ─ who you are (name, contact)   ← gitignored
                    │
   ┌────────────────┴────────────────────────────────────────────────────┐
   │                                                                     │
 1 │ SCOUT           Flatfox + Comparis → normalise → dedup across sites  │  free
   │                 scout.py · store.py · clusters.py                    │
   ├─────────────────────────────────────────────────────────────────────┤
 2 │ MUST-HAVES      rent · size · rooms · move-in window · lease term    │  free
   │  knockouts ✕    flat swaps · WG rooms · age-gated · temporary lets   │
   │                 gate.py · text.py                                    │
   ├─────────────────────────────────────────────────────────────────────┤
 3 │ HOOD            lat/lng → hoodmaps polygon → name + category         │  free
   │  knockout ✕     category in hood.exclude_categories → reject         │  (cached
   │                 hoods.py    rich·hipsters·normies·tourists·suits·crime│  30 days)
   ├─────────────────────────────────────────────────────────────────────┤
 4 │ TRANSIT         real ÖV journey, door-to-door, arrive-by 08:00       │  free
   │  knockout ✕     > transit.max_minutes → reject                       │  (no key)
   │                 unresolvable address → manual-check, never guessed   │
   │                 transit_check.py · transport.opendata.ch             │
   ├─────────────────────────────────────────────────────────────────────┤
 5 │ VISION          kitchen + bath condition graded from photos          │  💰 tokens
   │                 modern | acceptable | dated                          │  survivors
   │                 fetch_photos.py · apply_assessment.py · claude -p    │  only
   ├─────────────────────────────────────────────────────────────────────┤
 6 │ SCORE 0–100     commute 10 · condition 45 · hood 20 · value 25       │  free
   │                 orders within a bucket; never changes bucket         │
   │                 scoring.py                                           │
   ├─────────────────────────────────────────────────────────────────────┤
 7 │ BUCKET          A = all nice-to-haves · B = a gap · manual-check     │  free
   │                 bucket.py                                            │
   ├─────────────────────────────────────────────────────────────────────┤
 8 │ DIGEST          data/digests/YYYY-MM-DD.md  +  desktop UI            │  free
   │                 digest.py · serve_ui.py · web/                       │
   └─────────────────────────────────────────────────────────────────────┘
                    │
                    ▼  you decide:  outreach <id>   |   deprioritize <id>
                    │
 9   OUTREACH        draft first-contact email (DE/EN, auto-detected)
                     outreach.py → you review → you send.  Never auto-sends.
                    │
10   REPLIES         match Gmail replies to listings → draft a response →
                     learn from how you edit it before sending
                     check_replies.py · draft_replies.py · draft_learn.py  💰
```

**The order is deliberate — cost increases down the stack.** Steps 1–4 are free
API calls or local computation, so every cheap knockout runs *before* the vision
step, which costs LLM tokens per listing. Vision only ever sees listings that
already cleared rent, size, rooms, move-in, lease, hood and commute. On a typical
Zürich day that's a handful of listings out of a few hundred scraped.

**Two hard rules hold at every step:** nothing is guessed (a missing or
unparseable value becomes an explicit `unknown` and goes to manual-check), and
nothing is silently dropped (every rejection is logged with its reason).

Design decisions worth knowing about:

- **Undecided listings keep reappearing** in every digest until you explicitly
  `outreach` or `deprioritize` them, so nothing falls through the cracks.
- **Cross-site dedup** matches on address + postcode + rounded rooms + m²
  bucket (never rent — sites disagree on net vs gross), and keeps the copy with
  the best contact channel.
- **Mass-repost detection**: near-identical descriptions get clustered and
  demoted rather than deleted, so template spam sinks without disappearing.

---

## Ranking, neighbourhoods and freshness

Three subsystems that aren't obvious from the pipeline diagram:

### Neighbourhood classification (hoodmaps)

`scripts/applib/hoods.py` looks up each listing's lat/lng against
[hoodmaps.com](https://hoodmaps.com/zurich-neighborhood-map)'s public Zürich
GeoJSON and tags it with a neighbourhood **name** and **category** — `rich`,
`hipsters`, `normies`, `tourists`, `suits`, `crime`. That feeds two things:

- **A hard knockout.** Any category in `hood.exclude_categories`
  (`criteria.yaml`, default `[crime]`) is rejected outright. Listings *outside*
  the mapped polygons have no category and are never rejected on this rule.
- **20% of the ranking score**, via `scoring.hood_preferences` — a 0–1
  preference per category. Tune it; the shipped values are one person's taste.

**The GeoJSON is not committed.** It's third-party data and it's a cache, so
`hoods.py` downloads it once on first run into `data/cache/` and refreshes it
every 30 days. If the download fails it degrades quietly — lookups return
`(None, None)`, nothing is rejected, and the hood component scores as unknown.
So a first run without network gives you a working pipeline with no hood data,
not a crash.

> Treat these categories as crowd-sourced *vibes*, not statistics. `crime` is a
> knockout by default, which is a strong call on data that is essentially an
> internet poll — consider emptying `exclude_categories` and letting the
> scoring weight handle it instead.

### Score (0–100)

Orders listings *within* each digest section; it never moves a listing between
buckets. Weighted sum of four 0–1 components — commute, condition, hood, value
— with weights in `scoring.weights` (must sum to 100). Two deliberate choices:

- **Commute is weighted low (10).** It's already a hard gate, so every listing
  you see is acceptable on transit; weighting it again would just re-sort on
  something you've already filtered.
- **Unknown data scores `0.4`**, below the 0.5 midpoint, so a verified-good flat
  outranks an unverified one rather than tying with it.

Full rationale in `docs/2026-06-11-scoring-design.md`.

### Staleness and expiry

Zürich listings die within days, so `staleness` in `criteria.yaml` controls two
behaviours: past `stale_after_days` (2) the digest annotates a listing with
"last checked N d ago — may be gone"; past `expire_after_days` (14) an
undecided listing that stays unverifiable is auto-closed. Set `expire_after_days: 0`
to disable auto-closing.

### Draft-style learning

If you use the Gmail reply loop, `draft_learn.py` diffs what you *actually sent*
against what was drafted for you and accumulates up to three short, reusable
lessons per thread ("keep it to 3 short sentences") into
`data/.draft_style.json`. Later drafts apply them. It's gitignored, local, and
only runs when you've actually edited and sent a draft — a fresh clone starts
with no lessons and behaves neutrally.

---

## Setup

```bash
git clone <this repo>
cd ZRH-APTS
bin/setup.sh
```

`bin/setup.sh` creates `.venv/`, installs the three dependencies (`requests`,
`pyyaml`, `beautifulsoup4`), and copies `config/applicant.example.yaml` to
`config/applicant.yaml`.

### Then edit two files — nothing works properly until you do

**1. `config/applicant.yaml`** — who you are. Name, phone, email, age, job
title, start date. This file is **gitignored**; it is the single home for
personal data, and nothing in the repo hard-codes any of it. Until you fill it
in, drafted emails are signed "Vorname Nachname".

**2. `config/criteria.yaml`** — what you want. Every line you must set is marked
`«SET THIS»`:

| Setting | Shipped default | Note |
|---|---|---|
| `rent.min` / `rent.max` | 1000 / 3000 CHF | Deliberately wide. The `max` is the single most consequential number in the file. |
| `size.min_sqm` | 30 m² | |
| `rooms.min` | 1.5 | Swiss counting includes the living room — "2.5 Zimmer" is a 1-bedroom. |
| `move_in.earliest` / `.latest` | `null` / 2027-06-30 | Strict on **both** sides: a flat free *earlier* than `earliest` is rejected too (you'd pay double rent). Set `earliest: null` to disable that. |
| `transit.max_minutes` | 40 | Door-to-door, arriving by 08:00. 40 keeps most of the canton; 25–30 confines you to the city. |
| `transit.office` | Manessestrasse 2, 8003 Zürich | A central Zürich (Wiedikon) default. **If you work elsewhere, change `office_lat`/`office_lon` too** or the scout's radius pre-filter silently excludes the right areas. |
| `scoring.weights` | commute 10, condition 45, hood 20, value 25 | Must sum to 100. Commute is low on purpose — it's already a hard gate. |

Widening a filter is cheap: `gate.py` re-gates every stored listing on each run,
so previously-rejected flats come straight back with no new API calls.

### Verify it works

```bash
.venv/bin/python scripts/transit_check.py "Birmensdorferstrasse 100, 8003 Zürich"
```

You should get a PASS/FAIL with minutes and a route.

```bash
for t in scripts/tests/test_*.py; do .venv/bin/python "$t" || echo "FAIL $t"; done
```

15 test files, all passing at time of export.

Check the neighbourhood lookup — this also triggers the one-time hoodmaps
download into `data/cache/` (~536 KB):

```bash
.venv/bin/python -c "import sys; sys.path.insert(0,'scripts'); \
from applib import hoods; print(hoods.lookup(47.369, 8.528))"
# -> ('Werd', 'hipsters')
```

---

## Running it

```bash
bin/run_morning.sh          # the whole pipeline; writes data/digests/<today>.md
.venv/bin/python scripts/serve_ui.py    # or the browser UI at localhost
```

Individual steps are documented in `CLAUDE.md` → *Morning run — exact command
sequence*, and each has a playbook in `workflows/`.

**Schedule it for 07:00 daily:**

```bash
bin/install_schedule.sh
```

This renders `launchd/com.zrhapts.morning.plist.template` with your actual
checkout path and loads the agent (launchd needs absolute paths, so the plist
can't be committed ready-made). Your Mac must be **awake** at 07:00 — launchd
does not wake it and does not catch up by default.

```bash
launchctl list | grep zrhapts                            # loaded?
launchctl kickstart -k gui/$(id -u)/com.zrhapts.morning  # run it now
launchctl bootout  gui/$(id -u)/com.zrhapts.morning      # unschedule
```

---

## The Claude-dependent parts

Most of this is plain deterministic Python and runs standalone. Three steps
shell out to `claude -p` (Claude Code, headless) and will no-op without it:

| Step | What it does | Cost |
|---|---|---|
| **Vision scoring** | Grades kitchen/bath condition from listing photos | Per-listing tokens — the expensive one |
| **Reply matching** | Finds landlord replies in Gmail, matches them to listings | Only when there are replies |
| **Reply drafting + style learning** | Drafts responses; learns from how you edit them | Only when there are replies |

These need `claude` logged in (`claude` once interactively) and, for the Gmail
steps, the Gmail connector authorised for the address in `applicant.yaml`.

**Two cost warnings from the original run:**
- The vision and reply steps spawn `claude -p` workers that bill against your
  plan. A large backlog can be expensive.
- A big first backlog will trip the transit API's rate limiter and produce a
  wave of bogus `transit_unknown` manual-checks. Let it cool down and re-run
  `gate.py` — it re-gates from stored state without new scraping.

Without Claude: scout, filters, transit gate, bucketing and digests all work.
You lose condition grading and the Gmail loop.

---

## Layout

```
CLAUDE.md              master playbook — hard rules, pipeline, outreach flow
config/
  criteria.yaml        WHAT you want. Committed. «SET THIS» markers.
  applicant.example.yaml   template for ↓
  applicant.yaml       WHO you are. GITIGNORED. Created by setup.sh.
  sites.yaml           which portals are scraped
workflows/             per-step playbooks 1..5
scripts/               scout · gate · transit_check · fetch_photos ·
                       apply_assessment · bucket · digest · outreach ·
                       check_replies · serve_ui  (+ applib/ shared helpers)
  tests/               15 unittest files, no framework needed
web/                   the desktop UI served by serve_ui.py
style/                 good/ bad/ reference photos + rubric for vision scoring
templates/             first_contact.md · reply_guidelines.md
dossier/               your application PDFs — gitignored, see its README
data/                  listings.json · digests/ · logs/ · photos/ — all gitignored
bin/                   setup.sh · run_morning.sh · install_schedule.sh
docs/                  design docs + specs from the original build
```

---

## Privacy — read before you commit anything

This tool handles two kinds of personal data, and `.gitignore` is set up to keep
both out of git:

1. **Yours** — `config/applicant.yaml`, `dossier/` (passport, Betreibungsauszug,
   payslips), `forms/`.
2. **Other people's** — `data/listings.json` accumulates scraped listings
   including **letting agents' names, work emails and phone numbers**.
   Republishing that is a Swiss FADP / GDPR problem independent of who can see
   your repo.

The whole of `data/`, `dossier/` and `forms/` is ignored wholesale. If you need
to share state with someone, strip contact fields first. Prefer widening an
ignore rule over committing one file out of those trees.

The original author's data — and the entire git history containing it — was
removed before publication; this repo starts from a single clean commit.

---

## Known rough edges

- **Comparis is usually blocked** by Cloudflare. Flatfox is the reliable source.
  The pipeline never fabricates listings to compensate.
- **Flatfox detail pages started returning 403** in late July 2026, so
  `verify_listings.py` can only verify newhome. The Flatfox JSON API still works,
  which is what `scout.py` uses — scouting is unaffected, re-verification isn't.
- **Homegate / ImmoScout24 / Newhome** are stubbed in `sites.yaml`
  (`enabled: false`). The scaffolding is there; the parsers are not finished.
- **macOS-specific**: launchd scheduling, `open`, and the notification hook.
  The Python itself is portable; `bin/` is not.
- **`.venv` is Python 3.9** as shipped by macOS. Newer works fine.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `venv missing` | `bin/setup.sh` |
| Drafts signed "Vorname Nachname" | Fill in `config/applicant.yaml` |
| Everything rejected on move-in date | `move_in.earliest`/`.latest` in `criteria.yaml` — it's strict on both sides |
| Huge manual-check pile | Usually transit API rate limiting. Cool down, re-run `scripts/gate.py` |
| Vision step failed in the log | Digest still writes with `condition_unknown`. Check `claude` is logged in |
| Permission prompt during headless run | Add the command to `.claude/settings.local.json` → `permissions.allow` |
| Nothing at 07:00 | Mac was asleep, or agent not loaded — `launchctl list \| grep zrhapts` |
