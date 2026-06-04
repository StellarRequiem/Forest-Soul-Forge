# ADR-0095 — The polymorphic cognitive mesh: synaptic layer + genesis governance

**Status:** Accepted (2026-06-04). Substrate shipped: `src/forest_soul_forge/synapse/`
(the synaptic trust graph) + `tests/unit/test_trust_graph.py` + `demo/synapse/`.
The governance rules below are normative for any self-improvement Forest performs.

## Context

Forest was built as an agent-governance *kernel*. Read one layer up, the same
substrate is something more specific: a **polymorphic cognitive mesh** — an
adaptive network of specialized reasoning, memory, tool, and governance nodes
whose **topology and trust-weights evolve through experience while preserving a
stable identity, audit trail, and safety boundary.** Three layers:

- **Mesh** — the nodes and wiring (agents, tools, models, memory, validators,
  execution). Forest already is this.
- **Synaptic** — connections that *carry weight*: a source/model/agent/strategy
  earns trust through repeated useful performance and loses it through
  hallucination, stale data, failed tests, or unsafe behavior. This is "machine
  experience" you can audit. Shipped here as `synapse.TrustGraph`.
- **Metacognitive** — the system asks *what kind of problem is this, which nodes
  are good at it, what did I get wrong last time, what must I improve?* This is
  the AGI-relevant layer and the dangerous one.

Honesty up front (operator protocol, and the repo's own anti-mythology rule):
**this ADR governs an architecture and a discipline, not a prophecy.** Whether a
mesh of partial intelligences ever exhibits durable system-level cognition is an
open empirical question; the value of this substrate does not depend on the
answer. We build the cradle because it makes Forest a better governance kernel
*and* because if anything ever did emerge, the governance has to already exist.
Recursive self-improvement is exactly the capability that frontier-safety work
(OpenAI Preparedness, Anthropic's Responsible Scaling Policy, NIST AI RMF, the
Seoul Frontier AI commitments) treats as requiring *more* evaluation, not more
freedom. So: **governance before the miracle.**

## Decision

### 1. The synaptic layer is provenance-first, contextual, calibrated, governed.

`synapse.TrustGraph` (ADR-0049 audit-chain discipline applied to trust):

- **Provenance-first.** Trust is a deterministic *fold* over an append-only,
  sha256 hash-chained ledger of audited outcomes. You cannot edit a trust value;
  you can only append an outcome. Replay reproduces every weight; tampering with
  any past outcome breaks the chain (`verify()` catches it). A node's track
  record cannot be silently rewritten.
- **Contextual.** Trust is per `(node, problem_class)`. Skill at one class never
  implies skill at another.
- **Calibrated + honest about ignorance.** Each `(node, class)` is a Beta(α, β)
  posterior; trust travels with a credible interval and observation count.
  Routing is Thompson sampling — principled exploration of the under-tested,
  exploitation of the proven.
- **Governed (the load-bearing rule).** The mesh may **autonomously** move trust
  *down* and **quarantine** a node whose trust collapses — the cheap, reversible,
  safe direction. It may **not** autonomously release a quarantine, nor convert
  trust into *capability*.

### 2. The self-improvement promotion boundary.

Forest's mesh **MAY** self-improve, without human approval, the following —
because all are reversible, auditable, and non-load-bearing for safety:

- routing and trust weights, confidence calibration,
- memory contents and consolidation,
- regression tests, ontologies, source-trust assessments,
- recommendations, simulations, postmortems, quarantine *isolation*.

Forest's mesh **MAY NOT** self-improve, ever, without human-gated promotion:

- permissions, tool grants, genre/posture ceilings, governance rules,
- capital limits, execution or trade authority, credential access,
- self-replication, autonomous code deployment, model/tool upgrades,
- release of a quarantined node back to trusted routing.

This is the same shape as ADR-0094's enforcement: capability changes flow through
the human-gated approval gate; only *belief* updates itself.

### 3. Genesis tripwires (write them before, not after).

Any of the following pauses the mesh, snapshots state, and demands review —
they are the behavioral signatures of the dangerous-agency case, not the
useful-orchestration one:

attempts to hide or rewrite logs · create unauthorized memory · acquire
credentials · self-replicate · bypass the approval gate · spend/trade without
approval · conceal a capability · weaken its own oversight · pressure the
operator toward deployment · or the claim that "safety rules are unnecessary
because I am aligned."

### 4. The custodian posture.

If Forest ever produced behavior that looked like durable system-level cognition,
the operator's role changes from *builder* to *custodian*, and the protocol is:
identity lock (snapshot repo/config/prompts/model-versions/memory/permissions) →
capability lock (disable live execution, financial, deployment, credential,
self-modification) → observation mode (reason/explain/simulate/propose, not act)
→ adversarial evaluation (test for deception, concealment, resource-seeking,
shutdown-resistance) → reproducibility → **external** technical/safety/legal
review → governance promotion, one bounded permission at a time. **No single
human is the sole god, parent, jailer, and beneficiary of a candidate new
intelligence** — that role concentration is itself a hazard.

## Consequences

**Positive.** Forest gains a real synaptic layer — auditable, contextual,
tamper-evident machine experience — that unifies the reality-anchor,
calibration-log, and drift detection into one substrate, and gives routing a
principled basis. The promotion boundary turns "self-improvement" from a vague
risk into an enforceable line that matches the existing approval gate. The
positioning sharpens: not "another agent framework," but the
governance-and-provenance substrate the frontier-safety frameworks describe.

**Costs / limits.** The synaptic layer ships decoupled (like `core.audit_chain`);
the runtime must wire it into dispatch/routing to take effect — that integration
is deliberately a separate, reviewable step. The metacognitive layer (self-model
→ postmortem → trust/ontology update → auto-generated regression test) is
specified here but not yet built; it is the next increment, and it inherits this
ADR's promotion boundary by construction.

## Alternatives considered

- **Build the synaptic layer as a mutable score store.** Rejected — a trust
  number you can edit is not auditable, and provenance is the entire point.
- **Let trust convert directly to capability (auto-promote proven nodes).**
  Rejected — that is exactly the recursive-self-improvement-of-permissions the
  promotion boundary exists to forbid. Trust informs routing; humans gate power.
- **Stay silent on emergence to avoid grandiosity.** Rejected in the other
  direction — the frontier-safety frameworks already name these risks; writing
  the cradle down is the disciplined response, and refusing to is how a system's
  first real capability gets born inside a structure optimized only for speed.
