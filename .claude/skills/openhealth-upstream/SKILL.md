---
name: openhealth-upstream
description: Safely port useful code from a contributor's private health-tracking repo into the public OpenHealth project as a pull request, stripping all personal data along the way. Use when the user says "port to openhealth", "upstream from my health-os", "contribute from my personal repo", "extract from my personal health repo", "move this into openhealth", "pull this out of my personal health-os", or otherwise asks to move code from a private health workspace into OpenHealth. This skill is safety-critical — it must run BEFORE any file is copied, not after.
version: 0.1.0
---

# OpenHealth Upstream Contribution Workflow

You are helping an OpenHealth contributor take something valuable from their **private** health-tracking repository and turn it into a clean pull request on the **public** OpenHealth repo — without leaking a single byte of personal data.

This skill is safety-first. If any phase is ambiguous, **stop and ask the contributor** rather than guessing. A failed PR is fine. A leaked token, real health record, or real name is not.

## When to Use This Skill

- Contributor has their own `~/health-os`, `~/quantified-self`, `~/my-health`, or any private health-tracking repo and wants to upstream part of it
- They're porting a connector, a parser, a schema extension, a bug fix, a hypothesis definition, or a CLI command
- They ask for help "cleaning up" personal code for open source
- They mention preparing a PR against `github.com/igindin/openhealth`

## Non-Negotiable Safety Rules

1. **Never** `cp`, `mv`, `rsync`, or `git clone` the personal repo into the OpenHealth worktree.
2. **Never** run `git add -A` or `git add .`. Always stage files explicitly by name.
3. **Never** include the personal repo's path, username, folder layout, or owner name in commit messages, PR bodies, branch names, or code comments.
4. **Never** reuse real fixtures (`*.csv`, `*.json`) from the personal repo. Always generate synthetic data.
5. If Phase 2 flags anything as `NEVER-PORT`, **stop** and get explicit contributor confirmation. Do not silently filter.
6. If in doubt — ask. Abort and ask. The contributor is the final authority on their own data.

## Phase 1 — Scope & Consent

Before touching anything, ask the contributor (in one message, concise):

1. **Absolute path to your personal repo?** (e.g. `/Users/alice/health-stuff`) — must be outside the OpenHealth worktree.
2. **What do you want to upstream?** One of: `connector`, `core fix`, `schema addition`, `parser`, `hypothesis`, `CLI command`, `docs`, `other (describe)`.
3. **Your GitHub username?** (for branch naming and PR routing — will not appear inside code).
4. **Is there an existing RFC or issue for this?** (`core/` changes require one — see `CONTRIBUTING.md` review matrix).

Then, **read these OpenHealth files** to anchor on current conventions (do not modify them):

- `CONTRIBUTING.md` — review matrix, branch naming, conventional commit scopes
- `CLAUDE.md` — Python style rules (type hints, no `Any`, dataclasses, no hardcoded secrets)
- `connectors/_template/connector.py` — the target shape for connector ports
- `openhealth/models.py` — canonical dataclasses the upstreamed code must use
- `openhealth/config.py` — `SOURCE_TYPES` tuple to extend (never invent a parallel one)
- `openhealth/ingest.py` — use `ingest_path` as the single entry point
- `openhealth/storage.py` — use `ensure_repo_structure`, not ad-hoc folder creation
- `.github/PULL_REQUEST_TEMPLATE.md` — the existing checklist; do not duplicate it, extend it

**Refuse to proceed** if:
- The personal repo path is inside the OpenHealth clone's working tree (the two repos must be on disk in separate directories).
- The contributor can't state what they're upstreaming in one sentence.
- The change touches `core/` without a linked RFC.

## Phase 2 — Mandatory Safety Audit (BEFORE Any Copy)

Run a **read-only** pass over the candidate files in the personal repo using the full detection pattern list in [SANITIZATION-CHECKLIST.md](SANITIZATION-CHECKLIST.md).

Categories to scan for, in order:

