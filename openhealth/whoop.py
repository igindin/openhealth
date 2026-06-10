import base64
import hashlib
import hmac
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import URLError
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from . import index
from .contexts import build_source_brief, refresh_contexts
from .models import ArtifactManifest, ContextNote, Observation, SourceManifest, TimelineEvent
from .storage import ensure_repo_structure, now_utc, sha256sum, slugify, write_json, write_text


AUTHORIZATION_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE_URL = "https://api.prod.whoop.com/developer/v2"
WHOOP_SOURCE_ID = "whoop-live"
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
    return WhoopCredentials(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)


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
    }
    response = _post_form(TOKEN_URL, payload)
    return _normalize_token_response(response, refresh_token=refresh_token)


def save_tokens(path: Path, payload: Dict[str, Any]) -> None:
    write_json(path, payload)


def load_tokens(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise WhoopApiError("WHOOP token file not found at %s. Run whoop-exchange-code first." % path)
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_valid_tokens(path: Path, credentials: WhoopCredentials) -> Dict[str, Any]:
    tokens = load_tokens(path)
    expires_at = _parse_iso_datetime(tokens.get("expires_at"))
    if not expires_at or expires_at - timedelta(minutes=5) > datetime.now(timezone.utc):
        return tokens
    refreshed = refresh_tokens(credentials, tokens["refresh_token"])
    refreshed.setdefault("scope", tokens.get("scope", list(credentials.scopes)))
    save_tokens(path, refreshed)
    return refreshed


class WhoopClient:
    def __init__(self, credentials: WhoopCredentials, tokens: Dict[str, Any]):
        self.credentials = credentials
        self.tokens = tokens

    def get_profile(self) -> Dict[str, Any]:
        return self._get("/user/profile/basic")

    def get_body_measurements(self) -> Dict[str, Any]:
        return self._get("/user/measurement/body")

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
    index.init_db(paths.db_path)
    credentials = None
    if client is None:
        credentials = load_credentials_from_env()
        tokens = ensure_valid_tokens(paths.whoop_tokens_path, credentials)
        client = WhoopClient(credentials, tokens)
    sync_started_at = now_utc()
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
            page_records = normalize_whoop_payload(
                dataset_name=dataset_name,
                payload=payload,
                artifact_id=artifact["artifact_id"],
                source_id=WHOOP_SOURCE_ID,
                fetched_at=sync_started_at,
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


def normalize_body_measurements(payload: Dict[str, Any], artifact_id: str, source_id: str, fetched_at: str) -> List[Dict[str, Any]]:
    measurements = payload.get("records") if isinstance(payload.get("records"), list) else [payload]
    records: List[Dict[str, Any]] = []
    for item in measurements:
        measured_at = item.get("updated_at") or item.get("created_at") or fetched_at
        date_value = measured_at[:10]
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
                    id="whoop-body-%s" % slugify(metric_name),
                    record_type="Observation",
                    source_id=source_id,
                    title="WHOOP %s" % metric_name.replace("_", " "),
                    summary="WHOOP body measurement %s synced." % metric_name,
                    artifact_ids=[artifact_id],
                    evidence_class="personal",
                    confidence=0.95,
                    captured_at=measured_at,
                    date=date_value,
                    tags=["whoop", "body-measurement"],
                    metadata=item,
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
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise WhoopApiError("WHOOP %s failed: %s %s" % (path_hint or method, exc.code, detail))
    except URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc.reason):
            raise
        return _request_json_with_curl(method, url, headers=headers, data=data, path_hint=path_hint)


def _request_json_with_curl(
    method: str,
    url: str,
    headers: Dict[str, str],
    data: Optional[bytes],
    path_hint: str,
) -> Dict[str, Any]:
    command = ["curl", "-sS", "--fail-with-body", "-X", method]
    for key, value in headers.items():
        command.extend(["-H", "%s: %s" % (key, value)])
    if data is not None:
        command.extend(["--data", data.decode("utf-8")])
    command.append(url)
    try:
        output = subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise WhoopApiError(
            "WHOOP %s failed via curl fallback: %s" % (path_hint or method, exc.stderr.strip() or exc.stdout.strip())
        )
    return json.loads(output.stdout)
