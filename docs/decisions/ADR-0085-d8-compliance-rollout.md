# ADR-0085 — D8 Compliance Auditor domain: rollout

**Status:** Proposed (2026-05-22). Phase A CLOSED (commit D8-A) — audit_archivist + evidence_collector roles + skills + birth scripts shipped; births operator-driven.
**Date:** 2026-05-22
**Tracks:** Domain Rollout / Compliance
**Supersedes:** none
**Builds on:** ADR-0049 (per-event signatures — the audit chain IS
the evidence trail), ADR-0050 (encryption-at-rest — covered evidence
retention), ADR-0062 (supply-chain IoC scanner — license / signature
compliance signal), ADR-0067 (cross-domain orchestrator — D8 is a
target of d4 and d3 cascades), ADR-0033 (Security Swarm —
verifier_loop is the closest cousin role).

## Context

D3 Local SOC closed 2026-05-22 with all 15 SOC agents alive
(ADR-0078, Bursts 342-459). D4 Code Review closed before D3
(ADR-0077, Bursts 331-340). Per ADR-0067's rollout-order plan,
**D8 Compliance Auditor** is next.

D8's value proposition (from `config/domains/d8_compliance.yaml`):

> Continuous compliance, not annual audits. License/security/policy
> scans on every Code Review PR + scheduled sweeps. The hash-chained
> audit log IS the evidence trail — audit packet generation takes
> 30 seconds, not 30 hours. Framework-agnostic: detection rules per
> framework; operator picks which apply.

Most of the substrate is already in place. The audit chain
(ADR-0049 signed; ADR-0073 segmented) gives D8 the tamper-evident
evidence stream every compliance framework asks for. The IoC
scanner (ADR-0062) gives D8 the license + supply-chain signal.
Reality Anchor (ADR-0063) gives D8 the ground-truth substrate
for "the operator has asserted X." D8 builds the ROLES that
operate this substrate against framework-specific rule sets.

Five new roles per `config/domains/d8_compliance.yaml`:

| Role | Capability | Posture |
|---|---|---|
| `audit_archivist` | long_term_archival | GREEN (read-only) |
| `evidence_collector` | evidence_collection | GREEN (read-only) |
| `compliance_scanner` | compliance_scan | GREEN (read-only) |
| `policy_enforcer` | policy_enforcement | YELLOW (operator-gated) |
| `report_generator` | compliance_reporting | GREEN (read-only synthesis) |

## Decision

**Decision 1 — Five new roles, no new genres.**

| Role | Genre | Trait emphasis | Side-effects ceiling |
|---|---|---|---|
| `audit_archivist` | guardian | audit + double_checking + transparency | read_only (long-term archival = chain-of-custody for compliance artifacts) |
| `evidence_collector` | guardian | audit + thoroughness + evidence_demand | read_only (passive evidence capture; never mutates source data) |
| `compliance_scanner` | guardian | audit + thoroughness + double_checking | read_only (rule-driven framework checks; surfaces gaps) |
| `policy_enforcer` | actuator | caution + evidence_demand + formality | filesystem (gated remediations; YELLOW posture forces operator gate) |
| `report_generator` | researcher | thoroughness + research_thoroughness + transparency | read_only (synthesizes evidence + scans into operator-readable packets) |

The roles slot into the existing five genres — no new genre
needed (per ADR-0078 §Decision 1 precedent which added six new
roles across three existing genres). Compliance work is
fundamentally:
1. **Watching** (evidence_collector, observer-shaped but guardian
   for read-only ceiling + audit-trait emphasis like the SOC's
   forensic_archivist),
2. **Verifying** (compliance_scanner, audit_archivist — guardian
   genre, same as forensic_archivist / telemetry_steward),
3. **Synthesizing** (report_generator — researcher genre like
   detection_engineer; produces operator-readable artifacts
   from evidence streams), and
4. **Gating** (policy_enforcer — actuator genre; the only
   action-class compliance role).

**Decision 2 — Framework-loader substrate, SOC2 seed only at MVP.**

Compliance rules live in a new directory `config/compliance_frameworks/`
mirroring `config/detection_rules/` (ADR-0065). One file per
framework: `soc2.yaml` ships at MVP as the seed. Operators add
ISO27001 / GDPR / HIPAA via additional YAML files at runtime;
the loader is framework-agnostic.

MVP scope is intentionally narrow:
- SOC2 framework with the key Trust Service Criteria controls
  expressed as rules.
- The loader machinery (read directory → parse → expose to
  `framework_check.v1` tool).
- Operator-facing documentation for adding new frameworks.

ISO27001 / GDPR / HIPAA / CCPA / personal-policy are operator-
authorable, not Forest-shipped. The framework-loader substrate
+ SOC2 seed is sufficient demonstration; framework breadth is
operator-driven the same way detection rules are operator-driven
per ADR-0065.

**Decision 3 — Four-phase delivery.**

Each phase = one commit + one push; the work proceeds through
the phases sequentially because each builds on the prior:

- **Phase A — chain-of-custody foundation.** `audit_archivist`
  + `evidence_collector`. The two read-only roles that establish
  the evidence pipeline. No new builtin tools — reuse
  `file_integrity.v1`, `audit_chain_verify.v1`,
  `memory_recall.v1`, `memory_write.v1`. Skill manifests:
  `evidence_collection.v1`, `long_term_archival.v1`.

