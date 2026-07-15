# RFC-003: Proof layer — pre-registration & tamper-evidence for n=1 experiments (privacy-first)

**Status:** draft
**Author:** igindin
**Date:** 2026-06-21
**Supersedes:** —

## Motivation

OpenHealth lets a person run n=1 experiments on themselves: "does magnesium at
night raise my HRV?", run as an ABAB design over a few weeks. Two trust problems
make such results easy to dismiss — by others *and* by the experimenter's own
future self:

1. **Hindsight fitting (p-hacking).** It is trivial to decide the hypothesis and
   success criteria *after* seeing the data, then claim the protocol "worked".
   Without a record fixed *before* the experiment, an n=1 result is just a story.
2. **Silent data edits.** A skeptic (or the experimenter, months later) cannot
   tell whether the underlying daily data was quietly adjusted to fit the
   conclusion.

The hard constraint: solving this must **never** weaken OpenHealth's first
principle — *personal health data never leaves the user's machine*. So the goal
is to make an n=1 claim **checkable** without publishing any health data. The
slogan is not "trust us", it is "here is exactly what this stands on, fixed in
time, and here is how to check it".

This RFC designs the mechanism. It is explicitly **R&D, not first-priority**, and
**design-only** (no code ships with this RFC).

## Proposal

Four layers, in increasing cost and decreasing urgency. Only the **commitment**
(a hash) is ever externalized; the data behind it stays local.

### 1. Pre-registration of a protocol (proof-of-priority)

Before an experiment starts, the user freezes a small, structured record and
commits a hash of it. The plaintext stays in `data.local.json`; only the digest
is anchored.

The pre-registration record (design shape, not an implementation):

```jsonc
{
  "proto_id": "mg-hrv-2026-06",
  "hypothesis": "300mg Mg glycinate 1h before bed raises morning rMSSD",
  "design": "ABAB",                 // A=baseline, B=intervention, blocks of 7d
  "metric_id": "hrv",               // the registry metric being tested
  "success_criteria": "B-mean − A-mean ≥ 0.5·SD over ≥2 AB blocks (SWC)",
  "start_date": "2026-06-22",
  "planned_blocks": 4,
  "nonce": "<random 16 bytes>"      // prevents dictionary guessing of the digest
}
```

- **Commitment:** `commit = SHA-256(canonical_json(record))`. The `nonce` makes the
  commitment hiding (a third party cannot brute-force short fields).
- **Anchor:** `commit` gets an immutable timestamp *before* `start_date`. See
  §"What goes where" and §"MVP" for how.
- **Reveal (optional, later):** to prove priority, the user reveals the record;
  anyone recomputes the hash and checks it against the anchored timestamp. No
  reveal is ever required — the user chooses if/when to disclose.

This gives proof-of-priority: the hypothesis and success criteria provably
existed *before* the data did.

### 2. Tamper-evidence for the data behind a result

Periodically (e.g. nightly, or at each protocol block boundary), the engine
builds a **Merkle tree** over the day-records of `data.local.json` and anchors
only the **Merkle root**.

- Leaves: `SHA-256(canonical_json(day_record))` per day. Data never leaves the
  machine; only per-day digests form the tree.
- Anchor: the root (32 bytes) is timestamped like a commitment.
- Later, to prove a *specific* day was not altered after the fact, the user can
  disclose that day's record plus its Merkle inclusion path — revealing **one
  day**, not the whole history, and only if they choose to.

This proves the data underpinning a claim is the same data that existed at
anchor time, without publishing the data.

### 3. Open-source reproducibility (already partly in place)

Protocols, metric methodologies, and evidence grades live in the public
`registry.json` / `knowledge.json` / `docs/methodology/*`. Anyone can run the
*same* protocol and computation on *their own* local data. Reproducibility here
is about the **method**, which is already public — no proof layer needed, just
discipline to keep methods in the registry, not hardcoded.

### 4. (Future) Collective verification

