# Runbook — SOAR playbooks + purple-team exercises

**Substrate:** ADR-0066 (SOAR playbooks + playbook_pilot + purple_pete)
**Track:** D3 Local SOC Phase D
**Audience:** the operator authoring response playbooks and
adversary-simulation scenarios.

This runbook covers the response-and-test half of the D3 Local SOC:
how to write SOAR playbooks, how to write purple-team scenarios, and
how to review what they record on the audit chain.

---

## 1. What Phase D adds

Phase C (ADR-0065) made the SOC *detect* — continuous rules over the
telemetry stream, each match recorded as a `detection_fired` audit
event. Phase D closes the loop with *respond* and *test*:

```
  telemetry → detection_fired → playbook (respond) → playbook_executed
                     ↑                                       │
                     └──── purple_pete measures the whole ────┘
```

- **Playbooks** (`config/playbooks/*.yml`) — operator-authored
  response procedures. A playbook declares which detections fire it
  and an ordered list of steps. The `PlaybookEngine` resolves a
  fired detection against the playbook set and records a
  `playbook_executed` event.
- **playbook_pilot** — the actuator-genre agent that owns the
  playbook surface. Signature skill `playbook_run_review.v1`.
- **purple_pete** — the researcher-genre agent that runs synthetic
  attack scenarios against the SOC and measures detection coverage.
  Signature skill `purple_team_brief.v1`.

Two design rules run through all of it:

1. **Playbooks are operator-authored.** They are version-controlled
   YAML, reviewed like code. The substrate never synthesizes a
   playbook (ADR-0066 D1). LLM-assisted *authoring* may land later
   as a separate skill — never as autonomous authorship.
2. **Every state-changing step is approval-gated by default.** A
   step runs unattended only if you explicitly opted it out
   (ADR-0066 D2). The default is "ask me".

---

## 2. The playbook DSL

A playbook is one YAML file in `config/playbooks/`. The filename is
free-form; the `playbook_id` inside is the identity.

```yaml
playbook_id: reverse-shell-contain
version: '1'

trigger:
  detection_rule_ids: [reverse_shell_listener_port]
  min_severity: high
  cooldown_seconds: 600

approval:
  default: required_human
  steps_auto_approved:
    - collect_forensics

steps:
  - id: collect_forensics
    action: archive_evidence
    args:
      artifact_id: "${detection.rule_id}:${detection.batch_id}"
      transition_type: acquire
      attestor_reason: "playbook ${playbook_id} fired on ${detection.rule_id}"

  - id: isolate_process
    action: isolate_process
    args:
      pid: "${detection.evidence.pid}"
    requires_human_approval: true

  - id: notify_operator
    action: delegate
    args:
      to: operator
      message: "SOAR ${playbook_id} ran on ${detection.rule_id}"
    requires_human_approval: false

postconditions:
  audit_event_type: playbook_executed
```

### trigger

| field | meaning |
|---|---|
| `detection_rule_ids` | non-empty list. The playbook fires when a `detection_fired` event carries one of these `rule_id`s. Use the rule ids from `config/detection_rules/`. |
| `min_severity` | `informational` < `low` < `medium` < `high` < `critical`. The playbook fires only when the detection's severity is at or above this. |
| `cooldown_seconds` | re-fire suppression — see §4. Required; use `0` for "never suppress". |

### approval

| field | meaning |
|---|---|
| `default` | must be `required_human` — the v1 default-deny posture. |
| `steps_auto_approved` | list of step ids that run **without** operator approval. Every id must name a real step. |

### steps

An ordered list. Each step is **one** catalog tool or skill
invocation — no branches, no loops (ADR-0066 D1). If you need a
branch, write two playbooks with different triggers.

| field | meaning |
|---|---|
| `id` | unique within the playbook. The cooldown fingerprint and the `playbook_executed` step history key on it. |
| `action` | a catalog tool name (`isolate_process`, `delegate`) or skill name (`archive_evidence`). Version-free — resolved at dispatch. |
| `args` | the invocation arguments. May carry `${...}` references (§5). |
| `requires_human_approval` | optional per-step override — see §3. |

### postconditions

