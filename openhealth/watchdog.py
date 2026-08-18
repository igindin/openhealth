"""Privacy-safe operational watcher for WHOOP refresh incidents.

WHOOP refresh credentials rotate on use.  When a refresh POST has an
ambiguous outcome, :mod:`openhealth.whoop` records a durable fail-closed marker
instead of replaying a token that may already have been consumed.  This module
turns that local marker into a prompt alert without reading or exposing health
values, credentials, provider bodies, URLs, or exception text.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from .config import build_paths
from .whoop import (
    REFRESH_STATE_FORMAT,
    _exclusive_file_lock,
    load_whoop_refresh_gate_proof,
)


WATCHDOG_STATE_FORMAT = "openhealth-whoop-refresh-watchdog-v1"
DEFAULT_IN_FLIGHT_GRACE_SECONDS = 90
SAFE_CAUSE_CODES = frozenset(
    {
        "http_uncertain",
        "transport_timeout",
        "protocol_incomplete",
        "invalid_json",
        "missing_successor",
        "unusable_successor",
        "provider_rejected",
        "transport_failure",
    }
)
SAFE_PROVIDER_ERROR_CODES = frozenset(
    {
        "access_denied",
        "insufficient_scope",
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "invalid_scope",
        "invalid_token",
        "server_error",
        "temporarily_unavailable",
        "unauthorized_client",
        "unsupported_grant_type",
    }
)


@dataclass(frozen=True)
class RefreshIncident:
    """A sanitized, stable incident derived from the owner-only marker."""

    key: str
    state: str
    recorded_at: Optional[str]
    cause_code: str
    http_status: Optional[int] = None
    provider_error_code: Optional[str] = None
    verification_required: bool = False


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_http_status(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 100 <= value <= 599 else None


def _invalid_incident(marker_path: Path) -> RefreshIncident:
    try:
        version = marker_path.lstat().st_mtime_ns
    except OSError:
        version = 0
    return RefreshIncident(
        key="invalid_refresh_state|%s" % version,
        state="invalid_refresh_state",
        recorded_at=None,
        cause_code="invalid_refresh_state",
    )


def _read_private_json_object(path: Path) -> Optional[dict[str, Any]]:
    """Read a private regular file without following a replacement symlink."""

    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            return None
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return payload if isinstance(payload, dict) else None


def read_refresh_incident(
    marker_path: Path,
    *,
    now: Optional[datetime] = None,
    in_flight_grace_seconds: int = DEFAULT_IN_FLIGHT_GRACE_SECONDS,
) -> Optional[RefreshIncident]:
    """Return one sanitized incident, or ``None`` for no actionable marker."""

    try:
        marker_path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return _invalid_incident(marker_path)
    payload = _read_private_json_object(marker_path)
    if payload is None:
        # A successful gate removes this file atomically and WatchPaths wakes
        # on that deletion. If it vanished between lstat and open, absence is
        # recovery state rather than a corrupt-marker incident.
        try:
            marker_path.lstat()
        except FileNotFoundError:
            return None
        except OSError:
            return _invalid_incident(marker_path)
        return _invalid_incident(marker_path)
    if payload.get("format") != REFRESH_STATE_FORMAT:
        return _invalid_incident(marker_path)

    state = payload.get("state")
    recorded = _parse_datetime(payload.get("recorded_at"))
    if state not in {
        "refresh_in_flight",
        "outcome_uncertain",
        "reauthorization_required",
        "successor_verification_pending",
    } or recorded is None:
        return _invalid_incident(marker_path)

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if state in {"refresh_in_flight", "successor_verification_pending"}:
        if current - recorded <= timedelta(seconds=max(0, in_flight_grace_seconds)):
            return None
        cause_code = (
            "refresh_interrupted"
            if state == "refresh_in_flight"
            else "successor_verification_pending"
        )
    else:
        candidate = payload.get("cause_code")
        cause_code = candidate if candidate in SAFE_CAUSE_CODES else "unknown"
        if state == "reauthorization_required" and cause_code == "unknown":
            cause_code = "provider_rejected"

    provider_error = payload.get("provider_error_code")
    if provider_error not in SAFE_PROVIDER_ERROR_CODES:
        provider_error = None
    recorded_at = recorded.isoformat()
    return RefreshIncident(
        key="%s|%s" % (state, recorded_at),
        state=state,
        recorded_at=recorded_at,
        cause_code=cause_code,
        http_status=_safe_http_status(payload.get("http_status")),
        provider_error_code=provider_error,
        verification_required=(
            state == "refresh_in_flight"
            and payload.get("verification_required") is True
        ),
    )


def render_incident_alert(incident: RefreshIncident) -> str:
    """Render fixed Russian copy without interpolating unsafe provider text."""

    details = {
        "http_uncertain": "неоднозначный HTTP-ответ при ротации токена",
        "transport_timeout": "таймаут при ротации токена",
        "protocol_incomplete": "оборванный ответ при ротации токена",
        "invalid_json": "некорректный ответ при ротации токена",
        "missing_successor": "WHOOP не вернул новый refresh-токен",
        "unusable_successor": "WHOOP вернул непригодную новую пару токенов",
        "provider_rejected": "WHOOP отклонил refresh-токен",
        "transport_failure": "сбой транспорта при ротации токена",
        "refresh_interrupted": "процесс ротации токена не завершился локально",
        "successor_verification_pending": (
            "новая пара токенов сохранена, но проверочный запрос не завершён"
        ),
        "invalid_refresh_state": "локальный маркер ротации повреждён или неизвестен",
        "unknown": "результат ротации токена неоднозначен",
    }
    detail = details.get(incident.cause_code, details["unknown"])
    safe_suffixes = []
    if incident.http_status is not None:
        safe_suffixes.append("HTTP %d" % incident.http_status)
    if incident.provider_error_code is not None:
        safe_suffixes.append(incident.provider_error_code)
    suffix = " (%s)" % ", ".join(safe_suffixes) if safe_suffixes else ""
    if incident.state == "successor_verification_pending":
        action = (
            "Импорт пока заблокирован; повторите проверку ротации — новый "
            "refresh-запрос отправлен не будет."
        )
    elif incident.state == "refresh_in_flight":
        if incident.verification_required:
            action = (
                "Повторите проверку ротации: если новая пара уже сохранена, "
                "будет повторён только GET без нового refresh-запроса."
            )
        else:
            action = (
                "Запустите безопасную проверку синхронизации: если новая пара "
                "успела сохраниться, OAuth не понадобится."
            )
    elif incident.state == "invalid_refresh_state":
        action = (
            "Автозапуск остановлен; проверьте локальное состояние WHOOP и не "
            "удаляйте маркер вручную."
        )
    else:
        action = "Автоповтор заблокирован безопасно; нужна новая авторизация WHOOP."
    return "⚠️ WHOOP остановлен: %s%s.\n%s" % (detail, suffix, action)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically create an owner-only JSON file, including its first write."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".pending.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    complete = False
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(dict(payload), handle, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
        _fsync_parent(path)
        complete = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path.exists() and not complete:
            temporary_path.unlink()


def send_telegram_alert(
    text: str,
    *,
    token_path: Path,
    chat_id: str,
    timeout_seconds: int = 30,
) -> bool:
    """Send one alert and report confirmed Telegram JSON success."""

    descriptor = -1
    try:
        descriptor = os.open(token_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or stat.S_IMODE(details.st_mode) & 0o077
        ):
            return False
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            token = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not token or not str(chat_id).strip():
        return False
    body = urllib.parse.urlencode({"chat_id": str(chat_id), "text": text}).encode()
    try:
        with urllib.request.urlopen(
            "https://api.telegram.org/bot%s/sendMessage" % token,
            body,
            timeout=timeout_seconds,
        ) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read(4096).decode("utf-8"))
            return isinstance(payload, dict) and payload.get("ok") is True
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False


def _verified_recovery(
    *, token_path: Path, sync_state_path: Path, recorded_at: Any
) -> Optional[str]:
    """Return gate time only when rotation and a strictly later import are proven."""

    incident_time = _parse_datetime(recorded_at)
    if incident_time is None:
        return None
    gate_proof = load_whoop_refresh_gate_proof(token_path)
    gate_time = _parse_datetime(
        gate_proof.get("verified_at") if gate_proof is not None else None
    )
    if gate_time is None or gate_time <= incident_time:
        return None
    sync_time = _parse_datetime(
        _load_json_object(sync_state_path).get("last_successful_sync")
    )
    if sync_time is None or sync_time <= gate_time:
        return None
    return gate_time.isoformat()


def run_once(
    *,
    repo_root: Path,
    sender: Callable[[str], bool],
    now: Optional[datetime] = None,
    state_path: Optional[Path] = None,
) -> int:
    """Evaluate once: 0 healthy/deduped, 1 alert delivered, 2 send failed."""

    paths = build_paths(repo_root)
    marker_path = paths.whoop_tokens_path.with_name(
        paths.whoop_tokens_path.name + ".refresh-state"
    )
    watchdog_state_path = state_path or (
        paths.data_index / "whoop_refresh_watchdog_state.json"
    )
    lock_path = watchdog_state_path.with_name(watchdog_state_path.name + ".lock")
    with _exclusive_file_lock(lock_path):
        return _run_once_unlocked(
            paths=paths,
            marker_path=marker_path,
            watchdog_state_path=watchdog_state_path,
            sender=sender,
            now=now,
        )


def _run_once_unlocked(
    *,
    paths: Any,
    marker_path: Path,
    watchdog_state_path: Path,
    sender: Callable[[str], bool],
    now: Optional[datetime],
) -> int:
    state = _load_json_object(watchdog_state_path)
    if state.get("format") != WATCHDOG_STATE_FORMAT:
        state = {}
    incident = read_refresh_incident(marker_path, now=now)

    if incident is not None:
        if state.get("last_alerted_incident") == incident.key:
            return 0
        if not sender(render_incident_alert(incident)):
            return 2
        detected_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        atomic_write_private_json(
            watchdog_state_path,
            {
                "format": WATCHDOG_STATE_FORMAT,
                "active": True,
                "last_alerted_incident": incident.key,
                "incident_recorded_at": incident.recorded_at or detected_at.isoformat(),
            },
        )
        return 1

    # ``None`` can also mean a healthy recent in-flight marker. Marker absence
    # is a distinct recovery condition; lstat also catches broken symlinks.
    try:
        marker_path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        return 0
    else:
        return 0

    if state.get("active"):
        gate_verified_at = _verified_recovery(
            token_path=paths.whoop_tokens_path,
            sync_state_path=paths.whoop_sync_state_path,
            recorded_at=state.get("incident_recorded_at"),
        )
        if gate_verified_at is None:
            return 0
        if not sender("✅ WHOOP снова синхронизируется; ротация токена и импорт проверены."):
            return 2
        atomic_write_private_json(
            watchdog_state_path,
            {
                "format": WATCHDOG_STATE_FORMAT,
                "active": False,
                "last_alerted_incident": state.get("last_alerted_incident"),
                "incident_recorded_at": state.get("incident_recorded_at"),
                "gate_verified_at": gate_verified_at,
                "recovered_after_sync": True,
            },
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m openhealth.watchdog",
        description="Alert once for a durable WHOOP refresh incident.",
    )
    parser.add_argument("--repo-root", required=True)
    parser.add_argument(
        "--config-dir",
        default=os.getenv("OPENHEALTH_CONFIG_DIR", str(Path.home() / ".openhealth")),
    )
    parser.add_argument(
        "--chat-id",
        default=os.getenv("OPENHEALTH_TELEGRAM_ALERT_CHAT_ID", ""),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_dir = Path(args.config_dir).resolve()

    def sender(text: str) -> bool:
        return send_telegram_alert(
            text,
            token_path=config_dir / "telegram.token",
            chat_id=args.chat_id,
        )

    result = run_once(repo_root=Path(args.repo_root).resolve(), sender=sender)
    if result == 2:
        print("WHOOP refresh alert delivery failed; it will be retried.")
    elif result == 1:
        print("WHOOP refresh incident alert delivered.")
    else:
        print("WHOOP refresh watcher ok.")
    # A delivered alert is successful watcher operation. Reserve a failing
    # launchd status for delivery failure, which should retry next interval.
    return 1 if result == 2 else 0


if __name__ == "__main__":
    raise SystemExit(main())