Only once a real community exists: aggregate "N people ran protocol X; the
pooled effect direction was +/−/null", computed so that no individual's data or
identity is exposed (counts and signs, not series). This is the *only* layer
that might eventually justify a shared ledger — and even then, privacy-preserving
aggregation (see RFC-002's contribution model) may suffice without one.

### What goes where (the privacy contract)

| Goes onto an external anchor | Never leaves the machine |
|---|---|
| `SHA-256` commitments of pre-registration records | Hypotheses, plaintext records |
| Merkle **roots** of data snapshots | `data.local.json`, day-records, raw values |
| Timestamps of the above | Identities, device IDs, tokens |

Hard rule (matches the project's safety policy): **no personal health data, no
raw values, no identifiers, ever go on-chain or to any external service.** Only
opaque, hiding commitments do. The proof layer is **opt-in and off by default**;
with it off, behaviour is unchanged.

### MVP (cheapest thing that works)

Start with the **smallest** mechanism that delivers proof-of-priority and
tamper-evidence for a single user:

1. **Local signed append-only log.** Each commitment/root is appended with a
   monotonically increasing index and signed by a local key. This alone gives
   tamper-evidence *relative to the user's key* and an ordered history — zero
   external dependency, zero cost.
2. **OpenTimestamps anchor.** Submit the same commitments to OpenTimestamps,
   which batches them into a single Bitcoin transaction (free to the user, no
   wallet, no token). This upgrades "the user says so" to "the Bitcoin chain
   witnessed this digest existed before time T" — trustless priority, without
   running any chain ourselves.

No new coin, no smart contract, no per-step blockchain write. The append-only
log + periodic OpenTimestamps anchor covers the entire individual use case.

### Criterion: when (if ever) a full blockchain is justified

Adopt a heavier ledger **only** when *all* of these hold — otherwise the
MVP is strictly better:

- A real multi-user community exists and wants to *compare* results trustlessly.
- Participants do **not** trust a single coordinator to hold the append-only log
  honestly (i.e. a federated/signed log is insufficient).
- Aggregation must be verifiable by parties who ran no part of it.

Even then, prefer an existing public anchor (Bitcoin/Ethereum via timestamping)
over a bespoke chain, and prefer privacy-preserving aggregation over on-chain
data. **Never** introduce a token.

## Alternatives Considered

### Alternative A: Run our own blockchain / token
- A dedicated chain or token to record every experiment step.
- **Rejected:** massive operational and trust overhead, "blockchain for its own
  sake", and tokens create perverse incentives. Nothing here needs a new chain;
  a public timestamp anchor gives the same priority guarantee for free.

### Alternative B: Put data (encrypted) on-chain / IPFS
- Store encrypted snapshots off-machine for "durability + proof".
- **Rejected:** violates the first principle (data leaving the machine, even
  encrypted, is a standing decryption/leak risk and re-identification surface).
  Hashes + Merkle roots prove integrity without ever externalizing data.

### Alternative C: Self-hosted timestamp server only
- A single server we run that signs "this hash existed at time T".
- **Rejected as the trust root** (kept only as the local signed log): a server we
  control can backdate. Fine as a convenience log, not as proof to a skeptic —
  hence OpenTimestamps on top for a trustless anchor.

### Alternative D: Zero-knowledge proofs of "criteria met"
- Prove "B−A ≥ SWC" without revealing the series, via a ZK circuit.
- **Deferred, not rejected:** elegant for collective verification, but heavy to
  build and unnecessary for the individual MVP. Revisit at Layer 4 if a community
  forms.

## Impact

- **Breaking changes:** no. Entirely additive and opt-in; default off → behaviour
  unchanged. No effect on either skin, the registry render path, or sync.
- **Affected areas (future, when implemented):** a new `proof/` module (commitment
  + Merkle + OTS client), an opt-in toggle in settings, optional provenance UI
  ("pre-registered ✓ · anchored <date>"). The derived calendar and source
  calendars are untouched. `data.local.json` stays git-ignored and local.
- **Migration effort:** low. Nothing to migrate; first implementation is a
  greenfield opt-in feature gated behind a setting.

## Open Questions

- [ ] Canonical JSON form for hashing (key ordering, number/Unicode normalization) — pin one to make digests reproducible across machines.
- [ ] Anchor cadence for Merkle roots: nightly vs per-protocol-block — cost vs granularity of the tamper-evidence guarantee.
- [ ] Where the local signing key lives and how it is backed up without becoming a sensitive-file leak (ties into the L3 vault policy).
- [ ] Should pre-registration optionally publish *only the commitment* to a public OpenHealth index (still zero data) so priority is discoverable, or stay fully private until the user reveals?
- [ ] Minimum community size/threshold before Layer 4 (collective verification) is worth building at all.

---

*To submit this RFC, copy it to `rfcs/NNN-your-title.md` and open a PR. Discussion happens on the PR.*
