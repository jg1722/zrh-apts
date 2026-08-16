#!/usr/bin/env python3
"""Prep step for the daily Gmail reply matcher.

Emits a small JSON list (data/.outreach_context.json) of the listings we've
reached out to, with just the signals the matcher needs. Keeps the LLM step
cheap and deterministic-bounded. Never touches Gmail.

    python scripts/reply_context.py        # writes the context file, prints count
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import outreach  # noqa: E402  (scripts/outreach.py)
from applib import paths  # noqa: E402
from applib.store import Store  # noqa: E402


def _is_contacted(lst: dict) -> bool:
    return lst.get("status") == "contacted" or lst.get("decision") == "outreach"


def build_context(store: Store) -> list[dict]:
    """One dict per contacted listing with the matcher's input signals."""
    out: list[dict] = []
    for lid, lst in store.listings.items():
        if not _is_contacted(lst):
            continue
        try:
            subject = outreach.render(lst).get("subject")
        except Exception:
            subject = None
        out.append({
            "id": lid,
            "url": lst.get("url"),
            "channel": lst.get("outreach_channel"),
            "email": lst.get("outreach_email"),
            "subject": subject,
            "decision_at": lst.get("decision_at"),
            "street": lst.get("street"),
            "zipcode": lst.get("zipcode"),
            "city": lst.get("city"),
        })
    return out


def main() -> int:
    ctx = build_context(Store.load())
    paths.OUTREACH_CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.OUTREACH_CONTEXT_FILE.write_text(
        json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"reply_context: {len(ctx)} contacted listing(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
