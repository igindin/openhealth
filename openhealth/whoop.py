import base64
import hashlib
import hmac
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
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
# Items per page the client asks for. WHOOP pages every collection newest
# first, so the window purge treats an answer this long or longer as one that
# may have lost its oldest page (see ``plan_whoop_purge``).
WHOOP_PAGE_LIMIT = 25
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
            query = {"limit": WHOOP_PAGE_LIMIT}
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
    # Only datasets that actually returned data are replace candidates: a
    # dataset that came back empty (an API brownout answering
    # 200-with-empty-lists) has given this run no evidence about the window,
    # and purging on no evidence deletes existing history. The profile/body
    # datasets never count — they answer independently of the window.
    purgeable_datasets = [
        name for name in _WINDOWED_DATASET_KINDS if raw_counts.get(name, 0) > 0
    ]
    # A stored record counts as stale only when its provider interval lies
    # inside the window with a margin (``plan_whoop_purge``), never by date.
    purge_plan = plan_whoop_purge(
        index.list_records_by_source(paths.db_path, WHOOP_SOURCE_ID),
        records,
        start_value,
        end_value,
        purgeable_datasets,
    )
    replaced_ids = purge_plan.ids
    index.delete_records_by_ids(paths.db_path, replaced_ids)
    unpurged = sorted(set(_WINDOWED_DATASET_KINDS) - set(purgeable_datasets))
    if unpurged:
        parser_notes.append(
            "Purge skipped for datasets without windowed data: %s." % ", ".join(unpurged)
        )
    parser_notes.extend(purge_plan.notes)
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
        # Counts only: stale records deleted per dataset that returned data;
        # stale events kept per dataset whose breaker tripped; events kept at
        # the old edge of an answer that may have lost its oldest page; and
        # metrics kept because their event came back without a score.
        "stale_purged": dict(purge_plan.stale_purged),
        "purge_suppressed": dict(purge_plan.purge_suppressed),
        "old_edge_kept": dict(purge_plan.old_edge_kept),
        "unscored_kept": dict(purge_plan.unscored_kept),
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


_WINDOWED_DATASET_KINDS = {
    "cycles": ("whoop_cycle", "whoop_cycle_metric"),
    "recoveries": ("whoop_recovery", "whoop_recovery_metric"),
    "sleeps": ("whoop_sleep", "whoop_sleep_metric"),
    "workouts": ("whoop_workout", "whoop_workout_metric"),
}


# A stored record counts as stale only when its whole provider interval lies at
# least this far inside the fetched window, at both edges, because:
# - WHOOP revises item boundaries for days after an item ends (sleep ends have
#   moved up to about 5 h later, a workout's start 15 min earlier), so a stored
#   interval can be hours off the provider's current one;
# - a recovery's created_at can lag the end of its sleep by up to about 6 h;
# - a record wholly inside the window by this margin is returned under every
#   plausible filter model (interval overlap, start point, end point), so the
#   decision holds even if WHOOP changes how it filters.
WHOOP_PURGE_MARGIN = timedelta(hours=6)
# Safety breaker against a well-formed answer that lost many items. Per
# dataset: when more of its stored events inside the margin window are missing
# (those the old-edge guard keeps included, see WHOOP_PAGE_LIMIT) than this
# many, and than this percentage of those events rounded up, the run deletes
# none of that dataset's interval-stale records.
WHOOP_PURGE_BREAKER_MIN_EVENTS = 3
WHOOP_PURGE_BREAKER_PERCENT = 10

# Where each windowed kind's provider interval comes from. A recovery has no
# interval of its own: WHOOP returns a recovery exactly when it returns the
# recovery's sleep, so recovery events and metrics borrow that sleep's interval
# (or, with no sleep on record, the enclosing cycle's, which contains it).
_PURGE_INTERVAL_SOURCE = {
    "whoop_cycle": "item",
    "whoop_cycle_metric": "item",
    "whoop_sleep": "item",
    "whoop_sleep_metric": "item",
    "whoop_workout": "item",
    "whoop_workout_metric": "item",
    "whoop_recovery": "linked_sleep",
    "whoop_recovery_metric": "linked_sleep",
}

