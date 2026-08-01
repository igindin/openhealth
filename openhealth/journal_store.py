"""Disk mirror of the dashboard journal — stdlib only, local-first.

The web dashboard keeps daily check-ins (habits, mood, the wellbeing survey,
free-text notes) in browser ``localStorage``. That storage is invisible to the
engine and to agents, and it has no backup. This module is the disk side of
that journal:

    <home>/journal/days/YYYY-MM-DD.json   one day's payload (0600)
    <home>/journal/focus.json             current week-focus items (0600)

``<home>`` resolves like everywhere else in OpenHealth: explicit ``home``
argument first, then the ``OPENHEALTH_HOME`` environment variable, then
``~/.openhealth``. Directories are created 0700; every write is atomic
(temp file in the same directory, then ``os.replace``).

Day payload shape (mirrors the dashboard's localStorage keys):

    {
      "habits": {"<behavior_id>": true|false|<number>, ...},
      "mood":   {"quadrant": "...", "word": "...", "energy": <1-5>},
      "survey": {"energy": 4, "stress": 2, ..., "ts": "..."},
      "notes":  "free text"
    }

All sections are optional; unknown extra keys are preserved as-is (the store
mirrors, it does not censor). ``to_observations`` converts a stored day into
canonical ``Observation`` dicts (plus one ``ContextNote`` for notes) so the
data flows into the same SQLite index — and from there into
``modules.correlations.from_index`` — like any other source.

PRIVACY: journal entries are personal health data. They stay local, files are
0600, and their contents must never be logged.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import date as _date
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import journal_behaviors as catalog

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows fallback
    _fcntl = None

SOURCE_ID = "journal-ui"
SOURCE_TYPE = "local-journal"

DAYS_DIR = "days"
FOCUS_FILE = "focus.json"

MAX_FOCUS_ITEMS = 3
_DAY_THREAD_LOCKS: Dict[str, threading.Lock] = {}
_DAY_THREAD_LOCKS_GUARD = threading.Lock()

# Survey answers that become numeric metrics (matches the dashboard stepper).
SURVEY_FIELDS = ("energy", "stress", "sleep_quality", "pain", "mood")


# --- paths & private writes --------------------------------------------------


def journal_home(home: "Path | str | None" = None) -> Path:
    """Resolve the journal directory (``<home>/journal``)."""
    if home is None:
        home = os.environ.get("OPENHEALTH_HOME") or "~/.openhealth"
    return Path(home).expanduser() / "journal"


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        for p in (path, path.parent):
            os.chmod(p, 0o700)
    except OSError:
        pass  # exotic FS without chmod — keep going, files still local


def _write_private_json(path: Path, payload: Dict[str, Any]) -> None:
    """Atomic private write: temp file in the same dir, then replace."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".%s." % path.name,
            suffix=".tmp",
            delete=False,
        ) as fh:
            tmp = Path(fh.name)
            json.dump(
                payload,
                fh,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # a corrupt mirror file must not crash the engine
    return loaded if isinstance(loaded, dict) else None


def _valid_date(value: str) -> str:
    # Raises ValueError on a malformed date (core rule: do not invent dates).
    _date.fromisoformat(value)
    return value


# --- day payloads -------------------------------------------------------------


def normalize_day_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Light validation: known sections get shape checks, extras pass through.

    Raises ValueError loudly on a non-dict payload or wrongly-typed sections;
    stays tolerant about content inside them (the browser is the source).
    """
    if not isinstance(payload, dict):
        raise ValueError("day payload must be a dict, got %r" % type(payload).__name__)
    out = dict(payload)
    for section in ("habits", "mood", "survey"):
        if section in out and out[section] is not None and not isinstance(out[section], dict):
            raise ValueError("payload[%r] must be a dict, got %r" % (section, type(out[section]).__name__))
    if "notes" in out and out["notes"] is not None and not isinstance(out["notes"], str):
        raise ValueError("payload['notes'] must be a string")
    return out


def day_path(date: str, home: "Path | str | None" = None) -> Path:
    """Path of one day's mirror file (``days/YYYY-MM-DD.json``)."""
    return journal_home(home) / DAYS_DIR / ("%s.json" % _valid_date(date))


@contextmanager
def _day_lock(date: str, home: "Path | str | None" = None):
    lock_dir = journal_home(home) / ".locks"
    _ensure_private_dir(lock_dir)
    lock_path = lock_dir / ("%s.lock" % _valid_date(date))
    lock_key = str(lock_path.resolve())
    with _DAY_THREAD_LOCKS_GUARD:
        thread_lock = _DAY_THREAD_LOCKS.setdefault(
            lock_key,
            threading.Lock(),
        )
    with thread_lock:
        with lock_path.open("a+", encoding="utf-8") as lock:
            os.chmod(lock_path, 0o600)
            if _fcntl is not None:
                _fcntl.flock(lock.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(lock.fileno(), _fcntl.LOCK_UN)


def _save_day_unlocked(
    date: str,
    payload: Dict[str, Any],
    home: "Path | str | None" = None,
) -> Path:
    path = day_path(date, home)
    _ensure_private_dir(path.parent)
    body = normalize_day_payload(payload)
    body["date"] = _valid_date(date)
    _write_private_json(path, body)
    return path


def save_day(date: str, payload: Dict[str, Any], home: "Path | str | None" = None) -> Path:
    """Persist one complete day's journal payload to disk."""
    with _day_lock(date, home):
        return _save_day_unlocked(date, payload, home)


def load_day(date: str, home: "Path | str | None" = None) -> Optional[Dict[str, Any]]:
    """Load one day's payload, or None when absent/corrupt."""
    return _read_json(day_path(date, home))


def load_range(start: str, end: str, home: "Path | str | None" = None) -> Dict[str, Dict[str, Any]]:
    """Load all stored days in ``[start, end]`` inclusive, keyed by ISO date.

    Days without a file are simply absent from the result (no invented data).
    """
    start_d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)
    if end_d < start_d:
        raise ValueError("range end %s is before start %s" % (end, start))
    out: Dict[str, Dict[str, Any]] = {}
    current = start_d
    while current <= end_d:
        day = current.isoformat()
        payload = load_day(day, home)
        if payload is not None:
            out[day] = payload
        current += timedelta(days=1)
    return out


# --- conversion into engine records -------------------------------------------


def _habit_observation(day: str, behavior_id: str, value: Any) -> Optional[Dict[str, Any]]:
    """One habit answer -> an Observation dict (same shape as modules.journal).

    Boolean values keep their type so ``modules.correlations.from_index`` picks
    them up (it correlates only ``observation_kind == "journal_entry"`` records
    whose value is a bool, reading ``metadata.behavior_id``/``metric_name``).
    """
    if isinstance(value, bool):
        coerced: Any = value
    elif isinstance(value, (int, float)):
        coerced = float(value)
    else:
        return None  # unknown answer shape: mirror keeps it, the index skips it
    known = catalog.get_behavior(behavior_id)
    name = known["name"] if known else behavior_id
    category = known["category"] if known else "custom"
    return {
        "id": "obs-journal-%s-%s" % (day, behavior_id),
        "record_type": "Observation",
        "source_id": SOURCE_ID,
        "title": "Journal: %s" % name,
        "summary": "%s = %s on %s." % (name, coerced, day),
        "artifact_ids": [],
        "evidence_class": "personal",
        "confidence": 0.9,
        "date": day,
        "tags": ["journal", category, behavior_id],
        "metadata": {
            "behavior_id": behavior_id,
            "behavior_name": name,
            "category": category,
            "answer_type": "boolean" if isinstance(coerced, bool) else "quantity",
        },
        "observation_kind": "journal_entry",
        "metric_name": behavior_id,
        "value": coerced,
        "unit": None,
    }


def _metric_observation(day: str, kind: str, metric: str, value: float, title: str,
                        metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": "obs-%s-%s-%s" % (kind, day, metric),
        "record_type": "Observation",
        "source_id": SOURCE_ID,
        "title": title,
        "summary": "%s = %s on %s." % (metric, value, day),
        "artifact_ids": [],
        "evidence_class": "personal",
        "confidence": 0.9,
        "date": day,
        "tags": ["journal", kind],
        "metadata": metadata,
        "observation_kind": kind,
        "metric_name": metric,
        "value": float(value),
        "unit": None,
    }


def to_observations(day_payload: Dict[str, Any], date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Convert one stored day into canonical engine records.

    Returns Observation dicts (habits, mood, survey) plus one ContextNote for
    free-text notes. The result is ready for ``index.upsert_record``; boolean
    habit entries are format-compatible with ``modules.correlations.from_index``.
    """
    payload = normalize_day_payload(day_payload)
    day = _valid_date(date or payload.get("date") or "")
    records: List[Dict[str, Any]] = []

    for behavior_id, value in sorted((payload.get("habits") or {}).items()):
        rec = _habit_observation(day, str(behavior_id), value)
        if rec is not None:
            records.append(rec)

    mood = payload.get("mood") or {}
    energy = mood.get("energy")
    if isinstance(energy, (int, float)) and not isinstance(energy, bool):
        records.append(_metric_observation(
            day, "mood", "mood_energy", float(energy),
            "Mood check-in %s" % day,
            {"quadrant": mood.get("quadrant"), "word": mood.get("word")},
        ))

    survey = payload.get("survey") or {}
    for field in SURVEY_FIELDS:
        value = survey.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            records.append(_metric_observation(
                day, "survey", "survey_%s" % field, float(value),
                "Survey: %s %s" % (field, day),
                {"survey_field": field, "ts": survey.get("ts")},
            ))

    notes = (payload.get("notes") or "").strip()
    if notes:
        records.append({
            "id": "note-journal-%s" % day,
            "record_type": "ContextNote",
            "source_id": SOURCE_ID,
            "title": "Journal note %s" % day,
            "summary": notes,
            "artifact_ids": [],
            "evidence_class": "personal",
            "confidence": 0.9,
            "date": day,
            "tags": ["journal", "note"],
            "metadata": {"date": day},
            "note_kind": "journal_note",
            "themes": ["journal"],
        })
    return records


def persist_day(
    date: str,
    payload: Dict[str, Any],
    db_path,
    home: "Path | str | None" = None,
    *,
    merge: bool = False,
) -> int:
    """Save a day to disk *and* upsert its records into the SQLite index.

    Returns the number of records written to the index. This is the one-call
    bridge entry point: localStorage payload in, mirror file + indexed
    observations out.
    """
    from . import index
    from .storage import now_utc

    with _day_lock(date, home):
        body = normalize_day_payload(payload)
        if merge:
            existing_path = day_path(date, home)
            existing_day = _read_json(existing_path)
            if existing_path.exists() and existing_day is None:
                raise ValueError(
                    "existing journal day is unreadable; refusing partial merge"
                )
            existing_day = existing_day or {}
            merged = dict(existing_day)
            for key, value in body.items():
                if (
                    isinstance(value, dict)
                    and isinstance(merged.get(key), dict)
                ):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
            body = merged
        _save_day_unlocked(date, body, home)
        source = {
            "source_id": SOURCE_ID,
            "source_type": SOURCE_TYPE,
            "owner": "local-user",
            "label": "Local daily journal",
            "created_at": now_utc(),
            "coverage_start": date,
            "coverage_end": date,
            "files": [],
            "parser_status": "manual-entry",
            "notes": [
                "Daily check-ins captured locally through the journal interface."
            ],
            "metadata": {
                "storage": "local-first",
            },
        }
        return index.merge_source_coverage_and_upsert_records(
            db_path,
            source,
            [date],
            to_observations(body, date=date),
        )


# --- week focus ----------------------------------------------------------------


def save_week_focus(items: List[str], home: "Path | str | None" = None,
                    week_start: Optional[str] = None) -> Path:
    """Persist the current focus items (max MAX_FOCUS_ITEMS non-empty strings)."""
    if not isinstance(items, list):
        raise ValueError("focus items must be a list of strings")
    cleaned = [str(i).strip() for i in items if str(i).strip()]
    if len(cleaned) > MAX_FOCUS_ITEMS:
        raise ValueError("at most %d focus items, got %d" % (MAX_FOCUS_ITEMS, len(cleaned)))
    if week_start is not None:
        week_start = _valid_date(week_start)
    path = journal_home(home) / FOCUS_FILE
    _ensure_private_dir(path.parent)
    _write_private_json(path, {"items": cleaned, "week_start": week_start})
    return path


def load_week_focus(home: "Path | str | None" = None) -> Dict[str, Any]:
    """Load the stored focus, or an empty default when absent."""
    loaded = _read_json(journal_home(home) / FOCUS_FILE)
    if loaded is None or not isinstance(loaded.get("items"), list):
        return {"items": [], "week_start": None}
    return loaded


# --- backup --------------------------------------------------------------------


def export_all(home: "Path | str | None" = None) -> Dict[str, Any]:
    """One JSON-able snapshot of the whole journal mirror (backup/transfer)."""
    base = journal_home(home)
    days: Dict[str, Dict[str, Any]] = {}
    days_dir = base / DAYS_DIR
    if days_dir.is_dir():
        for path in sorted(days_dir.glob("*.json")):
            try:
                _valid_date(path.stem)
            except ValueError:
                continue  # foreign file in the mirror dir — not journal data
            payload = _read_json(path)
            if payload is not None:
                days[path.stem] = payload
    return {
        "version": 1,
        "days": days,
        "focus": load_week_focus(home),
    }
