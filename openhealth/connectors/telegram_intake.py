"""Telegram intake → canonical IntakeEnvelope. Pure stdlib, no network here.

This module is the *conversion* half of the Telegram channel: it turns raw
Telegram Bot API ``update`` objects into IntakeEnvelope dicts that satisfy
``schemas/intake-envelope.schema.json`` (required: ``submission_id``,
``submitted_at``, ``channel``, ``author``) plus the flat convenience fields the
agent layer expects (``type`` / ``text`` / ``ts`` / ``chat_id`` /
``source="telegram"``), and writes them to a local intake folder:

    <data-dir>/
      envelopes/YYYY-MM-DD/<submission_id>.json   ← the envelope itself
      files/voice/<submission_id>.oga             ← downloaded media (bot fills path)
      files/photo/<submission_id>.jpg
      inbox/<submission_id>.md                    ← human-readable intake card
      state/                                      ← polling offset, check-in state

The long-polling runtime, the Bot API client, and the conversational flows live
in ``openhealth.telegram_bot``. Keeping this half free of network code makes the
envelope contract unit-testable and reusable by any future transport (webhook,
export import, …).

Privacy: an allowlist of chat ids is mandatory. Updates from anyone else are
never converted or written — the policy helpers live here so every transport
enforces the same rule.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

CHANNEL = "telegram"
SOURCE = "telegram"
# Mirrors the "telegram-intake" entry in openhealth.config.SOURCE_TYPES.
SOURCE_ID = "telegram-intake"

ENV_ALLOWLIST = "OPENHEALTH_TG_CHAT_ID"
DEFAULT_ALLOWLIST_PATH = Path.home() / ".openhealth" / "telegram.allowlist"

# Message kinds this intake understands. Anything else (stickers, polls,
# locations, …) is ignored by design — low friction beats completeness.
KIND_TEXT = "text"
KIND_VOICE = "voice"
KIND_PHOTO = "photo"
KIND_CHECKIN = "checkin"


# --- helpers -----------------------------------------------------------------


def iso_utc(ts: Optional[int]) -> str:
    """Unix seconds → ISO-8601 UTC; falls back to *now* when absent."""
    if ts is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(microsecond=0).isoformat()


def classify_message(message: Dict[str, Any]) -> Optional[str]:
    """text / voice / photo, or None for kinds this intake does not handle."""
    if not isinstance(message, dict):
        return None
    if message.get("voice"):
        return KIND_VOICE
    if message.get("photo"):
        return KIND_PHOTO
    if isinstance(message.get("text"), str) and message["text"].strip():
        return KIND_TEXT
    return None


def message_author(message: Dict[str, Any]) -> str:
    """Stable, human-readable author label (never invents data)."""
    sender = message.get("from") or {}
    username = sender.get("username")
    if username:
        return str(username)
    name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p)
    if name:
        return name
    chat_id = (message.get("chat") or {}).get("id")
    return "chat:{}".format(chat_id) if chat_id is not None else "unknown"


def largest_photo(sizes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Telegram sends several downscaled variants; keep the largest one."""
    best = None
    best_area = -1
    for size in sizes or []:
        if not isinstance(size, dict):
            continue
        area = int(size.get("width") or 0) * int(size.get("height") or 0)
        if area > best_area:
            best, best_area = size, area
    return best


# --- update → envelope ---------------------------------------------------------


