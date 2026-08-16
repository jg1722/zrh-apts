#!/usr/bin/env python3
"""Step 4a — Bucketing.

Runs after vision scoring. Only `passed` listings (must-haves + transit ok) are
bucketed:
  * Bucket A — all nice-to-haves present AND kitchen/bath not dated.
  * Bucket B — missing a nice-to-have OR a dated kitchen/bath. The specific gap
    is recorded in bucket_gap.
condition_unknown never demotes (we don't penalise missing photos).
If criteria.condition.reject_on_dated is true, a dated room rejects instead.

Run:  python scripts/bucket.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import config, paths  # noqa: E402
from applib.scoring import score_listing  # noqa: E402
from applib.store import Store  # noqa: E402


def bucket_one(lst: dict, reject_on_dated: bool) -> None:
    dated_rooms = [name for name, key in (("kitchen", "condition_kitchen"),
                                          ("bath", "condition_bath"))
                   if lst.get(key) == "dated"]

    if reject_on_dated and dated_rooms:
        lst["gate_status"] = "rejected"
        lst["bucket"] = None
        lst["reject_reason"] = f"dated {', '.join(dated_rooms)} (reject_on_dated)"
        return

    gaps: list[str] = []
    if not lst.get("has_parking"):
        gaps.append("no parking")
    if not lst.get("has_balcony"):
        gaps.append("no balcony")
    for r in dated_rooms:
        gaps.append(f"dated {r}")

    lst["bucket"] = "B" if gaps else "A"
    lst["bucket_gap"] = "; ".join(gaps) if gaps else None


def main() -> int:
    crit = config.effective_criteria()  # criteria.yaml + learned-preferences overlay
    reject_on_dated = bool(crit.get("condition", {}).get("reject_on_dated", False))
    store = Store.load()

    for lst in store.active():
        if lst.get("gate_status") == "passed":
            bucket_one(lst, reject_on_dated)
        if lst.get("gate_status") in ("passed", "manual"):
            lst["score"], lst["score_parts"] = score_listing(lst, crit)

    store.save()
    a = sum(1 for l in store.active() if l.get("bucket") == "A")
    b = sum(1 for l in store.active() if l.get("bucket") == "B")
    print(f"bucket: {a} in Bucket A, {b} in Bucket B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
