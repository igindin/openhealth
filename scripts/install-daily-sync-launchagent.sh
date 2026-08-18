#!/usr/bin/env bash
# Install the full WHOOP/Oura daily sync LaunchAgent from a pinned runtime.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/install-pinned-sync-launchagent.sh" --service daily "$@"
