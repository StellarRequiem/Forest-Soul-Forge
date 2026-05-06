# ADR-0047 — Persistent Assistant Chat Mode (Single-Agent)

**Status:** Proposed (2026-05-05). Pairs with ADR-0048 (Computer Control
Allowance). Userspace-only delivery — uses existing kernel ABI
surfaces (ADR-003Y conversation runtime, ADR-0022 memory subsystem,
ADR-0045 posture, ADR-0043 plugin grants) without modifying any of
them.

## Context

ADR-003Y shipped a multi-agent conversation runtime (rooms, @mention
chains, cross-domain bridges, ambient nudges, lazy summarization).
Powerful for the Security Swarm, the SW-track triune, and any
multi-participant workflow. But the surface is operator-grade: an
operator who wants "an assistant they can talk to" has to first birth
a single agent, create a room, add the agent as a participant, then
start chatting. The Y3 multi-agent affordances (resolution order,
@mention chains, ambient quotas) add overhead that doesn't help in
the one-on-one case.

The 2026-05-05 outside review explicitly flagged this:

> Not "set and forget" for non-technical users yet (despite claims).
> Setup involves Python/Docker/Ollama; frontend is vanilla
> JS/operator-grade.

The user followed up with a direct ask:

> the chat function still has some bugs in it, there needs to be a
> delete room option and there still errors when trying to
> communicate to agents, lets see about making the chat function
> designated to 1 persistent agent that can do whatever the user
> requests and has its own customizable allowance for little or full
> control over computer functions.

The bugs (delete-room UX, communication errors) closed in B142/B143/
B144/B145. The structural ask — single-agent persistent chat with
configurable computer-control — is what this ADR addresses.

The agent runtime (ADR-002 DNA, ADR-0004 constitution, ADR-0021
genres, ADR-0045 posture) is already built around per-agent identity.
The conversation runtime (ADR-003Y) already supports persistent
single-room work (the multi-agent affordances are opt-in, not
required). The memory subsystem (ADR-0022) already supports per-agent
persistent context across sessions. **All the substrate exists; what's
missing is a UX mode that composes it the way ChatGPT / Claude users
expect.**

This ADR adds that mode as a strictly additive userspace feature.
ADR-003Y multi-agent rooms continue to work unchanged.

## Decision

Add a **Persistent Assistant** mode to the Chat tab as an alternate
UX, backed by **one dedicated agent born once per operator**, holding
the conversation in a single long-lived conversation room.

### Decision 1 — Userspace-only delivery

This work ships as:

- A new mode in the existing vanilla-JS frontend (`frontend/js/`)
- Configuration conventions (one conversation per operator marked
  `domain="assistant"` by convention)
- A new agent-persona template in `config/constitution_templates.yaml`
  (the assistant's role_base)
- ADR-0048 ships the computer-control plugin separately

**ZERO changes to kernel ABI.** The seven v1.0 ABI surfaces (per
ADR-0044 Decision 3) — tool dispatch protocol, audit chain schema,
plugin manifest schema, constitution.yaml schema, HTTP API contract,
CLI surface, schema migrations — all stay unchanged. This means:

- A future external integrator can ignore this entirely; their own
  distribution can implement single-agent chat differently or skip
  it
- The assistant + computer-control can be reverted without touching
  the kernel
- The kernel/userspace boundary doc (`docs/architecture/
  kernel-userspace-boundary.md`) doesn't move

### Decision 2 — Agent source: new dedicated agent born on first use

When an operator opens the Persistent Assistant mode for the first
time, the frontend triggers a birth flow with these defaults:

- Role: `assistant` (new role definition added to
  `config/trait_tree.yaml`; userspace-config addition, kernel ABI
  unchanged because the trait tree YAML is operator-customizable per
  the kernel/userspace boundary doc)
- Genre: `companion` (existing genre; `read_only` risk floor +
  `private` memory ceiling; matches ADR-0038 companion harm model)
- Default trait values: high empathy, high directness, moderate
  caution, high thoroughness — emphasize responsiveness +
  helpfulness without sacrificing the audit trail
- Operator can adjust trait sliders before clicking "birth my
  assistant"; the constitution_hash is locked at birth as usual

**Why a new dedicated agent (not "promote an existing specialist"):**

- Specialists already have role-specific constitutions optimized for
  their job (status_reporter generates briefs, dashboard_watcher
  polls health, etc.). Repurposing one as a chat agent dilutes its
  scope.
- A dedicated assistant agent has a stable identity (instance_id +
  DNA + constitution_hash) that persists across sessions, audits,
  and computer-control grants. The grants tied to it (per ADR-0048)
  stay tied to it.
- Forest's identity model (content-addressed DNA, immutable
  constitution) treats agents as personas. The assistant deserves
  its own persona, not a borrowed one.

