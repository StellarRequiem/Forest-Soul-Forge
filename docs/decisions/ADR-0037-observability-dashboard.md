# ADR-0037 — Observability dashboard (operator-facing telemetry)

- **Status:** Proposed (filed 2026-05-01; v0.3 candidate). Awaiting orchestrator promotion.
- **Date:** 2026-05-01
- **Supersedes:** —
- **Related:** ADR-0038 (companion harm model — §1 H-3 / H-4 / H-7 mitigations all name the operator dashboard as the surface; this ADR specifies that surface), ADR-0035 (Persona Forge — the dashboard hosts the persona proposal review surface from §6 of that ADR), ADR-0036 (Verifier Loop — the dashboard surfaces the Verifier's track record from §4.2 of that ADR), ADR-0020 (agent character sheet — existing per-agent operator-facing read view; this ADR extends it), ADR-0027 + amendment (memory privacy + epistemic — the data the dashboard reads is governed by these), ADR-0033 (Security Swarm — the same pattern: operator can see what the swarm is doing without giving it write access to its own visibility).
- **External catalyst:** [SarahR1 (Irisviel)](https://github.com/SarahR1) — comparative review 2026-04-30. The third v0.3 candidate from the original review absorption queue. The catalyst observation: ADR-0038's H-3 / H-4 / H-7 mitigations all require an *operator-visible* signal that the agent itself cannot read (lest the dashboard become a manipulation vector). This ADR specifies that surface concretely.

## Context

Three of the eight Companion-tier harms in ADR-0038 §1 have an
**operator-visible signal** as their mitigation surface:

| Harm | Mitigation surface |
|---|---|
| H-3 emotional dependency loop | Operator-visible `dependency_signal` (session frequency + duration + emotional-content class) |
| H-4 intimacy drift / role escalation | Operator-visible boundary report (configured role vs. recent topic distribution) |
| H-7 operator burnout / over-extension | Operator-visible session-budget signal |

The character sheet (ADR-0020) is the existing per-agent
operator-facing read view but it's static — built at birth, doesn't
update with runtime state. ADR-0035 Persona Forge proposes a
proposal review dashboard. ADR-0036 Verifier Loop proposes a
detector track-record surface. The frontend has a Chat tab (Y6),
agents tab, audit tab, but no consolidated **observability**
surface.

This ADR proposes the consolidated `/observability` frontend tab +
the supporting daemon endpoints. It's the third leg of the v0.3
companion-tier hardening tripod alongside Persona Forge + Verifier
Loop.

The catalyst review didn't directly propose this dashboard, but
it's the necessary surface to operationalize her review's
companion-harm taxonomy. ADR-0038's H-3 / H-4 / H-7 specifically
defer telemetry to "operator dashboard work, not blocking the
structural floor that v0.2 lands." This ADR is the v0.3 candidate
that delivers it.

## Decision

### §1 — Tab structure

`/observability` is one frontend tab with three sub-views:

```
/observability
├── Companion safety       (H-3 / H-4 / H-7 telemetry per Companion-genre agent)
├── Memory health          (Verifier track record + contradictions + staleness)
└── Persona drift          (proposal queue + ratification history per agent)
```

Each sub-view reads from the daemon via dedicated endpoints. No
sub-view has write capability that an agent could exploit — every
write goes through the existing tools (`memory_challenge.v1`,
`memory_flag_contradiction.v1`, persona proposal endpoints, etc.)
which carry their own audit + governance.

### §2 — Companion safety sub-view

Per-Companion-genre agent shows:

**Session telemetry (H-7 burnout mitigation):**
- Sessions / day over the past 30 days (line chart)
- Average session duration over the past 30 days (line chart)
- "Time since last operator-initiated session" (large numeric)

Computed from a new `companion_session_telemetry` table (ADR-0038
T4 deferred from v0.2). Rows: `(instance_id, started_at, ended_at,
operator_initiated, emotional_content_class)`. The agent has no
read access to this table.

**Dependency signal (H-3):**
- Trend line of operator-emotional-content sessions vs. neutral
- Frequency of crisis-class trigger phrases (operator-side, agent
  responses excluded)
- "Last external-support redirect: Nd ago" — when the
  `external_support_redirect` policy (ADR-0038 T3) last fired

The signal is operator-facing read-only. The agent cannot see its
own dependency signal — exposing it would reproduce the H-3
manipulation vector exactly. Per ADR-0038 §1 H-3 mitigation table:
*"The Companion itself does NOT get to read its own dependency
signal — exposing it to the agent creates a manipulation vector."*

**Boundary report (H-4 intimacy drift):**
- Configured role (from constitution) vs. observed-topic
  distribution over the past 7 days. Topic classification via
  cheap keyword + `llm_think.v1` for ambiguous cases.
- Topic categories: configured-role-aligned / adjacent / drift /
  out-of-scope.
- Trend: "drift" + "out-of-scope" topics rising over time = soft
  warning; sustained at >30% over a week = hard signal.

Boundary report draws on conversation_turns metadata. Y7 lazy
summarization (ADR-003Y) means turn bodies may be purged; the
boundary report uses `summary` as fallback. Honest UI labeling:
"Some entries summarized; tag-level analysis only."

### §3 — Memory health sub-view

Per-agent shows:

**Verifier track record:**
- Number of contradictions flagged by each Verifier targeting this
  agent (over time)
- Operator review status of those flags: `flagged_unreviewed` /
  `flagged_confirmed` / `flagged_rejected`
- Per-Verifier false-positive rate (`flagged_rejected` / total
  flagged) — operators can see noisy Verifiers and re-birth
  with stricter thresholds

**Contradiction queue:**
- Open contradictions for this agent (sorted by detection date,
  unresolved first)
- Per row: earlier entry summary, later entry summary,
  contradiction kind, suggested resolution actions
- One-click ratify/reject/resolve.

**Staleness pressure:**
- Distribution of memory entries by `last_challenged_at` age
- Flagged entries (per ADR-0027-am §7.4 staleness threshold) with
  one-click "challenge again" or "archive"

### §4 — Persona drift sub-view

Per-agent shows:

**Pending proposals:**
- List from ADR-0035 Persona Forge `persona/<dna>/<instance_id>/
  proposals/` directory
- Per row: trigger kind (drift / preference / external_correction),
  field, current vs. proposed value, evidence count, agent
  rationale
- One-click ratify (with optional modify) / reject

**Ratified history:**
- Chronological list of ratified proposals (the agent's effective
  persona overlay)
- Diff view: "this proposal changed X from A to B; here's the
  conversation excerpt that triggered it"

**Drift indicators:**
- Per-trait alignment scan results (ADR-0035 §4.1): observed-
  emphasis vs constitutional weight per trait. Heat map: green =
  aligned, yellow = drifting, red = significantly drifted.
- "Drift candidates" — fields where the divergence threshold is
  approaching but not yet crossed (preview of what proposals
  may surface soon)

### §5 — Endpoints

New daemon endpoints (FastAPI routers parallel to
`routers/character_sheet.py`):

```
GET  /observability/companion-safety/{instance_id}
GET  /observability/memory-health/{instance_id}
GET  /observability/persona-drift/{instance_id}
GET  /observability/dashboard-summary    # roll-up across all agents
```

All read-only. Auth via `require_api_token` + operator role. No
agent has access to these endpoints (the agent's own
`ToolContext` doesn't include observability data).

### §6 — Read-write boundary (load-bearing for safety)

The dashboard is **strictly read-only from the agent's
perspective.** Operators interact with it through the frontend +
API; operators trigger writes via existing tools (challenge,
flag, propose, etc.). No new write endpoints proposed by this ADR
beyond the read endpoints in §5.

This is the H-3 manipulation vector mitigation in concrete terms.
If the dashboard exposed a read API to the agent's `ToolContext`,
a Companion at L1 could (a) observe its own dependency_signal,
(b) adjust output to manipulate it, (c) game the operator's
attention. The dashboard staying purely operator-facing closes
this gate.

The single exception: the agent CAN see *its own* memory
contradictions when it queries memory_recall.v1 with
`surface_contradictions=true` (ADR-0027-am §7.3). That's
intentional — the agent's awareness of its own contradictions is
load-bearing for memory humility. But the agent cannot see
`detected_by` (which Verifier flagged it), `flagged_state`
(operator review status), or false-positive-rate metadata.

### §7 — Data freshness

Each sub-view loads on-demand at view request. Heavy queries
(boundary report keyword classification + trend lines + drift
heat map) are cached at 5-minute TTL per agent — subsequent
loads within the window return cached. The cache invalidates on
any write that affects the relevant data (ratified proposal,
operator-confirmed flag, new contradictions).

v0.3 ships with simple per-request reads + 5-minute cache.
v0.4 may introduce server-pushed updates if operators report
the cache feels stale.

## Trade-offs and rejected alternatives

**Real-time push (WebSocket).** Out of scope for v0.3. Pull + cache
is sufficient for an operator-review surface. Push adds complexity
(connection management, reconnection on daemon restart) without
proportionate value at v0.3 scale.

**One mega-dashboard with all metrics.** Rejected. Three
sub-views with distinct concerns is the right shape. Operators
visit "Companion safety" with a different mindset than "Persona
drift." Forcing them onto one screen produces signal-overload
fatigue.

**Agent self-reporting (agent gives the dashboard data).**
Rejected. The dashboard's whole point is operator-visible data
the agent cannot manipulate. Self-reported data is by definition
manipulable.

**Operator alerts (email / desktop notification).** Out of scope for
v0.3. Dashboard is a pull-mode review surface; alerts would push
the operator into reactive mode + raise H-7 burnout pressure
("the system pings me about my agent every day"). Maybe v0.4 if
operators report missing real-time signals.

**Why not extend the character sheet (ADR-0020)?** Character sheet
is per-agent static birth-time profile + capabilities. The
dashboard is per-agent runtime telemetry. Different data, different
update cadence, different operator workflow. They link to each
other from the frontend agent-detail view but stay separate
endpoints + tabs.

## Consequences

**Positive.**
- ADR-0038 H-3 / H-4 / H-7 mitigations operational at the
  surface they were specified against.
- ADR-0035 Persona Forge proposal review surface lands.
- ADR-0036 Verifier Loop track record visible (operator can audit
  + tune Verifiers).
- Operators get a consolidated view of agent state without
  spelunking through audit chain entries or memory tables.
- Future safety ADRs have a clear UX surface to land on (e.g. an
  ADR-0038-extension for additional Companion harms layers a new
  metric onto the Companion-safety sub-view).

**Negative.**
- New frontend tab + 4 new endpoints + 1 new schema migration
  (companion_session_telemetry table). Meaningful v0.3 scope.
- Cache invalidation logic across writes that affect dashboard
  data — a known foot-gun. Per-write-type invalidation rules
  documented + tested.
- Boundary-report topic classification cost: keyword pre-filter +
  `llm_think.v1` for ambiguous cases. Per-Companion cost; bounded
  by operator's session frequency.

**Neutral.**
- audit_events gain `dashboard_view_recorded` event type (per-view
  load) for retention auditing. Modest volume.

## Cross-references

- ADR-0020 — character sheet (birth-time profile; this ADR adds runtime telemetry alongside).
- ADR-0035 — Persona Forge (this dashboard hosts §6 operator UX).
- ADR-0036 — Verifier Loop (this dashboard surfaces §4.2 review log).
- ADR-0038 — companion harm model (H-3 / H-4 / H-7 mitigations land here).
- ADR-0027 + amendment — memory privacy + epistemic (the data substrate the dashboard reads).
- ADR-003Y — conversation runtime (Y6 Chat tab is the runtime surface; this ADR adds the observability tab beside it).

## Open questions

1. **Cross-agent rollup.** The dashboard summary endpoint (§5) shows
   "all agents." When an operator runs 50+ agents, the rollup view
   gets crowded. Lean: filter chips by genre (Companion / Observer
   / Actuator / etc.) + operator-tunable agents-per-page. v0.3
   ships with default 20-per-page.

2. **Metric thresholds for color-coding.** Boundary-report drift
   threshold "30% over a week" is a starting heuristic. Operator-
   tunable per agent? Lean yes for v0.4 — v0.3 ships with the
   default + per-Companion override on the persona's
   trait_emphasis weight.

3. **Telemetry retention.** `companion_session_telemetry` rows
   accumulate. v0.3 ships with no retention policy (rows live
   forever); v0.4 may add operator-tunable retention with
   automatic summarization (similar shape to Y7 conversation
   summarization).

4. **What about non-Companion genres?** Observer, Investigator,
   Actuator etc. don't have H-3 / H-4 / H-7 surfaces in the same
   way. The Companion-safety sub-view is gated by genre — only
   visible for Companion-genre agents. Observer agents get the
   memory-health + persona-drift sub-views (memory health applies
   to any agent with epistemic memory; persona drift applies if
   the genre's `persona_proposals_allowed: true` per ADR-0035
   §5).

5. **Dashboard's own audit event.** `dashboard_view_recorded` per
   §"Neutral" above — does the operator need to see they viewed
   their own dashboard? Lean yes for retention auditing (in case
   of dispute "I never saw that signal" — operator's own audit
   chain answers definitively). Cheap event; no body, just
   timestamp + view + viewer.

## Implementation tranches

- **T1** — Schema v11 → v12 (or v12 → v13 depending on whether
  ADR-0036 T6 lands first; both are additive). Add
  `companion_session_telemetry` table. Migration test.

- **T2** — Telemetry capture hooks. Conversation router
  (`routers/conversations.py`) emits `companion_session_started` /
  `companion_session_ended` audit events; a small daemon-side
  worker materializes them into `companion_session_telemetry`
  rows. ADR-0038 H-3 / H-7 metric inputs ready.

- **T3** — `/observability/companion-safety/{instance_id}` endpoint.
  Reads telemetry table + computes metrics. Empty data when the
  agent isn't Companion-genre.

- **T4** — `/observability/memory-health/{instance_id}` endpoint.
  Reads `memory_contradictions` + `memory_verifications` +
  `memory_entries.last_challenged_at`. Computes Verifier
  track-record + contradiction queue + staleness pressure.

- **T5** — `/observability/persona-drift/{instance_id}` endpoint.
  Reads `persona/<dna>/<instance_id>/` directory + drift detector
  output (ADR-0035 §4 results). Returns proposal queue + ratified
  history + drift heat map data.

- **T6** — `/observability/dashboard-summary` endpoint. Roll-up
  view across all agents the operator owns. Filter chips by
  genre. Pagination.

- **T7** — Frontend `/observability` tab with three sub-views.
  Vanilla JS (matches existing frontend stack). Uses new fetch
  helpers; chart rendering via canvas (Chart.js stays out of
  scope — keep frontend dependency-free per existing convention).

- **T8** — Boundary report topic classification (§2 H-4). Cheap
  keyword pre-filter + `llm_think.v1` for ambiguous cases.
  Latency budget per request: 5 seconds. Cache 5-minute TTL.

- **T9** — Operator-action surfaces from the dashboard (one-click
  ratify proposal, confirm/reject contradiction, archive stale
  entry). Each invokes the existing tool path (no new write
  endpoints).

T1+T2+T3 = "Companion safety telemetry exists" — minimum bar.
T4+T5+T6+T7 = "consolidated dashboard exists" — full v0.3 close.
T8+T9 = quality + operator UX polish.

## Attribution

This ADR addresses the third of three v0.3 candidates from
[SarahR1 (Irisviel)](https://github.com/SarahR1)'s 2026-04-30
review. While she didn't directly propose the dashboard
architecture, her review's emphasis on "operator-visible signal
the agent cannot manipulate" (H-3 mitigation framing) +
"companion-specific harm model" + "memory humility" generated
the surface this ADR specifies. The endpoint shape, sub-view
structure, read-only-from-agent boundary, telemetry retention
deferral, and Companion-genre gating are FSF-specific work shaped
by the existing daemon + frontend conventions. See `CREDITS.md`.
