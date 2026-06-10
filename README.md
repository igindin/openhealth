# OpenHealth

**A local-first, agent-native personal health operating system. Open-source.**

`v0.1.0` · MIT License

---

## Start in 60 seconds

No terminal, no install:

1. Green **Code** button above → **Download ZIP** → unpack.
2. Open the `ui/web` folder.
3. **macOS:** double-click `OpenHealth.command` (if blocked: right-click → Open).
   **Windows / Linux:** double-click `index.html`.

The dashboard opens with demo data. Your own data is one command away: `bash setup.sh`.

**Beginner guide, step by step:** [docs/START-HERE.md](./docs/START-HERE.md) (RU) · [docs/START-HERE.en.md](./docs/START-HERE.en.md) (EN)

If OpenHealth ends up useful to you, starring the repo is the single best way to help others find it. ⭐

---

## What is this?

OpenHealth turns your scattered health data into one local knowledge base you can
actually reason over — and you reason over it by **talking to an AI agent**, not
by clicking through an app.

Your sleep is in one wearable, your workouts in another, your bloodwork in a PDF,
your schedule in a calendar. Nothing connects them, and nothing lets you ask the
question that matters: *"What actually moves my recovery?"* OpenHealth is the
layer that connects them — locally — and answers that question as a careful,
testable prompt instead of a dashboard you have to interpret alone.

**It is not an app.** It is a Python package plus a set of agent skills. There is
no GUI: a person (often non-technical) talks to Claude Code or Codex, and the
agent logs data, runs local domain modules, and reads the results back gently.

## The core loop

Everything serves one loop:

> **journal → recovery → correlations → action**

1. **Journal** — low-friction daily check-ins (a WHOOP-style catalog of **200+
   behaviors**) plus passive imports become dated observations.
2. **Recovery** — HRV, resting heart rate, sleep and strain become transparent,
   **versioned** scores (change the formula, bump the version, old records stay
   reproducible).
3. **Correlations** — for each behavior you log, your average recovery on *yes*
   days is compared against *no* days over a personal baseline window. This is
   "what affects me", computed locally.
4. **Action** — every finding comes back as a confidence-graded prompt to test
   next (an n-of-1 experiment), never as a verdict.

## What's new in this release

Two months of quiet work turned OpenHealth from a manifest into a working system:

- **Modular domain system** — each health domain is a self-registering plugin
  (`pulse`, `sleep`, `cycle`, `body`, `metabolic`, `skin`, `journal`, `recovery`,
  `correlations`). Adding a domain = adding a module. No core changes.
- **Journal behavior library** — 200+ behaviors transcribed from the WHOOP
  Journal screens, as a static catalog you log against in seconds.
- **Versioned recovery scoring** — recovery (0–100, HRV-led), strain (0–21) and
  sleep debt, each stamped with an `algo_version` so hypotheses stay reproducible.
- **Personal correlations** — behavior → recovery impact with a 5-yes / 5-no
  threshold and confidence caps (a raw association is a weak signal until an
  on/off switch repeats).
- **Connectors** — **Apple Health** export, **WHOOP** (OAuth + API + webhook
  verification), and **Google Calendar** (meeting-load and life-context, writing
  only to a *derived* calendar), with more (Oura, Garmin, …) arriving via the
  connector template.
- **Evidence grading (C1–C5)** and red-flag safety checks baked into every module.
- **Optional `ask` layer** — answer questions over your local records with an LLM
  (Anthropic API), with every claim cited back to the record it came from.
- **Agent-native interface** — the `health-agent` skill plus slash commands
  (`/checkin`, `/log`, `/pulse`, `/insights`, `/trends`, `/protocol`, …) so a
  non-technical person can use the whole thing by chatting.
- **Web dashboard with a live agent bridge** — a premium dark dashboard
  (`ui/web/`, single file, GSAP) that renders your real local data and can
  *run an agent over it*: "generate insight", "re-run correlations" and
  per-marker deep-research buttons call a local bridge (`ui/web/server.py`,
  stdlib, 127.0.0.1 only) which executes Claude Code or Codex CLI on your
  machine and streams the answer back into the UI. Demo data out of the box;
  your data never leaves the device. Launch: `ui/web/OpenHealth.command`
  (background server + opens the dashboard), or see `ui/web/DESKTOP.md` for
  an always-on launchd setup and PWA install.
- **Telegram intake channel** — a stdlib-only bot (`python -m
  openhealth.telegram_bot run`): text/voice/photo become structured
  `IntakeEnvelope` records in your local folders, `/checkin` walks the daily
  questions, `/today` answers from your local summary, `/ask` runs a local
  agent. Allowlist-first, token stays in your env. See `docs/TELEGRAM.md`.

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/igindin/openhealth.git
cd openhealth
pip install -e .          # or: make setup  (installs + inits a workspace)
```

## Run

```bash
# Initialize a local workspace (folders + SQLite index)
python -m openhealth init

# See the health domain modules you can run
python -m openhealth modules

# Run a domain module on a JSON payload (also saves results locally)
python -m openhealth module --id recovery --payload-json '{ ... }'

# Import an Apple Health export into daily observations
python -m openhealth import-apple-health --path ~/apple_health_export/export.xml

# Connect WHOOP (OAuth), then sync
python -m openhealth whoop-auth-url
python -m openhealth whoop-sync --days-back 30

