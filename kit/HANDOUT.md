# Health Sprint Kit — start here

A small library of skills you drive through **Claude Code / Codex**. You talk; it
logs your data, finds patterns, and helps you build your own health system. No
GUI to learn. Your data stays on your machine.

> **You do not need 20 years of scans or a WHOOP.** Start with an iPhone and one
> question. Match nobody. The people loading decades of PDFs are optional
> inspiration, not the bar.

> This kit lives **inside the OpenHealth repo** (the `kit/` folder) — it is not a
> separate download. Open the **repo root** in your agent.

## Pick your path

You can do the whole sprint on the **light path** — just files and the agent,
nothing installed. The full engine is optional, for when you want real connectors
and recovery scoring.

- **Light path (default for the sprint).** Open the repo folder in Claude Code /
  Codex and talk to it. No Python, no `pip`, no `make`. You can draft your goal,
  fill `about-me`, log a journal, and pick one experiment — all in files.
- **Full engine (optional).** From the repo root: `pip install -e .` then
  `python -m openhealth init` (or `make setup`). This unlocks Apple Health / WHOOP
  imports, domain modules, recovery scoring, and the live dashboard.

Full command map: [docs/COMMANDS.md](../docs/COMMANDS.md). Step-by-step beginner
guide: [docs/START-HERE.md](../docs/START-HERE.md).

## 15-minute start

1. Open this repo's **root folder** in Claude Code (or Codex).
2. Say **`/kit`** and what you want, e.g. *"sleep + a daily check-in"*. It
   installs only what you asked for.
3. Get one source of data in:
   - iPhone → Health app → your photo (top right) → **Export All Health Data** →
     you get a `.zip`. Then say: *"import my Apple Health export from ~/Downloads"*
     (this one uses the full engine).
   - Or just one lab PDF, or even nothing — say *"start with what I have"*.
4. Pick **one** question: *"what moves my energy?"* / *"what breaks my sleep?"* /
   *"how does coffee affect me?"*. Say it; the agent sets up a tiny experiment.
5. Say **`/checkin`** once a day during the live test. That's it.

## What's inside (see `registry.yaml` in this folder)
- **Signals** (get data in): Apple Health, WHOOP, CSV, lab PDF (Oura/genetics scaffolded).
- **Knowledge**: labs, sleep, cycle, body, metabolic, skin (pulse/HRV optional).
- **Actions**: `/checkin`, `/protocol` (n-of-1), `/track` your own habits *(stub)*.
- **Trust**: deep-research + a hypothesis/critic/safety consilium *(stub)*, evidence C1–C5.
- **System**: `/kit` loader, an optional adaptive daily cockpit *(stub)*.

Cards marked *stub* are scaffolded but not finished — they install and tell you so.
They're the best **good-first contributions** if you want to build one out.

## The rules
- This is a **thinking partner, not a doctor.** No diagnoses. Medical decisions
  go to a clinician. Anything alarming → stop and seek care.
- Every meaningful number carries a **confidence label (C1–C5)**; low confidence
  is phrased as a question.
- **Don't copy any skill blindly.** A repo may be over-engineered for you, or
  built for a different health goal. Adapt it to yours.
- Local-first: nothing is uploaded. Real data and secrets stay on your machine.

OpenHealth is early and rough in places — that's fine. You're building *yours*.
