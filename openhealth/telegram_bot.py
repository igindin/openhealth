"""OpenHealth Telegram bot — stdlib-only long-polling intake + daily check-in.

    python3 -m openhealth.telegram_bot run [--data-dir DIR] [--enable-ask] [--once]
    python3 -m openhealth.telegram_bot check

Two-way agent channel over the Telegram Bot API, zero dependencies (repo core
rule): ``urllib.request`` for HTTP, ``json`` for payloads, ``pathlib`` for the
local intake folder. No webhook, no public port — the bot *pulls* updates via
``getUpdates`` long polling, so everything stays on this machine.

What it does:
  * text / voice / photo  → IntakeEnvelope JSON in ``<data-dir>/envelopes/`` +
    a markdown card in ``<data-dir>/inbox/`` (voice/photo files are downloaded
    into ``<data-dir>/files/``; voice transcription is a TODO hook,
    ``transcript`` stays ``null`` until a local transcriber exists).
  * /checkin — a 4-question daily check-in (sleep, workout, alcohol,
    wellbeing 1-5), one question at a time; answers land as a journal-style
    envelope. Dialog state lives in process memory *and* a JSON state file, so
    it survives a restart.
  * /today — a short daily summary read from a local ``data.local.json``
    (the same file the web dashboard uses); honest "no data" otherwise.
  * /ask <question> — optional local agent bridge: if a ``codex`` (or
    ``claude``) CLI is on PATH and the bot was started with ``--enable-ask``,
    the question runs locally with a compact data digest (same pattern as
    ``ui/web/server.py``); otherwise an honest "agent not connected" reply.

Privacy (non-negotiable):
  * the token comes from ``OPENHEALTH_TG_TOKEN`` or ``~/.openhealth/telegram.token``
    — never from the repo, never logged;
  * an allowlist of chat ids is mandatory (``OPENHEALTH_TG_CHAT_ID`` or
    ``~/.openhealth/telegram.allowlist``); strangers get «доступ не настроен»
    and *nothing* of theirs is ever stored;
  * message bodies are never written to the log — only chat ids, kinds and
    submission ids.

Reliability: exponential backoff on network errors, 429 ``retry_after``
honored, ``getUpdates`` offset persisted after every handled update (restart
neither loses nor duplicates: redelivery overwrites the same submission_id),
graceful SIGINT/SIGTERM, timeouts on every HTTP call including file downloads.
"""

import argparse
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .connectors import telegram_intake as intake

logger = logging.getLogger("openhealth.telegram_bot")

API_BASE = "https://api.telegram.org"

ENV_TOKEN = "OPENHEALTH_TG_TOKEN"
DEFAULT_TOKEN_PATH = Path.home() / ".openhealth" / "telegram.token"

DEFAULT_DATA_DIR = Path("data") / "intake" / "telegram"
DEFAULT_TODAY_FILE = Path("ui") / "web" / "data.local.json"

DEFAULT_POLL_TIMEOUT_S = 50  # Telegram long-poll hold; HTTP timeout adds margin on top
HTTP_TIMEOUT_MARGIN_S = 15
DOWNLOAD_TIMEOUT_S = 60
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # Bot API itself caps getFile at 20 MB
MAX_MESSAGE_CHARS = 4000  # Telegram hard limit is 4096; keep headroom
AGENT_TIMEOUT_S = 120

DENIED_TEXT = "Доступ не настроен. Этот бот — личный канал OpenHealth; сообщения чужих чатов не сохраняются."

HELP_TEXT = (
    "Я — локальный intake-канал OpenHealth. Всё, что ты присылаешь, остаётся на твоей машине.\n"
    "\n"
    "Просто пиши текстом, голосом или фото — я сохраню это в локальную health-папку.\n"
    "\n"
    "Команды:\n"
    "/checkin — чек-ин дня: 4 коротких вопроса по одному\n"
    "/today — краткая сводка дня (recovery / HRV / сон)\n"
    "/ask <вопрос> — спросить локального агента по твоим данным\n"
    "/cancel — прервать текущий чек-ин\n"
    "/help — эта справка\n"
    "\n"
    "Я не врач: не ставлю диагнозов и не назначаю лечение."
)

