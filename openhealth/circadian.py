import hashlib
import math
from datetime import date as date_class
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Union

from . import index
from .connectors.google_calendar import ensure_derived_calendar, load_google_calendar_config
from .contexts import refresh_contexts
from .environment import EnvironmentService
from .models import InsightHypothesis, Observation, SourceManifest, TimelineEvent
from .storage import ensure_repo_structure, now_utc, write_json
from .whoop import WHOOP_SOURCE_ID

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


CIRCADIAN_SOURCE_ID = "circadian-hypothesis"
MORNING_LIGHT_SOURCE_ID = "morning-light-checkins"
PHASE_DEFINITIONS = (
    ("morning-light", "Morning Light", timedelta(minutes=0), timedelta(minutes=60)),
    ("primary-peak", "Primary Peak", timedelta(hours=2), timedelta(hours=5)),
    ("midday-dip", "Midday Dip", timedelta(hours=7), timedelta(hours=8, minutes=30)),
    ("secondary-peak", "Secondary Peak", timedelta(hours=9), timedelta(hours=12)),
)

# --- Rise-style energy schedule (two-process model of sleep regulation) ----
#
# Phase offsets follow the publicly documented Rise methodology: grogginess
# (sleep inertia) right after wake, a morning peak ~+2.5-4h, an afternoon dip
# ~+6-8h, an evening peak ~+9-11h, then wind-down and a melatonin window
# anchored to the habitual bedtime. The two-process model itself is
# established science (C3-C4); the *personal* placement of these windows is a
# fit against the sleep anchor only (C2). Accumulated sleep debt (sleep_debt@v2
# from modules.recovery) deepens/widens the dip and shortens the peaks.
ENERGY_SCHEDULE_MODEL = "two-process-rise@v1"
ENERGY_DEBT_SATURATION_H = 8.0  # debt hours at which the debt effect saturates
DEFAULT_DAY_LENGTH_H = 16.5  # fallback wake->bed span when no usable anchor
ENERGY_PHASE_INFO = {
    "grogginess": (
        "Sleep inertia",
        "Light in your eyes and a glass of water; ease into it - avoid big decisions "
        "in the first 60-90 minutes.",
        "C4",
    ),
    "morning-peak": (
        "Morning peak",
        "The best window of the day: deep work or a workout.",
        "C3",
    ),
    "afternoon-dip": (
        "Afternoon dip",
        "A walk, light tasks, a nap of up to 20 minutes; better to skip coffee after 15:00.",
        "C3",
    ),
    "evening-peak": (
        "Evening peak",
        "A second wave of productivity: creative and social tasks.",
        "C3",
    ),
    "wind-down": (
        "Wind-down",
        "Screens down, warm light, no intense exercise and no heavy food.",
        "C3",
    ),
    "melatonin-window": (
        "Melatonin window",
        "The best window for lights-out: falling asleep is easiest here.",
        "C3",
    ),
    "sleep-window": (
        "Sleep window",
        "Sleep: aim for the target ~8 hours from your habitual bedtime.",
        "C3",
    ),
}
# Point-labeling priority: melatonin window wins over the wider wind-down.
_ENERGY_PHASE_PRIORITY = (
    "melatonin-window",
    "grogginess",
    "morning-peak",
    "afternoon-dip",
    "evening-peak",
    "wind-down",
    "sleep-window",
)


