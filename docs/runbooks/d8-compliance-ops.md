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
| **C** | policy_enforcer | policy_lint.v1 | CLOSED |
| **D** | report_generator | audit_packet_generate.v1 | CLOSED |

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

## Phase C — enforcement

### What it adds

- `policy_enforcer` (actuator genre, YELLOW posture): runs lint
  evaluations + surfaces remediation proposals; NEVER applies a
  fix silently.
- `policy_lint.v1` builtin tool: reads a framework's `lint_rules`
  section + lints operator config files. Rule kinds:
  yaml_key_required, yaml_key_forbidden, file_max_age_days.
- SOC2 framework gains a `lint_rules` section: writes_enabled_
  documented, signed_commits_enforced, anonymous_disabled,
  token_rotation_recent.
- `policy_enforcement.v1` skill — six-step pipeline that runs
  the lint, synthesizes operator-readable proposals, escalates
  via delegate, attests via memory_write.

### Posture rationale

The enforcer is the ONLY action-class role in D8. YELLOW posture
default means every dispatched tool call routes through the
approval gate. Combined with:
- `forbid_silent_remediation` policy (governance layer)
- kit composition with NO `code_edit` / `shell_exec`
  (tool-availability layer)

…the agent cannot apply a fix to disk by any path. The
"enforcer" verb refers to surfacing + gating, not autonomous
execution.

### Birth the agent

```bash
./dev-tools/force-restart-daemon.command
./dev-tools/birth-policy-enforcer.command
```

### First proposal cycle

```
POST /agents/<PolicyEnforcer-D8-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "policy_enforcement",
    "skill_version": "1",
    "inputs": {
      "framework_id": "soc2",
      "target_paths": [".claude/settings.local.json", "config/genres.yaml"],
      "operator_reason": "first soc2 lint sweep"
    }
  }
}
```

The skill runs `policy_lint.v1`, condenses findings into proposal
text, escalates via `delegate.v1` to the operator approval queue,
and writes the proposal bundle to private memory tagged
`framework:soc2` for trending.

### Promotion to GREEN

Move from YELLOW to GREEN posture only after several proposal
cycles confirm the false-positive rate is low. The YELLOW
friction is the bedding-in step — false positives waste the
operator's time; YELLOW makes that cost visible.

### Authoring new lint rules

The framework yaml's `lint_rules` section is operator-authored
just like the system-level `controls` section. Each rule:

```yaml
- rule_id: <unique>
  kind: yaml_key_required | yaml_key_forbidden | file_max_age_days
  severity: high | medium | low
  params:
    # kind-specific; see policy_lint.v1's docstring
    key: ...
    expected_value: ...
    value_pattern: ...
    file_pattern: ...
  remediation: |
    Operator-readable fix description.
```

---

## Phase D — reporting + cascade

### What it adds

- `report_generator` (researcher genre, GREEN posture): wraps
  audit-chain entries + scan reports + evidence into a single
  packet via `audit_packet_generate.v1`.
- `audit_packet_generate.v1` builtin tool: walks the chain by
  framework_tag, buckets entries by tag family (scan reports /
  evidence / archive / remediation), produces a structured
  packet with sha256 over its body. Read-only.
- `compliance_reporting.v1` skill — five-step pipeline:
  verify_chain → generate_packet → synthesize_narrative →
  attest_packet → handoff_to_operator.
- `dev-tools/birth-d8-compliance.command` — umbrella that
  births all 5 D8 agents in order.
- Cascade entries in `handoffs.yaml` for all 5 D8 capabilities
  (compliance_scan, policy_enforcement, compliance_reporting,
  evidence_collection, long_term_archival).

### Umbrella birth

After pulling D8-D, the umbrella births all 5 agents:

```bash
./dev-tools/birth-d8-compliance.command
```

Each child script restarts the daemon and is idempotent; if any
agent already exists, the script skips its birth and proceeds.

### First audit packet

```
POST /agents/<ReportGenerator-D8-id>/tools/call
{
  "tool_name": "skill_run",
  "tool_version": "1",
  "session_id": "<uuid>",
  "args": {
    "skill_name": "compliance_reporting",
    "skill_version": "1",
    "inputs": {
      "framework_id": "soc2",
      "window_days": 90,
      "operator_reason": "first soc2 baseline packet"
    }
  }
}
```

The skill verifies chain integrity, walks the chain for entries
tagged `framework:soc2` within the window, produces a packet
body with `packet_sha256`, synthesizes an operator-readable
narrative header, attests the packet to private memory, and
routes the finished bundle to the operator approval queue.

### Cascades

Two cascade rules in `handoffs.yaml`:

```yaml
- source_domain: d4_code_review
  source_capability: review_signoff
  target_domain: d8_compliance
  target_capability: compliance_scan
- source_domain: d3_local_soc
  source_capability: incident_response
  target_domain: d8_compliance
  target_capability: compliance_scan
```

Every successful PR review fires a compliance scan; every
incident response fires a compliance evidence capture. By the
time the operator asks for an audit packet, the chain already
has the data — that's how "30-second audit packet" actually
works.

### Verifying a packet

The packet body includes a `packet_sha256` over its
canonical-json serialization. Before sharing a packet with an
external party:

1. Recompute the sha256 over the body (excluding the
   `packet_sha256` field itself).
2. Verify it matches the recorded value.
3. Verify the packet's `chain_status` is `ok`.

A packet whose hash doesn't match its body has drifted post-
generation; a packet whose chain_status is `broken` was generated
against a tainted chain. Either case: do not share.

---

## D8 is LIVE

All four phases closed. The 5 D8 agents + 3 new builtin tools +
5 skills + cascade wiring form the full continuous-compliance
loop:

```
    d4 PR review --cascade--> d8 compliance_scan
    d3 incident   --cascade--> d8 compliance_scan
                                |
                                v
                        evidence_collector
                                |
                                v
                        audit_archivist
                                |
                                v
                        report_generator -> 30-second audit packet
                                |
                                v
                        policy_enforcer (operator-gated remediation)
```

Operator-on-demand audit packet generation is the value-prop
demonstration. Adding ISO27001 / GDPR / HIPAA frameworks is
operator-authorable via new yamls under
`config/compliance_frameworks/` — no code change required.
