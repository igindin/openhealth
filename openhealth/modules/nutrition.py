"""Nutrition module — eating-style profile plus a low-friction meal journal.

Two halves, mirroring the journal module's setup/check-in split:

- ``setup_style`` captures a one-time (but updatable) eating-style profile:
  dietary pattern, meals per day, declared eating window (the hook for IF and
  circadian links), caffeine, alcohol, sugary drinks, water, and free-text bad
  food habits. Stored as a single ``ContextNote`` with a stable id.
- ``compute`` is a day's meal log: entries {time, meal, text, photo_path?,
  tags[]} become Observations, plus a daily summary with the *actual* eating
  window (first -> last meal). When a bedtime is provided, a last meal inside
  the wind-down window (the 2h before bed, mirroring ``openhealth.circadian``)
  raises a C3 *question* about late eating vs circadian guidance — a prompt to
  test, never a verdict.

Meal photos are intake artifacts: ``store_meal_photo`` copies them read-only
into ``<root>/data/sources/nutrition/`` (same immutability contract as the
medical intake) and the record keeps only the path.

No diet advice, no calorie policing. Pure stdlib, zero external deps.
"""

import json
import shutil
import stat
from datetime import date as _date
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import evidence
from ..storage import sha256sum, slugify
from .base import ModuleResult, register

SOURCE_ID = "nutrition"

PATTERNS = ("omnivore", "vegetarian", "vegan", "keto", "intermittent_fasting", "other")
MEALS = ("breakfast", "lunch", "dinner", "snack")
ALCOHOL_FREQUENCIES = ("none", "rare", "weekly", "daily")

# Mirrors openhealth.circadian: wind-down is the 2h before bedtime.
WIND_DOWN_HOURS_BEFORE_BED = 2.0
MEAL_ESTIMATE_CONFIDENCE = evidence.confidence_to_numeric(evidence.Confidence.C2)
MEAL_ESTIMATE_CONFIDENCE_LEVEL = evidence.Confidence.C2.value
_ESTIMATE_FIELDS = ("kcal", "protein_g", "carb_g", "fat_g")


class MealCorrectionRedFlag(ValueError):
    """A correction mentioned a red flag and must not be interpreted."""

    def __init__(self, flags):
        super().__init__("red-flag text must not be interpreted as a meal correction")
        self.flags = list(flags)


# --- small helpers -----------------------------------------------------------


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _valid_date(value: str) -> str:
    # Raises ValueError on a malformed date (core rule: do not invent dates).
    _date.fromisoformat(value)
    return value


def _minutes(hhmm: str) -> int:
    """\"HH:MM\" -> minutes since midnight; raises ValueError when malformed."""
    parsed = datetime.strptime(str(hhmm).strip(), "%H:%M")
    return parsed.hour * 60 + parsed.minute


