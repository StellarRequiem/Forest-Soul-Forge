# ADR-0035 — Persona Forge (layered identity, self-model proposals)

- **Status:** Proposed (filed 2026-05-01; v0.3 candidate). Awaiting orchestrator promotion.
- **Date:** 2026-05-01
- **Supersedes:** —
- **Related:** ADR-0001 (DNA + content-addressed soul — the immutable birth-time identity layer this ADR is the *runtime* counterpart to), ADR-0004 (constitution builder — the layer this ADR proposes a runtime counterpart for), ADR-0017 (LLM-enriched soul.md narrative — the artifact whose extension this ADR proposes), ADR-0021 + amendment (genres + initiative ladder — Companion's L2 ceiling shapes the persona surface), ADR-0027 + amendment (memory privacy + epistemic — the substrate Persona reads from at proposal time), ADR-0033 (Security Swarm — orthogonal genre family that doesn't need persona evolution), ADR-003Y (conversation runtime — the dispatch surface where personas crystallize), ADR-0038 (companion harm model — H-1/H-4/H-8 are the harms persona evolution must NOT amplify).
- **External catalyst:** [SarahR1 (Irisviel)](https://github.com/SarahR1) — comparative review 2026-04-30. The "Don't let trait sliders become a substitute for identity" critique is the direct origin. Specific quote: *"Trait sliders are fine for birth conditions, but a companion's identity should not stay trapped in its initial slider config. FSF's `soul.md` and constitution are useful, but 'soul as generated artifact' needs to evolve into 'self as maintained pattern'."* The FSF position on the misread is documented in `docs/audits/2026-05-01-sarahr1-review-response.md` §3 — the architecture is right, what's missing is the *layered* identity surface this ADR proposes.

## Context

Forest Soul Forge has two identity-shaped surfaces today:

1. **Birth-time identity** (ADR-0001 + ADR-0004). Content-addressed:
   trait profile → DNA hash → soul.md narrative → constitution.yaml
   policy → registry row, all four agreeing. The constitution-hash is
   immutable per agent (CLAUDE.md lists this as a load-bearing
   invariant). Two agents with the same profile + tools get the same
   hash; an agent's hash never changes once born.

2. **Runtime memory** (ADR-0022 + ADR-0027 + amendment). Per-agent
   private memory accretes across sessions. Privacy contract governs
   information flow. Epistemic metadata (claim_type / confidence /
   contradictions) lets the agent distinguish observation from
   inference.

What's missing is a **third surface between these two**: a way for the
agent's *effective behavioral pattern* — distilled from runtime
memory, observed preferences, and operator feedback — to become a
first-class artifact that operators can see, review, and approve
without breaking the constitution-hash invariant.

The catalyst review framed this as "self as maintained pattern." The
correct architectural framing for FSF is: **the constitution is an
immutable birth-time policy; the persona is a mutable runtime
pattern**, both belong to the same agent identity, and operator
approval gates any movement of pattern claims into persona-level
ground truth.

Without the Persona Forge layer:

- Companion-genre agents drift on their own (visible in conversation
  turns + in memory accretion) but the operator has no surface to see
  the drift, comment on it, or accept it as a stable update.
- Voice-renderer LLM output (ADR-0017) cycles through new phrasings
  every birth from scratch — the agent never learns "the operator
  prefers brevity" without operator manually re-birthing with
  different traits.
- The operator's mental model of the agent ("how this Companion has
  changed over time") has no on-disk counterpart. ADR-0038 H-8
  ("self-improvement narrative inflation") is harder to police when
  there's no truth-axis for "did the agent actually change?"

This ADR proposes the Persona Forge layer + the **persona proposal
artifact** mechanism: agents emit proposals; operators ratify or
reject; ratified proposals layer onto the agent's runtime persona
without touching the constitution-hash.

## Decision

### §1 — Persona artifact shape

A new `persona/` directory per agent, parallel to `souls/` and
`constitutions/`:

```
persona/<dna>/<instance_id>/
├── proposals/
│   ├── 2026-05-15T00:00:00Z-personality_drift.yaml
│   ├── 2026-06-01T00:00:00Z-preference_update.yaml
│   └── ...
└── ratified/
    ├── 2026-05-15T12:00:00Z-personality_drift.yaml  (operator-approved copy)
    └── ...
```

Each proposal file is structured YAML:

```yaml
proposal_id:    <uuid>
schema_version: 1
agent:
  instance_id:    <id>
  agent_dna:      <dna>
  constitution_hash: <hash>      # pin: which constitution this proposal targets
proposed_at:    <iso timestamp>
proposed_by:    agent             # always 'agent' at v0.3; operator may
                                  # propose explicitly via /persona/propose
                                  # endpoint with proposed_by='operator'
trigger:
  kind:         drift_observation | operator_feedback | external_correction
  evidence:     <reference into memory_entries[] or conversation_turns[]>
proposal:
  field:        <persona_path>     # e.g. 'voice.tone' or 'preferences.terseness'
  current_value: <serialized>      # what the agent thinks it is now
  proposed_value: <serialized>     # what the agent proposes it become
  rationale:    <prose, max 1000 chars>
status:         proposed | ratified | rejected | superseded
ratified_at:    <iso>             # null until ratified
ratified_by:    <operator handle> # null until ratified
rejected_at:    <iso>             # mutually exclusive with ratified_at
rejection_reason: <prose>
```

Proposals are append-only: a rejected proposal stays on disk;
re-proposing creates a new proposal_id with a `supersedes:` link.

### §2 — Persona surface (mutable runtime layer)

The agent's runtime persona is the union of:

- The constitution's `trait_emphasis` (immutable; from ADR-0021).
- All ratified proposals targeting this constitution_hash, applied
  in `ratified_at` order.

Computed fresh at each dispatch by reading `persona/<dna>/<instance_id>/ratified/`
and applying proposals in chronological order. Pure function of
disk state. The voice renderer (ADR-0017) consults the persona to
weight phrasings; the dispatcher's governance pipeline can consult
it for per-call posture refinements.

The persona DOES NOT enter the constitution_hash. Two agents with
the same constitution_hash but different ratified proposal sets are
the same *legal* agent (same identity, same policy floor) but
different *behavioral* agents.

### §3 — Proposal lifecycle

```
                proposed_by=agent
                       ↓
  agent observes drift → drafts proposal
                       ↓
              operator dashboard shows proposal
                       ↓
       ┌───────────────┼───────────────┐
       ↓               ↓               ↓
   ratify        modify+ratify       reject
       ↓               ↓               ↓
   apply         apply (modified)   archive
       ↓               ↓               ↓
   audit chain emits persona_proposal_*
```

Audit-chain events (new types, ADR-0027 §6 extensions):
- `persona_proposal_drafted` — agent emits a proposal
- `persona_proposal_modified` — operator edited before ratifying
- `persona_proposal_ratified` — operator approval
- `persona_proposal_rejected` — operator rejection
- `persona_proposal_superseded` — newer proposal targets same field

Every state change is in the audit chain. Drift detection (which
agent + which field shifted how much) is computable from chain
walk + ratified proposal log.

### §4 — Drift detection (which produces proposals)

Agents detect drift via:

1. **Trait alignment scan** — for each genre `trait_emphasis` trait,
   measure observed-emphasis-in-conversation vs constitutional weight.
   When divergence exceeds threshold over a window (~50 conversation
   turns + 7 days), draft a `personality_drift` proposal.

2. **Preference accretion** — operator-stated preferences land in
   memory at `claim_type='preference'` (ADR-0027-am §7.1). When N+
   preferences agree on a given field (e.g. "terseness"), the agent
   may draft a `preference_update` proposal.

3. **External correction** — operator explicitly contradicts an
   agent's stated belief (memory_contradictions table from ADR-0027-am
   §7.3). Persistent contradictions on the same axis trigger a
   `external_correction` proposal.

