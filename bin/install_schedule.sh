#!/bin/bash
# Install (or reinstall) the 07:00 launchd agent for this checkout.
#
# launchd needs absolute paths and cannot expand variables, so we render
# launchd/com.zrhapts.morning.plist.template with this checkout's real location.
# Safe to re-run: it unloads any existing agent first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.zrhapts.morning"
TEMPLATE="$ROOT/launchd/$LABEL.plist.template"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

[ -f "$TEMPLATE" ] || { echo "ERROR: $TEMPLATE missing."; exit 1; }
[ -x "$ROOT/.venv/bin/python" ] || { echo "ERROR: no venv — run bin/setup.sh first."; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/data/logs"
sed "s|__PROJECT_ROOT__|$ROOT|g" "$TEMPLATE" > "$TARGET"

# bootout is a no-op-with-error when nothing is loaded, hence the || true.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "Installed $TARGET"
echo "  runs:     $ROOT/bin/run_morning.sh  daily at 07:00"
echo "  logs:     $ROOT/data/logs/launchd.{out,err}.log"
echo
echo "Verify / test / remove:"
echo "  launchctl list | grep zrhapts"
echo "  launchctl kickstart -k gui/\$(id -u)/$LABEL     # run it right now"
echo "  launchctl bootout  gui/\$(id -u)/$LABEL         # unschedule"
echo
echo "NOTE: your Mac must be awake at 07:00 — launchd does not wake it."
