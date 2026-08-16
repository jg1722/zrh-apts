#!/usr/bin/env python3
"""Write vision style verdicts back into the store.

The model (after reading the downloaded photos) calls this with a JSON map of
listing-id -> verdict. Valid conditions: modern | acceptable | dated |
condition_unknown. Anything else is rejected so we never store a guess.

Usage:
    echo '{"flatfox-123": {"kitchen":"modern","bath":"dated","reason":"old tiles in bath"}}' \\
        | python scripts/apply_assessment.py
    python scripts/apply_assessment.py --file verdicts.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib.store import Store  # noqa: E402

VALID = {"modern", "acceptable", "dated", "condition_unknown"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply vision style verdicts.")
    ap.add_argument("--file", default=None, help="JSON file; otherwise read stdin")
    args = ap.parse_args()

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    try:
        verdicts = json.loads(raw)
    except ValueError as exc:
        print(f"apply_assessment: invalid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(verdicts, dict):
        print("apply_assessment: expected a JSON object {id: {...}}", file=sys.stderr)
        return 1

    store = Store.load()
    applied, skipped = 0, []
    for lid, v in verdicts.items():
        lst = store.listings.get(lid)
        if not lst:
            skipped.append(f"{lid}: not in store")
            continue
        k = v.get("kitchen", "condition_unknown")
        b = v.get("bath", "condition_unknown")
        if k not in VALID or b not in VALID:
            skipped.append(f"{lid}: bad condition value(s) {k!r}/{b!r}")
            continue
        lst["condition_kitchen"] = k
        lst["condition_bath"] = b
        lst["condition_reason"] = v.get("reason") or None
        applied += 1

    store.save()
    print(f"apply_assessment: {applied} applied, {len(skipped)} skipped")
    for s in skipped:
        print("  -", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
