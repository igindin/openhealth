# Commands & tools — the cheat-sheet

Everything you can say or run, in one place. **You almost never type CLI commands
by hand** — you talk to the agent and it runs them for you. This page is the map,
not a thing to memorize.

- New here? Start with [START-HERE.md](./START-HERE.md) (RU) / [START-HERE.en.md](./START-HERE.en.md) (EN).
- Sprint participant? [kit/HANDOUT.md](../kit/HANDOUT.md).

---

## Three ways to run it (pick one)

| Way | What you need | Time | Good for |
|-----|---------------|------|----------|
| **0 · Just look** | Nothing. Download ZIP, open `ui/web/index.html` (or `OpenHealth.command` on macOS) | 60 s | See the dashboard on demo data |
| **1 · Files + agent** | Claude Code or Codex on your machine. No `pip`, no install | 5 min | Talk to the agent in this folder, log a journal, draft a goal — files only |
| **2 · Full engine** | Python 3.10+, `pip install -e .` | 10 min | Real connectors (Apple Health/WHOOP), modules, recovery scoring, dashboard with live data |

```bash
# Way 2 — full engine, from the repo root
pip install -e .                 # or: make setup  (installs + inits a workspace)
python -m openhealth init        # create local folders + SQLite index
```

> The `kit/` folder is **inside this repo** — it is not a separate download.
> Open the repo root in your agent; `make` targets and `python -m openhealth`
> run from there.

---

## The real interface: talk to the agent

You don't need commands. Just say what happened or what you want:

- *"log that I slept badly and had two coffees"*
- *"how's my recovery this week?"*
- *"what actually moves my energy?"*
- *"read this lab PDF and flag anything out of range"*
- *"start with what I have"* — when you have almost no data yet

The agent picks the right module or CLI call under the hood.

---

## Agent slash commands (live)

These ship in `.claude/commands/` and work the moment you open the repo in Claude
Code / Codex.

| Command | What it does |
|---------|--------------|
| `/openhealth` | The orchestrator — onboards you, routes to everything below |
| `/kit` | Install only the skills you ask for (e.g. *"sleep + a daily check-in"*) |
| `/checkin` | Daily low-friction check-in (yesterday's sleep, mood, behaviors) |
| `/log` | Log a single behavior, note, meal, supplement, event |
| `/pulse` | Recovery / HRV read for today |
| `/sleep` | Sleep & circadian view |
| `/trends` | Trend lines across your metrics with dates |
| `/insights` | Generate cautious, evidence-graded hypotheses |
| `/protocol` | Set up an n-of-1 experiment (toggle one thing, measure) |
| `/cycle` | Menstrual-cycle domain view |
| `/body` | Body composition / weight domain view |
| `/ship` | Log a daily ship (contributor habit) |

**Scaffolded, not done yet** (good-first contributions — see `kit/registry.yaml`):
`/track` (your own custom habits), research-consilium (deep-research + critic),
the adaptive daily cockpit, mental-health and MSK domains, Oura & genetics
connectors. They install as stubs and tell you they're stubs.

---

## CLI reference (`python -m openhealth …`)

The substrate the agent drives. Run from the repo root after Way 2.

**Workspace**
```bash
python -m openhealth init                 # create folders + SQLite index
python -m openhealth refresh-contexts     # rebuild contexts + insights from the index
python -m openhealth show-summary         # lightweight JSON summary of indexed data
python -m openhealth recent --type InsightHypothesis     # recent records/insights
python -m openhealth recent --metric rmssd_ms --limit 30
```

**Get data in**
```bash
python -m openhealth ingest --path <file-or-dir>         # drop any supported source
python -m openhealth import-apple-health --path ~/apple_health_export/export.xml
```

**Modules (the scoring engine)**
```bash
python -m openhealth modules                             # list domain modules
python -m openhealth module --id recovery --payload-json '{ ... }'
```

**WHOOP** (OAuth — see [INTEGRATIONS.md](./INTEGRATIONS.md))
```bash
python -m openhealth whoop-auth-url                      # start OAuth, get a URL
python -m openhealth whoop-exchange-code --code <code>   # exchange the code for tokens
python -m openhealth whoop-sync --days-back 30           # pull recovery/sleep/strain/HRV
python -m openhealth whoop-capabilities                  # what the public API exposes
python -m openhealth whoop-latest                        # latest synced timestamps
```

**Withings** (scales, OAuth)
```bash
python -m openhealth withings-auth-url
python -m openhealth withings-exchange --code <code>
python -m openhealth withings-sync
```

**Telegram intake bot** (text/voice/photo → records)
```bash
python -m openhealth bot-start            # polling mode; allowlist-first. See docs/TELEGRAM.md
```

---

## make targets

```bash
make help        # list targets
make setup       # pip install -e . + init a workspace
make onboard     # guided first-run for a newcomer
make dashboard   # build local data + open the web dashboard
make modules     # list domain modules
make test        # run the test suite
make lint        # lint
make check       # lint + tests — run before every PR
```

---

## Quick recipes ("I have X, I want Y")

| You have… | You want… | Do this |
|-----------|-----------|---------|
| Just an iPhone | A baseline in 10 min | Export Apple Health → *"import my Apple Health export from ~/Downloads"* |
| One lab PDF | Markers flagged | *"read my blood test PDF and flag anything out of range"* |
| A WHOOP | Recovery/HRV synced | `python -m openhealth whoop-auth-url` → `whoop-sync --days-back 30` |
| A smart scale | Weight trend | Withings: `withings-auth-url` → `withings-sync` |
| Nothing yet | To just start | *"start with what I have"* + `/checkin` once a day |
| A question (coffee? sleep?) | A real answer | Pick **one**: *"how does coffee affect me?"* → the agent sets up a `/protocol` |
| A new tracker export | It in the timeline | *"import this CSV from my tracker"* (date + value columns) |

---

## The rules (non-negotiable)

- **Thinking partner, not a doctor.** No diagnoses. Medical decisions → a clinician.
  Anything alarming (chest pain, fainting, a critical value) → stop and seek care.
- Every meaningful number carries a **confidence label (C1–C5)**; low confidence
  is phrased as a question, not a verdict.
- **Local-first.** Nothing is uploaded. Real data and secrets stay on your machine;
  the dashboard server listens on `127.0.0.1` only. No telemetry.
- **Don't copy any skill blindly.** A repo may be over-built for you or aimed at a
  different goal. Adapt it to yours.

---

## Troubleshooting

- **macOS won't open `OpenHealth.command`** — right-click → Open, then Open again
  (one-time Gatekeeper prompt). Fallback: double-click `ui/web/index.html`.
- **`make setup` / `make: command not found`** — you're not in the repo root, or
  on Windows. Use `pip install -e .` then `python -m openhealth init` instead.
- **Dashboard buttons ("generate insight") do nothing** — they need an agent on
  the machine (Claude Code or Codex) and the local bridge running. Demo data works
  without them; live agent actions don't.
- **Windows** — the `.command` launcher is macOS-only. Open `index.html` for the
  demo; run `python ui/web/server.py` for the live bridge.
- **No data shows up** — you're on demo data until you run Way 1/2. Real values
  land in `data/` and `ui/web/data.local.json`, both git-ignored.

---

*Contributing? See [README.md](../README.md#how-to-contribute) and
[AGENTS.md](../AGENTS.md). Telegram = discussion, GitHub = decisions, every change
via PR, `make check` green before you push.*