# Look at recent records / insights
python -m openhealth recent --type InsightHypothesis
python -m openhealth recent --metric rmssd_ms --limit 30
```

In practice you rarely type these by hand — you ask the agent ("log that I had
two coffees and slept badly", "how's my recovery this week?") and it runs them
for you. The CLI is the substrate; the agent is the interface.

**Full command map** (slash commands, CLI, `make` targets, quick recipes,
troubleshooting): [docs/COMMANDS.md](./docs/COMMANDS.md).

## Project structure

```
openhealth/
├── AGENTS.md               # Canonical operating contract for AI agents
├── CLAUDE.md               # Thin Claude Code adapter (imports AGENTS.md)
├── ARCHITECTURE.md         # Technical architecture
│
├── openhealth/             # The Python package (stdlib-only core)
│   ├── modules/            # Domain plugins: pulse, sleep, cycle, body,
│   │                       #   metabolic, skin, journal, recovery, correlations
│   ├── connectors/         # Apple Health export, Google Calendar
│   ├── whoop.py            # WHOOP OAuth + API + webhook verification
│   ├── evidence.py         # C1–C5 confidence scale + red-flag checks
│   ├── journal_behaviors.py# Loader for the behavior catalog
│   ├── ask.py              # Optional LLM Q&A over local records (cited)
│   ├── storage.py / index.py / ingest.py   # Local workspace + SQLite index
│   └── data/               # Static resources (journal behavior library)
│
├── connectors/             # Connector template for new data sources
├── hypotheses/             # Community experiment templates + examples
├── schemas/                # JSON Schemas (canonical record, manifests, intake)
├── rfcs/                   # Architecture proposals
├── kit/                    # Participant onboarding kit
└── tests/                  # pytest suite (synthetic data only)
```

## Principles

1. **Your data stays with you.** Local storage, local processing. No cloud
   accounts, no telemetry, no ads. Data leaves your machine only if you choose,
   and only ever as anonymized artifacts.
2. **A helper, never a doctor.** Nothing here diagnoses or prescribes. Findings
   are cautious prompts to test, graded C1–C5; anything at C3 or below is phrased
   as a question. Red flags route you straight to a clinician.
3. **Modular by design.** Each module and connector is a plugin. Install and run
   only what you need.
4. **Raw evidence is immutable.** Once archived, sources are never edited;
   provenance and confidence travel with every record.
5. **Quality over speed.** A few working connectors beat fifty broken ones.
   Security and privacy are non-negotiable.
6. **AI-native development.** Built with AI coding tools behind strong guardrails,
   clear interfaces, and automated checks.

## How to contribute

| Action | How to start | Difficulty |
|--------|-------------|-----------|
| **Build a connector** | Copy `connectors/_template/`, return canonical observations | Medium |
| **Add a domain module** | Copy a module in `openhealth/modules/`, implement `schema()` + `compute()` | Medium |
| **Propose a hypothesis** | Copy `hypotheses/_template/`, fill it in, open a PR | Easy |
| **Add a reference norm** | Add a marker + source to `reference_ranges.py` | Easy |
| **Improve architecture** | Write an RFC in `rfcs/`, discuss on the PR | Advanced |
| **Report bugs** | Open an Issue from the template | Easy |

**Ground rules** (see [CONTRIBUTING.md](./CONTRIBUTING.md) and
[AGENTS.md](./AGENTS.md)):

- Telegram = discussion. GitHub = decisions and code. Nothing is official without
  an Issue or PR.
- All changes via Pull Request; direct push to `main` is blocked.
- Conventional Commits, scoped by area: `feat(connector):`, `fix(core):`, `docs:`.
- **Never commit secrets or real health data.** Synthetic test data only;
  pre-commit hooks and CI scan every PR. Run `make check` (lint + tests) first.

## Disclaimer

OpenHealth is a self-tracking and personal-experimentation tool. It is **not a
medical device and does not provide medical advice, diagnosis, or treatment.**
Outputs are observational signals and hypotheses to discuss with a qualified
professional — not conclusions. If you have a medical concern, or if a red-flag
symptom appears (chest pain, fainting, suicidal thoughts, a critical lab value),
seek professional care. Use at your own discretion.

## License

[MIT](./LICENSE) — do what you want, keep the copyright notice.

---

## Для участников AI Mindset Health Sprint

OpenHealth — это local-first система: все данные остаются на вашей машине, а
интерфейс — это разговор с агентом (Claude Code / Codex), а не приложение.

Как начать:

1. `pip install -e .`, затем `python -m openhealth init` (или `make setup`).
2. Скопируйте `templates/about-me.md` в приватное место и заполните: кто вы,
   ваша цель по здоровью, где лежат данные. Это контекст, который агент читает
   первым — без него каждый раз начинаете с нуля.
3. Дальше просто говорите с агентом: «залогируй, что плохо спал», «как моё
   восстановление за неделю?». Он сам запускает нужный модуль. Под капотом —
   тот же цикл: журнал → recovery → корреляции → действие.

Все команды, инструменты и быстрые рецепты — в [docs/COMMANDS.md](./docs/COMMANDS.md).
Пошаговый гид для новичка — [docs/START-HERE.md](./docs/START-HERE.md).
Памятка для спринта — [kit/HANDOUT.md](./kit/HANDOUT.md).

Важно: это инструмент самонаблюдения, **не медицинский совет и не диагностика.**
Любой вывод — гипотеза с оценкой уверенности (C1–C5), а не заключение. Красные
флаги (боль в груди, обмороки и т.п.) — сразу к врачу. Самый низкий порог входа —
экспорт Apple Health: нужен только iPhone.
