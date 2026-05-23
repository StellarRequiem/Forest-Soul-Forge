# Runbook — D2 Daily Life OS (ADR-0087)

**Scope.** Operating the D2 Daily Life OS domain end-to-end:
birth, skill install, first dispatch, observation, recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands D2 Phase A (this runbook will grow as Phases B–D ship).

**Phase context.** D2 ships in four phases per ADR-0087:

| Phase | New agent(s) | New builtin tool | Status |
|---|---|---|---|
| **A** | coordinator + inbox_triager | none — reuses existing | CLOSED |
| **B** | time_steward (YELLOW) | schedule_reminder.v1 + calendar_block.v1 | CLOSED |
| **C** | task_prioritizer | task_rank.v1 | CLOSED |
| **D** | reflector | decision_journal_compile.v1 | queued |

Each phase = one commit + one push, so the operator can verify
phase N before phase N+1 fires.

---

## At a glance

D2's value proposition: **the everyday operating system** —
morning briefings, inbox triage, calendar protection, task
prioritization, evening reflection. Context-aware via the
operator profile (work_hours, areas_of_focus, timezone).

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `coordinator` | researcher | green | `daily_orchestration.v1` | Composes the morning briefing from operator-profile + chain + sibling-role handoffs + D1 deltas. Routes downstream via delegate.v1; never acts on calendars / inboxes / tasks. |
| `inbox_triager` | communicator | green | `inbox_triage.v1` | Reads inbox snapshots from private memory, classifies + ranks, drafts top reply via email_draft.v1. NEVER sends — drafts only. |

Both Phase A agents are **operator-birthed via the approval queue**
per ADR-0087 — no auto-birth.

**Why two intake roles, not one?** Orchestration and inbox
triage are different governance surfaces. The coordinator
COMPOSES + ROUTES (read-only synthesis, no message preparation);
the triager READS messages + DRAFTS responses (communicator
genre with email_draft tooling). Different traits, different
policies; one role would conflate them + raise the orchestration
discipline's blast radius unnecessarily.

**Connector posture.** D2 declares four connector dependencies:
`forest-calendar`, `forest-mail`, `forest-slack`, `forest-notes`.
None ship with v0.3 — they're operator-installable. Phase A
operates with **graceful degradation**: the inbox_triager reads
from `inbox_snapshot`-tagged private-memory entries that the
operator pastes manually until a real connector lands;
coordinator's briefing draws on what's actually in memory + the
chain, so the briefing shrinks honestly rather than hallucinating.

**Pacific time everywhere.** Per CLAUDE.md, all D2 timestamps
are Pacific time. The skill manifests explicitly tell the LLM
to use Pacific time so briefings don't drift into UTC framing.

---

## Phase A — intake foundation

### 1. Restart the daemon

The new role definitions land in `trait_tree.yaml` +
`genres.yaml` + `constitution_templates.yaml`; the per-role kits
land in `tool_catalog.yaml`. The daemon loads these at lifespan
boot, so a restart is required before the births can pick them
up.

```bash
./dev-tools/force-restart-daemon.command
```

Verify in `/healthz`'s `startup_diagnostics` that the genre
engine reports `status: ok` and that `coordinator` appears in
`/genres` under the `researcher` genre's `roles` list and
`inbox_triager` appears under `communicator`.

### 2. Birth the agents

```bash
./dev-tools/birth-coordinator.command
./dev-tools/birth-inbox-triager.command
```

Each script is idempotent — re-running it skips the birth if
the agent already exists. Both set posture GREEN as the default
per ADR-0087 Decision 1 (read-only orchestration + drafts-only
are non-acting).

### 3. First dispatch — morning briefing

```bash
curl -s --max-time 60 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${COORDINATOR_ID}/skills/run" \
  -H "X-FSF-Token: $FSF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "daily_orchestration",
    "skill_version": "1",
    "inputs": {"window_hours": 24, "operator_reason": "first D2 brief"}
  }' | python3 -m json.tool
```

Expect a structured briefing back: headline, top-3 priorities,
calendar density (sparse without forest-calendar — that's
honest), knowledge callouts (drawn from D1's daily-delta if
ran in the same window), carry-forward items (empty on first
run).

### 4. First dispatch — inbox triage

Paste an inbox snapshot into private memory first:

```bash
curl -s --max-time 30 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${INBOX_TRIAGER_ID}/tools/call" \
  -H "X-FSF-Token: $FSF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "memory_write",
    "tool_version": "1",
    "session_id": "inbox-paste-1",
    "args": {
      "content": "FROM: alice@example.com\nSUBJECT: Project review\n\nCan we reschedule tomorrow 2pm? — Alice",
      "layer": "episodic",
      "scope": "private",
      "tags": ["inbox_snapshot"]
    }
  }'
```

Then dispatch the triage skill:

```bash
curl -s --max-time 60 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${INBOX_TRIAGER_ID}/skills/run" \
  -H "X-FSF-Token: $FSF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "inbox_triage",
    "skill_version": "1",
    "inputs": {"snapshot_tag": "inbox_snapshot", "max_items": 10}
  }' | python3 -m json.tool
```

Expect a triage summary, action items, and a draft reply. The
draft lives in private memory — copy + paste into your real
mail client; the agent never sends.

