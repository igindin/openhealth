#!/usr/bin/env bash
# Install the daily WHOOP body snapshot LaunchAgent from an explicit pinned
# runtime release. The shared installer keeps the old service runnable until
# the replacement has bootstrapped successfully.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/install-pinned-sync-launchagent.sh" --service whoop-body "$@"