def update_to_envelope(update: Dict[str, Any], received_at: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Convert one Bot API update into an IntakeEnvelope dict.

    Returns None for updates this intake does not handle (no message, no chat
    id, unsupported content kind). Attachment ``path`` values start as None and
    are filled in by the runtime after a successful download.
    """
    message = update.get("message") if isinstance(update, dict) else None
    if not isinstance(message, dict):
        return None
    kind = classify_message(message)
    if kind is None:
        return None
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        return None

    ts = message.get("date")
    message_id = message.get("message_id")
    reply_to = message.get("reply_to_message")
    reply_to_message_id = (
        reply_to.get("message_id") if isinstance(reply_to, dict) else None
    )
    submission_id = "tg-{}-{}".format(chat_id, message_id if message_id is not None else update.get("update_id"))

    text = message.get("text") if kind == KIND_TEXT else message.get("caption")
    if isinstance(text, str):
        text = text.strip() or None

    attachments = []  # type: List[Dict[str, Any]]
    if kind == KIND_VOICE:
        voice = message.get("voice") or {}
        attachments.append(
            {
                "kind": "voice",
                "file_id": voice.get("file_id"),
                "file_unique_id": voice.get("file_unique_id"),
                "duration_s": voice.get("duration"),
                "mime_type": voice.get("mime_type") or "audio/ogg",
                "path": None,  # set by the runtime after download (relative to data-dir)
                "transcript": None,  # TODO hook: local transcription fills this later
            }
        )
    elif kind == KIND_PHOTO:
        best = largest_photo(message.get("photo") or [])
        if best is None:
            return None
        attachments.append(
            {
                "kind": "photo",
                "file_id": best.get("file_id"),
                "file_unique_id": best.get("file_unique_id"),
                "width": best.get("width"),
                "height": best.get("height"),
                "file_size": best.get("file_size"),
                "path": None,  # set by the runtime after download (relative to data-dir)
            }
        )

    envelope = {
        # schema-required (schemas/intake-envelope.schema.json)
        "submission_id": submission_id,
        "submitted_at": iso_utc(ts),
        "channel": CHANNEL,
        "author": message_author(message),
        # flat agent-facing contract
        "type": kind,
        "text": text,
        "ts": ts,
        "chat_id": chat_id,
        "source": SOURCE,
        # schema-optional
        "location": None,
        "attachments": attachments,
        "tags": [CHANNEL, kind],
        "metadata": {
            "update_id": update.get("update_id"),
            "message_id": message_id,
            "reply_to_message_id": reply_to_message_id,
            "from_id": (message.get("from") or {}).get("id"),
            "media_group_id": message.get("media_group_id"),
            "received_at": received_at or iso_utc(None),
        },
    }
    if kind == KIND_VOICE:
        envelope["transcript"] = None  # TODO hook mirrored at top level for voice
    return envelope


def checkin_envelope(
    chat_id: int,
    author: str,
    answers: Dict[str, str],
    started_at: Optional[str] = None,
    ts: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a journal-style envelope out of completed /checkin answers."""
    submitted_at = iso_utc(ts)
    day = submitted_at[:10]
    text = "; ".join("{}: {}".format(key, value) for key, value in answers.items()) or None
    return {
        "submission_id": "tg-{}-checkin-{}".format(chat_id, day),
        "submitted_at": submitted_at,
        "channel": CHANNEL,
        "author": author,
        "type": KIND_CHECKIN,
        "text": text,
        "ts": ts,
        "chat_id": chat_id,
        "source": SOURCE,
        "location": None,
        "attachments": [],
        "tags": [CHANNEL, KIND_CHECKIN, "journal"],
        "metadata": {
            "checkin": dict(answers),
            "started_at": started_at,
            "received_at": iso_utc(None),
        },
    }


# --- local persistence ----------------------------------------------------------


def atomic_write_text(path: Path, content: str) -> None:
    """Write via temp file + rename so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def envelope_path(envelope: Dict[str, Any], data_dir: Path) -> Path:
    day = str(envelope.get("submitted_at") or "")[:10] or "undated"
    return Path(data_dir) / "envelopes" / day / "{}.json".format(envelope["submission_id"])


def write_envelope(envelope: Dict[str, Any], data_dir: Path) -> Path:
    """Persist the envelope JSON. Same submission_id → same path, so an
    at-least-once redelivery overwrites instead of duplicating."""
    path = envelope_path(envelope, data_dir)
    atomic_write_text(path, json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
    return path


def render_card(envelope: Dict[str, Any], envelope_file: Optional[Path] = None) -> str:
    """Human-readable markdown intake card for the inbox folder."""
    lines = [
        "# Telegram intake — {}".format(envelope.get("submitted_at", "")),
        "",
        "- type: {}".format(envelope.get("type")),
        "- author: {}".format(envelope.get("author")),
        "- chat_id: {}".format(envelope.get("chat_id")),
        "- submission_id: {}".format(envelope.get("submission_id")),
    ]
    if envelope_file is not None:
        lines.append("- envelope: {}".format(envelope_file))
    for att in envelope.get("attachments") or []:
        descr = att.get("kind", "file")
        if att.get("duration_s") is not None:
            descr += ", {}s".format(att["duration_s"])
        if att.get("path"):
            descr += " → {}".format(att["path"])
        if "transcript" in att:
            descr += ", transcript: TODO" if att.get("transcript") is None else ", transcript: yes"
        lines.append("- attachment: {}".format(descr))
    text = envelope.get("text")
    if text:
        lines.extend(["", "## Text", "", str(text)])
    transcript = envelope.get("transcript")
    if transcript:
        lines.extend(["", "## Transcript", "", str(transcript)])
    checkin = (envelope.get("metadata") or {}).get("checkin")
    if checkin:
        lines.extend(["", "## Check-in", ""])
        lines.extend("- {}: {}".format(key, value) for key, value in checkin.items())
    lines.append("")
    return "\n".join(lines)


def write_card(envelope: Dict[str, Any], inbox_dir: Path, envelope_file: Optional[Path] = None) -> Path:
    path = Path(inbox_dir) / "{}.md".format(envelope["submission_id"])
    atomic_write_text(path, render_card(envelope, envelope_file=envelope_file))
    return path


# --- allowlist (mandatory privacy gate) ------------------------------------------


def parse_allowlist_text(text: str) -> Set[int]:
    """One chat id per line; ``#`` comments and blank lines are fine.
    Comma/space separated values are accepted too (env-style)."""
    allowed = set()  # type: Set[int]
    for raw_line in (text or "").splitlines():
        line = raw_line.split("#", 1)[0]
        for token in line.replace(",", " ").split():
            try:
                allowed.add(int(token))
            except ValueError:
                continue  # silently skip garbage — never crash the privacy gate open
    return allowed


def load_allowlist(
    env: Optional[Dict[str, str]] = None,
    path: Optional[Path] = None,
) -> Set[int]:
    """Union of env OPENHEALTH_TG_CHAT_ID and ~/.openhealth/telegram.allowlist.

    Empty result means "not configured" — the runtime must refuse to start
    rather than answer strangers.
    """
    env = os.environ if env is None else env
    path = DEFAULT_ALLOWLIST_PATH if path is None else Path(path)
    allowed = parse_allowlist_text(env.get(ENV_ALLOWLIST, ""))
    try:
        allowed |= parse_allowlist_text(path.read_text(encoding="utf-8"))
    except OSError:
        pass
    return allowed


def is_allowed(chat_id: Any, allowlist: Set[int]) -> bool:
    try:
        return int(chat_id) in allowlist
    except (TypeError, ValueError):
        return False
