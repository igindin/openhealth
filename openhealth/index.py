import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: Path) -> None:
    connection = connect(db_path)
    with connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                owner TEXT NOT NULL,
                created_at TEXT NOT NULL,
                coverage_start TEXT,
                coverage_end TEXT,
                parser_status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                checksum TEXT NOT NULL UNIQUE,
                source_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                original_path TEXT NOT NULL,
                archived_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                record_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                record_type TEXT NOT NULL,
                date TEXT,
                start_date TEXT,
                end_date TEXT,
                evidence_class TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
    connection.close()


def upsert_source(db_path: Path, payload: Dict[str, Any]) -> None:
    connection = connect(db_path)
    with connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO sources (
                source_id, source_type, owner, created_at, coverage_start, coverage_end, parser_status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["source_id"],
                payload["source_type"],
                payload["owner"],
                payload["created_at"],
                payload.get("coverage_start"),
                payload.get("coverage_end"),
                payload["parser_status"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    connection.close()


def upsert_artifact(db_path: Path, payload: Dict[str, Any]) -> None:
    connection = connect(db_path)
    with connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO artifacts (
                artifact_id, checksum, source_id, source_type, original_path, archived_path, mime_type, size_bytes, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["artifact_id"],
                payload["checksum"],
                payload["source_id"],
                payload["source_type"],
                payload["original_path"],
                payload["archived_path"],
                payload["mime_type"],
                payload["size_bytes"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    connection.close()


def upsert_record(db_path: Path, payload: Dict[str, Any]) -> None:
    connection = connect(db_path)
    with connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO records (
                record_id, source_id, record_type, date, start_date, end_date, evidence_class, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                payload["source_id"],
                payload["record_type"],
                payload.get("date"),
                payload.get("start_date"),
                payload.get("end_date"),
                payload["evidence_class"],
                json.dumps(payload, ensure_ascii=False),
            ),
        )
    connection.close()


def find_artifact_by_checksum(db_path: Path, checksum: str) -> Optional[Dict[str, Any]]:
    connection = connect(db_path)
    row = connection.execute(
        "SELECT payload_json FROM artifacts WHERE checksum = ?",
        (checksum,),
    ).fetchone()
    connection.close()
    if not row:
        return None
    return json.loads(row["payload_json"])


def delete_records_for_source(db_path: Path, source_id: str) -> None:
    connection = connect(db_path)
    with connection:
        connection.execute("DELETE FROM records WHERE source_id = ?", (source_id,))
    connection.close()


def delete_records_by_ids(db_path: Path, record_ids: List[str]) -> None:
    if not record_ids:
        return
    # Chunk the IN (...) list: SQLite caps host variables per statement
    # (SQLITE_MAX_VARIABLE_NUMBER, historically 999), and a multi-day WHOOP
    # purge easily exceeds that. Delete in batches well under the limit.
    connection = connect(db_path)
    with connection:
        for start in range(0, len(record_ids), 500):
            chunk = record_ids[start:start + 500]
            placeholders = ",".join("?" for _ in chunk)
            connection.execute("DELETE FROM records WHERE record_id IN (%s)" % placeholders, tuple(chunk))
    connection.close()


def list_records(db_path: Path, record_type: Optional[str] = None) -> List[Dict[str, Any]]:
    connection = connect(db_path)
    if record_type:
        rows = connection.execute(
            "SELECT payload_json FROM records WHERE record_type = ?",
            (record_type,),
        ).fetchall()
    else:
        rows = connection.execute("SELECT payload_json FROM records").fetchall()
    connection.close()
    return [json.loads(row["payload_json"]) for row in rows]


def list_sources(db_path: Path) -> List[Dict[str, Any]]:
    connection = connect(db_path)
    rows = connection.execute("SELECT payload_json FROM sources").fetchall()
    connection.close()
    return [json.loads(row["payload_json"]) for row in rows]


def list_records_by_source(db_path: Path, source_id: str) -> List[Dict[str, Any]]:
    connection = connect(db_path)
    rows = connection.execute(
        "SELECT payload_json FROM records WHERE source_id = ?",
        (source_id,),
    ).fetchall()
    connection.close()
    return [json.loads(row["payload_json"]) for row in rows]


def list_artifacts(db_path: Path) -> List[Dict[str, Any]]:
    connection = connect(db_path)
    rows = connection.execute("SELECT payload_json FROM artifacts").fetchall()
    connection.close()
    return [json.loads(row["payload_json"]) for row in rows]
