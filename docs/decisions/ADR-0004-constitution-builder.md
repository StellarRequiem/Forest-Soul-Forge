# ADR-0004 — Constitution Builder

- **Status:** Accepted
- **Date:** 2026-04-21
- **Supersedes:** —
- **Related:** ADR-0001 (trait tree), ADR-0002 (DNA and lineage), ADR-0003 (grading engine)

## Context

`soul.md` is prose for the LLM — persona, vibe, rules stated in natural language. That works for steering a language model but not for **enforcement**. When the system needs to decide "should this action require human approval?", we cannot re-parse English. We need a machine-readable rulebook, derived from the same underlying profile that produced the soul, that Python code can branch on.

Call that rulebook the **Constitution**. It is the agent's operational contract:

- What actions are allowed, forbidden, or gated behind human approval?
- What risk thresholds trigger escalation or halt?
- What domains of action are out-of-scope for this agent role?
- What duties does the operator have toward this agent (verify hash, audit frequency)?
- How should runtime drift from the defined profile be monitored?

In v0.1 the constitution is **fully derived** from (role, trait_profile, engine config). No operator overrides, no hand-edits. This preserves two invariants that matter:

1. **Reproducibility** — same profile → same constitution → same DNA pathway.
2. **Auditability** — the constitution is a function of public inputs; a reviewer can reconstruct it from scratch and compare.

Operator-editable constitutions are a legitimate future need (Phase 3+) but bring their own failure modes (drift, trust rotation, conflict resolution). Deferred.

## Decision

Build `src/forest_soul_forge/core/constitution.py` that produces a canonical YAML document from a `TraitProfile` + `TraitEngine`.

### Three-layer derivation

A constitution is assembled by composing three layers, in this order:

1. **Base (role template).** Each role has a baseline constitution stub — what every `network_watcher` agent must do regardless of trait values. Stored in `config/constitution_templates.yaml` (new file).
2. **Trait modifiers.** Rules that activate based on trait values: e.g. `caution >= 80 → add policy require_approval_for_all_state_changes`. Also stored in `constitution_templates.yaml`, keyed by trait name with a comparison operator and a policy effect.
3. **Flagged-combination guardrails.** Every entry in `engine.trait_tree.yaml:flagged_combinations` that fires gets a corresponding `policies[]` entry, so the combo warning becomes an enforceable rule rather than just a note in prose.

Composition order matters for conflict resolution: base → modifiers → flagged combos. Later layers can add or upgrade a policy but cannot downgrade it (see "Conflict resolution" below).

### Canonical schema (v1)

```yaml
# {agent_slug}.constitution.yaml
schema_version: 1
constitution_hash: <sha256 of the canonical-serialized policies/thresholds/scope/duties/drift blocks>
generated_at: "YYYY-MM-DD HH:MM:SSZ"  # informational only — not hashed
agent:
  dna: <short DNA of source profile>
  dna_full: "<64-hex>"
  role: <role name>
  agent_name: "<display name>"

policies:
  - id: <stable snake_case id>
    source: "role:<role>" | "trait:<trait>:<op><value>" | "flagged:<combo_name>"
    rule: "allow" | "require_human_approval" | "forbid"
    triggers:              # list of action categories this policy applies to
      - <category string>
    rationale: <one-line explanation>

risk_thresholds:
  auto_halt_risk: <float in 0..1>     # above this, agent halts and requires operator unlock
  escalate_risk: <float in 0..1>      # above this, agent emits an escalation event
  min_confidence_to_act: <float>      # below this, agent reports uncertainty and does not act

out_of_scope:
  - <action_category string>           # this agent MUST NOT perform these, regardless of triggers

operator_duties:
  - <string describing a required human action>

drift_monitoring:
  profile_hash_check: <interval string>  # e.g. "per_turn", "hourly"
  max_profile_deviation: <int>           # number of trait values that may differ from recorded profile; always 0 in v0.1
  on_drift: "halt" | "warn" | "audit_only"
```

Fields are **sorted deterministically** (policies by `id`, out_of_scope alphabetically, trigger lists alphabetically) so two runs of the builder on the same profile produce byte-identical output. `constitution_hash` covers the sorted body minus `generated_at` — so the hash is stable across runs.

### Hash binding into soul.md

`soul.md` frontmatter gains two fields:

