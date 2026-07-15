"""Oura Cloud API V2 — live OAuth2 pull → canonical records in the local index.

Sibling of the file-EXPORT connector ``openhealth.connectors.oura``: that one
reads a CSV/JSON the member downloads from the Membership Hub; this one talks to
the Oura Cloud API directly over OAuth2 and writes the same Observation shapes
into the SQLite index, the way the WHOOP and Withings live connectors do.

Clean-room implementation from the public ``cloud.ouraring.com`` / Oura API V2
documentation. Pure stdlib — nothing leaves the machine except the calls to
Oura's own endpoints.

OAuth2 (textbook, unlike Withings):
  * Authorize  GET  https://cloud.ouraring.com/oauth/authorize
  * Token      POST https://api.ouraring.com/oauth/token  (form-encoded)
               grant_type=authorization_code | refresh_token
  * Data       GET  https://api.ouraring.com/v2/usercollection/{collection}
               Bearer token; ``start_date`` / ``end_date`` (YYYY-MM-DD) date
               filters; paginate via ``next_token``.

Tokens persist as JSON under ``data/index/oura_tokens.json`` (gitignored, like
``whoop_tokens.json``). The access token auto-refreshes — and the refreshed pair
is saved back — when it is within five minutes of expiry, exactly like WHOOP.

Client credentials come from the environment (the repo reserves the names):
``OPENHEALTH_OURA_CLIENT_ID`` / ``OPENHEALTH_OURA_CLIENT_SECRET`` /
``OPENHEALTH_OURA_REDIRECT_URI`` (default ``http://localhost:8765/callback``) and
optional ``OPENHEALTH_OURA_SCOPES``.

Entry points (mirroring whoop.py):
    load_credentials_from_env() -> OuraCredentials
    build_authorization_url(credentials, state) -> str
    exchange_code_for_tokens(credentials, code) -> dict   (saved by the CLI)
    extract_code_from_redirect_url(url, expected_state) -> dict
    refresh_tokens(credentials, refresh_token) -> dict
    ensure_valid_tokens(path, credentials) -> dict
    OuraClient(credentials, tokens).list_*(start, end) -> list[pages]
    sync_oura(root, start, end, days_back=30, ...) -> dict (JSON summary)
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .. import index
from ..models import Observation, SourceManifest
from ..storage import ensure_repo_structure, now_utc, slugify, write_json
from . import oura as oura_export

AUTHORIZATION_URL = "https://cloud.ouraring.com/oauth/authorize"
# Oura migrated OAuth to its Ory-backed identity server: the redirect carries
# iss=moi.ouraring.com/oauth/v2/... and the token endpoint lives there now. The
# legacy api.ouraring.com/oauth/token returns 400 invalid_request. Confirmed via
# the issuer's .well-known/openid-configuration. The DATA API (API_BASE_URL)
# stays on api.ouraring.com/v2 with the Bearer token.
TOKEN_URL = "https://moi.ouraring.com/oauth/v2/ext/oauth-token"
API_BASE_URL = "https://api.ouraring.com/v2/usercollection"
OURA_SOURCE_ID = "oura-live"

# Oura's documented OAuth scopes for the collections we pull. ``personal`` covers
# profile, ``daily`` the daily_* summaries, ``heartrate`` the heartrate series,
# ``workout`` the workouts. Space-separated at the authorize step.
DEFAULT_SCOPES = ("personal", "daily", "heartrate", "workout")

ENV_CLIENT_ID = "OPENHEALTH_OURA_CLIENT_ID"
ENV_CLIENT_SECRET = "OPENHEALTH_OURA_CLIENT_SECRET"
ENV_REDIRECT_URI = "OPENHEALTH_OURA_REDIRECT_URI"
ENV_SCOPES = "OPENHEALTH_OURA_SCOPES"
DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"

# Refresh the access token when it expires within this slack window.
_EXPIRY_SLACK = timedelta(minutes=5)

# Oura scores are 0-100 indices; raw physiological signals (hr/hrv) are measured
# directly, so they carry slightly higher confidence — same split as oura.py.
CONF_RAW = oura_export.CONF_RAW
CONF_SCORE = oura_export.CONF_SCORE

CAPABILITIES = {
    "source": "Oura Cloud API v2",
    "collections": {
        "daily_readiness": {
            "scope": "daily",
            "description": "Daily readiness score and contributors (maps to recovery).",
            "endpoint": "/daily_readiness",
        },
        "daily_sleep": {
            "scope": "daily",
            "description": "Daily sleep score and contributors (sleep performance).",
            "endpoint": "/daily_sleep",
        },
        "daily_activity": {
            "scope": "daily",
            "description": "Daily activity score, steps, and energy.",
            "endpoint": "/daily_activity",
        },
        "sleep": {
            "scope": "daily",
            "description": "Per-night sleep periods: durations, HRV (rmssd), heart rate, respiratory rate.",
            "endpoint": "/sleep",
        },
        "daily_spo2": {
            "scope": "spo2",
            "description": "Daily average blood oxygen (SpO2) percentage. Needs the separate 'spo2' OAuth scope.",
            "endpoint": "/daily_spo2",
        },
        "workout": {
            "scope": "workout",
            "description": "Workout sessions with activity, intensity, and energy.",
            "endpoint": "/workout",
        },
    },
    "notes": [
        "heartrate (raw 5-min samples) uses start_datetime/end_datetime and is not pulled by default.",
        "Oura has no single 'strain' metric; the dashboard strain tile stays WHOOP-driven.",
    ],
}


@dataclass
class OuraCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: Tuple[str, ...] = DEFAULT_SCOPES


class OuraApiError(RuntimeError):
    """Raised when Oura returns an unexpected response or is misconfigured."""


# --------------------------------------------------------------------------- #
# Canonical metric vocabulary
#
# Where an Oura metric overlaps a WHOOP one the dashboard already reads, we emit
# the SAME metric_name (recovery_score, hrv_rmssd_milli, resting_heart_rate,
# sleep_performance_percentage) so Oura data lights up the existing recovery
# block. Oura-only metrics keep their oura.py names and are still stored (tagged)
# for the agent/timeline.
#
# spec: metric_name -> (observation_kind, unit, domain, confidence)
# --------------------------------------------------------------------------- #
_METRIC_SPEC: Dict[str, Tuple[str, str, str, float]] = {
    # Dashboard-overlapping (shared with WHOOP) -----------------------------
    "recovery_score": ("recovery_score", "%", "recovery", CONF_SCORE),
    "hrv_rmssd_milli": ("hrv", "ms", "pulse", CONF_RAW),
    "resting_heart_rate": ("resting_hr", "bpm", "pulse", CONF_RAW),
    "sleep_performance_percentage": ("sleep_score", "%", "sleep", CONF_SCORE),
    "sleep_duration_h": ("sleep_session", "h", "sleep", CONF_RAW),
    # Oura-only (stored + tagged, not on the WHOOP dashboard tiles) ----------
    "sleep_deep_h": ("sleep_session", "h", "sleep", CONF_RAW),
    "sleep_rem_h": ("sleep_session", "h", "sleep", CONF_RAW),
    "sleep_light_h": ("sleep_session", "h", "sleep", CONF_RAW),
    "sleep_latency_min": ("sleep_session", "min", "sleep", CONF_RAW),
    "sleep_efficiency": ("sleep_session", "%", "sleep", CONF_RAW),
    "heart_rate_avg_bpm": ("heart_rate", "bpm", "pulse", CONF_RAW),
    "respiratory_rate_rpm": ("respiratory_rate", "rpm", "pulse", CONF_RAW),
    "body_temp_delta_c": ("body_temperature", "c", "pulse", CONF_RAW),
    "readiness_score": ("readiness_score", "score", "recovery", CONF_SCORE),
    "activity_score": ("activity_score", "score", "body", CONF_SCORE),
    "steps": ("steps", "count", "body", CONF_RAW),
    "active_energy_kcal": ("active_energy", "kcal", "body", CONF_RAW),
    "total_energy_kcal": ("total_energy", "kcal", "body", CONF_RAW),
    "spo2_pct": ("spo2", "%", "pulse", CONF_RAW),
}

# Per-collection field aliases -> (canonical_metric, transform). The API V2 day
# summaries reuse the same field names the export connector already maps, so we
# lean on oura.py's tables where they overlap and add the live-only shapes.
_READINESS_FIELDS: Dict[str, Tuple[str, str]] = {
    # daily_readiness carries the readiness score we treat as recovery.
    "score": ("recovery_score", "ident"),
}
_DAILY_SLEEP_FIELDS: Dict[str, Tuple[str, str]] = {
    # daily_sleep is the SCORED daily summary -> sleep performance %.
    "score": ("sleep_performance_percentage", "ident"),
}
# Per-night /sleep period: durations in seconds, raw hr/hrv. Reuse oura.py's
# alias table (it already knows total_sleep_duration, rmssd, average_heart_rate…)
# and add the V2-specific keys the export shape did not include.
_SLEEP_PERIOD_FIELDS: Dict[str, Tuple[str, str]] = dict(oura_export._SLEEP_FIELDS)
_SLEEP_PERIOD_FIELDS.update(
    {
        "average_hrv": ("hrv_rmssd_milli", "ident"),
        "rmssd": ("hrv_rmssd_milli", "ident"),
        "average_heart_rate": ("heart_rate_avg_bpm", "ident"),
        "lowest_heart_rate": ("resting_heart_rate", "ident"),
        "efficiency": ("sleep_efficiency", "ident"),
        "latency": ("sleep_latency_min", "sec_to_min"),
    }
)
_ACTIVITY_FIELDS: Dict[str, Tuple[str, str]] = {
    "score": ("activity_score", "ident"),
    "steps": ("steps", "ident"),
    "active_calories": ("active_energy_kcal", "ident"),
    "total_calories": ("total_energy_kcal", "ident"),
}
_SPO2_FIELDS: Dict[str, Tuple[str, str]] = {
    # daily_spo2 nests the value as {"spo2_percentage": {"average": 97.0}}.
    "spo2_percentage": ("spo2_pct", "spo2_avg"),
    "average": ("spo2_pct", "ident"),
}

# How each collection is mapped at sync time.
# name -> (endpoint, field_map, observation_kind_label, default_score_metric)
_COLLECTIONS: Dict[str, Tuple[str, Dict[str, Tuple[str, str]], str]] = {
    "daily_readiness": ("/daily_readiness", _READINESS_FIELDS, "oura_readiness"),
    "daily_sleep": ("/daily_sleep", _DAILY_SLEEP_FIELDS, "oura_daily_sleep"),
    "daily_activity": ("/daily_activity", _ACTIVITY_FIELDS, "oura_activity"),
    "sleep": ("/sleep", _SLEEP_PERIOD_FIELDS, "oura_sleep_period"),
    "daily_spo2": ("/daily_spo2", _SPO2_FIELDS, "oura_spo2"),
}


# --------------------------------------------------------------------------- #
# Config / env
# --------------------------------------------------------------------------- #


def parse_scopes(raw: Optional[str]) -> Tuple[str, ...]:
    """Parse a comma- or space-separated scope string into a tuple."""
    if not raw:
        return ()
    return tuple(token for token in raw.replace(",", " ").split() if token)


def load_credentials_from_env() -> OuraCredentials:
    client_id = os.getenv(ENV_CLIENT_ID)
    client_secret = os.getenv(ENV_CLIENT_SECRET)
    missing = [
        name
        for name, value in ((ENV_CLIENT_ID, client_id), (ENV_CLIENT_SECRET, client_secret))
        if not value
    ]
    if missing:
        raise OuraApiError("Missing Oura credentials in environment: %s" % ", ".join(missing))
    redirect_uri = os.getenv(ENV_REDIRECT_URI) or DEFAULT_REDIRECT_URI
    scopes = parse_scopes(os.getenv(ENV_SCOPES)) or DEFAULT_SCOPES
    return OuraCredentials(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scopes=scopes,
    )


# --------------------------------------------------------------------------- #
# OAuth2: authorize URL, code exchange, refresh
# --------------------------------------------------------------------------- #


def build_authorization_url(credentials: OuraCredentials, state: str) -> str:
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


def exchange_code_for_tokens(credentials: OuraCredentials, code: str) -> Dict[str, Any]:
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
        raise OuraApiError("Oura returned an OAuth error: %s %s" % (error, error_description or ""))
    if not code:
        raise OuraApiError("Redirect URL did not include an OAuth code.")
    if expected_state and state != expected_state:
        raise OuraApiError("OAuth state mismatch. Expected %s, received %s." % (expected_state, state))
    return {"code": code, "state": state, "redirect_uri": parsed.geturl()}


def refresh_tokens(credentials: OuraCredentials, refresh_token: str) -> Dict[str, Any]:
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
    }
    response = _post_form(TOKEN_URL, payload)
    return _normalize_token_response(response, refresh_token=refresh_token)


def save_tokens(path: Path, payload: Dict[str, Any]) -> None:
    write_json(path, payload)


def load_tokens(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise OuraApiError("Oura token file not found at %s. Run oura-exchange-code first." % path)
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_valid_tokens(path: Path, credentials: OuraCredentials) -> Dict[str, Any]:
    tokens = load_tokens(path)
    expires_at = _parse_optional_iso_datetime(tokens.get("expires_at"))
    if not expires_at or expires_at - _EXPIRY_SLACK > datetime.now(timezone.utc):
        return tokens
    refreshed = refresh_tokens(credentials, tokens["refresh_token"])
    refreshed.setdefault("scope", tokens.get("scope", list(credentials.scopes)))
    save_tokens(path, refreshed)
    return refreshed


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #


class OuraClient:
    def __init__(self, credentials: OuraCredentials, tokens: Dict[str, Any]):
        self.credentials = credentials
        self.tokens = tokens

    def list_collection(self, path: str, start: Optional[str], end: Optional[str]) -> List[Dict[str, Any]]:
        next_token: Optional[str] = None
        pages: List[Dict[str, Any]] = []
        while True:
            query: Dict[str, Any] = {}
            if start:
                query["start_date"] = start
            if end:
                query["end_date"] = end
            if next_token:
                query["next_token"] = next_token
            payload = self._get(path, query)
            pages.append(payload)
            next_token = payload.get("next_token")
            if not next_token:
                break
        return pages

    def _get(self, path: str, query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = API_BASE_URL + path
        if query:
            filtered = {key: value for key, value in query.items() if value is not None}
            if filtered:
                url += "?" + urlencode(filtered)
        headers = {
            "Authorization": "Bearer %s" % self.tokens["access_token"],
            "Accept": "application/json",
        }
        return _request_json("GET", url, headers=headers, path_hint=path)


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


def sync_oura(
    root: Path,
    start: Optional[str] = None,
    end: Optional[str] = None,
    days_back: int = 30,
    owner: str = "user",
    collections: Optional[Iterable[str]] = None,
    client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Pull Oura collections for the window and write canonical records.

    ``start`` / ``end`` are ISO dates (YYYY-MM-DD); when ``start`` is omitted we
    look ``days_back`` days back from ``end`` (default today). Re-syncing the same
    window upserts on deterministic ids — never duplicates. Returns a JSON summary
    (coverage range, records imported, per-collection counts).
    """
    paths = ensure_repo_structure(root)
    index.init_db(paths.db_path)
    if client is None:
        credentials = load_credentials_from_env()
        tokens = ensure_valid_tokens(paths.oura_tokens_path, credentials)
        client = OuraClient(credentials, tokens)

    sync_started_at = now_utc()
    start_value, end_value = resolve_sync_window(start, end, days_back)
    wanted = list(collections) if collections is not None else list(_COLLECTIONS)

    records: List[Dict[str, Any]] = []
    raw_counts: Dict[str, int] = {}
    notes: List[str] = []

    for name in wanted:
        spec = _COLLECTIONS.get(name)
        if spec is None:
            notes.append("Unknown collection skipped: %s" % name)
            continue
        endpoint, field_map, kind_label = spec
        try:
            pages = client.list_collection(endpoint, start_value, end_value)
        except OuraApiError as exc:
            # A collection the granted token can't reach (e.g. daily_spo2 without
            # the spo2 scope) must not abort the whole sync — skip it with a note
            # so the collections that DID authorize still get saved.
            notes.append("%s skipped: %s" % (name, exc))
            raw_counts[name] = 0
            continue
        collection_records: List[Dict[str, Any]] = []
        for page in pages:
            for item in _items(page):
                collection_records.extend(_map_summary(name, item, field_map, kind_label))
            if page.get("next_token"):
                notes.append("%s returned a pagination token." % name)
        raw_counts[name] = len(collection_records)
        records.extend(collection_records)

    if not records:
        notes.append("Oura sync returned no records for the requested window.")

    # Deterministic ids mean re-sync overwrites in place; we also clear any stale
    # oura-live records that fall inside the window but were not re-emitted.
    replaced = _purge_window(paths.db_path, records, start_value, end_value)
    for record in records:
        index.upsert_record(paths.db_path, record)

    coverage_points = [r.get("date") for r in records if r.get("date")]
    coverage_start = min(coverage_points) if coverage_points else start_value
    coverage_end = max(coverage_points) if coverage_points else end_value

    source = SourceManifest(
        source_id=OURA_SOURCE_ID,
        source_type="oura",
        owner=owner,
        label="Oura Ring live sync",
        created_at=sync_started_at,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        files=[],
        parser_status="synced",
        notes=notes,
        metadata={
            "sync_window_start": start_value,
            "sync_window_end": end_value,
            "fetched_at": sync_started_at,
            "collections": raw_counts,
        },
    )
    write_json(paths.source_manifests / ("%s.json" % OURA_SOURCE_ID), source.to_dict())
    index.upsert_source(paths.db_path, source.to_dict())

    return {
        "source_id": OURA_SOURCE_ID,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "records_imported": len(records),
        "collections": raw_counts,
        "replaced_record_ids": len(replaced),
        "capabilities": CAPABILITIES,
    }


