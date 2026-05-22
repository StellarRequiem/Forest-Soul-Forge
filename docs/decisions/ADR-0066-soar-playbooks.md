# ADR-0066 — SOAR playbooks + playbook_pilot + purple_pete

**Status:** Accepted (2026-05-22, B454-B459). All 6 tranches
shipped; D3 Local SOC Phase D closed; playbook_pilot + purple_pete
live (PlaybookPilot-D3, PurplePete-D3). The SOAR playbook substrate
+ purple-team simulation substrate ship under
`src/forest_soul_forge/security/playbook/` and
`.../purple_team/`. See `docs/runbooks/soar-playbooks.md`. This
ADR closes D3 entirely — all 15 SOC agents alive.
**Date:** 2026-05-18
**Tracks:** D3 Local SOC Phase D
**Supersedes:** none
**Builds on:** ADR-0033 (Security Swarm — response_rogue already
takes actions but only ad-hoc), ADR-0064 (telemetry pipeline +
batch anchors), ADR-0065 (detection-as-code; `detection_fired`
events become playbook triggers), ADR-0078 Phase A (forensic
chain-of-custody), ADR-0079 (diagnostic harness — purple_pete's
synthetic scenarios run inside its discipline)
**Closes:** D3 Local SOC Phase D, completing the four-phase rollout
opened by ADR-0078

## Context

After Phase C lands, the SOC detects continuously + tags ATT&CK
techniques + records `detection_fired` events. What's still
missing is a **codified response surface**: operator-authored
playbooks that consume detections and dispatch actions (isolate
process, block network, quarantine artifact, page on-call,
collect forensics, notify operator) without the ad-hoc
`response_rogue` invocation pattern.

A SOAR (Security Orchestration, Automation, Response) layer
needs:
1. A **playbook DSL** — operator-authored YAML/JSON procedures
   the engine executes step-by-step. Reviewable, version-controlled,
   testable.
2. A **playbook_pilot role** — actuator-class agent that runs
   approved playbooks against fired detections. The action surface.
3. A **purple_pete role** — adversary simulation. Runs synthetic
   attack scenarios against the SOC, measures
   time-to-detect / time-to-respond, exposes coverage gaps
   without touching production state.

Without Phase D, the SOC ends at "detection visible in the audit
chain" — operator runs response manually. With Phase D, the
operator codifies responses once and the pipeline executes them
under explicit governance.

## Decision

Land Phase D as two roles + a DSL:

### 1. Playbook DSL

YAML files in `config/playbooks/*.yml`. Each playbook declares:

```yaml
playbook_id: isolate-and-collect-forensics
version: '1'
trigger:
  detection_rule_ids: [proc_spawn_suspicious]
  min_severity: high
  cooldown_seconds: 300        # don't re-fire on same scenario within 5m

approval:
  default: required_human       # operator approves each step by default
  steps_auto_approved:          # explicit allowlist of steps that can run
    - collect_forensics         # without approval (read-only or chain-of-custody)

steps:
  - id: collect_forensics
    action: archive_evidence    # maps to existing archive_evidence.v1 skill
    args:
      artifact_path: "${detection.evidence.process_image_path}"
      transition_type: acquire
      attestor_reason: "playbook ${playbook_id} fired on ${detection.rule_id}"

  - id: isolate_process
    action: isolate_process     # maps to isolate_process.v1 tool
    args:
      pid: "${detection.evidence.pid}"
    requires_human_approval: true

  - id: notify_operator
    action: delegate            # maps to delegate.v1 tool
    args:
      to: operator
      message: "Playbook ${playbook_id} executed on ${detection.rule_id}; isolate_process status: ${isolate_process.out.status}"
    requires_human_approval: false

postconditions:
  audit_event_type: playbook_executed
```

The DSL is intentionally simple — each step is one
catalog-defined tool or skill invocation. No conditional branches
or loops in v1; if the operator needs branching, the right shape
is multiple playbooks with different triggers (composition over
nesting).

### 2. playbook_pilot role

Genre: **actuator** (max_side_effects=external — same ceiling as
response_rogue). The role's discipline is enforced via
constitutional policies, NOT genre. Every state-changing action
gates through approval by default; only steps in the playbook's
explicit `steps_auto_approved` list bypass.

The role's job:
1. Subscribe to `detection_fired` events (chain tail).
2. Resolve each detection against the playbook trigger table;
   find matching playbooks within their cooldowns.
3. For each matched playbook, execute steps in order:
   - approval-gated steps queue to `pending_calls` (operator
     approves via the existing Pending tab).
   - auto-approved steps execute immediately.
4. Emit `playbook_executed` audit events with full step history.