def record_morning_light_checkin(
    root,
    timestamp: str,
    duration_minutes: int = 15,
    source: str = "manual",
    location: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    paths = ensure_repo_structure(root)
    index.init_db(paths.db_path)
    captured_at = _parse_datetime(timestamp)
    date_value = captured_at.date().isoformat()
    observation = Observation(
        id="obs-morning-light-%s-%s" % (date_value.replace("-", ""), captured_at.strftime("%H%M")),
        record_type="Observation",
        source_id=MORNING_LIGHT_SOURCE_ID,
        title="Morning light check-in",
        summary="Manual morning light exposure recorded for %s minute(s)." % duration_minutes,
        artifact_ids=[],
        evidence_class="personal",
        confidence=0.86,
        captured_at=captured_at.astimezone(timezone.utc).isoformat(),
        date=date_value,
        location=location,
        tags=["circadian", "morning-light", source],
        metadata={
            "source": source,
            "duration_minutes": duration_minutes,
            "notes": notes,
            "timestamp": captured_at.astimezone(timezone.utc).isoformat(),
        },
        observation_kind="morning_light_exposure",
        metric_name="duration_minutes",
        value=duration_minutes,
        unit="minutes",
    )
    event = TimelineEvent(
        id="event-morning-light-%s-%s" % (date_value.replace("-", ""), captured_at.strftime("%H%M")),
        record_type="TimelineEvent",
        source_id=MORNING_LIGHT_SOURCE_ID,
        title="Morning light exposure",
        summary="Morning light exposure captured via `%s`." % source,
        artifact_ids=[],
        evidence_class="personal",
        confidence=0.82,
        captured_at=observation.captured_at,
        date=date_value,
        location=location,
        tags=["circadian", "morning-light", source],
        metadata=observation.metadata,
        event_kind="morning_light_exposure",
        related_record_ids=[observation.id],
    )
    source = SourceManifest(
        source_id=MORNING_LIGHT_SOURCE_ID,
        source_type="manual-notes",
        owner="ilya",
        label="Morning light check-ins",
        created_at=now_utc(),
        coverage_start=date_value,
        coverage_end=date_value,
        files=[],
        parser_status="manual-entry",
        notes=["Morning light entries can be created manually or via future automation hooks."],
        metadata={"storage": "local-first"},
    )
    index.upsert_source(paths.db_path, source.to_dict())
    write_json(paths.source_manifests / ("%s.json" % MORNING_LIGHT_SOURCE_ID), source.to_dict())
    index.upsert_record(paths.db_path, observation.to_dict())
    index.upsert_record(paths.db_path, event.to_dict())
    refresh_contexts(paths, index)
    return {"date": date_value, "record_id": observation.id, "duration_minutes": duration_minutes}


def build_circadian_plan(
    root,
    date_value: str,
    timezone_name: Optional[str] = None,
    location: Optional[str] = None,
    environment_service: Optional[EnvironmentService] = None,
) -> Dict[str, Any]:
    paths = ensure_repo_structure(root)
    index.init_db(paths.db_path)
    config = load_google_calendar_config(root)
    tz_name = timezone_name or config.get("timezone") or "UTC"
    tzinfo = _resolve_timezone(tz_name)
    target_date = date_class.fromisoformat(date_value)
    sleep_sessions = _load_sleep_sessions(paths.db_path, tzinfo, target_date)
    anchor = _compute_sleep_anchor(sleep_sessions)
    environment_service = environment_service or EnvironmentService(paths.environment_cache_path)
    home_location = location or ((config.get("home_location") or {}).get("label"))
    environment_payload = environment_service.daily_context(
        date_value=date_value,
        location=home_location,
        latitude=(config.get("home_location") or {}).get("latitude"),
        longitude=(config.get("home_location") or {}).get("longitude"),
        timezone_name=tz_name,
    )
    morning_light = _find_morning_light_record(paths.db_path, date_value)
    wake_dt = _combine_clock(target_date, anchor["wake_minutes"], tzinfo)
    bedtime_dt = _combine_bedtime(target_date, anchor["bed_minutes"], tzinfo)
    shift_minutes = _morning_light_shift_minutes(morning_light, wake_dt)
    phases = []
    evidence_ids = [session["record_id"] for session in sleep_sessions[:6]]
    if morning_light:
        evidence_ids.append(morning_light["id"])
    for slug, title, start_offset, end_offset in PHASE_DEFINITIONS:
        phase_start = wake_dt + start_offset + timedelta(minutes=shift_minutes)
        phase_end = wake_dt + end_offset + timedelta(minutes=shift_minutes)
        if slug == "morning-light" and environment_payload and environment_payload.get("sunrise"):
            sunrise_dt = _parse_datetime(environment_payload["sunrise"]).astimezone(tzinfo)
            if sunrise_dt > phase_start:
                phase_start = sunrise_dt
                phase_end = max(phase_end, sunrise_dt + timedelta(minutes=30))
        phases.append(
            {
                "phase": slug,
                "title": title,
                "start": phase_start,
                "end": phase_end,
            }
        )
    phases.append(
        {
            "phase": "wind-down",
            "title": "Wind-down",
            "start": bedtime_dt - timedelta(hours=2),
            "end": bedtime_dt,
        }
    )
    confidence = _circadian_confidence(len(sleep_sessions), bool(environment_payload), bool(morning_light))
    summary = _circadian_summary(date_value, confidence, anchor, morning_light, environment_payload)
    # Rise-style energy schedule keyed off the SAME anchor + light shift: the
    # accumulated sleep debt (sleep_debt@v2) over the recent non-nap nights
    # deepens the dip and trims the peaks.
    nightly_hours = [
        (session["end"] - session["start"]).total_seconds() / 3600.0
        for session in reversed(sleep_sessions)
        if not session.get("nap")
    ]
    accumulated_debt_h = _accumulated_sleep_debt(nightly_hours)
    energy = energy_schedule(
        wake_dt,
        anchor=anchor,
        sleep_debt_h=accumulated_debt_h,
        light_shift_minutes=shift_minutes,
    )
    return {
        "date": date_value,
        "timezone": tz_name,
        "confidence": confidence,
        "anchor": anchor,
        "morning_light": morning_light,
        "environment": environment_payload,
        "phases": phases,
        "energy": energy,
        "hypothesis": {
            "id": "insight-circadian-%s" % date_value,
            "title": "Circadian hypothesis for %s" % date_value,
            "summary": summary,
            "statement": summary,
            "evidence_record_ids": evidence_ids,
            "open_questions": [
                "Did actual light exposure happen near wake time?",
                "Did schedule load or travel distort the usual sleep anchor?",
            ],
        },
    }


def compute_sleep_anchor(sleep_sessions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Public wrapper over the weighted sleep anchor (single source of truth).

    ``sleep_sessions`` is a list of dicts with tz-aware ``start``/``end``
    datetimes and ``days_ago`` (0 = most recent night); naps should be
    filtered out by the caller.
    """
    return _compute_sleep_anchor(sleep_sessions)


def day_phases(
    wake_time: Union[datetime, str],
    anchor: Optional[Union[Dict[str, Any], datetime]] = None,
    sleep_debt_h: float = 0.0,
    light_shift_minutes: int = 0,
) -> List[Dict[str, Any]]:
    """Rise-style day phases for one wake->wake cycle.

    Returns ``[{phase, start_iso, end_iso, label_ru, advice_ru, confidence}]``.
    ``anchor`` is either the dict from :func:`compute_sleep_anchor` (its
    ``bed_minutes`` defines the habitual bedtime) or an explicit bedtime
    datetime; without it the bedtime falls back to wake + 16.5h.

    Debt widens/deepens the afternoon dip, stretches sleep inertia and trims
    both peaks. ``light_shift_minutes`` (see ``_morning_light_shift_minutes``)
    delays the circadian phases (peaks and dip), not the wake-bound inertia.
    """
    wake_dt = _coerce_wake(wake_time)
    bed_dt = _bed_from_anchor(wake_dt, anchor)
    factor = _debt_factor(sleep_debt_h)
    shift = timedelta(minutes=int(light_shift_minutes or 0))
    evening_peak_end = min(
        wake_dt + timedelta(hours=11.0 - 0.5 * factor) + shift,
        bed_dt - timedelta(hours=2, minutes=15),
    )
    windows = (
        ("grogginess", wake_dt, wake_dt + timedelta(hours=1.25 + 0.25 * factor)),
        (
            "morning-peak",
            wake_dt + timedelta(hours=2.5) + shift,
            wake_dt + timedelta(hours=4.0 - 0.5 * factor) + shift,
        ),
        (
            "afternoon-dip",
            wake_dt + timedelta(hours=6.0 - 0.25 * factor) + shift,
            wake_dt + timedelta(hours=8.0 + 0.5 * factor) + shift,
        ),
        ("evening-peak", wake_dt + timedelta(hours=9.0) + shift, evening_peak_end),
        ("wind-down", bed_dt - timedelta(hours=2), bed_dt),
        ("melatonin-window", bed_dt - timedelta(minutes=60), bed_dt - timedelta(minutes=30)),
        ("sleep-window", bed_dt, wake_dt + timedelta(hours=24)),
    )
    phases = []
    for slug, start, end in windows:
        label_ru, advice_ru, confidence = ENERGY_PHASE_INFO[slug]
        phases.append(
            {
                "phase": slug,
                "start_iso": start.isoformat(),
                "end_iso": end.isoformat(),
                "label_ru": label_ru,
                "advice_ru": advice_ru,
                "confidence": confidence,
            }
        )
    return phases


def energy_curve(
    wake_time: Union[datetime, str],
    sleep_debt_h: float = 0.0,
    points_per_hour: int = 4,
    anchor: Optional[Union[Dict[str, Any], datetime]] = None,
    light_shift_minutes: int = 0,
) -> List[Dict[str, Any]]:
    """Continuous 24h energy wave: ``[{t_iso, energy (0-100), phase}]``.

    Cosine segments between phase-derived control points keep the wave smooth
    (zero slope at every control point). ``24 * points_per_hour`` points cover
    one wake->wake cycle. Same anchor/debt/light inputs as :func:`day_phases`.
    """
    wake_dt = _coerce_wake(wake_time)
    bed_dt = _bed_from_anchor(wake_dt, anchor)
    factor = _debt_factor(sleep_debt_h)
    shift_h = (int(light_shift_minutes or 0)) / 60.0
    bed_offset_h = (bed_dt - wake_dt).total_seconds() / 3600.0
    nodes = _energy_nodes(bed_offset_h, factor, shift_h)
    phases = day_phases(
        wake_dt, anchor=anchor, sleep_debt_h=sleep_debt_h, light_shift_minutes=light_shift_minutes
    )
    windows = _phase_windows(phases)
    points_per_hour = max(1, int(points_per_hour))
    points = []
    for step in range(24 * points_per_hour):
        offset_h = step / points_per_hour
        point_dt = wake_dt + timedelta(hours=offset_h)
        energy = max(0.0, min(100.0, _cosine_interpolate(nodes, offset_h)))
        points.append(
            {
                "t_iso": point_dt.isoformat(),
                "energy": round(energy, 1),
                "phase": _phase_at(windows, point_dt),
            }
        )
    return points


def energy_schedule(
    wake_time: Union[datetime, str],
    anchor: Optional[Union[Dict[str, Any], datetime]] = None,
    sleep_debt_h: float = 0.0,
    points_per_hour: int = 4,
    light_shift_minutes: int = 0,
) -> Dict[str, Any]:
    """Bundle phases + curve + melatonin window from one set of inputs."""
    wake_dt = _coerce_wake(wake_time)
    bed_dt = _bed_from_anchor(wake_dt, anchor)
    phases = day_phases(
        wake_dt, anchor=anchor, sleep_debt_h=sleep_debt_h, light_shift_minutes=light_shift_minutes
    )
    curve = energy_curve(
        wake_dt,
        sleep_debt_h=sleep_debt_h,
        points_per_hour=points_per_hour,
        anchor=anchor,
        light_shift_minutes=light_shift_minutes,
    )
    melatonin = next(item for item in phases if item["phase"] == "melatonin-window")
    return {
        "model": ENERGY_SCHEDULE_MODEL,
        "wake_time": wake_dt.isoformat(),
        "bed_time": bed_dt.isoformat(),
        "sleep_debt_h": round(max(0.0, float(sleep_debt_h or 0.0)), 2),
        "light_shift_minutes": int(light_shift_minutes or 0),
        "phases": phases,
        "curve": curve,
        "melatonin_window": {"start_iso": melatonin["start_iso"], "end_iso": melatonin["end_iso"]},
        "personal_fit": "C2",
        "evidence_note": (
            "The two-process model (Borbely) and the Rise phase offsets are established "
            "science (C3-C4); the personal fit of these windows comes only from the sleep "
            "anchor and the accumulated debt (C2)."
        ),
    }


def _coerce_wake(wake_time: Union[datetime, str]) -> datetime:
    if isinstance(wake_time, datetime):
        return wake_time
    text = str(wake_time).strip()
    if len(text) <= 5 and ":" in text:
        hours, minutes = text.split(":", 1)
        return datetime.combine(date_class.today(), time(int(hours), int(minutes)))
    return _parse_datetime(text)


def _bed_from_anchor(
    wake_dt: datetime, anchor: Optional[Union[Dict[str, Any], datetime]]
) -> datetime:
    bed_dt: Optional[datetime] = None
    if isinstance(anchor, datetime):
        bed_dt = anchor
    elif isinstance(anchor, dict) and anchor.get("bed_minutes") is not None:
        bed_dt = _combine_bedtime(wake_dt.date(), int(anchor["bed_minutes"]), wake_dt.tzinfo)
    if bed_dt is None:
        return wake_dt + timedelta(hours=DEFAULT_DAY_LENGTH_H)
    day_length_h = (bed_dt - wake_dt).total_seconds() / 3600.0
    if day_length_h < 13.0 or day_length_h > 20.0:
        return wake_dt + timedelta(hours=DEFAULT_DAY_LENGTH_H)
    return bed_dt


def _debt_factor(sleep_debt_h: Any) -> float:
    try:
        debt = float(sleep_debt_h or 0.0)
    except (TypeError, ValueError):
        debt = 0.0
    return max(0.0, min(debt, ENERGY_DEBT_SATURATION_H)) / ENERGY_DEBT_SATURATION_H


def _energy_nodes(bed_offset_h: float, factor: float, shift_h: float) -> List[Any]:
    """Control points (hours-from-wake, energy 0-100) for the cosine wave."""
    wake_level = 33.0 - 6.0 * factor
    raw = [
        (0.0, wake_level),
        (3.25 + shift_h, 92.0 - 15.0 * factor),  # morning peak
        (7.0 + 0.25 * factor + shift_h, 46.0 - 18.0 * factor),  # afternoon dip
        (10.0 - 0.25 * factor + shift_h, 80.0 - 14.0 * factor),  # evening peak
        (bed_offset_h - 0.75, 30.0 - 5.0 * factor),  # melatonin window
        (bed_offset_h, 22.0),  # habitual bedtime
        ((bed_offset_h + 24.0) / 2.0, 8.0),  # mid-sleep minimum
        (24.0, wake_level),  # wraps back to wake level (continuity)
    ]
    nodes: List[Any] = [raw[0]]
    for offset_h, energy in raw[1:]:
        # Keep offsets strictly increasing even for unusual anchors.
        offset_h = min(max(offset_h, nodes[-1][0] + 0.05), 24.0)
        nodes.append((offset_h, energy))
    return nodes


def _cosine_interpolate(nodes: Sequence[Any], offset_h: float) -> float:
    for (t0, e0), (t1, e1) in zip(nodes, nodes[1:]):
        if t0 <= offset_h <= t1:
            if t1 <= t0:
                return e1
            u = (offset_h - t0) / (t1 - t0)
            return e0 + (e1 - e0) * (1.0 - math.cos(math.pi * u)) / 2.0
    return nodes[-1][1]


def _phase_windows(phases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    windows = {}
    for item in phases:
        windows[item["phase"]] = (
            _parse_datetime(item["start_iso"]),
            _parse_datetime(item["end_iso"]),
        )
    return windows


def _phase_at(windows: Dict[str, Any], point_dt: datetime) -> str:
    probe = point_dt if point_dt.tzinfo else point_dt.replace(tzinfo=timezone.utc)
    for slug in _ENERGY_PHASE_PRIORITY:
        window = windows.get(slug)
        if not window or window[0] is None or window[1] is None:
            continue
        start, end = window
        start = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)
        if start <= probe < end:
            return slug
    return "transition"


def _accumulated_sleep_debt(nightly_hours: Sequence[float]) -> float:
    """Accumulated debt via sleep_debt@v2 (modules.recovery), 0.0 on any gap."""
    if not nightly_hours:
        return 0.0
    try:
        from .modules.recovery import sleep_debt as _recovery_sleep_debt
    except Exception:  # pragma: no cover - recovery module is part of the repo
        return 0.0
    payload = _recovery_sleep_debt(nightly_hours[-1], recent_nights_h=list(nightly_hours))
    return float(payload.get("accumulated_debt_h") or 0.0)


def sync_circadian_schedule(
    root,
    start_date: str,
    end_date: str,
    client: Optional[Any] = None,
    environment_service: Optional[EnvironmentService] = None,
) -> Dict[str, Any]:
    paths = ensure_repo_structure(root)
    index.init_db(paths.db_path)
    config = load_google_calendar_config(root)
    google_client = client
    derived_calendar = ensure_derived_calendar(root, client=google_client)
    if google_client is None:
        from .connectors.google_calendar import build_google_client

        google_client = build_google_client(root)
    date_values = _date_range(start_date, end_date)
    generated_event_ids: Dict[str, Dict[str, Any]] = {}
    plans: List[Dict[str, Any]] = []
    for date_value in date_values:
        plan = build_circadian_plan(
            root=root,
            date_value=date_value,
            timezone_name=config.get("timezone"),
            location=(config.get("home_location") or {}).get("label"),
            environment_service=environment_service,
        )
        plans.append(plan)
        for phase in plan["phases"]:
            event_id = _derived_event_id(date_value, phase["phase"])
            generated_event_ids[event_id] = _build_derived_event_payload(plan, phase, derived_calendar["id"])
            google_client.upsert_event(derived_calendar["id"], event_id, generated_event_ids[event_id])
    existing = google_client.list_events(
        derived_calendar["id"],
        time_min="%sT00:00:00Z" % start_date,
        time_max="%sT23:59:59Z" % end_date,
    )
    deleted_ids: List[str] = []
    for item in existing:
        marker = ((item.get("extendedProperties") or {}).get("private") or {}).get("openhealth_kind")
        if marker != "circadian_hypothesis":
            continue
        if item.get("id") not in generated_event_ids:
            google_client.delete_event(derived_calendar["id"], item["id"])
            deleted_ids.append(item["id"])
    _upsert_circadian_records(paths, plans)
    return {
        "derived_calendar_id": derived_calendar["id"],
        "generated_events": len(generated_event_ids),
        "deleted_events": len(deleted_ids),
        "dates": date_values,
    }


def _upsert_circadian_records(paths, plans: Sequence[Dict[str, Any]]) -> None:
    coverage_start = plans[0]["date"] if plans else None
    coverage_end = plans[-1]["date"] if plans else None
    source = SourceManifest(
        source_id=CIRCADIAN_SOURCE_ID,
        source_type="manual-notes",
        owner="ilya",
        label="Circadian hypothesis engine",
        created_at=now_utc(),
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        files=[],
        parser_status="generated",
        notes=["Generated windows are hypothetical planning aids, not a measurement of true circadian phase."],
        metadata={"kind": "circadian-hypothesis"},
    )
    index.upsert_source(paths.db_path, source.to_dict())
    write_json(paths.source_manifests / ("%s.json" % CIRCADIAN_SOURCE_ID), source.to_dict())
    for plan in plans:
        hypothesis = InsightHypothesis(
            id=plan["hypothesis"]["id"],
            record_type="InsightHypothesis",
            source_id=CIRCADIAN_SOURCE_ID,
            title=plan["hypothesis"]["title"],
            summary=plan["hypothesis"]["summary"],
            artifact_ids=[],
            evidence_class="derived-hypothesis",
            confidence=plan["confidence"],
            date=plan["date"],
            tags=["hypothesis", "circadian", "derived"],
            metadata={
                "timezone": plan["timezone"],
                "anchor": plan["anchor"],
                "morning_light": plan.get("morning_light"),
            },
            statement=plan["hypothesis"]["statement"],
            evidence_record_ids=plan["hypothesis"]["evidence_record_ids"],
            open_questions=plan["hypothesis"]["open_questions"],
        )
        index.upsert_record(paths.db_path, hypothesis.to_dict())
        for phase in plan["phases"]:
            event = TimelineEvent(
                id="circadian-event-%s-%s" % (plan["date"], phase["phase"]),
                record_type="TimelineEvent",
                source_id=CIRCADIAN_SOURCE_ID,
                title="Hypothetical %s" % phase["title"],
                summary="Heuristic circadian planning window for %s." % plan["date"],
                artifact_ids=[],
                evidence_class="derived-hypothesis",
                confidence=plan["confidence"],
                date=plan["date"],
                start_date=phase["start"].date().isoformat(),
                end_date=phase["end"].date().isoformat(),
                tags=["circadian", "hypothetical", phase["phase"]],
                metadata={
                    "start": phase["start"].isoformat(),
                    "end": phase["end"].isoformat(),
                    "timezone": plan["timezone"],
                },
                event_kind="circadian_hypothesis",
                related_record_ids=[hypothesis.id],
            )
            index.upsert_record(paths.db_path, event.to_dict())
    refresh_contexts(paths, index)


def _build_derived_event_payload(plan: Dict[str, Any], phase: Dict[str, Any], derived_calendar_id: str) -> Dict[str, Any]:
    evidence_line = ", ".join(plan["hypothesis"]["evidence_record_ids"][:4]) or "none"
    return {
        "summary": "Hypothetical: %s" % phase["title"],
        "description": (
            "OpenHealth generated this as a hypothetical circadian planning window.\n\n"
            "Confidence: %.2f\n"
            "Evidence IDs: %s\n"
            "Anchor: sleep midpoint %s min, wake %s min, bedtime %s min."
            % (
                plan["confidence"],
                evidence_line,
                plan["anchor"]["midpoint_minutes"],
                plan["anchor"]["wake_minutes"],
                plan["anchor"]["bed_minutes"],
            )
        ),
        "start": {"dateTime": phase["start"].isoformat(), "timeZone": plan["timezone"]},
        "end": {"dateTime": phase["end"].isoformat(), "timeZone": plan["timezone"]},
        "transparency": "transparent",
        "extendedProperties": {
            "private": {
                "openhealth_kind": "circadian_hypothesis",
                "openhealth_date": plan["date"],
                "openhealth_phase": phase["phase"],
                "openhealth_confidence": "%.2f" % plan["confidence"],
                "openhealth_calendar": derived_calendar_id,
            }
        },
    }


def _load_sleep_sessions(db_path, tzinfo, target_date: date_class) -> List[Dict[str, Any]]:
    records = index.list_records_by_source(db_path, WHOOP_SOURCE_ID)
    sessions: List[Dict[str, Any]] = []
    for record in records:
        if record.get("record_type") != "TimelineEvent":
            continue
        if "sleep" not in (record.get("tags") or []):
            continue
        metadata = record.get("metadata") or {}
        start_raw = metadata.get("start")
        end_raw = metadata.get("end")
        if not start_raw or not end_raw:
            continue
        start_dt = _parse_datetime(start_raw).astimezone(tzinfo)
        end_dt = _parse_datetime(end_raw).astimezone(tzinfo)
        days_ago = (target_date - end_dt.date()).days
        if days_ago < 0 or days_ago > 14:
            continue
        sessions.append(
            {
                "record_id": record["id"],
                "start": start_dt,
                "end": end_dt,
                "days_ago": days_ago,
                "nap": bool(metadata.get("nap")),
            }
        )
    return sorted(sessions, key=lambda item: item["end"], reverse=True)


def _compute_sleep_anchor(sleep_sessions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not sleep_sessions:
        return {
            "bed_minutes": 24 * 60 + 30,
            "wake_minutes": 8 * 60,
            "midpoint_minutes": 4 * 60 + 15,
            "sleep_session_count": 0,
        }
    weighted_bed = 0.0
    weighted_wake = 0.0
    weighted_mid = 0.0
    total_weight = 0.0
    for session in sleep_sessions:
        weight = 2.0 if session["days_ago"] <= 7 else 1.0
        bed_minutes = _minutes_relative_to_noon(session["start"])
        wake_minutes = session["end"].hour * 60 + session["end"].minute
        midpoint_minutes = int((wake_minutes + _clock_minutes_from_relative_noon(bed_minutes)) / 2)
        weighted_bed += bed_minutes * weight
        weighted_wake += wake_minutes * weight
        weighted_mid += midpoint_minutes * weight
        total_weight += weight
    return {
        "bed_minutes": round(_clock_minutes_from_relative_noon(weighted_bed / total_weight)),
        "wake_minutes": round(weighted_wake / total_weight),
        "midpoint_minutes": round(weighted_mid / total_weight),
        "sleep_session_count": len(sleep_sessions),
    }


def _find_morning_light_record(db_path, date_value: str) -> Optional[Dict[str, Any]]:
    for record in index.list_records_by_source(db_path, MORNING_LIGHT_SOURCE_ID):
        if record.get("date") == date_value and record.get("observation_kind") == "morning_light_exposure":
            return record
    return None


def _morning_light_shift_minutes(record: Optional[Dict[str, Any]], wake_dt: datetime) -> int:
    if not record:
        return 0
    metadata = record.get("metadata") or {}
    timestamp = metadata.get("timestamp")
    if not timestamp:
        return 0
    observed = _parse_datetime(timestamp).astimezone(wake_dt.tzinfo)
    delta = int((observed - wake_dt).total_seconds() // 60)
    if delta <= 15:
        return 0
    return min(max(delta // 2, 0), 30)


def _circadian_confidence(sleep_count: int, has_environment: bool, has_morning_light: bool) -> float:
    confidence = 0.28 + min(sleep_count, 7) * 0.06
    if has_environment:
        confidence += 0.05
    if has_morning_light:
        confidence += 0.12
    else:
        confidence -= 0.05
    return round(max(min(confidence, 0.92), 0.15), 2)


def _circadian_summary(
    date_value: str,
    confidence: float,
    anchor: Dict[str, Any],
    morning_light: Optional[Dict[str, Any]],
    environment_payload: Optional[Dict[str, Any]],
) -> str:
    parts = [
        "Hypothetical circadian windows were generated for %s from recent WHOOP sleep timing." % date_value,
        "Confidence is %.2f." % confidence,
    ]
    if morning_light:
        parts.append("Morning light timing was observed and used as a modifier.")
    else:
        parts.append("No morning light check-in was available, so timing confidence is lower.")
    if environment_payload and environment_payload.get("sunrise"):
        parts.append("Sunrise and daylength were used only as contextual modifiers.")
    parts.append(
        "The anchor currently assumes wake at %s minutes and bedtime at %s minutes local clock time."
        % (anchor["wake_minutes"], anchor["bed_minutes"])
    )
    return " ".join(parts)


def _derived_event_id(date_value: str, phase_slug: str) -> str:
    digest = hashlib.sha1(("%s|%s" % (date_value, phase_slug)).encode("utf-8")).hexdigest()
    return "hc%s" % digest[:22]


def _resolve_timezone(name: str):
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def _parse_datetime(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _combine_clock(target_date: date_class, minutes: int, tzinfo) -> datetime:
    minutes = minutes % (24 * 60)
    return datetime.combine(target_date, time(hour=minutes // 60, minute=minutes % 60), tzinfo=tzinfo)


def _combine_bedtime(target_date: date_class, minutes: int, tzinfo) -> datetime:
    if minutes >= 24 * 60:
        return datetime.combine(target_date + timedelta(days=1), time(hour=(minutes - 24 * 60) // 60, minute=minutes % 60), tzinfo=tzinfo)
    return _combine_clock(target_date, minutes, tzinfo)


def _minutes_relative_to_noon(value: datetime) -> int:
    return int(((value.hour * 60 + value.minute) - 12 * 60) % (24 * 60))


def _clock_minutes_from_relative_noon(value: float) -> int:
    clock = int(round((value + 12 * 60) % (24 * 60)))
    if clock < 12 * 60:
        return clock + 24 * 60
    return clock


def _date_range(start_date: str, end_date: str) -> List[str]:
    current = date_class.fromisoformat(start_date)
    end_value = date_class.fromisoformat(end_date)
    values = []
    while current <= end_value:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return values