- **Phase B — scanning surface.** `compliance_scanner` +
  new builtin tool `framework_check.v1`. The tool reads
  `config/compliance_frameworks/<framework>.yaml`, evaluates
  rules against the operator-supplied context (audit chain
  window, file paths, evidence corpus), surfaces gaps. SOC2
  ships as the seed framework.

- **Phase C — enforcement.** `policy_enforcer` + new builtin
  tool `policy_lint.v1`. The tool reads operator-supplied
  configurations + compliance rules, surfaces lint findings,
  proposes remediation. YELLOW posture per Decision 5: every
  proposed remediation is operator-gated.

- **Phase D — reporting + cascade.** `report_generator` +
  new builtin tool `audit_packet_generate.v1`. Wraps a window
  of audit chain + compliance scan results + evidence
  attestations into an operator-readable packet (the
  audit-packet-in-30-seconds value prop). Plus cascade wiring:
  the pre-existing `d4→d8` cascade in handoffs.yaml stays;
  `d3→d8` (incident_response → compliance_scan) was added by
  ADR-0078 and stays. Phase D adds the umbrella birth script
  + diagnostic-harness extensions + the final
  `d8_compliance.yaml` status flip to `live`.

**Decision 4 — Cascade defaults.**

The two pre-existing cascades (declared in `handoffs.yaml`):

```yaml
- source_domain: d4_code_review
  source_capability: review_signoff
  target_domain: d8_compliance
  target_capability: compliance_scan
  reason: "Every PR review fires a compliance scan."

- source_domain: d3_local_soc
  source_capability: incident_response
  target_domain: d8_compliance
  target_capability: compliance_scan
  reason: "Every incident response triggers a compliance evidence capture."
```

Both stay. Phase D's `audit_packet_generate.v1` tool consumes
the audit chain entries emitted by these cascades — that's how
"30-second audit packet" actually works: the compliance routes
are ALREADY in the chain by the time the operator asks for the
packet.

**Decision 5 — Posture defaults.**

- `audit_archivist` (guardian, read-only): **GREEN.** Long-term
  archival attestation is non-acting; same posture rationale
  as `forensic_archivist` per ADR-0078 Decision 5.
- `evidence_collector` (guardian, read-only): **GREEN.** Passive
  evidence capture; the alert path is operator-readable.
- `compliance_scanner` (guardian, read-only): **GREEN.** Rule
  evaluation surfaces gaps; remediation is `policy_enforcer`'s
  surface.
- `policy_enforcer` (actuator, filesystem): **YELLOW.** Every
  proposed remediation operator-gated regardless of posture;
  YELLOW posture adds bedding-in friction over the first weeks
  of operation. Same posture rationale as `playbook_pilot`.
- `report_generator` (researcher, read-only): **GREEN.**
  Synthesis is non-acting; the operator decides what to do
  with the packet.

**Decision 6 — Use the D3/D4 rollout pattern.**

Each phase follows the established template:
1. T1 ADR section (this is the rollup; Phase A/B/C/D
   sub-sections accumulate as they ship).
2. T2 trait_tree + genres + constitution_templates +
   tool_catalog entries.
3. T3 (when applicable) new builtin tool + registration +
   unit tests.
4. T4 birth scripts per role + skill manifests.
5. T5 operator runbook section.
6. T6 status flip per phase; final umbrella + cascade wiring
   in Phase D.

## Consequences

**Positive:**
- D8 becomes the highest-leverage value-prop demonstration for
  operators considering Forest professionally. Every successful
  D4 / D3 route already accrues compliance evidence in the
  chain; D8 just exposes it as packets.
- The framework-loader pattern (Decision 2) extends naturally:
  ISO27001 / GDPR / HIPAA all become operator-authorable YAMLs
  without core-code changes.
- The cascade wiring (Decision 4) means D8 is *continuous* by
  default, not on-demand. Drift caught at minute zero.

**Negative:**
- Five new roles is a lot to bed in. The phased delivery
  (Decision 3) mitigates by shipping each role with its
  surface area in isolation; operators verify Phase A before
  Phase B fires.
- SOC2 seed only at MVP means operators wanting ISO27001 /
  GDPR coverage have to author rules themselves. The
  framework-loader is the substrate; the rule libraries are
  operator-side. Acceptable per Decision 2 (mirrors the
  ADR-0065 detection-rule pattern).

**Risk register:**
- `policy_enforcer`'s YELLOW posture must be enforced
  end-to-end — a remediation path that bypasses the gate is a
  compliance-violation generator, not a fixer. Constitution
  policies + governance pipeline gate at substrate level.
- `audit_packet_generate.v1` reads the audit chain; chain
  segmentation (ADR-0073) means long-history packets may need
  segment-aware walks. Phase D scopes the v1 tool to the
  active segment; long-history packets are deferred.

## Phase tracking

- **Phase A** — chain-of-custody foundation. Status: CLOSED
  (2026-05-22). Shipped: `audit_archivist` + `evidence_collector`
  roles in trait_tree / genres / constitution_templates /
  tool_catalog; birth scripts `birth-audit-archivist.command`
  + `birth-evidence-collector.command`; skill manifests
  `examples/skills/evidence_collection.v1.yaml` +
  `examples/skills/long_term_archival.v1.yaml`; operator runbook
  `docs/runbooks/d8-compliance-ops.md`. No new builtin tools.
- **Phase B** — scanning surface. Status: NOT STARTED.
- **Phase C** — enforcement. Status: NOT STARTED.
- **Phase D** — reporting + cascade. Status: NOT STARTED.

ADR flips to **Accepted** when Phase D closes and
`d8_compliance.yaml` status is updated to `live`.
