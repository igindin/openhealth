import json

from openhealth import index
from openhealth.connectors import health_sync_bridge as bridge


def test_sample_maps_to_observation():
    line = json.dumps({
        "kind": "sample", "external_id": "u1", "series_type": "heart_rate",
        "value": 61, "unit": "bpm", "recorded_at": "2026-06-14T09:00:00Z",
        "source": "apple_health",
    })
    rec = bridge.record_for_line(line)
    assert rec["record_type"] == "Observation"
    assert rec["id"] == "obs-ah-u1"
    assert rec["metric_name"] == "heart_rate"
    assert rec["value"] == 61
    assert rec["date"] == "2026-06-14"
    assert rec["evidence_class"] == "personal"


def test_event_journal_context_and_skips():
    ev = bridge.record_for_line(json.dumps({
        "kind": "event", "external_id": "w1", "category": "workout", "type": "running",
        "start_at": "2026-06-14T18:00:00Z", "end_at": "2026-06-14T18:30:00Z",
    }))
    assert ev["record_type"] == "TimelineEvent"
    assert ev["event_kind"] == "workout"

    jr = bridge.record_for_line(json.dumps({
        "kind": "journal", "day_key": "2026-06-13", "recorded_at": "2026-06-14T07:00:00Z",
        "yes_no": {"alcohol": True}, "ratings": {"energy": 4}, "note": "ok",
    }))
    assert jr["record_type"] == "ContextNote"
    assert jr["id"] == "note-journal-2026-06-13"

    assert bridge.record_for_line("") is None
    assert bridge.record_for_line(json.dumps({"kind": "unknown"})) is None


def test_ingest_inbox_idempotent(tmp_path):
    db = tmp_path / "db.sqlite3"
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    state = tmp_path / "state.json"

    page = inbox / "heart_rate-1-p0.ndjson"
    lines = [
        json.dumps({"kind": "sample", "external_id": f"u{i}", "series_type": "heart_rate",
                    "value": 60 + i, "unit": "bpm", "recorded_at": "2026-06-14T09:00:00Z"})
        for i in range(3)
    ]
    page.write_text("\n".join(lines) + "\n", encoding="utf-8")

    first = bridge.ingest_inbox(db, inbox, state)
    assert first["files"] == 1
    assert first["records"] == 3
    assert len(index.list_records(db, "Observation")) == 3

    # Re-run: file already processed, no duplicates.
    second = bridge.ingest_inbox(db, inbox, state)
    assert second["files"] == 0
    assert len(index.list_records(db, "Observation")) == 3
