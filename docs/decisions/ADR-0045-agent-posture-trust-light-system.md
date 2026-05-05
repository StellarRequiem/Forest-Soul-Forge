# ADR-0045 — Agent Posture / Trust-Light System

**Status:** Accepted (2026-05-05). Filed alongside the ADR-0043
plugin grant follow-up (Burst 113a → 113b). Implementation in
Bursts 114-115.

## Context

Forest's existing governance gates (genre floor, initiative
ladder, per-tool `requires_human_approval`, ADR-0043 per-tool
mirroring from Burst 111, constitution-derived constraints) all
operate at the **per-tool** or **per-call** granularity. They
answer the question "should THIS specific dispatch be gated?"
based on pre-declared policy.

What's missing is a **per-agent runtime posture** — an
operator-facing dial that says "regardless of what the agent's
constitution declares, here is how much rope I'm extending to
this agent right now." The use cases that surface this gap:

1. **Trust ramp-up.** A newly-born agent should run on a tighter
   leash than the same agent six months and 1,000 successful
   dispatches later. Today the operator has to either
   over-approve at birth (and pay attention to every call) or
   under-approve (and re-birth or hand-edit constitutions to
   loosen the leash). Neither is good.
2. **Active incidents.** When an agent is suspected of
   misbehavior — or just has a flagged contradiction in its
   memory (ADR-0036 T6) — the operator wants a one-click
   "freeze, stop dispatching anything mutating" without archiving
   the agent (which would lose its memory and lineage).
3. **Walk-away mode.** An operator running a long-horizon
   scheduled task wants to GREEN-light an agent for a fixed
   window so the approval queue doesn't pile up overnight, then
   return to YELLOW when they're back at the keyboard.
4. **Constitutional vs. operational separation.** The
   constitution declares the agent's CAPABILITY ceiling — what
   it can ever do. Posture declares CURRENT TRUST — how much of
   that ceiling the operator extends right now. These are
   different concepts that have been conflated in the per-tool
   approval map.

The existing ADR-0033 security genre tiers
(`security_low`/`security_mid`/`security_high`) provide a
similar mechanism but at the **agent role** layer, set at birth
and immutable. They cannot represent runtime trust changes —
they're part of the constitution_hash, which is immutable per
agent (CLAUDE.md architectural invariant).

This ADR specifies a **traffic-light posture system** as a new
governance primitive that lives ALONGSIDE the existing gates,
not on top of them. Posture is mutable runtime state — like
`agents.status` (active/archived) — that the dispatcher consults
as the OUTER gate before any other policy fires.

## Decision

Forest adopts a three-state per-agent posture:

| State    | Semantic                                                    |
| -------- | ----------------------------------------------------------- |
| `green`  | Honor the agent's existing per-tool / per-genre / per-grant policy as-is. Posture adds no override. |
| `yellow` | Force `pending_approval` on every dispatch with `side_effects != read_only`, regardless of the agent's per-tool config. The "I'm watching" mode. |
| `red`    | Refuse every dispatch with `side_effects != read_only` outright (not pending — refused). The agent can still think, read memory, read code, emit memory_write to private scope. The "this agent is on probation" mode. |

The default posture for new agents is `yellow`. Operators
explicitly raise to `green` after observed-good behavior or
explicitly drop to `red` for incidents.

### Storage (schema v15)

Posture lives as a new column on the `agents` table:

```sql
ALTER TABLE agents ADD COLUMN posture TEXT NOT NULL
    DEFAULT 'yellow'
    CHECK (posture IN ('green', 'yellow', 'red'));
```

