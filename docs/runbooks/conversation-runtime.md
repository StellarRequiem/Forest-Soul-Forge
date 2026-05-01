# Conversation runtime — operator runbook

**ADR:** [ADR-003Y](../decisions/ADR-003Y-conversation-runtime.md)
**Status:** Accepted (Y1–Y7 shipped end-to-end 2026-04-30)
**Smoke test:** `live-test-y-full.command`

This runbook walks the operator through every endpoint in the
conversation runtime: opening rooms, adding agents, firing turns,
bridging across domains, ambient nudges, and lazy retention sweeps.

## What it is

The conversation runtime is the substrate that lets agents and
operators talk in multi-turn, multi-agent rooms. It sits beside the
single-shot dispatch path:

- Single-shot: `POST /agents/{id}/tools/call` — stateless, one tool
  call, one response.
- **Conversational**: multi-turn rooms with persisted history,
  @mention-driven addressing, cross-domain bridges, opt-in ambient
  nudges, and audit-trailed retention windows.

Every turn passes through the R3 governance pipeline (constitution
constraints, genre kit-tier ceiling, approval gates, hardware
quarantine). Every turn body has a SHA-256 `body_hash` that survives
even after Y7 lazy summarization purges the original content.

## The seven phases (Y1–Y7)

| Phase | What it adds | Files |
|:---:|---|---|
| Y1 | Schema + CRUD endpoints | `daemon/routers/conversations.py` |
| Y2 | `auto_respond` for single-agent rooms | same |
| Y3 | Multi-agent `@mention` chain | `conversation_resolver.py` |
| Y3.5 | Keyword-rank fallback when no addressing | `conversation_resolver.py` |
| Y4 | Cross-domain bridge endpoint | `bridge_participant` route |
| Y5 | Ambient nudges (opt-in, quota-gated) | `ambient_nudge` route |
| Y6 | Frontend Chat tab | `frontend/js/chat.js` |
| Y6.1 | Frontend wires for Y4 + Y5 + Y7 | same |
| Y7 | Lazy retention summarization sweep | `conversations_admin.py` |

## Quick start: open a room, add an agent, fire a turn

Assumes the daemon is running (`./start.command` or `docker-up.command`).

```bash
DAEMON=http://127.0.0.1:7423
TOKEN=$FSF_API_TOKEN  # if writes are gated

# 1. Open a room
ROOM=$(curl -s -X POST $DAEMON/conversations \
  -H "Content-Type: application/json" \
  -H "X-FSF-Token: $TOKEN" \
  -d '{"domain": "coding", "operator_id": "alex"}' \
  | jq -r '.conversation_id')
echo "room: $ROOM"

# 2. Add an agent (must already exist; birth one first if needed)
AGENT_ID=...  # an instance_id from /agents
curl -X POST $DAEMON/conversations/$ROOM/participants \
  -H "X-FSF-Token: $TOKEN" \
  -d '{"instance_id": "'$AGENT_ID'"}'

# 3. Operator turn that triggers an agent reply
curl -X POST $DAEMON/conversations/$ROOM/turns \
  -H "Content-Type: application/json" \
  -H "X-FSF-Token: $TOKEN" \
  -d '{
    "speaker": "alex",
    "body": "What is the cleanest way to refactor this?",
    "auto_respond": true,
    "max_response_tokens": 400
  }'
```

Returns the operator's turn + the agent's response in one payload:

```json
{
  "operator_turn":  { "turn_id": "t-...", "body_hash": "..." },
  "agent_turn":     { "turn_id": "t-...", "body": "...", "model_used": "qwen2.5:7b" },
  "agent_turn_chain": [ ... ],
  "chain_depth":    1,
  "agent_dispatch_failed": false
}
```

Easier path: drive the **Chat tab** in the frontend at
`http://127.0.0.1:5173`. Every endpoint above has a UI control.

## Resolution order — who responds when

When an operator fires `auto_respond: true`, the runtime decides
who answers in this priority order:

1. **Explicit addressing.** If `addressed_to: ["i1", "i2"]` is set,
   only those participants respond, in that order.
2. **`@mentions` in the body.** If the body contains `@AgentName`
   tokens that match room participants, those agents respond in
   mention-order, deduped, case-insensitive fallback.
3. **Y3.5 keyword-rank fallback.** When neither path hits, tokenize
   the body and pick the participant whose `(agent_name + role)`
   tokens overlap the body the most (BM25-lite). Falls back to the
   first participant if everything ties.

After an agent responds, its reply is parsed for new `@mentions`;
those become the next addressees. **Self-mention is filtered**: an
agent that mentions itself is not re-dispatched (DoS-via-self-pass
protection).

The chain stops when:
- No new `@mentions` parsed (natural end), OR
- `max_chain_depth` reached (default 4 per ADR-003Y), OR
- An agent dispatch returns non-success.

## Cross-domain bridge (Y4)

Bringing an agent from one operator-defined domain into another:

