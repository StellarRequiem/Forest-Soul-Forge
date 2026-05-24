# Runbook — D5 Smart Home Brain (ADR-0091)

**Scope.** Operating the D5 Smart Home Brain domain end-to-end:
birth, skill install, first dispatch, observation, recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands D5 Phase A (this runbook will grow as Phases B–D ship).

**Phase context.** D5 ships in four phases per ADR-0091:

| Phase | New agent(s) | New builtin tool | Status |
|---|---|---|---|
| **A** | home_steward + home_sentinel | none — reuses existing | SHIPPED |
| **B** | energy_warden + comfort_optimizer | energy_anomaly_scan.v1 + comfort_recommend.v1 | SHIPPED |
| **C** | routine_composer | home_state_snapshot.v1 + routine_compose.v1 | SHIPPED |
| **D** | (cascade + umbrella + live) | none | SHIPPED |

Each phase = one commit + one push, so the operator can verify
phase N before phase N+1 fires.

---

## At a glance

D5's value proposition: **local-first IoT orchestration with
causal scheduling + counterfactual diagnostics + cross-domain
awareness**. The lab gathers home_state, narrates state across
rooms, detects security anomalies, optimizes energy + comfort,
and queues routines for operator-approved execution.

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `home_steward` | researcher | green | `home_orchestration.v1` | Reads home_state attestations; composes state-of-the-home report attestation. NEVER acts on devices; NEVER alerts; NEVER optimizes. |
| `home_sentinel` | guardian | green | `home_security.v1` | Reads home_state attestations; composes alert attestations for anomalous events (unfamiliar presence, vacation inconsistency, sensor drift). NEVER acts on devices; NEVER mutates state. |

Both Phase A agents are **operator-birthed via the approval queue**
per ADR-0091 — no auto-birth.

**Why separate steward + sentinel?** Orchestration and alerting
are different governance surfaces. The steward composes the
state narrative; the sentinel watches for anomalies. Both
read-only over home_state attestations, but the sentinel's
output is alert-shaped (per-anomaly records + severity + matched
pattern) while the steward's is narrative-shaped (per-room +
cross-domain context). Combining them would conflate state
description with anomaly classification + lose the per-attestation
provenance discipline the d5→d3 cascade depends on.

**Home Assistant not required for Phase A.** D5 reads
`home_state_snapshot` memory attestations. These can be
operator-supplied (a one-shot `memory_write` recording the
current state of the house) OR connector-supplied (the
forest-home-assistant plugin, when installed, ingests Home
Assistant entity state into memory). Phase A ships substrate-
only; the operator chooses ingestion strategy.

**Pacific time everywhere.** Per CLAUDE.md, all D5 timestamps
are Pacific time. The skill manifests explicitly tell the LLM to
use Pacific time.

---

## Phase A — birth + first dispatch

### Birth

```bash
./dev-tools/birth-home-steward.command
./dev-tools/birth-home-sentinel.command
```

Each script:
1. Kickstarts the daemon (loads the new role).
2. Checks for an existing agent (by name).
3. POSTs `/birth` with the role + agent_name; the constitution
   templates + tool catalog kits are resolved at birth time.
4. Sets posture to GREEN.

Birth payload uses an idempotency key per agent
(`birth-home-steward-d5`, `birth-home-sentinel-d5`) — re-running
the script is safe; the second run finds the existing agent and
skips birth.

### First dispatch — steward

Compose a state-of-the-home report for an evening window. The
prerequisite is at least one `home_state_snapshot` memory
attestation (operator-supplied OR connector-supplied) within the
last `window_minutes` (default 60).

```bash
# Seed a quick test snapshot (operator-supplied state)
curl -sX POST "http://127.0.0.1:7423/agents/${HOME_STEWARD_ID}/memory" \
  -H "X-FSF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "content": "front_door=closed, kitchen_lights=off, presence=alex, vacation_mode=off (2026-05-24T18:30 Pacific)",
    "tags": ["home_state_snapshot"],
    "scope": "lineage"
  }'

# Dispatch the orchestration skill
curl -sX POST "http://127.0.0.1:7423/agents/${HOME_STEWARD_ID}/tools/call" \
  -H "X-FSF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "tool_name": "home_orchestration",
    "tool_version": "1",
    "session_id": "d5-phase-a-first-dispatch",
    "args": {
      "window_slug": "evening-2026-05-24",
      "operator_reason": "first dispatch — verifying Phase A wiring"
    }
  }'
```

Expected output:
- `window_slug`: echo of the input
- `report_text`: structured per-room + cross-domain narrative
- `snapshot_count`: ≥ 1 (the snapshot you seeded)
- `chain_status`: `ok`
- `report_entry_id`: memory ID for the report attestation

### First dispatch — sentinel

After the steward composes a report, dispatch the sentinel for
the same window. It reads the snapshots + the matching steward
report and composes alerts.

```bash
curl -sX POST "http://127.0.0.1:7423/agents/${HOME_SENTINEL_ID}/tools/call" \
  -H "X-FSF-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "tool_name": "home_security",
    "tool_version": "1",
    "session_id": "d5-phase-a-sentinel-dispatch",
    "args": {
      "window_slug": "evening-2026-05-24",
      "operator_reason": "first sentinel dispatch"
    }
  }'
```

For a quiet household state, the sentinel will compose a "no
anomalies detected" attestation; for an unfamiliar
front-door-event snapshot it surfaces an
`unfamiliar_presence` alert with severity.

---

## Recovery

### Steward refuses to compose a report

**Symptom.** `home_orchestration.v1` halts with
`chain_status != "ok"`.

**Diagnosis.** The audit chain is broken. The
`require_chain_integrity_before_report` policy refuses to attest
on a broken chain.

**Fix.** Run `dev-tools/check-drift.sh` to find the broken
linkage; restore the missing entry OR roll back to a known-good
chain point. Do NOT remove the policy — see CLAUDE.md §0.

### Sentinel alerts on every dispatch

**Symptom.** Each `home_security.v1` dispatch produces an alert
of kind `surveillance_gap`.

**Diagnosis.** No `home_state_snapshot` memory attestations
within `window_minutes`. The sentinel correctly flags the
ingestion gap.

**Fix.** Refresh the connector ingestion (forest-home-assistant
plugin, when installed) OR supply a one-shot operator snapshot
via `memory_write`. The sentinel's gap-alert is a real signal,
not a false positive.

### Steward composes, sentinel disagrees

**Symptom.** Steward report says "all quiet"; sentinel raises a
`vacation_inconsistency` alert for the same window.

**Diagnosis.** This is the expected ADR-0091 Decision 3
adversarial-attestation pattern: the two attestations BOTH
stand. The steward narrates state; the sentinel classifies
anomalies. Operator reads both + decides.

**Fix.** Not a bug — it's the design. If the operator finds the
sentinel consistently over-flags, audit the alert attestations
weekly + tune the matched_pattern thresholds (Phase B+
introduces explicit thresholds via energy_anomaly_scan; the
sentinel's pattern matching today is LLM-driven).

---

## Cross-domain context (Phases B-D will activate cascades)

The D5 manifest at `config/domains/d5_smart_home.yaml` lists
four handoff_targets:
- `d2_daily_life_os` — routines (vacation mode, etc.)
- `d3_local_soc` — posture changes based on home state
- `d1_knowledge_forge` — routines indexed for "when did I last…" queries
- `d6_finance` — power-bill anomaly hand-off

Phase D activates a subset of these (d2→d5 routine triggers,
d5→d3 security alerts → SOC incidents). The d5→d6 finance
cascade stays INERT until D6 ships (final domain in the rollout
order).
