# Claude Code Adapter — OpenHealth

@AGENTS.md

`AGENTS.md` is the shared, canonical operating contract for every agent. Keep
this file thin — add only Claude-specific behavior here. Promote durable
learnings into the shared files (`AGENTS.md`, methodology docs), not here.

## The Agent Is the Interface

OpenHealth has no GUI. When a person wants to log data, get a reading, or ask
about their body, use the **`health-agent`** skill (`.claude/skills/health-agent/`).
The slash commands `/checkin` `/log` `/pulse` `/sleep` `/cycle` `/body`
`/insights` `/trends` `/protocol` are thin wrappers over that skill — the real
logic lives there. Route the request to the right domain module and answer
cautiously, honoring the C1–C5 framing and red-flag rules from `AGENTS.md`.

## Code Standards (Claude-specific)

- **Python 3.10+, type hints everywhere.** Avoid `Any`. Use `dataclasses` for
  models, `Protocol` for public contracts, JSON Schema for boundary validation.
- **Core is zero-dependency.** `openhealth/` (core + modules) uses the standard
  library only — no external runtime deps. Connector-specific deps stay in the
  connector and the optional-dependency groups in `pyproject.toml`.
- **Tests with `pytest`.** Cover the happy path and error cases. Mock external
  APIs. Test data is synthetic — never real health data.
- **Lint with `ruff`.** Run `make check` (lint + tests) before you ship.
- **Never** commit secrets or real health data; never disable a security check to
  make something pass. If you spot a leaked secret in review, flag it immediately.
- **No `core/` features without an RFC.** Don't add cloud deps, telemetry, or
  analytics. Don't refactor someone else's connector without their approval.

## Extending the Dashboards

Before writing a new theme, module or skin, read `CAPABILITIES.md` and
`EXTENDING.md` — much already exists. First show the person what the system
already does (you can open both skins in a browser via a local server:
`dashboard.html` and `dashboard-v2.html`), propose an extension level (A/B/C
from `EXTENDING.md`), and write from scratch only if nothing fits. Any new
metric or section goes into `ui/web/assets/registry.json` and must appear in
**both** skins (parity); after changing the registry, regenerate the capability
map with `python3 ui/web/gen_capabilities.py`. The shared rule lives in
`AGENTS.md`.
