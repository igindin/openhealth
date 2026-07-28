#!/usr/bin/env bash
# Load local WHOOP credentials and capture the provider's current body metrics.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${OPENHEALTH_ENV_FILE:-$REPO/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "WHOOP environment file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

PYTHON_BIN="${OPENHEALTH_PYTHON_BIN:-python3}"
exec "$PYTHON_BIN" -m openhealth --repo-root "$REPO" whoop-body-sync
