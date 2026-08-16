#!/usr/bin/env python3
"""Download photos for transit survivors so the vision step can read them.

Vision style-scoring needs LOCAL image files (Claude's Read tool views images
from disk, not URLs). This runs after the transit gate, only for `passed`
listings that still need a kitchen/bath verdict — keeping the photo pulls (and
later the vision tokens) small.

Writes images to data/photos/<id>/NN.jpg and a manifest the workflow points the
model at. Run:  python scripts/fetch_photos.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from applib import http, paths  # noqa: E402
from applib.store import Store  # noqa: E402

MAX_PHOTOS = 8


def _needs_vision(lst: dict) -> bool:
    if lst.get("gate_status") != "passed":
        return False
    if not lst.get("photos"):
        return False
    # Re-score only when we don't yet have a verdict.
    return lst.get("condition_kitchen") in (None, "condition_unknown") \
        or lst.get("condition_bath") in (None, "condition_unknown")


def _download(lst: dict) -> list[str]:
    dest = paths.PHOTOS_DIR / lst["id"]
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for i, url in enumerate(lst["photos"][:MAX_PHOTOS], start=1):
        out = dest / f"{i:02d}.jpg"
        if out.exists() and out.stat().st_size > 0:
            saved.append(str(out))
            continue
        try:
            resp = http.get(url, accept="image/*")
            if resp.status_code == 200 and resp.content:
                out.write_bytes(resp.content)
                saved.append(str(out))
        except Exception:
            continue  # skip a bad image, never fail the run
    return saved


def main() -> int:
    paths.ensure_dirs()
    store = Store.load()
    manifest: dict[str, dict] = {}
    for lst in store.active():
        if not _needs_vision(lst):
            continue
        files = _download(lst)
        if files:
            manifest[lst["id"]] = {
                "dir": str(paths.PHOTOS_DIR / lst["id"]),
                "files": files,
                "title": lst.get("title"),
                "address": lst.get("address"),
                "photos_total": len(lst.get("photos") or []),
            }

    manifest_path = paths.PHOTOS_DIR / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"fetch_photos: {len(manifest)} listing(s) need vision scoring")
    for lid, m in manifest.items():
        print(f"  - {lid}: {len(m['files'])} photo(s) in {m['dir']}")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