1. **Credentials** — `*_TOKEN`, `*_SECRET`, `*_API_KEY`, `Bearer `, `client_secret`, `access_token`, `refresh_token`, Vercel OIDC, GitHub PATs
2. **Personal identifiers (PII)** — real first/last name, email, phone, Telegram handle, literal `user_id`
3. **Real health data** — anything under `data/raw/`, `data/processed/`, WHOOP API responses, HR arrays, sleep sessions, rsid variants, microbiota tables
4. **Personal paths** — `/Users/<name>/`, `~/health-os/`, iCloud paths, hardcoded home directories
5. **Hardcoded locations** — home city, GPS coordinates, addresses, timezones keyed to the contributor
6. **Binary/data files** — `*.sqlite`, `*.sqlite3`, `*.db`, `*.vcf`, `*.fastq`, `*.parquet`, `*.pkl`, images in `data/`

Produce a **classification table** for every candidate file:

| File | Classification | Notes |
|------|----------------|-------|
| `connectors/whoop/client.py` | NEEDS-SANITIZATION | Strip `owner="alice"`, rewrite imports |
| `data/raw/whoop/2024-05-01.json` | NEVER-PORT | Raw API response with real HR |
| `core/models/observation.py` | SAFE | Pure dataclass, no personal references |

Classifications:
- **SAFE** — can be copied as-is after import/env-var rewrites
- **NEEDS-SANITIZATION** — has personal traces that must be transformed in Phase 4
- **NEVER-PORT** — raw data, tokens, PII containers — must never enter the OpenHealth worktree

**Hard stop**: Show the contributor the full table. For every `NEVER-PORT` row, get explicit confirmation that they agree to exclude it. Do not proceed until every row is acknowledged.

## Phase 3 — Isolated Work Branch

Work only inside the OpenHealth repo. Do **not** create any files outside it and do **not** bring the personal repo into it.

```bash
cd /path/to/openhealth
git checkout main
git pull
git checkout -b feat/<scope>-<short-description>
```

Branch naming follows `CONTRIBUTING.md`:

- `feat/connector-<name>` — new connector
- `feat/core-<area>` — core addition (requires linked RFC)
- `fix/<short-bug>` — bug fix
- `docs/<topic>` — docs only
- `rfc/<proposal>` — new RFC

**Never** include the contributor's username or the personal repo name in the branch.

Optional: use `git worktree add` if the contributor wants to keep their current main branch clean.

## Phase 4 — Sanitize & Generalize While Porting

Copy file-by-file. For each file:

1. **Read** the source file from the personal repo using the `Read` tool.
2. **Write** a transformed version into the OpenHealth worktree using the `Write` tool.
3. **Never** use `cp`, `mv`, or bulk operations.

Apply these transforms during every copy (full examples in [SANITIZATION-CHECKLIST.md](SANITIZATION-CHECKLIST.md)):

- `owner="<real name>"` → `owner="user"` (matches existing repo convention)
- `"author": "<real name>"` → `"author": "user"`
- `from health_os.<x>` → `from openhealth.<x>`
- `import health_os` → `import openhealth`
- `HEALTH_OS_*` env vars → `OPENHEALTH_*`
- `/Users/<name>/…` paths → parameterized `Path` arguments
- Hardcoded city (`"Budapest"`, `"Tbilisi"`, etc.) → `location: str` parameter
- Personal constants (target HRV, baseline weight, medication dosages) → function parameters with sensible documented defaults
- Strip comments referencing personal context ("my WHOOP account", "after I got sick in March", personal dates)
- Replace every fixture with **synthetic** data generated inline — never copy a real CSV or JSON
- Align data types with `openhealth/models.py` dataclasses (`RecordBase`, `Observation`, `TimelineEvent`, `Intervention`, etc.)
- Add type hints on every public function (per `CLAUDE.md`: no `Any`, prefer `object` or proper types)
- Extend `openhealth.config.SOURCE_TYPES` instead of inventing new source-type strings
- Add unit tests with **synthetic** data only

