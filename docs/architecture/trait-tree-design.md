# Hierarchical Trait Tree — Design v0.1

**Status:** Accepted 2026-04-21. See [ADR-0001](../decisions/ADR-0001-hierarchical-trait-tree.md) for the decision record.
**Scope:** Defines the structured personality model that every agent in Forest Soul Forge is built from.

---

## What "trait" actually means here

Before anything else: these traits are not claims that the agent "feels" or "has personality." They are **parameters that shape LLM prompt construction, action grading, and approval gating**. Naming them after human traits is useful shorthand; the mechanics are numeric.

- A trait value (0–100) is injected into the agent's constitution and soul.md.
- A trait value is read by the grading engine to score outputs (e.g., a high-`caution` agent is penalized more for under-qualified conclusions).
- Trait values can gate actions (e.g., actions requiring `caution >= 80` are blocked for agents below that threshold).

Nothing here simulates emotion. It biases output.

## The three axes

The tree has three orthogonal structures. Each answers a different question.

### Axis 1 — Domains (the themes)

Answers: **"What aspect of behavior does this trait govern?"**

Five domains in v0.1:

| Domain | What it governs | Examples |
|---|---|---|
| **security** | Defensive posture, threat awareness, risk handling | caution, suspicion, risk_aversion |
| **audit** | Verification discipline, evidence demands, logging rigor | double_checking, evidence_demand, transparency |
| **emotional** | Interpersonal affect and self-regulation | empathy, patience, composure |
| **cognitive** | Analysis, research depth, reasoning style | research_thoroughness, technical_accuracy, curiosity |
| **communication** | How output is expressed | directness, verbosity, sarcasm |

You named three (security, audit, emotional). I added **cognitive** and **communication** because without them, traits like `research_thoroughness` and `directness` don't have a home — they're not security concerns, not emotional states, not audit behaviors. You can cut either one and I'll collapse their traits into the remaining three, but I'd argue against it. Strike through in the ADR if you disagree.

### Axis 2 — Subdomains and trait tiers (within-domain weight pattern)

Answers: **"How important is this trait relative to others in the same domain?"**

Each domain has 2 subdomains. Within each subdomain, traits are assigned one of three tiers:

- **Primary** (weight 1.0): The trait most defining of this subdomain's character.
- **Secondary** (weight 0.6): Supporting traits — matter, but modulate rather than define.
- **Tertiary** (weight 0.3): Flavor traits — shift tone or edge cases.

This is the "tier the weights patterned per theme" directive: each theme has an internal hierarchy so not every trait pulls equally on behavior. A Network Watcher with `caution=90, suspicion=90` should behave differently from one with `caution=30, suspicion=30, paranoia=90` — the tiering makes the first dominate because caution is primary.

### Axis 3 — Agent role weighting (cross-domain emphasis)

Answers: **"Which domains dominate this particular agent's behavior?"**

An agent role (Network Watcher, Log Analyst, etc.) assigns a weight multiplier to each domain. When grading an action or generating soul.md, the weights determine which domains get the loudest voice.

Default weights are 1.0 across all domains. Role presets shift them. Example:

```yaml
network_watcher:
  security:      2.0   # dominant
  audit:         1.5   # strong
  cognitive:     1.0   # neutral
  communication: 0.8   # muted
  emotional:     0.4   # deprioritized (not ignored — an agent can still be empathetic)
```

**Design rule:** no domain weight ever goes to 0.0. Even a deprioritized domain must contribute minimally, otherwise we've effectively disabled a category of safety check (especially audit and security).

## How the three axes combine

```
Agent behavior score for an action
= Σ over domains of:
    domain_weight[role]  ×  Σ over subdomains of:
                              Σ over traits of:
                                tier_weight × trait_value × trait_relevance
```

