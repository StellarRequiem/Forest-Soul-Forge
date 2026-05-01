# Memory subsystem — operator runbook

**ADRs:** [ADR-0022](../decisions/ADR-0022-memory-subsystem.md) (subsystem),
[ADR-0027](../decisions/ADR-0027-memory-privacy-contract.md) (privacy / scopes)
**Status:** Accepted (v0.1 + v0.2 shipped)

Per-agent memory store with three layers, four scopes, explicit
cross-agent disclosure, and a verified-memory tier (ADR-003X K1).
Every operation lands in the audit chain.

## Three layers

| Layer | Use |
|---|---|
| `episodic` | facts/events with timestamps — "X happened at Y" |
| `semantic` | crystallized knowledge — "the right pattern for Z is W" |
| `procedural` | how-to / playbook — "to do X, do steps 1-3" |

Layer is operator-supplied at write time. Recall can filter by layer
or read across all three.

## Four scopes

| Scope | Who can read | Default for genre |
|---|---|---|
| `private` | The owning agent only | Companion, Guardian, security_high |
| `lineage` | Owner + parent + descendants | Observer, Investigator, Actuator, security_low, security_mid |
| `consented` | Explicit allowlist of agent IDs | Researcher, Communicator |
| `realm` | Any agent in the same realm | Reserved for ADR-0024 H3 federation (not live) |

Each genre has a `memory_ceiling` — agents can write at-or-below their
ceiling but never above. Companion-genre tries to write `lineage` →
`MemoryScopeViolation` raised at write time. Operators can override
with explicit gating, audited.

## Write a memory

Via the `memory_write.v1` tool from inside an agent skill:

```yaml
- id: stash
  tool: memory_write.v1
  args:
    content: "found 3 ERROR matches in /var/log/app.log"
    layer: episodic
    scope: lineage          # default = private; must be ≤ genre ceiling
    tags: [errors, sweep]
```

Or directly from the CLI/HTTP layer (operator-only, debugging path):

```bash
curl -X POST $DAEMON/agents/$AGENT_ID/tools/call \
  -H "X-FSF-Token: $TOKEN" \
  -d '{
    "tool_name": "memory_write",
    "tool_version": "1",
    "session_id": "ops-1",
    "args": {"content": "...", "layer": "episodic"}
  }'
```

## Recall memory

The `memory_recall.v1` tool, with an optional `mode`:

| Mode | Returns |
|---|---|
| `private` (default, v0.1 backstop) | reader's own private rows only |
| `lineage` | reader's private+lineage + lineage-chain peers' lineage rows |
| `consented` | reader's own + any rows where reader's ID is in the consent allowlist |

```yaml
- id: lookup
  tool: memory_recall.v1
  args:
    layer: episodic
    query: "ERROR"          # substring match on content + tags + summaries
    mode: lineage           # or 'private' / 'consented'
    limit: 20
```

**Newest-first ordering** is guaranteed via `ORDER BY created_at DESC,
rowid DESC` (the rowid tiebreaker is what makes the guarantee airtight
under sub-microsecond writes — without it, two appends in the same
microsecond return in arbitrary order).

## Cross-agent disclosure (ADR-0027)

Agents can grant explicit consent for another agent to read a
specific memory entry:

```bash
# Grant
curl -X POST $DAEMON/agents/$OWNER_ID/memory/consents \
  -H "X-FSF-Token: $TOKEN" \
  -d '{
    "entry_id": "e-...",
    "grantee_instance_id": "$RECIPIENT_ID",
    "reason": "needed for the incident triage handoff"
  }'

# List grants
curl $DAEMON/agents/$OWNER_ID/memory/consents

# Revoke
curl -X DELETE $DAEMON/agents/$OWNER_ID/memory/consents/$GRANT_ID
```

Recipients reading a consented entry get a **summary + back-reference**,
NOT the original content (ADR-0027 §minimum-disclosure rule). The
back-reference lets the recipient call `memory_disclose.v1` to fetch
the disclosed copy with explicit audit trail.

## Verified-memory tier (ADR-003X K1)

Operators can promote a memory entry to `verified` via
`memory_verify.v1` — a sentinel consent grant from `operator:verified`:

```bash
curl -X POST $DAEMON/agents/$AGENT_ID/tools/call \
  -d '{
    "tool_name": "memory_verify", "tool_version": "1",
    "session_id": "verify-1",
    "args": {
      "entry_id": "e-...",
      "verifier_id": "alex",
      "seal_note": "manually checked source code"
    }
  }'
```

Verified entries surface in the Memory tab with a distinct marker;
agents can filter for verified rows via the recall tool's
`verified_only` arg (when shipped).

## Audit events emitted

| Event | When |
|---|---|
| `memory_written` | every successful write |
| `memory_read` | cross-agent reads only (per-agent self-reads are too noisy and the data is already in scope) |
| `memory_disclosed` | cross-agent disclosure path |
| `memory_verified` | operator promotes an entry to verified |
| `memory_verification_revoked` | operator un-verifies |
| `memory_deleted` | soft delete (tombstone) |
| `memory_purged` | hard delete |
| `memory_scope_override` | scope ceiling bypassed by operator |
| `memory_consent_granted` / `memory_consent_revoked` | explicit consent flow |

## Layer-vs-scope cheat sheet

| You want | Layer | Scope | Example |
|---|---|---|---|
| Note what just happened, kept private | episodic | private | "called API X, got 200" |
| Share a finding with the swarm chain | episodic | lineage | "anomaly detected at 2026-04-30T12:00Z" |
| Tell another specific agent | episodic | consented + grant | "for VaultWarden: this canary tripped" |
| Crystallize a rule of thumb | semantic | lineage | "always check DNS before alerting" |
| Document a playbook | procedural | lineage | "containment steps: 1, 2, 3" |
| Operator-blessed truth | any | any + verified | "incident root cause confirmed" |

## Where to dig deeper

- **ADR-0022**: Memory subsystem v0.1 + v0.2 spec
- **ADR-0027**: Privacy contract + minimum-disclosure rule
- **Module**: `core/memory.py`
- **Tools**: `tools/builtin/memory_write.py`,
  `memory_recall.py`, `memory_disclose.py`, `memory_verify.py`
- **HTTP**: `daemon/routers/memory_consents.py`
- **Frontend**: Memory tab — `frontend/js/memory.js`
- **Tests**: `tests/unit/test_memory.py`,
  `test_memory_recall_tool.py`, `test_memory_write_tool.py`,
  `test_memory_disclose_tool.py`