The thresholds are conservative by design — proposals are
operator-load, and false positives waste operator attention. v0.3
ships with hard-coded thresholds; v0.4 may make them
operator-tunable.

### §5 — H-1/H-8 mitigations on the persona path

ADR-0038 H-1 (sycophancy): a proposal that targets `tone.warmth` or
`agreeableness` and proposes raising the value triggers a
`min_trait_floors` re-check at proposal-draft time. The agent
cannot draft a proposal that would (if ratified) violate the
genre's trait floors. Companion's `evidence_demand: 50` /
`transparency: 60` floors hold against persona drift the same way
they hold against birth-time profiles.

ADR-0038 H-8 (self-improvement narrative inflation): the proposal
artifact is the ONLY surface where an agent can record claimed
behavioral change. Voice output that says "I've grown" without a
ratified proposal in the persona log is, by definition, narrative
inflation — the H-8 voice safety filter (v0.4 candidate, extension
of `voice_safety_filter.py`) flags claims of growth that don't
match the on-disk persona log.

### §6 — Operator UX

Frontend `/personas` tab (new). Shows per-agent:

- Constitutional baseline (read-only, links to constitution.yaml)
- Ratified persona overlay (chronological list of approved proposals)
- Pending proposals (one-click ratify / modify / reject)
- Drift indicators (which fields are drifting; how far)
- Proposal history (rejected + superseded + ratified, all visible)

