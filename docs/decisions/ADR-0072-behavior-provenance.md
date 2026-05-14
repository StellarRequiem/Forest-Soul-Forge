# ADR-0072 — Behavior Provenance + Policy Boundary

**Status:** Accepted (2026-05-14). Phase α of the ten-domain
platform arc. Locks the four-layer hierarchy that governs how
Forest's agents make decisions: operator policy vs. operator
preference vs. learned behavior vs. hardcoded handoffs.

## Context

Forest's agents take action via three substrate-layer disciplines:

1. **Constitutional rules** (per-agent allow_paths /
   allow_commands / allow_tools etc., shipped via ADR-0006 +
   ADR-0033 + ADR-0040). Static. Operator-edited at birth.
2. **Posture** (green / yellow / red trust dial, ADR-0045).
   Dynamic. Operator flips at runtime.
3. **Reality Anchor** (per-claim ground-truth verification,
   ADR-0063). Dynamic. Per-dispatch / per-turn.

What's missing: **the boundary between "operator policy" and
"learned behavior."** The ten-domain platform arc introduces
agents that adapt — the orchestrator learns routing preferences,
the Daily Life OS learns energy patterns, the Knowledge Forge
learns topics-of-interest. Adaptation is the point.

Without a clear hierarchy, adaptation drift becomes silent:
- Did the orchestrator route this because the operator said to,
  or because it inferred a preference from past routing?
- Did the Content Studio match this voice because the operator
  curated it, or because the style_steward inferred it?
- Did the SOC escalate this because a hardcoded rule fired, or
  because anomaly_ace decided on its own?

Operator answer from 2026-05-14:
> "Hardcoded for some, learned for others."

The "for some" / "for others" is what ADR-0072 formalizes.

## Decision

This ADR locks **three** decisions:

### Decision 1 — Four-layer rule hierarchy with strict precedence

| Layer | Where | Mutable by | Precedence |
|---|---|---|---|
| **Hardcoded handoff** | `config/handoffs.yaml` | Engineer via PR | Highest |
| **Operator policy** | `constitution.yaml` (per-agent) | Operator at birth; immutable after | High |
| **Operator preference** | `data/operator/preferences.yaml` | Operator (CLI / future UI) | Medium |
| **Learned behavior** | `data/learned_rules.yaml` | Agent (auto-edit; RA-gated) | Lowest |

**Strict precedence:** higher layer ALWAYS overrides lower on
conflict. Learned rules can never override hardcoded handoffs.
Operator preferences can never override per-agent constitutional
policies (those are immutable per ADR-0049 — the constitution
hash IS part of the agent's identity).

### Decision 2 — Learned rules are auto-edited but Reality-Anchor-gated

Learned rules get auto-written by agents based on observed
behavior (e.g., "operator routes 'draft' intents to D7 80% of the
time → boost D7 weight for 'draft' utterances"). Auto-edit is
necessary for adaptation to be useful — but it's also the
highest-risk surface for drift.

Gate: **every learned rule activation passes through the Reality
Anchor (ADR-0063) before it can affect dispatch.** A learned rule
proposed by the orchestrator that contradicts an operator-asserted
ground-truth fact gets refused with `learned_rule_refused` event.
The operator sees every rule before it activates.

Concretely:
- `data/learned_rules.yaml` has TWO sections: `pending_activation`
  and `active`. New rules land in `pending_activation`.
- A nightly cron (ADR-0041 scheduler) runs `verify_claim.v1`
  against each pending rule's text + the operator-global
  ground-truth catalog.
- Verified rules move to `active`. Refused rules stay in
  `pending_activation` with the verdict + reason recorded; the
  operator can view + manually-approve via the future UI.

### Decision 3 — All four layers feed `behavior_change` audit events

When ANY of the four layers mutates (operator edits preference,
agent proposes a learned rule, engineer pushes a handoffs.yaml
change, operator updates constitution), an audit chain entry
captures it:

```
event_type: behavior_change
event_data:
  layer: hardcoded_handoff | constitutional | preference | learned
  source: <commit_sha | operator_id | agent_dna>
  change: <delta-shape JSON; before/after>
  reason: <one-line operator-readable rationale>
```

This makes forensic replay of "why does Forest behave this way?"
fully answerable from the chain alone. Every behavioral decision
back-traces to its layer + its source + its rationale.

## Implementation Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Schema + loaders for preferences.yaml + learned_rules.yaml + behavior_change audit event | This burst (B290). Foundation. | 1 burst |
| T2 | Preference CLI: `fsf operator preference get/set/delete` + audit emit on change | 1 burst |
| T3 | Learned rule auto-edit substrate (agents emit proposed rules) + Reality Anchor gate cron | 1-2 bursts |
| T4 | Orchestrator integration: resolve_route consults preferences AND active learned rules with the precedence ordering | 1 burst |
| T5 | Frontend Provenance pane: show what behavior is policy vs preference vs learned, with active vs pending breakdown | 1-2 bursts |

Total: 5-7 bursts.

## Consequences

**Positive:**

- Operator answer to "why did Forest do that?" is always
  available + always layered. The hierarchy makes drift
  impossible to hide.
- Learned rules become safe-to-activate via the Reality Anchor
  gate. No silent drift.
- Hardcoded handoffs are honest about their authority — they ARE
  the most authoritative because they're code-reviewed.
- Per-agent constitutional immutability stays intact (it's the
  most-authoritative AGENT-LEVEL layer); the four-layer model
  doesn't violate ADR-0049's hash-immutability invariant.

**Negative:**

- Adds a Reality-Anchor-gated cron job to the daemon. One more
  background process. Mitigated by reusing ADR-0041's scheduler.
- Operator preferences become a real surface that can drift away
  from operator intent (operator forgets they set something).
  Mitigated by audit-chain capture + provenance pane in T5.
- Learned rules can stay `pending_activation` indefinitely if
  Reality Anchor keeps refusing them. That's a feature (no
  silent drift) but operators may forget to triage. T5 surfaces
  the backlog.

**Neutral:**

- Reuses ADR-0063 verifier + ADR-0041 scheduler + audit chain
  envelope encryption. No new substrate at the platform level.
- Per-agent constitution.yaml is unchanged — the four-layer
  hierarchy operates OUTSIDE the constitution, not on top of it.

## What this ADR does NOT do

- **Does not modify constitution.yaml format.** Operator policy
  stays exactly where it was; this ADR just names its precedence
  position formally.
- **Does not auto-promote pending learned rules.** Promotion is
  always Reality-Anchor-gated; rules that fail the gate stay
  pending until the operator manually approves OR the underlying
  ground truth changes to clear the conflict.
- **Does not ship the operator-preference CLI.** T2.
- **Does not ship learned-rule auto-emission.** T3.

## See Also

- ADR-0006 Constitution & Soul
- ADR-0045 Posture (trust dial)
- ADR-0063 Reality Anchor (the gate for learned rules)
- ADR-0049 per-event signatures (constitution hash immutability)
- ADR-0067 cross-domain orchestrator (primary consumer of
  preferences + learned rules at T4)
