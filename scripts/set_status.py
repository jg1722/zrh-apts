#!/usr/bin/env python3
"""Update a listing's outreach status (the state machine).

Valid: new -> contacted -> replied -> viewing -> closed | rejected
Used by the outreach workflow, e.g. after a draft is approved and sent:
    python scripts/set_status.py flatfox-123456 contacted --note "first contact sent 2026-05-30"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import paths  # noqa: E402
from applib.store import Store  # noqa: E402

VALID = ["new", "contacted", "replied", "viewing", "closed", "rejected"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Set a listing's outreach status.")
    ap.add_argument("listing_id")
    ap.add_argument("status", choices=VALID)
    ap.add_argument("--note", default=None, help="optional note appended to the log")
    args = ap.parse_args()

    store = Store.load()
    lst = store.listings.get(args.listing_id)
    if not lst:
        print(f"set_status: unknown listing {args.listing_id}", file=sys.stderr)
        return 1
    prev = lst.get("status")
    lst["status"] = args.status
    log = lst.setdefault("status_log", [])
    log.append({"at": paths.now_iso(), "from": prev, "to": args.status, "note": args.note})
    store.save()
    print(f"set_status: {args.listing_id} {prev} -> {args.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
