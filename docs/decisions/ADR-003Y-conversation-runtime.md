# ADR-003Y — Conversation Runtime

- **Status:** Proposed
- **Date:** 2026-04-29
- **Supersedes:** —
- **Related:** ADR-0008 (Companion genre — the genre that needs this most),
  ADR-0019 (tool execution runtime — conversation turns route through the
  same dispatcher for tool-of-the-turn calls), ADR-0021 (role genres —
  conversation participation declared per-genre), ADR-0022 (memory
  subsystem — turn bodies stored as private-scope episodic memory),
  ADR-0027 (memory privacy contract — retention windows extend the
  contract to conversation bodies), ADR-0033 (Security Swarm — proves the
  per-domain mental model that this ADR generalizes), ADR-003X (open-web
  tool family — same architectural posture: additive primitives + chain
  integration + opt-in defaults).

## Context

Every existing agent in Forest is conceptually `wake up, run a skill,
go idle.` The security swarm is `task_loop`. The Tool Forge invokes
agents in `one_shot`. There is **no first-class conversation primitive**.

Companions need one. A Therapist agent that resets to zero context
between turns isn't a Therapist — it's an autocomplete with an
empathetic voice. The Companion genre's whole point (private memory,
local-only providers, persistent identity) was to support exactly this
use case, but the runtime to make it happen was deferred.

The brief is to ship a **conversation runtime** that:

1. Lets an operator hold a multi-turn conversation with one or more
   agents that maintains identity, memory, and topic across turns
   AND across daemon restarts.
2. Supports **rooms** — multi-agent conversations scoped to an
   operator-defined domain (therapy / coding / building / admin / …).
3. Supports **opt-in ambient nudges** — agents proactively surfacing
   things to the operator at an operator-controlled rate.
4. Audits every turn through the existing chain without leaking
   conversation contents permanently — raw bodies live in a retention
   window; long-term storage is summary-only.

Same architectural posture as ADR-003X: additive primitives, chain
integration, opt-in defaults. Nothing in the existing platform changes
for `task_loop` or `one_shot` agents.

## What this ADR is **not**

To prevent scope creep, four explicit non-claims:

1. **This ADR does not enable inter-realm chat.** Conversations are
   scoped to one operator's local Forest. Cross-realm conversations
   (Realm scope from ADR-0022) require a separate inter-realm handshake
   ADR (filed as a horizon item from the cross-check vision read).

