# Workflow 3 — Style assessment (vision)

**Goal:** score each survivor's kitchen and bathroom condition from photos. This
is the only step you (the model) do by hand each morning. It runs **only on
transit survivors** (`gate_status = passed`) to keep token use low.

## Steps
1. Download survivor photos:
   ```
   .venv/bin/python scripts/fetch_photos.py
   ```
   This writes images to `data/photos/<id>/NN.jpg` and a manifest at
   `data/photos/_manifest.json` listing exactly which files to look at.
2. **Read** the photos for each listing in the manifest (use the Read tool on the
   local files — Read views images from disk).
3. Score each room against the rubric in `style/README.md` and any reference
   images the user has added to `style/good/` and `style/bad/`.
4. Apply the verdicts:
   ```
   echo '{
     "flatfox-123456": {"kitchen": "modern", "bath": "acceptable", "reason": ""},
     "flatfox-234567": {"kitchen": "dated",  "bath": "modern", "reason": "old wall tiles + worn cabinets in kitchen"}
   }' | .venv/bin/python scripts/apply_assessment.py
   ```

## Scoring scale
- `modern` — recently renovated / contemporary fittings.
- `acceptable` — clean, functional, not dated enough to matter.
- `dated` — old fittings, worn surfaces, run-down → demotes to Bucket B and flags
  the reason (does NOT reject unless `condition.reject_on_dated: true`).
- `condition_unknown` — no usable photo for that room. **Do not penalise**; do
  not guess from the other room or the listing text.

## Rules
- Only score what a photo actually shows. No photo of the bathroom → bath is
  `condition_unknown`, full stop.
- Always include a short `reason` when you mark something `dated`.
- Be conservative: when a kitchen/bath is borderline, prefer `acceptable` over
  `dated` — the user makes the final call from the digest.
