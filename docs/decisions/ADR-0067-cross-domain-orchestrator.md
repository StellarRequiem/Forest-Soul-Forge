# ADR-0067 — Cross-Domain Orchestrator

**Status:** Accepted (2026-05-14). Phase α of the ten-domain platform
arc — the router that decomposes operator intents into per-domain
work and routes each sub-intent to the right swarm.

## Context

The ten-domain platform arc (D1 Knowledge Forge through D10 Research
Lab) needs a single front door. The Persistent Assistant Chat
(ADR-0047) is that front door from the operator's perspective — one
conversation, one agent to talk to. But behind it, ten distinct
swarms own ten distinct capabilities. Something has to:

1. **Decompose** an operator utterance into sub-intents.
   "Remind me to call Mom and draft the Q3 update and tell me what
   the SOC saw overnight" = 3 sub-intents across 3 domains.

2. **Route** each sub-intent to the right domain.
   Each sub-intent needs to land at the entry agent of the
   correct domain swarm with enough context to act.

3. **Coordinate** when sub-intents span domains.
   "Tell me my burn rate AND remind me about the rent deadline"
   = finance + life-OS handoff. The orchestrator sequences these.

4. **Audit** every decomposition + routing decision.
   Forensic replay of "why did the assistant route this to SOC?"
   must work from the chain alone.

5. **Govern** routing via the same constitutional substrate that
   gates tool calls. Cross-domain routing IS a kind of delegate
   call — it should ride on the existing `delegate.v1` + `agent_delegated`
   audit family.

Without a router, every operator utterance either lands at a single
hard-coded agent (limiting) or requires the operator to pick the
right domain themselves (defeats the assistant-front-door pattern).

## Decision

This ADR locks **five** decisions:

### Decision 1 — Domain registry is the source of truth for routing

A domain is the unit of routing granularity. The orchestrator
doesn't reason about which specific agent inside a domain to wake
— it routes to the domain's **entry agent**, and the entry agent's
own constitution decides further handoff inside the swarm.

Each domain has a YAML manifest at `config/domains/<domain_id>.yaml`:

```yaml
domain_id: d3_local_soc
name: Local SOC
status: planned | partial | live
description: |
  Single-host, audit-grade, sovereign Security Operations Center...
entry_agents:
  - role: response_rogue
    capability: incident_response
  - role: log_lurker
    capability: log_monitoring
capabilities:
  - threat_detection
  - incident_response
  - vulnerability_management
example_intents:
  - "is there anything weird happening on my machine?"
  - "what did the SOC see overnight?"
  - "are my patches up to date?"
depends_on_substrate:
  - ADR-0049  # per-event signatures
  - ADR-0064  # telemetry pipeline (queued)
depends_on_connectors:
  - forest-files
handoff_targets:
  - d8_compliance
  - d4_code_review
```

Manifests are loaded at daemon boot into a `DomainRegistry`. The
loader validates required fields + cross-references (e.g., a
`handoff_target` must point at another loaded domain).

### Decision 2 — Status field gates routing eligibility

Each domain manifest carries a `status` field:

- **planned** — registered but no entry agents alive yet. Router
  acknowledges the intent but refuses to dispatch, surfaces "this
  domain is planned, not yet live" to the operator.
- **partial** — some entry agents alive; some capabilities work.
  Router dispatches when the requested capability has a live entry
  agent; refuses on capabilities still planned.
- **live** — fully birthed swarm. Router dispatches freely.

This lets the registry sit alongside ten manifests immediately while
domains roll out one at a time. The orchestrator's "I can route to
X but not Y" honesty surfaces in the operator UX without code
changes per domain.

### Decision 3 — Routing is a delegate call

Cross-domain dispatch rides on `delegate.v1` (already shipped per
ADR-0033). The orchestrator agent calls `delegate.v1` with:

- `target_instance_id`: the entry agent for the routed domain
- `skill_invocation`: a skill manifest describing the sub-intent
- `args`: extracted from the operator utterance

The existing `agent_delegated` audit event records the handoff.
Adding a new event type would split the cross-agent-call surface;
reusing `agent_delegated` keeps the audit chain query model
uniform.

A NEW event type IS added: `domain_routed`. Emitted by the
orchestrator BEFORE the delegate call, captures the decomposition
decision:

- `operator_intent`: the original utterance (or its hash, if
  encrypted)
- `decomposed_subintents`: list of sub-intents
- `routing_decisions`: map of sub-intent → target_domain
- `confidence`: orchestrator's confidence score per routing

This lets operators replay routing decisions independently of the
downstream delegate audit events.

### Decision 4 — Intent decomposition uses the local LLM

Sub-intent extraction needs natural-language understanding. The
orchestrator uses `llm_think.v1` (already shipped) with a
constrained prompt that outputs structured JSON:

```json
{
  "subintents": [
    {
      "intent": "remind me to call Mom",
      "domain": "d2_daily_life_os",
      "capability": "reminder",
      "confidence": 0.95
    },
    {
      "intent": "draft Q3 update",
      "domain": "d7_content_studio",
      "capability": "draft_writing",
      "confidence": 0.85
    },
    {
      "intent": "what did SOC see overnight",
      "domain": "d3_local_soc",
      "capability": "incident_summary",
      "confidence": 0.92
    }
  ]
}
```

