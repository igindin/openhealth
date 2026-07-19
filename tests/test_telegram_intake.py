"""Unit tests for the Telegram channel: intake connector + stdlib bot runtime.

No real network anywhere: the Bot API client gets a fake ``opener``, the bot
gets a fake API object, /ask gets fake ``which``/``runner``. All fixtures are
synthetic Bot API update objects.

Run:  python3 -m pytest tests/test_telegram_intake.py
"""

import io
import json
import urllib.error
from pathlib import Path

import pytest

from openhealth import telegram_bot as tb
from openhealth.connectors import telegram_intake as intake

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "intake-envelope.schema.json"


# --- non-ASCII input the bot must round-trip byte-for-byte -------------------
# The channel is used in Russian, so Cyrillic text has to survive the envelope
# and the markdown card unchanged. Written as escapes to keep this file ASCII.
CYRILLIC_NOTE = "\u0441\u043f\u0430\u043b 6 \u0447\u0430\u0441\u043e\u0432"  # "slept 6 hours"


# --- fixtures: synthetic Bot API updates -------------------------------------


def text_update(update_id=1, chat_id=111, message_id=42, text="slept 6 hours", date=1760000000):
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": date,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "username": "ilya", "first_name": "Ilya"},
            "text": text,
        },
    }


def voice_update(update_id=2, chat_id=111):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 43,
            "date": 1760000100,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "first_name": "Ilya"},
            "voice": {
                "file_id": "VOICE_FILE_ID",
                "file_unique_id": "uniq-voice",
                "duration": 12,
                "mime_type": "audio/ogg",
            },
        },
    }


def photo_update(update_id=3, chat_id=111, caption="rash on my arm"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 44,
            "date": 1760000200,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "username": "ilya"},
            "caption": caption,
            "photo": [
                {"file_id": "PHOTO_SMALL", "file_unique_id": "u1", "width": 90, "height": 90},
                {"file_id": "PHOTO_BIG", "file_unique_id": "u2", "width": 800, "height": 600},
            ],
        },
    }


# --- update → envelope --------------------------------------------------------


def test_text_update_to_envelope():
    env = intake.update_to_envelope(text_update())
    assert env["type"] == "text"
    assert env["text"] == "slept 6 hours"
    assert env["chat_id"] == 111
    assert env["ts"] == 1760000000
    assert env["source"] == "telegram"
    assert env["channel"] == "telegram"
    assert env["author"] == "ilya"
    assert env["submission_id"] == "tg-111-42"
    assert env["submitted_at"].startswith("2025-10-09")
    assert env["attachments"] == []
    assert env["metadata"]["update_id"] == 1


def test_voice_update_to_envelope_has_transcript_todo():
    env = intake.update_to_envelope(voice_update())
    assert env["type"] == "voice"
    assert env["transcript"] is None  # top-level TODO hook
    (att,) = env["attachments"]
    assert att["kind"] == "voice"
    assert att["file_id"] == "VOICE_FILE_ID"
    assert att["duration_s"] == 12
    assert att["transcript"] is None
    assert att["path"] is None  # runtime fills after download


def test_photo_update_keeps_caption_and_largest_size():
    env = intake.update_to_envelope(photo_update())
    assert env["type"] == "photo"
    assert env["text"] == "rash on my arm"
    (att,) = env["attachments"]
    assert att["file_id"] == "PHOTO_BIG"
    assert att["width"] == 800


def test_unsupported_updates_are_ignored():
    sticker = {"update_id": 9, "message": {"message_id": 1, "chat": {"id": 111}, "sticker": {"file_id": "x"}}}
    assert intake.update_to_envelope(sticker) is None
    assert intake.update_to_envelope({"update_id": 10}) is None  # no message at all
    no_chat = {"update_id": 11, "message": {"message_id": 2, "text": "hi", "chat": {}}}
    assert intake.update_to_envelope(no_chat) is None


