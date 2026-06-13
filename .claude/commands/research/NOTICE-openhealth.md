# NOTICE — deep-research skill (vendored)

This `research` skill is **vendored from a third-party project** and adapted for OpenHealth.

## Origin & license

- **Source:** https://github.com/tonyazhuuki/deep-research-skill
- **License:** MIT — Copyright (c) 2026 Tonya Zhuuki. Full text preserved at [`./LICENSE`](./LICENSE).
- Shared publicly by the author for the AI-Mindset health sprint, with an explicit invitation to fork and adapt.

The MIT copyright notice above is a license obligation and is retained verbatim. Crediting the
author in the project-level [`CONTRIBUTORS.md`](../../../CONTRIBUTORS.md) (RFC-002 idea-contributor
row) is **pending her explicit confirmation** — ping her before adding that row.

## OpenHealth adaptations (what we changed)

- `tools/research_adapters/db_lookup.py`: genericized the HTTP `User-Agent` (removed the author's
  personal contact); added a stdlib-only macOS CA-cert bootstrap (`SSL_CERT_FILE`) so the biomedical
  DB calls verify TLS on python.org builds. Verified live: ClinVar, SNPedia, ClinicalTrials.gov,
  Reactome return real data.
- Excluded the author's private `context.md` (kept `context_template.md` only).
- API keys and the filled personal `context.md` are gitignored — they never enter the repo.

## How it ties into OpenHealth

- Run `/research <topic> [priority] [hours] [mode]` inside Claude Code (alongside the other
  `.claude/commands/`).
- Copy `context_template.md` → `context.md` (gitignored) and point it at your vault. Set the output
  directory to your **personal** vault (e.g. `~/health-os/research/`), never the public repo.
- OpenHealth already surfaces a `research/` folder: `ui/web/server.py build_user_context()` feeds the
  freshest research files into the agent context, and the dashboard shows a per-marker research badge
  (`renderResearchBadge` / `runBioResearch`). So skill outputs in `research/` appear automatically.
- Confidence: the skill grades claims 0-1 (GRADE/PICO for health). Map to OpenHealth's C1-C5 in
  `openhealth/evidence.py` when writing back into the vault.
- Optional DB keys (PharmGKB/OMIM/NCBI/OpenFDA) live in `~/.research_db_keys.json` (chmod 600);
  without them the no-auth databases still work and missing ones degrade gracefully.
