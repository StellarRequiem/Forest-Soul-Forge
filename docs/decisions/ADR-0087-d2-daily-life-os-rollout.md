# ADR-0087 — D2 Daily Life OS: rollout

**Status:** Accepted (2026-05-23). All four phases CLOSED —
D2 Daily Life OS LIVE with all 5 agents alive (Coordinator-D2,
InboxTriager-D2, TimeSteward-D2 YELLOW, TaskPrioritizer-D2,
Reflector-D2); 4 new builtin tools (schedule_reminder.v1,
calendar_block.v1, task_rank.v1, decision_journal_compile.v1)
with 118 unit tests total; 7 skill manifests; cascade wiring
d1→d2 morning_briefing ACTIVE; umbrella + runbook live.
**Date:** 2026-05-23
**Tracks:** Domain Rollout / Daily Operator Leverage
**Supersedes:** none
**Builds on:** ADR-0041 (set-and-forget scheduler — schedule_reminder.v1
substrate), ADR-0067 (cross-domain orchestrator — D2 is next after
D1 closes), ADR-0068 (operator profile — work_hours, areas_of_focus,
timezone drive every D2 decision), ADR-0076 (vector index for
personal context — D2's queryable substrate), ADR-0085 (D8
rollout precedent), ADR-0086 (D1 rollout precedent — same four-
phase / one-commit-per-phase shape).

## Context

D1 Personal Knowledge Forge closed 2026-05-23 (ADR-0086, all four
phases CLOSED, 4 agents alive). Per ADR-0067's rollout-order plan
(D4→D3→D8→D1→**D2**→D7→D9→D10→D5→D6), **D2 Daily Life OS** is next.

D2's value proposition (from
`config/domains/d2_daily_life_os.yaml`):

> The everyday operating system. Coordinates morning briefings,
> inbox triage, calendar protection, task prioritization, evening
> reflection, weekly review. Context-aware: reads operator profile
> for timezone + work_hours, surfaces relevant knowledge from D1,
> hands off to D6 for finance reminders, D7 for content seeds, D5
> for home routines.

D2 is the **operator-leverage-heaviest domain.** The manifest's
own notes flag it: *"If only one domain ships per quarter, this
is the one with the biggest user-visible impact."*

Five new roles per `config/domains/d2_daily_life_os.yaml`:

| Role | Capability | Posture |
|---|---|---|
| `coordinator` | daily_orchestration | GREEN (read-only orchestration) |
| `inbox_triager` | inbox_triage | GREEN (drafts only — never sends) |
| `time_steward` | calendar_management | YELLOW (every external action operator-gated) |
| `task_prioritizer` | task_prioritization | GREEN (read-only ranking) |
| `reflector` | daily_reflection | GREEN (read-only synthesis) |

## Decision

**Decision 1 — Five roles, no new genres.**

| Role | Genre | Trait emphasis | Side-effects ceiling |
|---|---|---|---|
| `coordinator` | researcher | thoroughness + lateral_thinking + transparency | network (read-only composition; routing via delegate.v1) |
| `inbox_triager` | communicator | empathy + transparency + directness | network (drafts only via email_draft.v1 — never sends) |
| `time_steward` | actuator | caution + evidence_demand + transparency | external (calendar_block.v1 — operator-gated per call) |
| `task_prioritizer` | researcher | research_thoroughness + transparency + lateral_thinking | read_only (LLM ranking with operator-profile context) |
| `reflector` | researcher | thoroughness + transparency + double_checking | read_only (audit-chain walk; never mutates source) |

Same pattern as ADR-0085 (D8: 5 roles across 3 genres) +
ADR-0086 (D1: 4 roles across 2 genres). The fundamental work
of a Daily Life OS decomposes into:

1. **Orchestration** (coordinator — researcher; composes the
   briefing, routes downstream, never acts);
2. **Communication** (inbox_triager — communicator; classifies +
   drafts; never sends);
3. **Action** (time_steward — actuator; the only acting role in
   D2; YELLOW posture forces operator review of every external
   action);
4. **Ranking** (task_prioritizer — researcher; ranks operator-
   provided task lists; never mutates the task store);
5. **Reflection** (reflector — researcher; evening sweep of
   operator-decision events for a decision-journal digest).

**Decision 2 — time_steward defaults YELLOW.**

time_steward is the only actuator-tier role in D2. Calendar
actions are external, irreversible from the operator's
perspective (a sent meeting decline can't be silently un-sent;
a created scheduled reminder fires on its schedule unless
explicitly cancelled). YELLOW posture ensures every dispatch
queues for operator approval until the operator explicitly
flips to GREEN after the proposal-quality bar is bedded in —
same pattern as policy_enforcer (ADR-0085 Phase C) and
knowledge_verifier (ADR-0086 Phase C).

