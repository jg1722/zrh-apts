# Design — Multi-source scout, cross-site dedup & outreach-channel priority

**Date:** 2026-05-31
**Status:** Approved (design); pending implementation plan
**Author:** Outreach agent (with the original author)

## 1. Goal

Extend the Zurich flat pipeline beyond Flatfox by adding **newhome** as a second
live source, then **deduplicate apartments that appear on more than one site** and,
for each duplicate set, surface the copy with the **best outreach channel**.

Non-goal: adding Homegate, ImmoScout24, or Comparis (see §3). Non-goal: changing
any existing Flatfox behaviour or any gate (see §8, the regression contract).

## 2. Site feasibility assessment (probed 2026-05-31)

All four previously-stubbed sites block plain HTTP. Evidence from live probes:

| Site | Protection observed | Verdict |
|---|---|---|
| Homegate | Cloudflare **+ DataDome** (`api.homegate.ch` returns a `captcha-delivery.com` redirect) | Out of scope — requires anti-bot bypass |
| ImmoScout24 | Cloudflare **+ DataDome** (same SMG backend as Homegate) | Out of scope |
| Comparis | **DataDome** + Cloudflare; already stubbed as "blocked". Aggregator — mostly re-posts of the others | Out of scope, low marginal value |
| **newhome** | Cloudflare **active JS challenge** ("Just a moment…"), no DataDome | **In scope** — clearable with a rendering client |

Rationale for excluding the DataDome trio: every access path (stealth browser,
paid unblocker) exists specifically to defeat anti-bot the operator deliberately
deployed, which conflicts with **Hard Rule #4** ("Respect each site's terms…").
They remain `enabled: false` in `config/sites.yaml` with a one-line note.

## 3. Decisions (locked with user)

1. **Sources:** add **newhome only**, via a rendering fetcher. Keep the DataDome
   trio disabled.
2. **newhome fetch:** try a lightweight **TLS-impersonation client (`curl_cffi`,
   impersonate=chrome)** first; switch to a full stealth headless browser
   (Playwright) only if it cannot clear the JS challenge.
   **OUTCOME (implemented):** `curl_cffi` clears the challenge for GETs and the
   newhome ServiceStack API returns clean JSON — **no headless browser is needed
   at runtime.** Contract reverse-engineered:
   `GET service.newhome.ch/api/api/SearchListingRequest` (newest-first `order=1`,
   `radius` + `radiusCenterCoordinate=lat;lon` server-side scope, `rowCount`≤20)
   and `…/ListingDetailRequest?immocode=…` for enrichment. The fetch lives behind
   one interface (`applib/browser.py`) so the mechanism can still be swapped.
3. **Outreach tiers (highest wins on a duplicate):**
   `email` > `onsite_now` > `onsite_windowed` > `channel_unknown`.
4. **Dedup key:** fuzzy — normalized street+number + PLZ + rounded rooms +
   m²-bucket. Rent is **not** in the key (tolerates gross/net mismatch).
5. **Channel detection:** parse the listing **detail page** heuristically; if
   nothing parseable → `channel_unknown` → manual-check. **Never guess.**
6. **Duplicate handling:** hide the lower-tier copy (`dupe_of`), note the
   cross-post on the surviving (winner) listing in the digest.

## 4. Architecture

New/changed units, each with one clear purpose:

### 4.1 `scripts/applib/browser.py` (new)
Single interface: `fetch_rendered(url, *, want="json"|"html") -> str/dict`.
- Honours the same politeness config (`request_delay_seconds`, `timeout_seconds`)
  as `applib/http.py`. One request at a time, no parallelism.
- Implementation v1: `curl_cffi` with `impersonate="chrome"`.
- Implementation v2 (fallback): Playwright headless Chromium + stealth, used iff
  v1 cannot clear the challenge. Same signature, so callers don't change.
- On unresolved challenge / error: raises; callers log "blocked" and continue,
  **never fabricate** (mirrors existing `scout_comparis` behaviour).

### 4.2 `scout_newhome` in `scripts/scout.py` (new)
- Loads the newhome search results via `browser.fetch_rendered`. newhome's results
  page drives a JSON search endpoint; we intercept/replay that JSON rather than
  DOM-scrape (cleaner, structured).
- `_newhome_normalise(r)` → listing schema: `id="newhome-<pk>"`, `source="newhome"`,
  rent_net/gross, size_sqm, rooms, street/zip/city/address, lat/long, amenities,
  photos, availability, blurb, `hood_name`/`hood_category` via `hoods.lookup`.
- Reuses `_in_scope(...)` (radius-first, zip/city fallback) and the lookback cutoff
  unchanged. Registered in the existing `SCOUTERS` dict.

### 4.3 Outreach-channel detector (new) — folded into `verify_listings.py`
The morning `verify_listings.py` already fetches each active listing's detail page.
We piggy-back channel detection on that same fetch (Flatfox via `http`, newhome via
`browser`) to avoid double-fetching.
- Heuristics on the detail page:
  - exposed `mailto:` / contact email → `email` (+ capture `outreach_email`)
  - application/contact ("send message") form available now → `onsite_now`
  - wait-window text (e.g. "Bewerbungen ab…", "applications open on…",
    Warteliste/queue, a future open-date) → `onsite_windowed` (+ capture
    `outreach_window`)
  - nothing parseable → `channel_unknown`
- New schema fields (in `store.PIPELINE_DEFAULTS`, so they persist & default safely):
  `outreach_channel` (default `channel_unknown`), `outreach_email` (None),
  `outreach_window` (None), `outreach_detected_at` (None).