def test_envelope_satisfies_schema_required_fields():
    required = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["required"]
    for update in (text_update(), voice_update(), photo_update()):
        env = intake.update_to_envelope(update)
        for key in required:
            assert key in env and env[key] is not None, "missing {}".format(key)


def test_checkin_envelope_shape():
    answers = {"sleep": "7", "workout": "yes, gym", "alcohol": "no", "wellbeing": "4"}
    env = intake.checkin_envelope(111, "ilya", answers, started_at="2026-06-10T08:00:00+00:00", ts=1760000300)
    assert env["type"] == "checkin"
    assert env["metadata"]["checkin"] == answers
    assert "journal" in env["tags"]
    assert "sleep: 7" in env["text"]
    assert env["submission_id"].startswith("tg-111-checkin-")


# --- local persistence ----------------------------------------------------------


def test_write_envelope_and_card(tmp_path):
    # Cyrillic body on purpose: the envelope and the card must keep it intact.
    env = intake.update_to_envelope(text_update(text=CYRILLIC_NOTE))
    env_path = intake.write_envelope(env, tmp_path)
    assert env_path == tmp_path / "envelopes" / "2025-10-09" / "tg-111-42.json"
    stored = json.loads(env_path.read_text(encoding="utf-8"))
    assert stored == env
    assert stored["text"] == CYRILLIC_NOTE

    card_path = intake.write_card(env, tmp_path / "inbox", envelope_file=env_path)
    card = card_path.read_text(encoding="utf-8")
    assert CYRILLIC_NOTE in card
    assert "tg-111-42" in card

    # same submission_id overwrites (at-least-once redelivery, no duplicates)
    intake.write_envelope(env, tmp_path)
    assert len(list((tmp_path / "envelopes").rglob("*.json"))) == 1


# --- allowlist (privacy gate) -----------------------------------------------------


def test_parse_allowlist_text_lines_comments_commas():
    text = "# my chats\n111\n222, 333\ngarbage\n  444  # tail comment\n"
    assert intake.parse_allowlist_text(text) == {111, 222, 333, 444}
    assert intake.parse_allowlist_text("") == set()


def test_load_allowlist_unions_env_and_file(tmp_path):
    allow_file = tmp_path / "telegram.allowlist"
    allow_file.write_text("222\n", encoding="utf-8")
    allowed = intake.load_allowlist(env={intake.ENV_ALLOWLIST: "111"}, path=allow_file)
    assert allowed == {111, 222}
    # missing file → env only; empty everything → empty set (runtime must refuse)
    assert intake.load_allowlist(env={intake.ENV_ALLOWLIST: "111"}, path=tmp_path / "nope") == {111}
    assert intake.load_allowlist(env={}, path=tmp_path / "nope") == set()


def test_is_allowed():
    assert intake.is_allowed(111, {111})
    assert intake.is_allowed("111", {111})
    assert not intake.is_allowed(999, {111})
    assert not intake.is_allowed(None, {111})


# --- offset persistence ------------------------------------------------------------


def test_offset_roundtrip(tmp_path):
    path = tmp_path / "state" / "offset.json"
    assert tb.load_offset(path) is None  # fresh start
    tb.store_offset(path, 1007)
    assert tb.load_offset(path) == 1007
    path.write_text("not json", encoding="utf-8")
    assert tb.load_offset(path) is None  # broken file never crashes the loop


# --- TelegramAPI client (fake opener, no network) ------------------------------------


class FakeResponse:
    def __init__(self, body):
        self._stream = io.BytesIO(body)

    def read(self, n=-1):
        return self._stream.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Scripted urlopen replacement: each item is an Exception or a payload."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, bytes):
            return FakeResponse(item)
        return FakeResponse(json.dumps(item).encode("utf-8"))


def http_error(code, payload=None):
    body = io.BytesIO(json.dumps(payload).encode("utf-8") if payload is not None else b"")
    return urllib.error.HTTPError("https://api.telegram.org/x", code, "err", {}, body)


