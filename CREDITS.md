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
| [ADR-0038 — Companion harm model](docs/decisions/ADR-0038-companion-harm-model.md) | The eight-harm taxonomy (H-1 sycophancy through H-8 self-improvement narrative inflation) is adapted from her review's harm list. The §0 reasoning, the per-harm mitigation surface mapping, the `min_trait_floors` mechanic, and the FSF-specific cross-references are this ADR's own work. |
| [ADR-0027 amendment — epistemic metadata](docs/decisions/ADR-0027-amendment-epistemic-metadata.md) | The framing "FSF's audit chain proves 'this happened' better than it proves 'this belief is true' — a companion with durable memory needs not only memory privacy, but also memory humility" is adopted verbatim as the amendment's catalyst. The MemoryNode schema reference + Iron Gate prior art (from her project) inform the column shape; the FSF-specific three-state confidence, separate `memory_contradictions` table, K1 fold, and schema-bump migration plan are this amendment's work. |
| [ADR-0021 amendment — initiative ladder](docs/decisions/ADR-0021-amendment-initiative-ladder.md) | The L0–L5 initiative ladder is adapted from her review's table. The mapping to FSF's seven genres, the SW-track interaction, the `InitiativeFloorStep` dispatcher addition, and the schema/constitution integration are this amendment's work. |

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
