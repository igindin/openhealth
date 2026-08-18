#!/usr/bin/env bash
# Full local health sync from a pinned code release against a separate data
# workspace. WHOOP full history plus the current body snapshot share one daily
# bundle; Oura and dashboard work remain independent at every schedule window.
set -euo pipefail
umask 077

readonly RUNTIME_ROOT="${OPENHEALTH_RUNTIME_ROOT:?OPENHEALTH_RUNTIME_ROOT is required}"
readonly DATA_ROOT="${OPENHEALTH_DATA_ROOT:?OPENHEALTH_DATA_ROOT is required}"
readonly RUNTIME_REVISION="${OPENHEALTH_RUNTIME_REVISION:?OPENHEALTH_RUNTIME_REVISION is required}"
readonly ENV_FILE="${OPENHEALTH_ENV_FILE:-$DATA_ROOT/.env}"
readonly PINNED_PYTHON_BIN="${OPENHEALTH_PYTHON_BIN:-$(command -v python3 2>/dev/null || true)}"
readonly LIFECYCLE_HELPER="$RUNTIME_ROOT/scripts/runner_lifecycle.py"
readonly LIFECYCLE_LOCK="$DATA_ROOT/data/index/daily-sync.lifecycle.lock"

if [[ "${OPENHEALTH_RUNNER_LIFECYCLE_GUARDED:-}" != "1" ]]; then
  if [[ "$PINNED_PYTHON_BIN" != /* || ! -x "$PINNED_PYTHON_BIN" ]] ||
    [[ -L "$LIFECYCLE_HELPER" || ! -f "$LIFECYCLE_HELPER" ]]; then
    echo "OpenHealth daily lifecycle guard is unavailable" >&2
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
# Capture the host's calendar date before the data workspace environment can
# override TZ; the schedule contract is keyed to the Mac's local day.
readonly HOST_LOCAL_DATE="$(unset TZ; date +%F)"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
# Keep the Python importer on the same host-local calendar used by the daily
# claim. A data-workspace TZ override must not split one bundle across two dates.
unset TZ

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
whoop_ok=0
oura_ok=1
readonly INDEX_DIR="$DATA_ROOT/data/index"
readonly CLAIM_ROOT="$INDEX_DIR/daily-sync-claims"
readonly CLAIM_HELPER="$RUNTIME_ROOT/scripts/daily_sync_claim.py"

mark_failed() {
  failed=1
  if [[ -n "$failed_steps" ]]; then
    failed_steps="$failed_steps,$1"
  else
    failed_steps="$1"
  fi
}

echo "[$(ts)] ===== daily sync start ($RUNTIME_REVISION) ====="

if [[ -L "$CLAIM_HELPER" || ! -f "$CLAIM_HELPER" ]]; then
  echo "[$(ts)] ERROR: WHOOP daily claim helper is unavailable"
  mark_failed "whoop-daily-claim"
else
  local_date="$HOST_LOCAL_DATE"
  if claim_status="$("$PINNED_PYTHON_BIN" -P "$CLAIM_HELPER" claim --root "$CLAIM_ROOT" --date "$local_date")"; then
    case "$claim_status" in
      claimed)
        echo "[$(ts)] WHOOP daily bundle (14d + current body snapshot)..."
        if "$PINNED_PYTHON_BIN" -P -m openhealth --repo-root "$DATA_ROOT" whoop-sync --no-profile --days-back 14; then
          if success_status="$("$PINNED_PYTHON_BIN" -P "$CLAIM_HELPER" success --root "$CLAIM_ROOT" --date "$local_date")" &&
            [[ "$success_status" == "success_marked" || "$success_status" == "already_success" ]]; then
            whoop_ok=1
          else
            echo "[$(ts)] ERROR: WHOOP success marker could not be recorded"
            mark_failed "whoop-success-marker"
          fi
        else
          echo "[$(ts)] ERROR: whoop-sync failed; automatic retry is suppressed until the next local day"
          mark_failed "whoop-sync"
        fi
        ;;
      already_success)
        whoop_ok=1
        echo "[$(ts)] WHOOP daily bundle already succeeded; skip this window"
        ;;
      already_attempted)
        echo "[$(ts)] WHOOP daily bundle already attempted; skip automatic same-day retry"
        ;;
      *)
        echo "[$(ts)] ERROR: invalid WHOOP daily claim status"
        mark_failed "whoop-daily-claim"
        ;;
    esac
  else
    echo "[$(ts)] ERROR: WHOOP daily claim failed"
    mark_failed "whoop-daily-claim"
  fi
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