# The parent event of each metric kind: its id pattern, and the raw-item fields
# the normalizers build the id from, in their order (``str(first or second)``).
_PURGE_PARENT_ID = {
    "whoop_cycle_metric": ("whoop-cycle-%s", ("id", "cycle_id")),
    "whoop_recovery_metric": ("whoop-recovery-%s", ("cycle_id", "id")),
    "whoop_sleep_metric": ("whoop-sleep-%s", ("id", "sleep_id")),
    "whoop_workout_metric": ("whoop-workout-%s", ("id", "workout_id")),
}

# Event kinds a recovery links to, looked up by record id.
_PURGE_LINK_KINDS = ("whoop_sleep", "whoop_cycle")

_Interval = Tuple[datetime, datetime]


@dataclass(frozen=True)
class WhoopPurgePlan:
    """What one WHOOP window re-sync deletes, with per-dataset counts only.

    ``ids`` is sorted: every re-emitted id (rewritten in place) plus each stale
    id. ``stale_purged`` counts the stale records (events and metrics) deleted
    in each dataset that returned data. ``purge_suppressed`` counts, for each
    dataset whose breaker tripped, the stale events kept. ``old_edge_kept``
    counts, per dataset, the events (their metrics go with them) kept because
    they start before, or within the margin after, the oldest item of an
    answer that may have lost its oldest page. ``unscored_kept`` counts, per
    dataset, the metrics kept because their event came back without any
    metric. The last three list only datasets with something kept, and
    ``notes`` explains each of them for the source manifest.
    """

    ids: List[str]
    stale_purged: Dict[str, int]
    purge_suppressed: Dict[str, int]
    notes: List[str]
    old_edge_kept: Dict[str, int]
    unscored_kept: Dict[str, int]


