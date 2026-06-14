#!/usr/bin/env bash
# Install the OpenHealth health-sync LaunchAgent: keeps ui/web/health_sync_run.py
# --watch running in the background, ingesting the iCloud inbox and writing the
# outbox for the phone. macOS only.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_SRC="$REPO/scripts/health-sync.plist"
DEST="$HOME/Library/LaunchAgents/org.openhealth.health-sync.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$REPO/data/index"
sed "s#__REPO__#$REPO#g" "$PLIST_SRC" > "$DEST"

launchctl unload "$DEST" 2>/dev/null || true
launchctl load "$DEST"

echo "Loaded $DEST"
echo "health_sync_run.py --watch is now running in the background."
echo "Logs:  $REPO/data/index/health-sync.log"
echo "Stop:  launchctl unload $DEST"