2. **This ADR does not ship a voice/audio runtime for Companions.**
   Audio (the Companion genre's `interactive_session` kit hint) lands
   in a follow-up. v1 is text only.

3. **This ADR does not promise a "general-purpose multi-agent
   debate" framework.** Conversation rooms are scoped to a single
   domain by default; cross-domain bridging requires explicit
   invitation. We're not building Anthropic's swarm-debate research,
   we're building a chat that respects boundaries.

4. **This ADR does not change the security swarm.** The 9 swarm agents
   stay `task_loop`. They could later opt into `conversation` for
   incident-channel collaboration, but that is a follow-up choice, not
   a default.

## Decision

### Interaction modes

Add an `interaction_modes` constitution field. Optional, list-valued,
opt-in. Default: `[one_shot]` for back-compat — every existing agent
stays exactly the same.

```yaml
interaction_modes: [conversation, ambient]
```

Defined values:

| Mode           | What it means                                                      |
|----------------|---------------------------------------------------------------------|
| `one_shot`     | Operator triggers a skill, agent runs, output returns. Today's only mode. |
| `task_loop`    | Agent runs autonomously on a recurring trigger (cron via `schedule` skill). |
| `conversation` | Turn-based exchange with persistent memory + identity across turns. |
| `ambient`      | Background presence; agent may surface proactive turns at operator-set rate. |

The Companion genre's default shifts to `[conversation]`. Operator can
set `[conversation, ambient]` per-agent when they want proactive nudges.

### Conversation primitive

A first-class runtime entity, persisted in registry schema v10:

```sql
CREATE TABLE conversations (
    conversation_id  TEXT PRIMARY KEY,
    domain           TEXT NOT NULL,         -- operator-defined free-text
    operator_id      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_turn_at     TEXT,
    status           TEXT NOT NULL,         -- 'active' | 'idle' | 'archived'
    retention_policy TEXT NOT NULL          -- 'full_7d' (default) | 'full_30d' | 'full_indefinite'
);

CREATE TABLE conversation_participants (
    conversation_id  TEXT NOT NULL,
    instance_id      TEXT NOT NULL,
    joined_at        TEXT NOT NULL,
    bridged_from     TEXT,                  -- domain this agent was bridged from, if any
    PRIMARY KEY (conversation_id, instance_id),
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id),
    FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
);

CREATE TABLE conversation_turns (
    turn_id          TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    speaker          TEXT NOT NULL,         -- operator_id OR instance_id
    addressed_to     TEXT,                  -- comma-joined instance_ids; NULL = whole room
    body             TEXT,                  -- raw body (NULL after retention window expires)
    summary          TEXT,                  -- written when retention window closes
    body_hash        TEXT NOT NULL,         -- SHA256(body); persists after body is purged
    token_count      INTEGER,
    timestamp        TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
);
```

### Domain isolation

Each conversation has an operator-defined `domain` string (free-text;
recommended seeds: `therapy`, `coding`, `builders`, `admin`).

Each agent's constitution declares which domains they participate in:

```yaml
participating_domains: [therapy]
bridge_domains: []                # explicit cross-domain invitation list
```

A conversation in domain `therapy` can only be joined by agents whose
`participating_domains` includes `therapy` — UNLESS an operator
explicitly invokes `POST /conversations/{id}/bridge` to invite an agent
from another domain. The bridge invitation requires the inviting
operator + a reason, emits a `conversation_bridged` audit event, and
the joined agent's row in `conversation_participants` carries
`bridged_from = <other_domain>` so the source is visible forever.

This is the structural defense against "everyone hears everything."
Mass dissemination requires explicit operator action and lands in
the chain.

### Turn orchestration — addressed-first, @mention to pass

Operator sends a turn via `POST /conversations/{id}/turn` with
optional `addressed_to: [instance_id, ...]`.

Resolver order:

1. **`addressed_to` is set** → only those agents respond.
2. **`addressed_to` is empty AND the operator's body @mentions agents
   by name** → those agents respond, in mention order.
3. **Neither addressing nor @mentions** → use `suggest_agent.v1`
   internally to pick the participant whose role best matches the
   turn body. That agent responds.

After an agent responds, its turn body can include `@<agent_name>` to
pass the conversation to a sister. The orchestrator wakes the
@mentioned agent next, which sees both the operator's turn AND the
preceding agent's response.

Round-robin is the **fallback** when nothing addresses anyone — it's
not the default. This avoids the stilted "agents respond in fixed
order regardless of relevance" pattern.

A configurable `max_chain_depth` (default 4) caps how many agent-to-
agent passes happen before the orchestrator stops and waits for the
operator. Prevents an inadvertent infinite-pass between two cooperating
agents.

### Audit posture — chain hashes, retention-window bodies, lazy summaries

**Per-turn chain entry** (`conversation_turn`) carries:
- `turn_id`, `conversation_id`, `speaker`, `addressed_to`
- `body_hash` (SHA256 of the body — persists forever)
- `token_count`
- `model_used` (when speaker is an agent)

The chain entry **never** carries the body itself. Privacy default.

**Body retention** is governed by `conversations.retention_policy`:

| Policy            | Body lifetime                                              |
|-------------------|------------------------------------------------------------|
| `full_7d` (default) | Raw body kept 7 days; then summarized + body deleted.    |
| `full_30d`        | Raw body kept 30 days; then summarized + body deleted.    |
| `full_indefinite` | Raw body kept forever. Operator-deliberate; flagged in UI.|

When the window closes for a conversation, a background pass:
1. Calls the active provider with the turn bodies + a "summarize this
   conversation in N sentences" prompt.
2. Writes the summary to `conversation_turns.summary`.
3. Deletes the bodies from `conversation_turns.body`.
4. Emits one `conversation_summarized` audit event with the
   conversation_id + token-count delta + summary length.

Lazy by design: NO per-turn LLM summary call. Summaries are written
once, when the window expires.

**Operator can extend retention** per conversation via
`POST /conversations/{id}/retention {policy: full_30d, reason: ...}`.
Audited as `retention_policy_changed`.

### Ambient mode — opt-in + operator-set rate

Agents with `ambient` in `interaction_modes` may proactively surface
turns. Two gates:

1. **Per-agent constitution flag** `ambient_opt_in: false` (default
   false). Even agents whose genre permits ambient must explicitly
   opt in via constitution.

2. **Operator-level rate setting** `ambient_rate: minimal | normal | heavy`.
   Per-agent-per-day quotas:

   | Rate    | Proactive turns / day / agent |
   |---------|-------------------------------|
   | minimal | 1                             |
   | normal  | 3                             |
   | heavy   | 10                            |

   Quota tracked per `(agent, conversation, calendar_day)` — a single
   agent can't dominate a single conversation, and can't burn an
   operator's whole quota across all conversations.

Each ambient turn emits a distinct `ambient_nudge` audit event before
the turn lands, so operators can review what their agents are
proactively surfacing.

### Daemon-restart stickiness

Conversations persist in SQLite — they survive daemon restart by
construction. The frontend Chat tab additionally:

- Stores the operator's currently-active `conversation_id` in
  `localStorage`.
- On Chat-tab load, if a stored conversation_id resolves to an active
  conversation in the registry, auto-resumes that view. Operator picks
  up exactly where they left off.

Mid-turn behavior: if the daemon restarts while a turn is in flight
(operator sent, agent hadn't responded yet), the orchestrator on
boot scans for `conversation_turns` rows with `speaker = operator_id`
and no subsequent agent response within the same conversation that's
younger than 5 minutes. Those turns are re-dispatched. The orchestrator
emits a `turn_redispatched` audit event so operators can see when this
fired.

### Frontend Chat tab

New tab beside `Memory` and `Audit`. Components:

- **Conversation list** (left rail): all active conversations the
  operator owns, grouped by domain. Click → load conversation.
- **Room view** (center): scrollable turn history; recent turns
  rendered verbatim, summarized turns rendered as collapsed cards.
- **Participant chips** (top): names of agents in the room; click to
  address; explicit `+ Bridge` button to invite from another domain.
- **Composer** (bottom): textarea + Send. `@AgentName` autocomplete
  for explicit addressing. `addressed_to` chip when one is selected.
- **Retention indicator**: shows current policy + countdown to next
  summarization for this conversation.

No framework — vanilla JS like the existing tabs.

## Phases

| #  | Deliverable                                                                                  |
|----|---------------------------------------------------------------------------------------------|
| Y1 | Schema v10 + Conversation/Turn dataclasses + `conversations` router (CRUD only, no orchestration) |
| Y2 | Single-agent conversation: operator + 1 agent, addressed-only, bodies in retention window   |
| Y3 | Multi-agent rooms within one domain: addressing + @mention pass + suggest_agent fallback    |
| Y4 | Cross-domain bridge endpoint + `bridged_from` audit trail                                   |
| Y5 | Ambient mode: opt-in flag + rate slider + proactive-turn quota enforcement                  |
| Y6 | Frontend Chat tab (vanilla JS); restart-stickiness via localStorage                         |
| Y7 | Lazy summarization background task + retention-policy operator endpoint                     |

Order: Y1 → Y2 → Y3 → Y4 → Y5 → Y6 → Y7. Estimated 2-3 weeks of focused
work. Each phase is independently shippable + live-testable.

## Threat model addendum

Three new threat classes:

### 1. Cross-conversation context bleed (HIGH)

An agent participates in conversations A and B. A is therapy with the
operator's emotional content; B is a coding conversation in the same
session. The agent's runtime context could accidentally fold A's
memory into B's response.

**Mitigations:**
- Memory recall on every turn is filtered by `conversation_id` —
  semantic memory tagged with conv_id A is invisible when answering
  in conv B unless explicitly tagged with both.
- The `conversation_id` is an opaque token, not a topic name —
  operator cannot manipulate it to merge contexts.
- Provider context for each turn is built fresh per conversation,
  not shared.

### 2. Operator dependency / unhealthy attachment (MEDIUM)

A Companion in `conversation` mode with a generous `ambient_rate` and
a `full_indefinite` retention can become emotionally central to the
operator. The cross-check vision read flagged this.

**Mitigations:**
- `ambient_rate` defaults to `minimal` — operators must actively turn
  it up.
- `retention_policy` defaults to `full_7d` — long-term context decays
  unless the operator deliberately extends it.
- Future Companion-genre policy: weekly `attachment_check` ceremony
  event that surfaces "you've spoken with $agent_name N times this
  week, your average session length is X minutes" — operator-facing
  reflection, not a hard gate.

### 3. Cross-domain bridge as exfiltration vector (MEDIUM)

An adversarial agent in a coding conversation gets bridged into a
therapy conversation by an unwary operator and exfiltrates therapy
content into coding-conversation memory.

**Mitigations:**
- Bridge action requires `operator_id + reason` + emits
  `conversation_bridged` event in the chain — visible after the fact.
- Bridged agents land with `bridged_from = <source_domain>` on their
  participant row; the chronicle render highlights them visually.
- Per-agent constitution can declare `bridge_domains: []` (no
  bridges accepted) as a defensive default for sensitive Companions.

## Open questions deferred to v2 / horizon

- **Voice / audio** — text-first in v1. Audio runtime ships in a
  separate ADR.
- **Inter-realm conversation** — same operator across two devices, OR
  two operators sharing a moderated conversation. Requires the
  inter-realm handshake ADR (horizon).
- **Conversation export** — operator wants to download their full
  conversation history as a doc. Trivial extension of the existing
  chronicle render — file separately when the operator asks.
- **Per-conversation cost telemetry** — token-count is in the chain
  per turn; aggregating into a per-conversation cost dashboard is a
  small frontend extension, not a runtime concern.
- **Provider KV-cache reuse** — accepted v1 cost is per-turn full
  context re-send. Provider-level caching is a follow-up optimization
  worth doing once latency is the felt bottleneck.

## Decision summary

Conversation is a first-class runtime primitive, scoped to operator-
defined domains, defaulting to private retention, with opt-in ambient
mode at operator-controlled rates. Architecturally additive: nothing
about today's `task_loop` or `one_shot` agents changes. The phased
rollout (Y1-Y7) ships independently testable chunks over ~2-3 weeks.

This ADR is the contract; phases are the implementation; the
decision is how Forest finally fulfills the original ADR-0008
Companion genre promise without subsuming what makes Forest
distinct (audited, consent-first, local-first, operator-controlled).
