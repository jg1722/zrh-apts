# Style rubric — kitchen & bathroom condition

The user wants to avoid old, run-down kitchens, bathrooms and toilets. Modern or
refurbished is strongly preferred. Score **each room independently** from the
listing photos, using this rubric plus any reference images in `good/` and
`bad/` (start empty — the user adds their own examples over time; when present,
weight them heavily as they encode the user's actual taste).

## The three grades (+ unknown)

### `modern`
- Recently renovated / contemporary fittings.
- Kitchen: flat-front or handleless cabinets, stone/composite worktops, modern
  oven/induction hob, integrated appliances, clean consistent finishes.
- Bath: walk-in or frameless glass shower, large-format tiles, wall-hung WC,
  modern vanity, good lighting.

### `acceptable`
- Clean and functional, not dated enough to matter. Neutral, well-kept.
- Standard but tidy cabinetry/tiling, no obvious wear, nothing off-putting.
- When a room is borderline between acceptable and dated, choose **acceptable**
  (the user makes the final call from the digest).

### `dated`
- Old fittings, worn surfaces, run-down. Demotes to Bucket B + flag the reason.
- Kitchen: wood-laminate or oak cabinets from the 80s/90s, small old hob, busy
  patterned tiles, visibly worn worktops, fluorescent tube lighting.
- Bath: coloured suites (beige/green/pink), small dated wall tiles, old tub with
  curtain, freestanding shabby furniture, cracked grout, exposed old plumbing.

### `condition_unknown`
- No usable photo of that specific room. **Do not penalise. Do not infer** from
  the other room or the text.

## How to apply
1. Look only at photos that clearly show the kitchen / the bathroom.
2. Grade each room. Add a one-line `reason` whenever you grade `dated`.
3. Be conservative — photos can be old, wide-angle, or flattering. Reserve
   `dated` for clearly old/worn rooms.
4. Write verdicts with `scripts/apply_assessment.py` (see `workflows/3-assess-style.md`).

## Adding reference images
- Put clearly-good kitchens/baths in `good/`, clearly-bad ones in `bad/`.
- Any image format is fine. The more examples, the better the calibration to the
  user's taste.