def _fmt_minutes(total: int) -> str:
    return "%02d:%02d" % ((total // 60) % 24, total % 60)


def normalize_meal_estimate(estimate: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize a model-produced meal estimate.

    The result is still only a C2 photo/comment estimate. Validation prevents a
    malformed model response from entering the health index; it does not make
    the estimate more trustworthy.
    """
    if not isinstance(estimate, dict):
        raise ValueError("meal estimate must be an object")
    title = str(estimate.get("title") or "").strip()
    if not title:
        raise ValueError("meal estimate requires a title")
    out = {"title": title[:120]}
    for field in _ESTIMATE_FIELDS:
        try:
            value = int(round(float(estimate[field])))
        except (KeyError, TypeError, ValueError):
            raise ValueError("meal estimate requires numeric %s" % field)
        if value < 0:
            raise ValueError("meal estimate %s cannot be negative" % field)
        out[field] = value
    if out["kcal"] <= 0:
        raise ValueError("meal estimate kcal must be positive")
    out["note"] = str(estimate.get("note") or "").strip()[:500]
    ingredients = estimate.get("ingredients")
    if ingredients is not None:
        if not isinstance(ingredients, list):
            raise ValueError("meal estimate ingredients must be a list")
        out["ingredients"] = [str(item).strip()[:160] for item in ingredients if str(item).strip()]
    return out


def _c2_question(text: str) -> str:
    """Make a stored weak estimate visibly C2 and interrogative."""
    body = str(text or "").strip().rstrip(".?")
    if body.startswith("[C2 Weak signal]"):
        return body + "?"
    return "[C2 Weak signal] Could %s?" % body


def build_corrected_meal_record(
    current_record: Dict[str, Any],
    estimate: Dict[str, Any],
    display_title: Optional[str] = None,
    display_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the current meal record with one revised C2 estimate applied."""
    if not isinstance(current_record, dict) or not current_record.get("id"):
        raise ValueError("current meal record is required")
    tags = set(current_record.get("tags") or [])
    kind = current_record.get("observation_kind")
    if "meal" not in tags and kind not in ("meal", "nutrition_meal"):
        raise ValueError("record is not a meal observation")

    normalized = normalize_meal_estimate(estimate)
    updated = json.loads(json.dumps(current_record, ensure_ascii=False))
    metadata = dict(updated.get("metadata") or {})
    metadata.update(normalized)
    metadata["method"] = "photo+user-correction-estimate"
    metadata["confidence_level"] = MEAL_ESTIMATE_CONFIDENCE_LEVEL
    updated["metadata"] = metadata
    updated["confidence"] = MEAL_ESTIMATE_CONFIDENCE
    updated["metric_name"] = "meal_kcal"
    updated["value"] = normalized["kcal"]
    updated["unit"] = "kcal"
    updated["title"] = _c2_question(
        display_title or "this meal be %s" % normalized["title"]
    )
    updated["summary"] = _c2_question(
        display_summary
        or (
            "%s be ~%d kcal (P%d / F%d / C%d)%s"
            % (
                normalized["title"],
                normalized["kcal"],
                normalized["protein_g"],
                normalized["fat_g"],
                normalized["carb_g"],
                (": " + normalized["note"]) if normalized["note"] else "",
            )
        )
    )
    return updated


def apply_meal_correction(
    db_path: Path,
    record_id: str,
    estimate: Dict[str, Any],
    correction_text: str,
    revision_id: str,
    created_at: str,
    source_type: str,
    evidence_artifact_ids: Optional[List[str]] = None,
    expected_revision: Optional[int] = None,
    display_title: Optional[str] = None,
    display_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply one audited text/voice correction to an existing meal record.

    Raw text and audio belong in immutable artifacts; this function stores the
    artifact ids and an append-only before/after revision, while the ``records``
    table remains the convenient current view.
    """
    correction = str(correction_text or "").strip()
    if not correction:
        raise ValueError("meal correction text is required")
    red_flags = evidence.scan_text_red_flags(correction)
    if red_flags:
        raise MealCorrectionRedFlag(red_flags)

    from .. import index

    current = index.get_record(db_path, record_id)
    if current is None:
        raise KeyError("record not found: %s" % record_id)
    observed_revision = int((current.get("metadata") or {}).get("revision") or 0)
    updated = build_corrected_meal_record(
        current,
        estimate,
        display_title=display_title,
        display_summary=display_summary,
    )
    normalized = normalize_meal_estimate(estimate)
    return index.apply_record_revision(
        db_path,
        updated_record=updated,
        revision_id=revision_id,
        created_at=created_at,
        reason="user_meal_correction",
        actor="user",
        evidence_artifact_ids=evidence_artifact_ids,
        patch={
            "source_type": source_type,
            "correction_text": correction,
            "estimate": normalized,
            "confidence_level": MEAL_ESTIMATE_CONFIDENCE_LEVEL,
        },
        expected_revision=(
            observed_revision if expected_revision is None else expected_revision
        ),
    )


def meal_correction_confirmation_required(
    source_type: str,
    prior_source_types: Optional[List[str]] = None,
) -> bool:
    """Return whether a committed correction still needs explicit confirmation.

    A provider-backed update derived entirely from immutable text is already
    auditable and can be corrected in a later reply, so a second yes/no adds no
    safety. Voice, mixed, missing, and legacy source metadata stay fail-closed
    because a transcription may not reflect the person's exact words.
    """
    sources = list(prior_source_types or []) + [source_type]
    return any(
        str(item or "").strip().lower() != "text" for item in sources
    )


def format_meal_correction_confirmation(
    estimate: Dict[str, Any],
    *,
    confirmation_required: bool = True,
) -> str:
    """Reader-facing C2 question for a revised estimate."""
    normalized = normalize_meal_estimate(estimate)
    summary = (
        "[C2 Weak signal] Could the revised estimate be ~%d kcal "
        "(P%d / F%d / C%d)?"
        % (
            normalized["kcal"],
            normalized["protein_g"],
            normalized["fat_g"],
            normalized["carb_g"],
        )
    )
    if confirmation_required:
        return summary + " Does that reflect your correction?"
    return (
        summary
        + " No second confirmation is needed; reply with a concrete correction "
        "if this is wrong."
    )


# --- eating-style profile (setup, updatable) ---------------------------------


def setup_style(answers: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the eating-style questionnaire; returns a ``ContextNote`` dict.

    The record id is stable (``nutrition-style``) so re-running the
    questionnaire updates the profile in place via the index upsert.
    """
    pattern = answers.get("pattern")
    if pattern not in PATTERNS:
        raise ValueError("pattern must be one of %s (got %r)" % (", ".join(PATTERNS), pattern))
    meals_per_day = int(answers.get("meals_per_day") or 0)
    if not (1 <= meals_per_day <= 10):
        raise ValueError("meals_per_day must be between 1 and 10 (got %r)" % answers.get("meals_per_day"))

    window = answers.get("eating_window")
    declared_window = None
    if window:
        start, end = _minutes(window["start"]), _minutes(window["end"])
        if end <= start:
            raise ValueError("eating_window end must be after start (got %s-%s)" % (window["start"], window["end"]))
        declared_window = {
            "start": _fmt_minutes(start),
            "end": _fmt_minutes(end),
            "hours": round((end - start) / 60.0, 1),
        }

    caffeine = answers.get("caffeine") or {}
    caffeine_profile = {
        "servings_per_day": float(caffeine.get("servings_per_day") or 0),
        "last_time": _fmt_minutes(_minutes(caffeine["last_time"])) if caffeine.get("last_time") else None,
    }
    alcohol = answers.get("alcohol_frequency") or "none"
    if alcohol not in ALCOHOL_FREQUENCIES:
        raise ValueError("alcohol_frequency must be one of %s (got %r)" % (", ".join(ALCOHOL_FREQUENCIES), alcohol))

    profile = {
        "pattern": pattern,
        "meals_per_day": meals_per_day,
        "eating_window": declared_window,
        "caffeine": caffeine_profile,
        "alcohol_frequency": alcohol,
        "sugary_drinks": bool(answers.get("sugary_drinks", False)),
        "water_liters_per_day": float(answers["water_liters_per_day"]) if answers.get("water_liters_per_day") else None,
        "bad_habits": str(answers.get("bad_habits") or "").strip() or None,
    }
    window_txt = (
        " Eating window %s-%s." % (declared_window["start"], declared_window["end"]) if declared_window else ""
    )
    return {
        "id": "nutrition-style",
        "record_type": "ContextNote",
        "source_id": SOURCE_ID,
        "title": "Eating-style profile",
        "summary": "Pattern: %s, %d meal(s)/day.%s Alcohol: %s." % (pattern, meals_per_day, window_txt, alcohol),
        "artifact_ids": [],
        "evidence_class": "personal",
        "confidence": 1.0,
        "date": today_iso(),
        "tags": ["nutrition", "style", "setup"],
        "metadata": {"profile": profile},
        "note_kind": "nutrition_style",
        "themes": ["nutrition"],
    }


def profile_from_record(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Read the profile back out of a stored style record (or None)."""
    if not record:
        return None
    return record.get("metadata", {}).get("profile")


# --- meal photo intake --------------------------------------------------------


def store_meal_photo(root: Path, path: Path, day: Optional[str] = None) -> str:
    """Copy a meal photo into ``<root>/data/sources/nutrition/`` (read-only).

    Same intake contract as medical documents: content-addressed name, the
    original is never edited, re-storing the same photo is a no-op. Returns
    the archived path (store it in the meal record's ``photo_path``).
    """
    src = Path(path)
    if not src.is_file():
        raise ValueError("no file at %s" % src)
    day = _valid_date(day) if day else today_iso()
    checksum = sha256sum(src)
    target_dir = Path(root) / "data" / "sources" / "nutrition"
    target_dir.mkdir(parents=True, exist_ok=True)
    archived = target_dir / ("%s-meal-%s-%s%s" % (day, slugify(src.stem), checksum[:8], src.suffix.lower()))
    if not archived.exists():
        shutil.copy2(src, archived)
        archived.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)  # raw stays immutable
    return str(archived)


# --- the module (a day's meal log) -------------------------------------------


class NutritionModule:
    id = "nutrition"
    name = "Nutrition — eating style & meal journal"
    domain = "nutrition"
    summary = "Logs meals into Observations, derives the actual eating window, flags late eating as a C3 question."

    def schema(self) -> Dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "NutritionDayInput",
            "type": "object",
            "required": ["meals"],
            "properties": {
                "date": {"type": "string", "description": "ISO date being logged; defaults to today."},
                "meals": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["time", "meal"],
                        "properties": {
                            "time": {"type": "string", "description": "HH:MM local clock time."},
                            "meal": {"type": "string", "enum": list(MEALS)},
                            "text": {"type": "string", "description": "Free-text description of what was eaten."},
                            "photo_path": {"type": "string", "description": "Archived path from store_meal_photo."},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "bedtime": {
                    "type": "string",
                    "description": "HH:MM planned bedtime; enables the late-eating (wind-down) check.",
                },
                "profile": {
                    "type": "object",
                    "description": "Eating-style profile (metadata.profile of the nutrition-style record).",
                },
            },
        }

    def compute(self, payload: Dict[str, Any]) -> ModuleResult:
        meals = payload.get("meals") or []
        if not meals:
            raise ValueError("need at least one meal entry")
        day = _valid_date(payload.get("date") or today_iso())
        profile = payload.get("profile")

        metrics: List[Dict[str, Any]] = []
        times: List[int] = []
        by_meal: Dict[str, int] = {}
        for i, raw in enumerate(meals):
            meal = raw.get("meal")
            if meal not in MEALS:
                raise ValueError("meal must be one of %s (got %r)" % (", ".join(MEALS), meal))
            minute = _minutes(raw.get("time") or raw.get("ts"))
            times.append(minute)
            by_meal[meal] = by_meal.get(meal, 0) + 1
            text = str(raw.get("text") or "").strip()
            metrics.append({
                "id": "obs-nutrition-meal-%s-%s-%d" % (day, meal, i),
                "record_type": "Observation",
                "source_id": SOURCE_ID,
                "title": "Meal: %s at %s" % (meal, _fmt_minutes(minute)),
                "summary": "%s at %s on %s%s" % (meal, _fmt_minutes(minute), day, ": %s." % text if text else "."),
                "artifact_ids": [],
                "evidence_class": "personal",
                "confidence": 0.9,
                "date": day,
                "tags": sorted(set(["nutrition", "meal", meal] + list(raw.get("tags") or []))),
                "metadata": {
                    "meal": meal,
                    "time": _fmt_minutes(minute),
                    "text": text or None,
                    "photo_path": raw.get("photo_path"),
                },
                "observation_kind": "meal",
                "metric_name": "meal_time",
                "value": _fmt_minutes(minute),
                "unit": None,
            })

        first, last = min(times), max(times)
        window_hours = round((last - first) / 60.0, 1)
        day_summary = {
            "meals_count": len(meals),
            "by_meal": by_meal,
            "first_meal": _fmt_minutes(first),
            "last_meal": _fmt_minutes(last),
            "eating_window_hours": window_hours,
        }
        metrics.append({
            "id": "obs-nutrition-window-%s" % day,
            "record_type": "Observation",
            "source_id": SOURCE_ID,
            "title": "Eating window %s" % day,
            "summary": "%d meal(s); first %s, last %s; actual eating window %.1f h." % (
                len(meals), _fmt_minutes(first), _fmt_minutes(last), window_hours,
            ),
            "artifact_ids": [],
            "evidence_class": "personal",
            "confidence": 0.9,
            "date": day,
            "tags": ["nutrition", "eating-window"],
            "metadata": day_summary,
            "observation_kind": "eating_window",
            "metric_name": "eating_window_hours",
            "value": window_hours,
            "unit": "h",
        })

        insights: List[Dict[str, Any]] = []

        # Late eating vs wind-down: last meal within 2h of bedtime -> C3 question.
        bedtime_raw = payload.get("bedtime")
        if bedtime_raw:
            bed = _minutes(bedtime_raw)
            if bed < 12 * 60:
                bed += 24 * 60  # bedtime after midnight ("00:30") belongs to the same evening
            wind_down_start = bed - int(WIND_DOWN_HOURS_BEFORE_BED * 60)
            if last >= wind_down_start:
                conf = evidence.Confidence.C3
                txt = (
                    "The last meal at %s falls inside the wind-down window (within %.0fh of bedtime %s). "
                    "Circadian guidance generally favors finishing food earlier — does late eating affect "
                    "your sleep or recovery" % (_fmt_minutes(last), WIND_DOWN_HOURS_BEFORE_BED, bedtime_raw)
                )
                insights.append({
                    "id": "insight-nutrition-late-eating-%s" % day,
                    "record_type": "InsightHypothesis",
                    "source_id": SOURCE_ID,
                    "title": "Late eating vs wind-down",
                    "summary": evidence.frame_statement(txt, conf),
                    "artifact_ids": [],
                    "evidence_class": "derived-hypothesis",
                    "confidence": evidence.confidence_to_numeric(conf),
                    "date": day,
                    "tags": ["nutrition", "circadian", "late-eating"],
                    "metadata": {
                        "last_meal": _fmt_minutes(last),
                        "bedtime": bedtime_raw,
                        "wind_down_start": _fmt_minutes(wind_down_start),
                    },
                    "statement": txt,
                    "evidence_record_ids": ["obs-nutrition-window-%s" % day],
                    "open_questions": [
                        "Does an earlier last meal change sleep or next-day recovery?",
                        "Could something else (stress, screens, alcohol) explain the same evenings?",
                    ],
                })

        # Declared vs actual window: drift outside the declared IF window -> C2 question.
        declared = (profile or {}).get("eating_window")
        if declared:
            declared_start, declared_end = _minutes(declared["start"]), _minutes(declared["end"])
            if first < declared_start or last > declared_end:
                conf = evidence.Confidence.C2
                txt = (
                    "Today's actual eating window (%s-%s) drifted outside the declared %s-%s window. "
                    "Is the declared eating style still holding" % (
                        _fmt_minutes(first), _fmt_minutes(last), declared["start"], declared["end"],
                    )
                )
                insights.append({
                    "id": "insight-nutrition-window-drift-%s" % day,
                    "record_type": "InsightHypothesis",
                    "source_id": SOURCE_ID,
                    "title": "Eating window drift",
                    "summary": evidence.frame_statement(txt, conf),
                    "artifact_ids": [],
                    "evidence_class": "derived-hypothesis",
                    "confidence": evidence.confidence_to_numeric(conf),
                    "date": day,
                    "tags": ["nutrition", "eating-window", "drift"],
                    "metadata": {
                        "declared": declared,
                        "actual": {"start": _fmt_minutes(first), "end": _fmt_minutes(last)},
                    },
                    "statement": txt,
                    "evidence_record_ids": ["obs-nutrition-window-%s" % day],
                    "open_questions": ["One-off exception or a recurring pattern?"],
                })

        # Day note (the UI/agent-facing daily summary).
        insights.append({
            "id": "nutrition-day-%s" % day,
            "record_type": "ContextNote",
            "source_id": SOURCE_ID,
            "title": "Nutrition day %s" % day,
            "summary": "Logged %d meal(s) on %s; eating window %s-%s (%.1f h)." % (
                len(meals), day, _fmt_minutes(first), _fmt_minutes(last), window_hours,
            ),
            "artifact_ids": [],
            "evidence_class": "personal",
            "confidence": 0.95,
            "date": day,
            "tags": ["nutrition", "daily-summary"],
            "metadata": day_summary,
            "note_kind": "nutrition_day",
            "themes": ["nutrition"],
        })

        notes = ["nutrition day %s: %d meal(s), window %.1f h" % (day, len(meals), window_hours)]
        return ModuleResult(metrics=metrics, insights=insights, notes=notes)


def persist(result: ModuleResult, db_path) -> int:
    """Write a day's meal records + notes into the SQLite index; returns count."""
    from .. import index

    written = 0
    for rec in list(result.metrics) + list(result.insights):
        index.upsert_record(db_path, rec)
        written += 1
    return written


def persist_style(style_record: Dict[str, Any], db_path) -> None:
    """Write (or update) the eating-style profile record into the index."""
    from .. import index

    index.upsert_record(db_path, style_record)


register(NutritionModule())