def make_api(script, max_tries=5):
    opener = FakeOpener(script)
    sleeps = []
    api = tb.TelegramAPI("123:TESTTOKEN", opener=opener, sleep=sleeps.append, max_tries=max_tries)
    return api, opener, sleeps


def test_api_retries_transient_errors_then_succeeds():
    api, opener, sleeps = make_api(
        [urllib.error.URLError("down"), OSError("reset"), {"ok": True, "result": {"x": 1}}]
    )
    assert api.call("getMe") == {"x": 1}
    assert len(opener.requests) == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential backoff grows


def test_api_honors_retry_after_on_429():
    api, _, sleeps = make_api(
        [http_error(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 7}}),
         {"ok": True, "result": []}]
    )
    assert api.call("sendMessage", {"chat_id": 1, "text": "hi"}) == []
    assert sleeps and sleeps[0] >= 7


def test_api_bad_token_fails_fast_without_retry():
    api, opener, sleeps = make_api(
        [http_error(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})]
    )
    with pytest.raises(tb.TelegramAPIError):
        api.call("getMe")
    assert len(opener.requests) == 1  # no retries on a bad token
    assert sleeps == []


def test_api_gives_up_after_max_tries():
    api, opener, _ = make_api([urllib.error.URLError("down")] * 3, max_tries=3)
    with pytest.raises(tb.TelegramNetworkError):
        api.call("getUpdates")
    assert len(opener.requests) == 3


def test_api_download_writes_file_and_caps_size(tmp_path):
    api, _, _ = make_api(
        [{"ok": True, "result": {"file_path": "voice/file_1.oga"}}, b"OGGDATA"]
    )
    dest = tmp_path / "files" / "voice" / "x.oga"
    api.download_file("FILE_ID", dest)
    assert dest.read_bytes() == b"OGGDATA"

    api2, _, _ = make_api(
        [{"ok": True, "result": {"file_path": "voice/file_2.oga"}}, b"X" * 64]
    )
    with pytest.raises(tb.TelegramAPIError):
        api2.download_file("FILE_ID", tmp_path / "big.oga", max_bytes=10)
    assert not (tmp_path / "big.oga").exists()  # no half-written file


# --- Bot routing (fake API object) ----------------------------------------------------


class FakeAPI:
    def __init__(self):
        self.sent = []
        self.downloads = []

    def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))

    def download_file(self, file_id, dest, max_bytes=None):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"BYTES")
        self.downloads.append((file_id, dest))
        return dest


class FailingDownloadAPI(FakeAPI):
    def download_file(self, file_id, dest, max_bytes=None):
        raise tb.TelegramNetworkError("offline")


def make_bot(tmp_path, api=None, allowlist=None, **config_kw):
    api = api or FakeAPI()
    config = tb.BotConfig(
        data_dir=tmp_path / "intake",
        allowlist=allowlist if allowlist is not None else {111},
        today_file=tmp_path / "data.local.json",
        **config_kw
    )
    return tb.Bot(api, config), api, config


def stored_envelopes(config):
    return sorted((config.data_dir / "envelopes").rglob("*.json")) if (config.data_dir / "envelopes").exists() else []


def test_bot_denies_stranger_and_stores_nothing(tmp_path):
    bot, api, config = make_bot(tmp_path)
    bot.handle_update(text_update(chat_id=999))
    bot.handle_update({"update_id": 5, "message": {"message_id": 9, "chat": {"id": 999}, "text": "/checkin"}})
    assert [chat for chat, _ in api.sent] == [999, 999]
    assert all(text == tb.DENIED_TEXT for _, text in api.sent)
    assert stored_envelopes(config) == []  # nothing of theirs is ever written
    assert not bot.checkin.active(999)


def test_bot_stores_text_intake_with_card(tmp_path):
    bot, api, config = make_bot(tmp_path)
    bot.handle_update(text_update())
    (env_path,) = stored_envelopes(config)
    env = json.loads(env_path.read_text(encoding="utf-8"))
    assert env["text"] == "slept 6 hours"
    assert (config.inbox_dir / "tg-111-42.md").exists()
    assert api.sent[-1][1] == "Saved to the journal."