```bash
curl -X POST $DAEMON/conversations/$ROOM/bridge \
  -H "Content-Type: application/json" \
  -H "X-FSF-Token: $TOKEN" \
  -d '{
    "instance_id": "agent-from-other-domain",
    "from_domain": "research",
    "operator_id":  "alex",
    "reason": "needs the research context for this build decision"
  }'
```

Refused with 400 if `from_domain` matches the room's domain — that's a
same-domain join, use `/participants` instead. The bridge emits a
`conversation_bridged` audit event with operator_id + reason for
attribution.

## Ambient nudges (Y5)

A nudge invites an agent to surface ONE proactive contribution rather
than reply to a recent message:

```bash
curl -X POST $DAEMON/conversations/$ROOM/ambient/nudge \
  -H "Content-Type: application/json" \
  -H "X-FSF-Token: $TOKEN" \
  -d '{
    "instance_id":   "the-agent",
    "operator_id":   "alex",
    "nudge_kind":    "check_in",
    "max_response_tokens": 300
  }'
```

**Two structural gates** before any dispatch:

1. **Constitution opt-in.** The agent's constitution YAML must have
   `interaction_modes.ambient_opt_in: true`. Default is false —
   ambient is structurally opt-in even when the genre would permit
   it. Without this, the endpoint returns 403.
2. **Quota.** Per-agent-per-room quota in last 24h:
   - `minimal` rate (default): 1 nudge/day
   - `normal`: 3
   - `heavy`: 10

   Set via `FSF_AMBIENT_RATE` env var. Returns 429 when exhausted.

Quota is counted by walking the audit chain for `ambient_nudge`
events tagged with the same (instance_id, conversation_id) tuple
in the last 24h.

## Lazy retention sweep (Y7)

Conversations have a `retention_policy` set at creation time:

| Policy | Window |
|---|---|
| `full_7d` | 7 days (default) |
| `full_30d` | 30 days |
| `full_indefinite` | never auto-summarize |

After the window expires, an operator-triggered sweep summarizes the
turn body via `llm_think.v1` and replaces the body with the summary —
but `body_hash` (SHA-256 of the original) stays for tamper-evidence.

```bash
# Dry-run: what WOULD be swept?
curl -X POST $DAEMON/admin/conversations/sweep_retention \
  -H "Content-Type: application/json" \
  -H "X-FSF-Token: $TOKEN" \
  -d '{"dry_run": true, "limit": 100}'

# Actual sweep
curl -X POST $DAEMON/admin/conversations/sweep_retention \
  -H "Content-Type: application/json" \
  -H "X-FSF-Token: $TOKEN" \
  -d '{"dry_run": false, "summary_max_tokens": 200, "limit": 100}'
```

The summarizer is the FIRST agent participant of the room. If the
room is operator-only (no agent participants), the turn is skipped
with `status="no_summarizer_agent"`.

The frontend Chat tab has a `Sweep` button that runs dry-run first,
shows the candidates, and only fires the real sweep if the operator
confirms.

## Audit events emitted

| Event | When |
|---|---|
| `conversation_started` | room created |
| `conversation_status_changed` | status transitions (active/idle/archived) |
| `conversation_archived` | specifically when status → archived |
| `retention_policy_changed` | retention window changes |
| `conversation_participant_joined` | non-bridge join |
| `conversation_bridged` | cross-domain bridge invitation |
| `conversation_participant_left` | participant removed |
| `conversation_turn` | every turn (operator OR agent) |
| `ambient_nudge` | every Y5 nudge — quota counter source |
| `conversation_summarized` | every Y7 sweep entry (success or skip) |

All emit through the same audit chain that lifecycle / dispatch
events use.

## End-to-end smoke

```bash
./live-test-y-full.command
```

This drives all 7 phases in 10 steps, prints results, and verifies
the audit chain coherence (operator turn → llm_think dispatched →
llm_think succeeded → agent turn). Last reliable run: 2026-04-30.

## Known limits / by-design caveats

- **`max_chain_depth=4` default.** Higher values risk runaway chains.
  Operators can raise via the `max_chain_depth` field on POST /turns.
- **Ambient quota is per-(agent, room).** Same agent in two rooms has
  two independent quotas. By design — different rooms = different
  tasks.
- **Retention sweep is operator-triggered.** No daemon-side scheduler.
  The operator picks when to run sweeps. Y7.1 (deferred to v0.3) may
  add a periodic asyncio task.
- **Body purge is irreversible.** Once `summarize_and_purge_body`
  runs, the original body is gone. `body_hash` proves tamper-
  evidence; the summary is what's left.

## Where to dig deeper

- **ADR**: `docs/decisions/ADR-003Y-conversation-runtime.md`
- **Schema**: `daemon/schemas/conversations.py` — Pydantic shapes for
  every request + response
- **Resolver**: `daemon/routers/conversation_resolver.py` — pure
  resolution logic, unit-tested in `test_conversation_resolver.py`
- **Helpers**: `daemon/routers/conversation_helpers.py` — prompt
  builders + ambient gate readers, unit-tested in
  `test_conversation_helpers.py`
- **Sweep**: `daemon/routers/conversations_admin.py` — Y7 retention
  sweep, helpers unit-tested in `test_conversations_admin.py`
