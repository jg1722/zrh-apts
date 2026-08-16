#!/usr/bin/env python3
"""Print one listing's full record (for the outreach workflow).

    python scripts/show.py flatfox-123456
    python scripts/show.py flatfox-123456 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib.store import Store  # noqa: E402

KEY_ORDER = [
    "id", "source", "status", "bucket", "bucket_gap", "gate_status",
    "title", "url", "address", "rent_net", "rent_gross", "size_sqm", "rooms",
    "has_parking", "has_balcony", "condition_kitchen", "condition_bath",
    "condition_reason", "transit_min", "transit_route", "transit_status",
    "availability", "first_seen", "last_seen", "manual_check",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("listing_id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    store = Store.load()
    lst = store.listings.get(args.listing_id)
    if not lst:
        print(f"show: unknown listing {args.listing_id}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(lst, ensure_ascii=False, indent=2))
        return 0

    for k in KEY_ORDER:
        if k in lst:
            print(f"{k:18} {lst[k]}")
    photos = lst.get("photos") or []
    print(f"{'photos':18} {len(photos)} url(s)")
    print(f"{'blurb':18} {(lst.get('blurb') or '')[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