### 5. Recovery

- **`role 'coordinator' not found` on birth.** Daemon didn't
  reload trait_tree.yaml. Re-run
  `./dev-tools/force-restart-daemon.command`.
- **`tool_name 'email_draft.v1' not found in kit`.** The kit
  resolver fell back to the communicator genre default. Check
  that `inbox_triager` appears under `archetypes:` in
  `config/tool_catalog.yaml` with `email_draft.v1` in its
  standard_tools list.
- **Skill dispatch returns "skill not found".** The skill
  manifest may not be installed in the runtime dir. Confirm
  the file exists under `examples/skills/` and run
  `curl -X POST /skills/reload` to pick it up.

---

## Phase B — scheduling + calendar (YELLOW)

### 1. Restart the daemon

Phase B adds the `time_steward` role + two new builtin tools
(`schedule_reminder.v1` filesystem, `calendar_block.v1`
external). The daemon picks these up at lifespan boot.

```bash
./dev-tools/force-restart-daemon.command
```

Verify `time_steward` appears under the `actuator` genre's
roles list and that the two new tools appear in
`/tools/catalog`.

### 2. Birth the agent

```bash
./dev-tools/birth-time-steward.command
```

The script does NOT flip posture — time_steward stays at its
default **YELLOW** per ADR-0087 Decision 2. Every non-read-only
dispatch queues for operator approval.

### 3. First dispatch — schedule a reminder

```bash
curl -s --max-time 30 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${TIME_STEWARD_ID}/skills/run" \
  -H "X-FSF-Token: $FSF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "schedule_reminder",
    "skill_version": "1",
    "inputs": {
      "fire_at": "2026-05-24T09:00:00-07:00",
      "message": "Review D2 Phase B outcomes",
      "channel": "memory"
    }
  }' | python3 -m json.tool
```

YELLOW posture → the dispatch will queue rather than
auto-execute. Approve it via the pending-calls endpoint or
the Approvals tab; the tool then appends the reminder to
`data/d2/reminders.jsonl`. The fire itself runs unattended
when a future connector picks up the record.

### 4. First dispatch — decline a calendar event

```bash
curl -s --max-time 30 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${TIME_STEWARD_ID}/skills/run" \
  -H "X-FSF-Token: $FSF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "calendar_management",
    "skill_version": "1",
    "inputs": {
      "operation": "decline",
      "event_id": "fake-event-1",
      "decline_message": "Scheduling conflict — async would work better"
    }
  }' | python3 -m json.tool
```

If the forest-calendar connector marker
(`data/connectors/forest-calendar.enabled`) is absent, the
tool refuses cleanly with "calendar connector not wired" —
that's the graceful-degradation pattern. To smoke-test
without a real connector, touch the marker file first:

```bash
mkdir -p data/connectors
touch data/connectors/forest-calendar.enabled
```

### 5. YELLOW → GREEN promotion criteria

Move time_steward from YELLOW to GREEN only after:

- At least 10 reminders have been queued + manually approved
  without operator-corrected drift (the operator agreed with
  the timing + channel each time).
- At least 5 calendar actions have been queued + manually
  approved without the operator declining the decline.
- The `data/d2/reminders.jsonl` ledger doesn't show any
  fire times outside the operator's work_hours that the
  operator wouldn't endorse.

Even after GREEN, the per-call `requires_human_approval`
gates on `schedule_reminder.v1` (filesystem) and
`calendar_block.v1` (external) remain in force — that's the
actuator-genre + tool-policy baseline regardless of posture.

## Phase C — prioritization

### 1. Restart the daemon + birth

Phase C adds the `task_prioritizer` role + the `task_rank.v1`
builtin tool.

```bash
./dev-tools/force-restart-daemon.command
./dev-tools/birth-task-prioritizer.command
```

GREEN posture default — read-only ranking is non-acting.

### 2. First dispatch — rank an operator-provided list

```bash
curl -s --max-time 60 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${TASK_PRIORITIZER_ID}/skills/run" \
  -H "X-FSF-Token: $FSF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "task_prioritization",
    "skill_version": "1",
    "inputs": {
      "tasks": [
        {"title": "Pay rent", "urgency": 10, "impact": 9, "effort": 1, "tags": ["finance"], "due_in_hours": 24},
        {"title": "Reply to Alice", "urgency": 6, "impact": 4, "effort": 2, "tags": ["communication"]},
        {"title": "Read latest D2 ADR", "urgency": 3, "impact": 8, "effort": 4, "tags": ["forest"]}
      ]
    }
  }' | python3 -m json.tool
```

Expect a ranking with the highest-leverage task on top + a
narration that cites the dominant score component per item.
The ranking is deterministic — re-run with the same inputs to
get the same order.

### 3. Scoring discipline

- Default weights: urgency=1.2, impact=1.5, effort=0.5 (effort
  SUBTRACTS — cheap high-impact wins).
- `due_in_hours` overrides low urgency: ~10 hour deadline floors
  urgency at ~8.3.
- `areas_of_focus` from the operator profile adds +1 to any
  task whose tags match. The `focus_bonus` skill input
  overrides this default magnitude.

## Phase D onward

Section for Phase D will land with the phase commit.
