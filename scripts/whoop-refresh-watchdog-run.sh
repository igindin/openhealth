#!/usr/bin/env bash
# Run the privacy-safe WHOOP refresh watcher from an immutable pinned runtime.
set -euo pipefail
umask 077

readonly RUNTIME_ROOT="${OPENHEALTH_RUNTIME_ROOT:?OPENHEALTH_RUNTIME_ROOT is required}"
readonly DATA_ROOT="${OPENHEALTH_DATA_ROOT:?OPENHEALTH_DATA_ROOT is required}"
readonly RUNTIME_REVISION="${OPENHEALTH_RUNTIME_REVISION:?OPENHEALTH_RUNTIME_REVISION is required}"
readonly ENV_FILE="${OPENHEALTH_ENV_FILE:-$DATA_ROOT/.env}"
readonly PINNED_PYTHON_BIN="${OPENHEALTH_PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"
readonly LIFECYCLE_HELPER="$RUNTIME_ROOT/scripts/runner_lifecycle.py"
readonly LIFECYCLE_LOCK="$DATA_ROOT/data/index/whoop-refresh-watchdog.lifecycle.lock"

if [[ "${OPENHEALTH_RUNNER_LIFECYCLE_GUARDED:-}" != "1" ]]; then
  if [[ "$PINNED_PYTHON_BIN" != /* || ! -x "$PINNED_PYTHON_BIN" ]] ||
    [[ -L "$LIFECYCLE_HELPER" || ! -f "$LIFECYCLE_HELPER" ]]; then
    echo "OpenHealth watchdog lifecycle guard is unavailable" >&2
    exit 1
  fi
  for name in "${!PYTHON@}"; do unset "$name"; done
  export PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
  exec "$PINNED_PYTHON_BIN" -P "$LIFECYCLE_HELPER" \
    --lock "$LIFECYCLE_LOCK" -- /bin/bash "$0"
fi

if [[ "$RUNTIME_ROOT" != /* || "$DATA_ROOT" != /* || "$RUNTIME_ROOT" == "$DATA_ROOT" ]]; then
  echo "OpenHealth runtime and data roots must be separate absolute paths" >&2
  exit 1
fi
if [[ ! -d "$RUNTIME_ROOT/openhealth" || ! -d "$DATA_ROOT/data/index" ]]; then
  echo "OpenHealth runtime or data root is unavailable" >&2
  exit 1
fi
if [[ ! -f "$RUNTIME_ROOT/REVISION" ]] ||
  [[ "$(tr -d '[:space:]' < "$RUNTIME_ROOT/REVISION")" != "$RUNTIME_REVISION" ]]; then
  echo "OpenHealth pinned runtime revision does not match $RUNTIME_REVISION" >&2
  exit 1
fi
if [[ "$PINNED_PYTHON_BIN" != /* || ! -x "$PINNED_PYTHON_BIN" ]]; then
  echo "OpenHealth pinned Python must be an absolute executable path" >&2
  exit 1
fi
readonly RUNTIME_VERIFIER="$RUNTIME_ROOT/scripts/build_pinned_runtime.py"
if [[ -L "$RUNTIME_VERIFIER" || ! -f "$RUNTIME_VERIFIER" ]]; then
  echo "OpenHealth pinned runtime verifier is unavailable" >&2
  exit 1
fi
if ! (
  for name in "${!PYTHON@}"; do
    unset "$name"
  done
  export PYTHONSAFEPATH=1
  export PYTHONNOUSERSITE=1
  export PYTHONDONTWRITEBYTECODE=1
  exec "$PINNED_PYTHON_BIN" -P "$RUNTIME_VERIFIER" verify \
    --release "$RUNTIME_ROOT" \
    --revision "$RUNTIME_REVISION"
) >/dev/null; then
  echo "OpenHealth pinned runtime manifest verification failed" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  echo "OpenHealth environment file not found" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
if [[ -z "${OPENHEALTH_TELEGRAM_ALERT_CHAT_ID:-}" ]]; then
  echo "OPENHEALTH_TELEGRAM_ALERT_CHAT_ID is required" >&2
  exit 2
fi

for name in "${!PYTHON@}"; do
  unset "$name"
done
export OPENHEALTH_RUNTIME_ROOT="$RUNTIME_ROOT"
export OPENHEALTH_DATA_ROOT="$DATA_ROOT"
export OPENHEALTH_RUNTIME_REVISION="$RUNTIME_REVISION"
export OPENHEALTH_PYTHON_BIN="$PINNED_PYTHON_BIN"
export PYTHONPATH="$RUNTIME_ROOT"
export PYTHONSAFEPATH=1
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
cd "$RUNTIME_ROOT"

exec "$PINNED_PYTHON_BIN" -P -m openhealth.watchdog --repo-root "$DATA_ROOT"