When the LLM confidence falls below operator-tuned thresholds
(default 0.6), the orchestrator surfaces ambiguity to the operator
rather than guessing.

The decomposition tool is `decompose_intent.v1` (queued for T2).
T1 ships the registry + manifest format; T2 ships the
decomposition tool that reads the registry to populate the
LLM's domain enumeration.

### Decision 5 — Hardcoded + learned routing co-exist

Per the operator-locked design (B277 conversation), routing
behavior splits across two rails:

- **Hardcoded** — `config/handoffs.yaml` (new) — load-bearing
  cross-domain handoffs. Example: every new D4 Code Review PR
  triggers D8 Compliance Auditor. Engineer-edited via PR;
  code-reviewed before merge.
- **Learned** — `data/learned_routes.yaml` — orchestrator-curated
  preference adjustments. Example: operator consistently routes
  "draft" intents to D7 even when ambiguous between D7 + D10 →
  the learned weight on "draft" → D7 grows. Auto-edited;
  audit-logged per change; Reality Anchor verifies before
  activation.

Learned routes never override hardcoded handoffs. Hardcoded paths
always win on conflict.

T1 ships the registry foundation; the handoffs.yaml format +
learned-route adapter ship in T4 (ADR-0067 T4 — full routing
engine with both rails).

## Implementation Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Domain registry + manifest format + loader + 10 seed manifests | This burst (B279). Foundation. | 1 burst |
| T2 | `decompose_intent.v1` builtin tool | LLM-driven sub-intent extractor reading the registry. JSON-output mode. | 1-2 bursts |
| T3 | `route_to_domain.v1` builtin tool | Single-domain routing primitive. Wraps `delegate.v1`. Emits `domain_routed` audit event. | 1 burst |
| T4 | Full routing engine | Hardcoded `handoffs.yaml` + learned `learned_routes.yaml` + the orchestrator agent role + birth. | 2-3 bursts |
| T5 | `domain_orchestrator` agent role + birth | New companion-genre role with the routing tool in its constitution. Birth a singleton instance. | 1 burst |
| T6 | Cross-domain handoff coordinator | When sub-intents span domains, coordinate sequencing + parallel dispatch + result aggregation. | 2 bursts |
| T7 | Frontend Orchestrator pane | Operator-facing surface: see routing decisions, override, tune confidence thresholds, edit handoffs.yaml. | 2 bursts |
| T8 | Health surface | `/orchestrator/status` returning per-domain readiness + routing stats. | 1 burst |

Total estimate: 11-13 bursts across T1-T8.

## Consequences

**Positive:**

- Single operator-facing front door. The Chat tab (ADR-0047)
  routes to the right domain without the operator picking.
- Domain rollout is independent. Adding D5 Smart Home doesn't
  require code changes to the orchestrator — drop a manifest in
  `config/domains/`, mark status=live when ready.
- Routing decisions are first-class audit chain entries
  (`domain_routed`). Forensic replay covers "why did this go to X?"
- Hardcoded + learned dual-rail respects the operator's design
  preference (B277).
- Trust dial discipline holds: routing IS a delegate call, gated
  by the same constitution + posture as any other delegate.

**Negative:**

- Adds one more substrate layer between operator and agents.
  Latency cost: ~one LLM call per utterance for decomposition.
  Mitigated by caching frequent intents (queued for T4).
- Domain manifests are operator-edited YAML — bad edits could
  break routing. Validated at boot with clear error messages.
- LLM-driven decomposition has a confidence floor (0.6 default).
  Ambiguous utterances surface back to operator — slower than
  blind routing but more honest.

**Neutral:**

- The registry doesn't define agents — it references them. Agents
  are still birthed via the standard `/birth` flow.
- The orchestrator doesn't replace direct chat with a specific
  agent. Operators who want to talk to Kraine directly can still
  `@Kraine` in the chat tab; the orchestrator only fires on
  un-targeted utterances.
- ELv2 license discipline holds: every manifest tracks
  `depends_on_substrate` + `depends_on_connectors` so future
  packagers see exactly what each domain needs.

## What this ADR does NOT do

- **Does not implement the orchestrator agent in T1.** T5 ships
  the role + birth. T1 is just the registry.
- **Does not implement intent decomposition.** T2 ships the
  `decompose_intent.v1` tool. T1's manifests provide the LLM's
  domain enumeration without yet calling the LLM.
- **Does not auto-discover domains from agent registry state.**
  Domain manifests are authored, not inferred. Operators can
  see exactly what the orchestrator knows about.
- **Does not pre-empt direct-tool-call workflows.** Power users
  scripting `fsf agent ...` or hitting the HTTP API directly
  bypass the orchestrator entirely — same as today.
- **Does not encrypt domain manifests.** They're public-config
  files (matching genres.yaml, tool_catalog.yaml). Operator
  personal data stays in the operator profile (ADR-0068).

## See Also

- ADR-0033 (Security Swarm) — first proof of multi-agent chain
- ADR-0047 (Persistent Assistant Chat) — the front door
- ADR-0068 (Personal Context Store) — orchestrator reads operator
  profile to inform routing (e.g., timezone-aware reminders)
- ADR-0072 (Behavior Provenance, queued) — locks the hardcoded vs
  learned boundary
