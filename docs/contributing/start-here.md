# Start here (no coding or git experience needed)

OpenHealth is built and used **through an AI agent** — Claude Code or Codex. You
talk to the agent in plain language; it writes the code, runs the tests, and
ships your change. You never need to learn git.

## First 5 minutes

1. Open this folder in **Claude Code** (or Codex).
2. In the chat, say: **"run make setup"**. The agent installs everything and
   creates a local workspace. (Under the hood: `make setup`.)
3. Say: **"show me the health modules"**. (Under the hood: `make modules`.)
4. Try using the product as a person would — type `/checkin`, or
   `/pulse`, `/sleep`, `/cycle`, `/body`. The agent asks you simple questions and
   logs your data locally. Nothing leaves your machine.

## Make your first contribution

1. Open **TASKS.md** and pick a card that looks fun (each says exactly what to
   do, which file, and how to know it works).
2. Tell the agent: **"let's do task N from TASKS.md"**. It will write the code
   and the test with you.
3. Say: **"check it"** — the agent runs `make check` (lint + tests).
4. When it's green, say: **"ship it: <one line about what you did>"**. The agent
   runs `/ship`, which makes a branch and commit for you. (A maintainer enables
   the final publish step.)

## The only rules

- We never diagnose or give medical advice — we surface gentle prompts.
- Every number that means something carries a confidence label (C1–C5); low
  confidence is always phrased as a question.
- Only synthetic, made-up data in the repo — never real personal health data,
  never secrets/keys.

That's it. Pick a card and tell the agent.