def plan_whoop_purge(
    existing_records: Iterable[Dict[str, Any]],
    new_records: List[Dict[str, Any]],
    start: str,
    end: str,
    datasets: Iterable[str],
) -> WhoopPurgePlan:
    """Plan which WHOOP records a re-sync of ``start``..``end`` replaces.

    Pure: it reads only its arguments and writes nothing. The caller deletes
    ``ids`` and then upserts ``new_records`` (``sync_whoop`` does; so does
    ``purge_existing_whoop_records`` for direct callers).

    Every id in ``new_records`` is replaced, because it is rewritten. An existing
    record that was not re-emitted is deleted as stale only when

    - its dataset is in ``datasets``, the windowed datasets that returned data
      this run: an empty answer (an API brownout's 200 with empty lists) is no
      evidence about the window, so that dataset keeps its history. It is
      required rather than defaulted, because the only sensible default,
      "every dataset", turns a brownout into data loss. Body measurements and
      the profile are never candidates, since no window answers for them; and
    - either its parent event was re-emitted without it (the fresh item no
      longer carries that metric), whatever its interval, or its whole provider
      interval lies inside the window with ``WHOOP_PURGE_MARGIN`` to spare:
      ``start + margin <= interval start`` and ``interval end <= end - margin``.

    WHOOP filters on timestamps, never on dates: it returns an item when the
    item's interval overlaps the window (an open item always), and a recovery
    exactly when it returns the recovery's sleep. Cycles, sleeps and workouts,
    events and metrics alike, take their interval from the raw item's start and
    end. Recoveries take their linked sleep's (this run's version first, then
    the stored one), or their cycle's when no sleep is on record. A missing,
    open or unparseable boundary makes the interval unknown, and an unknown
    interval keeps the record. The attributed date never decides.

    The trade-off is deliberate. WHOOP's own overlap rule would also delete an
    item it dropped near a window edge; the margin keeps such an item until a
    later window covers it with room to spare, which for the leading edge of a
    scheduled window is never (a backfill with an earlier ``--start`` reaches
    it). In exchange a record the provider still has, merely absent because it
    ended before the window or because its boundaries moved, is never deleted.

    Two guards keep what an answer cannot speak for:

    - The old edge. WHOOP pages each collection newest first, so an answer of
      ``WHOOP_PAGE_LIMIT`` or more events may have lost its oldest page without
      a sign. In such an answer an event that starts before, or within
      ``WHOOP_PURGE_MARGIN`` after, the oldest returned event's start (a
      recovery by its sleep, else its cycle, as above) is kept with its
      metrics, because a stored start can lag a revised one; so is every
      missing event when no returned event has a known interval. A shorter
      answer was a single page that was not full, so it reached the end of the
      collection.
    - The unscored event. A re-emitted event that normalized to no metric at
      all (WHOOP is re-scoring it, gave up on it, or answered without scores)
      says nothing about its stored metrics: they are kept whatever their
      interval. An event that came back with any metric retires the ones it
      no longer carries.

    A per-dataset breaker guards against a well-formed answer that lost many
    items: when more of a dataset's stored events inside the margin window are
    missing (those kept at the old edge included) than
    ``max(WHOOP_PURGE_BREAKER_MIN_EVENTS, ceil(WHOOP_PURGE_BREAKER_PERCENT
    percent of them))``, none of that dataset's interval-stale records goes
    (metrics retired by a re-emitted parent with metrics still do) and the
    plan reports it. The guards therefore only ever keep more than the
    interval rule and breaker alone would.
    """
    window_start = _parse_iso_datetime(start)
    window_end = _parse_iso_datetime(end)
    margin_window: Optional[_Interval]
    try:
        margin_window = (window_start + WHOOP_PURGE_MARGIN, window_end - WHOOP_PURGE_MARGIN)
    except OverflowError:
        # The window opens less than the margin before the latest representable
        # instant, or closes less than the margin after the earliest: no instant
        # lies the margin inside it, so nothing is interval-stale.
        margin_window = None
    # Either argument may be a one-shot iterable: read each exactly once.
    existing = list(existing_records)
    fresh = list(new_records)
    reemitted = {record["id"] for record in fresh}
    purgeable = sorted({name for name in datasets if name in _WINDOWED_DATASET_KINDS})
    dataset_of_kind = {kind: name for name in purgeable for kind in _WINDOWED_DATASET_KINDS[name]}
    links = _purge_link_intervals(existing, fresh)
    scored_parents = _scored_parent_ids(fresh)
    old_edges = _answer_old_edges(fresh, purgeable, links)

    def inside(interval: Optional[_Interval]) -> bool:
        if interval is None or margin_window is None:
            return False
        return margin_window[0] <= interval[0] and interval[1] <= margin_window[1]

    def beyond_answer(dataset: str, interval: _Interval) -> bool:
        if dataset not in old_edges:
            return False
        edge = old_edges[dataset][0]
        return edge is None or interval[0] <= edge

    in_window_events = dict.fromkeys(purgeable, 0)
    missing_events = dict.fromkeys(purgeable, 0)
    old_edge_kept = dict.fromkeys(purgeable, 0)
    unscored_kept = dict.fromkeys(purgeable, 0)
    stale_by_interval: Dict[str, List[str]] = {name: [] for name in purgeable}
    stale_by_parent: Dict[str, List[str]] = {name: [] for name in purgeable}
    for record in existing:
        record_id = record.get("id")
        if not isinstance(record_id, str) or _never_purged(record):
            continue
        kind = record.get("observation_kind") or record.get("event_kind")
        dataset = dataset_of_kind.get(kind)
        if dataset is None:
            continue
        is_event = kind == _WINDOWED_DATASET_KINDS[dataset][0]
        metadata = record.get("metadata")
        if record_id in reemitted:
            if is_event and inside(_purge_interval(kind, metadata, links)):
                in_window_events[dataset] += 1
            continue
        if not is_event:
            parent_id = _purge_parent_id(kind, metadata)
            if parent_id in reemitted:
                # The parent came back: the metric's absence is evidence only
                # when the parent carries a score this time.
                if parent_id in scored_parents:
                    stale_by_parent[dataset].append(record_id)
                else:
                    unscored_kept[dataset] += 1
                continue
        interval = _purge_interval(kind, metadata, links)
        if interval is None or not inside(interval):
            continue
        if beyond_answer(dataset, interval):
            # Kept whatever the breaker decides, yet still missing: the breaker
            # weighs the whole shortfall, so this guard only ever keeps more
            # than the interval rule alone would, never lets more go.
            if is_event:
                old_edge_kept[dataset] += 1
                in_window_events[dataset] += 1
                missing_events[dataset] += 1
            continue
        if is_event:
            in_window_events[dataset] += 1
            missing_events[dataset] += 1
        stale_by_interval[dataset].append(record_id)

    selected = set(reemitted)
    stale_purged: Dict[str, int] = {}
    purge_suppressed: Dict[str, int] = {}
    notes: List[str] = []
    for name in purgeable:
        stale = list(stale_by_parent[name])
        limit = _purge_breaker_limit(in_window_events[name])
        if missing_events[name] > limit:
            purge_suppressed[name] = missing_events[name]
            notes.append(
                "Purge suppressed for %s: %d of %d stored events inside the window were missing "
                "from the answer (limit %d); kept them and their metrics."
                % (name, missing_events[name], in_window_events[name], limit)
            )
        else:
            stale.extend(stale_by_interval[name])
        if old_edge_kept[name]:
            edge, answered = old_edges[name]
            if edge is None:
                notes.append(
                    "Old edge kept for %s: none of the %d items in the answer has a known start and end, "
                    "so %d stored events missing from it were kept with their metrics."
                    % (name, answered, old_edge_kept[name])
                )
            else:
                notes.append(
                    "Old edge kept for %s: %d stored events start before, or within %d h after, the oldest "
                    "of the %d items in the answer, which may have been cut short there; kept them and "
                    "their metrics."
                    % (name, old_edge_kept[name], WHOOP_PURGE_MARGIN // timedelta(hours=1), answered)
                )
        if unscored_kept[name]:
            notes.append(
                "Unscored events for %s: %d stored metrics belong to events that came back without any "
                "metric; kept them." % (name, unscored_kept[name])
            )
        stale_purged[name] = len(stale)
        selected.update(stale)
    return WhoopPurgePlan(
        ids=sorted(selected),
        stale_purged=stale_purged,
        purge_suppressed=purge_suppressed,
        notes=notes,
        old_edge_kept={name: count for name, count in old_edge_kept.items() if count},
        unscored_kept={name: count for name, count in unscored_kept.items() if count},
    )


def _scored_parent_ids(new_records: List[Dict[str, Any]]) -> Set[str]:
    """The events this run re-emitted with at least one metric of their own."""
    parents: Set[str] = set()
    for record in new_records:
        parent_id = _purge_parent_id(record.get("observation_kind"), record.get("metadata"))
        if parent_id is not None:
            parents.add(parent_id)
    return parents


def _answer_old_edges(
    new_records: List[Dict[str, Any]],
    purgeable: List[str],
    links: Dict[str, Optional[_Interval]],
) -> Dict[str, Tuple[Optional[datetime], int]]:
    """For each dataset whose answer holds a full page of events or more: how
    far its old edge reaches, and how many events it returned.

    The edge is the earliest start among the returned events plus
    ``WHOOP_PURGE_MARGIN``: a stored start can lag the provider's revised one,
    so an item on a lost page may look newer than the oldest returned item. It
    is None when no returned event has a known interval (start and end).
    """
    dataset_of_event = {_WINDOWED_DATASET_KINDS[name][0]: name for name in purgeable}
    answered: Dict[str, int] = {}
    oldest: Dict[str, datetime] = {}
    for record in new_records:
        kind = record.get("event_kind")
        dataset = dataset_of_event.get(kind)
        if dataset is None:
            continue
        answered[dataset] = answered.get(dataset, 0) + 1
        interval = _purge_interval(kind, record.get("metadata"), links)
        if interval is not None and (dataset not in oldest or interval[0] < oldest[dataset]):
            oldest[dataset] = interval[0]
    edges: Dict[str, Tuple[Optional[datetime], int]] = {}
    for name, count in answered.items():
        if count < WHOOP_PAGE_LIMIT:
            continue
        edge: Optional[datetime] = None
        if name in oldest:
            try:
                edge = oldest[name] + WHOOP_PURGE_MARGIN
            except OverflowError:
                # Within the margin of the latest instant: everything is older.
                edge = datetime.max.replace(tzinfo=timezone.utc)
        edges[name] = (edge, count)
    return edges


def _purge_breaker_limit(in_window_events: int) -> int:
    """The most missing in-window events a dataset tolerates in one answer."""
    percent_limit = (in_window_events * WHOOP_PURGE_BREAKER_PERCENT + 99) // 100
    return max(WHOOP_PURGE_BREAKER_MIN_EVENTS, percent_limit)


def _never_purged(record: Dict[str, Any]) -> bool:
    """Records no window answers for: body measurements and the profile.

    Both answer independently of the window, and their deterministic ids
    overwrite in place on upsert anyway.
    """
    return (
        record.get("observation_kind") == "whoop_body_measurement"
        or record.get("note_kind") == "whoop_profile"
    )


def _purge_link_intervals(
    existing: List[Dict[str, Any]],
    new_records: List[Dict[str, Any]],
) -> Dict[str, Optional[_Interval]]:
    """Every sleep and cycle event's interval by record id; this run's version wins."""
    intervals: Dict[str, Optional[_Interval]] = {}
    for records in (existing, new_records):
        for record in records:
            record_id = record.get("id")
            if isinstance(record_id, str) and record.get("event_kind") in _PURGE_LINK_KINDS:
                intervals[record_id] = _item_interval(record.get("metadata"))
    return intervals


def _purge_interval(
    kind: Optional[str],
    metadata: Any,
    links: Dict[str, Optional[_Interval]],
) -> Optional[_Interval]:
    """A stored windowed record's provider interval, or None when it is unknown."""
    source = _PURGE_INTERVAL_SOURCE.get(kind or "")
    if source == "item":
        return _item_interval(metadata)
    if source == "linked_sleep":
        return _recovery_interval(metadata, links)
    return None


def _recovery_interval(metadata: Any, links: Dict[str, Optional[_Interval]]) -> Optional[_Interval]:
    """A recovery's interval: its sleep's when that sleep is on record, else its cycle's.

    A sleep on record decides even when its own interval is unknown; the cycle
    stands in only for a sleep nobody has seen.
    """
    if not isinstance(metadata, dict):
        return None
    sleep_key = _provider_key(metadata.get("sleep_id"))
    if sleep_key is not None:
        sleep_id = "whoop-sleep-%s" % sleep_key
        if sleep_id in links:
            return links[sleep_id]
    cycle_key = _provider_key(metadata.get("cycle_id"))
    if cycle_key is None:
        return None
    return links.get("whoop-cycle-%s" % cycle_key)


def _purge_parent_id(kind: Optional[str], metadata: Any) -> Optional[str]:
    """The event id a metric observation belongs to, derived from its raw item
    exactly as the normalizers derive it; None without a usable item id."""
    rule = _PURGE_PARENT_ID.get(kind or "")
    if rule is None or not isinstance(metadata, dict):
        return None
    pattern, (first, second) = rule
    value = metadata.get(first) or metadata.get(second)
    if value is None or value == "":
        return None
    return pattern % str(value)


def _provider_key(value: Any) -> Optional[str]:
    """A linked provider id (an int or a UUID string) as record ids render it."""
    if isinstance(value, bool) or not isinstance(value, (int, str)) or value == "":
        return None
    return str(value)


def _item_interval(metadata: Any) -> Optional[_Interval]:
    """A raw item's [start, end], read as the normalizers read it; None when unknown."""
    if not isinstance(metadata, dict):
        return None
    lo = _provider_instant(metadata.get("start") or metadata.get("start_time"))
    hi = _provider_instant(metadata.get("end") or metadata.get("end_time"))
    if lo is None or hi is None or hi < lo:
        return None
    return lo, hi


def _provider_instant(value: Any) -> Optional[datetime]:
    """One provider timestamp as an aware UTC instant, or None.

    Accepts strings only, with ``Z`` or an explicit offset and optional
    fractional seconds; a naive timestamp is UTC, as in ``_parse_iso_datetime``.
    A bare date names no instant, with or without a zone designator: the date
    must be followed by ``T``, ``t`` or a space and then a time, because
    ``datetime.fromisoformat`` takes any character after the date as the
    separator and would read ``2026-07-27+02:00`` as 02:00 that day. Anything
    else, unparseable or out of range, is unknown rather than an error.
    Instants compare correctly where strings do not: ``...:39.500Z`` sorts
    before ``...:39Z`` as text.
    """
    date_length = len("YYYY-MM-DD")
    if not isinstance(value, str) or len(value) <= date_length or value[date_length] not in "Tt ":
        return None
    try:
        return _parse_iso_datetime(value)
    except (TypeError, ValueError, OverflowError, WhoopApiError):
        return None


def purge_existing_whoop_records(
    db_path: Path,
    new_records: List[Dict[str, Any]],
    start: str,
    end: str,
    datasets: Iterable[str],
) -> List[str]:
    """Delete the WHOOP records a re-sync of ``start``..``end`` replaces.

    Kept for direct callers; the rules live in ``plan_whoop_purge`` (stale only
    when the record's provider interval lies inside the window with a margin,
    unknown keeps, the old-edge and unscored-event guards and the per-dataset
    breaker apply). ``sync_whoop`` plans the purge itself, so that it can report
    the plan's counts and notes. Returns the deleted ids, sorted.
    """
    to_delete = plan_whoop_purge(
        index.list_records_by_source(db_path, WHOOP_SOURCE_ID),
        new_records,
        start,
        end,
        datasets,
    ).ids
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