**One assistant per operator — by convention, not enforcement.** If
an operator births a second assistant (e.g., to test trait
variations), nothing prevents it. The frontend just remembers which
instance_id is "the active assistant" for a given operator (in
localStorage) and routes the Persistent Assistant tab to it. Other
assistants surface in the multi-agent Chat tab as regular agents.

### Decision 3 — Persistence: single long-lived conversation per operator

The assistant chat lives in one conversation row, marked by
convention with `domain="assistant"` and never auto-archived. Reuses
the existing ADR-003Y conversation runtime end-to-end:

- Operator and assistant both append turns
- Y7 lazy summarization purges old turn bodies after the retention
  window, leaving body_hash for tamper-evidence
- The audit chain captures every turn as `conversation_turn`
- The assistant uses `memory_recall.v1` (existing ADR-0022 tool) to
  retrieve persistent facts across sessions — its own memory plus
  any consented cross-agent memory

**Why one conversation, not separate sessions:**

- ADR-003Y's body_hash + Y7 summarization already solves the
  "context grows unbounded" problem
- A single conversation makes "what did we talk about last week"
  trivially answerable via memory recall
- Forest's audit chain treats the conversation as one continuous
  artifact — splitting into sessions adds no kernel-shape value
- ChatGPT / Claude users mentally model "the chat" as one thread
  across time; matching that mental model is a UX win

**Retention policy default:** `full_indefinite` for the assistant
conversation. The operator can change this at conversation creation
time via the existing retention selector.

### Decision 4 — Coexistence with multi-agent chat

The existing multi-agent Chat tab (rooms, @mention chains, bridges,
ambient nudges) **stays unchanged**. The Persistent Assistant is a
new mode reachable via a tab toggle or a sidebar entry. Same Chat
infrastructure, different UX layer.

This means:

- Security Swarm operators continue to use multi-agent rooms for
  swarm coordination
- SW-track triune workflows (Architect / Engineer / Reviewer)
  continue unchanged
- Cross-domain bridges, ambient mode, retention sweeps — all
  available where they're useful