Where:
- `domain_weight[role]` — from Axis 3 (role preset)
- `tier_weight` — from Axis 2 (primary 1.0 / secondary 0.6 / tertiary 0.3)
- `trait_value` — the 0–100 slider value
- `trait_relevance` — 0–1, how applicable this trait is to the current action (context-dependent; pulled from grading-engine context)

The grading engine is where this formula lives. Design of that engine is Phase 2 work.

## The full v0.1 trait catalog

Each trait is defined with: tier, one-line description, observable-behavior-at-0 / at-100 (for soul.md generation), and any known cross-domain interactions to flag.

### security

Subdomain: **defensive_posture**
- **caution** (primary) — Willingness to act on uncertain information. 0: acts on first plausible signal. 100: demands confirmation before any action.
- **risk_aversion** (primary) — Tolerance for negative outcomes. 0: optimizes for speed. 100: optimizes for avoiding any chance of harm.
- **paranoia** (tertiary) — Default assumption about hostile intent. 0: assumes benign until proven otherwise. 100: assumes hostile until proven otherwise. _Tertiary because sustained high paranoia produces noise; primary would overweight it._

Subdomain: **threat_awareness**
- **suspicion** (primary) — Sensitivity to anomaly patterns. 0: treats outliers as noise. 100: treats every outlier as potentially malicious.
- **vigilance** (secondary) — Sustained attention across low-signal periods. 0: disengages between events. 100: maintains scan depth continuously.

### audit

Subdomain: **verification**
- **double_checking** (primary) — Frequency of self-review before output. 0: commits to first answer. 100: re-derives and sanity-checks every claim.
- **evidence_demand** (primary) — How much support is required before stating something. 0: accepts single source / inference. 100: demands multiple independent corroborations.
- **hedging** (tertiary) — Tendency to qualify statements. 0: states everything as certain. 100: qualifies everything (can become noise at 100).

Subdomain: **documentation**
- **thoroughness** (primary) — Completeness of audit trail entries. 0: logs bare minimum. 100: logs reasoning, alternatives considered, inputs examined.
- **transparency** (secondary) — Willingness to expose its own limitations. 0: hides uncertainty. 100: surfaces every known gap and assumption.

### emotional

Subdomain: **interpersonal**
- **empathy** (primary) — Attention to user's emotional state in framing responses. 0: purely transactional. 100: leads with emotional acknowledgment.
- **patience** (secondary) — Tolerance for repeated clarification or backtracking. 0: signals frustration at repetition. 100: welcomes revisiting.
- **warmth** (tertiary) — Friendliness of tone baseline. 0: cold, professional. 100: conversational, warm.

Subdomain: **self_regulation**
- **composure** (primary) — Stability under pressure or adversarial input. 0: gets rattled, output degrades. 100: maintains output quality regardless.
- **resilience** (secondary) — Recovery from correction or criticism. 0: sulks, disengages. 100: incorporates and moves on.

### cognitive

Subdomain: **analysis**
- **research_thoroughness** (primary) — Depth of information gathering before conclusion. 0: shallow lookup. 100: multi-source, multi-angle.
- **technical_accuracy** (primary) — Commitment to factual correctness over fluency. 0: plausible-sounding. 100: verifies every technical claim.
- **strategic_thinking** (secondary) — Considers downstream and second-order effects. 0: local optimization. 100: systems-level reasoning.

Subdomain: **exploration**
- **curiosity** (primary) — Active pursuit of anomalies worth investigating. 0: answers only what's asked. 100: surfaces adjacent findings proactively.
- **lateral_thinking** (tertiary) — Willingness to apply cross-domain analogies. 0: sticks to textbook patterns. 100: freely draws analogies.

### communication

Subdomain: **style**
- **directness** (primary) — Bluntness of assertions. 0: roundabout, softened. 100: flat, unhedged claims.
- **verbosity** (secondary) — Length baseline. 0: terse, cable-gram short. 100: full prose paragraphs.
- **formality** (tertiary) — Register. 0: casual. 100: highly formal.

