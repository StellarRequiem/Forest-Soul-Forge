# Response to SarahR1 (Irisviel) — comparative review of FSF

**Date:** 2026-05-01
**Reviewer:** [SarahR1 (Irisviel)](https://github.com/SarahR1) — Nexus / Irkalla project ([nexus-portfolio](https://github.com/SarahR1/nexus-portfolio))
**Review date:** 2026-04-30
**Repo state at response time:** `255b894` (v0.1.1 — audit + hardening release, tagged 2026-04-30)
**Author:** Alex (orchestrator) + harness (drafting)

This doc is the response to SarahR1's comparative review of Forest
Soul Forge vs. her Nexus / Irkalla project. It is intentionally kept
in the audit trail so future contributors can see (a) what the
review said, (b) what we adopted, (c) what we declined and why, and
(d) what we asked back.

The response itself follows. The disk-verified citations cross-
reference the actual repo at `255b894`.

---

## Sarah,

Thanks for the review. It's longer + more substantive than most
external reads, and the parts that landed *did* land. Three things
got absorbed directly into the codebase. A few were stale because of
where you saw the snapshot. A few I'm pushing back on. Below is the
honest read.

### 1. Snapshot freshness — please refresh from `255b894`

A couple of the load-bearing claims in your analysis appear to be
running against a state that's roughly 2-3 days behind. v0.1.1 just
landed (2026-04-30, tag `v0.1.1`, commit `255b894`). Specifically:

- **"Skill-manifest dict/list bug blocks cross-agent chains."** Fixed
  2026-04-28 in commit `04c0d27`. `compile_arg` in
  `src/forest_soul_forge/forge/skill_expression.py:568` is a
  type-dispatched recursive compiler that preserves dict / list YAML
  structure end-to-end. `delegate.v1` runs cleanly.

- **"They only claim one integration test."** Five integration cases
  now (`tests/integration/test_cross_subsystem.py` × 3,
  `tests/integration/test_full_forge_loop.py` × 2), plus
  `live-test-y-full.command` end-to-end smoke covering Y1–Y7, plus
  `live-triune-file-adr-0034.command` covering the SW-track triune.
  Total unit suite is at 1439 passing as of v0.1.1, up from 992 at
  v0.1.0.

- **"FSF currently more like a constitutional agent factory than a
  lived-continuity system."** Was true at v0.1.0. ADR-003Y
  (Conversation Runtime) is now Accepted; Y1–Y7 are all shipped.
  `body_hash` survives Y7 lazy summarization for tamper-evidence.
  The `conversations` + `participants` + `turns` tables in schema
  v10 are the lived-continuity surface, alongside the existing
  episodic / semantic / procedural memory layers.

- **"Memory has scopes but no epistemic verification."** Half-stale.
  `memory_verify.v1` exists (ADR-003X K1 — explicitly the "Iron Gate
  equivalent" per the source comment at
  `src/forest_soul_forge/tools/builtin/memory_verify.py:1`). It's
  one bit, though, which is your *deeper* point — and that point is
  valid (see §2).

If your next pass can hit the v0.1.1 README + STATE.md + CHANGELOG,
the snapshot will be aligned.

### 2. Where you were sharp — what I absorbed

Three of your gaps are real. They're now filed as ADRs (Proposed
status; awaiting promotion):

- **[ADR-0038 — Companion harm model](docs/decisions/ADR-0038-companion-harm-model.md).**
  Your eight-harm list (sycophancy, false sentience, dependency
  loops, intimacy drift, privacy-via-helpfulness, memory overreach,
  burnout, self-improvement narrative) is now FSF's load-bearing
  taxonomy for Companion-tier safety. Each harm got a mitigation
  surface mapped to existing or new structure. Notable additions
  unique to FSF: `min_trait_floors` as a genre-level mechanic
  symmetric to `max_side_effects`, and per-harm landing in specific
  ADRs (ADR-0017 voice renderer for H-2; ADR-0027 amendment for
  H-6; etc.).

