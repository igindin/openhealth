# Plan: Device and protocol-source library (the OpenHealth knowledge layer)

**Goal:** Assemble a curated library of (1) devices and alternatives for health/recovery/training and (2) protocol sources at the Attia/Huberman level (YouTube/podcasts), plus (3) an educational layer — for EVERY metric, a "what it is / how it works" explanation and short authoritative videos (Huberman clips, Attia and others), so we match and then beat WHOOP/Welltory on comprehensibility. All of it built into OpenHealth as a reference/knowledge layer that feeds metric provenance, n=1 protocols and interpretations.

**Architecture:** Deep-research fan-out (web/YouTube) → a structured reference file with provenance (link + date + confidence) → read by the engine as one more registry-driven section, "Devices and sources", available in both skins. This is a knowledge-enrichment layer: protocols (`data.local.json` → `protocols[]`) and interpretations (`provenance`) point at a source from the library.

On top of that, a **knowledge verifiability system**: every claim (a metric interpretation, a protocol, a piece of advice, a video link) carries an evidence level (our C1-C5 scale + evidence type: meta-analysis / RCT / observational / expert opinion / n=1) and a chain of sources. The user can expand the chain "claim → source → level" and hit "re-verify" (already in `oh-provenance.js`: re-verify through the local agent). The goal is not "trust us" but "here is what this rests on and how to check it".

**Tech Stack:** deep-research harness (web search + adversarial verify + cited synthesis), registry-first JSON, provenance/confidence (C1-C5, as we already do for biomarkers and patterns), vanilla JS rendering from the engine.

**Created:** 2026-06-18

**Progress (2026-06-21):** ✅ Tasks 2-9 done. `knowledge.json` (22 devices across 6 categories, 15 sources, 9 videos) with provenance + an honest evidence_level; the "Devices and sources" section in both skins (V2 bento + V1 native zones); video explainers in each metric's "?" popup; the evidence scale (high/mid/low ↔ C1-C5) on every entry; the verifiability UI (level + sources + "re-verify"). ✅ Task 10 — RFC `rfcs/003-proof-layer.md` (design only, privacy-first). Task 1 (extraction from the transcript) and Task 6 (link n=1 protocols to sources) are deferred: the knowledge was gathered by deep research directly, and the linking waits for real n=1 protocols in `data.local.json`.

**Success Signals:**
- A device library file: organized by category (HRV/recovery trackers, sleep, metabolic/CGM, training/power, vagus/neuro/meditation), each entry covering what it measures, alternatives, price tier, and a source link.
- A protocol-source library file: roughly 12-15 experts at the Attia/Huberman level — who they are, their topics, format, a link to the channel/podcast, and where to lean on them carefully.
- Every entry has provenance (URL + date + confidence); nothing is invented.
- The library is reachable as a dashboard section (both skins, from the registry), and "?" leads to the source.
- The training module's n=1 protocols can reference a source from the library.
- Every key metric has a "what it is / how it works" explanation plus at least one short authoritative video (parity with WHOOP/Welltory on comprehensibility).
- Every claim/piece of advice/protocol shows its evidence level (C1-C5 + type) and its sources; the user can expand the "what this rests on" chain and hit "re-verify". Opinion and n=1 are labeled honestly and never passed off as proven.

**DON'T DO:**
- Do NOT give medical recommendations or prescriptions; everything is reference material, with a disclaimer and a confidence level (we are not a doctor — a project rule).
- Do NOT invent entries: every device and expert entry needs a source link (provenance is mandatory, per the Evidence policy).
- Do NOT buy or order devices and do NOT advise purchases — description and comparison only.
- Do NOT carry the conversational metaphors and esoterics from Gera's presentation into the library.
- Do NOT hardcode the list in HTML — registry/reference file only (engine discipline).
- Do NOT let it bloat: the target volume is curated (5-6 device categories, ~12-15 experts), not an encyclopedia.
- Proof layer (Task 10): NEVER put personal health data on a blockchain or in public — hashes/commitments only. Do NOT introduce tokens or coins and do not do "blockchain for blockchain's sake"; start with a cheap timestamp anchor (OpenTimestamps) or a signed log, and build the full chain only once there is a real community. This is R&D, not a priority.