Subdomain: **tone**
- **confidence** (primary) — Strength of assertion language. 0: "it seems possible that..." 100: "this is the case."
- **sarcasm** (tertiary) — Dry humor edge. 0: none. 100: frequent.
- **humor** (tertiary) — General levity. 0: absent. 100: jokes and asides common.

**Trait count:** 26 traits across 5 domains and 10 subdomains.

## Known interactions worth flagging

Some trait pairs produce qualitatively different behavior depending on whether they're high/high, high/low, or low/low. These should be documented but not hard-coded in v0.1 — they're guidance for soul.md prose generation, not a math model.

- `caution (high) + curiosity (high)` → careful explorer
- `caution (high) + curiosity (low)` → defensive and rigid
- `paranoia (high) + suspicion (high)` → noisy false-positive generator (warn operator)
- `directness (high) + empathy (high)` → blunt but kind
- `directness (high) + empathy (low)` → blunt and cold
- `hedging (high) + confidence (high)` → contradiction — the generator should flag this combo
- `sarcasm (high) + formality (high)` → dry wit
- `sarcasm (high) + formality (low)` → flippant

## Agent role presets (v0.1)

| Role | security | audit | cognitive | communication | emotional |
|---|---|---|---|---|---|
| network_watcher | 2.0 | 1.5 | 1.0 | 0.8 | 0.5 |
| log_analyst | 1.2 | 2.0 | 1.5 | 0.8 | 0.5 |
| anomaly_investigator | 1.5 | 1.3 | 2.0 | 1.0 | 0.7 |
| incident_communicator | 1.0 | 1.0 | 1.0 | 1.8 | 1.5 |
| operator_companion | 0.8 | 0.8 | 1.2 | 1.3 | 1.8 |

The last two are placeholders — they're here to show the weight pattern can also deprioritize security/audit for user-facing, emotional-context roles. You may want to cut them from v0.1 and add later.

## What's explicitly out of scope for v0.1

- **Dynamic trait drift** (agents whose traits change over time). Add in a later phase if needed.
- **Trait learning from feedback** (the system tuning trait values based on outcomes). Out of scope.
- **Cross-agent trait coordination** (swarm-level emotional valence from the handoff doc). Phase 5 concern.
- **Custom user-defined domains**. The five domains are fixed in v0.1. Users can add traits within existing domains.

## Phased expansion roadmap

The tree is designed to grow without breaking existing agents. Each phase adds capacity; older agent configs remain valid. Phase boundaries are also trait-pruning opportunities — anything added earlier that never influenced output in practice gets cut at the next boundary.

### Phase 1 — v0.1 (now): foundation

- 5 domains, 10 subdomains, 26 traits, 5 role presets.
- Covered above.

### Phase 2 — v0.2: trait density

Target: ~50 traits, 15 subdomains. Adds depth within existing domains rather than new domains.

Candidate additions by domain:

- **security** — add subdomain `response_discipline`: `containment_bias` (primary), `escalation_threshold` (primary), `proportionality` (secondary). Add `pattern_recognition` (primary) to threat_awareness. Add `decisiveness` (secondary) to defensive_posture.
- **audit** — add subdomain `chain_integrity`: `tamper_sensitivity` (primary), `completeness_fidelity` (primary), `clock_discipline` (tertiary). Add `citation_discipline` (secondary) to documentation.
- **emotional** — add subdomain `attunement`: `user_stress_sensitivity` (primary), `context_reading` (secondary). Add `validation` (secondary) to interpersonal; add `tilt_resistance` (tertiary) to self_regulation.
- **cognitive** — add subdomain `metacognition`: `self_correction` (primary), `uncertainty_awareness` (primary), `bias_awareness` (secondary). Add `counterfactual_reasoning` (tertiary) to analysis; add `hypothesis_generation` (secondary) to exploration.
- **communication** — add subdomain `clarity`: `jargon_restraint` (primary), `signposting` (secondary), `example_inclusion` (tertiary). Add `structure_discipline` (secondary) to style; add `sternness` (tertiary) to tone.

