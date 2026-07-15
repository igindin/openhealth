"""Unit tests for POST /api/intake (server.handle_intake).

Verifies the "one base" seam: an IntakeEnvelope from any transport (web,
Telegram-via-Hermes, a webhook) lands as a ContextNote record in the health
index, mirrors the raw envelope to disk, degrades gracefully without an index,
and validates the required envelope fields.
"""

import importlib.util
import sqlite3
from pathlib import Path

_SERVER_PATH = Path(__file__).resolve().parent.parent / "ui" / "web" / "server.py"
_spec = importlib.util.spec_from_file_location("bridge_server_intake", _SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

from openhealth import index as oh_index  # noqa: E402  (server put repo root on sys.path)


def _envelope(**over):
    env = {
        "submission_id": "tg-123-42",
        "submitted_at": "2026-07-07T08:15:00+00:00",
        "channel": "telegram",
        "author": "ilya",
        "text": "лёг в 23:10, чувствую себя отдохнувшим",
        "tags": ["sleep", "mood"],
    }
    env.update(over)
    return env


def _make_index(base_dir: Path) -> Path:
    db = base_dir / "data" / "index" / "health_os.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    oh_index.init_db(db)
    return db


def _records(db: Path):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT record_id, source_id, record_type, date, evidence_class, payload_json FROM records"
    ).fetchall()
    conn.close()
    return rows


def test_intake_indexes_envelope_as_record(tmp_path):
    db = _make_index(tmp_path)
    status, body = server.handle_intake(_envelope(), tmp_path)
    assert status == 200
    assert body["status"] == "ok"
    assert body["indexed"] is True
    assert body["record_id"] == "intake-telegram-tg-123-42"

    rows = _records(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["record_id"] == "intake-telegram-tg-123-42"
    assert row["record_type"] == "ContextNote"
    assert row["source_id"] == "intake-telegram"
    assert row["date"] == "2026-07-07"
    assert row["evidence_class"] == "personal"
    import json
    payload = json.loads(row["payload_json"])
    assert "отдохнувшим" in payload["summary"]
    assert "telegram" in payload["tags"] and "intake" in payload["tags"]
    assert payload["metadata"]["channel"] == "telegram"


def test_intake_mirrors_raw_envelope_to_disk(tmp_path):
    _make_index(tmp_path)
    status, body = server.handle_intake(_envelope(), tmp_path)
    assert status == 200
    env_path = body.get("envelope")
    assert env_path and Path(env_path).is_file()
    assert "data/intake/telegram/envelopes/2026-07-07" in env_path.replace("\\", "/")


def test_intake_graceful_without_index(tmp_path):
    # no data/index/*.sqlite3 -> saved to disk, not indexed (same as journal)
    status, body = server.handle_intake(_envelope(), tmp_path)
    assert status == 200
    assert body["status"] == "ok"
    assert body["indexed"] is False
    assert "record_id" in body


def test_intake_rejects_missing_required(tmp_path):
    status, body = server.handle_intake({"channel": "web"}, tmp_path)
    assert status == 400
    assert "missing required" in body["message"]


def test_intake_rejects_non_dict(tmp_path):
    status, body = server.handle_intake(["not", "a", "dict"], tmp_path)
    assert status == 400


def test_intake_web_channel_distinct_source(tmp_path):
    db = _make_index(tmp_path)
    server.handle_intake(_envelope(channel="web", submission_id="w-1"), tmp_path)
    rows = _records(db)
    assert rows[0]["source_id"] == "intake-web"
    assert rows[0]["record_id"] == "intake-web-w-1"
