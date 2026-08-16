#!/usr/bin/env python3
"""Record the user's decision on a listing — this is what moves it off the main
digest buckets (A/B/manual), NOT the fact that it was seen before.

A listing keeps appearing in its bucket every morning until you decide:
  * outreach      — you will contact them. Moves to the digest's "Outreach" list.
                    (Sending a first contact, i.e. status >= contacted, implies this.)
  * deprioritize  — you decided NOT to contact. Moves to the "Deprioritized" list.
  * reset         — back to undecided; it returns to its bucket.

    python scripts/decide.py newhome-6098300 outreach
    python scripts/decide.py flatfox-86072983 deprioritize --note "dated kitchen"
    python scripts/decide.py flatfox-86072983 reset
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import paths  # noqa: E402
from applib.store import Store  # noqa: E402

ACTIONS = {"outreach": "outreach", "deprioritize": "deprioritized", "reset": None}


def main() -> int:
    ap = argparse.ArgumentParser(description="Record a decision on a listing.")
    ap.add_argument("listing_id")
    ap.add_argument("action", choices=list(ACTIONS))
    ap.add_argument("--note", default=None)
    args = ap.parse_args()

    store = Store.load()
    lst = store.listings.get(args.listing_id)
    if not lst:
        print(f"decide: unknown listing {args.listing_id}", file=sys.stderr)
        return 1
    prev = lst.get("decision")
    lst["decision"] = ACTIONS[args.action]
    lst["decision_at"] = paths.now_iso()
    lst["decision_note"] = args.note
    store.save()
    print(f"decide: {args.listing_id} decision {prev} -> {lst['decision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
