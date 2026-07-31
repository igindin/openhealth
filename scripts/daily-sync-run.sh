#!/usr/bin/env bash
# Full local health sync from a pinned code release against a separate data
# workspace. It replaces the mutable ~/.openhealth/daily-sync.sh runner.
set -euo pipefail
umask 077

readonly RUNTIME_ROOT="${OPENHEALTH_RUNTIME_ROOT:?OPENHEALTH_RUNTIME_ROOT is required}"
readonly DATA_ROOT="${OPENHEALTH_DATA_ROOT:?OPENHEALTH_DATA_ROOT is required}"
readonly RUNTIME_REVISION="${OPENHEALTH_RUNTIME_REVISION:?OPENHEALTH_RUNTIME_REVISION is required}"
readonly ENV_FILE="${OPENHEALTH_ENV_FILE:-$DATA_ROOT/.env}"
readonly PINNED_PYTHON_BIN="${OPENHEALTH_PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"

if [[ "$RUNTIME_ROOT" != /* || "$DATA_ROOT" != /* || "$RUNTIME_ROOT" == "$DATA_ROOT" ]]; then
  echo "OpenHealth runtime and data roots must be separate absolute paths" >&2
  exit 1
fi
if [[ ! -d "$RUNTIME_ROOT/openhealth" || ! -d "$DATA_ROOT/data" ]]; then
  echo "OpenHealth runtime or data root is unavailable" >&2
  exit 1
fi
if [[ ! -f "$RUNTIME_ROOT/REVISION" ]] ||
  [[ "$(tr -d '[:space:]' < "$RUNTIME_ROOT/REVISION")" != "$RUNTIME_REVISION" ]]; then
  echo "OpenHealth pinned runtime revision does not match $RUNTIME_REVISION" >&2
  exit 1
fi
if [[ "$PINNED_PYTHON_BIN" != /* || ! -x "$PINNED_PYTHON_BIN" ]]; then
  echo "OpenHealth pinned Python must be an absolute executable path: $PINNED_PYTHON_BIN" >&2
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
  echo "OpenHealth environment file not found: $ENV_FILE" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

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

ts() { date "+%Y-%m-%d %H:%M:%S"; }
failed=0
failed_steps=""
whoop_ok=1
oura_ok=1

mark_failed() {
  failed=1
  if [[ -n "$failed_steps" ]]; then
    failed_steps="$failed_steps,$1"
  else
    failed_steps="$1"
  fi
}

echo "[$(ts)] ===== daily sync start ($RUNTIME_REVISION) ====="

echo "[$(ts)] WHOOP sync (14d)..."
if ! "$PINNED_PYTHON_BIN" -P -m openhealth --repo-root "$DATA_ROOT" whoop-sync --no-profile --days-back 14; then
  echo "[$(ts)] ERROR: whoop-sync failed"
  whoop_ok=0
  mark_failed "whoop-sync"
fi

echo "[$(ts)] Oura sync (14d)..."
if ! "$PINNED_PYTHON_BIN" -P -m openhealth --repo-root "$DATA_ROOT" oura-sync --days-back 14; then
  echo "[$(ts)] ERROR: oura-sync failed"
  oura_ok=0
  mark_failed "oura-sync"
fi

if [[ "$whoop_ok" -eq 1 && "$oura_ok" -eq 1 ]]; then
  echo "[$(ts)] weekly pass (idempotent)..."
  # The scheduler prints derived health values on success; keep the durable
  # service log operational-only while preserving failures on stderr.
  if ! "$PINNED_PYTHON_BIN" -P -m openhealth.scheduler --repo-root "$DATA_ROOT" >/dev/null; then
    echo "[$(ts)] ERROR: weekly scheduler failed"
    mark_failed "weekly-scheduler"
  fi
else
  echo "[$(ts)] skip weekly pass: WHOOP and Oura imports must both succeed"
fi

echo "[$(ts)] rebuild dashboard data..."
# The dashboard builder's success summary includes current health values.
if ! "$PINNED_PYTHON_BIN" -P "$RUNTIME_ROOT/ui/web/build_dashboard_data.py" \
    --db "$DATA_ROOT/data/index/health_os.sqlite3" \
    --out "$DATA_ROOT/ui/web/data.local.json" >/dev/null; then
  echo "[$(ts)] ERROR: dashboard build failed"
  mark_failed "dashboard-build"
fi

if [[ "$failed" -ne 0 ]]; then
  echo "[$(ts)] ===== daily sync completed with failures: $failed_steps ====="
  exit 1
fi

echo "[$(ts)] ===== daily sync done ====="