`audit_event_type` — the event type the engine emits. Leave it
`playbook_executed` unless you have a specific reason.

The DSL is deliberately strict: a step carrying an unknown key
(`if`, `loop`, `on_failure`, …) is **rejected** at parse time so the
gap is visible, never silently ignored.

---

## 3. The approval model (ADR-0066 D2)

The posture is **default-deny**: a step is approval-gated unless you
explicitly opt it out. There are exactly two opt-out channels, both
explicit:

1. list the step id in `approval.steps_auto_approved`, or
2. set `requires_human_approval: false` on the step.

A per-step `requires_human_approval: true` always wins — it can
escalate a listed step back to approval-required, but nothing can
silently de-escalate. A step that is **both** in
`steps_auto_approved` **and** sets `requires_human_approval: true`
is a contradiction and the parser rejects the playbook.

| step declaration | result |
|---|---|
| in `steps_auto_approved`, no per-step field | auto-approved |
| `requires_human_approval: false` | auto-approved |
| `requires_human_approval: true` | approval-gated |
| neither | approval-gated (default) |

Approval-gated steps show in the operator **Pending tab**;
auto-approved steps are recorded ready-to-dispatch. The run's
`outcome` is `approval_pending` if any step is gated, else
`completed`.

**Rule of thumb:** auto-approve only read-only or
chain-of-custody steps (evidence collection, notifications).
Anything that kills a process, blocks network, or quarantines a
file stays gated.

---

## 4. Cooldown semantics (ADR-0066 D4)

A noisy detection that fires every batch must not become an action
storm. The cooldown fingerprint is:

```
(playbook_id, detection_rule_id, target_entity)
```

`target_entity` is the primary subject of the detection — today the
engine uses the first `matched_event_ids` entry (a future detection
payload may carry an explicit `target_entity`). The same playbook
firing for the **same** target inside `cooldown_seconds` is
suppressed; a **different** target is not. Pick the window to match
the cost of the response: a notification-only playbook can use a
short cooldown; a containment playbook should use a wide one.

---

## 5. `${...}` argument interpolation

Step `args` may reference the firing detection. The engine resolves:

- `${playbook_id}`, `${playbook_version}`
- `${detection.<dotted.path>}` — any field of the `detection_fired`
  event_data: `${detection.rule_id}`, `${detection.severity}`,
  `${detection.technique}`, `${detection.batch_id}`,
  `${detection.match_count}`.

A reference that cannot be resolved is left as the **literal**
`${...}` string — an honest signal that the substrate could not
bind it. The starter playbooks reference `${detection.evidence.*}`
fields (e.g. a pid or an artifact path); those resolve only once
your detections carry an `evidence` block. Until then the operator
supplies the value at dispatch.

---

## 6. Writing a playbook — checklist

1. Copy a starter playbook from `config/playbooks/` as a base.
2. Set `playbook_id` and point `trigger.detection_rule_ids` at real
   rule ids from `config/detection_rules/`.
3. Set `min_severity` and a `cooldown_seconds` matched to the
   response cost.
4. Write the steps. Keep destructive steps **out** of
   `steps_auto_approved`.
5. Validate it parses:
   ```
   .venv/bin/python -c "from pathlib import Path; \
     from forest_soul_forge.security.playbook import parse_playbooks_from_dir; \
     p,f=parse_playbooks_from_dir(Path('config/playbooks')); \
     print('ok',len(p)) if not f else [print('FAIL',x) for x in f]"
   ```
   `dev-tools/diagnostic/section-01-static-config.command` runs the
   same check — one bad playbook fails CI.
6. Commit the playbook via the normal git workflow. playbook_pilot
   **never** writes `config/playbooks/` — the operator owns it
   (`forbid_playbook_authorship`).

---

## 7. The purple-team scenario DSL

A scenario is one YAML file in `config/purple_pete_scenarios/`. It
emulates one ATT&CK technique as synthetic telemetry and names the
detection rule the SOC *should* catch it with.

```yaml
scenario_id: spctl-gatekeeper-disable
version: '1'
description: Emulates a Gatekeeper bypass attempt.
technique: attack.T1553.001

events:
  - source: endpoint_sensor
    event_type: process_spawn
    severity: warn
    payload:
      process:
        image: /usr/sbin/spctl

expect:
  detection_rule_id: gatekeeper_disable_attempt
```

