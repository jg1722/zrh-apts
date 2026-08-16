"""Accumulated draft-style lessons learned from the user's edits to reply drafts.

Lives in data/.draft_style.json; mirrors learning.py's load/save/pause/reset.
Component B (draft_replies) injects these notes into every drafter prompt.
"""
from __future__ import annotations

import datetime as _dt
import json

from . import paths


def _load() -> dict:
    if paths.DRAFT_STYLE_FILE.exists():
        return json.loads(paths.DRAFT_STYLE_FILE.read_text(encoding="utf-8"))
    return {"notes": [], "paused": False}


def _save(data: dict) -> None:
    paths.DRAFT_STYLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.DRAFT_STYLE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(paths.DRAFT_STYLE_FILE)


def note_texts() -> list[str]:
    return [n.get("text", "") for n in _load().get("notes", []) if n.get("text")]


def add_notes(lessons: list[str], source: str | None = None) -> int:
    data = _load()
    existing = {n.get("text") for n in data.get("notes", [])}
    now = _dt.datetime.now().replace(microsecond=0).isoformat()
    added = 0
    for raw in lessons or []:
        t = (raw or "").strip()
        if t and t not in existing:
            data.setdefault("notes", []).append({"text": t, "added_at": now, "from": source})
            existing.add(t)
            added += 1
    if added:
        _save(data)
    return added


def is_paused() -> bool:
    return bool(_load().get("paused"))


def set_paused(flag: bool) -> None:
    data = _load()
    data["paused"] = bool(flag)
    _save(data)


def reset() -> None:
    paths.DRAFT_STYLE_FILE.unlink(missing_ok=True)


def status() -> dict:
    d = _load()
    return {"notes": d.get("notes", []), "paused": bool(d.get("paused")),
            "count": len(d.get("notes", []))}
