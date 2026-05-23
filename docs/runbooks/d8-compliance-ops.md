# Runbook — D8 Compliance Auditor (ADR-0085)

**Scope.** Operating the D8 Compliance Auditor domain end-to-end:
birth, skill install, first dispatch, observation, recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands D8 Phase A (this runbook will grow as Phases B–D ship).

**Phase context.** D8 ships in four phases per ADR-0085:

| Phase | New agent(s) | New builtin tool | Status |
|---|---|---|---|
| **A** | audit_archivist + evidence_collector | none — reuses file_integrity + audit_chain_verify | CLOSED |
| **B** | compliance_scanner | framework_check.v1 | CLOSED |
| C | policy_enforcer | policy_lint.v1 | pending |
| D | report_generator | audit_packet_generate.v1 | pending |

Each phase = one commit + one push, so the operator can verify
phase N before phase N+1 fires.

---

## At a glance

D8's value proposition: **continuous compliance, not annual
audits.** The hash-chained audit log (ADR-0049 signed; ADR-0073
segmented) is already the evidence trail; D8 builds the roles
that operate this substrate against framework-specific rule sets.

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `audit_archivist` | guardian | green | `long_term_archival.v1` | Verifies + attests long-term archival of compliance packets. Never mutates archive bytes. |
| `evidence_collector` | guardian | green | `evidence_collection.v1` | Captures snapshots of compliance-relevant sources (configs, audit chain windows, file integrity) into the evidence corpus. |

Both Phase A agents are **operator-birthed via the approval queue**
per ADR-0085 — no auto-birth.

**Why two roles, not one?** Capture and archival are different
governance surfaces. The collector PRODUCES evidence entries
(short-cycle, runs on every cascade fire); the archivist VERIFIES
and RETAINS them long-term (long-cycle, runs on periodic operator
sweep). Different cadences, different policies; one role would
conflate them.

---

## Phase A — chain-of-custody foundation

### 1. Restart the daemon

The new role definitions land in `trait_tree.yaml` + `genres.yaml`
+ `constitution_templates.yaml`; the per-role kits land in
`tool_catalog.yaml`. The daemon loads these at lifespan boot, so
a restart is required before the births can pick them up.

```bash
./dev-tools/force-restart-daemon.command
```

Verify in `/healthz`'s `startup_diagnostics` that the genre engine
reports `status: ok` and that `audit_archivist` + `evidence_collector`
appear in `/genres` under the `guardian` genre's `roles` list.

### 2. Birth the agents

```bash
./dev-tools/birth-audit-archivist.command
./dev-tools/birth-evidence-collector.command
```

Each script is idempotent — re-running it skips the birth if the
agent already exists. Both set posture GREEN as the default per
ADR-0085 Decision 5 (read-only attestation is non-acting).

### 3. Confirm the evidence corpus and archive roots

The birth scripts `mkdir -p` the canonical paths if missing:

```
data/compliance/
data/compliance/evidence/   ← evidence_collector writes attestations
data/compliance/archive/    ← operator-driven long-term storage
```

The agents themselves never `mkdir` — the per-tool allowed_paths
constraints are scoped to these existing roots.

### 4. First dispatch — evidence_collection.v1

The operator drives the first capture explicitly (no cascade yet
— cascades land in Phase D when audit_packet_generate.v1 is the
downstream consumer).

```
POST /agents/<EvidenceCollector-D8-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "evidence_collection",
    "skill_version": "1",
    "inputs": {
      "evidence_id": "soc2_CC6.1_keyfile_2026q2",
      "source_paths": ["data/compliance/evidence/keyfile-2026q2.json"],
      "framework_tag": "soc2",
      "control_id": "CC6.1",
      "operator_reason": "quarterly key access evidence sweep"
    }
  }
}
```

The skill writes an `evidence_captured` entry to the collector's
private memory tagged `framework:soc2`, `evidence_id:<id>`, and
`attestor:EvidenceCollector-D8`. Recall by tag to reconstruct
the per-framework evidence chain.