Constitution policies enforce:
- `forbid_unscheduled_action` — pilot only acts on a
  detection-triggered playbook, never on operator-typed intent.
- `require_playbook_signature_match` — every step's tool must
  match the playbook's declaration verbatim; runtime substitution
  is forbidden (defense against playbook+detection injection).
- `forbid_playbook_authorship` — pilot doesn't write
  `config/playbooks/`. The operator owns playbook content; pilot
  consumes.
- `require_cooldown_respect` — repeated detections within a
  cooldown window do NOT re-fire the playbook.

### 3. purple_pete role

Genre: **researcher** (controlled adversary simulation; allowlisted
external reach for fetching synthetic attack patterns from
research catalogs; no production-state mutation).

The role's job:
1. Run **synthetic scenarios** from a curated library
   (`config/purple_pete_scenarios/*.yml`).
2. Each scenario emits **synthetic telemetry events** into a
   sandbox-tagged subset of the telemetry store (separate
   `data/telemetry_simulation.sqlite` so production scans aren't
   polluted).
3. Measure **time-to-detect** (gap between synthetic event emit
   and the first matching `detection_fired`) and
   **time-to-respond** (gap between detection and
   `playbook_executed`).
4. Emit `purple_team_run_completed` audit events with the
   measured metrics + the scenario id + coverage notes
   ("technique T1059.004 detected in 1.4s; technique T1003 NOT
   detected").
5. Surface coverage gaps via a signature skill
   `purple_team_brief.v1` (parallel to telemetry_steward_brief).

Constitution policies:
- `forbid_production_telemetry_emit` — purple_pete writes ONLY
  to the simulation store. The discriminator is mandatory.
- `forbid_real_response_dispatch` — purple_pete cannot invoke
  playbook_pilot's action surface; its scenarios run against a
  configured-as-simulation detection engine path.
- `require_scenario_provenance` — every emitted synthetic event
  carries `purple_team_run_id` + scenario name; the chain entry
  records the same so reviewers can distinguish synthetic from
  real.

## Decisions

**Decision 1 — Playbooks are operator-authored YAML, not
LLM-generated.**

`detection_engineer` (Phase C) proposes detection rules via
LLM synthesis. Playbooks DO NOT take that shape. The action
surface is too consequential for LLM-synthesized procedures;
each playbook is operator-written, version-controlled, and
reviewable. The pilot consumes; the operator authors.

LLM-assisted playbook authoring may land later as a separate
skill (think: `propose_playbook.v1` mirroring
`propose_detection.v1`) — but never as autonomous authorship.

**Decision 2 — Every state-changing step requires human approval
by default.**

The playbook `approval.steps_auto_approved` allowlist must
ENUMERATE each step that runs without approval. The default is
require_human_approval. Operators who want truly autonomous
response opt in step-by-step, knowing exactly which actions are
auto-fired.

This is the inverse of "approval is required unless the operator
opts out per-call." Phase D inverts the default to be safer
out-of-the-box: approval is required UNLESS the playbook
explicitly auto-approves.

**Decision 3 — purple_pete writes to a separate simulation store.**

`data/telemetry_simulation.sqlite` is a copy of the regular
`SqliteTelemetryStore` schema, used exclusively by purple_pete.
The DetectionEngine (ADR-0065) gains a "simulation mode" hook —
when invoked on the sim store, its `detection_fired` events carry
`event_data.simulation=true` and are emitted to a separate
sub-chain (per ADR-0073 segment) so production audit reviewers
can filter them out.

This separation is load-bearing: synthetic events polluting the
real chain would invalidate every other Phase A/B/C audit
property.

**Decision 4 — Cooldown semantics are per-playbook, per-trigger
fingerprint.**

The trigger fingerprint is `(playbook_id, detection_rule_id,
target_entity)` where target_entity is the primary subject of
the detection (process pid, file path, user id — whichever the
detection emits). A detection fires for pid=1234; the playbook
runs; pid=1234 keeps firing → cooldown blocks re-runs until the
window expires. A detection fires for pid=5678 → different
target_entity, no cooldown.

**Decision 5 — `playbook_executed` events are first-class on the
chain.**

Same shape as `detection_fired` (B387 ADR-0065 D6):
```
event_type: "playbook_executed"
event_data: {
  playbook_id: "<filename>",
  playbook_version: "<sha256 of playbook body>",
  trigger_detection_id: "<seq>",
  steps: [
    {id, action, status, audit_event_seq,
     approval_state, executed_at}
  ],
  outcome: "completed" | "halted" | "approval_pending"
}
agent_dna: <playbook_pilot's dna>
```

**Decision 6 — purple_pete's scenarios live in a separate
allowlist file.**

`config/purple_pete_scenarios/*.yml`. Operator-authored. Each
scenario is a sequence of synthetic event templates plus expected
detection/response timings. Loaded at lifespan; reload via
`POST /purple_pete/reload`.

Starter library ships with 3-5 ATT&CK technique simulations
(T1059.004 shell, T1003 credential dumping, T1071 C2 beacon) so
the operator has working examples to clone.

**Decision 7 — Phase D closure requires both roles + DSL +
end-to-end smoke.**

T1-T6 bring the components live; T6 ships a live-test
demonstrating a detection_fired → playbook_executed →
purple_team_run_completed end-to-end run.

## Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | ADR doc + Playbook DSL parser + PlaybookDef dataclass + tests | 1 burst |
| T2 | PlaybookEngine + detection_fired subscription + approval gating + audit emission | 1 burst (long) |
| T3 | playbook_pilot role (trait_tree + genre + constitution + tool_catalog + handoffs + d3 + birth + tests) | 1 burst (long) |
| T4 | purple_pete role + simulation store + scenario DSL + purple_team_brief.v1 + tests | 1 burst (long) |
| T5 | Operator runbook (writing playbooks, configuring scenarios, reviewing playbook_executed/purple_team logs) + starter playbook library + starter scenario library | 1 burst |
| T6 | End-to-end smoke: synthetic scenario fires synthetic event → DetectionEngine fires → playbook_pilot executes → measurements recorded → north-star update → status: Accepted (CLOSES Phase D + D3 entirely) | 1 burst (long) |

Total: ~6 bursts. Phase D = ADR-0066 T1-T6.

## Consequences

**Positive:**

- D3 closes as a full Detect → Respond → Test loop. The local
  SOC matches industry SOAR shapes while staying single-host +
  sovereign + audit-grade.
- Playbook YAML in `config/` lets the operator version-control
  their response procedures alongside the rest of the kernel
  config. Diffs are reviewable.
- purple_pete's synthetic-run metrics turn "did our SOC actually
  catch X?" from a manual exercise into a recurring measurement.
- The approval-required default keeps the operator in the loop
  for every consequential action while still letting truly
  auto-fired steps (forensic collection, notifications) run at
  machine speed.

**Negative:**

- Adding the action surface widens blast radius. playbook_pilot
  carries actuator genre — the genre kit-tier check stops obvious
  drift, but the operator still owns playbook content quality.
  Mitigation: every state-changing step defaults to approval; an
  operator who auto-approves the wrong step has bypassed three
  defenses (write the playbook + add to auto-approve list + the
  default-deny posture they had to reverse).
- purple_pete's simulation store doubles the telemetry footprint.
  Mitigation: simulation store gets its own retention policy
  (default 7 days; configurable per scenario).
- Cooldown logic needs careful design — false negatives (legit
  re-fire blocked) are as bad as false positives. T2 ships a
  per-playbook cooldown override; T-future could add cooldown-
  break on operator-configured escalation conditions.

**Open questions:**

- Multi-step approval bundling: when a playbook has 3 steps each
  requiring approval, does the operator approve once for all 3
  or per-step? T2 default: per-step (most cautious); T-future
  can add "bundle approval" if operators find it noisy.
- Cross-playbook coordination: two playbooks fire on the same
  detection. Sequential or parallel? T2 default: sequential by
  playbook_id alphabetical order; parallel needs explicit
  configuration (a future ADR).
- Rollback: a playbook fires `isolate_process`, operator wants
  to undo. T-future surface — `playbook_undo.v1` skill that
  re-runs reverse steps. Out of scope for Phase D.

## See Also

- ADR-0033 — Security Swarm (response_rogue, the existing ad-hoc
  response surface; playbook_pilot is the SOAR-ified successor)
- ADR-0064 — telemetry pipeline (the substrate purple_pete writes
  synthetic events to)
- ADR-0065 — detection-as-code (the source of detection_fired
  events playbook_pilot consumes)
- ADR-0078 — D3 Local SOC umbrella (this ADR's parent)
- ADR-0036 — per-agent posture (the operator-controlled posture
  level constrains what playbook_pilot will execute)
- `config/playbooks/` — where playbooks live (created on demand)
- `config/purple_pete_scenarios/` — scenario library
- `data/telemetry_simulation.sqlite` — purple_pete's isolated
  telemetry surface
- https://attack.mitre.org/ — ATT&CK technique catalog
- https://github.com/atomicredteam/atomic-red-team — reference
  for synthetic-scenario design (we author our own, but their
  format inspires the scenario DSL)
