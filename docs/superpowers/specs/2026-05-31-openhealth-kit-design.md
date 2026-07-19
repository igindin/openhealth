# OpenHealth Kit — design spec

**Date:** 2026-05-31 · **Status:** draft for review · **Repo:** igindin/openhealth

## Purpose

Turn OpenHealth into a distributable kit that newcomers can
self-serve: drop a few prompts, get the modules they need. Ideologically like a
handed-out "stack" of skills — a library of pointed, single-purpose skills that
each own one area of knowledge, action, or signal about a person. The agent
(Claude Code / Codex) is the brain and the interface; no GUI required.

Non-negotiables (inherited): local-first, MIT, no diagnosis, C1–C5 confidence +
red flags, original implementations from open science (no third-party brand
assets or proprietary code).

## Form (decided)

- **Approach A** — a library-kit on top of OpenHealth (not a hosted platform,
  not a bare prompt-pack). Maximal reuse of the module system, evidence layer
  and agent-native interface already built.
- **Skill unit** — each module ships as a `SKILL.md` + a slash command, thin
  over the existing Python CLI/modules. Native to Claude Code / Codex, works out
  of the box, no plugin/MCP runtime required.
- **Brain** — the agent's frontier model. Specialized medical models are
  optional, deferred adapters used only where a specialist beats the generalist
  (chiefly imaging segmentation). MedGemma / Med-PaLM / Meta brain-decoding are
  parked; if ever needed, confirmed via the Deep Research skill first.

## Architecture

```
openhealth/                  # existing: modules, evidence, schemas, CLI, privacy
kit/
  registry.yaml              # the catalog: every skill as a card
  loader (/kit command)      # prompt -> matched skills -> installed into .claude/
  skills/<id>/SKILL.md       # one per domain/action/signal
.claude/commands/<id>.md     # the slash wrappers the person actually types
```

The agent reads `kit/registry.yaml`, matches a person's request to cards, and
installs the matching `SKILL.md` + slash command into their workspace. Each skill
runs on the canonical data layer (modules, schemas, evidence) so logging and
insights stay structured and graded.

## Components

### 1. Skill Registry (`kit/registry.yaml` + cards)
The hand-out. Each card: `id`, `title`, `area` (knowledge | action | signal),
`summary`, `inputs`, `uses` (module/tool), `confidence_policy`, `starter_prompt`,
`status` (full | stub). Stubs let the catalog cover every area from day one.

### 2. Self-serve loader (`/kit`)
`/kit "track sleep + analyze my labs + watch screen time"` →
agent matches registry → proposes a bundle → installs skills + slash commands +
seeds synthetic fixtures → confirms. This is the "drop a prompt → get modules"
mechanism. Re-runnable; idempotent.

### 3. Domain knowledge skills
labs · pulse/HRV · sleep/circadian · cycle · metabolic · skin · MSK/anatomy ·
imaging (optional) · mental/emotional. Each is a thin skill over a module +
(optionally) the Deep Research skill for external grounding. Some ship full
(reuse existing modules), some as stubs.

### 4. Action / tracking skills (WHOOP-style)
`/track` to define and log custom actions/habits; daily `/checkin`; streaks;
later, correlation of actions with outcomes (reuse correlation + C1–C5). The
user will supply real action lists → these become a starter pack of pre-defined
trackables.

### 5. Signal connectors (opt-in, privacy-gated)
browser history · screen time · IG/FB export · call logs · call-transcript
emotion analysis. Each: import → canonical signals → `privacy.strip_pii` before
any processing → into the index. Local-first; nothing leaves the machine. Emotion
analysis runs on the agent's model over de-identified text, graded C2–C3, framed
as observation, never a diagnosis.

### 6. Trust layer (anti-hallucination spine)
- **Deep Research skill** — fan-out web search, fetch, adversarially verify,
  synthesize with citations (reuse the existing deep-research harness).
- **Data Confirmation skill** — every client-facing claim gets a source +
  C1–C5, or an explicit `assumption` label. No bare assertions.
- **Dev-&-Check cycle** — any new module/skill/insight an attendee generates
  must pass: (1) schema-valid, (2) test green (`make check`), (3) red-flag +
  confidence pass, (4) a skeptic prompt that tries to refute it. Only then
  surfaced. This is the loop the audience explicitly wants.

### 7. Intelligence adapters (optional, deferred)
A clean interface so a specialist can be plugged where it wins: imaging
segmentation (TotalSegmentator / MONAI / nnU-Net — open source), or a local
small model for offline privacy. Not a dependency; the kit works without any.

## Data flow

1. Person runs `/kit "<wants>"` → loader installs skills.
2. Person logs via `/log`, `/track`, `/checkin`, or imports via a connector.
3. Data → canonical records (schemas) → index (local SQLite).
4. Insight skills read records → produce graded insights (C1–C5) → Dev-&-Check
   gate → surfaced via `/insights` / `/trends`.
5. External questions → Deep Research skill → cited synthesis → Data Confirmation.

## Error handling & safety

- Red flags (critical lab value, crisis language, alarming symptom) short-circuit
  all interpretation and route to professional care.
- Missing inputs → the skill asks one plain question at a time (newcomer-safe).
- Connector failures degrade gracefully (skip + note), never block the session.
- Privacy: real PII never enters the repo; connectors strip before processing;
  push to remote stays gated (`OPENHEALTH_ALLOW_PUSH`).

## Testing

- Every skill has a contract test on synthetic fixtures (extends current suite).
- Registry has a validator: every card resolves to a real skill or a declared
  stub; every full skill has a test.
- The Dev-&-Check cycle itself is tested (a known-bad insight must be caught).

## Scope / non-goals (for the first cut)

- IN: registry + loader, the trust layer, tracking/habits, 2–3 connectors
  (start with the lowest-friction exports), all domain skills as full-or-stub.
- OUT (deferred): hosted platform, required specialized med-LLMs, imaging
  pixel analysis, GUI. These are optional adapters/later phases.

## Open questions

1. Which connectors first? (screen-time / browser export are usually lowest
   friction; call transcripts richest but heaviest.)
2. Starter action-list for `/track` — awaiting the user's real lists.
3. Distribution: clone-the-repo vs a one-line installer script for total
   newcomers (both compatible with the skill form).