def _map_summary(
    collection: str,
    item: Dict[str, Any],
    field_map: Dict[str, Tuple[str, str]],
    kind_label: str,
) -> List[Dict[str, Any]]:
    """Map one Oura API V2 day/period object onto canonical Observation records.

    One Observation per (metric, day). The id is deterministic from the source
    object id (or the day, for day-summaries) so re-syncing upserts cleanly.
    """
    day = _summary_date(item)
    if not day:
        return []
    obj_key = str(item.get("id") or day)
    records: List[Dict[str, Any]] = []
    for raw_key, raw_value in _flatten(item).items():
        spec = field_map.get(raw_key)
        if spec is None:
            continue
        metric, how = spec
        if metric not in _METRIC_SPEC:
            continue
        fval = oura_export._to_float(raw_value)
        if fval is None:
            continue
        value = _transform(fval, how)
        kind, unit, domain, confidence = _METRIC_SPEC[metric]
        records.append(
            _obs(
                day=day,
                obj_key=obj_key,
                kind=kind,
                metric=metric,
                value=value,
                unit=unit,
                domain=domain,
                confidence=confidence,
                collection=collection,
                metadata=item,
            )
        )
    return records


def _obs(
    day: str,
    obj_key: str,
    kind: str,
    metric: str,
    value: float,
    unit: str,
    domain: str,
    confidence: float,
    collection: str,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    return Observation(
        id="obs-oura-live-%s-%s-%s" % (slugify(collection), metric, obj_key),
        record_type="Observation",
        source_id=OURA_SOURCE_ID,
        title="Oura %s (%s)" % (metric.replace("_", " "), day),
        summary="%s = %s %s" % (metric, value, unit),
        artifact_ids=[],
        evidence_class="personal",
        confidence=confidence,
        date=day,
        tags=["oura", "oura-live", domain, collection],
        metadata={"connector": "oura-live", "source": "oura", "collection": collection, "raw": metadata},
        observation_kind=kind,
        metric_name=metric,
        value=value,
        unit=unit,
    ).to_dict()


def _purge_window(db_path: Path, new_records: List[Dict[str, Any]], start: str, end: str) -> List[str]:
    """Drop stale oura-live records in the window so a re-sync is idempotent."""
    existing = index.list_records_by_source(db_path, OURA_SOURCE_ID)
    keep_ids = {record["id"] for record in new_records}
    to_delete: List[str] = []
    for record in existing:
        if record["id"] in keep_ids:
            continue
        record_date = record.get("date")
        if record_date and start[:10] <= record_date <= end[:10]:
            to_delete.append(record["id"])
    index.delete_records_by_ids(db_path, to_delete)
    return to_delete


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _transform(value: float, how: str) -> float:
    if how == "sec_to_h":
        return round(value / 3600.0, 2)
    if how == "sec_to_min":
        return round(value / 60.0, 1)
    return round(value, 3)


def _summary_date(item: Dict[str, Any]) -> Optional[str]:
    for key in ("day", "summary_date", "bedtime_start", "timestamp", "date"):
        if item.get(key):
            parsed = oura_export._parse_date(str(item[key]))
            if parsed:
                return parsed
    return None


def _flatten(item: Dict[str, Any]) -> Dict[str, Any]:
    """One-level flatten: surface the leaves of nested objects (e.g. daily_spo2's
    ``spo2_percentage: {average: 97}``) so the flat field maps can find them.

    Top-level scalars win over nested ones on key collision, and ``contributors``
    sub-scores are deliberately not promoted (they would shadow the headline
    ``score``)."""
    flat: Dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(value, dict):
            if key == "contributors":
                continue
            for sub_key, sub_value in value.items():
                flat.setdefault(sub_key, sub_value)
            # keep the container too, for spo2_percentage-style nested maps
            flat.setdefault(key, value)
        else:
            flat[key] = value
    return flat


def _items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def resolve_sync_window(start: Optional[str], end: Optional[str], days_back: int) -> Tuple[str, str]:
    end_date = _to_date(end) if end else datetime.now(timezone.utc).date()
    if start:
        start_date = _to_date(start)
    else:
        start_date = end_date - timedelta(days=days_back)
    return start_date.isoformat(), end_date.isoformat()


def _to_date(value: str):
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        raise OuraApiError("Cannot parse date %r (expected YYYY-MM-DD)." % (value,)) from None


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Quick rollup for the agent to read back: metrics found and date span."""
    metrics: Dict[str, int] = {}
    days = set()
    for r in records:
        metrics[r["metric_name"]] = metrics.get(r["metric_name"], 0) + 1
        if r.get("date"):
            days.add(r["date"])
    return {
        "source": OURA_SOURCE_ID,
        "total_records": len(records),
        "metrics": dict(sorted(metrics.items())),
        "days_covered": len(days),
        "date_from": min(days) if days else None,
        "date_to": max(days) if days else None,
    }


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
    if "access_token" not in payload:
        raise OuraApiError("Oura token response is missing access_token: %s" % sorted(payload))
    expires_in = int(payload.get("expires_in", 86400))
    expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=expires_in)
    scope = payload.get("scope")
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", refresh_token),
        "token_type": payload.get("token_type", "Bearer"),
        "scope": scope.split() if isinstance(scope, str) else scope,
        "expires_at": expires_at.isoformat(),
        "raw": payload,
    }


def _parse_optional_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
    request = Request(url, data=data, headers=dict(headers or {}), method=method)
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise OuraApiError("Oura %s failed: HTTP %s %s" % (path_hint or method, exc.code, detail)) from None
    except URLError as exc:
        raise OuraApiError("Oura %s failed: %s" % (path_hint or method, exc.reason)) from None