Existing rows migrate to `posture='yellow'` (the current de-facto
behavior for non-`security_high` agents — they gate per-tool
config; posture=yellow doesn't change that).

History is captured by the audit chain (one event per change).
There is no `agent_posture_history` table — the chain is the
source of truth, the column is the indexed view.

### Audit event

One new event type:

```
agent_posture_changed:
  instance_id:     str
  prior_posture:   str  ('green' | 'yellow' | 'red')
  new_posture:     str  ('green' | 'yellow' | 'red')
  set_by:          str | None  # operator id when authenticated
  set_at:          str  # ISO8601 UTC
  reason:          str | None  # operator-supplied free text
```

This is the 68th audit event type. It composes with existing
events: a posture change followed by a refused tool call
produces a clean forensic chain.

### Dispatcher integration

A new `PostureGateStep` is added to the governance pipeline.
Its position is **after every existing step** (after
`ApprovalGateStep`) — it's the outermost authority. Pipeline
order becomes:

1. HardwareQuarantineStep
2. TaskUsageCapStep
3. ToolLookupStep
4. ArgsValidationStep
5. ConstraintResolutionStep
6. PostureOverrideStep
7. GenreFloorStep
8. InitiativeFloorStep
9. CallCounterStep
10. McpPerToolApprovalStep (Burst 111)
11. ApprovalGateStep
12. **PostureGateStep** ← new

The step reads `dctx.agent_posture` (populated by the dispatcher
before the pipeline runs, same pattern as
`dctx.mcp_registry`). Behavior:

```
if posture == 'red' and tool.side_effects != 'read_only':
    return REFUSE(reason='agent_posture_red')
if posture == 'yellow' and tool.side_effects != 'read_only':
    return PENDING(gate_source='posture_yellow')
# posture == 'green' or read-only: GO (let upstream verdict stand)
```

Why outermost: posture overrides upstream verdicts. A posture-
green agent doesn't bypass per-tool gating (the upstream step
already returned its own verdict and posture is GO). A
posture-yellow agent ELEVATES an upstream GO to PENDING. A
posture-red agent ELEVATES anything mutating to REFUSE, even if
upstream said GO.

When upstream returned PENDING and posture is YELLOW, the
PENDING is preserved — the operator already needed to approve
it; YELLOW doesn't change that.

When upstream returned REFUSE for any reason, posture doesn't
re-evaluate — the refusal stands with the original reason. No
double-refusal.

### Operator surface

**HTTP:** `POST /agents/{instance_id}/posture` body
`{posture: 'green'|'yellow'|'red', reason?: str}`. Gated by
`require_writes_enabled` + `require_api_token`.

**CLI:** `fsf agent posture set <instance_id> --tier green|yellow|red [--reason "..."]`.
Hits the HTTP endpoint.

**Frontend:** A new dial widget in the Agents tab — three radio
buttons or a segmented control. Updates live via the same SSE
infrastructure existing fields use. Shows current posture +
last change time.

### Interaction with constitution_hash

Posture is **runtime state, not constitution**. It does NOT
participate in `constitution_hash`. Changing posture does not
invalidate verification. Same posture as `agents.status`,
`agents.flagged_state`, etc. — these are operator-state columns
that live alongside the immutable identity hash.

This is critical: if posture were part of the constitution, the
"trust ramp-up" use case would require re-birthing the agent,
which loses memory and lineage. The whole point of posture is
to be cheap to flip.

### Interaction with per-grant trust_tier (ADR-0043 follow-up)

The grants table from Burst 113a stores a `trust_tier` field
per (agent, plugin) pair, with values matching the posture
colors. ADR-0045's PostureGateStep checks BOTH:

1. The agent's posture (column on `agents`)
2. The per-grant trust_tier for the specific plugin being called
   (via mcp_call.v1)

Precedence: **`red` always dominates**, then `yellow`, then
`green`. So:

- Agent green + grant green = GREEN (no override)
- Agent green + grant yellow = elevate that mcp_call to PENDING
- Agent yellow + grant green = elevate non-read-only to PENDING
  (agent posture wins for non-MCP tools; grant green doesn't
  rescue non-MCP)
- Agent red + grant green = REFUSE non-read-only (red dominates)
- Agent yellow + grant red = REFUSE that mcp_call (red dominates
  per-grant)

Per-grant trust_tier enforcement is implemented as part of
PostureGateStep when the dispatched tool is `mcp_call.v1` —
read `dctx.args["server_name"]` and consult
`dctx.plugin_grants_view[server_name].trust_tier`.

## Implementation tranches

