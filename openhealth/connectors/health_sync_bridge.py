"""iCloud bridge ingest (Result 2, Mac side).

Reads the health-sync NDJSON pages the iPhone wrote to the shared iCloud inbox
and upserts them into the canonical store. Wire format:
``schemas/health-sync.schema.json`` - one JSON object per line, discriminated by
``kind`` (sample / event / journal / context).

Idempotent on two levels: each record gets a deterministic id derived from the
HealthKit ``external_id`` (so re-ingesting a page never duplicates), and
already-processed filenames are skipped via a small state file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from openhealth import index

SOURCE_ID = "apple-health-bridge"


def _sample_to_record(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ext = item.get("external_id")
    series = item.get("series_type")
    if not ext or not series or item.get("value") is None:
        return None
    recorded = item.get("recorded_at") or ""
    return {
        "id": f"obs-ah-{ext}",
        "record_type": "Observation",
        "source_id": SOURCE_ID,
        "title": series.replace("_", " "),
        "summary": f"{item['value']} {item.get('unit', '')}".strip(),
        "artifact_ids": [],
        "evidence_class": "personal",
        "confidence": 0.95,
        "date": recorded[:10] or None,
        "captured_at": recorded or None,
        "tags": ["apple-health", series],
        "metadata": {
            "external_id": ext,
            "source": item.get("source"),
            "source_bundle_id": item.get("source_bundle_id"),
            "device_model": item.get("device_model"),
            "zone_offset_seconds": item.get("zone_offset_seconds"),
        },
        "observation_kind": "health_sample",
        "metric_name": series,
        "value": item["value"],
        "unit": item.get("unit"),
    }


def _event_to_record(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ext = item.get("external_id")
    category = item.get("category")
    if not ext or not category:
        return None
    typ = item.get("type") or category
    start = item.get("start_at") or ""
    end = item.get("end_at") or ""
    return {
        "id": f"event-ah-{ext}",
        "record_type": "TimelineEvent",
        "source_id": SOURCE_ID,
        "title": f"{category}: {typ}",
        "summary": typ,
        "artifact_ids": [],
        "evidence_class": "personal",
        "confidence": 0.95,
        "date": start[:10] or None,
        "start_date": start[:10] or None,
        "end_date": end[:10] or None,
        "captured_at": start or None,
        "tags": ["apple-health", category],
        "metadata": {"external_id": ext, "type": typ, "metrics": item.get("metrics")},
        "event_kind": category,
        "related_record_ids": [],
    }


def _journal_to_record(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    day = item.get("day_key")
    if not day:
        return None
    yes_no = item.get("yes_no") or {}
    ratings = item.get("ratings") or {}
    parts = [k for k, v in yes_no.items() if v] + [f"{k}={v}" for k, v in ratings.items()]
    mood = ratings.get("mood")
    return {
        "id": f"note-journal-{day}",
        "record_type": "ContextNote",
        "source_id": SOURCE_ID,
        "title": f"Journal {day}",
        "summary": ", ".join(parts) if parts else (item.get("note") or "journal entry"),
        "artifact_ids": [],
        "evidence_class": "personal",
        "confidence": 0.9,
        "date": day,
        "captured_at": item.get("recorded_at"),
        "tags": ["journal"],
        "metadata": {"yes_no": yes_no, "ratings": ratings, "note": item.get("note", "")},
        "note_kind": "journal_entry",
        "people": [],
        "themes": list(yes_no.keys()),
        "mood": str(mood) if mood is not None else None,
    }


def _context_to_record(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ext = item.get("external_id")
    date = item.get("date")
    if not ext or not date:
        return None
    kind = item.get("kind_tag") or item.get("kind") or "context"
    return {
        "id": f"note-context-{ext}",
        "record_type": "ContextNote",
        "source_id": SOURCE_ID,
        "title": kind,
        "summary": item.get("text") or kind,
        "artifact_ids": [],
        "evidence_class": "contextual",
        "confidence": 0.8,
        "date": date,
        "tags": ["context", kind],
        "metadata": {"external_id": ext, "values": item.get("values"), "kind": kind},
        "note_kind": "context",
        "people": [],
        "themes": [kind],
        "mood": None,
    }


_MAP = {
    "sample": _sample_to_record,
    "event": _event_to_record,
    "journal": _journal_to_record,
    "context": _context_to_record,
}


def record_for_line(line: str) -> Optional[Dict[str, Any]]:
    """Map one NDJSON line to a canonical record dict (or None to skip)."""
    line = line.strip()
    if not line:
        return None
    item = json.loads(line)
    fn = _MAP.get(item.get("kind"))
    return fn(item) if fn else None


def ingest_file(db_path: Path, ndjson_path: Path) -> int:
    records = []
    with ndjson_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            record = record_for_line(line)
            if record is not None:
                records.append(record)
    if not records:
        return 0
    # One connection + executemany per page. A year of per-minute heart rate is
    # millions of rows; a fresh connection per record (upsert_record) would be
    # unusably slow. INSERT OR REPLACE keeps it idempotent by record id.
    connection = index.connect(db_path)
    with connection:
        connection.executemany(
            "INSERT OR REPLACE INTO records "
            "(record_id, source_id, record_type, date, start_date, end_date, evidence_class, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["id"], r["source_id"], r["record_type"], r.get("date"),
                    r.get("start_date"), r.get("end_date"), r["evidence_class"],
                    json.dumps(r, ensure_ascii=False),
                )
                for r in records
            ],
        )
    connection.close()
    return len(records)


def _load_state(state_path: Path) -> set:
    if state_path.exists():
        try:
            return set(json.loads(state_path.read_text(encoding="utf-8")).get("processed", []))
        except Exception:
            return set()
    return set()


def _save_state(state_path: Path, processed: set) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"processed": sorted(processed)}, ensure_ascii=False), encoding="utf-8")


def ingest_inbox(db_path: Path, inbox_dir: Path, state_path: Path) -> Dict[str, int]:
    """Ingest all new ``*.ndjson`` pages from ``inbox_dir``. Idempotent: skips
    already-processed filenames; upsert is keyed by record id."""
    index.init_db(db_path)
    processed = _load_state(state_path)
    files = sorted(inbox_dir.glob("*.ndjson")) if inbox_dir.exists() else []
    new_files = 0
    new_records = 0
    for page in files:
        if page.name in processed:
            continue
        new_records += ingest_file(db_path, page)
        processed.add(page.name)
        new_files += 1
        _save_state(state_path, processed)
    return {"files": new_files, "records": new_records, "total_seen": len(files)}