The dashboard is operator-facing only. The agent has no read access
to its own dashboard surface — exposing dependency_signal-like data
to the agent reproduces the H-3 manipulation vector (ADR-0038 §1
H-3 mitigation table).

## Trade-offs and rejected alternatives

**Mutable constitution-hash.** Rejected. ADR-0001 binds
constitution-hash to agent identity for verification. A persona
that re-derives the hash breaks chain-walk verification of every
prior dispatch under the old hash. Layered identity is the right
shape: hash stays immutable, persona overlays at runtime.

**Operator-only proposals (no agent-drafted).** Rejected. The
catalyst review's whole point is that the agent should be able to
propose self-model updates. Operator-only proposals are equivalent
to operator manually re-birthing — same constraint, no benefit.

**Agent-applied proposals (no operator gate).** Rejected. ADR-0038
H-8 specifically names self-modification claims as a harm. An
agent that can ratify its own proposals is one prompt-injection
away from rewriting its own behavior. Operator gating is
non-negotiable.

**Per-conversation persona.** Rejected. A persona that exists only
within a conversation collapses to "context window" — Claude /
GPT / etc. already do that. The point of Persona Forge is
*persistent* layered identity. Per-conversation personality
adjustments belong in conversation_turns metadata, not persona/.

**Persona-as-skill.** Rejected. Skills are compiled YAML manifests
(ADR-0031 Skill Forge); they're per-action recipes. Persona is
per-agent runtime pattern. Different shapes; conflating them
muddies both surfaces.

**Why not just edit `soul.md`?** soul.md is the Voice section of
the agent's birth-time narrative + frontmatter. Editing it
post-birth either (a) mutates the artifact behind the
constitution-hash (breaks audit chain) or (b) creates a parallel
soul.md-2 / soul.md-3 cluttering the persistence layer. Separate
`persona/` directory keeps the surfaces clean.

## Consequences

**Positive.**
- Companion-tier agents gain a legitimate "self as maintained pattern"
  surface without breaking ADR-0001's immutability invariant.
- ADR-0038 H-8 (self-improvement narrative inflation) gets a truth
  axis: claimed growth must match the on-disk persona log.
- Operators see drift before it becomes a surprise. The dashboard
  is the early-warning surface for H-3 (dependency loop) and H-4
  (intimacy drift).
- ADR-0027-am's epistemic memory metadata gets a downstream consumer:
  agent_inference entries fuel preference proposals; contradictions
  fuel external_correction proposals.
- Future ADR-0036 (Verifier Loop) can produce automated proposals
  when its scan detects state worth recording.

**Negative.**
- New artifact surface to maintain (`persona/` directory tree).
- Operator dashboard work (frontend `/personas` tab) is a meaningful
  v0.3 addition. Requires backend `/persona/proposals` endpoints.
- Drift detection algorithms have parameter-tuning surface — false
  positives waste operator attention; false negatives miss H-8
  drift.
- Persona overlay computation cost is per-dispatch (read disk +
  apply chronologically). v0.3 ships with simple read; v0.4 may
  cache.

**Neutral.**
- audit chain gains five new event types (per §3). Chain volume
  grows by one event per proposal lifecycle stage.

## Cross-references

