"""Detect mass-duplicate listing clusters (spam / bulk template re-posts).

Scammers and bulk landlords post the same apartment many times with small
permutations — varying rooms and price, PLZ-only addresses, auto-generated
"PLZ City - CHF N incl. utilities per month" titles — while the description text
stays the same (e.g. the 8810 Horgen "Helle Wohnung in naturnaher Lage…" batch).

We group active listings by a normalised description signature: lowercased,
letters only (digits and punctuation stripped so the permuted room counts and
prices collapse), truncated to a prefix. Any group of >= ``min_size`` listings
is a cluster; every member gets ``cluster_size`` set so the UI can flag it and
the serve layer can demote it (kept visible, just pushed to the bottom).
"""
from __future__ import annotations

import re

DEFAULT_MIN_SIZE = 4
_MIN_SIG_LEN = 60          # blurbs shorter than this can't form a reliable signature
_SIG_PREFIX = 160          # letters of the normalised blurb that define the signature

_NON_LETTER = re.compile(r"[^a-z]+")


def _signature(lst: dict) -> str | None:
    norm = _NON_LETTER.sub("", (lst.get("blurb") or "").lower())
    if len(norm) < _MIN_SIG_LEN:
        return None
    return norm[:_SIG_PREFIX]


def annotate(rows: list[dict], min_size: int = DEFAULT_MIN_SIZE) -> list[dict]:
    """Mutate ``rows`` in place: set ``cluster_size`` on members of any group of
    >= ``min_size`` listings that share a description signature. ``min_size`` <= 1
    disables detection. Returns ``rows`` for convenience."""
    if not min_size or min_size <= 1:
        return rows
    groups: dict[str, list[dict]] = {}
    for lst in rows:
        sig = _signature(lst)
        if sig:
            groups.setdefault(sig, []).append(lst)
    for members in groups.values():
        if len(members) >= min_size:
            n = len(members)
            for lst in members:
                lst["cluster_size"] = n
    return rows
