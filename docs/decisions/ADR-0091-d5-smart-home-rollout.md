# ADR-0091 — D5 Smart Home Brain: rollout

**Status:** Proposed (2026-05-24). Phase A shipped today
(home_steward + home_sentinel); Phases B (energy_warden +
comfort_optimizer + energy_anomaly_scan.v1 + comfort_recommend.v1),
C (routine_composer + routine_compose.v1 + home_state_snapshot.v1),
D (cascade + umbrella + live) pending.
**Date:** 2026-05-24
**Tracks:** Domain Rollout / Smart Home Substrate
**Supersedes:** none
**Builds on:** ADR-0067 (cross-domain orchestrator — D5 is next
after D10 closes per the rollout order
D4→D3→D8→D1→D2→D7→D9→D10→**D5**→D6), ADR-0068 (operator profile —
home address + routines + areas_of_focus frame state-of-the-home
reports + anomaly thresholds), ADR-0043 (MCP plugin protocol —
forest-home-assistant connector ingests Home Assistant entity
state into memory_writes), ADR-0076 (vector index for personal
context — `personal_recall.v1` surfaces prior household routines),
ADR-0085 / ADR-0086 / ADR-0087 / ADR-0088 / ADR-0089 / ADR-0090
(domain-rollout precedents — same four-phase / one-commit-per-
phase shape).

## Context

D10 Multi-Agent Research Lab closed 2026-05-23 (ADR-0090, all
four phases CLOSED, 5 agents alive). Per ADR-0067's rollout-
order plan (D4→D3→D8→D1→D2→D7→D9→D10→**D5**→D6), **D5 Smart
Home Brain** is next.

D5's value proposition (from `config/domains/d5_smart_home.yaml`):

> Local-first IoT orchestration. NOT "Alexa-but-local" — causal
> scheduling (warm coffee maker earlier when rain forecast
> extends warm-up time), counterfactual diagnostics (power bill
> higher than usual → which device + which window + suggested
> action), cross-domain awareness (calendar [Travel] event
> activates vacation mode; SOC sees you're not home → tightens
> posture). Single MQTT plugin pulls in Home Assistant entities
> and exposes them as Forest tools.

The manifest is explicit that D5 is "lowest-priority for Alex's
first deployment per his read — Home Assistant isn't set up
yet. Substrate is ready; the domain ships when operator turns
on HA + the forest-home-assistant plugin." D5 ships as a
**substrate-ready domain**: all home_state arrives via
`home_state_snapshot` memory attestations (operator-supplied
one-shots OR connector-supplied), not via live device queries.
This decouples the role wiring from connector availability —
the operator can install + birth + dispatch D5 today; turn on
the connector later.

Five roles per the domain manifest:

| Role | Capability | Posture |
|---|---|---|
| `home_steward` | home_orchestration | GREEN (state composition; non-acting) |
| `energy_warden` | energy_optimization | GREEN (anomaly detection; non-acting) |
| `comfort_optimizer` | comfort_tuning | GREEN (recommendation composition; non-acting) |
| `home_sentinel` | home_security | GREEN (alert composition; non-acting) |
| `routine_composer` | routine_management + vacation_mode | YELLOW (the only acting role; queue-driven) |

## Decision

**Decision 1 — Five roles, no new genres; four GREEN + one
YELLOW.**

| Role | Genre | Trait emphasis | Side-effects ceiling |
|---|---|---|---|
| `home_steward` | researcher | thoroughness + transparency + composure | read_only (state reports to private memory) |
| `home_sentinel` | guardian | evidence_demand + double_checking + caution | read_only (alert attestations to private memory) |
| `energy_warden` | researcher | thoroughness + evidence_demand + transparency | read_only (anomaly attestations to private memory) |
| `comfort_optimizer` | researcher | thoroughness + warmth + transparency | read_only (recommendation attestations to private memory) |
| `routine_composer` | actuator | caution + evidence_demand + transparency + formality | filesystem (queue file writes; queue → operator/connector pickup) |

The fundamental work of a Smart Home Brain decomposes into:

1. **Orchestration** (home_steward — researcher; reads
   home_state snapshots + composes state-of-the-home reports;
   never acts);
2. **Security watching** (home_sentinel — guardian; reads
   home_state snapshots + composes alert attestations; never
   acts);