def test_bot_downloads_voice_and_fills_path(tmp_path):
    bot, api, config = make_bot(tmp_path)
    bot.handle_update(voice_update())
    (env_path,) = stored_envelopes(config)
    env = json.loads(env_path.read_text(encoding="utf-8"))
    (att,) = env["attachments"]
    assert att["path"] == "files/voice/tg-111-43.oga"
    assert (config.data_dir / att["path"]).read_bytes() == b"BYTES"
    assert env["transcript"] is None
    assert "Transcription" in api.sent[-1][1]


def test_bot_keeps_envelope_when_download_fails(tmp_path):
    bot, api, config = make_bot(tmp_path, api=FailingDownloadAPI())
    bot.handle_update(photo_update())
    (env_path,) = stored_envelopes(config)
    env = json.loads(env_path.read_text(encoding="utf-8"))
    (att,) = env["attachments"]
    assert att["path"] is None
    assert "download_error" in att
    assert "could not be downloaded" in api.sent[-1][1]


def test_bot_help_and_unknown_command(tmp_path):
    bot, api, _ = make_bot(tmp_path)
    bot.handle_update(text_update(text="/help"))
    assert api.sent[-1][1] == tb.HELP_TEXT
    bot.handle_update(text_update(text="/frobnicate"))
    assert "/help" in api.sent[-1][1]


# --- /checkin state machine --------------------------------------------------------


def test_checkin_full_flow_writes_journal_envelope(tmp_path):
    bot, api, config = make_bot(tmp_path)
    bot.handle_update(text_update(text="/checkin", message_id=50))
    assert tb.CHECKIN_QUESTIONS[0][1] in api.sent[-1][1]

    answers = ["7", "yes, gym", "no", "4"]
    for i, answer in enumerate(answers):
        bot.handle_update(text_update(text=answer, message_id=51 + i))

    assert api.sent[-1][1] == tb.CHECKIN_DONE_TEXT
    env_path = next(p for p in stored_envelopes(config) if "checkin" in p.name)
    env = json.loads(env_path.read_text(encoding="utf-8"))
    assert env["type"] == "checkin"
    assert env["metadata"]["checkin"] == {"sleep": "7", "workout": "yes, gym", "alcohol": "no", "wellbeing": "4"}
    assert not bot.checkin.active(111)


def test_checkin_state_survives_restart(tmp_path):
    bot1, api1, config = make_bot(tmp_path)
    bot1.handle_update(text_update(text="/checkin", message_id=60))
    bot1.handle_update(text_update(text="7", message_id=61))
    assert tb.CHECKIN_QUESTIONS[1][1] in api1.sent[-1][1]

    # new process: fresh Bot over the same state file resumes mid-dialog
    bot2 = tb.Bot(FakeAPI(), config)
    assert bot2.checkin.active(111)
    bot2.handle_update(text_update(text="no", message_id=62))
    assert tb.CHECKIN_QUESTIONS[2][1] in bot2.api.sent[-1][1]


def test_checkin_cancel(tmp_path):
    bot, api, _ = make_bot(tmp_path)
    bot.handle_update(text_update(text="/cancel"))
    assert "nothing to cancel" in api.sent[-1][1].lower()
    bot.handle_update(text_update(text="/checkin"))
    bot.handle_update(text_update(text="/cancel"))
    assert "cancelled" in api.sent[-1][1].lower()
    assert not bot.checkin.active(111)
    # after cancel, plain text is plain intake again, not a checkin answer
    bot.handle_update(text_update(text="just a note", message_id=70))
    assert api.sent[-1][1] == "Saved to the journal."


# --- /today ---------------------------------------------------------------------


