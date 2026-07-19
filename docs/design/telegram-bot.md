# Telegram Bot & Mini App Spec

**Status:** draft for community review
**Date:** 2026-05-29

How OpenHealth meets beginners where they already are: a Telegram bot for
low-friction intake plus a Mini App for review. The bot is **transport, not
storage** — it feeds the local engine and never becomes the system of record.

The current implementation is in [`openhealth/bot.py`](../../openhealth/bot.py)
(python-telegram-bot, polling). This spec records what ships now and what the
2025–2026 Bot API makes possible next.

## Shipping now (in bot.py)

- **Photos** → body-zone + visible-attribute tagging → MediaObservation.
- **Documents (PDF/CSV/JSON)** → routed to the lab-panel parser when the caption
  or filename hints at labs (`lab`, `blood`, `panel`, plus Cyrillic stems for
  "lab test" and "blood" — see `LAB_HINTS` in `bot.py`), else the generic
  document parser. Lab values come back flagged in/out of range.
- **Voice/audio** → stored and enveloped (transcription is a TODO, see below).
- **Text** → note, with an **immediate red-flag safety response**: a message
  mentioning chest pain, shortness of breath, etc. gets an instant "see a
  clinician" reply and is not interpreted.
- **/checkin, /status, /start** with a non-diagnostic disclaimer.

## Privacy (non-negotiable)

- Telegram bots have **no end-to-end encryption**; Telegram servers see bot
  traffic in plaintext. So: minimize what the bot retains in Telegram, store raw
  health data only in the local engine, and get explicit consent at onboarding.
- **Transcribe voice locally** (whisper.cpp / faster-whisper), never ship audio
  to a third-party API. Aligns with local-first principle.
- Self-host the bot. For files over Bot API's **20 MB `getFile` limit** (large
  lab PDFs, long audio), run a **Local Bot API Server** (raises the limit to 2 GB)
  and keep file traffic under your control.

## Roadmap (2025–2026 Bot API features worth adopting)

Mapped from the API changelog; adopt where they reduce friction for beginners.

- **Native checklists (Bot API 9.1, Jul 2025)** — for routines/interventions:
  "morning supplements" checklist; the bot receives completion updates straight
  into the intervention ledger.
- **Private-chat topics (9.4, Feb 2026)** — separate "Labs / How I feel / Food"
  into topics inside one chat. Clean organization without multiple bots.
- **`sendMessageDraft` streaming (9.3, Dec 2025)** — stream partial replies so a
  beginner sees the bot "thinking" during a longer (local-LLM) read-out.
- **Mini App storage + biometrics (9.0, Apr 2025)** — `SecureStorage` for
  drafts; `BiometricManager` to lock entry into sensitive health views.

## Mini App (the review surface)

Chat is best for quick capture on the go; a Mini App is best for structured input
and visualization. Build the dashboard from [ui-spec.md](./ui-spec.md) as a Mini
App first (reuses the screens, themes natively via `themeParams`), then a PWA.

- **MainButton** "Save entry"; **BottomButton/BackButton** for flows.
- **initData validation is mandatory**: rebuild the data-check-string, key =
  `HMAC-SHA256(bot_token, "WebAppData")`, compare `HMAC-SHA256(data, key)` to the
  `hash`. Never trust `user.id` without it.
- Camera/file access from the Mini App is limited — prefer plain file upload in
  chat for photos/PDFs, use the Mini App for forms, sliders, and charts.

## Stack note

The engine is Python, and the current bot is python-telegram-bot, which keeps the
intake path in one language next to the parsers. If a richer Mini App backend is
built later, grammY (TypeScript) is the house standard — use
`sessions` + `conversations` + `runner` (`sequentialize` per chat), keep long
work (file download, transcription, LLM) out of webhook handlers via a queue.

## Onboarding flow (beginner-first)

`/start` → welcome → **consent to local storage of health data** → one example of
each input (text, photo, lab PDF, voice) → first real entry. Mirror the
"Today's Missions" pattern: 2–3 tiny first actions with checkmarks.