- `channel_unknown` adds an entry to `manual_check` so it surfaces, never guessed.

### 4.4 Cross-site dedup + priority in `scripts/applib/store.py` (replace existing)
The current `_crosspost_key` (normalized address + rounded rent) is replaced by a
fuzzy key and a winner-selection step:
- `_crosspost_key(lst)` → `(norm_street_number, plz, round(rooms))`.
  Returns None if a component is missing (then no dedup — safe default).
- **Size is matched by TOLERANCE, not a rounding bucket.** Rounding splits the
  same flat across a boundary (77 m² → 75, 78 m² → 80). Instead `_size_clusters`
  groups members within ±`dedup.m2_bucket` m² of each other inside each key group,
  so 77≈78 merges but two differently-sized flats in the same building do not.
- `_recompute_crossposts()`: groups all listings by key; within each group picks a
  **winner** = max outreach tier, tie-break (a) most non-null scraped fields,
  (b) earliest `first_seen`, (c) id sort for determinism. Sets `dupe_of=winner_id`
  on losers, clears `dupe_of` on the winner, and writes `crosspost_sources`
  (list of `{source, url}`) on the winner. **Recomputed every run** (called at the
  end of scout's upsert pass), so a newly-opened window can flip the winner.
- `store.active()` is unchanged — it already filters out `dupe_of`, so every
  downstream gate automatically operates on winners only.

### 4.5 `scripts/digest.py`
Per surfaced listing, render: outreach channel + window (if any), and a
"also on: flatfox/newhome" cross-post note from `crosspost_sources`.

### 4.6 Config
- `config/sites.yaml`: `newhome.enabled: true`, `kind: browser`; DataDome trio stay
  `enabled: false` with note "requires anti-bot bypass — out of scope (Hard Rule 4)".
- `config/criteria.yaml`: add `dedup` block (m²-bucket size, rooms rounding) and
  `outreach.tier_order` (the four-tier ranking) so logic is config-driven, not
  hard-coded.

## 5. Data flow (morning run, after change)

```
scout.py            flatfox + newhome → upsert → _recompute_crossposts()
verify_listings.py  per active winner: page fetch → verify rooms/size/rent
                                       + detect outreach_channel/email/window
gate.py             must-haves + transit knockout   (unchanged; runs on winners)
fetch_photos.py     survivor photos                 (unchanged)
[vision step]       kitchen/bath scoring            (unchanged)
apply_assessment.py verdicts                          (unchanged)
bucket.py           A/B/manual/rejected             (unchanged)
digest.py           + outreach channel + crosspost note
```

## 6. Config additions (shape)

```yaml
# criteria.yaml
dedup:
  m2_bucket: 5          # m² rounded to nearest 5 for the match key
  rooms_round: 0.5      # rooms rounded to nearest 0.5
outreach:
  tier_order: [email, onsite_now, onsite_windowed, channel_unknown]
```

## 7. Error handling

- newhome blocked / challenge unsolved → log "newhome: blocked", return [], continue.
  Flatfox and the rest of the run are unaffected.
- Channel undetectable → `channel_unknown` + manual_check entry. Never guessed.
- Dedup key incomplete (missing address/rooms/size) → listing is simply not deduped
  (treated as unique). No false merges.
- `browser.py` v1→v2 switch is internal; a failure in v1 degrades to "blocked", not
  to a crash.

## 8. Regression contract (existing Flatfox behaviour MUST be preserved)

This update is **additive**. Explicit invariants to verify before completion:

1. **Transit knockout** (`gate.py`, `transit_check.py`) runs unchanged and still
   gates every listing (now winners), arriving-by-08:00 within `max_minutes`.
2. **Hoodmaps category knockout** (`hood.exclude_categories`) unchanged.
3. **Must-haves** (rent/size/rooms) and **bucketing** (A/B/manual/rejected)
   unchanged.
4. **Style/condition scoring** (vision step + `apply_assessment.py`) unchanged.
5. **API↔page verification** (`verify_listings.py`) still overrides stale Flatfox
   values; channel detection is added alongside, not in place of, verification.
6. **Dedup never re-shows a seen listing** unless price/status changed (Hard Rule 3);
   `store.active()` semantics preserved.
7. **No auto-send** (Hard Rule 2) — outreach remains draft-only & user-initiated.
   Channel detection only *records* how to reach out; it does not reach out.
8. Existing Flatfox listings already in `data/listings.json` get the new fields via
   `PIPELINE_DEFAULTS` on next run — no migration needed, no data loss.
9. Morning command sequence in `CLAUDE.md` is unchanged (no new step inserted);
   new work rides inside existing `scout.py` / `verify_listings.py`.

## 9. Out of scope

- Homegate / ImmoScout24 / Comparis scraping (anti-bot bypass).
- Any change to the transit, hoods, style, or bucketing logic.

## 10. Open risks

- newhome relies on `curl_cffi` continuing to clear Cloudflare's challenge. If
  Cloudflare tightens and curl_cffi starts getting 403s, the scraper logs
  "blocked" and skips (no fabrication); the Playwright fallback can be wired into
  `browser.py` without touching scout/verify. Playwright + Chromium were installed
  during development but are NOT used at runtime.
- newhome `SearchListingRequest` reverse-engineered, not an official API: field
  names (`rowCount`≤20, `radiusCenterCoordinate=lat;lon`, `order=1`=newest) could
  change. Failures degrade to "blocked", never to bad data.
- newhome search entries lack a publish date, so scout pages newest-first and caps
  at `max_pages` (300 newest) rather than applying `lookback_days`; the store dedup
  prevents re-surfacing. First run after enabling newhome shows a one-time backlog.