`requires_human_approval=True` on `calendar_block.v1` makes the
per-call gate the load-bearing safety regardless of posture;
posture is the secondary discipline (auto-pause every other
non-read-only dispatch the role can fire). The two layers
together let the operator move time_steward from YELLOW to
GREEN safely once they have a calibration period.

**Decision 3 — schedule_reminder is filesystem-class (ADR-0041
substrate); calendar_block is external-class (forest-calendar
connector).**

The two new builtin tools in Phase B occupy different governance
tiers:

- **schedule_reminder.v1** writes a scheduled-task row via the
  ADR-0041 substrate. side_effects=filesystem because the
  scheduled-tasks table lives in the registry SQLite (which is
  the canonical action — the scheduler picks up the row on its
  next tick). The actuator genre's ceiling permits this.
  requires_human_approval=True per the
  `filesystem_always_human_approval` rule means every reminder
  also gates on operator approval at dispatch time — the
  reminder itself fires unattended at schedule time, but the
  *creation* is operator-reviewed.
- **calendar_block.v1** crosses the external boundary (writes
  to the operator's calendar via the forest-calendar connector).
  side_effects=external + requires_human_approval=True per the
  `external_always_human_approval` rule. When the
  forest-calendar connector is absent, the tool refuses cleanly
  with "calendar connector not wired" rather than crashing —
  graceful degradation per the ADR-0086 Decision 4 pattern.

The tools share a kit (time_steward only) and the actuator
genre's external ceiling permits both.

**Decision 4 — Cascade wiring: D1→D2 morning_briefing ACTIVE;
D2→{d6,d7,d5} declared INERT.**

Per ADR-0086's INERT-cascade pattern, D1's daily_knowledge_delta
was declared INERT pending D2's morning_briefing surface. With
D2 Phase D shipping the umbrella + the morning_briefing
capability live, the D1→D2 cascade ACTIVATES.

The D2 manifest's three downstream handoff_targets are
upstream of D2 in the rollout order (D6 finance, D7 content,
D5 smart home). We DECLARE the cascade intent in
`config/handoffs.yaml` comments so the wiring is visible at a
glance + a future maintainer doesn't have to re-derive the
plan; the cascade rules themselves stay un-codified until
each downstream rollout lands. Mirrors the ADR-0086 Phase D
pattern for D1's downstream cascades.

## Implementation tranches

**Phase A — intake + orchestration foundation.**
- coordinator + inbox_triager roles in trait_tree / genres /
  constitution_templates / tool_catalog
- No new builtin tools — reuse existing
- Skill manifests: daily_orchestration.v1 + inbox_triage.v1
- Birth scripts: birth-coordinator.command + birth-inbox-triager.command
- Runbook section + ADR-0087 in Proposed status

**Phase B — scheduling + calendar surface.**
- time_steward role (YELLOW)
- schedule_reminder.v1 + calendar_block.v1 builtin tools
- Skill manifests: schedule_reminder.v1 + calendar_management.v1
- Birth script: birth-time-steward.command

**Phase C — prioritization.**
- task_prioritizer role
- task_rank.v1 builtin tool
- Skill manifest: task_prioritization.v1
- Birth script: birth-task-prioritizer.command

**Phase D — reflection + cascade + umbrella.**
- reflector role
- decision_journal_compile.v1 builtin tool
- Skill manifests: daily_reflection.v1 + decision_journal.v1
- Birth script: birth-reflector.command
- Umbrella: birth-d2-daily-life-os.command
- Cascade wiring: ACTIVATE d1→d2 morning_briefing; declare
  INERT d2→d6, d2→d7, d2→d5
- ADR-0087 → Accepted; domain manifest status → live

Each phase = one commit + one push. The operator can verify
phase N before phase N+1 fires.

## Consequences

**Operator leverage.** D2 is the domain operators interact with
multiple times a day. Getting the orchestration / triage /
scheduling / prioritization / reflection loop right multiplies
the value of every other domain (D1's knowledge surfaces into
the briefing; D3's incidents flag overnight; D6 future-cascades
will surface bill deadlines, etc.).

**YELLOW posture friction.** time_steward's YELLOW default means
every schedule + calendar action queues. The operator will see
the queue more during D2 bedding-in than during D1/D8 (which
shipped with mostly read-only roles). Documenting the
YELLOW→GREEN promotion criteria in the runbook is load-bearing
for adoption.

**Graceful connector degradation.** forest-calendar /
forest-mail / forest-slack / forest-notes are operator-
installable — none ship with D2. Phase A operates entirely
through memory_write (operator pastes inbox snapshots; daily
briefings synthesize from chain + profile). Phase B's
calendar_block.v1 refuses cleanly when forest-calendar is
absent. Same graceful-degradation pattern as ADR-0086 Decision 4.

**Pacific time everywhere.** Per CLAUDE.md operator constraints,
all timestamps in D2 prompts + briefing prose are Pacific
time. Operator profile's `timezone` field is the source of
truth; the LLM prompts in each skill manifest explicitly state
the constraint to prevent UTC drift.