3. **Energy analysis** (energy_warden — researcher; dispatches
   `energy_anomaly_scan.v1` + composes anomaly attestations;
   never tunes);
4. **Comfort recommendation** (comfort_optimizer — researcher;
   dispatches `comfort_recommend.v1` + composes recommendation
   attestations; never tunes);
5. **Routine queueing** (routine_composer — actuator; dispatches
   `routine_compose.v1` to queue routine envelopes for operator
   pickup OR forest-home-assistant connector consumption; never
   fires routines directly).

Four roles are GREEN posture because their deliverable is
always a memory-attested report or alert; none cross the
external boundary themselves. `routine_composer` is YELLOW
because it writes the routine envelope to a filesystem queue
(`data/d5/routine_queue.jsonl`) which is operator-visible +
connector-consumable. The actuation discipline mirrors D7's
`distribution_pilot` + D9's `spaced_repetition_pilot`: a
filesystem queue → operator/connector pickup is the load-bearing
separation, not direct external-device control.

**Decision 2 — Queue-driven actuation; no direct device control
in D5.**

The forest-home-assistant connector (when installed) is the only
path that touches Home Assistant entities. `routine_composer`
writes routine envelopes to a queue file; the connector picks
them up (operator-approved, per-routine). D5 has NO builtin tool
that calls Home Assistant directly. This means:

- D5 ships substrate-ready without forest-home-assistant present.
- The operator can dispatch D5 roles + read attestations + see
  queued routines without any IoT connector wired up.
- When the connector ships, it consumes the queue + applies
  routines + writes `home_state_snapshot` attestations back into
  memory. The D5 roles see the connector's writes the same way
  they see operator-supplied snapshots.

Same queue-driven pattern as D7's `publish_schedule.v1` → forest-
publish connector + D9's `spaced_repetition_schedule.v1` →
operator pickup. Different connector, same separation.

**Decision 3 — Steward + sentinel produce parallel attestations
("both stand").**

For any given window the home_steward composes a state-of-the-
home report AND the home_sentinel composes an alert set. The two
attestations stand alongside in the audit chain. Operators read
both — the steward narrates state, the sentinel classifies
anomalies. Neither role overwrites the other's attestation.