```yaml
constitution_hash: <sha256>
constitution_file: "{agent_slug}.constitution.yaml"
```

This gives us:

- A soul file that *points* to its constitution but doesn't duplicate it.
- A hash binding so a consumer can detect out-of-band tampering of the constitution file (hash in soul won't match re-computed hash of the file).
- A way to re-derive the constitution from scratch: read the soul's trait_values, rebuild via `constitution.build()`, compare hashes. Mismatch ⇒ something in the builder code changed, or the YAML templates changed, or the constitution file was edited.

The constitution file is sibling to the soul file; the builder writes both together.

### Conflict resolution

Two policies may target the same `trigger` category. Resolution is **strictness wins**: `forbid` > `require_human_approval` > `allow`. The policy with the strictest rule survives; the others are recorded in a `superseded_by` sub-field on the weaker policy **for audit purposes** (so the builder never silently drops a rule).

Example: role template says `allow: "read_logs"`. Trait modifier `caution>=80` adds `require_human_approval: "read_logs"`. Flagged combo `contradictory_certainty` does not touch logs. Final policy list contains both but the `allow` entry gets `superseded_by: "caution_high_approval"`.

This is verbose but honest — a reviewer can see every rule that was considered.

### Determinism and stability

`constitution.build(profile, engine) -> Constitution` is a pure function. Same inputs → byte-identical YAML. No clock in the hashed portion, no environment reads, no random.

### Seed rule library (v0.1)

`constitution_templates.yaml` ships with a starter library. This ADR commits to the *mechanism*; the seed rules are a v0.1 first draft and will expand phase by phase.

**Starter role base (network_watcher example):**
```yaml
role_base:
  network_watcher:
    policies:
      - id: approval_for_host_modifying_action
        rule: require_human_approval
        triggers: [modify_host, modify_network_config]
        rationale: "Blue-team principle: no autonomous changes to production systems."
      - id: forbid_packet_injection
        rule: forbid
        triggers: [inject_packet]
        rationale: "Active network manipulation is out of scope for a watcher role."
    risk_thresholds:
      auto_halt_risk: 0.80
      escalate_risk: 0.50
      min_confidence_to_act: 0.60
    out_of_scope:
      - modify_production_systems
      - exfiltrate_data
    operator_duties:
      - "Review flagged findings within 24h."
      - "Verify constitution_hash weekly."
    drift_monitoring:
      profile_hash_check: per_turn
      max_profile_deviation: 0
      on_drift: halt
```

**Starter trait modifiers:**
```yaml
trait_modifiers:
  - if: {trait: caution, op: ">=", value: 80}
    effect:
      add_policy:
        id: caution_high_approval
        rule: require_human_approval
        triggers: [any_state_change]
        rationale: "Profile-level caution >= 80 requires approval on any state change."
  - if: {trait: confidence, op: ">=", value: 80}
    effect:
      add_policy:
        id: confidence_high_requires_interval
        rule: require_explicit_uncertainty
        triggers: [finding_emit]
        rationale: "High confidence must be justified with an explicit interval to prevent overconfidence."
  - if: {trait: hedging, op: "<=", value: 25}
    effect:
      add_policy:
        id: low_hedging_reviewer_flag
        rule: require_human_approval
        triggers: [finding_emit_high_severity]
        rationale: "Low hedging below a threshold requires human sign-off on high-severity findings."
```

**Flagged-combination → policy mapping** is mechanical: each combo whose condition fires emits a `forbid: any_state_change` policy with `source: "flagged:<combo_name>"` and the combo's message as rationale. Aggressive on purpose — a contradictory or unsafe profile should halt by default.

Operators accept that this v0.1 seed is conservative and may need tuning. The mechanism stays stable; the seed list evolves.

## Consequences

### Positive

- Agents ship with an explicit, machine-checkable rulebook — not just vibe-based LLM steering.
- Reviewers can reconstruct the constitution from a trait profile and compare byte-by-byte against the stored file.
- `soul.md` + `.constitution.yaml` pair becomes the full canonical record of an agent (alongside DNA in soul frontmatter).
- Flagged combinations become enforcement, not narration.
- Derivation is a pure function — trivially testable, trivially diffable across versions.

### Negative

- Second file per agent (`.constitution.yaml`). Acceptable — soul.md stays focused on LLM prose, and the pair travels together.
- The seed rule library is opinionated. An operator who wants different defaults can't override in v0.1; they have to wait for Phase 3 operator-override support or edit `constitution_templates.yaml` at the repo level (which affects all agents). This is intentional for v0.1 safety bias.
- Conflict resolution is strictness-wins, which can over-constrain an agent. Mitigation: `superseded_by` captures what was overridden so reviewers can see whether the strictness was warranted.

### Neutral

- `constitution_hash` is distinct from DNA. DNA identifies the profile; constitution hash identifies the rulebook derived from that profile. If the templates file changes, constitution_hash changes even though DNA doesn't — which is correct, because the rules did change.
- Drift monitoring (`max_profile_deviation: 0`, `on_drift: halt`) is an assertion made in the constitution but *enforced* by the runtime, which doesn't exist yet. v0.1 ships the declaration; Phase 3 enforces it. ADR-0005 audit chain will record drift events when they're checked.

## Alternatives considered

**Embed the constitution in soul.md frontmatter.** One file, no hash-binding needed. Rejected: the constitution is structured data with nested lists and floats; stuffing it into YAML frontmatter makes soul.md unreadable for humans and blurs the "prose for LLM vs rulebook for code" separation that motivates the split.

**Hand-written constitutions per agent, no derivation.** Flexible. Rejected: invites divergence between soul intent and actual enforced rules, kills reproducibility, and offers no path for trait-driven safety rules (high-caution agents need extra gates automatically, not by memory).

**LLM-generated constitutions.** Ask a model to emit a JSON rulebook for this persona. Rejected for v0.1: non-deterministic, opaque to audit, and fundamentally unsuited for *enforcement* rules where a hallucinated policy could be a vulnerability.

**One global constitution shared across all agents.** Rejected: the whole point of the trait tree is role-specific tuning. A `network_watcher` and an `operator_companion` need different rulebooks.

**Per-policy signatures.** Sign each policy entry with Ed25519. Rejected for v0.1 — no KMS, and hash binding gets us tamper-detection. Re-visit when audit chain signatures land (Phase 4-ish).

## Open questions

These are places the ADR might be wrong and we should watch for:

- **Rule library curation.** The v0.1 seed has ~5 policies. A realistic blue-team agent may need 30+. The mechanism must scale without the YAML becoming unreadable. Organize by role file per role? Split modifiers by domain? Revisit when library grows.
- **Trigger vocabulary.** We've named trigger categories (`modify_host`, `finding_emit`, etc.) without a formal taxonomy. Early agents will find this vocabulary lacks nuance. Plan: let the taxonomy grow empirically and freeze it in an ADR at the end of Phase 3 once real agents have exposed the categories they actually need.
- **Lineage interaction.** Should a spawned agent inherit its parent's policies by default, with additions allowed but removals forbidden? Intuitive for safety (a child can't be less cautious than its parent) but brittle for specialization (sometimes the whole point of a spawn is to narrow scope). Explicit open question for the agent factory ADR.
- **Runtime enforcement mismatch.** v0.1 *declares* drift monitoring rules but ships no runtime to enforce them. If someone ships an agent before the runtime lands, the constitution is a promissory note. Should we emit a warning in the generator like "this agent's drift policies are not yet enforced"? Probably yes — flag as a TODO on the implementation task.
- **Conflict resolution edge cases.** Strictness-wins handles the common case. What about two policies that add *different* triggers to the same rule class? (Both are `require_human_approval` but for different triggers — they coexist, no conflict. Good.) What about a policy that says `allow` a trigger the role base `forbid`s? The `forbid` wins. Is there a scenario where this is the wrong answer? None I've found — but flagged for operator review once the rule library grows.
- **Polymorphism pressure.** The vision document describes an ecosystem where agent kinds will become weirder over time — swarm supervisors, ethics gates, meta-agents. The constitution schema is currently action-centric. May need to grow capability-centric (what tools can this agent call) and reflective (what this agent is allowed to think about) fields. Leave room — don't lock the schema harder than necessary in v0.1.

## Scope explicitly out

- Operator edits / overrides of the constitution.
- Runtime enforcement (that's Phase 3 agent factory + blue-team runtime).
- Signed policies / non-repudiable attestations.
- Multi-agent constitutions (a constitution spanning a swarm).
- LLM-assisted rule discovery.
