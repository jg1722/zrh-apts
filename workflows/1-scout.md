# Workflow 1 — Scout

**Goal:** pull new/changed listings from the enabled sites, normalise them into
the listing schema, dedup against the store. No filtering happens here.

## Run
```
.venv/bin/python scripts/scout.py
```

## What it does
- Reads `config/sites.yaml` (only `enabled: true` sites are scraped) and the
  `politeness` block (per-host delay, timeout, retries, shared User-Agent).
- **Flatfox** (`flatfox.ch`, primary): hits the public JSON API
  `/api/v1/public-listing/?...&expand=images`, pages newest-first, and filters
  client-side to the `scope` (zip prefixes / city) because the endpoint ignores
  geo params. Maps every field; collects all signed photo URLs.
- **Comparis** (`comparis.ch`, aggregator): Cloudflare-protected. The scraper
  attempts a polite fetch; on a challenge/non-200 it logs `blocked` and
  continues. **It never fabricates data.**
- Upserts into `data/listings.json`:
  - new listing → `first_seen = today`, `status = new`;
  - seen before → updates `last_seen`; if rent or availability changed, sets
    `changed = true` and records the change (so the digest re-surfaces it);
  - dedup key is `url`/`id`; cross-posts are caught by `address + rent` and
    tagged `dupe_of` so they aren't shown twice.

## Rules
- Missing field → `null` (shown as `unknown`). Never guess.
- If a site blocks or its layout changed, log it and move on — do not invent
  listings to fill the gap.
- Don't add sites here; add them to `config/sites.yaml` and write a parser.

## Output
A summary line (`+N new, M updated, K changed`) plus per-site notes. The store is
the source of truth for the next steps.

## Important: API ↔ page mismatches
Flatfox's API has been observed returning **inflated rooms / size / price** for
listings compared with what the actual detail page renders for the same `pk`.
The morning wrapper therefore runs `scripts/verify_listings.py` immediately
after `scout.py`. It fetches each active listing's detail page, parses the real
rooms / m² / CHF, and **overrides the stored API values** when they disagree,
setting `verified_at` and `verification_notes` on the listing. The gate, vision
step, bucket and digest all run on the verified values. Do not display
unverified API numbers to the user.