# Mirrors GREEN/YELLOW recovery zones in ui/web/build_dashboard_data.py.
RECOVERY_GREEN, RECOVERY_YELLOW = 67, 34

_SECRET_RE = re.compile(r"(bot\d+:[A-Za-z0-9_\-]+|sk-[A-Za-z0-9_\-]{10,}|Bearer\s+\S+)", re.IGNORECASE)

PROMPT_INTRO = "Ты — агент OpenHealth, локальный ассистент по личным данным здоровья."
PROMPT_SAFETY = (
    "Правила: ты не врач, не ставь диагнозов и не назначай лечение. Каждому выводу "
    "давай уровень доверия C1-C5; вывод C3 и ниже формулируй как вопрос-гипотезу. "
    "Red flags (боль в груди, обморок, мысли о причинении себе вреда, критические "
    "значения) — не интерпретируй, сразу советуй обратиться к врачу."
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", text or "")


# --- token --------------------------------------------------------------------


def load_token(env: Optional[Dict[str, str]] = None, path: Optional[Path] = None) -> Optional[str]:
    """OPENHEALTH_TG_TOKEN env var, else ~/.openhealth/telegram.token. None if absent."""
    env = os.environ if env is None else env
    token = (env.get(ENV_TOKEN) or "").strip()
    if token:
        return token
    path = DEFAULT_TOKEN_PATH if path is None else Path(path)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


# --- offset persistence ---------------------------------------------------------


def load_offset(path: Path) -> Optional[int]:
    """Next getUpdates offset from disk; None for a fresh start or a broken file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return int(data["offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store_offset(path: Path, offset: int) -> None:
    intake.atomic_write_text(Path(path), json.dumps({"offset": int(offset)}) + "\n")


# --- Telegram Bot API client (urllib only) ---------------------------------------


class TelegramAPIError(Exception):
    """Telegram replied ok=false (or HTTP error) and a retry will not help."""

    def __init__(self, description: str, error_code: Optional[int] = None):
        super().__init__(description)
        self.error_code = error_code


class TelegramNetworkError(Exception):
    """Transient failure: retries with backoff were exhausted."""


class TelegramAPI:
    """Minimal Bot API client: JSON POST + retries with exponential backoff.

    ``opener`` and ``sleep`` are injectable so unit tests never touch the
    network or the clock.
    """

    def __init__(
        self,
        token: str,
        opener: Optional[Callable[..., Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
        max_tries: int = 5,
        base_backoff_s: float = 1.0,
    ):
        self._token = token
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep
        self._max_tries = max(1, int(max_tries))
        self._base_backoff_s = base_backoff_s

    # -- plumbing --

    def _method_url(self, method: str) -> str:
        return "{}/bot{}/{}".format(API_BASE, self._token, method)

    def _file_url(self, file_path: str) -> str:
        return "{}/file/bot{}/{}".format(API_BASE, self._token, file_path)

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 30.0) -> Any:
        """POST one Bot API method; return ``result``. Retries transient errors."""
        body = json.dumps(params or {}).encode("utf-8")
        last_error = "unknown error"
        for attempt in range(self._max_tries):
            request = urllib.request.Request(
                self._method_url(method),
                data=body,
                headers={"Content-Type": "application/json"},
            )
            retry_after = None
            try:
                with self._opener(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:  # subclass of URLError — first
                payload, retry_after = self._payload_from_http_error(exc)
                if payload is None:
                    if exc.code in (401, 404):
                        raise TelegramAPIError(
                            "HTTP {}: токен не принят Telegram (проверь OPENHEALTH_TG_TOKEN)".format(exc.code),
                            exc.code,
                        )
                    last_error = "HTTP {}".format(exc.code)
                    self._backoff(attempt, retry_after)
                    continue
            except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
                last_error = _redact(str(exc))
                self._backoff(attempt, None)
                continue
            except ValueError as exc:  # broken JSON from a proxy/captive portal
                last_error = "bad JSON from API: {}".format(_redact(str(exc)))
                self._backoff(attempt, None)
                continue

            if payload.get("ok"):
                return payload.get("result")

            error_code = payload.get("error_code")
            description = _redact(str(payload.get("description") or "no description"))
            if error_code == 429 or (isinstance(error_code, int) and error_code >= 500):
                retry_after = retry_after or (payload.get("parameters") or {}).get("retry_after")
                last_error = "{} {}".format(error_code, description)
                self._backoff(attempt, retry_after)
                continue
            if error_code in (401, 404):
                raise TelegramAPIError(
                    "{}: токен не принят Telegram (проверь OPENHEALTH_TG_TOKEN)".format(error_code), error_code
                )
            raise TelegramAPIError("{}: {}".format(error_code, description), error_code)

        raise TelegramNetworkError("{} failed after {} tries: {}".format(method, self._max_tries, last_error))

    @staticmethod
    def _payload_from_http_error(exc: "urllib.error.HTTPError") -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
        """Bot API sends ok=false JSON bodies with non-200 codes; salvage them."""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (OSError, ValueError, AttributeError):
            return None, None
        retry_after = (payload.get("parameters") or {}).get("retry_after")
        return payload, retry_after

    def _backoff(self, attempt: int, retry_after: Optional[float]) -> None:
        if attempt >= self._max_tries - 1:
            return  # retries exhausted — fail fast, the caller decides what is next
        delay = min(self._base_backoff_s * (2 ** attempt), 60.0)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except (TypeError, ValueError):
                pass
        self._sleep(delay)

    # -- Bot API methods --

    def get_updates(self, offset: Optional[int], poll_timeout_s: int) -> List[Dict[str, Any]]:
        params = {"timeout": int(poll_timeout_s), "allowed_updates": ["message"]}  # type: Dict[str, Any]
        if offset is not None:
            params["offset"] = int(offset)
        result = self.call("getUpdates", params, timeout=poll_timeout_s + HTTP_TIMEOUT_MARGIN_S)
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str) -> Any:
        text = (text or "").strip() or "…"
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[: MAX_MESSAGE_CHARS - 1] + "…"
        return self.call("sendMessage", {"chat_id": chat_id, "text": text})

    def download_file(self, file_id: str, dest: Path, max_bytes: int = MAX_DOWNLOAD_BYTES) -> Path:
        """getFile → stream the payload to ``dest`` (atomic, size-capped, timed out)."""
        info = self.call("getFile", {"file_id": file_id})
        file_path = (info or {}).get("file_path")
        if not file_path:
            raise TelegramAPIError("getFile вернул пустой file_path")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(self._file_url(file_path))
        fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", dir=str(dest.parent))
        try:
            written = 0
            with os.fdopen(fd, "wb") as out, self._opener(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise TelegramAPIError("файл больше лимита {} байт".format(max_bytes))
                    out.write(chunk)
            os.replace(tmp_name, str(dest))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return dest


# --- /checkin finite-state machine ------------------------------------------------

CHECKIN_QUESTIONS = (
    ("sleep", "1/4 Сколько часов ты спал(а) этой ночью?"),
    ("workout", "2/4 Была сегодня тренировка? (да/нет, какая)"),
    ("alcohol", "3/4 Вчера был алкоголь? (да/нет, сколько)"),
    ("wellbeing", "4/4 Самочувствие сейчас, от 1 до 5?"),
)

CHECKIN_DONE_TEXT = "Чек-ин записан в журнал. Спасибо!"


class CheckinFlow:
    """One-question-at-a-time daily check-in.

    State lives in process memory and is mirrored to a JSON file after every
    step, so a bot restart resumes mid-dialog instead of forgetting it.
    """

    def __init__(self, state_path: Path):
        self._path = Path(state_path)
        self._state = self._load()  # type: Dict[str, Dict[str, Any]]

    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        intake.atomic_write_text(self._path, json.dumps(self._state, ensure_ascii=False, indent=2) + "\n")

    def active(self, chat_id: int) -> bool:
        return str(chat_id) in self._state

    def start(self, chat_id: int) -> str:
        """(Re)start the flow; returns the first question."""
        self._state[str(chat_id)] = {
            "step": 0,
            "answers": {},
            "started_at": intake.iso_utc(None),
        }
        self._save()
        return CHECKIN_QUESTIONS[0][1]

    def cancel(self, chat_id: int) -> bool:
        removed = self._state.pop(str(chat_id), None) is not None
        if removed:
            self._save()
        return removed

    def answer(self, chat_id: int, text: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Feed one reply. Returns (next_question, None) mid-flow, or
        (None, {"answers":…, "started_at":…}) when the check-in is complete."""
        key = str(chat_id)
        session = self._state.get(key)
        if session is None:
            return None, None
        step = int(session.get("step", 0))
        if 0 <= step < len(CHECKIN_QUESTIONS):
            session["answers"][CHECKIN_QUESTIONS[step][0]] = (text or "").strip()
        session["step"] = step + 1
        if session["step"] >= len(CHECKIN_QUESTIONS):
            self._state.pop(key, None)
            self._save()
            return None, {"answers": session["answers"], "started_at": session.get("started_at")}
        self._save()
        return CHECKIN_QUESTIONS[session["step"]][1], None


# --- /today summary out of data.local.json -----------------------------------------


def load_today_data(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _fmt_num(value: Any) -> str:
    return str(round(value, 1)) if isinstance(value, float) else str(value)


def _recovery_zone(recovery: float) -> str:
    if recovery >= RECOVERY_GREEN:
        return "зелёная зона"
    if recovery >= RECOVERY_YELLOW:
        return "жёлтая зона"
    return "красная зона"


def today_summary(data: Optional[Dict[str, Any]]) -> str:
    """4-5 honest lines out of the dashboard's data.local.json."""
    if not data:
        return (
            "Локальной сводки нет: data.local.json не найден.\n"
            "Собери его скриптом ui/web/build_dashboard_data.py или укажи путь через --today-file."
        )
    lines = ["Сводка{}:".format(" за {}".format(data["date"]) if data.get("date") else "")]
    recovery = data.get("recovery")
    if recovery is not None:
        lines.append("Recovery {}% — {}.".format(_fmt_num(recovery), _recovery_zone(float(recovery))))
    pulse_bits = []
    if data.get("hrv") is not None:
        pulse_bits.append("HRV {} ms".format(_fmt_num(data["hrv"])))
    if data.get("rhr") is not None:
        pulse_bits.append("RHR {} bpm".format(_fmt_num(data["rhr"])))
    if pulse_bits:
        lines.append(", ".join(pulse_bits) + ".")
    if data.get("sleep") is not None:
        need = data.get("sleepNeeded")
        lines.append(
            "Сон {} ч{}.".format(_fmt_num(data["sleep"]), " (цель {} ч)".format(_fmt_num(need)) if need else "")
        )
    if data.get("strain") is not None:
        lines.append("Strain {}.".format(_fmt_num(data["strain"])))
    if len(lines) == 1:
        return "data.local.json найден, но в нём нет recovery/HRV/сна — сводку построить не из чего."
    return "\n".join(lines[:5])


def summarize_for_prompt(data: Optional[Dict[str, Any]]) -> str:
    """Compact digest of data.local.json for the /ask agent prompt."""
    if not data:
        return ""
    parts = []
    for key, label, unit in (
        ("date", "Дата", ""),
        ("recovery", "Recovery", "%"),
        ("hrv", "HRV", " ms"),
        ("rhr", "RHR", " bpm"),
        ("sleep", "Сон", " ч"),
        ("strain", "Strain", ""),
    ):
        if data.get(key) is not None:
            parts.append("{}: {}{}".format(label, _fmt_num(data[key]), unit))
    if isinstance(data.get("readiness"), str):
        parts.append("Готовность: {}".format(data["readiness"]))
    return "\n".join("- " + p for p in parts)[:1500]


# --- /ask: optional local agent bridge ----------------------------------------------


def build_ask_prompt(question: str, data_summary: str) -> str:
    blocks = [PROMPT_INTRO, PROMPT_SAFETY, "Вопрос: {}".format(question)]
    if data_summary:
        blocks.append("ДАННЫЕ (выжимка из data.local.json):\n" + data_summary)
    else:
        blocks.append(
            "ДАННЫЕ: data.local.json не найден — реальных данных нет. Скажи это честно, не выдумывай значения."
        )
    blocks.append("Отвечай по-русски, кратко (это сообщение в Telegram), обычным текстом.")
    return "\n\n".join(blocks)


def ask_agent(
    question: str,
    data_summary: str,
    timeout_s: int = AGENT_TIMEOUT_S,
    which: Callable[[str], Optional[str]] = shutil.which,
    runner: Callable[..., Any] = subprocess.run,
) -> str:
    """Run the question through a local agent CLI (codex first, claude fallback).

    Same subprocess pattern as ui/web/server.py: the prompt travels as a single
    argv element, no shell, read-only sandbox for codex. Honest message when no
    CLI is installed or every CLI failed.
    """
    agents = [name for name in ("codex", "claude") if which(name)]
    if not agents:
        return "Агент не подключён: на этой машине нет codex CLI (и claude тоже). /ask требует локального агента."

    prompt = build_ask_prompt(question, data_summary)
    last_error = ""
    for agent in agents:
        last_message_path = None
        if agent == "codex":
            fd, last_message_path = tempfile.mkstemp(prefix="oh-tg-ask-", suffix=".txt")
            os.close(fd)
            # --skip-git-repo-check: codex exec refuses to run outside a git repo;
            # --sandbox read-only: analysis only; --output-last-message: clean answer.
            cmd = [
                "codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                "--output-last-message", last_message_path, prompt,
            ]
        else:
            cmd = ["claude", "-p", prompt, "--output-format", "text"]
        try:
            proc = runner(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                stdin=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                tail = ((proc.stderr or "").strip() or (proc.stdout or "").strip())[-300:]
                last_error = "{}: exit {} ({})".format(agent, proc.returncode, _redact(tail) or "no output")
                continue
            answer = (proc.stdout or "").strip()
            if last_message_path:
                try:
                    clean = Path(last_message_path).read_text(encoding="utf-8").strip()
                    if clean:
                        answer = clean
                except OSError:
                    pass
            return answer or "{} вернул пустой ответ.".format(agent)
        except subprocess.TimeoutExpired:
            last_error = "{}: не уложился в {} секунд".format(agent, timeout_s)
        except OSError as exc:
            last_error = "{}: {}".format(agent, _redact(str(exc)))
        finally:
            if last_message_path:
                try:
                    os.unlink(last_message_path)
                except OSError:
                    pass
    return "Агент не ответил. {}".format(last_error).strip()


# --- the bot ------------------------------------------------------------------------


class BotConfig:
    def __init__(
        self,
        data_dir: Path,
        inbox_dir: Optional[Path] = None,
        allowlist: Optional[Set[int]] = None,
        today_file: Path = DEFAULT_TODAY_FILE,
        enable_ask: bool = False,
        agent_timeout_s: int = AGENT_TIMEOUT_S,
        bridge_url: Optional[str] = None,
    ):
        self.data_dir = Path(data_dir)
        self.inbox_dir = Path(inbox_dir) if inbox_dir is not None else self.data_dir / "inbox"
        self.allowlist = allowlist or set()
        self.today_file = Path(today_file)
        self.enable_ask = enable_ask
        self.agent_timeout_s = agent_timeout_s
        # When set (e.g. http://127.0.0.1:8770), a plain intake is ALSO POSTed to
        # the bridge's /api/intake so it lands in the health index in real time
        # ("внёс в телеге — сразу видно в вебе"). Without it, envelopes stay on
        # disk and reach the index via the batch import parser.
        self.bridge_url = bridge_url.rstrip("/") if bridge_url else None
        self.state_dir = self.data_dir / "state"
        self.offset_path = self.state_dir / "offset.json"
        self.checkin_state_path = self.state_dir / "checkin.json"


class Bot:
    """Routes allowed updates into intake files, check-in flow and commands."""

    def __init__(self, api: TelegramAPI, config: BotConfig):
        self.api = api
        self.config = config
        self.checkin = CheckinFlow(config.checkin_state_path)

    # -- update routing --

    def handle_update(self, update: Dict[str, Any]) -> None:
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            return  # edited_message, my_chat_member, … — not intake
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is None:
            return
        if not intake.is_allowed(chat_id, self.config.allowlist):
            logger.warning("denied chat_id=%s (not in allowlist); nothing stored", chat_id)
            self.api.send_message(chat_id, DENIED_TEXT)
            return

        text = message.get("text") or ""
        if text.startswith("/"):
            self._handle_command(chat_id, message, text)
            return
        if self.checkin.active(chat_id) and intake.classify_message(message) == intake.KIND_TEXT:
            self._handle_checkin_answer(chat_id, message, text)
            return
        self._handle_intake(update, message, chat_id)

    # -- commands --

    def _handle_command(self, chat_id: int, message: Dict[str, Any], text: str) -> None:
        parts = text.split(None, 1)
        command = parts[0].split("@", 1)[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        logger.info("command %s chat_id=%s", command, chat_id)

        if command in ("/start", "/help"):
            self.api.send_message(chat_id, HELP_TEXT)
        elif command == "/checkin":
            self.api.send_message(chat_id, "Чек-ин дня. " + self.checkin.start(chat_id))
        elif command == "/cancel":
            cancelled = self.checkin.cancel(chat_id)
            self.api.send_message(chat_id, "Чек-ин отменён." if cancelled else "Сейчас нечего отменять.")
        elif command == "/today":
            self.api.send_message(chat_id, today_summary(load_today_data(self.config.today_file)))
        elif command == "/ask":
            self._handle_ask(chat_id, argument)
        else:
            self.api.send_message(chat_id, "Не знаю такой команды. /help — список того, что умею.")

    def _handle_ask(self, chat_id: int, question: str) -> None:
        if not self.config.enable_ask:
            self.api.send_message(
                chat_id, "Агентный мост выключен. Запусти бота с флагом --enable-ask, чтобы включить /ask."
            )
            return
        if not question:
            self.api.send_message(chat_id, "Использование: /ask <вопрос>. Например: /ask что сегодня с HRV?")
            return
        self.api.send_message(chat_id, "Думаю над вопросом локальным агентом…")
        digest = summarize_for_prompt(load_today_data(self.config.today_file))
        answer = ask_agent(question, digest, timeout_s=self.config.agent_timeout_s)
        self.api.send_message(chat_id, answer)

    # -- check-in --

    def _handle_checkin_answer(self, chat_id: int, message: Dict[str, Any], text: str) -> None:
        next_question, finished = self.checkin.answer(chat_id, text)
        if next_question:
            self.api.send_message(chat_id, next_question)
            return
        if finished is None:
            return
        envelope = intake.checkin_envelope(
            chat_id=chat_id,
            author=intake.message_author(message),
            answers=finished["answers"],
            started_at=finished.get("started_at"),
            ts=message.get("date"),
        )
        envelope_file = intake.write_envelope(envelope, self.config.data_dir)
        intake.write_card(envelope, self.config.inbox_dir, envelope_file=envelope_file)
        logger.info("checkin stored submission_id=%s chat_id=%s", envelope["submission_id"], chat_id)
        self.api.send_message(chat_id, CHECKIN_DONE_TEXT)

    # -- plain intake (text / voice / photo) --

    def _handle_intake(self, update: Dict[str, Any], message: Dict[str, Any], chat_id: int) -> None:
        envelope = intake.update_to_envelope(update)
        if envelope is None:
            self.api.send_message(
                chat_id, "Пока понимаю только текст, голосовые и фото. Это сообщение я не сохранил."
            )
            return
        download_failed = False
        for attachment in envelope.get("attachments", []):
            download_failed |= not self._download_attachment(envelope, attachment)
        envelope_file = intake.write_envelope(envelope, self.config.data_dir)
        intake.write_card(envelope, self.config.inbox_dir, envelope_file=envelope_file)
        indexed = self._push_to_bridge(envelope)
        logger.info(
            "intake stored type=%s submission_id=%s chat_id=%s indexed=%s",
            envelope["type"], envelope["submission_id"], chat_id, indexed,
        )
        self.api.send_message(chat_id, self._confirmation(envelope, download_failed))

    def _push_to_bridge(self, envelope: Dict[str, Any]) -> bool:
        """Best-effort real-time indexing: POST the envelope to the bridge's
        /api/intake so it reaches the health index immediately. Returns True on a
        200. Never raises — the disk copy is the durable path and the batch import
        parser still ingests it if the bridge is offline.
        """
        if not self.config.bridge_url:
            return False
        try:
            body = json.dumps(envelope).encode("utf-8")
            req = urllib.request.Request(
                self.config.bridge_url + "/api/intake",
                data=body, headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — local bridge
                return 200 <= getattr(resp, "status", resp.getcode()) < 300
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError, ValueError) as exc:
            logger.warning("bridge intake push failed (envelope kept on disk): %s", exc.__class__.__name__)
            return False

    def _download_attachment(self, envelope: Dict[str, Any], attachment: Dict[str, Any]) -> bool:
        """Fetch one attachment into <data-dir>/files/<kind>/; True on success."""
        file_id = attachment.get("file_id")
        if not file_id:
            return False
        kind = attachment.get("kind", "file")
        ext = ".oga" if kind == "voice" else ".jpg"
        relative = Path("files") / kind / "{}{}".format(envelope["submission_id"], ext)
        try:
            self.api.download_file(file_id, self.config.data_dir / relative)
        except (TelegramAPIError, TelegramNetworkError, OSError) as exc:
            logger.error("download failed submission_id=%s: %s", envelope["submission_id"], _redact(str(exc)))
            attachment["download_error"] = _redact(str(exc))
            return False
        attachment["path"] = str(relative)
        return True

    @staticmethod
    def _confirmation(envelope: Dict[str, Any], download_failed: bool) -> str:
        kind = envelope.get("type")
        if kind == intake.KIND_VOICE:
            base = "Голос сохранён (.oga). Транскрипция появится позже — пока это TODO-hook."
        elif kind == intake.KIND_PHOTO:
            base = "Фото сохранено{}.".format(" вместе с подписью" if envelope.get("text") else "")
        else:
            base = "Записал в журнал."
        if download_failed:
            base += " Файл скачать не удалось — пришли его ещё раз, когда сеть появится."
        return base


# --- long-polling loop -----------------------------------------------------------


class _Stop(Exception):
    """Raised from the SIGTERM handler to unwind the loop gracefully."""


def _sigterm_handler(signum, frame):  # pragma: no cover - signal plumbing
    raise _Stop()


def run_loop(
    bot: Bot,
    api: TelegramAPI,
    offset_path: Path,
    poll_timeout_s: int = DEFAULT_POLL_TIMEOUT_S,
    once: bool = False,
    idle_sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Pull updates forever (or one batch with ``once``); offset survives restarts.

    The offset file is written after *each handled update*: a crash redelivers
    at most the update being processed, and redelivery overwrites the same
    submission_id file — no loss, no duplicates.
    """
    offset = load_offset(offset_path)
    logger.info("long polling started (offset=%s)", offset)
    try:
        while True:
            try:
                updates = api.get_updates(offset, poll_timeout_s)
            except TelegramNetworkError as exc:
                logger.error("getUpdates: %s; retrying in 5s", exc)
                idle_sleep(5)
                continue
            for update in updates:
                try:
                    bot.handle_update(update)
                except (TelegramAPIError, TelegramNetworkError) as exc:
                    logger.error("update %s failed: %s", update.get("update_id"), _redact(str(exc)))
                except Exception:  # never let one bad update kill the channel
                    logger.exception("unexpected error on update %s", update.get("update_id"))
                update_id = update.get("update_id")
                if update_id is not None:
                    offset = int(update_id) + 1
                    store_offset(offset_path, offset)
            if once:
                return 0
    except (KeyboardInterrupt, _Stop):
        logger.info("stopping: offset saved, until next time")
        return 0


# --- CLI ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m openhealth.telegram_bot",
        description="OpenHealth Telegram intake bot (stdlib only, local-first).",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="start the long-polling bot")
    run_p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR,
                       help="local intake folder (default: %(default)s)")
    run_p.add_argument("--inbox-dir", type=Path, default=None,
                       help="markdown cards folder (default: <data-dir>/inbox; point at data/raw/inbox "
                            "to feed the ingest pipeline)")
    run_p.add_argument("--today-file", type=Path, default=DEFAULT_TODAY_FILE,
                       help="data.local.json for /today and /ask context (default: %(default)s)")
    run_p.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_PATH,
                       help="token file, used when OPENHEALTH_TG_TOKEN is not set (default: %(default)s)")
    run_p.add_argument("--allowlist-file", type=Path, default=intake.DEFAULT_ALLOWLIST_PATH,
                       help="chat id allowlist file (default: %(default)s)")
    run_p.add_argument("--poll-timeout", type=int, default=DEFAULT_POLL_TIMEOUT_S,
                       help="getUpdates long-poll hold in seconds (default: %(default)s)")
    run_p.add_argument("--agent-timeout", type=int, default=AGENT_TIMEOUT_S,
                       help="/ask CLI timeout in seconds (default: %(default)s)")
    run_p.add_argument("--enable-ask", action="store_true",
                       help="enable the /ask local agent bridge (codex/claude CLI)")
    run_p.add_argument("--once", action="store_true",
                       help="process one getUpdates batch and exit (smoke runs)")
    run_p.add_argument("--bridge-url", default=os.environ.get("OPENHEALTH_BRIDGE_URL"),
                       help="POST plain intake to this bridge's /api/intake for real-time "
                            "indexing (e.g. http://127.0.0.1:8770); env OPENHEALTH_BRIDGE_URL")

    check_p = sub.add_parser("check", help="offline self-check: token, allowlist, folders, agent CLIs")
    check_p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    check_p.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_PATH)
    check_p.add_argument("--allowlist-file", type=Path, default=intake.DEFAULT_ALLOWLIST_PATH)
    return parser


def _cmd_check(args: argparse.Namespace) -> int:
    """No-network doctor. Prints a short report, exit 0 when runnable."""
    token = load_token(path=args.token_file)
    allowlist = intake.load_allowlist(path=args.allowlist_file)
    agents = [name for name in ("codex", "claude") if shutil.which(name)]
    ok = True
    print("token: {}".format("found" if token else "MISSING (env {} or {})".format(ENV_TOKEN, args.token_file)))
    print("allowlist: {}".format(
        "{} chat id(s)".format(len(allowlist)) if allowlist
        else "EMPTY (env {} or {})".format(intake.ENV_ALLOWLIST, args.allowlist_file)
    ))
    print("data dir: {}".format(args.data_dir))
    print("agent CLIs for /ask: {}".format(", ".join(agents) if agents else "none (codex/claude not on PATH)"))
    if not token or not allowlist:
        ok = False
        print("verdict: NOT READY — см. docs/TELEGRAM.md")
    else:
        print("verdict: ready — python3 -m openhealth.telegram_bot run")
    return 0 if ok else 1


def _cmd_run(args: argparse.Namespace) -> int:
    token = load_token(path=args.token_file)
    if not token:
        logger.error(
            "токен не найден: положи его в env %s или в файл %s (см. docs/TELEGRAM.md)",
            ENV_TOKEN, args.token_file,
        )
        return 2
    allowlist = intake.load_allowlist(path=args.allowlist_file)
    if not allowlist:
        logger.error(
            "allowlist пуст — это обязательный privacy-щит. Добавь свой chat_id в env %s "
            "или в файл %s (см. docs/TELEGRAM.md)",
            intake.ENV_ALLOWLIST, args.allowlist_file,
        )
        return 2

    config = BotConfig(
        data_dir=args.data_dir,
        inbox_dir=args.inbox_dir,
        allowlist=allowlist,
        today_file=args.today_file,
        enable_ask=args.enable_ask,
        agent_timeout_s=args.agent_timeout,
        bridge_url=getattr(args, "bridge_url", None),
    )
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.inbox_dir.mkdir(parents=True, exist_ok=True)

    api = TelegramAPI(token)
    bot = Bot(api, config)
    signal.signal(signal.SIGTERM, _sigterm_handler)
    logger.info(
        "bot up: data_dir=%s inbox=%s allowlist=%d chat(s) ask=%s",
        config.data_dir, config.inbox_dir, len(allowlist), "on" if config.enable_ask else "off",
    )
    try:
        return run_loop(bot, api, config.offset_path, poll_timeout_s=args.poll_timeout, once=args.once)
    except TelegramAPIError as exc:
        logger.error("fatal: %s", exc)
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "check":
        return _cmd_check(args)
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
