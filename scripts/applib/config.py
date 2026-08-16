"""Load the YAML config files. criteria.yaml is the single source of truth for
filtering; sites.yaml controls which portals get scraped; applicant.yaml holds
the personal data (name, contact details) and is deliberately gitignored."""
from __future__ import annotations

import sys
from functools import lru_cache

import yaml

from . import paths


@lru_cache(maxsize=1)
def criteria() -> dict:
    with open(paths.CRITERIA_FILE, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def applicant() -> dict:
    """Who is applying — name, contact details, and the facts the outreach copy
    states about you.

    Reads config/applicant.yaml, which is gitignored so personal data never
    enters the repository. When it is missing we fall back to the committed
    config/applicant.example.yaml and warn, rather than crashing: a fresh clone
    should still run end-to-end and pass its tests. The placeholder name is
    obvious enough ("Vorname Nachname") that no one sends a draft signed with it
    by accident, and nothing in this pipeline sends mail without a human click.
    """
    path = paths.APPLICANT_FILE
    if not path.exists():
        path = paths.APPLICANT_EXAMPLE_FILE
        print(
            f"WARNING: {paths.APPLICANT_FILE.name} not found — using placeholder "
            f"identity from {path.name}.\n"
            f"         cp config/{paths.APPLICANT_EXAMPLE_FILE.name} "
            f"config/{paths.APPLICANT_FILE.name}  and fill it in.",
            file=sys.stderr,
        )
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def signature() -> str:
    """The email signature block — name / phone / email, one per line.

    Blank fields are skipped so an applicant without a phone number doesn't get
    an empty line in the middle of their signature.
    """
    ident = applicant().get("identity") or {}
    lines = (ident.get("name"), ident.get("phone"), ident.get("email"))
    return "\n".join(s for s in (str(x or "").strip() for x in lines) if s)


def applicant_email() -> str:
    """The mailbox replies land in / drafts are copied to."""
    return str(((applicant().get("identity") or {}).get("email") or "")).strip()


@lru_cache(maxsize=1)
def sites() -> dict:
    with open(paths.SITES_FILE, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def enabled_sites() -> dict:
    return {k: v for k, v in sites().get("sites", {}).items() if v.get("enabled")}


def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge overlay onto a copy of base (base is never mutated)."""
    out = dict(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def effective_criteria() -> dict:
    """criteria.yaml with the learned-preferences overlay merged on top.

    Deliberately NOT @lru_cache'd: the overlay file changes at runtime as
    decisions are recorded, so every call must re-read it.
    """
    from . import learning  # local import to avoid a cycle at module load
    base = criteria()
    overlay = learning.scoring_overlay()
    return deep_merge(base, overlay) if overlay else base
