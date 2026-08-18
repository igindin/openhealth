import base64
import errno
import hashlib
import hmac
import json
import os
import socket
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from http.client import HTTPException
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from . import index
from .contexts import build_source_brief, refresh_contexts
from .models import ArtifactManifest, ContextNote, Observation, SourceManifest, TimelineEvent
from .storage import ensure_repo_structure, now_utc, sha256sum, slugify, write_json, write_text

if os.name == "nt":
    import msvcrt as _file_locking
else:
    import fcntl as _file_locking

AUTHORIZATION_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE_URL = "https://api.prod.whoop.com/developer/v2"
WHOOP_SOURCE_ID = "whoop-live"
HTTP_TIMEOUT_SECONDS = 30
CURL_CONNECT_TIMEOUT_SECONDS = 10
CURL_MAX_TIME_SECONDS = 30
CURL_PROCESS_TIMEOUT_SECONDS = CURL_MAX_TIME_SECONDS + 5
TOKEN_TRANSACTION_FORMAT = "openhealth-whoop-token-transaction-v1"
REFRESH_STATE_FORMAT = "openhealth-whoop-refresh-state-v1"
REFRESH_GATE_FORMAT = "openhealth-whoop-refresh-gate-v1"
SAFE_HTTP_ERROR_CODES = {
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
SAFE_REFRESH_CAUSE_CODES = {
    "http_uncertain",
    "invalid_json",
    "missing_successor",
    "protocol_incomplete",
    "provider_rejected",
    "transport_failure",
    "transport_timeout",
    "unusable_successor",
}
PROBE_FALLBACK_HTTP_STATUSES = {400, 403, 404, 405, 406, 409, 410, 415, 422}
PROBE_NON_FALLBACK_ERROR_CODES = {
    "access_denied",
    "insufficient_scope",
    "invalid_client",
    "invalid_token",
    "unauthorized_client",
}
# WHOOP sits behind Cloudflare, which bans the default urllib User-Agent
# (Error 1010 "browser_signature_banned"). Send a browser-like UA so OAuth
# token exchange and API calls are not blocked at the WAF.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
DEFAULT_SCOPES = (
    "read:profile",
    "read:recovery",
    "read:cycles",
    "read:sleep",
    "read:workout",
    "read:body_measurement",
    "offline",
)
CAPABILITIES = {
    "source": "WHOOP API v2",
    "collections": {
        "cycles": {
            "scope": "read:cycles",
            "description": "Daily cycle windows and strain-centric summary data.",
            "endpoint": "/cycle",
        },
        "recovery": {
            "scope": "read:recovery",
            "description": "Recovery score, HRV, resting heart rate, skin temp, and related recovery metrics.",
            "endpoint": "/recovery",
        },
        "sleep": {
            "scope": "read:sleep",
            "description": "Sleep windows and stage-level performance metrics.",
            "endpoint": "/activity/sleep",
        },
        "workout": {
            "scope": "read:workout",
            "description": "Workout sessions with sport identifiers, strain, and energy metrics.",
            "endpoint": "/activity/workout",
        },
        "profile": {
            "scope": "read:profile",
            "description": "Basic user profile information.",
            "endpoint": "/user/profile/basic",
        },
        "body_measurement": {
            "scope": "read:body_measurement",
            "description": "Body measurements such as height, weight, and max heart rate when available.",
            "endpoint": "/user/measurement/body",
        },
    },
    "not_available_in_public_api": [
        "journal / behaviors",
        "custom notes",
        "meal logs",
    ],
}


@dataclass
class WhoopCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: Tuple[str, ...] = DEFAULT_SCOPES


class WhoopApiError(RuntimeError):
    """Raised when WHOOP returns an unexpected response."""


class WhoopRequestError(WhoopApiError):
    """A sanitized WHOOP HTTP/transport failure with retry-safety metadata."""

    def __init__(
        self,
        message: str,
        *,
        http_status: Optional[int] = None,
        provider_error_code: Optional[str] = None,
        outcome_uncertain: bool = False,
        cause_code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.http_status = _safe_http_status(http_status)
        self.provider_error_code = _safe_provider_error_code(provider_error_code)
        self.outcome_uncertain = outcome_uncertain
        self.cause_code = _safe_refresh_cause_code(cause_code)


class _WhoopRefreshFailure(WhoopApiError):
    """Refresh failure carrying only fixed, privacy-safe diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        cause_code: Optional[str] = None,
        http_status: Optional[int] = None,
        provider_error_code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.cause_code = _safe_refresh_cause_code(cause_code)
        self.http_status = _safe_http_status(http_status)
        self.provider_error_code = _safe_provider_error_code(provider_error_code)


class WhoopRefreshOutcomeUncertain(_WhoopRefreshFailure):
    """The provider may have rotated the token without returning its successor."""


class WhoopRefreshRejected(_WhoopRefreshFailure):
    """The provider definitively rejected the current refresh credential."""


def _safe_refresh_cause_code(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value in SAFE_REFRESH_CAUSE_CODES else None


def _safe_http_status(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 100 <= value <= 599 else None


def _safe_provider_error_code(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value in SAFE_HTTP_ERROR_CODES else None


def _transport_outcome_uncertain(reason: Any) -> bool:
    """Return false only for failures known to happen before request dispatch."""
    if isinstance(reason, socket.gaierror):
        return False
    if isinstance(reason, OSError) and reason.errno in {
        errno.ECONNREFUSED,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
    }:
        return False
    return True


def parse_scopes(raw: Optional[str]) -> Tuple[str, ...]:
    """Parse a comma- or space-separated scope string into a tuple.

    Empty / missing input yields an empty tuple so callers can fall back to
    ``DEFAULT_SCOPES``.
    """
    if not raw:
        return ()
    return tuple(token for token in raw.replace(",", " ").split() if token)


def load_credentials_from_env() -> WhoopCredentials:
    client_id = os.getenv("OPENHEALTH_WHOOP_CLIENT_ID")
    client_secret = os.getenv("OPENHEALTH_WHOOP_CLIENT_SECRET")
    redirect_uri = os.getenv("OPENHEALTH_WHOOP_REDIRECT_URI")
    missing = [
        name
        for name, value in (
            ("OPENHEALTH_WHOOP_CLIENT_ID", client_id),
            ("OPENHEALTH_WHOOP_CLIENT_SECRET", client_secret),
            ("OPENHEALTH_WHOOP_REDIRECT_URI", redirect_uri),
        )
        if not value
    ]
    if missing:
        raise WhoopApiError("Missing WHOOP credentials in environment: %s" % ", ".join(missing))
    # Not every WHOOP app is granted the full default scope set (e.g. apps
    # without read:profile / read:body_measurement). OPENHEALTH_WHOOP_SCOPES
    # lets the user request exactly the scopes their app allows and avoid an
    # invalid_scope error at the authorize step.
    scopes = parse_scopes(os.getenv("OPENHEALTH_WHOOP_SCOPES")) or DEFAULT_SCOPES
    return WhoopCredentials(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scopes=scopes,
    )


def build_authorization_url(credentials: WhoopCredentials, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": credentials.client_id,
            "redirect_uri": credentials.redirect_uri,
            "scope": " ".join(credentials.scopes),
            "state": state,
        }
    )
    return "%s?%s" % (AUTHORIZATION_URL, query)


def exchange_code_for_tokens(credentials: WhoopCredentials, code: str) -> Dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": credentials.redirect_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
    }
    response = _post_form(TOKEN_URL, payload)
    return _normalize_token_response(response)


def extract_code_from_redirect_url(redirect_url: str, expected_state: Optional[str] = None) -> Dict[str, Any]:
    parsed = urlparse(redirect_url.strip())
    query = parse_qs(parsed.query)
    code = _first_query_value(query, "code")
    state = _first_query_value(query, "state")
    error = _first_query_value(query, "error")
    error_description = _first_query_value(query, "error_description")
    if error:
        raise WhoopApiError("WHOOP returned an OAuth error: %s %s" % (error, error_description or ""))
    if not code:
        raise WhoopApiError("Redirect URL did not include an OAuth code.")
    if expected_state and state != expected_state:
        raise WhoopApiError("OAuth state mismatch. Expected %s, received %s." % (expected_state, state))
    return {"code": code, "state": state, "redirect_uri": parsed.geturl()}


def refresh_tokens(credentials: WhoopCredentials, refresh_token: str) -> Dict[str, Any]:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        # WHOOP requires this scope when rotating an access/refresh-token pair.
        "scope": "offline",
    }
    try:
        response = _post_form(TOKEN_URL, payload)
    except WhoopRequestError as exc:
        if exc.http_status in {400, 401, 403}:
            suffix = (
                " (%s)" % exc.provider_error_code
                if exc.provider_error_code
                else ""
            )
            raise WhoopRefreshRejected(
                "WHOOP rejected the refresh credential with HTTP %s%s."
                % (exc.http_status, suffix),
                cause_code="provider_rejected",
                http_status=exc.http_status,
                provider_error_code=exc.provider_error_code,
            ) from None
        if exc.outcome_uncertain:
            raise WhoopRefreshOutcomeUncertain(
                "WHOOP did not return a definitive refresh result; the provider "
                "may have rotated the credential without delivering its successor.",
                cause_code=exc.cause_code or "transport_failure",
                http_status=exc.http_status,
                provider_error_code=exc.provider_error_code,
            ) from None
        raise
    except json.JSONDecodeError:
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP did not return a definitive refresh result; the provider "
            "may have rotated the credential without delivering its successor.",
            cause_code="invalid_json",
        ) from None
    except (TimeoutError, subprocess.TimeoutExpired):
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP did not return a definitive refresh result; the provider "
            "may have rotated the credential without delivering its successor.",
            cause_code="transport_timeout",
        ) from None
    except UnicodeDecodeError:
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP did not return a definitive refresh result; the provider "
            "may have rotated the credential without delivering its successor.",
            cause_code="invalid_json",
        ) from None
    except (ConnectionError, subprocess.SubprocessError):
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP did not return a definitive refresh result; the provider "
            "may have rotated the credential without delivering its successor.",
            cause_code="transport_failure",
        ) from None
    except OSError as exc:
        if not _transport_outcome_uncertain(exc):
            raise WhoopRequestError(
                "WHOOP refresh failed before the request was dispatched.",
                outcome_uncertain=False,
                cause_code="transport_failure",
            ) from None
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP did not return a definitive refresh result; the provider "
            "may have rotated the credential without delivering its successor.",
            cause_code="transport_failure",
        ) from None

    if not isinstance(response, dict):
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP returned an unusable refresh response; the provider may have "
            "rotated the credential without delivering a usable successor.",
            cause_code="unusable_successor",
        )
    successor_refresh_token = response.get("refresh_token")
    if not isinstance(successor_refresh_token, str) or not successor_refresh_token.strip():
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP refresh response did not include a usable successor "
            "refresh credential.",
            cause_code="missing_successor",
        )
    if hmac.compare_digest(successor_refresh_token.strip(), refresh_token.strip()):
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP refresh response did not rotate the refresh credential.",
            cause_code="unusable_successor",
        )
    try:
        normalized = _normalize_token_response(response)
    except (
        KeyError,
        OverflowError,
        TypeError,
        ValueError,
    ):
        # A successful HTTP response with an unusable body may still have
        # consumed WHOOP's single-use refresh credential.
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP returned an unusable refresh response; the provider may have "
            "rotated the credential without delivering a usable successor.",
            cause_code="unusable_successor",
        ) from None
    successor_access_token = normalized.get("access_token")
    normalized_refresh_token = normalized.get("refresh_token")
    if (
        not isinstance(successor_access_token, str)
        or not successor_access_token.strip()
        or not isinstance(normalized_refresh_token, str)
        or not normalized_refresh_token.strip()
    ):
        raise WhoopRefreshOutcomeUncertain(
            "WHOOP returned an unusable refresh response; the provider may have "
            "rotated the credential without delivering a usable successor.",
            cause_code="unusable_successor",
        )
    return normalized


@contextmanager
def _token_file_lock(path: Path) -> Iterator[None]:
    """Serialize rotating-token reads and writes across local processes."""
    with _exclusive_file_lock(path.with_name(path.name + ".lock")):
        yield


@contextmanager
def _exclusive_file_lock(lock_path: Path) -> Iterator[None]:
    """Hold one owner-only advisory lock across processes and threads."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        _set_owner_only(lock_path, descriptor)
        _lock_descriptor(descriptor)
        locked = True
        yield
    finally:
        if locked:
            _unlock_descriptor(descriptor)
        os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    if os.name != "nt":
        _file_locking.flock(descriptor, _file_locking.LOCK_EX)
        return

    # msvcrt locks byte ranges, so make sure byte zero exists. LK_NBLCK plus
    # retry provides the blocking semantics of flock without its 10-second cap.
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
        os.fsync(descriptor)
    while True:
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            _file_locking.locking(descriptor, _file_locking.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            time.sleep(0.05)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name != "nt":
        _file_locking.flock(descriptor, _file_locking.LOCK_UN)
        return
    os.lseek(descriptor, 0, os.SEEK_SET)
    _file_locking.locking(descriptor, _file_locking.LK_UNLCK, 1)


def _set_owner_only(path: Path, descriptor: Optional[int] = None) -> None:
    if descriptor is not None and hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)
    else:
        os.chmod(path, 0o600)


def _fsync_parent_directory(path: Path) -> None:
    """Persist same-directory renames on platforms that support directory fsync."""
    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _token_scopes(payload: Dict[str, Any]) -> Set[str]:
    raw_scopes = payload.get("scope")
    if raw_scopes is None and isinstance(payload.get("raw"), dict):
        raw_scopes = payload["raw"].get("scope")
    if isinstance(raw_scopes, str):
        return set(parse_scopes(raw_scopes))
    if isinstance(raw_scopes, (list, tuple, set)):
        return {str(scope) for scope in raw_scopes if scope}
    return set()


def require_token_scopes(tokens: Dict[str, Any], required: Iterable[str], operation: str) -> None:
    required_set = set(required)
    missing = sorted(required_set - _token_scopes(tokens))
    if missing:
        raise WhoopApiError(
            "WHOOP token is missing required scopes for %s: %s. "
            "Reauthorize WHOOP with the required scopes."
            % (operation, ", ".join(missing))
        )
    if "offline" in required_set and not str(tokens.get("refresh_token") or "").strip():
        raise WhoopApiError(
            "WHOOP token has no refresh token for %s. "
            "Reauthorize WHOOP with the offline scope." % operation
        )


def _full_sync_required_scopes(
    include_profile: bool,
    include_body_measurements: bool,
) -> Set[str]:
    required = {
        "offline",
        "read:cycles",
        "read:recovery",
        "read:sleep",
        "read:workout",
    }
    if include_profile:
        required.add("read:profile")
    if include_body_measurements:
        required.add("read:body_measurement")
    return required


def _check_scope_reduction(
    path: Path,
    payload: Dict[str, Any],
    allow_scope_reduction: bool,
) -> None:
    if allow_scope_reduction or not path.exists():
        return
    removed = sorted(_token_scopes(load_tokens(path)) - _token_scopes(payload))
    if removed:
        raise WhoopApiError(
            "Refusing to replace the WHOOP token with narrower scopes; "
            "the new token omits: %s. Pass --allow-scope-reduction only "
            "when this downgrade is intentional." % ", ".join(removed)
        )


def _pending_token_path(path: Path) -> Path:
    return path.with_name(path.name + ".pending")


def _staged_token_paths(path: Path) -> List[Path]:
    return list(path.parent.glob(path.name + ".pending.*.tmp"))


def _promotion_token_paths(path: Path) -> List[Path]:
    return list(path.parent.glob(path.name + ".promote.*.tmp"))


def _token_recovery_paths(path: Path) -> List[Path]:
    return [
        candidate
        for candidate in [
            _pending_token_path(path),
            *_staged_token_paths(path),
            *_promotion_token_paths(path),
        ]
        if candidate.exists()
    ]


def _refresh_state_path(path: Path) -> Path:
    return path.with_name(path.name + ".refresh-state")


def _refresh_state_paths(path: Path) -> List[Path]:
    state_path = _refresh_state_path(path)
    return [
        candidate
        for candidate in [
            state_path,
            *path.parent.glob(state_path.name + ".pending.*.tmp"),
        ]
        if candidate.exists()
    ]


def whoop_refresh_gate_path(path: Path) -> Path:
    """Return the owner-only proof sidecar path used by the local watcher."""
    return path.with_name(path.name + ".refresh-gate")


def _refresh_gate_paths(path: Path) -> List[Path]:
    gate_path = whoop_refresh_gate_path(path)
    return [
        candidate
        for candidate in [
            gate_path,
            *path.parent.glob(gate_path.name + ".pending.*.tmp"),
        ]
        if candidate.exists()
    ]


def load_whoop_refresh_gate_proof(path: Path) -> Optional[Dict[str, str]]:
    """Read only the fixed, non-sensitive fields from a valid gate proof."""
    gate_path = whoop_refresh_gate_path(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    elif gate_path.is_symlink():
        return None
    descriptor = -1
    try:
        descriptor = os.open(gate_path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            return None
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    verified_at = payload.get("verified_at") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("format") != REFRESH_GATE_FORMAT
        or not isinstance(verified_at, str)
    ):
        return None
    try:
        parsed = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return {"format": REFRESH_GATE_FORMAT, "verified_at": verified_at}


def _write_refresh_gate_proof_unlocked(path: Path) -> Dict[str, str]:
    gate_path = whoop_refresh_gate_path(path)
    proof = {
        "format": REFRESH_GATE_FORMAT,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary_path = _write_token_file(
        path,
        proof,
        prefix=gate_path.name + ".pending.",
    )
    try:
        os.replace(temporary_path, gate_path)
        _fsync_parent_directory(gate_path)
        _set_owner_only(gate_path)
    except OSError as exc:
        preserved = gate_path if gate_path.exists() else temporary_path
        if preserved.exists():
            _set_owner_only(preserved)
        raise WhoopApiError(
            "Could not persist the WHOOP refresh-gate proof; scheduled syncs "
            "remain blocked."
        ) from exc
    stale = [candidate for candidate in _refresh_gate_paths(path) if candidate != gate_path]
    for candidate in stale:
        try:
            candidate.unlink()
        except OSError as exc:
            raise WhoopApiError(
                "WHOOP refresh-gate proof was saved, but stale proof state "
                "could not be cleaned; scheduled syncs remain blocked."
            ) from exc
    if stale:
        _fsync_parent_directory(gate_path)
    return proof


def _write_refresh_state_unlocked(
    path: Path,
    state: str,
    base_payload: Dict[str, Any],
    failure: Optional[Any] = None,
    verification_required: bool = False,
) -> None:
    if state not in {
        "refresh_in_flight",
        "outcome_uncertain",
        "reauthorization_required",
        "successor_verification_pending",
    }:
        raise ValueError("Unsupported WHOOP refresh state: %s" % state)
    state_path = _refresh_state_path(path)
    payload = {
        "format": REFRESH_STATE_FORMAT,
        "state": state,
        "base_fingerprint": _token_fingerprint(base_payload),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    if state == "refresh_in_flight" and verification_required is True:
        payload["verification_required"] = True
    if failure is not None:
        cause_code = _safe_refresh_cause_code(getattr(failure, "cause_code", None))
        http_status = _safe_http_status(getattr(failure, "http_status", None))
        provider_error_code = _safe_provider_error_code(
            getattr(failure, "provider_error_code", None)
        )
        if cause_code is not None:
            payload["cause_code"] = cause_code
        if http_status is not None:
            payload["http_status"] = http_status
        if provider_error_code is not None:
            payload["provider_error_code"] = provider_error_code
    temporary_path = _write_token_file(
        path,
        payload,
        prefix=state_path.name + ".pending.",
    )
    try:
        os.replace(temporary_path, state_path)
        _fsync_parent_directory(state_path)
        _set_owner_only(state_path)
    except OSError as exc:
        preserved = state_path if state_path.exists() else temporary_path
        if preserved.exists():
            _set_owner_only(preserved)
        raise WhoopApiError(
            "Could not promote the fail-closed WHOOP refresh state; it is "
            "preserved at %s. Stop scheduled WHOOP syncs before trying again."
            % preserved
        ) from exc


def _clear_refresh_state_unlocked(path: Path) -> None:
    removed = False
    for candidate in _refresh_state_paths(path):
        if candidate.exists():
            candidate.unlink()
            removed = True
    if removed:
        _fsync_parent_directory(path)


def _successor_verification_pending_unlocked(
    path: Path,
    active_payload: Dict[str, Any],
) -> bool:
    candidates = _refresh_state_paths(path)
    state_path = _refresh_state_path(path)
    if not candidates or not state_path.exists():
        return False
    if len(candidates) != 1:
        raise WhoopApiError(
            "WHOOP successor verification state is incomplete; automatic "
            "rotation remains blocked."
        )
    _set_owner_only(state_path)
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if payload.get("format") != REFRESH_STATE_FORMAT:
        return False
    state = payload.get("state")
    active_fingerprint = _token_fingerprint(active_payload)
    base_fingerprint = payload.get("base_fingerprint")
    if (
        state == "refresh_in_flight"
        and payload.get("verification_required") is True
        and isinstance(base_fingerprint, str)
        and base_fingerprint != active_fingerprint
    ):
        _write_refresh_state_unlocked(
            path,
            "successor_verification_pending",
            active_payload,
        )
        return True
    if state != "successor_verification_pending":
        return False
    if base_fingerprint != active_fingerprint:
        raise WhoopApiError(
            "WHOOP successor verification state does not match the active "
            "token; automatic rotation remains blocked."
        )
    return True


def _raise_if_refresh_blocked_unlocked(path: Path) -> None:
    candidates = _refresh_state_paths(path)
    if not candidates:
        return
    state_path = _refresh_state_path(path)
    candidate = state_path if state_path.exists() else candidates[0]
    _set_owner_only(candidate)
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    state = payload.get("state") if payload.get("format") == REFRESH_STATE_FORMAT else None
    if state == "refresh_in_flight":
        base_fingerprint = payload.get("base_fingerprint")
        try:
            active_payload = load_tokens(path)
            active_fingerprint = _token_fingerprint(active_payload)
        except (OSError, ValueError, WhoopApiError):
            active_payload = None
            active_fingerprint = None
        if (
            isinstance(base_fingerprint, str)
            and active_fingerprint is not None
            and base_fingerprint != active_fingerprint
        ):
            if payload.get("verification_required") is True and active_payload is not None:
                _write_refresh_state_unlocked(
                    path,
                    "successor_verification_pending",
                    active_payload,
                )
                state = "successor_verification_pending"
            else:
                _clear_refresh_state_unlocked(path)
                return
    if state == "reauthorization_required":
        detail = "the provider rejected the current refresh credential"
    elif state == "outcome_uncertain":
        detail = "the previous refresh outcome was uncertain"
    elif state == "refresh_in_flight":
        detail = "the previous refresh did not finish locally"
    elif state == "successor_verification_pending":
        raise WhoopApiError(
            "WHOOP automatic sync is blocked because the durable successor "
            "has not passed its authenticated GET gate. Run "
            "whoop-refresh-gate again; it will retry only the GET without "
            "another token rotation."
        )
    else:
        detail = "the local refresh state is incomplete"
    raise WhoopApiError(
        "WHOOP automatic refresh is blocked because %s. Reauthorize WHOOP "
        "before syncing; OpenHealth will not reuse a possibly consumed "
        "refresh token." % detail
    )


def _token_fingerprint(payload: Dict[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _token_transaction(
    base_payload: Optional[Dict[str, Any]],
    successor_payload: Dict[str, Any],
    *,
    fresh_authorization: bool = False,
) -> Dict[str, Any]:
    transaction = {
        "format": TOKEN_TRANSACTION_FORMAT,
        "base_fingerprint": _token_fingerprint(base_payload) if base_payload is not None else None,
        "successor_fingerprint": _token_fingerprint(successor_payload),
        "token": successor_payload,
    }
    if fresh_authorization:
        transaction["fresh_authorization"] = True
    return transaction


def _read_token_candidate(path: Path) -> Optional[Dict[str, Any]]:
    try:
        transaction = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(transaction, dict) or transaction.get("format") != TOKEN_TRANSACTION_FORMAT:
        return None
    payload = transaction.get("token")
    base_fingerprint = transaction.get("base_fingerprint")
    successor_fingerprint = transaction.get("successor_fingerprint")
    fresh_authorization = transaction.get("fresh_authorization", False)
    if not isinstance(payload, dict) or not payload.get("access_token"):
        return None
    if base_fingerprint is not None and not isinstance(base_fingerprint, str):
        return None
    if not isinstance(fresh_authorization, bool):
        return None
    if successor_fingerprint != _token_fingerprint(payload):
        return None
    return transaction


def _remove_token_recovery_files(path: Path, candidates: Iterable[Path]) -> None:
    removed = False
    for candidate in candidates:
        if candidate.exists():
            candidate.unlink()
            removed = True
    for candidate in _promotion_token_paths(path):
        if candidate.exists():
            candidate.unlink()
            removed = True
    if removed:
        _fsync_parent_directory(path)


def _quarantine_token_recovery_files(
    path: Path,
    candidates: Iterable[Path],
) -> List[Path]:
    """Preserve stale recovery evidence before a fresh OAuth authorization."""
    existing = [candidate for candidate in candidates if candidate.exists()]
    if not existing:
        return []
    quarantine = path.with_name(path.name + ".recovery-quarantine")
    quarantine.mkdir(mode=0o700, parents=False, exist_ok=True)
    if quarantine.is_symlink() or not quarantine.is_dir():
        raise WhoopApiError(
            "WHOOP token recovery quarantine is not a safe directory at %s."
            % quarantine
        )
    quarantine.chmod(0o700)
    moved = []
    for ordinal, candidate in enumerate(existing):
        destination = quarantine / (
            "%s.%s.%s" % (candidate.name, time.time_ns(), ordinal)
        )
        try:
            os.replace(candidate, destination)
            _set_owner_only(destination)
            _fsync_parent_directory(destination)
        except OSError as exc:
            raise WhoopApiError(
                "Could not preserve stale WHOOP token recovery evidence at %s."
                % candidate
            ) from exc
        moved.append(destination)
    _fsync_parent_directory(path)
    return moved


def _write_token_file(path: Path, payload: Dict[str, Any], prefix: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    complete = False
    try:
        _set_owner_only(temporary_path, descriptor)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        complete = True
        # Persist the directory entry as well as the file contents. This makes
        # the staged recovery record durable before any rename is attempted.
        _fsync_parent_directory(path)
        return temporary_path
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path.exists() and not complete:
            temporary_path.unlink()


def _promote_pending_token_unlocked(
    path: Path,
    pending_path: Path,
    transaction: Dict[str, Any],
) -> None:
    promotion_path = _write_token_file(
        path,
        transaction["token"],
        prefix=path.name + ".promote.",
    )
    try:
        os.replace(promotion_path, path)
        _fsync_parent_directory(path)
        _set_owner_only(path)
    except OSError as exc:
        if promotion_path.exists():
            promotion_path.unlink()
        raise WhoopApiError(
            "Could not persist the WHOOP token; the new rotating token is "
            "preserved at %s and will be recovered on the next attempt." % pending_path
        ) from exc

    try:
        pending_path.unlink()
        _fsync_parent_directory(path)
    except OSError as exc:
        # Canonical already contains the successor. The pending transaction is
        # now a harmless duplicate and the next locked access will remove it.
        raise WhoopApiError(
            "WHOOP token was saved, but its duplicate pending transaction "
            "could not be cleaned up at %s." % pending_path
        ) from exc


def _recover_pending_tokens_unlocked(path: Path) -> None:
    """Recover a transaction only when it is the active token's successor."""
    pending_path = _pending_token_path(path)
    candidates = [candidate for candidate in [pending_path, *_staged_token_paths(path)] if candidate.exists()]
    if not candidates:
        _remove_token_recovery_files(path, [])
        return

    transactions = [(candidate, _read_token_candidate(candidate)) for candidate in candidates]
    if any(transaction is None for _, transaction in transactions):
        raise WhoopApiError(
            "WHOOP token recovery data is incomplete at %s. "
            "Keep these owner-only files and reauthorize WHOOP before syncing." % path.parent
        )

    active_payload = load_tokens(path) if path.exists() else None
    active_fingerprint = _token_fingerprint(active_payload) if active_payload is not None else None
    duplicates = []
    successors = []
    conflicts = []
    for candidate, transaction in transactions:
        if transaction["successor_fingerprint"] == active_fingerprint:
            duplicates.append(candidate)
        elif transaction["base_fingerprint"] == active_fingerprint:
            successors.append((candidate, transaction))
        else:
            conflicts.append(candidate)

    distinct_successors = {transaction["successor_fingerprint"] for _, transaction in successors}
    fresh_authorization_modes = {
        transaction.get("fresh_authorization", False)
        for _, transaction in successors
    }
    if (
        conflicts
        or len(distinct_successors) > 1
        or len(fresh_authorization_modes) > 1
    ):
        raise WhoopApiError(
            "WHOOP token recovery conflicts with the active credential at %s. "
            "The canonical token was left unchanged; inspect the owner-only "
            "pending transaction before syncing." % path
        )

    if not successors:
        _remove_token_recovery_files(path, duplicates)
        return

    # Multiple files with the same base/successor are duplicate copies of one
    # transaction. Prefer the fixed pending path when it is already present.
    selected_path, selected_transaction = next(
        (
            (candidate, transaction)
            for candidate, transaction in successors
            if candidate == pending_path
        ),
        successors[0],
    )
    if selected_path != pending_path:
        try:
            os.replace(selected_path, pending_path)
            _fsync_parent_directory(path)
        except OSError as exc:
            raise WhoopApiError(
                "Could not finish WHOOP token recovery; the successor remains "
                "preserved at %s. Retry after fixing the filesystem error." % selected_path
            ) from exc
    _set_owner_only(pending_path)
    if selected_transaction.get("fresh_authorization") is True:
        # A fresh OAuth exchange supersedes every prior refresh incident and
        # gate proof. Do this before canonical promotion so a crash can never
        # expose the new token alongside a proof produced for an older token
        # lineage. If quarantine fails, leave the canonical token unchanged
        # and the durable pending transaction available for a safe retry.
        _quarantine_token_recovery_files(
            path,
            # Invalidate proof first. If a later state move fails, the absent
            # proof is itself fail-closed for the watcher while the canonical
            # token remains unchanged.
            [*_refresh_gate_paths(path), *_refresh_state_paths(path)],
        )
    _promote_pending_token_unlocked(path, pending_path, selected_transaction)
    _remove_token_recovery_files(path, [candidate for candidate, _ in successors] + duplicates)


def _save_tokens_unlocked(
    path: Path,
    payload: Dict[str, Any],
    *,
    allow_scope_reduction: bool = False,
) -> None:
    _check_scope_reduction(path, payload, allow_scope_reduction)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending_path = _pending_token_path(path)
    base_payload = load_tokens(path) if path.exists() else None
    transaction = _token_transaction(base_payload, payload)
    temporary_path = _write_token_file(
        path,
        transaction,
        prefix=path.name + ".pending.",
    )
    try:
        os.replace(temporary_path, pending_path)
        _fsync_parent_directory(path)
        _set_owner_only(pending_path)
    except OSError as exc:
        preserved = pending_path if pending_path.exists() else temporary_path
        raise WhoopApiError(
            "Could not persist the WHOOP token; the new rotating token is "
            "preserved at %s and will be recovered on the next attempt." % preserved
        ) from exc
    _promote_pending_token_unlocked(path, pending_path, transaction)


def _save_fresh_authorization_tokens_unlocked(
    path: Path,
    payload: Dict[str, Any],
    *,
    allow_scope_reduction: bool = False,
) -> None:
    """Supersede stale recovery state with a newly authorized token pair."""
    _check_scope_reduction(path, payload, allow_scope_reduction)
    path.parent.mkdir(parents=True, exist_ok=True)
    base_payload = load_tokens(path) if path.exists() else None
    transaction = _token_transaction(
        base_payload,
        payload,
        fresh_authorization=True,
    )
    stale_candidates = _token_recovery_paths(path)
    stale_candidates.extend(_refresh_gate_paths(path))
    stale_candidates.extend(_refresh_state_paths(path))
    temporary_path = _write_token_file(
        path,
        transaction,
        prefix=path.name + ".pending.",
    )
    _quarantine_token_recovery_files(path, stale_candidates)
    pending_path = _pending_token_path(path)
    try:
        os.replace(temporary_path, pending_path)
        _fsync_parent_directory(path)
        _set_owner_only(pending_path)
    except OSError as exc:
        preserved = pending_path if pending_path.exists() else temporary_path
        raise WhoopApiError(
            "Could not persist the newly authorized WHOOP token; it is "
            "preserved at %s and will be recovered on the next attempt."
            % preserved
        ) from exc
    _promote_pending_token_unlocked(path, pending_path, transaction)


def save_tokens(
    path: Path,
    payload: Dict[str, Any],
    *,
    allow_scope_reduction: bool = False,
    fresh_authorization: bool = False,
) -> None:
    with _token_file_lock(path):
        if fresh_authorization:
            _save_fresh_authorization_tokens_unlocked(
                path,
                payload,
                allow_scope_reduction=allow_scope_reduction,
            )
            return
        _recover_pending_tokens_unlocked(path)
        _save_tokens_unlocked(
            path,
            payload,
            allow_scope_reduction=allow_scope_reduction,
        )


def load_tokens(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise WhoopApiError("WHOOP token file not found at %s. Run whoop-exchange-code first." % path)
    return json.loads(path.read_text(encoding="utf-8"))


def _rotate_tokens_unlocked(
    path: Path,
    credentials: WhoopCredentials,
    tokens: Dict[str, Any],
    *,
    required_scopes: Optional[Iterable[str]] = None,
    operation: str,
    require_successor_verification: bool = False,
) -> Dict[str, Any]:
    refresh_token = str(tokens.get("refresh_token") or "").strip()
    if not refresh_token:
        raise WhoopApiError(
            "WHOOP token has no refresh token for %s. "
            "Reauthorize WHOOP with the offline scope." % operation
        )
    _write_refresh_state_unlocked(
        path,
        "refresh_in_flight",
        tokens,
        verification_required=require_successor_verification,
    )
    try:
        refreshed = refresh_tokens(credentials, refresh_token)
    except WhoopRefreshOutcomeUncertain as exc:
        _write_refresh_state_unlocked(
            path,
            "outcome_uncertain",
            tokens,
            failure=exc,
        )
        raise WhoopApiError(
            "WHOOP refresh outcome is uncertain. Reauthorize WHOOP before "
            "syncing; OpenHealth will not reuse a possibly consumed "
            "refresh token."
        ) from None
    except WhoopRefreshRejected as exc:
        _write_refresh_state_unlocked(
            path,
            "reauthorization_required",
            tokens,
            failure=exc,
        )
        raise WhoopApiError(
            "WHOOP rejected the current refresh credential. Reauthorize "
            "WHOOP before syncing; automatic retries are blocked."
        ) from None
    except WhoopRequestError:
        _clear_refresh_state_unlocked(path)
        raise
    if not _token_scopes(refreshed):
        inherited_scopes = sorted(_token_scopes(tokens))
        refreshed["scope"] = inherited_scopes or list(credentials.scopes)
    _save_tokens_unlocked(path, refreshed, allow_scope_reduction=True)
    if required_scopes is not None:
        try:
            require_token_scopes(refreshed, required_scopes, operation)
        except WhoopApiError:
            _write_refresh_state_unlocked(
                path,
                "reauthorization_required",
                refreshed,
                failure=WhoopRefreshRejected(
                    "WHOOP returned a successor with reduced scopes.",
                    cause_code="unusable_successor",
                ),
            )
            raise
    if require_successor_verification:
        _write_refresh_state_unlocked(
            path,
            "successor_verification_pending",
            refreshed,
        )
    else:
        _clear_refresh_state_unlocked(path)
    return refreshed


def ensure_valid_tokens(
    path: Path,
    credentials: WhoopCredentials,
    *,
    required_scopes: Optional[Iterable[str]] = None,
    operation: str = "requested operation",
) -> Dict[str, Any]:
    with _token_file_lock(path):
        _recover_pending_tokens_unlocked(path)
        _raise_if_refresh_blocked_unlocked(path)
        tokens = load_tokens(path)
        _set_owner_only(path)
        if required_scopes is not None:
            try:
                require_token_scopes(tokens, required_scopes, operation)
            except WhoopApiError:
                _write_refresh_state_unlocked(
                    path,
                    "reauthorization_required",
                    tokens,
                    failure=WhoopRefreshRejected(
                        "WHOOP authorization does not satisfy required scopes.",
                        cause_code="unusable_successor",
                    ),
                )
                raise
        expires_at = _parse_iso_datetime(tokens.get("expires_at"))
        if not expires_at or expires_at - timedelta(minutes=5) > datetime.now(timezone.utc):
            return tokens
        return _rotate_tokens_unlocked(
            path,
            credentials,
            tokens,
            required_scopes=required_scopes,
            operation=operation,
        )


class WhoopClient:
    def __init__(self, credentials: WhoopCredentials, tokens: Dict[str, Any]):
        self.credentials = credentials
        self.tokens = tokens

    def get_profile(self) -> Dict[str, Any]:
        return self._get("/user/profile/basic")

    def get_body_measurements(self) -> Dict[str, Any]:
        return self._get("/user/measurement/body")

    def verify_authenticated_access(self) -> str:
        """Run one minimal authenticated GET and discard its response body."""
        scopes = _token_scopes(self.tokens)
        probes = (
            ("read:cycles", "/cycle", {"limit": 1}),
            ("read:body_measurement", "/user/measurement/body", None),
            ("read:profile", "/user/profile/basic", None),
            ("read:recovery", "/recovery", {"limit": 1}),
            ("read:sleep", "/activity/sleep", {"limit": 1}),
            ("read:workout", "/activity/workout", {"limit": 1}),
        )
        last_endpoint_error = None
        for scope, path, query in probes:
            if scope not in scopes:
                continue
            try:
                self._get(path, query)
            except WhoopRequestError as exc:
                if (
                    exc.http_status in PROBE_FALLBACK_HTTP_STATUSES
                    and exc.provider_error_code not in PROBE_NON_FALLBACK_ERROR_CODES
                ):
                    last_endpoint_error = exc
                    continue
                raise
            return scope
        if last_endpoint_error is not None:
            raise last_endpoint_error
        raise WhoopApiError(
            "WHOOP rotation gate needs at least one supported read scope."
        )

    def list_cycles(self, start: Optional[str], end: Optional[str]) -> List[Dict[str, Any]]:
        return self._paginate("/cycle", start, end)

    def list_recoveries(self, start: Optional[str], end: Optional[str]) -> List[Dict[str, Any]]:
        return self._paginate("/recovery", start, end)

    def list_sleeps(self, start: Optional[str], end: Optional[str]) -> List[Dict[str, Any]]:
        return self._paginate("/activity/sleep", start, end)

    def list_workouts(self, start: Optional[str], end: Optional[str]) -> List[Dict[str, Any]]:
        return self._paginate("/activity/workout", start, end)

    def _paginate(self, path: str, start: Optional[str], end: Optional[str]) -> List[Dict[str, Any]]:
        next_token = None
        pages: List[Dict[str, Any]] = []
        while True:
            query = {"limit": 25}
            if start:
                query["start"] = start
            if end:
                query["end"] = end
            if next_token:
                query["nextToken"] = next_token
            payload = self._get(path, query)
            pages.append(payload)
            next_token = payload.get("nextToken") or payload.get("next_token")
            if not next_token:
                break
        return pages

    def _get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = API_BASE_URL + path
        if query:
            filtered = {key: value for key, value in query.items() if value is not None}
            url += "?" + urlencode(filtered)
        headers = {
            "Authorization": "Bearer %s" % self.tokens["access_token"],
            "Accept": "application/json",
        }
        return _request_json("GET", url, headers=headers, path_hint=path)


def verify_whoop_refresh_rotation(
    root: Path,
    *,
    credentials: Optional[WhoopCredentials] = None,
) -> Dict[str, Any]:
    """Force one A-to-B rotation and prove durable B with an authenticated GET."""
    paths = ensure_repo_structure(root)
    active_credentials = credentials or load_credentials_from_env()
    sync_lock_path = paths.whoop_tokens_path.with_name("whoop-sync.lock")
    with _exclusive_file_lock(sync_lock_path):
        with _token_file_lock(paths.whoop_tokens_path):
            _recover_pending_tokens_unlocked(paths.whoop_tokens_path)
            candidate = load_tokens(paths.whoop_tokens_path)
            _set_owner_only(paths.whoop_tokens_path)
            verification_pending = _successor_verification_pending_unlocked(
                paths.whoop_tokens_path,
                candidate,
            )
            if not verification_pending:
                _raise_if_refresh_blocked_unlocked(paths.whoop_tokens_path)
            configured_scopes = set(active_credentials.scopes) | {"offline"}
            try:
                require_token_scopes(
                    candidate,
                    configured_scopes,
                    "refresh rotation gate",
                )
            except WhoopApiError:
                _write_refresh_state_unlocked(
                    paths.whoop_tokens_path,
                    "reauthorization_required",
                    candidate,
                    failure=WhoopRefreshRejected(
                        "WHOOP authorization does not satisfy configured scopes.",
                        cause_code="unusable_successor",
                    ),
                )
                raise
            if verification_pending:
                persisted = candidate
                rotation_performed = False
            else:
                candidate_scopes = _token_scopes(candidate)
                candidate_fingerprint = _token_fingerprint(candidate)
                rotated = _rotate_tokens_unlocked(
                    paths.whoop_tokens_path,
                    active_credentials,
                    candidate,
                    required_scopes=candidate_scopes,
                    operation="refresh rotation gate",
                    require_successor_verification=True,
                )
                persisted = load_tokens(paths.whoop_tokens_path)
                if (
                    _token_fingerprint(persisted) != _token_fingerprint(rotated)
                    or _token_fingerprint(persisted) == candidate_fingerprint
                ):
                    raise WhoopApiError(
                        "WHOOP rotation gate could not verify the durable successor."
                    )
                rotation_performed = True
            try:
                probe_scope = WhoopClient(
                    active_credentials,
                    persisted,
                ).verify_authenticated_access()
            except Exception as exc:
                http_status = _safe_http_status(getattr(exc, "http_status", None))
                provider_error_code = _safe_provider_error_code(
                    getattr(exc, "provider_error_code", None)
                )
                suffix = ""
                if http_status is not None:
                    suffix += " HTTP %s" % http_status
                if provider_error_code is not None:
                    suffix += " (%s)" % provider_error_code
                _write_refresh_state_unlocked(
                    paths.whoop_tokens_path,
                    "successor_verification_pending",
                    persisted,
                    failure=exc,
                )
                raise WhoopApiError(
                    "WHOOP rotation gate kept the durable successor blocked "
                    "because its authenticated GET failed%s. Rerun this gate "
                    "to retry only the GET without another token rotation."
                    % suffix
                ) from None
            gate_proof = _write_refresh_gate_proof_unlocked(
                paths.whoop_tokens_path
            )
            _clear_refresh_state_unlocked(paths.whoop_tokens_path)
    return {
        "rotation_verified": True,
        "rotation_performed": rotation_performed,
        "authenticated_get": True,
        "probe_scope": probe_scope,
        "expires_at": persisted.get("expires_at"),
        "scope": sorted(_token_scopes(persisted)),
        "verified_at": gate_proof["verified_at"],
    }


def sync_whoop(
    root: Path,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days_back: int = 30,
    owner: str = "user",
    include_profile: bool = True,
    include_body_measurements: bool = True,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    paths = ensure_repo_structure(root)
    lock_path = paths.whoop_tokens_path.with_name("whoop-sync.lock")
    with _exclusive_file_lock(lock_path):
        return _sync_whoop_unlocked(
            root=root,
            start=start,
            end=end,
            days_back=days_back,
            owner=owner,
            include_profile=include_profile,
            include_body_measurements=include_body_measurements,
            client=client,
        )


def _local_snapshot_time(
    instant: str,
    local_timezone: Optional[tzinfo] = None,
) -> str:
    """Render one aware sync instant in the machine's local calendar time."""
    parsed = datetime.fromisoformat(instant.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("WHOOP snapshot instant must include a timezone")
    return parsed.astimezone(local_timezone).replace(microsecond=0).isoformat()


def _sync_whoop_unlocked(
    root: Path,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days_back: int = 30,
    owner: str = "user",
    include_profile: bool = True,
    include_body_measurements: bool = True,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    paths = ensure_repo_structure(root)
    index.init_db(paths.db_path)
    credentials = None
    if client is None:
        credentials = load_credentials_from_env()
        tokens = ensure_valid_tokens(
            paths.whoop_tokens_path,
            credentials,
            required_scopes=_full_sync_required_scopes(
                include_profile,
                include_body_measurements,
            ),
            operation="full sync",
        )
        client = WhoopClient(credentials, tokens)
    sync_started_at = now_utc()
    body_fetched_at = (
        _local_snapshot_time(sync_started_at)
        if include_body_measurements
        else None
    )
    sync_stamp = sync_started_at.replace(":", "").replace("+00:00", "z")
    state = load_sync_state(paths.whoop_sync_state_path)
    start_value, end_value = resolve_sync_window(start, end, days_back, state)

    artifacts: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    parser_notes: List[str] = []
    raw_counts: Dict[str, int] = {}

    datasets = [
        ("cycles", client.list_cycles(start_value, end_value), "/cycle"),
        ("recoveries", client.list_recoveries(start_value, end_value), "/recovery"),
        ("sleeps", client.list_sleeps(start_value, end_value), "/activity/sleep"),
        ("workouts", client.list_workouts(start_value, end_value), "/activity/workout"),
    ]
    if include_profile:
        datasets.append(("profile", [client.get_profile()], "/user/profile/basic"))
    if include_body_measurements:
        datasets.append(("body_measurements", [client.get_body_measurements()], "/user/measurement/body"))

    for dataset_name, pages, endpoint_path in datasets:
        raw_counts[dataset_name] = 0
        for page_index, payload in enumerate(pages, start=1):
            artifact = archive_whoop_payload(
                paths=paths,
                dataset_name=dataset_name,
                endpoint_path=endpoint_path,
                payload=payload,
                sync_stamp=sync_stamp,
                page_index=page_index,
            )
            artifacts.append(artifact)
            fetched_at = (
                body_fetched_at
                if dataset_name == "body_measurements"
                else sync_started_at
            )
            page_records = normalize_whoop_payload(
                dataset_name=dataset_name,
                payload=payload,
                artifact_id=artifact["artifact_id"],
                source_id=WHOOP_SOURCE_ID,
                fetched_at=fetched_at,
            )
            raw_counts[dataset_name] += len(page_records)
            records.extend(page_records)
            if payload.get("nextToken") or payload.get("next_token"):
                parser_notes.append("%s page %s included pagination token." % (dataset_name, page_index))

    if not records:
        parser_notes.append("WHOOP sync returned no records for the requested window.")
    replaced_ids = purge_existing_whoop_records(paths.db_path, records, start_value, end_value)
    for artifact in artifacts:
        write_json(paths.artifact_manifests / ("%s.json" % artifact["artifact_id"]), artifact)
        index.upsert_artifact(paths.db_path, artifact)
    for record in records:
        index.upsert_record(paths.db_path, record)

    coverage_points = [record.get("date") or record.get("start_date") for record in records if record.get("date") or record.get("start_date")]
    coverage_start = min(coverage_points) if coverage_points else start_value[:10] if start_value else None
    coverage_end = max(coverage_points) if coverage_points else end_value[:10] if end_value else None
    source = SourceManifest(
        source_id=WHOOP_SOURCE_ID,
        source_type="whoop",
        owner=owner,
        label="WHOOP live sync",
        created_at=sync_started_at,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        files=[artifact["archived_path"] for artifact in artifacts],
        parser_status="synced",
        notes=parser_notes,
        metadata={
            "sync_window_start": start_value,
            "sync_window_end": end_value,
            "fetched_at": sync_started_at,
            "collections": raw_counts,
        },
    )
    write_json(paths.source_manifests / ("%s.json" % WHOOP_SOURCE_ID), source.to_dict())
    index.upsert_source(paths.db_path, source.to_dict())
    source_brief = build_source_brief(source.to_dict(), index.list_artifacts(paths.db_path), index.list_records(paths.db_path))
    write_text(paths.briefs / ("%s.md" % WHOOP_SOURCE_ID), source_brief)
    context_stats = refresh_contexts(paths, index)
    save_sync_state(
        paths.whoop_sync_state_path,
        {
            "last_successful_sync": sync_started_at,
            "last_window_start": start_value,
            "last_window_end": end_value,
            "last_record_count": len(records),
            "replaced_record_ids": len(replaced_ids),
        },
    )
    return {
        "source_id": WHOOP_SOURCE_ID,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "records_imported": len(records),
        "artifacts_archived": len(artifacts),
        "collections": raw_counts,
        "replaced_record_ids": len(replaced_ids),
        "contexts": context_stats,
        "capabilities": CAPABILITIES,
    }


def sync_whoop_body_measurements(
    root: Path,
    owner: str = "user",
    client: Optional[Any] = None,
    fetched_at: Optional[str] = None,
) -> Dict[str, Any]:
    paths = ensure_repo_structure(root)
    lock_path = paths.whoop_tokens_path.with_name("whoop-sync.lock")
    with _exclusive_file_lock(lock_path):
        return _sync_whoop_body_measurements_unlocked(
            root=root,
            owner=owner,
            client=client,
            fetched_at=fetched_at,
        )


def _sync_whoop_body_measurements_unlocked(
    root: Path,
    owner: str = "user",
    client: Optional[Any] = None,
    fetched_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch and persist a dated snapshot of WHOOP's current body measurements.

    WHOOP's public body-measurement endpoint returns the current values rather
    than a historical collection. This lightweight sync is therefore intended
    for a daily local scheduler. Repeated runs on the same date are idempotent;
    runs on later dates retain earlier snapshots.
    """
    paths = ensure_repo_structure(root)
    index.init_db(paths.db_path)
    if client is None:
        credentials = load_credentials_from_env()
        tokens = ensure_valid_tokens(
            paths.whoop_tokens_path,
            credentials,
            required_scopes={"read:body_measurement", "offline"},
            operation="body sync",
        )
        client = WhoopClient(credentials, tokens)

    # Use the machine's local offset for the daily bucket so a morning fetch
    # near midnight UTC is still stored under the person's local calendar date.
    snapshot_at = fetched_at or datetime.now().astimezone().replace(microsecond=0).isoformat()
    sync_stamp = snapshot_at.replace(":", "").replace("+00:00", "z")
    payload = client.get_body_measurements()
    artifact = archive_whoop_payload(
        paths=paths,
        dataset_name="body_measurements",
        endpoint_path="/user/measurement/body",
        payload=payload,
        sync_stamp=sync_stamp,
        page_index=1,
    )
    records = normalize_body_measurements(
        payload=payload,
        artifact_id=artifact["artifact_id"],
        source_id=WHOOP_SOURCE_ID,
        fetched_at=snapshot_at,
    )

    write_json(paths.artifact_manifests / ("%s.json" % artifact["artifact_id"]), artifact)
    index.upsert_artifact(paths.db_path, artifact)
    for record in records:
        index.upsert_record(paths.db_path, record)

    all_records = index.list_records_by_source(paths.db_path, WHOOP_SOURCE_ID)
    coverage_points = [
        record.get("date") or record.get("start_date")
        for record in all_records
        if record.get("date") or record.get("start_date")
    ]
    existing_source = next(
        (source for source in index.list_sources(paths.db_path) if source["source_id"] == WHOOP_SOURCE_ID),
        None,
    )
    source_files = list((existing_source or {}).get("files") or [])
    if artifact["archived_path"] not in source_files:
        source_files.append(artifact["archived_path"])
    source_notes = list((existing_source or {}).get("notes") or [])
    timestamp_note = (
        "WHOOP current body measurements are dated by fetch time when the provider "
        "does not return a measurement timestamp."
    )
    if timestamp_note not in source_notes:
        source_notes.append(timestamp_note)
    source_metadata = dict((existing_source or {}).get("metadata") or {})
    source_metadata["latest_body_snapshot"] = {
        "fetched_at": snapshot_at,
        "records": len(records),
        "endpoint": "/user/measurement/body",
    }
    source = SourceManifest(
        source_id=WHOOP_SOURCE_ID,
        source_type="whoop",
        owner=owner,
        label=(existing_source or {}).get("label") or "WHOOP live sync",
        created_at=(existing_source or {}).get("created_at") or snapshot_at,
        coverage_start=min(coverage_points) if coverage_points else snapshot_at[:10],
        coverage_end=max(coverage_points) if coverage_points else snapshot_at[:10],
        files=source_files,
        parser_status="synced",
        notes=source_notes,
        metadata=source_metadata,
    )
    write_json(paths.source_manifests / ("%s.json" % WHOOP_SOURCE_ID), source.to_dict())
    index.upsert_source(paths.db_path, source.to_dict())
    source_brief = build_source_brief(
        source.to_dict(),
        index.list_artifacts(paths.db_path),
        all_records,
    )
    write_text(paths.briefs / ("%s.md" % WHOOP_SOURCE_ID), source_brief)
    context_stats = refresh_contexts(paths, index)
    return {
        "source_id": WHOOP_SOURCE_ID,
        "snapshot_at": snapshot_at,
        "snapshot_date": snapshot_at[:10],
        "records_imported": len(records),
        "artifact_id": artifact["artifact_id"],
        "archived_path": artifact["archived_path"],
        "contexts": context_stats,
    }


def archive_whoop_payload(
    paths,
    dataset_name: str,
    endpoint_path: str,
    payload: Dict[str, Any],
    sync_stamp: str,
    page_index: int,
) -> Dict[str, Any]:
    target_dir = paths.raw_archive_whoop_api / sync_stamp / slugify(dataset_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / ("page-%03d.json" % page_index)
    write_json(target_path, payload)
    checksum = sha256sum(target_path)
    artifact_id = "artifact-whoop-%s" % checksum[:12]
    artifact = ArtifactManifest(
        artifact_id=artifact_id,
        source_id=WHOOP_SOURCE_ID,
        source_type="whoop",
        original_path="whoop://%s?page=%s" % (endpoint_path, page_index),
        archived_path=str(target_path),
        checksum=checksum,
        mime_type="application/json",
        size_bytes=target_path.stat().st_size,
        provenance={"ingested_at": now_utc(), "endpoint_path": endpoint_path},
        privacy={"storage": "local-first", "shareable": False},
        metadata={
            "dataset_name": dataset_name,
            "page_index": page_index,
            "next_token": payload.get("nextToken") or payload.get("next_token"),
        },
    )
    return artifact.to_dict()


def normalize_whoop_payload(
    dataset_name: str,
    payload: Dict[str, Any],
    artifact_id: str,
    source_id: str,
    fetched_at: str,
) -> List[Dict[str, Any]]:
    if dataset_name == "profile":
        return normalize_profile(payload, artifact_id, source_id, fetched_at)
    if dataset_name == "body_measurements":
        return normalize_body_measurements(payload, artifact_id, source_id, fetched_at)
    items = extract_items(payload)
    if dataset_name == "cycles":
        return normalize_cycles(items, artifact_id, source_id)
    if dataset_name == "recoveries":
        return normalize_recoveries(items, artifact_id, source_id)
    if dataset_name == "sleeps":
        return normalize_sleeps(items, artifact_id, source_id)
    if dataset_name == "workouts":
        return normalize_workouts(items, artifact_id, source_id)
    return []


def normalize_profile(payload: Dict[str, Any], artifact_id: str, source_id: str, fetched_at: str) -> List[Dict[str, Any]]:
    profile = payload.get("user") if isinstance(payload.get("user"), dict) else payload
    text = "WHOOP profile synced for %s." % (profile.get("user_id") or profile.get("id") or "unknown user")
    note = ContextNote(
        id="whoop-profile",
        record_type="ContextNote",
        source_id=source_id,
        title="WHOOP profile",
        summary=text,
        artifact_ids=[artifact_id],
        evidence_class="personal",
        confidence=0.98,
        captured_at=fetched_at,
        tags=["whoop", "profile"],
        metadata=profile,
        note_kind="whoop_profile",
        themes=["whoop-profile"],
    )
    return [note.to_dict()]


def normalize_body_measurements(
    payload: Dict[str, Any],
    artifact_id: str,
    source_id: str,
    fetched_at: str,
) -> List[Dict[str, Any]]:
    measurements = payload.get("records") if isinstance(payload.get("records"), list) else [payload]
    records: List[Dict[str, Any]] = []
    for item in measurements:
        provider_timestamp = item.get("updated_at") or item.get("created_at")
        date_value = (provider_timestamp or fetched_at)[:10]
        metadata = dict(item)
        metadata["openhealth_snapshot"] = {
            "fetched_at": fetched_at,
            "provider_measurement_timestamp": provider_timestamp,
            "date_basis": "provider_timestamp" if provider_timestamp else "fetch_timestamp",
        }
        for metric_name, unit in (
            ("height_meter", "m"),
            ("weight_kilogram", "kg"),
            ("max_heart_rate", "bpm"),
        ):
            value = item.get(metric_name)
            if value is None:
                continue
            records.append(
                Observation(
                    id="whoop-body-%s-%s" % (slugify(metric_name), date_value),
                    record_type="Observation",
                    source_id=source_id,
                    title="WHOOP current %s snapshot" % metric_name.replace("_", " "),
                    summary="WHOOP current body measurement %s captured on %s." % (metric_name, fetched_at),
                    artifact_ids=[artifact_id],
                    evidence_class="personal",
                    confidence=0.95,
                    captured_at=fetched_at,
                    date=date_value,
                    tags=["whoop", "body-measurement"],
                    metadata=metadata,
                    observation_kind="whoop_body_measurement",
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                ).to_dict()
            )
    return records


def normalize_cycles(items: Iterable[Dict[str, Any]], artifact_id: str, source_id: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for item in items:
        cycle_id = str(item.get("id") or item.get("cycle_id"))
        start_value = item.get("start") or item.get("start_time")
        end_value = item.get("end") or item.get("end_time")
        score = item.get("score") or {}
        date_value = (start_value or end_value or "")[:10] or None
        observation_ids: List[str] = []
        for metric_name, value, unit in (
            ("strain", _pick_metric(score, ["strain", "day_strain"]), None),
            ("kilojoule", _pick_metric(score, ["kilojoule", "kilojoules"]), "kJ"),
            ("average_heart_rate", item.get("average_heart_rate"), "bpm"),
            ("max_heart_rate", item.get("max_heart_rate"), "bpm"),
        ):
            if value is None:
                continue
            metric_id = "whoop-cycle-%s-%s" % (cycle_id, slugify(metric_name))
            observation_ids.append(metric_id)
            records.append(
                Observation(
                    id=metric_id,
                    record_type="Observation",
                    source_id=source_id,
                    title="WHOOP cycle %s" % metric_name.replace("_", " "),
                    summary="Cycle metric %s for %s." % (metric_name, date_value or cycle_id),
                    artifact_ids=[artifact_id],
                    evidence_class="personal",
                    confidence=0.96,
                    date=date_value,
                    start_date=date_value,
                    tags=["whoop", "cycle", slugify(metric_name)],
                    metadata=item,
                    observation_kind="whoop_cycle_metric",
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                ).to_dict()
            )
        records.append(
            TimelineEvent(
                id="whoop-cycle-%s" % cycle_id,
                record_type="TimelineEvent",
                source_id=source_id,
                title="WHOOP cycle",
                summary="Cycle from %s to %s." % (start_value or "unknown", end_value or "unknown"),
                artifact_ids=[artifact_id],
                evidence_class="personal",
                confidence=0.95,
                date=date_value,
                start_date=date_value,
                tags=["whoop", "cycle"],
                metadata=item,
                event_kind="whoop_cycle",
                related_record_ids=observation_ids,
            ).to_dict()
        )
    return records


def normalize_recoveries(items: Iterable[Dict[str, Any]], artifact_id: str, source_id: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for item in items:
        recovery_id = str(item.get("cycle_id") or item.get("id"))
        date_value = ((item.get("created_at") or item.get("updated_at") or item.get("score_state", {}).get("updated_at") or "")[:10] or None)
        score = item.get("score") or item.get("score_state") or {}
        observation_ids: List[str] = []
        for metric_name, value, unit in (
            ("recovery_score", _pick_metric(score, ["recovery_score", "recovery"]), "%"),
            ("hrv_rmssd", _pick_metric(score, ["hrv_rmssd_milli", "hrv_rmssd"]), "ms"),
            ("resting_heart_rate", _pick_metric(score, ["resting_heart_rate"]), "bpm"),
            ("skin_temp_celsius", _pick_metric(score, ["skin_temp_celsius", "skin_temp"]), "C"),
        ):
            if value is None:
                continue
            metric_id = "whoop-recovery-%s-%s" % (recovery_id, slugify(metric_name))
            observation_ids.append(metric_id)
            records.append(
                Observation(
                    id=metric_id,
                    record_type="Observation",
                    source_id=source_id,
                    title="WHOOP recovery %s" % metric_name.replace("_", " "),
                    summary="Recovery metric %s for %s." % (metric_name, date_value or recovery_id),
                    artifact_ids=[artifact_id],
                    evidence_class="personal",
                    confidence=0.96,
                    date=date_value,
                    tags=["whoop", "recovery", slugify(metric_name)],
                    metadata=item,
                    observation_kind="whoop_recovery_metric",
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                ).to_dict()
            )
        records.append(
            TimelineEvent(
                id="whoop-recovery-%s" % recovery_id,
                record_type="TimelineEvent",
                source_id=source_id,
                title="WHOOP recovery",
                summary="Recovery synced for %s." % (date_value or recovery_id),
                artifact_ids=[artifact_id],
                evidence_class="personal",
                confidence=0.95,
                date=date_value,
                tags=["whoop", "recovery"],
                metadata=item,
                event_kind="whoop_recovery",
                related_record_ids=observation_ids,
            ).to_dict()
        )
    return records


def normalize_sleeps(items: Iterable[Dict[str, Any]], artifact_id: str, source_id: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for item in items:
        sleep_id = str(item.get("id") or item.get("sleep_id"))
        start_value = item.get("start") or item.get("start_time")
        end_value = item.get("end") or item.get("end_time")
        score = item.get("score") or {}
        date_value = (start_value or end_value or "")[:10] or None
        observation_ids: List[str] = []
        metrics = [
            ("sleep_performance_percentage", _pick_metric(score, ["sleep_performance_percentage", "sleep_performance"]), "%"),
            ("sleep_efficiency_percentage", _pick_metric(score, ["sleep_efficiency_percentage", "sleep_efficiency"]), "%"),
            ("sleep_consistency_percentage", _pick_metric(score, ["sleep_consistency_percentage", "sleep_consistency"]), "%"),
            ("respiratory_rate", _pick_metric(score, ["respiratory_rate"]), "breaths/min"),
        ]
        stage_summary = item.get("sleep_stage_summary") or score.get("sleep_stage_summary") or {}
        metrics.extend(
            [
                ("total_in_bed_time_milli", stage_summary.get("total_in_bed_time_milli"), "ms"),
                ("total_awake_time_milli", stage_summary.get("total_awake_time_milli"), "ms"),
                ("total_light_sleep_time_milli", stage_summary.get("total_light_sleep_time_milli"), "ms"),
                ("total_slow_wave_sleep_time_milli", stage_summary.get("total_slow_wave_sleep_time_milli"), "ms"),
                ("total_rem_sleep_time_milli", stage_summary.get("total_rem_sleep_time_milli"), "ms"),
            ]
        )
        for metric_name, value, unit in metrics:
            if value is None:
                continue
            metric_id = "whoop-sleep-%s-%s" % (sleep_id, slugify(metric_name))
            observation_ids.append(metric_id)
            records.append(
                Observation(
                    id=metric_id,
                    record_type="Observation",
                    source_id=source_id,
                    title="WHOOP sleep %s" % metric_name.replace("_", " "),
                    summary="Sleep metric %s for %s." % (metric_name, date_value or sleep_id),
                    artifact_ids=[artifact_id],
                    evidence_class="personal",
                    confidence=0.96,
                    date=date_value,
                    tags=["whoop", "sleep", slugify(metric_name)],
                    metadata=item,
                    observation_kind="whoop_sleep_metric",
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                ).to_dict()
            )
        records.append(
            TimelineEvent(
                id="whoop-sleep-%s" % sleep_id,
                record_type="TimelineEvent",
                source_id=source_id,
                title="WHOOP sleep",
                summary="Sleep from %s to %s." % (start_value or "unknown", end_value or "unknown"),
                artifact_ids=[artifact_id],
                evidence_class="personal",
                confidence=0.95,
                date=date_value,
                start_date=date_value,
                tags=["whoop", "sleep"],
                metadata=item,
                event_kind="whoop_sleep",
                related_record_ids=observation_ids,
            ).to_dict()
        )
    return records


def normalize_workouts(items: Iterable[Dict[str, Any]], artifact_id: str, source_id: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for item in items:
        workout_id = str(item.get("id") or item.get("workout_id"))
        start_value = item.get("start") or item.get("start_time")
        end_value = item.get("end") or item.get("end_time")
        score = item.get("score") or {}
        date_value = (start_value or end_value or "")[:10] or None
        observation_ids: List[str] = []
        for metric_name, value, unit in (
            ("strain", _pick_metric(score, ["strain"]), None),
            ("kilojoule", _pick_metric(score, ["kilojoule", "kilojoules"]), "kJ"),
            ("average_heart_rate", _pick_metric(score, ["average_heart_rate"]), "bpm"),
            ("max_heart_rate", _pick_metric(score, ["max_heart_rate"]), "bpm"),
            ("distance_meter", _pick_metric(score, ["distance_meter", "distance"]), "m"),
        ):
            if value is None:
                continue
            metric_id = "whoop-workout-%s-%s" % (workout_id, slugify(metric_name))
            observation_ids.append(metric_id)
            records.append(
                Observation(
                    id=metric_id,
                    record_type="Observation",
                    source_id=source_id,
                    title="WHOOP workout %s" % metric_name.replace("_", " "),
                    summary="Workout metric %s for %s." % (metric_name, date_value or workout_id),
                    artifact_ids=[artifact_id],
                    evidence_class="personal",
                    confidence=0.96,
                    date=date_value,
                    tags=["whoop", "workout", slugify(metric_name)],
                    metadata=item,
                    observation_kind="whoop_workout_metric",
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                ).to_dict()
            )
        workout_type = item.get("sport_name") or item.get("sport") or item.get("sport_id")
        records.append(
            TimelineEvent(
                id="whoop-workout-%s" % workout_id,
                record_type="TimelineEvent",
                source_id=source_id,
                title="WHOOP workout",
                summary="Workout %s from %s to %s." % (workout_type or "session", start_value or "unknown", end_value or "unknown"),
                artifact_ids=[artifact_id],
                evidence_class="personal",
                confidence=0.95,
                date=date_value,
                start_date=date_value,
                tags=["whoop", "workout"],
                metadata=item,
                event_kind="whoop_workout",
                related_record_ids=observation_ids,
            ).to_dict()
        )
    return records


def purge_existing_whoop_records(db_path: Path, new_records: List[Dict[str, Any]], start: str, end: str) -> List[str]:
    existing = index.list_records_by_source(db_path, WHOOP_SOURCE_ID)
    target_ids = {record["id"] for record in new_records}
    for record in existing:
        record_date = record.get("date") or record.get("start_date")
        if record["id"] in target_ids:
            target_ids.add(record["id"])
        elif record.get("observation_kind") == "whoop_body_measurement":
            # The public API exposes only the current body measurement, so
            # daily local snapshots are the history. Never remove an older
            # snapshot merely because a full sync window overlaps its date.
            continue
        elif record_date and start[:10] <= record_date <= end[:10]:
            target_ids.add(record["id"])
    to_delete = sorted(target_ids)
    index.delete_records_by_ids(db_path, to_delete)
    return to_delete


def extract_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("records", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    if isinstance(payload, list):
        return payload
    return []


def resolve_sync_window(
    start: Optional[str],
    end: Optional[str],
    days_back: int,
    state: Dict[str, Any],
) -> Tuple[str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end_dt = _parse_iso_datetime(end) if end else now
    if start:
        start_dt = _parse_iso_datetime(start)
    elif state.get("last_successful_sync"):
        start_dt = _parse_iso_datetime(state["last_successful_sync"]) - timedelta(days=2)
    else:
        start_dt = end_dt - timedelta(days=days_back)
    return start_dt.isoformat().replace("+00:00", "Z"), end_dt.isoformat().replace("+00:00", "Z")


def save_sync_state(path: Path, payload: Dict[str, Any]) -> None:
    write_json(path, payload)


def load_sync_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def latest_whoop_summary(root: Path) -> Dict[str, Any]:
    paths = ensure_repo_structure(root)
    index.init_db(paths.db_path)
    records = index.list_records_by_source(paths.db_path, WHOOP_SOURCE_ID)
    sync_state = load_sync_state(paths.whoop_sync_state_path)

    summary = {
        "source_id": WHOOP_SOURCE_ID,
        "records": len(records),
        "last_successful_sync": _format_timestamp(sync_state.get("last_successful_sync")),
        "latest_capture": _latest_timestamp_from_fields(records, ("captured_at",)),
        "latest_sleep_end": _latest_timestamp_from_metadata(records, "sleep", "end"),
        "latest_workout_end": _latest_timestamp_from_metadata(records, "workout", "end"),
        "latest_recovery": _latest_timestamp_from_metadata(records, "recovery", "created_at"),
        "latest_cycle_update": _latest_timestamp_from_metadata(records, "cycle", ("updated_at", "end", "start")),
    }
    return summary


def verify_webhook_signature(secret: str, payload_bytes: bytes, signature_header: str, timestamp_header: str) -> bool:
    signed_payload = timestamp_header.encode("utf-8") + payload_bytes
    digest = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, signature_header)


def _post_form(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = urlencode(payload).encode("utf-8")
    return _request_json(
        "POST",
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data=body,
        path_hint="token exchange",
    )


def _normalize_token_response(payload: Dict[str, Any], refresh_token: Optional[str] = None) -> Dict[str, Any]:
    expires_in = int(payload.get("expires_in", 3600))
    expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=expires_in)
    normalized = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", refresh_token),
        "token_type": payload.get("token_type", "Bearer"),
        "scope": payload.get("scope", "").split() if isinstance(payload.get("scope"), str) else payload.get("scope"),
        "expires_at": expires_at.isoformat(),
        "raw": payload,
    }
    return normalized


def _parse_iso_datetime(value: Optional[str]) -> datetime:
    if not value:
        raise WhoopApiError("Missing datetime value for WHOOP sync window.")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pick_metric(payload: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _latest_timestamp_from_fields(records: List[Dict[str, Any]], fields: Iterable[str]) -> Optional[Dict[str, Any]]:
    latest = None
    for record in records:
        for field in fields:
            value = record.get(field)
            if not value:
                continue
            parsed = _parse_optional_iso_datetime(value)
            if not parsed:
                continue
            if latest is None or parsed > latest[0]:
                latest = (parsed, field, record)
    if latest is None:
        return None
    return {
        "field": latest[1],
        "record_type": latest[2].get("record_type"),
        "title": latest[2].get("title"),
        "record_id": latest[2].get("id"),
        "timestamp": _format_timestamp(latest[0]),
    }


def _latest_timestamp_from_metadata(records: List[Dict[str, Any]], tag: str, field: Any) -> Optional[Dict[str, Any]]:
    fields = field if isinstance(field, (list, tuple)) else (field,)
    latest = None
    for record in records:
        tags = record.get("tags") or []
        if tag not in tags:
            continue
        metadata = record.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        for field_name in fields:
            parsed = _parse_optional_iso_datetime(metadata.get(field_name))
            if not parsed:
                continue
            if latest is None or parsed > latest[0]:
                latest = (parsed, field_name, record, metadata)
    if latest is None:
        return None
    local_value = _format_in_record_timezone(latest[0], latest[3].get("timezone_offset"))
    return {
        "field": latest[1],
        "record_type": latest[2].get("record_type"),
        "title": latest[2].get("title"),
        "record_id": latest[2].get("id"),
        "timestamp": _format_timestamp(latest[0]),
        "local_timestamp": local_value.isoformat(),
        "timezone_offset": latest[3].get("timezone_offset"),
    }


def _parse_optional_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return _parse_iso_datetime(value)
    except (TypeError, ValueError, WhoopApiError):
        return None


def _format_timestamp(value: Any) -> Optional[Dict[str, Any]]:
    parsed = value if isinstance(value, datetime) else _parse_optional_iso_datetime(value)
    if not parsed:
        return None
    local_value = parsed.astimezone()
    return {
        "utc": parsed.astimezone(timezone.utc).isoformat(),
        "local": local_value.isoformat(),
        "hour": local_value.strftime("%H"),
        "minute": local_value.strftime("%M"),
    }


def _format_in_record_timezone(value: datetime, offset: Optional[str]) -> datetime:
    if not offset or len(offset) != 6 or offset[0] not in {"+", "-"}:
        return value.astimezone()
    try:
        sign = 1 if offset[0] == "+" else -1
        hours = int(offset[1:3])
        minutes = int(offset[4:6])
    except ValueError:
        return value.astimezone()
    record_tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
    return value.astimezone(record_tz)


def _first_query_value(query: Dict[str, List[str]], key: str) -> Optional[str]:
    values = query.get(key) or []
    return values[0] if values else None


def _request_json(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[bytes] = None,
    path_hint: str = "",
) -> Dict[str, Any]:
    headers = dict(headers or {})
    headers.setdefault("User-Agent", USER_AGENT)
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            raw_detail = exc.read()
        except Exception:
            raw_detail = b""
        detail = (
            raw_detail.decode("utf-8", errors="ignore")
            if isinstance(raw_detail, bytes)
            else ""
        )
        error_code = _safe_http_error_code(detail)
        suffix = " (%s)" % error_code if error_code else ""
        # Provider error bodies can echo OAuth forms or bearer credentials.
        # Keep the raw body out of both the exception and its chained context.
        raise WhoopRequestError(
            "WHOOP %s failed with HTTP %s%s."
            % (path_hint or method, exc.code, suffix),
            http_status=exc.code,
            provider_error_code=error_code,
            outcome_uncertain=(
                method.upper() not in {"GET", "HEAD"}
                and (exc.code >= 500 or exc.code == 408)
            ),
            cause_code=(
                "http_uncertain"
                if exc.code >= 500 or exc.code == 408
                else None
            ),
        ) from None
    except URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc.reason):
            outcome_uncertain = (
                method.upper() not in {"GET", "HEAD"}
                and _transport_outcome_uncertain(exc.reason)
            )
            raise WhoopRequestError(
                "WHOOP %s failed without a definitive HTTP response."
                % (path_hint or method),
                outcome_uncertain=outcome_uncertain,
                cause_code=(
                    "transport_timeout"
                    if isinstance(exc.reason, (TimeoutError, socket.timeout))
                    else "transport_failure"
                ),
            ) from None
        return _request_json_with_curl(method, url, headers=headers, data=data, path_hint=path_hint)
    except HTTPException:
        # Partial protocol responses may carry provider bytes (including
        # echoed OAuth fields) in their exception object. Never chain them.
        raise WhoopRequestError(
            "WHOOP %s ended before a complete response was received."
            % (path_hint or method),
            outcome_uncertain=method.upper() not in {"GET", "HEAD"},
            cause_code="protocol_incomplete",
        ) from None


def _safe_http_error_code(detail: str) -> Optional[str]:
    """Return only a fixed, non-secret OAuth/API error code."""
    try:
        payload = json.loads(detail)
    except (TypeError, ValueError):
        return None
    error_code = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error_code, str) and error_code in SAFE_HTTP_ERROR_CODES:
        return error_code
    return None


def _request_json_with_curl(
    method: str,
    url: str,
    headers: Dict[str, str],
    data: Optional[bytes],
    path_hint: str,
) -> Dict[str, Any]:
    # Keep authorization headers, OAuth form fields, and query parameters out
    # of argv and exception objects. curl reads the complete request from an
    # ephemeral stdin config instead.
    command = ["curl", "--config", "-"]
    config_lines = [
        "silent",
        "show-error",
        "fail-with-body",
        "connect-timeout = %s" % _curl_config_quote(str(CURL_CONNECT_TIMEOUT_SECONDS)),
        "max-time = %s" % _curl_config_quote(str(CURL_MAX_TIME_SECONDS)),
        "request = %s" % _curl_config_quote(method),
        "url = %s" % _curl_config_quote(url),
    ]
    for key, value in headers.items():
        config_lines.append("header = %s" % _curl_config_quote("%s: %s" % (key, value)))
    if data is not None:
        config_lines.append("data = %s" % _curl_config_quote(data.decode("utf-8")))
    config = "\n".join(config_lines) + "\n"
    try:
        output = subprocess.run(
            command,
            check=True,
            capture_output=True,
            input=config,
            text=True,
            timeout=CURL_PROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        outcome_uncertain = (
            method.upper() not in {"GET", "HEAD"}
            and exc.returncode not in {5, 6, 7, 35, 60}
        )
        raise WhoopRequestError(
            "WHOOP %s failed via curl fallback with exit code %s."
            % (path_hint or method, exc.returncode),
            outcome_uncertain=outcome_uncertain,
            cause_code=(
                "http_uncertain"
                if exc.returncode == 22
                else "transport_timeout"
                if exc.returncode == 28
                else "transport_failure"
            ),
        ) from None
    except subprocess.TimeoutExpired:
        raise WhoopRequestError(
            "WHOOP %s timed out via curl fallback after %s seconds."
            % (path_hint or method, CURL_PROCESS_TIMEOUT_SECONDS),
            outcome_uncertain=method.upper() not in {"GET", "HEAD"},
            cause_code="transport_timeout",
        ) from None
    return json.loads(output.stdout)


def _curl_config_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return '"%s"' % escaped
