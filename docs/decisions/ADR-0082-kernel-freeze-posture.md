# ADR-0082 — Kernel Freeze Posture

**Status:** Accepted (2026-05-19, B424)
**Date:** 2026-05-19
**Tracks:** Architecture / Governance / Project discipline
**Supersedes:** none (extends ADR-0044, ADR-0040)
**Builds on:** ADR-0044 (kernel/userspace boundary), ADR-0040
(trust-surface decomposition rule), ADR-0067 (cross-domain
orchestrator — defines domain as userspace ON kernel)
**Unblocks:** the explicit promise external integrators need before
they commit to building against Forest's surfaces

## Context

Two independent external assessments in May 2026 converged on the
same critique:

1. **Sonnet 4.6 pinned-thread review (2026-05-17):** "The real
   question isn't your level. It's whether you have the discipline
   to slow down and close those gaps before the codebase complexity
   outpaces your ability to hold it in your head solo."
2. **ChatGPT external assessment (2026-05-19):** "Currently
   resembles an experimental research sandbox more than a coherent
   'agentic kernel.' The risk isn't 'fake.' The risk is
   uncontrolled architectural ambition."

Two independent assessors hitting the same point is signal, not
noise. The project has:

- Real engineering effort: 575 commits, 87,188 LoC, 78 ADRs,
  214 test files across unit/integration/conformance, 19,211 audit
  chain entries
- Genuine governance discipline: append-only audit chain with
  hash-linked tamper-evidence, content-addressed constitution hash,
  per-agent posture system, per-tool plugin grants, trust-surface
  decomposition rule (ADR-0040)
- A documented kernel/userspace boundary (ADR-0044 P1.1) that
  classifies every directory as kernel or userspace

What it lacks: a **forcing function** preventing creep back into
the kernel. ADR-0044 says "default to userspace; promotion to
kernel happens when external integrator demands it." That rule is
aspirational. There is no mechanism enforcing it.

Phase α (10/10 substrate ADRs closed across 2026-05-12 to
2026-05-15) was the substantive kernel buildout. ADR-0050
encryption-at-rest, ADR-0067 cross-domain orchestrator, ADR-0068
operator profile, ADR-0070 voice I/O, ADR-0071 plugin author kit,
ADR-0072 behavior provenance, ADR-0073 audit chain segmentation,
ADR-0074 memory consolidation, ADR-0075 scheduler scale, ADR-0076
vector index. With those closed, the kernel surface has the shape
it needs to support the ten-domain rollout.

