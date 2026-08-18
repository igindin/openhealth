#!/usr/bin/env bash
# Render and install a pinned OpenHealth LaunchAgent with rollback-safe service
# replacement and crash-safe retirement of the legacy WHOOP body scheduler.
set -euo pipefail
umask 077
ORIGINAL_ARGS=("$@")

usage() {
  cat >&2 <<'EOF'
Usage: install-pinned-sync-launchagent.sh --service SERVICE \
  --runtime-root PATH --data-root PATH --revision SHA [options]

Services: daily, whoop-watchdog (whoop-body is a compatibility alias for daily)
Options:
  --python-bin PATH       Python executable (default: OPENHEALTH_PYTHON_BIN/python3)
  --render-only PATH      Validate and render only; do not call launchctl
  --launch-agents-dir PATH
                          Destination directory (default: ~/Library/LaunchAgents)
EOF
}

SERVICE=""
RUNTIME_ROOT=""
DATA_ROOT=""
RUNTIME_REVISION=""
PYTHON_BIN="${OPENHEALTH_PYTHON_BIN:-$(command -v python3)}"
RENDER_ONLY=""
LAUNCH_AGENTS_DIR="${OPENHEALTH_LAUNCH_AGENTS_DIR:-${HOME:?}/Library/LaunchAgents}"
LAUNCHCTL_BIN="${OPENHEALTH_LAUNCHCTL_BIN:-$(command -v launchctl 2>/dev/null || true)}"
PLUTIL_BIN="${OPENHEALTH_PLUTIL_BIN:-$(command -v plutil 2>/dev/null || true)}"
LAUNCH_DOMAIN="${OPENHEALTH_LAUNCH_DOMAIN:-gui/$(id -u)}"

while (( $# )); do
  case "$1" in
    --service) SERVICE="${2:-}"; shift 2 ;;
    --runtime-root) RUNTIME_ROOT="${2:-}"; shift 2 ;;
    --data-root) DATA_ROOT="${2:-}"; shift 2 ;;
    --revision) RUNTIME_REVISION="${2:-}"; shift 2 ;;
    --python-bin) PYTHON_BIN="${2:-}"; shift 2 ;;
    --render-only) RENDER_ONLY="${2:-}"; shift 2 ;;
    --launch-agents-dir) LAUNCH_AGENTS_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$SERVICE" || -z "$RUNTIME_ROOT" || -z "$DATA_ROOT" || -z "$RUNTIME_REVISION" ]]; then
  usage
  exit 2
fi
if [[ "$SERVICE" == "whoop-body" ]]; then
  SERVICE="daily"
