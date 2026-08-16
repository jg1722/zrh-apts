#!/bin/bash
# One-time setup: create the project virtualenv, install dependencies, and
# scaffold your personal config. Safe to re-run — it never overwrites
# config/applicant.yaml once it exists.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "Project root: $ROOT"
echo "Creating virtualenv at .venv ..."
/usr/bin/python3 -m venv .venv

echo "Installing dependencies ..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

.venv/bin/python -c "import requests, yaml, bs4; print('deps OK')"

# Personal config: gitignored, so a fresh clone has to create it.
if [ ! -f config/applicant.yaml ]; then
  cp config/applicant.example.yaml config/applicant.yaml
  echo "Created config/applicant.yaml from the example (gitignored)."
  NEEDS_IDENTITY=1
else
  echo "config/applicant.yaml already exists — left untouched."
  NEEDS_IDENTITY=0
fi

mkdir -p data/digests data/logs data/photos data/cache

echo
echo "──────────────────────────────────────────────────────────────────────"
echo "Setup complete. Two files to edit before your first real run:"
echo
if [ "$NEEDS_IDENTITY" = "1" ]; then
echo "  1. config/applicant.yaml   — your name, phone, email, job details."
echo "                               Drafts are signed 'Vorname Nachname' until"
echo "                               you do this."
fi
echo "  2. config/criteria.yaml    — every line marked «SET THIS»: budget,"
echo "                               size, rooms, move-in window, commute cap,"
echo "                               and the office address if it isn't ours."
echo
echo "Then spot-check the transit gate resolves a real address:"
echo "  .venv/bin/python scripts/transit_check.py \"Birmensdorferstrasse 100, 8003 Zürich\""
echo
echo "Then a full run:"
echo "  bin/run_morning.sh"
echo "──────────────────────────────────────────────────────────────────────"
