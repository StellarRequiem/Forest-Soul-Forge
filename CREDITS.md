# CREDITS

External contributors whose work shaped Forest Soul Forge. Sourced
from review threads, comparative analyses, and direct contributions.
Internal commit authorship is captured in `git log`; this file
captures the *source of an idea* when it came from outside the
direct development line.

## Attribution discipline

- **One entry per external contributor**, regardless of how many
  ADRs / docs / decisions they shaped.
- **Cite the specific decisions or ADRs their work informed**, with
  links to the affected docs.
- **Quote the proximate language** when an ADR explicitly adopts a
  framing — keep attribution traceable, not just symbolic.
- **Note where their suggestion was declined**, not just where it
  was adopted. Declines are part of the record.

This file is updated when external work lands in the codebase. It is
not a thank-you list. It exists so that a future reader of any ADR
or doc can see where the load-bearing ideas came from.

## Contributors

### SarahR1 (Irisviel)

- **GitHub:** https://github.com/SarahR1
- **Public project surface:** [nexus-portfolio](https://github.com/SarahR1/nexus-portfolio) — public landing for her Nexus / Irkalla project.
- **Project context:** Nexus / Irkalla — a distributed local AI assistant platform spanning orchestration, inference, memory, telemetry, voice, and monitoring. Adjacent project space to FSF; different center of gravity (persistent cognitive substrate around one primary relational identity, vs. FSF's local-first agent foundry).

#### Contribution: comparative review of FSF (2026-04-30)

A multi-section comparative analysis of FSF's architecture vs. her
Nexus / Irkalla project. Contributed via direct review (not a PR or
issue). The review identified gaps in FSF's companion-tier safety
posture, memory epistemics, and autonomy modeling, and proposed
specific shapes for closing them.

The review's snapshot was pre-v0.1.0 in places (predated the
2026-04-28 skill-engine dict/list fix in commit `04c0d27` and the
Y-track conversation runtime in ADR-003Y). Stale-claim corrections
were communicated separately; the gap analysis below stands on its
own and was absorbed.

##### Adopted into the codebase

| ADR / doc | Adoption shape |
|---|---|
| [ADR-0038 — Companion harm model](docs/decisions/ADR-0038-companion-harm-model.md) | The eight-harm taxonomy (H-1 sycophancy through H-8 self-improvement narrative inflation) is adapted from her review's harm list. The §0 reasoning, the per-harm mitigation surface mapping, the `min_trait_floors` mechanic, and the FSF-specific cross-references are this ADR's own work. **Status: Accepted (v0.1.2). T1+T2+T3 shipped.** |
| [ADR-0027 amendment — epistemic metadata](docs/decisions/ADR-0027-amendment-epistemic-metadata.md) | The framing "FSF's audit chain proves 'this happened' better than it proves 'this belief is true' — a companion with durable memory needs not only memory privacy, but also memory humility" is adopted verbatim as the amendment's catalyst. The MemoryNode schema reference + Iron Gate prior art (from her project) inform the column shape; the FSF-specific three-state confidence, separate `memory_contradictions` table, K1 fold, and schema-bump migration plan are this amendment's work. **Status: Accepted (v0.1.2). T1+T2+T3+T4 shipped.** |
| [ADR-0021 amendment — initiative ladder](docs/decisions/ADR-0021-amendment-initiative-ladder.md) | The L0–L5 initiative ladder is adapted from her review's table. The mapping to FSF's seven genres, the SW-track interaction, the `InitiativeFloorStep` dispatcher addition, and the schema/constitution integration are this amendment's work. **Status: Accepted (v0.1.2). T1+T2+T3 shipped; per-tool annotations in production from Burst 46.** |
| [ADR-0035 — Persona Forge](docs/decisions/ADR-0035-persona-forge.md) | Layered identity / self-model proposals as runtime artifact. Adopted from her review's "Initial traits → observed behavior → reflected preferences → user-confirmed continuity anchors → contradiction/drift review → revised self-model proposal" framing. FSF-specific work: constitution-immutability constraint preserved, operator-gate enforced, drift-detection inputs (claim_type, contradictions, conversation telemetry), H-1/H-8 floor enforcement on the proposal path. **Status: Proposed (v0.3 candidate).** |
| [ADR-0036 — Verifier Loop](docs/decisions/ADR-0036-verifier-loop.md) | Auto-detected memory contradictions via dedicated Guardian-genre Verifier agent. Builds on ADR-0027-am's `memory_contradictions` table + `memory_challenge.v1` patterns. The MemoryNode + Iron Gate prior art she cited is the structural inspiration; FSF-specific work: Verifier as a first-class agent (not daemon-side cron), intra-agent scope at v0.3 with cross-agent deferred, LLM-classification at high confidence threshold, operator review surface for false-positive handling. **Status: Proposed (v0.3 candidate).** |
| [ADR-0037 — Observability dashboard](docs/decisions/ADR-0037-observability-dashboard.md) | Operator-facing telemetry dashboard surfacing ADR-0038's H-3 / H-4 / H-7 mitigations + ADR-0035 proposal queue + ADR-0036 Verifier track record. Not directly proposed in her review but operationally required to deliver the H-3 manipulation-vector mitigation she emphasized ("operator-visible signal the agent cannot read"). Companion-safety + memory-health + persona-drift sub-views, strictly read-only from agent perspective. **Status: Proposed (v0.3 candidate).** |

##### Declined from her review (with reasoning)

| Suggestion | Decline reason |
|---|---|
| Embodied / interoceptive state (`energy_budget`, `attention_load`, `uncertainty_pressure`, etc.) | Documented in ADR-0038 §4 as out-of-scope. Adds attack surface (operator-manipulable runtime state) and risks conflating computational state with felt state — exactly the H-2 (false sentience claims) harm her own review flags. Defer to post-v0.3, conditional on a concrete safety win. |
| "Soul as generated artifact needs to evolve into self as maintained pattern" | Documented as a misread in the review-response thread. Content-addressed `soul.md` is intentional design (DNA derivation is a load-bearing invariant per ADR-0001). The lived-continuity layer is the memory + chain + Y-track conversation runtime, not the soul artifact. The architecture is right; what's missing is layered identity (deferred to ADR-0035 Persona Forge, v0.3 queued). |
| "Fix the dict/list skill-manifest bug first" | Stale claim — already fixed 2026-04-28 in commit `04c0d27` via `compile_arg` type-dispatched compiler in `src/forest_soul_forge/forge/skill_expression.py:568`. |
| "They only claim one integration test" | Stale claim — repo at `255b894` (v0.1.1) has 5 integration cases across 2 files (`tests/integration/test_cross_subsystem.py`, `tests/integration/test_full_forge_loop.py`) plus `live-test-y-full.command` end-to-end smoke and `live-triune-file-adr-0034.command`, on top of 1439 unit tests. |

## House rules for adding entries

1. **Verify before crediting.** Don't credit secondhand. The contributor's
   idea must be traceable to a specific source (review doc, message,
   PR, issue link).
2. **Cite the proximate language.** When an ADR adopts a phrase or
   structure, quote it. Keep the trail visible.
3. **Document declines.** A declined suggestion is part of the record;
   silent omission misrepresents the review.
4. **Update on landing.** When an ADR an external contributor influenced
   moves from Proposed to Accepted, the entry here gets a status update.