| # | Burst | Description |
|---|---|---|
| T1 | 114 | Schema v15 migration + agents.posture column. PostureGateStep added to pipeline. PostureGateStep reads agent posture from registry but enforces ONLY agent-level posture (no per-grant interaction yet). |
| T2 | 114b | HTTP endpoint POST /agents/{id}/posture + audit event emit. CLI subcommand `fsf agent posture set`. Frontend dial. |
| T3 | 115 | Per-grant trust_tier enforcement folded into PostureGateStep (the precedence table above becomes the live behavior). |
| T4 | 115b | Tests for the precedence matrix (16 combinations: 3 agent postures × 3 grant tiers + read-only short-circuits). |

## Out of scope (deferred to amendments)

- **Operator-session posture (Scope 3 from the planning chat).**
  Global "auto-approve everything for the next hour" dial.
  Useful but a separate concept — the AGENT's posture stays
  YELLOW; the SESSION dial just auto-approves the queue. Filed
  as ADR-0045-amendment-1 when the use case is concrete.
- **Programmatic posture changes.** Agents downgrading
  themselves on suspected hallucination (ADR-0036 verifier loop
  could plausibly drop posture to RED on a high-confidence
  flag). Possible but requires careful design — agents should
  not self-elevate, only self-demote. Filed as
  ADR-0045-amendment-2.
- **Posture audit policy.** Should `red→green` transitions
  require a higher authority than `yellow→green`? Currently no;
  any operator with write access can set any posture. Filed as
  ADR-0045-amendment-3 if the multi-operator scenario becomes
  real.
- **Time-bounded posture.** "GREEN for the next 4 hours, then
  auto-revert to YELLOW." Useful for the walk-away case but
  adds a scheduler dependency that's deferred.

## Consequences

**Positive:**

- Operators get a coarse, immediate trust dial that doesn't
  require touching constitutions.
- The per-tool/per-genre gating semantics stay unchanged —
  posture is purely additive.
- Audit chain captures every posture change, so the forensic
  question "was this agent on a tight leash when it dispatched
  X?" becomes a chain query.
- Composes cleanly with the Burst 113a grant trust_tier field
  — the storage already exists; T3 wires the enforcement.
- Maps directly onto a clear UI metaphor (traffic light) that's
  simpler than the existing per-tool config matrix.

**Negative:**

- Adds a new pipeline step + new schema column + new audit
  event = three more surfaces to keep coherent on every
  governance change.
- The precedence table (agent × grant × per-tool × genre) is
  getting cognitively heavy. Documentation burden grows; ADR-0040
  trust-surface decomposition will likely need an amendment to
  call out the new gate.
- "YELLOW elevates everything to PENDING" can flood the
  approval queue if the operator forgets they're in YELLOW
  while the agent is doing repetitive work. The queue UI will
  need to stay performant under N=1000+ pending tickets.

**Neutral:**

- Default posture for existing agents is `yellow`, which matches
  the de-facto behavior of "every per-tool config is honored,
  most mutating tools gate." Migration is a no-op semantically.

## References

- ADR-0007 — Constitution as immutable hash (the reason posture
  cannot be in the constitution).
- ADR-0019 — Tool dispatch + governance pipeline.
- ADR-0021-amendment — Genre.max_initiative_level (the closest
  prior gate that's runtime-mutable, but it's per-genre not per-
  agent).
- ADR-0033 — Security genre tiers (the closest prior approach,
  but birth-time only).
- ADR-0036 — Verifier loop + flagged_state (the closest prior
  example of operator-set runtime state on agents).
- ADR-0038 — Companion harm model (motivates the RED state for
  agent-side interventions).
- ADR-0040 — Trust-surface decomposition rule.
- ADR-0043 — MCP plugin protocol.
- ADR-0043 follow-up #2 (Burst 113a) — `agent_plugin_grants`
  table with forward-compat `trust_tier` field that this ADR
  starts consulting.

## Credit

The traffic-light formulation surfaced from a chat with Alex
(2026-05-05) about giving operators a clearer trust dial than
the per-tool approval matrix. The kernel-grade framing — that
posture is a new governance primitive that belongs IN the
kernel rather than as one-off knobs — came out of the same
conversation, in the context of the v0.6 kernel positioning
(ADR-0044, queued).