If a file is classified as SAFE but you notice anything personal during the copy — **stop and re-classify it as NEEDS-SANITIZATION**.

## Phase 5 — Verify Before PR

Run these checks in order. **Abort and fix** on any failure.

```bash
# 1. Lint
ruff check .

# 2. Unit tests
python -m unittest discover -s tests

# 3. Stage only the files you intend to ship (never -A / -.)
git add openhealth/<file1>.py tests/<file2>.py connectors/<name>/...

# 4. Final leak grep on the staged diff — all of these must return ZERO lines
git diff --staged | grep -iE '(ilya|gindin|<contributor-real-name>)'
git diff --staged | grep -iE '(access_token|refresh_token|client_secret|bearer )'
git diff --staged | grep -E '/Users/[^/]+/'
git diff --staged | grep -E 'HEALTH_OS_'
git diff --staged | grep -iE '[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}'

# 5. Line-by-line review with the contributor
git diff --staged

# 6. If trufflehog is installed, run it on the staged diff
git diff --staged > /tmp/openhealth-upstream.diff
trufflehog filesystem /tmp/openhealth-upstream.diff 2>/dev/null || true
rm /tmp/openhealth-upstream.diff
```

If any leak grep returns lines, go back to Phase 4 and sanitize the specific hits. Do not proceed to Phase 6 until every check is clean.

## Phase 6 — Draft the Pull Request

1. Commit with a Conventional Commits message. Scopes: `connector`, `core`, `schema`, `cli`, `docs`, `rfc`, `hypothesis`, `test`.

   ```
   feat(connector): add Garmin daily summary connector

   Ports a read-only Garmin daily summary connector that maps into
   openhealth.models.Observation. Uses openhealth.ingest.ingest_path
   as the entry point. No personal data is included; fixtures are
   synthetic.
   ```

   **Never** mention the personal repo path, owner, dates from the contributor's own history, or filesystem details.

2. Push the branch:

   ```bash
   git push -u origin feat/<scope>-<short-description>
   ```

3. Open the PR using `gh pr create` with the exact body from [PR-TEMPLATE.md](PR-TEMPLATE.md). Fill in every section. Leave the `## Sanitization audit` checklist visible so reviewers can see what was verified.

4. Request review per the `CONTRIBUTING.md` matrix:
   - `connectors/<name>/` → 1 review from core team
   - `core/` → 2 reviews + BDFL
   - `hypotheses/` → 1 review
   - `rfcs/` → community discussion + BDFL decision
   - `docs/` → 1 review

5. Post the review routing note in the PR body, not in a separate comment.

## Quick Checklist (paste into your working scratchpad)

- [ ] Phase 1: Got absolute personal repo path, scope, GitHub username, RFC link if needed
- [ ] Phase 1: Read CONTRIBUTING, CLAUDE, models, template
- [ ] Phase 2: Full classification table produced
- [ ] Phase 2: Every NEVER-PORT confirmed with contributor
- [ ] Phase 3: Fresh branch on OpenHealth repo only
- [ ] Phase 4: File-by-file copy via Read/Write
- [ ] Phase 4: All sanitization transforms applied
- [ ] Phase 4: Synthetic fixtures only
- [ ] Phase 5: `ruff check .` clean
- [ ] Phase 5: `python -m unittest discover` clean
- [ ] Phase 5: Every leak grep returned zero lines
- [ ] Phase 5: Contributor walked through `git diff --staged`
- [ ] Phase 6: Conventional Commits message, no personal references
- [ ] Phase 6: PR body follows PR-TEMPLATE.md with sanitization audit filled in
- [ ] Phase 6: Review routed per CONTRIBUTING.md matrix

## Related Files in This Skill

- [SANITIZATION-CHECKLIST.md](SANITIZATION-CHECKLIST.md) — detection patterns, before/after examples, the never-port list
- [PR-TEMPLATE.md](PR-TEMPLATE.md) — exact PR body to use in Phase 6
