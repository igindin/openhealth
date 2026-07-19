# Plan: agent-native multi-domain OpenHealth

## Summary
Grow OpenHealth into an agent-native health OS: the primary interface is Claude
Code / Codex, not a GUI. Each domain (pulse, cycle, body, metabolic, skin, sleep)
is a plugin module with a contract, a schema and tests on synthetic data. We
implement classes of functionality originally, from open science; we copy no
third-party brand assets or code. No diagnoses: a C1–C5 confidence scale plus
red flags. Local-first, MIT. GUI comes later, via A2UI.

## What we are doing
- Modular plugin system (`openhealth/modules/`): the `HealthModule` contract + a registry. [DONE]
- Domain modules: Pulse (HRV) [DONE], then Sleep/Circadian, Cycle, Body, Metabolic, Skin.
- Agent-native UX: slash commands (/checkin /log /fast /sleep /pulse /insights /trends /protocol) + a health-agent orchestrator on top of the Python CLI.
- Onboarding without git: `make setup`, pre-commit, CI, git/PR hidden behind agent scripts, 20+ agent task cards, beginner-facing AGENTS/CLAUDE/CONTRIBUTING.
- core/privacy (anonymization + tests), headless API + TS SDK + OpenAPI, A2UI adapter (Insight→intent, golden tests, no rendering).

## Verification
- before: OpenHealth = ingest + parsers + evidence + lab, with no modules, agent UX or API.
- after: tests/types/lint green; `make setup` works from scratch; every module passes its contract test; 6+ slash commands work end-to-end on synthetic data; 20+ agent task cards; a newcomer can go from "opened Claude Code → logged something / got an insight" and "picked a task → PR" without knowing git.

## Assumptions
- We work in the public `igindin/openhealth`, branch `feat/agent-native-os`, no push without permission.
- GUI is out of scope (the agent is the interface). Core stays stdlib-only (no numpy).
- A2UI: confirm which package (google/A2UI vs codaaiteam/ai2ui) before building the adapter.
- Everything runs on synthetic data, zero real PII.