### 5. First dispatch — long_term_archival.v1

After the operator bundles evidence into an archive at
`data/compliance/archive/<archive_id>.bundle`, attest it:

```
POST /agents/<AuditArchivist-D8-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "long_term_archival",
    "skill_version": "1",
    "inputs": {
      "archive_id": "soc2_2026Q2_evidencepack",
      "archive_path": "data/compliance/archive/soc2-2026q2.bundle",
      "framework_tag": "soc2",
      "transition_type": "acquire",
      "retention_floor_days": 365,
      "operator_reason": "Q2 2026 SOC2 evidence bundle archival"
    }
  }
}
```

The skill walks the prior attestation chain, verifies the bundle's
sha256, verifies the audit chain itself, and emits ATTEST /
HALT depending on the rules in `evaluate_retention`.

### 6. Observation surface

Per-evidence chain: `memory_recall.v1` with `tags:[evidence_id]`.

Per-framework rollup: `memory_recall.v1` with `tags:[framework:soc2]`.

Audit chain: the standard `audit_chain_verify.v1` tool +
`examples/audit_chain.jsonl` — every memory_write the collector
or archivist performs joins the chain via the standard
`memory_written` event.

### 7. Recovery patterns

- **HALT verdict on `long_term_archival`** (chain_broken,
  tamper_suspected, retention_floor_violation): the attestation
  is still written, marking the halt with `HALT_CODE`. Operator
  investigates the named root cause before proceeding.
- **Capture fails with allowed_paths error**: the source root is
  outside the per-tool constraint set patched at birth. Edit the
  constitution (or re-run the birth script after extending the
  default constraint list) to expand the scope.
- **`evidence_collector` skill error on duplicate evidence_id**:
  not currently a HALT; duplicates are recorded as additional
  attestations of the same evidence_id (immutable history). The
  operator decides whether the new capture confirms or supersedes
  the prior one.

---

## Phase B — scanning surface

### What it adds

- `compliance_scanner` (guardian, green): runs framework rule
  evaluations, surfaces gaps, NEVER acts on findings.
- `framework_check.v1` builtin tool: loads a framework yaml,
  evaluates each rule (required_file, forbidden_pattern,
  required_attestation, audit_event_required), returns per-rule
  + per-control verdicts.
- `config/compliance_frameworks/soc2.yaml` seed framework with
  CC6.1 / CC7.2 / CC8.1 / A1.2 / C1.1 controls.
- `compliance_scan.v1` skill — five-step pipeline: prior_context
  → verify_chain → run_framework → synthesize_report →
  write_report.

### Birth the agent

```bash
./dev-tools/force-restart-daemon.command
./dev-tools/birth-compliance-scanner.command
```

The daemon restart is required so it picks up the new
`compliance_scanner` role + `framework_check.v1` registration.

### First scan

```
POST /agents/<ComplianceScanner-D8-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "compliance_scan",
    "skill_version": "1",
    "inputs": {
      "framework_id": "soc2",
      "operator_reason": "first SOC2 baseline scan"
    }
  }
}
```

The skill returns rule_results + an operator-readable gap
report; the gap report is written to private memory tagged
`framework:soc2` for trending.

### Adding frameworks

The framework loader is YAML-driven. To add ISO27001 / GDPR /
HIPAA / personal-policy, drop a `config/compliance_frameworks/
<framework_id>.yaml` mirroring the schema of `soc2.yaml`. The
scanner picks them up on next dispatch — no code change required.

### Observation surface

Per-framework reports: `memory_recall.v1` with
`tags:[framework:<framework_id>]`.

All gap reports: `memory_recall.v1` with
`tags:[compliance_gap_report]`.

---

## Phase C–D (pending)

This runbook will grow as Phases C / D ship. Each phase
adds one role, one builtin tool, one skill, and one runbook
section here.