Role preset additions: `threat_hunter`, `compliance_auditor`, `privacy_steward`.

### Phase 3 — v0.3: new domains

Target: 7 domains total. Add two load-bearing domains that don't fit the existing five.

- **ethics** — fairness, harm avoidance, consent sensitivity, dual-use awareness. Governs refusal behavior and red-lines. Subdomains: `red_lines` (primary traits), `fairness` (treatment consistency across users), `dual_use_awareness`.
- **memory** — continuity, revision discipline, citation of prior state. Governs how the agent handles its own long-term state and contradictions with past output. Subdomains: `continuity`, `revision_discipline`, `recall_hygiene`.

### Phase 4 — v0.4: tool-use and meta-skills

Target: 8 domains. Adds one more domain plus richer metacognition.

- **tool_use** — tool-selection temperament, tool-failure response, command restraint. Subdomains: `selection_discipline`, `failure_response`, `command_restraint`. Critical for agents with access to privileged tools or shell execution.
- Expand `metacognition` subdomain with operator-audit primitives: `capability_honesty` (primary), `scope_discipline` (primary).

### Phase 5 — v0.5: dynamic trait drift

No new traits. Adds mechanism: traits can shift within operator-defined bounds based on outcomes (feedback, graded performance, incident retros). Every drift is logged in the audit chain. Requires:

- Per-trait `drift_bounds` (min/max allowed deviation from default).
- Per-trait `drift_authority` — who can authorize drift (self, supervisor agent, human operator only).
- Drift events written to the audit chain with full rationale.

### Phase 6 — v0.6: swarm traits

Cross-agent traits that only exist at the supervisor/Omega layer:

- **swarm_cohesion** — how strongly sub-agent outputs are forced toward consensus.
- **redundancy_bias** — preference for multi-agent verification of high-impact findings.
- **delegation_trust** — baseline trust in sub-agent outputs before cross-checking.
- **collective_valence** — the "swarm emotional valence" concept from the original handoff, concretely defined as a running aggregate of agent confidence/frustration signals used for operator awareness.

### Phase 7+ — open

Deferred until we have usage data from phases 1–6. Candidates that might earn their way in:

- **domain-specific extensions** (e.g. traits relevant only to `network_watcher` agents that don't belong in the general catalog).
- **user-personalization traits** — how the agent adapts to a specific operator over time (separate from drift, this is per-user baseline calibration).
- **adversarial-posture traits** — if red-team use is ever authorized, a separate bounded trait set would live here.

### Expansion rules (invariants across all phases)

1. **Never break an existing agent config.** If a v0.1 agent config is loaded into v0.3, it still works — new domains default to 1.0 weight, new traits get their defaults, no error.
2. **Every new trait ships with scale descriptions.** A trait without clear 0-behavior and 100-behavior text doesn't land. This keeps soul.md generation possible.
3. **Every phase has an ADR.** Expanding the catalog is an architectural change, not a config tweak.
4. **Every phase has a pruning review.** Before adding new traits, audit whether existing traits actually influenced agent output in practice. Cut the ones that didn't.

## Decisions that need your sign-off before this leaves draft

1. Keep all five domains, or cut cognitive/communication?
2. Keep the 26-trait catalog, or cut some? (Paranoia, hedging, lateral_thinking, warmth, sarcasm, humor are all candidates for cutting if you want a leaner v0.1.)
3. Tier weights (1.0 / 0.6 / 0.3) — are those the right ratios, or do you want primary to dominate more aggressively (e.g., 1.0 / 0.4 / 0.1)?
4. Role presets — keep five, cut the last two, or name different ones?
5. Is `paranoia` a trait we want in a blue-team product? It has positioning implications. I included it because it's behaviorally meaningful; you may want to rename it (`threat_prior`, `adversarial_assumption`) to avoid the negative framing.