def test_today_summary_from_local_data(tmp_path):
    bot, api, config = make_bot(tmp_path)
    config.today_file.write_text(json.dumps(
        {"date": "2026-06-09", "recovery": 78, "hrv": 92.4, "rhr": 51, "sleep": 7.2, "sleepNeeded": 8.0,
         "strain": 12.3}
    ), encoding="utf-8")
    bot.handle_update(text_update(text="/today"))
    reply = api.sent[-1][1]
    assert "Recovery 78%" in reply and "green zone" in reply
    assert "HRV 92.4 ms" in reply
    assert "Sleep 7.2 h" in reply
    assert 2 <= len(reply.splitlines()) <= 5


def test_today_summary_honest_when_no_data(tmp_path):
    bot, api, _ = make_bot(tmp_path)
    bot.handle_update(text_update(text="/today"))
    assert "data.local.json not found" in api.sent[-1][1]


def test_recovery_zones():
    assert tb._recovery_zone(80) == "green zone"
    assert tb._recovery_zone(50) == "yellow zone"
    assert tb._recovery_zone(10) == "red zone"


# --- /ask agent bridge --------------------------------------------------------------


def test_ask_disabled_by_default(tmp_path):
    bot, api, _ = make_bot(tmp_path)
    bot.handle_update(text_update(text="/ask what about my HRV?"))
    assert "--enable-ask" in api.sent[-1][1]


def test_ask_enabled_but_no_cli_is_honest():
    answer = tb.ask_agent("what about my HRV?", "", which=lambda name: None)
    assert "not connected" in answer


def test_ask_runs_codex_cli_pattern():
    calls = {}

    class Proc:
        returncode = 0
        stdout = "Agent answer: HRV looks normal (C2)."
        stderr = ""

    def fake_runner(cmd, **kw):
        calls["cmd"] = cmd
        return Proc()

    answer = tb.ask_agent(
        "what about my HRV?", "- Recovery: 78%",
        which=lambda name: "/usr/local/bin/codex" if name == "codex" else None,
        runner=fake_runner,
    )
    assert answer.startswith("Agent answer")
    assert calls["cmd"][:2] == ["codex", "exec"]
    assert "--sandbox" in calls["cmd"] and "read-only" in calls["cmd"]
    prompt = calls["cmd"][-1]
    assert "what about my HRV?" in prompt and "Recovery: 78%" in prompt and "not a doctor" in prompt


def test_ask_question_required(tmp_path):
    bot, api, _ = make_bot(tmp_path, enable_ask=True)
    bot.handle_update(text_update(text="/ask"))
    assert "Usage" in api.sent[-1][1]


# --- long-polling loop: offset never lost, never duplicated ---------------------------


class LoopAPI(FakeAPI):
    def __init__(self, batches):
        super().__init__()
        self.batches = list(batches)

    def get_updates(self, offset, poll_timeout_s):
        self.last_offset = offset
        return self.batches.pop(0) if self.batches else []


def test_run_loop_once_advances_offset(tmp_path):
    api = LoopAPI([[text_update(update_id=100), text_update(update_id=101, message_id=43)]])
    config = tb.BotConfig(data_dir=tmp_path / "intake", allowlist={111})
    bot = tb.Bot(api, config)
    code = tb.run_loop(bot, api, config.offset_path, once=True)
    assert code == 0
    assert tb.load_offset(config.offset_path) == 102  # max update_id + 1
    assert len(stored_envelopes(config)) == 2

    # restart: next poll starts from the stored offset → no duplicates
    api2 = LoopAPI([[]])
    bot2 = tb.Bot(api2, config)
    tb.run_loop(bot2, api2, config.offset_path, once=True)
    assert api2.last_offset == 102


def test_run_loop_survives_handler_crash(tmp_path):
    api = LoopAPI([[{"update_id": 200, "message": "not-a-dict-boom"},
                    text_update(update_id=201)]])
    config = tb.BotConfig(data_dir=tmp_path / "intake", allowlist={111})
    bot = tb.Bot(api, config)
    assert tb.run_loop(bot, api, config.offset_path, once=True) == 0
    assert tb.load_offset(config.offset_path) == 202  # bad update skipped, offset still moves
    assert len(stored_envelopes(config)) == 1
