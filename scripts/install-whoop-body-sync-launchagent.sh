#!/usr/bin/env bash
# Compatibility entry point. WHOOP history and body measurements now share the
# daily bundle, so this installs the single daily LaunchAgent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "WHOOP body sync is included in the daily LaunchAgent; installing that service." >&2
exec "$SCRIPT_DIR/install-pinned-sync-launchagent.sh" --service whoop-body "$@"
