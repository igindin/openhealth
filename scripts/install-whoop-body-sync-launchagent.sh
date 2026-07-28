#!/usr/bin/env bash
# Install the local daily WHOOP body-measurement snapshot LaunchAgent.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$REPO/scripts/whoop-body-sync.plist"
LAUNCH_AGENTS_DIR="${OPENHEALTH_LAUNCH_AGENTS_DIR:-${HOME:?}/Library/LaunchAgents}"
DEST="$LAUNCH_AGENTS_DIR/org.openhealth.whoop-body-sync.plist"
PYTHON_BIN="${OPENHEALTH_PYTHON_BIN:-$(command -v python3)}"

mkdir -p "$LAUNCH_AGENTS_DIR" "$REPO/data/index"
sed -e "s#__REPO__#$REPO#g" -e "s#__PYTHON__#$PYTHON_BIN#g" "$PLIST_SRC" > "$DEST"

launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "Loaded $DEST"
echo "WHOOP body measurements will be captured daily at 12:15 local time and at login."
echo "Logs: $REPO/data/index/whoop-body-sync.log"
echo "Stop: launchctl unload $DEST"