The next risk-mode is: ten domains rolling out, each tempting
new substrate additions ("we just need one more kernel primitive
to make D5 work"). Without an explicit freeze, every domain
rollout becomes a kernel-extension opportunity, and the kernel
keeps growing forever. That is the "uncontrolled architectural
ambition" failure mode the assessments warned about.

## Decision

**The kernel surface is functionally frozen as of Phase α close.**
Domain rollouts continue. Userspace experimentation continues.
Plugin/tool/skill authoring continues. The seven ABI surfaces
(KERNEL.md §"The seven kernel ABI surfaces") remain the
commitment-to-be. But the kernel itself stops growing.

This is the "make it a feature" move: the discipline that
constrains ambition becomes the project's explicit, visible
posture. ChatGPT's critique converts from "uncontrolled ambition"
to "bounded ambition with an explicit freeze line."

### Frozen abstractions (extends KERNEL.md §"The seven kernel ABI surfaces")

The following derivations and invariants are committed to in
addition to the seven ABI surfaces. Changes to any require a major
version bump and a deliberate ABI bump signal in release notes:

1. **Audit chain canonical form** — `{seq, timestamp, agent_dna,
   event_type, event_data, prev_hash, entry_hash}`. `entry_hash =
   SHA-256(prev_hash || canonical_json(event_minus_timestamp))`.
   Genesis `prev_hash = "GENESIS"` (literal). Per memory
   [[project_audit_chain_canonical_form]].
2. **Constitution hash derivation** — content hash over
   `policies + thresholds + scope + duties + drift + tools +
   genre`. Triune block and posture/status are excluded from the
   hash (they're mutable). Per ADR-0007.
3. **DNA derivation** — `dna_short(profile)` and
   `dna_full(profile)` from `core/dna.py`. Determined entirely by
   canonical trait profile (role + trait values + domain weights).
   Same trait profile → same DNA, forever.
4. **`instance_id` derivation** — `f"{role}_{dna_short}"` for the
   first instance; `f"{role}_{dna_short}_{sibling_index}"` for
   subsequent siblings. `agents.instance_id` is the SQLite
   PRIMARY KEY; reused instance_ids are forbidden by the schema.
5. **Schema migration discipline** — strictly additive forward
   migrations only. Dropping columns, tightening constraints,
   renaming forbidden. Escape hatch: `rebuild_from_artifacts`.
6. **Singleton-per-forest roles** — `reality_anchor`,
   `domain_orchestrator`, `wiring_sentinel` (per
   `_SINGLETON_ROLES` in `writes/birth.py`). The list itself is
   part of the freeze; adding a singleton role is a kernel change.
7. **Side-effect classification** — `read_only / network /
   filesystem / external`. Adding a fifth class is a kernel
   change requiring an ABI signal.

### Domain rollouts vs. kernel additions

ADR-0067 (cross-domain orchestrator) defines a domain as a set of
roles, archetype kits, and handoff routes mapped over the existing
kernel surface. A domain rollout adds:

- New role entries in `trait_tree.yaml`
- New archetype kits in `tool_catalog.yaml`
- New genre claims (over existing genres) in `genres.yaml`
- New skill manifests in `examples/skills/`
- New constitution templates in `constitution_templates.yaml`
- New handoff routes in `handoffs.yaml`
- New birth scripts in `dev-tools/`

**None of those are kernel additions.** They are configuration
and userspace artifacts that compose against the frozen kernel
surface. D4 Code Review (ADR-0077) is the canonical worked
example: 10-burst rollout (B331-B340) added three roles, three
skills, three birth scripts. Zero kernel-side changes.

What WOULD be a kernel addition:

- A new tool side-effect class (e.g., adding `audio` between
  `network` and `filesystem`)
- A new audit chain event-type schema family (vs. extending an
  existing one with optional fields)
- A new singleton-per-forest role
- A new constitution body field that participates in
  `constitution_hash`
- A new top-level subsystem under `src/forest_soul_forge/`
- A new top-level HTTP API route family (vs. adding endpoints
  under existing families)
- A schema migration that isn't strictly additive
- Changing one of the seven ABI surfaces or seven frozen
  abstractions above

If the work proposed falls into the second list, it requires an
ADR explicitly arguing for the kernel addition. The ADR must
identify the **external demand** that justifies the addition (see
"Exception path" below). The ADR must also identify the major-
version-bump implications.

### Exception path: when the kernel CAN grow

The freeze is not permanent. Three triggers unfreeze a specific
kernel addition:

1. **External integrator demand.** Per ADR-0044 Decision 4 + Phase
   6, v1.0 is gated on external integrator validation. When an
   integrator builds against the kernel and discovers a missing
   primitive that cannot be expressed in userspace, that's a
   first-class kernel-extension demand. The ADR proposing the
   addition cites the integrator's specific need.
2. **Operator-level safety requirement.** When a new defensive
   primitive is genuinely required at the kernel level (e.g.,
   ADR-0050 encryption-at-rest in Phase α was a safety-driven
   kernel addition), the ADR cites the threat model and explains
   why userspace can't deliver the safety property.
3. **Architectural bug discovery.** When implementing a feature
   surfaces a structural issue with an existing kernel surface
   (e.g., B416 + B420 surfaced that DNA-instance_id coupling
   prevents same-trait-profile rebirths from picking up new
   template defaults — a real architectural finding that would
   justify a kernel-side mechanism for in-place constitution
   refresh). The ADR documents the finding and proposes the
   minimum kernel change to resolve it.

A demand that doesn't fit those three triggers does not unfreeze
the kernel. "I want to add this because it would be cool" is not
a trigger. "D5 would be easier if we had X kernel feature" is not
a trigger if D5 can also be built in userspace, however
inelegantly.

### Enforcement

This ADR is enforced by three mechanisms:

1. **ADR-0040 trust-surface decomposition** already requires that
   new kernel files declare their trust surface. Kernel additions
   that don't fit an existing surface require explicit decomposition.
2. **`dev-tools/check-drift.sh`** (the drift sentinel) will gain
   a kernel-LoC budget check: total LoC under
   `src/forest_soul_forge/` should not grow more than 10% per
   month outside of explicit kernel-extension ADRs. Crossing the
   budget without a citing ADR flags the next drift run RED.
   (Implementation: deferred to a future burst; ADR-0082 ships
   the rule, the sentinel update is a follow-on.)
3. **ADR-0044 P6 outreach materials** (B131) are the
   integrator-recruitment vehicle. When integrators arrive, their
   demands become the unfreeze triggers. Until then, the freeze
   holds.

## Consequences

**Positive:**

- The "uncontrolled ambition" critique from external assessments
  becomes "bounded ambition with an explicit freeze line." Same
  code, sharper story, real discipline.
- Domain rollouts get to use the kernel as it is, not as it might
  become. This forces creative composition over substrate
  expansion — which is the kernel's actual test.
- v1.0 becomes describable: "v1.0 is the kernel surface we froze
  at Phase α + the additions that external integrator demand
  drove through individual ADRs."
- The pitch to external integrators sharpens: "Here is the kernel.
  These seven ABI surfaces are committed. The frozen abstractions
  list is what we'll never change without a major version bump.
  If you find something missing, that's an ADR and we'll prioritize
  it. Otherwise, build in userspace."

**Negative:**

- Some genuinely useful kernel-side ideas will be deferred or
  rejected. The freeze is deliberately stricter than necessary,
  because under-strictness is the failure mode the assessments
  flagged.
- Domain rollouts may surface awkward userspace workarounds for
  cases where a kernel addition would be cleaner. This is the cost
  of discipline; document the workarounds as evidence for future
  unfreeze ADRs.

**Mitigations:**

- The exception path is documented and explicit. The freeze is not
  a prohibition; it is a high bar.
- Phase α delivered a substantial kernel surface. Operations
  against it should be possible for most domain rollouts.

## Open questions

- **Q1: What constitutes "external integrator validation" for v1.0
  unfreezing?** ADR-0044 Decision 4 is light on specifics. Future
  ADR work should define minimum criteria (e.g., at least one
  third-party plugin published; at least one third-party
  distribution built; at least one conformance-suite-passing
  external integration).
- **Q2: Should the frozen abstractions list itself be versioned?**
  This ADR commits to the list as-of-now. If a future ADR amends
  the list (e.g., adds an item), the amendment should be a
  visible ABI signal. Open whether the list itself gets a version
  number.
- **Q3: Does this freeze apply to the SoulUX flagship
  distribution?** No — userspace under SoulUX is free to evolve
  per ADR-0044's existing rule. The freeze is kernel-only.
  `apps/desktop/`, `frontend/`, `dist/`, repo-root `*.command`
  scripts are all untouched by this ADR.

## References

- ADR-0044 — Kernel Positioning + SoulUX Flagship Branding
  (Decision 4 + Phase 6 are the v1.0 unfreeze trigger this ADR
  formalizes)
- ADR-0040 — Trust-Surface Decomposition Rule (the file-grained
  governance mechanism that makes kernel additions structurally
  visible)
- ADR-0067 — Cross-Domain Orchestrator (defines domain as
  userspace ON kernel — the load-bearing distinction this ADR
  preserves)
- KERNEL.md — The seven ABI surfaces (this ADR's frozen-
  abstractions list extends that doc with seven additional
  invariants)
- `docs/architecture/kernel-userspace-boundary.md` — directory-
  level boundary map
- `STATE.md` — current Phase α + domain rollout status snapshot
- Memory [[project_2026_05_15_phase_alpha_status]] — Phase α
  10/10 close record
- Memory [[project_d4_rollout_pattern]] — D4 canonical 10-burst
  rollout pattern (no kernel-side changes)