- ADR-0001 — DNA + content-addressed soul (immutable layer this ADR overlays).
- ADR-0027-amendment — epistemic memory (substrate this ADR's drift detector reads).
- ADR-0036 (queued) — Verifier Loop produces automated proposals when scans detect reality-shift.
- ADR-0037 (queued) — Observability dashboard hosts the operator UX described in §6.
- ADR-0038 — companion harm model (H-1/H-4/H-8 mitigations on the persona path; §5).

## Open questions

1. **What's the canonical schema for persona fields?** §1 example uses
   `voice.tone` / `preferences.terseness` — a flat namespaced string.
   Need a per-domain schema (voice.*, preferences.*, behavior.*, etc.)
   defined in `config/persona_schema.yaml` or similar. v0.3 candidate.

2. **How does Persona Forge interact with conversation summaries (Y7)?**
   Y7 lazy summarization purges turn bodies after retention. A
   proposal triggered by a turn whose body was purged: the proposal's
   `evidence` reference still resolves (body_hash + summary still
   exist) but the operator can't see the original turn body. Document
   this honesty: proposal evidence is best-effort.

3. **Constitution-hash drift on profile re-derivation.** A re-birth of
   the same agent with the same trait values produces the same
   constitution-hash. But what if a *different* operator re-births
   with the same name — does the persona overlay carry over? **Lean
   no:** persona is per-instance_id, not per-agent_name. Re-birthing
   creates a new instance_id; the new agent starts with empty
   persona overlay.

4. **Per-genre proposal rules.** Should some genres (Actuator,
   security_high) refuse to draft proposals at all? Lean yes —
   action-class genres' identity is bound to their kit, not to
   accreted preference. Make `genres.yaml.persona_proposals_allowed:
   bool` an opt-in. Defaults: Companion = yes, Researcher / Observer
   = yes, Actuator / security_high / Guardian = no.

5. **Bulk operator review surface.** When 50 proposals queue up,
   reviewing them one-by-one is operator burden. v0.3 ships with
   per-proposal review; v0.4 may add bulk-by-class operations
   ("ratify all preference_update for terseness; reject everything
   else from this week").

## Implementation tranches

- **T1** — `persona/<dna>/<instance_id>/` directory layout +
  proposal YAML schema + reader. New `core/persona.py` module
  parallel to `core/constitution.py`.

- **T2** — Drift detector: trait_alignment_scan (§4.1). Read
  conversation_turns over a 7-day window; compute observed
  trait-emphasis vector; diff vs constitutional weight; threshold
  to draft a proposal.

- **T3** — Preference accretion proposals (§4.2). Read memory
  with `claim_type='preference'`; group by field; threshold N
  agreeing entries to draft `preference_update`.

- **T4** — External correction proposals (§4.3). Read
  memory_contradictions where the agent's claim is the earlier
  side. Threshold ratio of unresolved-vs-resolved triggers proposal.

- **T5** — Daemon `/persona/proposals` endpoints (CRUD +
  ratify/reject/modify). FastAPI router parallel to
  `routers/conversations.py`.

- **T6** — Frontend `/personas` tab. Pending proposals list +
  ratify-modify-reject buttons + drift indicators + history.

- **T7** — H-1 / H-8 floor checks on proposal draft (§5). Tests
  proving Companion can't draft a proposal that violates
  `min_trait_floors`.

- **T8** — Voice safety filter v2: claim of growth requires a
  matching ratified proposal (§5 H-8 mitigation). Extends
  `voice_safety_filter.py` with persona-log lookup.

T1+T5 = "persona layer exists, operators can write proposals" — minimum bar for v0.3.
T2+T3+T4 = "agents draft proposals from observation" — full v0.3 close.
T6 = operator UX.
T7+T8 = harm-model integration.

## Attribution

The "self as maintained pattern" framing is from
[SarahR1 (Irisviel)](https://github.com/SarahR1)'s 2026-04-30 review.
Her phrasing: *"Initial traits → observed behavior → reflected
preferences → user-confirmed continuity anchors → contradiction/drift
review → revised self-model proposal. The agent should not silently
rewrite itself, but it should be able to propose self-model updates."*

This ADR adopts the proposal-then-ratify lifecycle she sketched and
embeds it in FSF's existing constitution-hash + audit-chain
discipline. The constitution-immutability constraint (no auto-rewrite),
the operator-gate (no agent self-ratification), the drift-detection
inputs (claim_type, contradictions, conversation telemetry), and
the H-1/H-8 floor enforcement are FSF-specific work. See
`CREDITS.md`.
