#!/bin/bash
# Morning pipeline wrapper — triggered by launchd at 07:00.
# Runs the deterministic steps directly (robust, free), invokes Claude ONLY for
# the vision style-scoring step, then notifies + opens the digest.
# Never drafts or sends email.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# launchd gives a minimal PATH; add the tools we need.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

PY="$ROOT/.venv/bin/python"
TODAY="$(date +%F)"
LOG="$ROOT/data/logs/run-$TODAY.log"
mkdir -p "$ROOT/data/logs"

# Keep the Mac awake for the whole run: launchd fires when the lid opens and a
# sleep mid-run stretched one pipeline pass across days (observed 2026-06-30 →
# 07-02). -w releases the assertion the moment this script exits.
/usr/bin/caffeinate -i -s -w $$ &

{
  echo "=== morning run $(date) ==="
  if [ ! -x "$PY" ]; then
    echo "ERROR: venv missing at $PY — run bin/setup.sh first."
    exit 1
  fi

  # launchd often fires seconds after wake, before Wi-Fi is up — a pass without
  # network burns every listing as FAIL. Wait up to 10 min for DNS to work.
  tries=0
  until "$PY" -c 'import socket; socket.getaddrinfo("flatfox.ch", 443)' 2>/dev/null; do
    tries=$((tries + 1))
    if [ "$tries" -ge 40 ]; then
      echo "ERROR: no network after 10 min — aborting run."
      exit 1
    fi
    echo "network not up yet (try $tries/40) — sleeping 15s"
    sleep 15
  done

  "$PY" scripts/scout.py
  "$PY" scripts/verify_listings.py --scope all
  "$PY" scripts/gate.py
  "$PY" scripts/fetch_photos.py

  # Vision scoring only if there are survivors that need it.
  if "$PY" - <<'PYEOF'
import json, sys, pathlib
m = pathlib.Path("data/photos/_manifest.json")
data = json.loads(m.read_text()) if m.exists() else {}
sys.exit(0 if data else 1)
PYEOF
  then
    echo "--- vision style-scoring via Claude ---"
    claude -p "Run ONLY the vision style-scoring step from CLAUDE.md / workflows/3-assess-style.md. Read data/photos/_manifest.json; for each listing, Read its photo files and grade the kitchen and the bathroom as one of modern|acceptable|dated|condition_unknown per style/README.md. Write a JSON object mapping listing id to {\"kitchen\":...,\"bath\":...,\"reason\":...} to data/.verdicts.json, then run: .venv/bin/python scripts/apply_assessment.py --file data/.verdicts.json . Do NOT draft or send any email. Do nothing else." \
      --permission-mode acceptEdits \
      || echo "WARN: vision step failed; continuing with condition_unknown"
  else
    echo "no survivors need vision scoring"
  fi

  "$PY" scripts/bucket.py
  "$PY" scripts/digest.py

  # --- daily Gmail reply pipeline: match -> draft -> learn (best-effort) ---
  "$PY" scripts/check_replies.py || echo "WARN: reply pipeline failed; continuing"

  echo "=== done $(date) ==="
} >>"$LOG" 2>&1

# Notify + auto-open today's digest (must run outside the log redirect for GUI).
DIGEST="$ROOT/data/digests/$TODAY.md"
SUMMARY="$(cat "$ROOT/data/.last_summary.txt" 2>/dev/null || echo 'Digest ready')"
if [ -f "$DIGEST" ]; then
  /usr/bin/osascript -e "display notification \"${SUMMARY}\" with title \"ZRH APTS\" subtitle \"Morning digest\" sound name \"Glass\"" 2>/dev/null
  /usr/bin/open "$DIGEST" 2>/dev/null
else
  /usr/bin/osascript -e "display notification \"Run failed — see data/logs/run-${TODAY}.log\" with title \"ZRH APTS\"" 2>/dev/null
fi
