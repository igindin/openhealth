---
name: contributor-harvest
description: Maintainer-side workflow to bring another person's Health-OS work into OpenHealth as an ATTRIBUTED pull request. Review their build, strip every byte of personal data, classify each finding into the RFC-002 contribution streams, diff against the current repo to avoid duplication, and open a DRAFT PR that credits them as a contributor — only with their explicit consent. Use when the maintainer says "harvest from X's health os", "забери наработки контрибьютора в openhealth", "review someone's HealthOS and PR it", "credit X as a contributor", "pull ideas from <their Vercel/repo> with attribution". Sibling of `openhealth-upstream` (which is self-upstreaming); this one is maintainer-side and cross-person, so it INVERTS the name rule — it credits the source by name, by consent, while still never shipping their data. Safety-critical: personal data NEVER enters the PR; nobody is credited without explicit consent.
version: 0.1.0
---

# Contributor Harvest — attributed, privacy-safe intake from another Health OS

You are helping the **maintainer** of OpenHealth take valuable work from **someone
else's** personal Health OS (a sprint partner, another forker, a shared
Vercel/repo) and turn it into a clean, **attributed** pull request — so that person
becomes a real contributor — **without shipping a single byte of their personal
health data, and without crediting anyone who has not agreed to it.**

This is the maintainer-side mirror of [`openhealth-upstream`](../openhealth-upstream/SKILL.md).
Two differences drive everything:

1. **Cross-person, not self.** The work belongs to someone else. That makes
   **consent** the first gate, not an afterthought.
2. **Attribution is the point.** `openhealth-upstream` strips the owner's name
   (self-upstreaming → anonymize self). Here we do the opposite: we **credit the
   source by name**, because the whole goal is to let them "числиться
   контрибьютором". But we credit only with consent, and we still strip all of
   their **data**.

Reuse, do not reinvent, the detection machinery in
[`openhealth-upstream/SANITIZATION-CHECKLIST.md`](../openhealth-upstream/SANITIZATION-CHECKLIST.md).
This skill adds the consent gate, the attribution mechanics, the
diff-against-repo step, and the mapping to RFC-002.

## When to Use