fi
if [[ "$RUNTIME_ROOT" != /* || "$DATA_ROOT" != /* || "$LAUNCH_AGENTS_DIR" != /* ]]; then
  echo "Runtime, data, and LaunchAgents roots must be absolute paths" >&2
  exit 2
fi
if [[ "$RUNTIME_ROOT" == "$DATA_ROOT" ]]; then
  echo "Runtime and data roots must be separate" >&2
  exit 2
fi
if [[ ! "$RUNTIME_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Revision must be a full lowercase Git SHA" >&2
  exit 2
fi
if [[ -L "$RUNTIME_ROOT" || ! -d "$RUNTIME_ROOT" ]]; then
  echo "Pinned runtime root is missing or is a symlink: $RUNTIME_ROOT" >&2
  exit 1
fi
if [[ ! -f "$RUNTIME_ROOT/REVISION" ]] ||
  [[ "$(tr -d '[:space:]' < "$RUNTIME_ROOT/REVISION")" != "$RUNTIME_REVISION" ]]; then
  echo "Pinned runtime REVISION does not match $RUNTIME_REVISION" >&2
  exit 1
fi
if [[ ! -d "$RUNTIME_ROOT/openhealth" || ! -d "$DATA_ROOT/data" ]]; then
  echo "Runtime package or data workspace is unavailable" >&2
  exit 1
fi
if [[ ! -f "$DATA_ROOT/.env" ]]; then
  echo "Data workspace environment file is missing: $DATA_ROOT/.env" >&2
  exit 1
fi
if [[ "$PYTHON_BIN" != /* || ! -x "$PYTHON_BIN" ]]; then
  echo "Python must be an absolute executable path: $PYTHON_BIN" >&2
  exit 1
fi
if [[ -z "$PLUTIL_BIN" || ! -x "$PLUTIL_BIN" ]]; then
  echo "plutil is required to render and validate LaunchAgents" >&2
  exit 1
fi

RUNTIME_VERIFIER="$RUNTIME_ROOT/scripts/build_pinned_runtime.py"
if [[ -L "$RUNTIME_VERIFIER" || ! -f "$RUNTIME_VERIFIER" ]]; then
  echo "Pinned runtime verifier is unavailable: $RUNTIME_VERIFIER" >&2
  exit 1
fi
if ! (
  pinned_interpreter="$PYTHON_BIN"
  for name in "${!PYTHON@}"; do unset "$name"; done
  export PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
  exec "$pinned_interpreter" -P "$RUNTIME_VERIFIER" verify \
    --release "$RUNTIME_ROOT" --revision "$RUNTIME_REVISION"
) >/dev/null; then
  echo "Pinned runtime manifest verification failed" >&2
  exit 1
fi

MIGRATION_ENABLED=0
case "$SERVICE" in
  daily)
    LABEL="com.openhealth.daily-sync"
    PLIST_NAME="daily-sync.plist"
    RUNNER_NAME="daily-sync-run.sh"
    LOG_NAME="daily-sync.log"
    ERR_NAME="daily-sync.log"
    SCHEDULE_DESCRIPTION="WHOOP will run at most once per local day (first 09:00/14:00/21:00 window); Oura remains scheduled at all three windows."
    MIGRATION_ENABLED=1
    ;;
  whoop-watchdog)
    LABEL="com.openhealth.whoop-refresh-watchdog"
    PLIST_NAME="whoop-refresh-watchdog.plist"
    RUNNER_NAME="whoop-refresh-watchdog-run.sh"
    LOG_NAME="whoop-refresh-watchdog.log"
    ERR_NAME="whoop-refresh-watchdog.err"
    SCHEDULE_DESCRIPTION="WHOOP refresh incidents will be checked on marker changes and every 120 seconds."
    ;;
  *) echo "Unsupported service: $SERVICE" >&2; exit 2 ;;
esac

PLIST_SRC="$RUNTIME_ROOT/scripts/$PLIST_NAME"
RUNNER="$RUNTIME_ROOT/scripts/$RUNNER_NAME"
MIGRATION_HELPER="$RUNTIME_ROOT/scripts/launchagent_migration.py"
PRIVATE_FILE_HELPER="$RUNTIME_ROOT/scripts/operational_file.py"
LIFECYCLE_HELPER="$RUNTIME_ROOT/scripts/runner_lifecycle.py"
if [[ ! -f "$PLIST_SRC" || ! -x "$RUNNER" ]]; then
  echo "Pinned runtime is missing $PLIST_NAME or executable $RUNNER_NAME" >&2
  exit 1
fi
if [[ -L "$PRIVATE_FILE_HELPER" || ! -f "$PRIVATE_FILE_HELPER" ]]; then
  echo "Pinned private-file helper is unavailable" >&2
  exit 1
fi
if [[ -L "$LIFECYCLE_HELPER" || ! -f "$LIFECYCLE_HELPER" ]]; then
  echo "Pinned lifecycle helper is unavailable" >&2
  exit 1
fi
if [[ -L "$MIGRATION_HELPER" || ! -f "$MIGRATION_HELPER" ]]; then
  echo "Pinned LaunchAgent transaction helper is unavailable" >&2
  exit 1
fi
if [[ "$SERVICE" == "daily" ]] &&
  { [[ ! -f "$RUNTIME_ROOT/ui/web/build_dashboard_data.py" ]] || [[ ! -d "$DATA_ROOT/ui/web" ]]; }; then
  echo "Pinned dashboard builder or data-workspace dashboard directory is unavailable" >&2
  exit 1
fi

STAGED_PLIST=""
PRIOR_PLIST_BACKUP=""
RESTORE_PLIST=""
HAD_DEST=0
WAS_LOADED=0
DEPLOYMENT_ACTIVE=0
MIGRATION_PREPARED=0
MIGRATION_COMMITTED=0
LEGACY_SOURCE_PRESENT=0
LEGACY_WAS_LOADED=0
ROLLBACK_OK=1

cleanup() {
  local status="$?"
  trap - EXIT HUP INT TERM
  if (( DEPLOYMENT_ACTIVE )) && declare -F rollback_service >/dev/null; then
    if ! rollback_service; then status=1; ROLLBACK_OK=0; fi
  fi
  if (( MIGRATION_ENABLED && MIGRATION_PREPARED && ! MIGRATION_COMMITTED )) &&
    declare -F reconcile_daily_migration_after_failure >/dev/null; then
    if ! reconcile_daily_migration_after_failure; then status=1; ROLLBACK_OK=0; fi
  fi
  if [[ -n "$STAGED_PLIST" && -e "$STAGED_PLIST" ]]; then rm -f -- "$STAGED_PLIST"; fi
  if [[ -n "$RESTORE_PLIST" && -e "$RESTORE_PLIST" ]]; then rm -f -- "$RESTORE_PLIST"; fi
  if [[ -n "$PRIOR_PLIST_BACKUP" && -e "$PRIOR_PLIST_BACKUP" ]]; then
    if (( ROLLBACK_OK )); then
      rm -f -- "$PRIOR_PLIST_BACKUP"
    else
      echo "CRITICAL: owner-only prior plist backup preserved at $PRIOR_PLIST_BACKUP" >&2
    fi
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

render_plist() {
  local destination="$1"
  cp "$PLIST_SRC" "$destination"
  "$PLUTIL_BIN" -remove ProgramArguments.1 "$destination"
  "$PLUTIL_BIN" -insert ProgramArguments.1 -string "$RUNNER" "$destination"
  "$PLUTIL_BIN" -replace WorkingDirectory -string "$RUNTIME_ROOT" "$destination"
  "$PLUTIL_BIN" -replace EnvironmentVariables.OPENHEALTH_RUNTIME_ROOT -string "$RUNTIME_ROOT" "$destination"
  "$PLUTIL_BIN" -replace EnvironmentVariables.OPENHEALTH_DATA_ROOT -string "$DATA_ROOT" "$destination"
  "$PLUTIL_BIN" -replace EnvironmentVariables.OPENHEALTH_RUNTIME_REVISION -string "$RUNTIME_REVISION" "$destination"
  "$PLUTIL_BIN" -replace EnvironmentVariables.OPENHEALTH_ENV_FILE -string "$DATA_ROOT/.env" "$destination"
  "$PLUTIL_BIN" -replace EnvironmentVariables.OPENHEALTH_PYTHON_BIN -string "$PYTHON_BIN" "$destination"
  "$PLUTIL_BIN" -replace EnvironmentVariables.PYTHONPATH -string "$RUNTIME_ROOT" "$destination"
  "$PLUTIL_BIN" -replace StandardOutPath -string "$DATA_ROOT/data/index/$LOG_NAME" "$destination"
  "$PLUTIL_BIN" -replace StandardErrorPath -string "$DATA_ROOT/data/index/$ERR_NAME" "$destination"
  if [[ "$SERVICE" == "whoop-watchdog" ]]; then
    # Swift plutil treats a numeric replace key as an array insertion. Remove
    # the template value first so no placeholder survives rendering.
    "$PLUTIL_BIN" -remove WatchPaths.0 "$destination"
    "$PLUTIL_BIN" -insert WatchPaths.0 -string "$DATA_ROOT/data/index/whoop_tokens.json.refresh-state" "$destination"
  fi
  "$PLUTIL_BIN" -lint "$destination" >/dev/null
  if [[ "$("$PLUTIL_BIN" -extract Label raw "$destination")" != "$LABEL" ]] ||
    [[ "$("$PLUTIL_BIN" -extract ProgramArguments raw "$destination")" != "2" ]] ||
    [[ "$("$PLUTIL_BIN" -extract ProgramArguments.1 raw "$destination")" != "$RUNNER" ]] ||
    [[ "$("$PLUTIL_BIN" -extract WorkingDirectory raw "$destination")" != "$RUNTIME_ROOT" ]] ||
    [[ "$("$PLUTIL_BIN" -extract EnvironmentVariables.OPENHEALTH_DATA_ROOT raw "$destination")" != "$DATA_ROOT" ]] ||
    [[ "$("$PLUTIL_BIN" -extract EnvironmentVariables.OPENHEALTH_RUNTIME_REVISION raw "$destination")" != "$RUNTIME_REVISION" ]] ||
    [[ "$("$PLUTIL_BIN" -extract EnvironmentVariables.OPENHEALTH_PYTHON_BIN raw "$destination")" != "$PYTHON_BIN" ]] ||
    [[ "$("$PLUTIL_BIN" -extract EnvironmentVariables.PYTHONPATH raw "$destination")" != "$RUNTIME_ROOT" ]] ||
    [[ "$("$PLUTIL_BIN" -extract EnvironmentVariables.PYTHONSAFEPATH raw "$destination")" != "1" ]]; then
    echo "Rendered LaunchAgent does not match the requested pin" >&2
    return 1
  fi
  chmod 600 "$destination"
}

if [[ -n "$RENDER_ONLY" ]]; then
  if [[ "$RENDER_ONLY" != /* ]]; then echo "Render destination must be an absolute path" >&2; exit 2; fi
  mkdir -p "$(dirname "$RENDER_ONLY")"
  STAGED_PLIST="$(mktemp "$(dirname "$RENDER_ONLY")/.${LABEL}.render.XXXXXX")"
  render_plist "$STAGED_PLIST"
  mv -f "$STAGED_PLIST" "$RENDER_ONLY"
  STAGED_PLIST=""
  echo "Rendered and validated $RENDER_ONLY"
  exit 0
fi

if [[ -z "$LAUNCHCTL_BIN" || ! -x "$LAUNCHCTL_BIN" ]]; then
  echo "launchctl is required unless --render-only is used" >&2
  exit 1
fi

INSTALL_TRANSACTION_LOCK="$DATA_ROOT/data/index/openhealth-launchagent.installer.lock"
if [[ "${OPENHEALTH_INSTALLER_TRANSACTION_GUARDED:-}" != "1" ]]; then
  pinned_interpreter="$PYTHON_BIN"
  for name in "${!PYTHON@}"; do unset "$name"; done
  export PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
  exec "$pinned_interpreter" -P "$LIFECYCLE_HELPER" \
    --guard-env OPENHEALTH_INSTALLER_TRANSACTION_GUARDED \
    --lock "$INSTALL_TRANSACTION_LOCK" -- /bin/bash "$0" "${ORIGINAL_ARGS[@]}"
fi

INDEX_DIR="$DATA_ROOT/data/index"
SYNC_LOCK_PATH="$INDEX_DIR/whoop-sync.lock"
DAILY_LIFECYCLE_LOCK="$INDEX_DIR/daily-sync.lifecycle.lock"
WATCHDOG_LIFECYCLE_LOCK="$INDEX_DIR/whoop-refresh-watchdog.lifecycle.lock"
LOG_PATH="$INDEX_DIR/$LOG_NAME"
ERR_PATH="$INDEX_DIR/$ERR_NAME"
DEST="$LAUNCH_AGENTS_DIR/$LABEL.plist"
SERVICE_TARGET="$LAUNCH_DOMAIN/$LABEL"
LEGACY_LABEL="org.openhealth.whoop-body-sync"
LEGACY_DEST="$LAUNCH_AGENTS_DIR/$LEGACY_LABEL.plist"
LEGACY_BACKUP="$LAUNCH_AGENTS_DIR/.org.openhealth.whoop-body-sync.plist.openhealth-migration-backup"
LEGACY_MARKER="$LAUNCH_AGENTS_DIR/.org.openhealth.whoop-body-sync.openhealth-migration-v1"
LEGACY_SERVICE_TARGET="$LAUNCH_DOMAIN/$LEGACY_LABEL"

run_migration_helper() {
  (
    local operation="$1"
    shift
    local pinned_interpreter="$PYTHON_BIN"
    for name in "${!PYTHON@}"; do unset "$name"; done
    export PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
    exec "$pinned_interpreter" -P "$MIGRATION_HELPER" "$operation" \
      --launch-agents-dir "$LAUNCH_AGENTS_DIR" "$@"
  )
}

wait_for_service_absent() {
  local target="$1"
  local attempt
  for attempt in {1..50}; do
    if ! "$LAUNCHCTL_BIN" print "$target" >/dev/null 2>&1; then return 0; fi
    sleep 0.1
  done
  return 1
}

guarded_bootout() {
  local service_label="$1"
  local result
  local lock_args=()
  case "$service_label" in
    "$LEGACY_LABEL") lock_args=(--sync-lock "$SYNC_LOCK_PATH") ;;
    "com.openhealth.daily-sync")
      lock_args=(--lifecycle-lock "$DAILY_LIFECYCLE_LOCK" --sync-lock "$SYNC_LOCK_PATH")
      ;;
    "com.openhealth.whoop-refresh-watchdog")
      lock_args=(--lifecycle-lock "$WATCHDOG_LIFECYCLE_LOCK")
      ;;
    *) return 1 ;;
  esac
  result="$(run_migration_helper guarded-bootout \
    --launchctl-bin "$LAUNCHCTL_BIN" \
    --launch-domain "$LAUNCH_DOMAIN" \
    --service-label "$service_label" \
    "${lock_args[@]}")" || return 1
  [[ "$result" == "retired" || "$result" == "absent" ]]
}

mkdir -p "$LAUNCH_AGENTS_DIR"
if (( MIGRATION_ENABLED )); then
  recovery_status="$(run_migration_helper recover)"
  case "$recovery_status" in
    no_recovery|recovery_ready|restored_legacy_path|retired_legacy_path) ;;
    *) echo "Invalid legacy WHOOP recovery status" >&2; exit 1 ;;
  esac
fi

SERVICE_SNAPSHOT=""
if SERVICE_SNAPSHOT="$("$LAUNCHCTL_BIN" print "$SERVICE_TARGET" 2>/dev/null)"; then
  WAS_LOADED=1
  if [[ -L "$DEST" || ! -f "$DEST" ]]; then
    echo "Loaded $LABEL has no regular rollback plist at $DEST; refusing replacement" >&2
    exit 1
  fi
  if printf '%s\n' "$SERVICE_SNAPSHOT" | grep -Eq $'^\tpid[[:space:]]*=[[:space:]]*[0-9]+[[:space:]]*$'; then
    echo "Loaded $LABEL is actively running; stop and verify it before replacement" >&2
    exit 1
  fi
elif [[ -e "$DEST" && ( -L "$DEST" || ! -f "$DEST" ) ]]; then
  echo "Existing LaunchAgent plist is not a regular file: $DEST" >&2
  exit 1
fi

if (( MIGRATION_ENABLED )); then
  LEGACY_SERVICE_SNAPSHOT=""
  if LEGACY_SERVICE_SNAPSHOT="$("$LAUNCHCTL_BIN" print "$LEGACY_SERVICE_TARGET" 2>/dev/null)"; then
    LEGACY_WAS_LOADED=1
    if printf '%s\n' "$LEGACY_SERVICE_SNAPSHOT" | grep -Eq $'^\tpid[[:space:]]*=[[:space:]]*[0-9]+[[:space:]]*$'; then
      echo "Loaded $LEGACY_LABEL is actively running; retry migration after that sync finishes" >&2
      exit 1
    fi
    if { [[ -L "$LEGACY_DEST" ]] || [[ ! -f "$LEGACY_DEST" ]]; } &&
      { [[ -L "$LEGACY_BACKUP" ]] || [[ ! -f "$LEGACY_BACKUP" ]]; }; then
      echo "Loaded $LEGACY_LABEL has neither a regular boot plist nor recovery backup" >&2
      exit 1
    fi
  fi
  for legacy_path in "$LEGACY_DEST" "$LEGACY_BACKUP" "$LEGACY_MARKER"; do
    if [[ -e "$legacy_path" && ( -L "$legacy_path" || ! -f "$legacy_path" ) ]]; then
      echo "Unsafe legacy WHOOP migration entry: $legacy_path" >&2
      exit 1
    fi
  done
  if [[ -f "$LEGACY_DEST" || -f "$LEGACY_BACKUP" || "$LEGACY_WAS_LOADED" -eq 1 ]]; then
    LEGACY_SOURCE_PRESENT=1
  fi
fi

mkdir -p "$INDEX_DIR"
if ! (
  pinned_interpreter="$PYTHON_BIN"
  for name in "${!PYTHON@}"; do unset "$name"; done
  export PYTHONSAFEPATH=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
  exec "$pinned_interpreter" -P "$PRIVATE_FILE_HELPER" \
    --path "$LOG_PATH" --path "$ERR_PATH"
) >/dev/null; then
  echo "Operational log setup failed closed" >&2
  exit 1
fi
chmod 600 "$DATA_ROOT/.env"
if (( MIGRATION_ENABLED )); then
  for legacy_log_path in "$INDEX_DIR/whoop-body-sync.log" "$INDEX_DIR/whoop-body-sync.err"; do
    if [[ -e "$legacy_log_path" ]]; then
      if [[ -L "$legacy_log_path" || ! -f "$legacy_log_path" ]]; then
        echo "Existing legacy body log is not a regular file: $legacy_log_path" >&2
        exit 1
      fi
      chmod 600 "$legacy_log_path"
    fi
  done
fi
if [[ "$SERVICE" == "daily" && -e "$DATA_ROOT/ui/web/data.local.json" ]]; then
  if [[ -L "$DATA_ROOT/ui/web/data.local.json" || ! -f "$DATA_ROOT/ui/web/data.local.json" ]]; then
    echo "Existing dashboard data output is not a regular file" >&2
    exit 1
  fi
  chmod 600 "$DATA_ROOT/ui/web/data.local.json"
fi
for token_path in "$INDEX_DIR/whoop_tokens.json" "$INDEX_DIR/oura_tokens.json"; do
  if [[ -f "$token_path" ]]; then chmod 600 "$token_path"; fi
done

if [[ -e "$DEST" ]]; then
  HAD_DEST=1
  PRIOR_PLIST_BACKUP="$(run_migration_helper snapshot \
    --source "$DEST" --service-label "$LABEL")"
  if [[ "$PRIOR_PLIST_BACKUP" != "$LAUNCH_AGENTS_DIR"/* ]] ||
    [[ -L "$PRIOR_PLIST_BACKUP" || ! -f "$PRIOR_PLIST_BACKUP" ]]; then
    echo "Could not create a durable prior plist snapshot" >&2
    exit 1
  fi
fi

STAGED_PLIST="$(mktemp "$LAUNCH_AGENTS_DIR/.${LABEL}.plist.XXXXXX")"
render_plist "$STAGED_PLIST"

rollback_service() {
  if ! guarded_bootout "$LABEL"; then
    echo "CRITICAL: refusing to kill an active $LABEL during rollback" >&2
    return 1
  fi
  if ! wait_for_service_absent "$SERVICE_TARGET"; then
    echo "CRITICAL: failed $LABEL remained registered; prior plist backup retained" >&2
    return 1
  fi
  if (( HAD_DEST )); then
    if [[ -z "$PRIOR_PLIST_BACKUP" || ! -f "$PRIOR_PLIST_BACKUP" ]]; then return 1; fi
    if [[ "$(run_migration_helper publish \
      --source "$PRIOR_PLIST_BACKUP" --service-label "$LABEL")" != "published" ]]; then
      echo "CRITICAL: could not restore prior $LABEL plist" >&2
      return 1
    fi
    PRIOR_PLIST_BACKUP=""
  elif (( MIGRATION_ENABLED && MIGRATION_PREPARED && LEGACY_SOURCE_PRESENT )); then
    # A legacy-only prepare installs a validated daily bridge before retiring
    # the old label. Keep that single boot path for crash-safe reconciliation.
    if [[ -L "$DEST" || ! -f "$DEST" ]]; then return 1; fi
  elif [[ -e "$DEST" ]]; then
    local remove_status
    remove_status="$(run_migration_helper remove --service-label "$LABEL")" || return 1
    [[ "$remove_status" == "removed" || "$remove_status" == "absent" ]] || return 1
  fi
  if (( WAS_LOADED )) && [[ -f "$DEST" ]]; then
    if ! "$LAUNCHCTL_BIN" bootstrap "$LAUNCH_DOMAIN" "$DEST"; then return 1; fi
  fi
  DEPLOYMENT_ACTIVE=0
}

reconcile_daily_migration_after_failure() {
  local prior_daily_available=0
  if (( HAD_DEST )) || "$LAUNCHCTL_BIN" print "$SERVICE_TARGET" >/dev/null 2>&1; then
    prior_daily_available=1
  fi
  if (( prior_daily_available || ! LEGACY_SOURCE_PRESENT )); then
    if ! guarded_bootout "$LEGACY_LABEL"; then
      echo "CRITICAL: refusing to kill an active legacy WHOOP sync during reconciliation" >&2
      return 1
    fi
    if ! wait_for_service_absent "$LEGACY_SERVICE_TARGET" || [[ -e "$LEGACY_DEST" ]]; then
      echo "CRITICAL: legacy WHOOP body schedule survived failed migration" >&2
      return 1
    fi
    [[ "$(run_migration_helper complete)" == "complete" ]] || return 1
  else
    [[ "$(run_migration_helper restore \
      --launchctl-bin "$LAUNCHCTL_BIN" \
      --launch-domain "$LAUNCH_DOMAIN")" == "restored" ]] || return 1
    if (( LEGACY_WAS_LOADED )); then
      "$LAUNCHCTL_BIN" bootstrap "$LAUNCH_DOMAIN" "$LEGACY_DEST" || return 1
    fi
  fi
  MIGRATION_PREPARED=0
}

if (( MIGRATION_ENABLED )); then
  migration_args=(
    --source "$STAGED_PLIST"
    --launchctl-bin "$LAUNCHCTL_BIN"
    --launch-domain "$LAUNCH_DOMAIN"
    --sync-lock "$SYNC_LOCK_PATH"
  )
  if (( LEGACY_WAS_LOADED )); then migration_args+=(--legacy-loaded); fi
  migration_status="$(run_migration_helper prepare "${migration_args[@]}")"
  case "$migration_status" in
    stashed|restashed|already_stashed|no_legacy) ;;
    *) echo "Invalid legacy WHOOP migration status" >&2; exit 1 ;;
  esac
  MIGRATION_PREPARED=1
  if [[ "$migration_status" != "no_legacy" ]]; then LEGACY_SOURCE_PRESENT=1; fi
  if [[ -e "$LEGACY_DEST" ]] || "$LAUNCHCTL_BIN" print "$LEGACY_SERVICE_TARGET" >/dev/null 2>&1; then
    echo "Legacy WHOOP body schedule was not retired before daily replacement" >&2
    exit 1
  fi
fi

if (( WAS_LOADED )); then
  guarded_bootout "$LABEL" || exit 1
  if ! wait_for_service_absent "$SERVICE_TARGET"; then exit 1; fi
fi
DEPLOYMENT_ACTIVE=1

[[ "$(run_migration_helper publish \
  --source "$STAGED_PLIST" --service-label "$LABEL")" == "published" ]] || exit 1
STAGED_PLIST=""
"$LAUNCHCTL_BIN" bootstrap "$LAUNCH_DOMAIN" "$DEST" || exit 1
"$LAUNCHCTL_BIN" print "$SERVICE_TARGET" >/dev/null 2>&1 || exit 1

if (( MIGRATION_ENABLED )); then
  if [[ -e "$LEGACY_DEST" ]] || "$LAUNCHCTL_BIN" print "$LEGACY_SERVICE_TARGET" >/dev/null 2>&1; then exit 1; fi
  [[ "$(run_migration_helper complete)" == "complete" ]] || exit 1
  if [[ -e "$LEGACY_DEST" ]] || "$LAUNCHCTL_BIN" print "$LEGACY_SERVICE_TARGET" >/dev/null 2>&1; then exit 1; fi
  MIGRATION_COMMITTED=1
  MIGRATION_PREPARED=0
fi
DEPLOYMENT_ACTIVE=0

echo "Loaded $DEST"
echo "$SCHEDULE_DESCRIPTION"
echo "Runtime: $RUNTIME_ROOT ($RUNTIME_REVISION)"
echo "Data: $DATA_ROOT"
echo "Logs: $LOG_PATH"
if (( MIGRATION_ENABLED && LEGACY_SOURCE_PRESENT )); then
  echo "Retired: $LEGACY_SERVICE_TARGET (recovery backup and legacy logs preserved)"
fi
echo "Stop: launchctl bootout $SERVICE_TARGET"
