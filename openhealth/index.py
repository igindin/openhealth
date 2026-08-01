import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class RecordRevisionConflict(RuntimeError):
    """The record changed after a caller prepared a correction."""


TELEGRAM_LINK_RELATIONS = {
    "source_message",
    "bot_reply",
    "correction_reply",
    "confirmation_reply",
}


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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS record_revisions (
                revision_id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                actor TEXT NOT NULL,
                evidence_artifact_ids_json TEXT NOT NULL,
                patch_json TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL,
                UNIQUE(record_id, revision)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_record_revisions_record_id
            ON record_revisions(record_id, revision)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_record_links (
                chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                record_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                PRIMARY KEY(chat_id, message_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_telegram_record_links_record_id
            ON telegram_record_links(record_id)
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


def _merge_source_coverage_on_connection(
    connection: sqlite3.Connection,
    payload: Dict[str, Any],
    coverage_dates: List[str],
) -> None:
    """Upsert one source without committing the caller's transaction."""
    source_id = str(payload["source_id"])
    row = connection.execute(
        "SELECT payload_json FROM sources WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    existing = json.loads(row["payload_json"]) if row else {}
    merged = {**existing, **payload}
    for key in ("created_at", "owner", "label"):
        if existing.get(key):
            merged[key] = existing[key]
    merged["metadata"] = {
        **(existing.get("metadata") or {}),
        **(payload.get("metadata") or {}),
    }
    if existing.get("notes"):
        merged["notes"] = existing["notes"]

    files: List[Any] = []
    for manifest in (existing.get("files"), payload.get("files")):
        if not isinstance(manifest, list):
            continue
        for item in manifest:
            if item not in files:
                files.append(item)
    merged["files"] = files

    points = [
        str(point)
        for point in (
            *coverage_dates,
            existing.get("coverage_start"),
            existing.get("coverage_end"),
            payload.get("coverage_start"),
            payload.get("coverage_end"),
        )
        if point
    ]
    rows = connection.execute(
        """
        SELECT date, start_date, end_date
        FROM records
        WHERE source_id = ?
        """,
        (source_id,),
    ).fetchall()
    for record in rows:
        points.extend(
            str(point)
            for point in (
                record["date"],
                record["start_date"],
                record["end_date"],
            )
            if point
        )
    merged["coverage_start"] = min(points) if points else None
    merged["coverage_end"] = max(points) if points else None

    connection.execute(
        """
        INSERT OR REPLACE INTO sources (
            source_id, source_type, owner, created_at, coverage_start,
            coverage_end, parser_status, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            merged["source_type"],
            merged["owner"],
            merged["created_at"],
            merged.get("coverage_start"),
            merged.get("coverage_end"),
            merged["parser_status"],
            json.dumps(merged, ensure_ascii=False),
        ),
    )


def merge_source_coverage(
    db_path: Path,
    payload: Dict[str, Any],
    coverage_dates: List[str],
) -> None:
    """Atomically upsert a source while widening, never shrinking, coverage."""
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _merge_source_coverage_on_connection(
            connection,
            payload,
            coverage_dates,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def upsert_artifact(db_path: Path, payload: Dict[str, Any]) -> None:
    connection = connect(db_path)
    with connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO artifacts (
                artifact_id, checksum, source_id, source_type, original_path,
                archived_path, mime_type, size_bytes, payload_json
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


def _upsert_record_on_connection(
    connection: sqlite3.Connection,
    payload: Dict[str, Any],
) -> None:
    """Insert one record without committing the caller's transaction.

    Source re-imports are allowed to be repeatable, but they must not silently
    replace the head of an append-only correction ledger. If revisions exist,
    their latest ``after`` payload remains the current record.
    """
    stored_payload = payload
    revisions_table = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'record_revisions'
        """
    ).fetchone()
    if revisions_table:
        head = connection.execute(
            """
            SELECT after_json
            FROM record_revisions
            WHERE record_id = ?
            ORDER BY revision DESC
            LIMIT 1
            """,
            (payload["id"],),
        ).fetchone()
        if head:
            stored_payload = json.loads(head["after_json"])
    connection.execute(
        """
        INSERT OR REPLACE INTO records (
            record_id, source_id, record_type, date, start_date, end_date,
            evidence_class, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stored_payload["id"],
            stored_payload["source_id"],
            stored_payload["record_type"],
            stored_payload.get("date"),
            stored_payload.get("start_date"),
            stored_payload.get("end_date"),
            stored_payload["evidence_class"],
            json.dumps(stored_payload, ensure_ascii=False),
        ),
    )


def upsert_record(db_path: Path, payload: Dict[str, Any]) -> None:
    """Insert a record, preserving an audited user-corrected current view."""
    connection = connect(db_path)
    with connection:
        _upsert_record_on_connection(connection, payload)
    connection.close()


def merge_source_coverage_and_upsert_records(
    db_path: Path,
    source: Dict[str, Any],
    coverage_dates: List[str],
    records: Iterable[Dict[str, Any]],
) -> int:
    """Atomically widen source coverage and upsert all supplied records."""
    pending = list(records)
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _merge_source_coverage_on_connection(
            connection,
            source,
            coverage_dates,
        )
        for record in pending:
            _upsert_record_on_connection(connection, record)
        connection.commit()
        return len(pending)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_record(db_path: Path, record_id: str) -> Optional[Dict[str, Any]]:
    """Return one current record payload, or None when it is not indexed."""
    connection = connect(db_path)
    row = connection.execute(
        "SELECT payload_json FROM records WHERE record_id = ?",
        (record_id,),
    ).fetchone()
    connection.close()
    return json.loads(row["payload_json"]) if row else None


def link_telegram_message(
    db_path: Path,
    chat_id: Any,
    message_id: int,
    record_id: str,
    relation: str,
) -> None:
    """Link a Telegram source, bot, correction, or confirmation to one record.

    Re-linking the same message to the same record and relation is an
    idempotent no-op. Reassigning either field is rejected: reply routing and
    provenance must never silently change.
    """
    if relation not in TELEGRAM_LINK_RELATIONS:
        raise ValueError("unknown Telegram record-link relation: %s" % relation)
    chat_key = str(chat_id)
    message_key = int(message_id)
    connection = connect(db_path)
    try:
        with connection:
            existing = connection.execute(
                """
                SELECT record_id, relation
                FROM telegram_record_links
                WHERE chat_id = ? AND message_id = ?
                """,
                (chat_key, message_key),
            ).fetchone()
            if existing:
                if (
                    existing["record_id"] != record_id
                    or existing["relation"] != relation
                ):
                    raise ValueError(
                        "Telegram message %s/%s is already linked to %s as %s"
                        % (
                            chat_key,
                            message_key,
                            existing["record_id"],
                            existing["relation"],
                        )
                    )
                return
            connection.execute(
                """
                INSERT INTO telegram_record_links (chat_id, message_id, record_id, relation)
                VALUES (?, ?, ?, ?)
                """,
                (chat_key, message_key, record_id, relation),
            )
    finally:
        connection.close()


def resolve_telegram_reply(db_path: Path, chat_id: Any, replied_message_id: int) -> Optional[str]:
    """Resolve a Telegram reply target to a record id, if it was linked."""
    connection = connect(db_path)
    row = connection.execute(
        """
        SELECT record_id
        FROM telegram_record_links
        WHERE chat_id = ? AND message_id = ?
        """,
        (str(chat_id), int(replied_message_id)),
    ).fetchone()
    connection.close()
    return str(row["record_id"]) if row else None


def list_record_revisions(db_path: Path, record_id: str) -> List[Dict[str, Any]]:
    """Return the append-only audit trail for one record."""
    connection = connect(db_path)
    rows = connection.execute(
        """
        SELECT revision_id, record_id, revision, created_at, reason, actor,
               evidence_artifact_ids_json, patch_json, before_json, after_json
        FROM record_revisions
        WHERE record_id = ?
        ORDER BY revision
        """,
        (record_id,),
    ).fetchall()
    connection.close()
    return [
        {
            "revision_id": row["revision_id"],
            "record_id": row["record_id"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "reason": row["reason"],
            "actor": row["actor"],
            "evidence_artifact_ids": json.loads(row["evidence_artifact_ids_json"]),
            "patch": json.loads(row["patch_json"]),
            "before": json.loads(row["before_json"]),
            "after": json.loads(row["after_json"]),
        }
        for row in rows
    ]


def apply_record_revision(
    db_path: Path,
    updated_record: Dict[str, Any],
    revision_id: str,
    created_at: str,
    reason: str,
    actor: str,
    evidence_artifact_ids: Optional[List[str]] = None,
    patch: Optional[Dict[str, Any]] = None,
    expected_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Atomically append an audit revision and replace the current record.

    ``revision_id`` is the idempotency key (for Telegram, use its stable
    submission id). Re-delivery returns the already-applied result. A caller
    may pass ``expected_revision`` to prevent a stale correction from
    overwriting a newer one.
    """
    record_id = str(updated_record.get("id") or "")
    if not record_id:
        raise ValueError("updated_record requires id")
    if not revision_id:
        raise ValueError("revision_id is required")

    artifact_ids = list(dict.fromkeys(evidence_artifact_ids or []))
    patch_payload = dict(patch or {})
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT record_id, revision, after_json
            FROM record_revisions
            WHERE revision_id = ?
            """,
            (revision_id,),
        ).fetchone()
        if existing:
            if existing["record_id"] != record_id:
                raise ValueError("revision_id %s belongs to another record" % revision_id)
            connection.commit()
            return {
                "applied": False,
                "revision_id": revision_id,
                "revision": int(existing["revision"]),
                "record": json.loads(existing["after_json"]),
            }

        current_row = connection.execute(
            "SELECT payload_json FROM records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if not current_row:
            raise KeyError("record not found: %s" % record_id)
        before = json.loads(current_row["payload_json"])

        latest_row = connection.execute(
            "SELECT MAX(revision) AS revision FROM record_revisions WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        ledger_revision = int(latest_row["revision"] or 0)
        metadata_revision = int((before.get("metadata") or {}).get("revision") or 0)
        current_revision = max(ledger_revision, metadata_revision)
        if expected_revision is not None and int(expected_revision) != current_revision:
            raise RecordRevisionConflict(
                "record %s is at revision %d, expected %d"
                % (record_id, current_revision, int(expected_revision))
            )

        next_revision = current_revision + 1
        after = json.loads(json.dumps(updated_record, ensure_ascii=False))
        after["id"] = record_id
        metadata = dict(after.get("metadata") or {})
        metadata["revision"] = next_revision
        metadata["current_revision_id"] = revision_id
        after["metadata"] = metadata
        after["artifact_ids"] = list(
            dict.fromkeys(list(after.get("artifact_ids") or []) + artifact_ids)
        )

        connection.execute(
            """
            INSERT INTO record_revisions (
                revision_id, record_id, revision, created_at, reason, actor,
                evidence_artifact_ids_json, patch_json, before_json, after_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                record_id,
                next_revision,
                created_at,
                reason,
                actor,
                json.dumps(artifact_ids, ensure_ascii=False),
                json.dumps(patch_payload, ensure_ascii=False),
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO records (
                record_id, source_id, record_type, date, start_date, end_date,
                evidence_class, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                after["source_id"],
                after["record_type"],
                after.get("date"),
                after.get("start_date"),
                after.get("end_date"),
                after["evidence_class"],
                json.dumps(after, ensure_ascii=False),
            ),
        )
        connection.commit()
        return {
            "applied": True,
            "revision_id": revision_id,
            "revision": next_revision,
            "record": after,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
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
