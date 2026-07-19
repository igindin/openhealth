"""Telegram bot for OpenHealth daily multimodal intake.

Handles photos (body zone tagging), voice notes (transcription),
text messages, and reactive checklists. Creates IntakeEnvelopes
and feeds them into the ingest pipeline.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import evidence
from .config import build_paths
from .ingest import ingest_path
from .models import BodyZone
from .storage import ensure_repo_structure, now_utc

# Caption/filename hints that route an uploaded document to the lab-panel parser
# rather than the generic document parser. The two escaped entries are Russian
# word stems, kept so that Russian-language captions and filenames still reach
# the lab parser: "analiz" (lab test / analysis) and "krov" (stem of "blood").
LAB_HINTS = ("lab", "blood", "panel", "\u0430\u043d\u0430\u043b\u0438\u0437", "\u043a\u0440\u043e\u0432", "biomarker", "result")

logger = logging.getLogger(__name__)

BODY_ZONE_LABELS = {
    BodyZone.FACE: "Face",
    BodyZone.EYES: "Eyes",
    BodyZone.EYELIDS: "Eyelids",
    BodyZone.SCALP: "Scalp / Hair",
    BodyZone.NECK: "Neck",
    BodyZone.CHEST: "Chest",
    BodyZone.ARMS: "Arms / Hands",
    BodyZone.TORSO: "Torso",
    BodyZone.LEGS: "Legs / Feet",
    BodyZone.CUSTOM: "Other",
}

VISIBLE_ATTR_OPTIONS = [
    "redness",
    "puffiness",
    "dryness",
    "irritation",
    "swelling",
    "breakout_intensity",
    "discoloration",
    "texture_change",
]


def _build_zone_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for zone in BodyZone:
        if zone == BodyZone.CUSTOM:
            continue
        label = BODY_ZONE_LABELS.get(zone, zone.value)
        row.append(InlineKeyboardButton(label, callback_data="zone:%s" % zone.value))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Other / Skip", callback_data="zone:custom")])
    return InlineKeyboardMarkup(buttons)


def _build_attr_keyboard(selected: List[str]) -> InlineKeyboardMarkup:
    buttons = []
    row = []
    for attr in VISIBLE_ATTR_OPTIONS:
        mark = "* " if attr in selected else ""
        row.append(InlineKeyboardButton(
            "%s%s" % (mark, attr.replace("_", " ")),
            callback_data="attr:%s" % attr,
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("Done", callback_data="attr:done")])
    return InlineKeyboardMarkup(buttons)


def _create_envelope(
    author: str,
    text: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "submission_id": "tg-%s" % datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
        "submitted_at": now_utc(),
        "channel": "telegram",
        "author": author,
        "text": text,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "attachments": attachments or [],
        "tags": tags or [],
        "metadata": metadata or {},
    }


def _save_envelope(repo_root: Path, envelope: Dict[str, Any]) -> Path:
    """Save envelope JSON to inbox and trigger ingest."""
    paths = ensure_repo_structure(repo_root)
    intake_dir = paths.raw_inbox / "telegram-intake"
    intake_dir.mkdir(parents=True, exist_ok=True)
    envelope_path = intake_dir / ("%s.json" % envelope["submission_id"])
    envelope_path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    return envelope_path


def _ingest_envelope(repo_root: Path, envelope_path: Path) -> Dict[str, object]:
    """Run the ingest pipeline on a saved envelope."""
    return ingest_path(
        root=repo_root,
        source_type="telegram-intake",
        path=envelope_path,
        label=envelope_path.stem,
    )


class HealthBot:
    def __init__(self, token: str, repo_root: Path, allowed_users: Optional[List[int]] = None):
        self.token = token
        self.repo_root = repo_root.resolve()
        self.allowed_users = allowed_users
        self._pending_photos: Dict[int, Dict[str, Any]] = {}

    def _is_authorized(self, user_id: int) -> bool:
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update.effective_user.id):
            await update.message.reply_text("Not authorized.")
            return
        await update.message.reply_text(
            "OpenHealth\n\n"
            "Send me:\n"
            "- Photos (face, eyes, skin) for body zone tracking\n"
            "- Lab reports (PDF/CSV) - I will flag values in/out of range\n"
            "- Voice notes for journal entries\n"
            "- Text notes for quick observations\n\n"
            "Commands:\n"
            "/checkin - Quick daily check-in\n"
            "/status - Show current data summary\n\n"
            "I never diagnose or prescribe. I organize your data and surface "
            "questions to discuss with a clinician."
        )

    async def cmd_checkin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update.effective_user.id):
            return
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Great", callback_data="checkin:great"),
                InlineKeyboardButton("Good", callback_data="checkin:good"),
            ],
            [
                InlineKeyboardButton("Okay", callback_data="checkin:okay"),
                InlineKeyboardButton("Bad", callback_data="checkin:bad"),
            ],
        ])
        await update.message.reply_text("How are you feeling right now?", reply_markup=keyboard)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update.effective_user.id):
            return
        from . import index as idx
        paths = ensure_repo_structure(self.repo_root)
        idx.init_db(paths.db_path)
        sources = idx.list_sources(paths.db_path)
        records = idx.list_records(paths.db_path)
        record_types: Dict[str, int] = {}
        for r in records:
            rt = r.get("record_type", "unknown")
            record_types[rt] = record_types.get(rt, 0) + 1
        lines = [
            "OpenHealth Status",
            "Sources: %d" % len(sources),
            "Records: %d" % len(records),
        ]
        for rt, count in sorted(record_types.items()):
            lines.append("  %s: %d" % (rt, count))
        await update.message.reply_text("\n".join(lines))

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update.effective_user.id):
            return
        user_id = update.effective_user.id
        photo = update.message.photo[-1]  # highest resolution
        file = await photo.get_file()

        # Download to temp location
        paths = ensure_repo_structure(self.repo_root)
        media_dir = paths.raw_inbox / "telegram-intake" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        filename = "photo-%s-%s.jpg" % (
            datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"),
            photo.file_unique_id,
        )
        local_path = media_dir / filename
        await file.download_to_drive(str(local_path))

        # Store pending photo and ask for body zone
        self._pending_photos[user_id] = {
            "file_path": str(local_path),
            "caption": update.message.caption or "",
            "timestamp": now_utc(),
            "selected_attrs": [],
        }
        await update.message.reply_text(
            "Got the photo. What body zone is this?",
            reply_markup=_build_zone_keyboard(),
        )

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update.effective_user.id):
            return
        voice = update.message.voice or update.message.audio
        if not voice:
            return
        file = await voice.get_file()

        paths = ensure_repo_structure(self.repo_root)
        media_dir = paths.raw_inbox / "telegram-intake" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        filename = "voice-%s.ogg" % datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        local_path = media_dir / filename
        await file.download_to_drive(str(local_path))

        author = update.effective_user.username or str(update.effective_user.id)
        envelope = _create_envelope(
            author=author,
            text="[Voice note: %s, duration: %ss]" % (filename, voice.duration),
            attachments=[{
                "type": "voice",
                "file_path": str(local_path),
                "duration": voice.duration,
            }],
            tags=["voice-note", "journal"],
        )
        envelope_path = _save_envelope(self.repo_root, envelope)
        result = _ingest_envelope(self.repo_root, envelope_path)
        await update.message.reply_text(
            "Voice note saved. %d record(s) created." % result.get("records_imported", 0)
        )

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update.effective_user.id):
            return
        document = update.message.document
        if not document:
            return
        file = await document.get_file()
        paths = ensure_repo_structure(self.repo_root)
        media_dir = paths.raw_inbox / "documents"
        media_dir.mkdir(parents=True, exist_ok=True)
        safe_name = document.file_name or ("document-%s" % datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"))
        local_path = media_dir / safe_name
        await file.download_to_drive(str(local_path))

        # Route to the lab-panel parser when the caption or filename suggests a
        # blood/lab report; otherwise treat as a generic document.
        caption = (update.message.caption or "").lower()
        name_l = safe_name.lower()
        source_type = "lab-panel" if any(h in caption or h in name_l for h in LAB_HINTS) else "document-tests"
        try:
            result = ingest_path(self.repo_root, source_type, local_path, label=safe_name)
        except Exception as exc:  # surface ingest errors to the user, keep file
            logger.exception("Document ingest failed")
            await update.message.reply_text("Could not process that file: %s" % exc)
            return
        kind = "lab panel" if source_type == "lab-panel" else "document"
        await update.message.reply_text(
            "Saved %s. %d record(s) created. Send /status to see your data."
            % (kind, result.get("records_imported", 0))
        )

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update.effective_user.id):
            return
        text = update.message.text
        if not text:
            return

        # Safety first: if the message mentions a red-flag symptom, respond
        # immediately and route to care. The note is still saved for the record.
        red_flags = evidence.scan_text_red_flags(text)
        if red_flags:
            messages = "\n".join("- %s" % f.message for f in red_flags)
            await update.message.reply_text(
                "I noticed something that needs a real clinician, not an app:\n%s\n\n"
                "Please seek medical care. I will not try to interpret this." % messages
            )

        author = update.effective_user.username or str(update.effective_user.id)
        envelope = _create_envelope(
            author=author,
            text=text,
            tags=["text-note"],
        )
        envelope_path = _save_envelope(self.repo_root, envelope)
        result = _ingest_envelope(self.repo_root, envelope_path)
        if not red_flags:
            await update.message.reply_text(
                "Noted. %d record(s) created." % result.get("records_imported", 0)
            )

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        if not self._is_authorized(user_id):
            return
        data = query.data

        # Body zone selection for pending photo
        if data.startswith("zone:"):
            zone = data.split(":", 1)[1]
            pending = self._pending_photos.get(user_id)
            if not pending:
                await query.edit_message_text("No pending photo. Send a photo first.")
                return
            pending["body_zone"] = zone
            await query.edit_message_text(
                "Zone: %s. Any visible changes?" % zone,
                reply_markup=_build_attr_keyboard(pending.get("selected_attrs", [])),
            )

        # Visible attribute selection
        elif data.startswith("attr:"):
            attr = data.split(":", 1)[1]
            pending = self._pending_photos.get(user_id)
            if not pending:
                await query.edit_message_text("No pending photo.")
                return
            if attr == "done":
                # Finalize the photo observation
                author = query.from_user.username or str(user_id)
                envelope = _create_envelope(
                    author=author,
                    text=pending.get("caption") or "Photo observation",
                    attachments=[{
                        "type": "photo",
                        "file_path": pending["file_path"],
                        "body_zone": pending.get("body_zone", "custom"),
                        "visible_attributes": pending.get("selected_attrs", []),
                        "caption": pending.get("caption", ""),
                    }],
                    tags=["photo-observation", "body-zone-%s" % pending.get("body_zone", "custom")],
                )
                envelope_path = _save_envelope(self.repo_root, envelope)
                result = _ingest_envelope(self.repo_root, envelope_path)
                del self._pending_photos[user_id]
                zone = pending.get("body_zone", "custom")
                attrs = pending.get("selected_attrs", [])
                summary = "Saved %s observation" % zone
                if attrs:
                    summary += " (%s)" % ", ".join(attrs)
                summary += ". %d record(s) created." % result.get("records_imported", 0)
                await query.edit_message_text(summary)
            else:
                selected = pending.setdefault("selected_attrs", [])
                if attr in selected:
                    selected.remove(attr)
                else:
                    selected.append(attr)
                await query.edit_message_reply_markup(
                    reply_markup=_build_attr_keyboard(selected),
                )

        # Check-in response
        elif data.startswith("checkin:"):
            feeling = data.split(":", 1)[1]
            author = query.from_user.username or str(user_id)
            envelope = _create_envelope(
                author=author,
                text="Daily check-in: feeling %s" % feeling,
                tags=["checkin", "mood-%s" % feeling],
                metadata={"checkin_response": feeling},
            )
            envelope_path = _save_envelope(self.repo_root, envelope)
            result = _ingest_envelope(self.repo_root, envelope_path)
            await query.edit_message_text(
                "Recorded: feeling %s. %d record(s) created." % (feeling, result.get("records_imported", 0))
            )

    def build_app(self) -> Application:
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("checkin", self.cmd_checkin))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.handle_voice))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text))
        app.add_handler(CallbackQueryHandler(self.handle_callback))
        return app

    def run(self) -> None:
        logger.info("Starting OpenHealth bot (polling mode)...")
        app = self.build_app()
        app.run_polling(drop_pending_updates=True)


def start_bot(repo_root: Path) -> None:
    token = os.environ.get("OPENHEALTH_TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError(
            "OPENHEALTH_TELEGRAM_BOT_TOKEN not set. "
            "Create a bot via @BotFather and set the token."
        )
    allowed_str = os.environ.get("OPENHEALTH_TELEGRAM_ALLOWED_USERS", "")
    allowed_users = [int(uid.strip()) for uid in allowed_str.split(",") if uid.strip()] or None

    bot = HealthBot(token=token, repo_root=repo_root, allowed_users=allowed_users)
    bot.run()