Same adversarial-attestation pattern as D10's analyst + critic
(ADR-0090 Decision 3) — "outputs aren't averaged; both stand."
The discipline applies even when the two attestations disagree
("steward says all quiet; sentinel raises vacation_inconsistency
alert") — the operator decides.

**Decision 4 — Cross-domain awareness is a manifest contract,
not a runtime override.**

The home_steward's report includes a `cross_domain_narrative`
section ("operator has Travel block 2026-05-24..2026-05-28; D3
posture shifted to vacation-tightened"). This narrative is
LLM-composed from D2 morning_briefing + D3 incident attestations
read via `memory_recall scope=lineage`. The steward
**describes** the cross-domain context; it does NOT mutate D2 or
D3 state.

Cross-domain mutation (e.g. "vacation mode should tighten D3's
posture") happens via cascades in Phase D, not via direct
steward writes. Cascade audit events are joinable via
`cascade_source_*` fields per ADR-0067, so the operator can
trace which D5 event drove which D3 posture shift.

**Decision 5 — Four new builtin tools across Phases B–C; one
filesystem-class tool (routine_compose.v1).**

| Phase | Tool | Side-effects | Role consumer |
|---|---|---|---|
| B | `energy_anomaly_scan.v1` | read_only | energy_warden |
| B | `comfort_recommend.v1` | read_only | comfort_optimizer |
| C | `home_state_snapshot.v1` | read_only | routine_composer (also home_steward + sentinel follow-ups) |
| C | `routine_compose.v1` | filesystem | routine_composer |

Three are read_only; `routine_compose.v1` is filesystem because
it writes the routine envelope to the queue file. Same pattern
as `spaced_repetition_schedule.v1` — filesystem class, YELLOW
posture, requires_human_approval=True at per-call grant time.

The contrast with D10's all-read_only kit is intentional: D5 has
an explicit acting role (`routine_composer`) because the domain
manifest's value-prop is operator-approved automation. D10's
deliverable is a report; D5's deliverable is a state + a queued
action. Different surface = different ceiling.

## Phase plan

### Phase A — orchestration + security foundation (SHIPPED 2026-05-24)

- Add `home_steward` (researcher, GREEN) + `home_sentinel`
  (guardian, GREEN) to `trait_tree.yaml`, `genres.yaml`,
  `constitution_templates.yaml`, `tool_catalog.yaml`.
- No new builtin tools — both roles reuse existing kit.
- Skill manifests: `home_orchestration.v1`, `home_security.v1`.
- Birth scripts: `dev-tools/birth-home-steward.command`,
  `dev-tools/birth-home-sentinel.command`.
- Runbook: `docs/runbooks/d5-smart-home-ops.md`.

### Phase B — energy + comfort analysis (pending)

- Add `energy_warden` (researcher, GREEN) + `comfort_optimizer`
  (researcher, GREEN) to trait_tree / genres /
  constitution_templates / tool_catalog.
- Two new builtin tools:
  - `energy_anomaly_scan.v1` — deterministic per-device anomaly
    detection (current draw vs. operator-supplied baseline →
    {spike, drift, normal, missing_baseline}). read_only. ~20
    tests.
  - `comfort_recommend.v1` — deterministic comfort-tuning
    recommendation composer (lighting/temperature/scene from
    current state + operator profile + time-of-day window).
    read_only. ~20 tests.
- Skill manifests: `energy_optimization.v1`, `comfort_tuning.v1`.
- Birth scripts: `dev-tools/birth-energy-warden.command`,
  `dev-tools/birth-comfort-optimizer.command`.

### Phase C — routine queueing (pending)

- Add `routine_composer` (actuator, YELLOW).
- Two new builtin tools:
  - `home_state_snapshot.v1` — deterministic snapshot reader
    over recent home_state_snapshot attestations. read_only.
    ~15 tests.
  - `routine_compose.v1` — deterministic routine envelope
    composer + queue writer (filesystem class; queue at
    `data/d5/routine_queue.jsonl`). ~20 tests.
- Skill manifests: `routine_management.v1`, `vacation_mode.v1`.
- Birth script: `dev-tools/birth-routine-composer.command`.

### Phase D — cascade + umbrella + domain live (pending)

- No new roles or builtin tools.
- Skill manifest: `smart_home.v1` (umbrella composition).
- Cascade wiring in `handoffs.yaml`:
  - ACTIVATE: d2→d5 (daily_orchestration → home_orchestration —
    morning briefing seeds the state-of-the-home pass),
    d2→d5 (task_prioritization → routine_management —
    high-priority tasks compose routine envelopes),
    d5→d3 (home_security → incident_response — security alerts
    route to SOC incident correlator),
    d5→d2 (routine_management → reminder — routine fire times
    feed D2 schedule_reminder).
  - Declare INERT: d5→d1 (routines indexed; d1 routines_index
    capability doesn't exist), d5→d6 (power bill anomaly hand-
    off; d6 not shipped).
- Umbrella: `dev-tools/birth-d5-smart-home.command`.
- Flip `d5_smart_home.yaml` status to `live`.
- Flip this ADR to Accepted.

## Consequences

**Substrate-ready before connector.** The operator can install
+ birth + dispatch all five D5 roles without any IoT plugin
present. The roles read operator-supplied `home_state_snapshot`
attestations; when forest-home-assistant ships, it writes the
same attestation shape. This decouples D5's release schedule
from connector availability.

**Queue is the only path to actuation.** `routine_composer` is
the only acting role; its only output is a queue file
`data/d5/routine_queue.jsonl`. The forest-home-assistant
connector (or the operator manually) consumes the queue. D5 has
NO builtin tool that touches a Home Assistant entity directly.
This means: no rogue device actuation, no silent state mutation,
no surprise scene activation. Every routine that fires is one
operator-approved queue entry away.

**Steward + sentinel are intentional friction.** Operators who
want "just tell me the state of the house" will need to dispatch
both. The cost is one extra dispatch; the benefit is the
adversarial-attestation pattern from ADR-0090 — the steward and
the sentinel see the same snapshots through different lenses, so
the operator gets both narrative and anomaly classification side
by side.

**Cross-domain awareness is read-only.** D5 narrates D2/D3
context but never mutates them. The cascade events in Phase D
codify the cross-domain wiring explicitly so the audit chain can
join D5 → D2/D3 events; ad-hoc cross-domain writes from inside
D5 are forbidden by `forbid_external_disclosure` on every role.
