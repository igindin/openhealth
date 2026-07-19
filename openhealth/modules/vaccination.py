"""Vaccination module — a personal immunization ledger.

Keeps a plain record of received vaccinations ({name, date, dose_number?,
next_due?, note?}) and turns the ledger into:

- one ``Observation`` per shot (the canonical ledger entries),
- an attention prompt (C3 — a *question*, never an instruction) when a
  recorded ``next_due`` date is already in the past: "worth discussing a
  booster with a clinician?",
- a ledger snapshot note (totals + upcoming due dates) for the UI/agent.

OpenHealth does not build vaccination schedules and does not say which
vaccines a person needs — that is a clinician's call. This module only
mirrors what the person recorded and flags dates they themselves set.
Pure stdlib, zero external deps (core rule).
"""

import re
from datetime import date as _date
from datetime import datetime, timezone
from typing import Any, Dict, List

from .. import evidence
from .base import ModuleResult, register

SOURCE_ID = "vaccination"


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _valid_date(value: str) -> str:
    # Raises ValueError on a malformed date (core rule: do not invent dates).
    _date.fromisoformat(value)
    return value


def _slug(value: str) -> str:
    """ASCII+Cyrillic-tolerant slug (storage.slugify drops Cyrillic letters).

    The escaped range in the character class is the Cyrillic lowercase alphabet
    (a-ya plus yo); it is kept so Russian vaccine names still produce a readable
    slug instead of collapsing to dashes.
    """
    lowered = value.strip().lower()
    lowered = re.sub("[^a-z0-9\u0430-\u044f\u0451]+", "-", lowered)
    return re.sub(r"-{2,}", "-", lowered).strip("-") or "item"


def normalize_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate one ledger item and fill defaults. Raises ValueError loudly."""
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("vaccination item needs a non-empty name")
    date = raw.get("date")
    if date is not None:
        date = _valid_date(date)
    next_due = raw.get("next_due")
    if next_due is not None:
        next_due = _valid_date(next_due)
    dose_number = raw.get("dose_number")
    if dose_number is not None:
        dose_number = int(dose_number)
        if dose_number < 1:
            raise ValueError("item %r: dose_number must be >= 1 (got %d)" % (name, dose_number))
    return {
        "name": name,
        "date": date,  # None = undated record, not an invented date
        "dose_number": dose_number,
        "next_due": next_due,
        "note": raw.get("note"),
    }


class VaccinationModule:
    id = "vaccination"
    name = "Vaccination — immunization ledger"
    domain = "vaccination"
    summary = "Mirrors recorded vaccinations and flags past next_due dates as clinician-discussion prompts."

    def schema(self) -> Dict[str, Any]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "VaccinationLedgerInput",
            "type": "object",
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {
                            "name": {"type": "string"},
                            "date": {"type": "string", "description": "ISO date of the shot; omit if unknown."},
                            "dose_number": {"type": "integer", "minimum": 1},
                            "next_due": {"type": "string", "description": "ISO date the person noted for a booster."},
                            "note": {"type": "string"},
                        },
                    },
                },
                "today": {"type": "string", "description": "ISO date override (tests / backfill)."},
            },
        }

    def compute(self, payload: Dict[str, Any]) -> ModuleResult:
        raw_items = payload.get("items") or []
        if not raw_items:
            raise ValueError("need at least one vaccination record")
        today = _valid_date(payload.get("today") or today_iso())

        items = [normalize_item(raw) for raw in raw_items]
        metrics: List[Dict[str, Any]] = []
        insights: List[Dict[str, Any]] = []
        overdue: List[Dict[str, Any]] = []
        upcoming: List[Dict[str, Any]] = []

        for item in items:
            slug = _slug(item["name"])
            rec_id = "obs-vaccination-%s-%s" % (slug, item["date"] or "undated")
            dose = " (dose %d)" % item["dose_number"] if item["dose_number"] else ""
            metrics.append({
                "id": rec_id,
                "record_type": "Observation",
                "source_id": SOURCE_ID,
                "title": "Vaccination: %s%s" % (item["name"], dose),
                "summary": "%s%s on %s.%s" % (
                    item["name"],
                    dose,
                    item["date"] or "an unrecorded date",
                    " Next due noted: %s." % item["next_due"] if item["next_due"] else "",
                ),
                "artifact_ids": [],
                "evidence_class": "personal",
                "confidence": 0.9,
                "date": item["date"],
                "tags": ["vaccination"],
                "metadata": {
                    "name": item["name"],
                    "dose_number": item["dose_number"],
                    "next_due": item["next_due"],
                    "note": item["note"],
                },
                "observation_kind": "vaccination",
                "metric_name": "vaccination",
                "value": item["dose_number"],
                "unit": None,
            })

            if not item["next_due"]:
                continue
            entry = {"name": item["name"], "next_due": item["next_due"]}
            if item["next_due"] >= today:  # ISO dates compare lexicographically
                upcoming.append(entry)
                continue
            overdue.append(entry)
            conf = evidence.Confidence.C3
            txt = (
                "The next-due date you noted for '%s' (%s) has passed. A revaccination "
                "may be worth discussing with a clinician" % (item["name"], item["next_due"])
            )
            insights.append({
                "id": "insight-vaccination-due-%s" % slug,
                "record_type": "InsightHypothesis",
                "source_id": SOURCE_ID,
                "title": "Attention: %s next dose may be due" % item["name"],
                "summary": evidence.frame_statement(txt, conf),
                "artifact_ids": [],
                "evidence_class": "derived-hypothesis",
                "confidence": evidence.confidence_to_numeric(conf),
                "date": today,
                "tags": ["vaccination", "attention", "see-clinician"],
                "metadata": {"name": item["name"], "next_due": item["next_due"], "as_of": today},
                "statement": txt,
                "evidence_record_ids": [rec_id],
                "open_questions": [
                    "Was the booster perhaps already received but not recorded here?",
                    "Does the schedule still apply? (Only a clinician can say.)",
                ],
            })

        upcoming.sort(key=lambda e: e["next_due"])
        insights.append({
            "id": "note-vaccination-snapshot-%s" % today,
            "record_type": "ContextNote",
            "source_id": SOURCE_ID,
            "title": "Vaccination ledger snapshot %s" % today,
            "summary": "%d vaccination(s) recorded; %d past next-due date(s); %d upcoming." % (
                len(items), len(overdue), len(upcoming),
            ),
            "artifact_ids": [],
            "evidence_class": "personal",
            "confidence": 1.0,
            "date": today,
            "tags": ["vaccination", "snapshot"],
            "metadata": {"total": len(items), "overdue": overdue, "upcoming": upcoming},
            "note_kind": "vaccination_snapshot",
            "themes": ["vaccination"],
        })

        notes = ["vaccination ledger: %d record(s), %d overdue next-due flag(s)" % (len(items), len(overdue))]
        return ModuleResult(metrics=metrics, insights=insights, notes=notes)


def persist(result: ModuleResult, db_path) -> int:
    """Write ledger records + notes into the SQLite index; returns count."""
    from .. import index

    written = 0
    for rec in list(result.metrics) + list(result.insights):
        index.upsert_record(db_path, rec)
        written += 1
    return written


register(VaccinationModule())
