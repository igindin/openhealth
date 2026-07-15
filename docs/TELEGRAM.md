# Telegram intake bot

A two-way Telegram channel for OpenHealth: you write text / voice / photos to
your own private bot, and they land **on your machine** as structured intake
envelopes. The bot answers back — daily check-in questions, a short daily
summary, and (optionally) a local agent for questions about your data.

Pure stdlib (repo core rule): `urllib.request` long polling, `json`,
`pathlib`. No webhook, no public port, no third-party SDK.

## Privacy, first

- **Everything stays local.** Messages are pulled from the Bot API and written
  to a folder on your disk. Nothing is uploaded anywhere else. The only remote
  party is Telegram itself, which already carries your messages.
- **The token never lives in the repo.** Environment variable or a dot-file in
  your home directory.
- **An allowlist of chat ids is mandatory.** The bot refuses to start without
  one. Anyone not on the list gets «Доступ не настроен» and *nothing* they
  send is ever stored.
- **Logs carry no message bodies** — only chat ids, message kinds and
  submission ids, on stderr.
- Keep your bot private: don't publish its @username, and remember that
  anybody who knows the token *is* the bot — guard the token file like a
  password (`chmod 600`).

## Setup

### 1. Create a bot with BotFather

1. In Telegram, open [@BotFather](https://t.me/BotFather).
2. `/newbot` → pick a display name and a unique username (must end in `bot`).
3. BotFather replies with an HTTP API token like `1234567890:AAE...xyz`.

### 2. Store the token (never commit it)

Either an environment variable:

```bash
export OPENHEALTH_TG_TOKEN="1234567890:AAE...xyz"
```

…or a file (survives shell restarts; preferred for a long-running bot):

```bash
mkdir -p ~/.openhealth
printf '%s\n' '1234567890:AAE...xyz' > ~/.openhealth/telegram.token
chmod 600 ~/.openhealth/telegram.token
```

### 3. Find your chat id and allow it

Easiest: message [@userinfobot](https://t.me/userinfobot) — it replies with
your numeric id. Or send your new bot any message and ask the Bot API
directly:

```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -m json.tool
# look for "chat": {"id": 123456789, ...}
```

Then allow that id — file (one id per line, `#` comments allowed):

```bash
printf '%s\n' '123456789' > ~/.openhealth/telegram.allowlist
```

…or environment variable (comma-separated for several):

```bash
export OPENHEALTH_TG_CHAT_ID="123456789"
```

Both sources are merged. An empty allowlist is a startup error by design.

### 4. Check and run

```bash
python3 -m openhealth.telegram_bot check   # offline self-check, no network
python3 -m openhealth.telegram_bot run     # long-polling loop, Ctrl+C to stop
```

Useful flags for `run`:

| Flag | Default | Meaning |
|------|---------|---------|
| `--data-dir` | `./data/intake/telegram` | where envelopes/files/cards land |
| `--inbox-dir` | `<data-dir>/inbox` | markdown cards; point at `data/raw/inbox` to feed the ingest pipeline |
| `--today-file` | `ui/web/data.local.json` | local summary used by `/today` and `/ask` |
| `--enable-ask` | off | turn on the `/ask` local agent bridge |
| `--once` | off | process one `getUpdates` batch and exit (smoke runs) |
| `--poll-timeout` | 50 | long-poll hold in seconds |
| `--bridge-url` | `$OPENHEALTH_BRIDGE_URL` | POST plain intake to this bridge's `/api/intake` for **real-time** indexing (e.g. `http://127.0.0.1:8770`); without it, envelopes stay on disk and reach the index via the batch import parser |

## Commands

- `/checkin` — daily check-in: 4 short questions one at a time (hours of
  sleep, workout, alcohol, wellbeing 1-5). Answers are stored as one
  journal-style envelope. The dialog state is mirrored to disk, so a bot
  restart resumes mid-conversation.
- `/today` — a 4-5 line summary (recovery zone, HRV, RHR, sleep, strain) read
  from the local `data.local.json` built by `ui/web/build_dashboard_data.py`.
  Honest "no data" when the file is absent.
- `/ask <question>` — runs your question through a local agent CLI (`codex`
  first, `claude` as fallback) together with a compact digest of
  `data.local.json` — the same pattern as the dashboard bridge
  (`ui/web/server.py`). Requires `--enable-ask`; without a CLI on PATH the
  reply honestly says the agent is not connected. Codex runs with
  `--sandbox read-only`.
- `/cancel` — abort the current check-in.
- `/help`, `/start` — what the bot can do.

Anything that is not a command is intake: text, voice messages, photos
(captions included). Other kinds (stickers, polls, …) are politely declined
and not stored.

## What lands where

```
<data-dir>/                              # default: ./data/intake/telegram
  envelopes/2026-06-10/tg-<chat>-<msg>.json   # IntakeEnvelope, one per message
  files/voice/tg-<chat>-<msg>.oga             # downloaded voice (as sent, .oga)
  files/photo/tg-<chat>-<msg>.jpg             # largest photo variant
  inbox/tg-<chat>-<msg>.md                    # human-readable intake card
  state/offset.json                           # getUpdates cursor (restart-safe)
  state/checkin.json                          # check-in dialog state
```

The envelope follows `schemas/intake-envelope.schema.json` plus flat
agent-facing fields:

```json
{
  "submission_id": "tg-123456789-42",
  "submitted_at": "2026-06-10T08:15:00+00:00",
  "channel": "telegram",
  "author": "ilya",
  "type": "voice",
  "text": null,
  "ts": 1781424900,
  "chat_id": 123456789,
  "source": "telegram",
  "transcript": null,
  "attachments": [
    {
      "kind": "voice",
      "file_id": "…",
      "duration_s": 12,
      "mime_type": "audio/ogg",
      "path": "files/voice/tg-123456789-42.oga",
      "transcript": null
    }
  ],
  "tags": ["telegram", "voice"],
  "metadata": {"update_id": 100, "message_id": 42, "from_id": 123456789, "received_at": "…"}
}
```

`transcript` is deliberately `null`: voice transcription is a **TODO hook**
for a future local transcriber. The raw `.oga` is preserved either way — raw
stays immutable.

## Reliability notes

- **Offset is persisted after every handled update** (`state/offset.json`), so
  a restart neither loses messages nor processes them twice — a redelivered
  update overwrites the same `submission_id` file instead of duplicating it.
- Network errors retry with exponential backoff (cap 60 s); Telegram's 429
  `retry_after` is honored; a bad token (401) fails fast with a clear message.
- Every HTTP call has a timeout, including file downloads (60 s, 20 MB cap —
  the Bot API's own `getFile` limit).
- SIGINT/SIGTERM stop the loop gracefully; the offset is already on disk.
- One malformed update never kills the channel: it is logged and skipped, the
  offset still advances.

## Limitations

- Voice is stored, not transcribed (yet) — `transcript: null` is the hook.
- Designed for private one-person chats; groups are not a goal.
- Documents/stickers/locations are not intake kinds yet.
- `/today` only knows what `data.local.json` knows; rebuild it with
  `ui/web/build_dashboard_data.py`.

## Tests

```bash
python3 -m pytest tests/test_telegram_intake.py
```

No network anywhere in tests: the HTTP layer is a scripted fake, the agent
CLI is a fake runner.