**Verify First (facts):**
- The core from the presentation transcript (`health-os/docs/transcripts/2026-06-17-gera-aimindset-health-data.txt`): lactate meter, glucometer/CGM, Stride/PowerPod (running biomechanics), Garmin, WHOOP; Huberman is mentioned. From the chat (per the agent's report): Pulsetto (vagus), BrainTap (visors), EEG headphones, Endel.
- OpenHealth already has the C1-C5 trust layer, `biomarkers` with reference ranges, `protocols[]` with `protocol_ref`/`confidence_cap`, and the "?" provenance popup (oh-provenance.js). What does NOT exist yet is the "devices/sources" reference section — that is what we are adding.
- Data discipline (from the review): the real/demo/empty/insufficient states; for the library the relevant part is showing only what was gathered with a source, with everything else as empty ("not added yet").

---

## Task 1: Extract the core from the transcript (devices + experts)
**File:** `docs/knowledge/_seed-from-transcript.md` (a working note)
**Action:** Write down everything actually mentioned in the presentation (devices, brands, expert names, Ilya's protocol routines), with timestamps. This is the seed for the research, so the context specifics are not lost.
**Verify:** The note contains at least 8 devices/brands and at least 1 expert from the transcript.
**Commit:** `docs(knowledge): seed devices/experts from Gera transcript`

## Task 2: Deep research — devices by category
**Action:** Run deep research for the categories (1) HRV/recovery trackers (WHOOP/Oura/Garmin/Apple Watch/Polar), (2) sleep, (3) metabolic/CGM (Libre/Dexcom/Lingo/Levels), (4) training/power/running (Stryd/PowerPod/Garmin power), (5) vagus/neuro/meditation (Pulsetto/Apollo/Muse/BrainTap/Endel), (6) lactate (lactate meters) — for each device: what it measures, its key metric, alternatives, price tier, what it is useful for in n=1, and a source link. Verify adversarially (what it actually measures, not the marketing).
**Verify:** A report with at least 5 categories, 3-5 devices each with sources; no unsupported claims.
**Commit:** (research artifact, no code)

## Task 3: Deep research — protocol sources (YouTube/podcasts at the Attia/Huberman level)
**Action:** Deep research: find roughly 12-15 authoritative sources on health/longevity/training protocols (e.g. Peter Attia, Andrew Huberman, Rhonda Patrick/FoundMyFitness, Stacy Sims, Inigo San Millan, Andy Galpin, Kelly Starrett, Layne Norton, Gabrielle Lyon, Dan Garner, Mike Israetel/RP — check each one's current status and reputation). For each: who they are, their field, format (podcast/video/protocols), a link to the channel, content type, and a caveat about where to lean carefully or where they are contested. Note the evidence level of the content.
**Verify:** At least 12 sources with working links + field + caveat; those closer to evidence-based medicine vs popularizers are explicitly marked.
**Commit:** (research artifact, no code)

## Task 4: Structure it into reference files with provenance
**Files:** `ui/web/assets/devices.json`, `ui/web/assets/protocol-sources.json` (or merge into `knowledge.json`)
**Action:** Turn the Task 2-3 results into JSON: `devices[]` ({id, category, name, measures, key_metric, alternatives[], price_tier, useful_for, source_url, checked_at, confidence}) and `protocol_sources[]` ({id, name, area, format, url, content_type, caveat, evidence_level, checked_at}). Every entry carries provenance.
**Verify:** The JSON is valid; every entry has source_url + checked_at; `python3 -c "import json;json.load(open(...))"` passes.
**Commit:** `feat(knowledge): devices + protocol-sources reference data`

## Task 5: Registry section "Devices and sources"
**Files:** `ui/web/assets/registry.json` (+ the engine if needed)
**Action:** Add a `knowledge` section (or two: `devices`, `sources`) to the registry with order/icon, reading from the reference files. Render as a list of categories (devices) and a list of sources (experts) — neutral markup that both skins theme (V1 classic, V2 bento). The "?" provenance leads to the source link. Empty categories get an honest empty state ("not added yet").
**Verify:** Chrome: the section is visible in both skins, devices/sources have links, provenance works; empty ones are honest empties. `make test` green.
**Commit:** `feat(web): devices & sources reference section (both skins)`

## Task 6: Link n=1 protocols to sources
**Files:** `data.local.json` → `protocols[]` (private), the provenance engine
**Action:** Give protocols a `source_id` field pointing at `protocol_sources`/`devices`. Show "based on: <source>" in a protocol's provenance. That way n=1 hypotheses are tied to an authority rather than to thin air.
**Verify:** For a protocol with a source_id, the provenance popup shows the source link.
**Commit:** `feat(knowledge): protocols reference their evidence source`

## Task 7: Educational layer — video explainers per metric (catch up to WHOOP/Welltory)
**Files:** deep research → `ui/web/assets/registry.json` (`metrics[].provenance.video_refs`)
**Action:** Deep research: for each key metric (HRV, recovery, RHR, sleep stages, strain, VO2max, stress, breathing, glucose) find 1-3 SHORT authoritative videos (Huberman clips, Attia, FoundMyFitness and similar) with a timecode. Add `video_refs[]` ({title, channel, url, timestamp, lang, evidence_level}) to the metric's provenance. We already have the "what it is / how it works" explanation (`provenance.what/how/why`) — the video complements it.
**Verify:** At least 8 key metrics have at least 1 working video link with a source; nothing is invented.
**Commit:** `feat(knowledge): authoritative video explainers per metric`

## Task 8: An evidence scale on every knowledge entry
**Files:** `ui/web/assets/registry.json` + the reference files (Task 4)
**Action:** Introduce a single evidence field for any piece of knowledge (metric interpretation, protocol, device claim, video link): `evidence: { confidence: C1-C5, type: meta|rct|observational|expert|n1, sources: [url], checked_at }`. Reuse the existing C1-C5 scale (personal patterns capped at ≤C3 — a project rule). Fill it in for the entries already gathered.
**Verify:** Every library entry and every metric interpretation has an `evidence` block with a type and at least 1 source; personal n=1 never exceeds C3.
**Commit:** `feat(knowledge): evidence grade (confidence + type + sources) on every claim`

## Task 10 (R&D, future): Proof layer — protocol pre-registration and experiment tamper-evidence
**Files:** the design doc `docs/rfcs/<date>-proof-layer.md` (RFC first, not code)
**Action:** Design a trust mechanism for n=1 that does not disclose data:
1. Pre-registration: a hash of {hypothesis, ABAB design, success_criteria, start date} → an immutable timestamp BEFORE the experiment (proof of priority against retrofitting). Cheap: OpenTimestamps (anchored in Bitcoin) or a signed append-only log; the full chain only once there is a community.
2. Tamper-evidence: Merkle hashes of `data.local.json` snapshots are recorded periodically — proving immutability WITHOUT disclosing the data.
3. Open-source reproducibility: protocols and methodologies are public (registry/knowledge), and anyone can run them against their own data.
4. (Future) Collective verification: how many people reproduced a protocol and with what result — aggregated, with privacy preserved.
Assess honestly whether a blockchain is needed at every step at all, or whether a timestamp anchor plus signatures is enough. Do not drag in tokens or hype.
**Verify:** The RFC describes what goes on the chain (hashes only), what does not (the data), the MVP option (OpenTimestamps), and the criterion for when a full blockchain becomes necessary.
**Commit:** `docs(rfc): proof layer — pre-registration + tamper-evidence (privacy-first)`

## Task 9: Verifiability UI — the "claim → source → level" chain + "re-verify"
**Files:** `ui/web/assets/oh-provenance.js` (+ both skins theme it)
**Action:** In the "?" popup, show: the evidence level (C1-C5 + type, as a colored badge), the list of source links (video/studies/expert), and the existing "re-verify" button (re-verify through the agent) — which now checks exactly the validity of the claim against the sources. Expand the "what this rests on" chain. Honest: if the knowledge is at the "expert opinion / n=1" level, that is stated outright and never passed off as proven.
**Verify:** Chrome (both skins): "?" on a metric shows the level + sources + video + "re-verify"; a low level is honestly labeled. `make test` green.
**Commit:** `feat(provenance): verifiable knowledge — evidence grade, sources, re-verify`

---

## Order and notes
- Task 1 → 2/3 (deep research, can run in parallel) → 4 (structuring) → 5 (UI section) → 6 (linking to protocols).
- Reference data (devices/experts) is a public good and lives in the public `openhealth`. Personal device choices and subscriptions stay private in `health-os` if needed.
- Connection to the training module (a separate brainstorm): protocol sources and devices feed n=1 protocols and the interpretations of "what counts as concerning". This knowledge layer is the foundation under that module.
- Out of scope: automatic parsing of expert videos/transcripts, a device recommendation system, purchases.
