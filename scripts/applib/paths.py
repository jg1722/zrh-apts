"""Filesystem locations. Everything is resolved relative to the project root
so scripts work no matter what directory they're invoked from."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

# scripts/applib/paths.py -> project root is two parents up.
ROOT = Path(__file__).resolve().parents[2]

CONFIG_DIR = ROOT / "config"
CRITERIA_FILE = CONFIG_DIR / "criteria.yaml"
SITES_FILE = CONFIG_DIR / "sites.yaml"
# Who is applying. applicant.yaml is gitignored (personal data); the .example
# next to it is committed and acts as the fallback. See applib.config.applicant.
APPLICANT_FILE = CONFIG_DIR / "applicant.yaml"
APPLICANT_EXAMPLE_FILE = CONFIG_DIR / "applicant.example.yaml"

DATA_DIR = ROOT / "data"
LISTINGS_FILE = DATA_DIR / "listings.json"
DIGESTS_DIR = DATA_DIR / "digests"
LOGS_DIR = DATA_DIR / "logs"
PHOTOS_DIR = DATA_DIR / "photos"
SUMMARY_FILE = DATA_DIR / ".last_summary.txt"  # one-liner for the launchd notification
LEARNED_PREFS_FILE = DATA_DIR / ".learned_prefs.json"  # learning overlay (gitignored)
LEARNING_LOG_FILE = DATA_DIR / ".learning_log.jsonl"   # append-only retune log
OUTREACH_CONTEXT_FILE = DATA_DIR / ".outreach_context.json"  # prep for the gmail reply matcher (gitignored)
REPLY_MATCHES_FILE = DATA_DIR / ".reply_matches.json"        # claude -p scratch output (gitignored)
DRAFT_JOBS_FILE = DATA_DIR / ".draft_jobs.json"        # drafter input (gitignored)
DRAFT_RESULTS_FILE = DATA_DIR / ".draft_results.json"  # drafter claude -p output (gitignored)
LEARN_JOBS_FILE = DATA_DIR / ".learn_jobs.json"        # learner input (gitignored)
LEARN_RESULTS_FILE = DATA_DIR / ".learn_results.json"  # learner claude -p output (gitignored)
DRAFT_STYLE_FILE = DATA_DIR / ".draft_style.json"      # accumulated draft-style lessons (gitignored)
WEB_DIR = ROOT / "web"                                 # frontend static files

STYLE_DIR = ROOT / "style"
TEMPLATES_DIR = ROOT / "templates"
DOSSIER_DIR = ROOT / "dossier"


def today_iso() -> str:
    return _dt.date.today().isoformat()


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def digest_file(date_iso: str | None = None) -> Path:
    return DIGESTS_DIR / f"{date_iso or today_iso()}.md"


def ensure_dirs() -> None:
    for d in (DATA_DIR, DIGESTS_DIR, LOGS_DIR, PHOTOS_DIR):
        d.mkdir(parents=True, exist_ok=True)