| field | meaning |
|---|---|
| `scenario_id` | identity. |
| `technique` | the ATT&CK technique this emulates. |
| `events` | non-empty list of synthetic telemetry events. `event_type` must be a telemetry `EVENT_TYPES` value, `severity` a `SEVERITIES` value. |
| `expect.detection_rule_id` | the rule that should fire. Optional — omit for a pure probe with no expectation. |

When `purple_pete` runs a scenario it:

1. materialises each event into a real `TelemetryEvent`, stamped
   with `purple_team_run_id` + `simulation: true` provenance;
2. writes them **only** to the simulation store
   (`data/telemetry_simulation.sqlite`) — never production;
3. replays them through the production `DetectionEngine` in
   simulation mode (`audit_chain=None` — synthetic detections reach
   no chain);
4. records one `purple_team_run_completed` event with the coverage
   result.

A scenario whose `expect.detection_rule_id` does **not** fire is a
**coverage gap** — a real hole in the rule set. The starter library
ships `process-discovery-gap.yml` as a deliberate standing gap:
it is, in effect, the spec for a rule `detection_engineer` should
author.

---

## 8. Reviewing the logs

Phase D writes two new audit-chain event types:

- **`playbook_executed`** — one per fired playbook. Carries
  `playbook_id`, `playbook_version` (sha256 of the playbook body),
  `trigger_detection_id`, the per-step history with each step's
  `approval_state`, and `outcome`.
- **`purple_team_run_completed`** — one per scenario run. Carries
  `scenario_id`, `technique`, `detected`, `coverage_gap`,
  `detected_rule_ids`, timing, and `simulation: true`.

Two signature skills turn these into operator briefs:

- **`playbook_run_review.v1`** (playbook_pilot) — outcome tally,
  approval backlog, cooldown-storm / version-drift /
  auto-approved-action flags.
- **`purple_team_brief.v1`** (purple_pete) — coverage summary, the
  list of coverage gaps to route to `detection_engineer`,
  never-responded and regression flags.

Both skills are read-only. They take the recent events as an input
list (the operator or a substrate query supplies it) and write the
brief to the agent's private memory, tagged by date so historical
briefs are queryable.

---

## 9. What the starter libraries ship

`config/playbooks/` — 3 starter playbooks:

| playbook | trigger rule | shape |
|---|---|---|
| `gatekeeper-disable-response` | `gatekeeper_disable_attempt` | fully auto-approved (preserve + notify) |
| `reverse-shell-contain` | `reverse_shell_listener_port` | mixed — auto forensics, gated `isolate_process`, auto notify |
| `launchdaemon-persistence-triage` | `launchdaemon_persistence_write` | triage — preserve + notify auto, human review gated |

`config/purple_pete_scenarios/` — 5 starter scenarios: four that the
starter detection rules catch (`osascript`, `spctl`, `keychain`,
`reverse-shell`) and one standing coverage gap
(`process-discovery-gap`).

---

## 10. Operator duties

- **Author + review every playbook and scenario.** The substrate
  executes what you write; a bad playbook is an operator bug.
- **Review the Pending tab.** Default-deny means every gated step
  waits for you.
- **Audit `steps_auto_approved` quarterly.** Each entry is an action
  that fires at machine speed with you out of the loop.
- **Route coverage gaps.** A `purple_team_brief` gap is a missing
  detection rule — hand it to `detection_engineer`.
- **Posture.** `playbook_pilot` and `purple_pete` both birth at
  YELLOW. Promote `purple_pete` to GREEN after reviewing the first
  few exercise reports; `playbook_pilot` stays YELLOW — every SOAR
  action is operator-gated regardless of posture.

---

## See also

- `docs/decisions/ADR-0066-soar-playbooks.md` — the substrate ADR
- `docs/decisions/ADR-0078-d3-local-soc-advanced-rollout.md` — the
  D3 rollout umbrella
- `docs/runbooks/detection-as-code.md` — the Phase C detection half
- `config/detection_rules/` — the rules that fire playbooks