- New Persistent Assistant mode is the default landing for Chat tab
  (per the user's stated direction); multi-agent rooms reachable via
  the existing rooms rail

### Decision 5 — Settings panel exposes posture + allowances

The Persistent Assistant mode includes a settings panel showing:

- Agent identity (instance_id, role, genre, DNA, constitution_hash —
  same surface as the Agents tab character sheet)
- Trait values (read-only post-birth per Forest's identity model)
- Posture dial (green / yellow / red — ADR-0045 substrate, mutable
  at runtime, every change audited)
- Computer-control allowances (ADR-0048 substrate — per-category
  toggles backed by per-tool grants)
- Memory consent grants (existing ADR-0027 substrate, exposed in
  the Memory tab today; mirrored here for assistant-context UX)

The settings panel is the operator's "what can my assistant do right
now" glance. Posture flips and grant changes take effect immediately
— no restart required (per ADR-0045 + ADR-0043 substrate).

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Frontend mode | New `assistant` mode in Chat tab. Tab toggle: "Assistant / Rooms". Renders single-conversation thread + settings sidebar. | 1 burst |
| T2 | Birth flow | First-use modal: trait sliders, genre confirmation (companion), birth button. Persists assistant instance_id to localStorage. | 1 burst |
| T3 | Conversation init | Auto-create the assistant's persistent conversation on first chat (POST /conversations with `domain="assistant"`, `retention_policy="full_indefinite"`); auto-add the assistant agent as participant. | 0.5 burst |
| T4 | Settings panel | Sidebar UI: identity card, posture dial, allowance toggles (ADR-0048), memory consents. | 1 burst |
| T5 | Memory integration | Wire `memory_recall.v1` calls into the assistant's prompt-building so it sees persistent facts across sessions. | 0.5 burst |
| T6 | Role definition | Add `assistant` role to `config/trait_tree.yaml`; add role_base to `config/constitution_templates.yaml`; add archetype kit to `config/tool_catalog.yaml` (defaults to read-heavy + memory). | 0.5 burst |

Total estimate: 4-5 bursts. Plus ADR-0048 implementation (separate
arc).

## Consequences

**Positive:**

- Closes the "set-and-forget for non-technical users" gap the outside
  review flagged
- Delivers ChatGPT / Claude-style UX on the Forest substrate without
  competing with those products on cloud features (the differentiator
  is local-first + auditable + governance-pipeline-gated)
- Strictly additive: multi-agent rooms (ADR-003Y) keep working;
  Security Swarm + SW-track unaffected
- ZERO kernel ABI impact — keeps ADR-0044's v1.0 commitment intact
- Sets up the "another outside analysis" arc (the user's stated
  follow-on) with a polished surface to evaluate

**Negative:**

- Adds operator-facing surface area (a new mode + settings panel)
  that needs to be documented, supported, and tested
- "One assistant per operator" is a convention not enforced by the
  kernel; an operator with multiple assistant agents will see UX
  oddities (which one is "the" assistant?). Acceptable for v0.6 —
  flag in docs.
- Trait values for the assistant are locked at birth (per Forest's
  identity model). An operator who wants to "evolve" the assistant's
  personality has to birth a new one. This is by design; communicate
  in the UX.
- Computer-control work (ADR-0048) is separate but the assistant
  isn't fully useful without it. ADR-0047 ships in pieces; ADR-0048
  in parallel pieces.

**Neutral:**

- Memory grows unbounded over a long-lived assistant conversation.
  Y7 lazy summarization handles this — old turn bodies purge,
  body_hash retains for tamper-evidence. No new mechanism needed.
- The assistant participates in the same audit chain as every other
  agent. No special "private chat" exemption — provenance applies.
- The assistant's `companion` genre means `read_only` risk floor
  (no shell exec, no file writes) BY DEFAULT. Computer-control
  capabilities (per ADR-0048) come via per-(agent, plugin) grants
  that augment the constitution; they don't bypass it.

## What this ADR does NOT do

- **Does not add a new kernel ABI surface.** The seven v1.0 surfaces
  are unchanged.
- **Does not deprecate or remove ADR-003Y multi-agent rooms.** They
  continue to ship and work as-is.
- **Does not change the Chat tab's URL routing or the daemon's
  endpoints.** All existing API surfaces unchanged.
- **Does not specify the computer-control tools.** Those are
  ADR-0048's scope.
- **Does not promise persistence across daemon restarts beyond what
  ADR-003Y already promises.** The assistant conversation row +
  turns persist in registry; daemon restart re-reads them via the
  existing rebuild path.
- **Does not address voice interface, real-time A/V, or accessibility
  modes.** ADR-0038 Companion harm model + future ADRs cover those
  separately.

## References

- ADR-003Y — Conversation runtime (the substrate this ADR composes)
- ADR-0022 — Memory subsystem (per-agent persistent facts)
- ADR-0021 — Role genres (companion genre claims this role)
- ADR-0027 — Memory privacy contract (consent grants)
- ADR-0038 — Companion harm model (the assistant inherits the
  refusal + disclaimer scaffolding)
- ADR-0044 — Kernel positioning + SoulUX flagship (the
  kernel/userspace boundary this ADR respects)
- ADR-0045 — Agent posture / trust-light system (the runtime trust
  dial the settings panel exposes)
- ADR-0048 — Computer control allowance (the action capabilities
  this ADR's assistant uses)
- 2026-05-05 outside review (Cowork session 87fd4f13) — flagged
  the "set-and-forget for non-technical users" gap that this ADR
  addresses

## Credit

The "make the chat function designated to 1 persistent agent" framing
came from the operator (Alex) in the 2026-05-05 Cowork session, after
B142/B143/B144 closed the chat-tab bug arc and B145 closed the
delete-room UX arc. The userspace-only framing came from the kernel/
userspace boundary discipline established in ADR-0044.