- Ilya wants to bring ideas/patterns from another person's Health OS into OpenHealth
  and have them credited (the originating motivation: "я забрал идеи других билдеров,
  но хочу чтобы они числились контрибьюторами").
- A sprint participant shared their HealthOS (repo, Vercel deploy, exported files,
  screenshots) and there's something worth adopting.
- The maintainer says "harvest", "забери наработки", "credit X", "PR their ideas".

Do **not** use this for the contributor upstreaming their *own* repo — that's
`openhealth-upstream`. Do **not** use it to scrape work nobody shared.

## Non-Negotiable Rules (read before anything)

1. **Personal data never enters the PR.** RFC-002 Stream 3 (raw files, observations,
   photos, lab values, genome, personal protocol/mission *content*, recovery/HRV
   numbers, anyone's actual measurements) has **no PR path by design**. If a finding
   carries data, you ship the *idea/structure*, never the data.
2. **No consent → no harvest, no credit.** Never credit a person, name them in a
   commit/PR, or contribute "their" work without their **explicit** agreement to
   both. A public Vercel demo is *visible*, not *consent to be credited*. Ask.
3. **Ideas and public formats are free; their code is theirs.** Architecture,
   information patterns, reference ranges, insight-template shapes, public API
   formats — fine to adopt and re-express in our own code. Verbatim copying of their
   source needs a license-compatible grant from them. When unsure, re-implement the
   idea, don't copy the bytes.
4. **Draft PR + human approval before publish.** Open PRs as **draft**. Ilya
   approves before "ready for review"; the credited person confirms before merge
   (RFC-002 tiered review). Never auto-open a non-draft PR, never merge autonomously.
5. **Strip identity except the consented credit.** Their private paths, usernames,
   tokens, folder layout, device names — out. The *only* identity that may appear is
   the agreed contributor credit (display name + the public source they pointed you
   to).
6. **If in doubt, stop and ask.** A missed contribution is fine. A leaked health
   record, a fabricated credit, or an unconsented attribution is not.

## Phase 1 — Consent & Source Scoping

Before reading anything substantive, confirm in one concise message:

1. **Whose work, and did they agree?** Name + confirmation they're OK with (a) you
   reviewing it and (b) being **credited as a contributor**. If consent isn't
   established, the only allowed next step is to draft a short ask for them — not to
   harvest.
2. **Where is the source?** A repo URL, a deployed surface (e.g. a Vercel like
   `healthos-architecture.vercel.app`), exported files, or a local path **outside**
   the OpenHealth worktree. Record it as the public provenance for attribution.
3. **What's the focus?** Architecture pattern / a feature / reference norms /
   insight templates / a connector / methodology. One sentence.
4. **How do they want to be credited?** Display name + (optional) handle/email for
   `Co-Authored-By`. Never invent these.

Then read these repo files to anchor (do not modify yet):
`rfcs/002-community-contribution-model.md` (the three streams), `CONTRIBUTING.md`
(review matrix, branch/commit conventions), `CLAUDE.md` / `AGENTS.md` (style + data
rules), `.github/PULL_REQUEST_TEMPLATE.md`, and the diff targets you'll compare
against (`openhealth/reference_ranges.py`, `openhealth/insights.py`, `connectors/`).

**Refuse to proceed** if consent is unclear, the source is someone's private data
they did not share, or the request is to copy their code verbatim without a grant.

## Phase 2 — Harvest (read-only)

Survey the source and extract **ideas, structures, and patterns** — never data:

- **Architecture** — layers, module boundaries, naming, data flow (e.g. a 7-layer
  agentic stack, a build step that resolves markers to live values).
- **Features / surfaces** — what screens/flows/skills they built and the *shape* of
  each (e.g. a "research map" with queue/confidence/domains; a "mission cascade"
  mapping dream→goal→lever→protocol).
- **Knowledge artifacts** — reference ranges they encoded, insight/hypothesis
  templates, methodology docs.
- **Connectors** — providers they integrated and the source-exporter shape.

Write the findings to a scratch note **with all data redacted as you go** (use
placeholders: `<their goal text>`, `<a biomarker>`). Do not copy personal mission
text, real protocol names tied to a person, or any measurement.

## Phase 3 — Mandatory Sanitization + RFC-002 Classification

Run the full read-only detection pass from
[`SANITIZATION-CHECKLIST.md`](../openhealth-upstream/SANITIZATION-CHECKLIST.md)
(credentials, PII, real fixtures, embedded values). Then **classify every candidate
into exactly one RFC-002 stream**:

- **Stream 1 — Code** (connectors / parsers / core). Re-expressed in our
  conventions, synthetic fixtures only, tests included.
- **Stream 2 — Knowledge artifacts**, the anonymized layer that carries knowledge
  without anyone's data:
  - **(a) Reference norms** → `reference_ranges.py` additions, each citing an
    authoritative source (LOINC / guideline / peer-reviewed range). Facts about
    tests, never about a person.
  - **(b) Insight templates** → the RFC-002 shape: `id`, `statement`, `requires`,
    `confidence` (C1–C5), `sources`, `validation`. The idea + required signals +
    confidence + sources + a suggested n-of-1 protocol — **no one's results**.
- **Stream 3 — Personal data** → **DROP**. Never reaches a PR. If a great idea is
  entangled with their data, keep the idea (Stream 1/2), discard the data.

A finding that can't be cleanly placed in Stream 1 or 2 without dragging data along
gets reworked until it can — or dropped. Run a **final PII scan** over the
sanitized artifacts before Phase 5; if anything trips, stop.

## Phase 4 — Diff Against OpenHealth (don't duplicate)

For each sanitized artifact, compare to what's already in the repo:

- Reference norm already in `reference_ranges.py`? Skip or improve with a better
  source.
- Insight template already present? Skip, or extend `requires`/`sources`.
- Feature/pattern already shipped (check the dashboard zones, skills, connectors)?
  Then the contribution is the *delta* — the part that's genuinely new or better,
  not a re-paste.

Produce a short **gap report**: `NEW` (contribute), `BETTER` (contribute as
improvement, cite why), `DUP` (drop). Only `NEW`/`BETTER` proceed.

## Phase 5 — Re-express as OpenHealth-native

Write the `NEW`/`BETTER` items in OpenHealth's conventions (Stream-1 code uses
`openhealth/models.py` dataclasses, the `connectors/_template` shape, `SOURCE_TYPES`,
type hints, no secrets; Stream-2 artifacts use the RFC-002 shapes). Generate
synthetic fixtures; never reuse theirs. Keep each PR **small and single-purpose**
(one connector, one norm set, one template family).

## Phase 6 — Attributed Draft PR

1. Branch off `main` with a neutral name: `feat/<area>-<short-desc>` (no personal
   paths/usernames).
2. Stage **only** the files you authored, explicitly by name (never `git add -A`).
3. **Credit the source**, with their consent:
   - `Co-Authored-By: <Display Name> <email-or-noreply>` in the commit (only if they
     gave a name/email for this).
   - Add/extend **`CONTRIBUTORS.md`** with their name + the public provenance they
     shared (create the file if missing; one line per person, idea summary, link).
   - PR body: a one-paragraph "Credit" section naming them and the HealthOS the idea
     came from, linking `rfcs/002-community-contribution-model.md`, and stating
     plainly that **no personal data was included**. Use
     `.github/PULL_REQUEST_TEMPLATE.md` and extend it, don't duplicate.
4. Open as **DRAFT** (`gh pr create --draft`). Then **stop** and hand to Ilya for
   approval. After Ilya's OK, ping the credited person to confirm the attribution
   before it's marked ready / merged (RFC-002 tiered review).

## Output

End with: the gap report (NEW/BETTER/DUP), the list of files authored, the draft PR
URL, the CONTRIBUTORS.md entry, and an explicit line: "Personal data shipped: none —
verified by final PII scan." If any phase was blocked (no consent, PII tripped),
say so and what's needed to unblock.

## Anti-patterns

- Treating a public demo as consent → always ask before crediting.
- Crediting by guessing a name/handle → never fabricate attribution.
- Porting their mission/protocol *text* or any measurement → that's Stream 3, drop
  it; ship the structure only.
- One giant PR mixing a connector + norms + templates → split per stream/topic.
- `git add -A`, reusing their fixtures, or putting their repo path in a commit.
- Opening a non-draft PR or merging without Ilya + the source person confirming.