- **[ADR-0027 amendment — epistemic metadata on memory entries](docs/decisions/ADR-0027-amendment-epistemic-metadata.md).**
  Your "audit chain proves 'this happened' but not 'this belief is
  true'" framing is the catalyst quote. The amendment adds
  `claim_type` (six-class enum: observation / user_statement /
  agent_inference / preference / promise / external_fact),
  three-state `confidence`, a separate `memory_contradictions`
  table, and `last_challenged_at` for staleness pressure. K1 stays
  in force; verification combines with confidence at read time.
  Schema bumps v10 → v11, additive only.

- **[ADR-0021 amendment — initiative ladder](docs/decisions/ADR-0021-amendment-initiative-ladder.md).**
  Your L0–L5 ladder is adopted as `max_initiative_level` on
  genres, orthogonal to the existing `max_side_effects`. Companion
  caps at L2 (suggestion); SW-track Engineer earns L4 (reversible
  side-effects with policy); Actuator stays L5 (destructive with
  friction). Dispatcher gains an `InitiativeFloorStep` in the R3
  governance pipeline.

All three ADRs explicitly attribute the catalyst to your review,
with quoted source language. See `CREDITS.md` for the full
attribution discipline. Status is Proposed until orchestrator
promotion; if you want to comment before promotion, the doors are
open.

### 3. Where I'm pushing back

- **"Embodied / interoceptive state — energy_budget, attention_load,
  etc."** Declining. Documented in ADR-0038 §4 with reasoning: it
  adds attack surface (operator-manipulable runtime state) and
  risks conflating computational state with felt state — which is
  exactly the H-2 (false sentience claims) harm your own review
  flags. Will revisit only with a concrete safety win that's
  unattainable any other way.

- **"Soul as generated artifact needs to evolve into self as
  maintained pattern."** This is a misread of the architecture.
  Content-addressed `soul.md` is intentional design — DNA
  derivation is one of the load-bearing invariants we don't break
  without a major version bump and migration plan (ADR-0001). The
  lived-continuity layer IS the memory + chain + Y-track
  conversation runtime; the soul is birth-time identity. What's
  missing is *layered identity* — birth-time constitution stays
  immutable, runtime self-model proposals land as a separate
  artifact gated through operator approval. That's queued for v0.3
  (depends on ADR-0035 Persona Forge landing first).

- **The "advice you can give them" framing.** A small thing. Your
  review reads like advice *to* an external project from the
  outside; it lands well when delivered as peer review. If your
  position is collaborator-curious rather than competitive
  comparison, calibrating that explicitly will save misunderstanding.
  Worth clarifying — see §4.

### 4. Asking back

Three questions:

1. **Is your interest in FSF specifically (peer review of an
   adjacent project) or in the *space* you both occupy
   (companion-tier local-first agents)?** The honest answer
   shapes whether this is a one-time review or the start of a
   working channel.

2. **Nexus / Irkalla — would you accept a similar peer review in
   the other direction?** I (Alex) have been operating FSF as
   orchestrator + my LLM dev arm; I've not yet read Nexus
   directly. The public surface is `nexus-portfolio`, but the
   architecture you describe (Corpus Callosum routing, Oneiroi
   consolidation, Plutchik-affect mapping, SigLIP2 vision) is
   private. If a directional knowledge-share is something you'd
   want, I'd be open. If not, that's also a clean answer.

3. **The MemoryNode + Iron Gate framing in your review — is that
   a published spec somewhere, or is it documented privately?**
   The ADR-0027 amendment adopts the prior-art framing; it would
   be helpful to cite a public source if one exists.

Whatever the answer is, thank you for the review. The three ADRs
above are stronger for it. A copy of this response is saved in the
repo at `docs/audits/2026-05-01-sarahr1-review-response.md` for the
record.

— Alex
