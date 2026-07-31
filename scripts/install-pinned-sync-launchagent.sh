#!/usr/bin/env bash
# Render and install a pinned OpenHealth sync LaunchAgent.
set -euo pipefail
umask 077

usage() {
  cat >&2 <<'EOF'
Usage: install-pinned-sync-launchagent.sh --service SERVICE \
  --runtime-root PATH --data-root PATH --revision SHA [options]

Services: whoop-body, daily
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
    --service)
      SERVICE="${2:-}"
      shift 2
      ;;
    --runtime-root)
      RUNTIME_ROOT="${2:-}"
      shift 2
      ;;
    --data-root)
      DATA_ROOT="${2:-}"
      shift 2
      ;;
    --revision)
      RUNTIME_REVISION="${2:-}"
      shift 2
      ;;
    --python-bin)
      PYTHON_BIN="${2:-}"
      shift 2
      ;;
    --render-only)
      RENDER_ONLY="${2:-}"
      shift 2
      ;;
    --launch-agents-dir)
      LAUNCH_AGENTS_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$SERVICE" || -z "$RUNTIME_ROOT" || -z "$DATA_ROOT" || -z "$RUNTIME_REVISION" ]]; then
  usage
  exit 2
fi
if [[ "$RUNTIME_ROOT" != /* || "$DATA_ROOT" != /* ]]; then
  echo "Runtime and data roots must be absolute paths" >&2
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
  for name in "${!PYTHON@}"; do
    unset "$name"
  done
  export PYTHONSAFEPATH=1
  export PYTHONNOUSERSITE=1
  export PYTHONDONTWRITEBYTECODE=1
  exec "$pinned_interpreter" -P "$RUNTIME_VERIFIER" verify \
    --release "$RUNTIME_ROOT" \
    --revision "$RUNTIME_REVISION"
) >/dev/null; then
  echo "Pinned runtime manifest verification failed" >&2
  exit 1
fi

case "$SERVICE" in
  whoop-body)
    LABEL="org.openhealth.whoop-body-sync"
    PLIST_NAME="whoop-body-sync.plist"
    RUNNER_NAME="whoop-body-sync-run.sh"
    LOG_NAME="whoop-body-sync.log"
    ERR_NAME="whoop-body-sync.err"
    SCHEDULE_DESCRIPTION="WHOOP body measurements will be captured daily at 12:15 local time and at login."
    ;;
  daily)
    LABEL="com.openhealth.daily-sync"
    PLIST_NAME="daily-sync.plist"
    RUNNER_NAME="daily-sync-run.sh"
    LOG_NAME="daily-sync.log"
    ERR_NAME="daily-sync.log"
    SCHEDULE_DESCRIPTION="WHOOP/Oura sync will run daily at 09:00, 14:00, and 21:00 local time."
    ;;
  *)
    echo "Unsupported service: $SERVICE" >&2
    exit 2
    ;;
esac

PLIST_SRC="$RUNTIME_ROOT/scripts/$PLIST_NAME"
RUNNER="$RUNTIME_ROOT/scripts/$RUNNER_NAME"
if [[ ! -f "$PLIST_SRC" || ! -x "$RUNNER" ]]; then
  echo "Pinned runtime is missing $PLIST_NAME or executable $RUNNER_NAME" >&2
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
DEPLOYMENT_ACTIVE=0
cleanup() {
  local status="$?"
  local rollback_ok=1
  trap - EXIT HUP INT TERM
  if (( DEPLOYMENT_ACTIVE )) && declare -F rollback_service >/dev/null; then
    if ! rollback_service; then
      status=1
      rollback_ok=0
    fi
  fi
  if [[ -n "$STAGED_PLIST" && -e "$STAGED_PLIST" ]]; then
    rm -f -- "$STAGED_PLIST"
  fi
  if [[ -n "$RESTORE_PLIST" && -e "$RESTORE_PLIST" ]]; then
    rm -f -- "$RESTORE_PLIST"
  fi
  if [[ -n "$PRIOR_PLIST_BACKUP" && -e "$PRIOR_PLIST_BACKUP" ]]; then
    if (( rollback_ok )); then
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
  # Swift plutil treats a numeric `-replace` key path as an array insertion.
  # Remove the template placeholder first so rendering cannot retain an extra
  # untrusted argument.
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
  if [[ "$RENDER_ONLY" != /* ]]; then
    echo "Render destination must be an absolute path" >&2
    exit 2
  fi
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

INDEX_DIR="$DATA_ROOT/data/index"
LOG_PATH="$INDEX_DIR/$LOG_NAME"
ERR_PATH="$INDEX_DIR/$ERR_NAME"
DEST="$LAUNCH_AGENTS_DIR/$LABEL.plist"
SERVICE_TARGET="$LAUNCH_DOMAIN/$LABEL"
WAS_LOADED=0
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

mkdir -p "$LAUNCH_AGENTS_DIR" "$INDEX_DIR"
touch "$LOG_PATH" "$ERR_PATH"
chmod 600 "$LOG_PATH" "$ERR_PATH"
chmod 600 "$DATA_ROOT/.env"
if [[ "$SERVICE" == "daily" && -e "$DATA_ROOT/ui/web/data.local.json" ]]; then
  if [[ -L "$DATA_ROOT/ui/web/data.local.json" || ! -f "$DATA_ROOT/ui/web/data.local.json" ]]; then
    echo "Existing dashboard data output is not a regular file" >&2
    exit 1
  fi
  chmod 600 "$DATA_ROOT/ui/web/data.local.json"
fi
for token_path in "$INDEX_DIR/whoop_tokens.json" "$INDEX_DIR/oura_tokens.json"; do
  if [[ -f "$token_path" ]]; then
    chmod 600 "$token_path"
  fi
done

if [[ -e "$DEST" ]]; then
  if [[ -L "$DEST" || ! -f "$DEST" ]]; then
    echo "Existing LaunchAgent plist is not a regular file: $DEST" >&2
    exit 1
  fi
  HAD_DEST=1
  PRIOR_PLIST_BACKUP="$(mktemp "$LAUNCH_AGENTS_DIR/.${LABEL}.prior.XXXXXX")"
  cp "$DEST" "$PRIOR_PLIST_BACKUP"
  chmod 600 "$PRIOR_PLIST_BACKUP"
  if ! cmp -s "$DEST" "$PRIOR_PLIST_BACKUP"; then
    echo "Could not verify the prior LaunchAgent backup" >&2
    exit 1
  fi
fi

STAGED_PLIST="$(mktemp "$LAUNCH_AGENTS_DIR/.${LABEL}.plist.XXXXXX")"
render_plist "$STAGED_PLIST"

wait_for_service_absent() {
  local attempt
  for attempt in {1..50}; do
    if ! "$LAUNCHCTL_BIN" print "$SERVICE_TARGET" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.1
  done
  return 1
}

rollback_service() {
  "$LAUNCHCTL_BIN" bootout "$SERVICE_TARGET" >/dev/null 2>&1 || true
  if ! wait_for_service_absent; then
    echo "CRITICAL: failed $LABEL remained registered; prior plist backup is retained" >&2
    return 1
  fi
  if (( HAD_DEST )); then
    if [[ -z "$PRIOR_PLIST_BACKUP" || ! -f "$PRIOR_PLIST_BACKUP" ]]; then
      echo "CRITICAL: prior $LABEL plist backup is unavailable" >&2
      return 1
    fi
    if ! RESTORE_PLIST="$(mktemp "$LAUNCH_AGENTS_DIR/.${LABEL}.restore.XXXXXX")"; then
      echo "CRITICAL: could not stage prior $LABEL plist restore" >&2
      return 1
    fi
    if ! cp "$PRIOR_PLIST_BACKUP" "$RESTORE_PLIST" || ! chmod 600 "$RESTORE_PLIST"; then
      echo "CRITICAL: could not copy prior $LABEL plist restore" >&2
      return 1
    fi
    if ! cmp -s "$PRIOR_PLIST_BACKUP" "$RESTORE_PLIST"; then
      echo "CRITICAL: prior $LABEL plist restore copy is invalid" >&2
      return 1
    fi
    if ! mv -f "$RESTORE_PLIST" "$DEST"; then
      echo "CRITICAL: could not restore prior $LABEL plist" >&2
      return 1
    fi
    RESTORE_PLIST=""
  elif [[ -e "$DEST" ]]; then
    if ! rm -f -- "$DEST"; then
      echo "CRITICAL: could not remove failed new $LABEL plist" >&2
      return 1
    fi
  fi
  if (( WAS_LOADED )) && [[ -f "$DEST" ]]; then
    if ! "$LAUNCHCTL_BIN" bootstrap "$LAUNCH_DOMAIN" "$DEST"; then
      echo "CRITICAL: replacement failed and prior $LABEL could not be restarted" >&2
      return 1
    fi
  fi
  DEPLOYMENT_ACTIVE=0
}

if (( WAS_LOADED )); then
  DEPLOYMENT_ACTIVE=1
  if ! "$LAUNCHCTL_BIN" bootout "$SERVICE_TARGET"; then
    echo "Could not stop the existing $LABEL; prior service remains authoritative" >&2
    exit 1
  fi
  if ! wait_for_service_absent; then
    echo "Existing $LABEL remained registered after bootout; restoring prior service" >&2
    exit 1
  fi
else
  DEPLOYMENT_ACTIVE=1
fi

if ! mv -f "$STAGED_PLIST" "$DEST"; then
  echo "Replacement plist could not be promoted; restoring prior service" >&2
  exit 1
fi
STAGED_PLIST=""
if ! "$LAUNCHCTL_BIN" bootstrap "$LAUNCH_DOMAIN" "$DEST"; then
  echo "Replacement $LABEL failed to bootstrap; restoring prior service" >&2
  exit 1
fi
if ! "$LAUNCHCTL_BIN" print "$SERVICE_TARGET" >/dev/null 2>&1; then
  echo "Replacement $LABEL did not remain loaded; restoring prior service" >&2
  exit 1
fi
DEPLOYMENT_ACTIVE=0

echo "Loaded $DEST"
echo "$SCHEDULE_DESCRIPTION"
echo "Runtime: $RUNTIME_ROOT ($RUNTIME_REVISION)"
echo "Data: $DATA_ROOT"
echo "Logs: $LOG_PATH"
echo "Stop: launchctl bootout $SERVICE_TARGET"
