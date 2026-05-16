# ADR-0078 — D3 Local SOC domain: advanced rollout

**Status:** Proposed
**Date:** 2026-05-16
**Tracks:** Domain Rollout / Security
**Supersedes:** none
**Builds on:** ADR-0033 (Security Swarm — the 9-agent blue team),
ADR-0067 (cross-domain orchestrator), ADR-0049 (per-event
signatures), ADR-0050 (encryption-at-rest), ADR-0051 (per-tool
subprocess sandbox), ADR-0062 (supply-chain scanner)
**Pulls in:** ADR-0064 (telemetry pipeline — queued),
ADR-0065 (detection-as-code — queued), ADR-0066 (SOAR
playbooks — queued)

## Context

ADR-0077 closed the D4 Code Review advanced rollout in 10 bursts
(plus B341 correction). The same template now applies to D3
Local SOC — but D3 is materially larger:

- D4 added 3 new agents to an existing triune.
- **D3 adds 6 new agents** to an existing 9-agent Security
  Swarm (ADR-0033), bringing the SOC team to 15 agents total.
- D4 needed zero new infrastructure ADRs.
- **D3 pulls in three queued ADRs (0064/0065/0066)** for
  telemetry pipeline + detection-as-code + SOAR playbook DSL.

The D3 manifest's notes call out why this is the right
investment: "This is the domain Forest is MOST architecturally
suited for — audit chain + signatures + encryption + sandbox +
IoC catalog + Reality Anchor are ALL load-bearing for production
SOC work." The existing substrate makes D3 the highest-leverage
domain rollout in the ten-domain plan.

The existing 9-agent Security Swarm covers baseline monitoring +
triage (log_lurker, anomaly_ace, response_rogue, vault_warden,
patch_patrol, gatekeeper, net_ninja, zero_zero, deception_duke).
What the operator hits in real-world SOC work that the existing
swarm does NOT cover:

1. **Telemetry pipeline.** The swarm reads logs ad-hoc; a real
   SOC needs continuous telemetry ingestion with audit-grade
   integrity. `telemetry_steward` owns this.
2. **Detection engineering.** The swarm uses hardcoded patterns;
   a real SOC writes detection rules (Sigma format, MITRE ATT&CK
   tags) that evolve with the threat landscape.
   `detection_engineer` owns this.
3. **SOAR playbook orchestration.** Today's incident_response
   reactions are coded paths in `response_rogue`. A real SOC
   parameterizes them as playbooks the operator can review and
   tune. `playbook_pilot` owns the playbook DSL +
   parameterization.
4. **Threat intelligence curation.** The swarm matches against a
   static IoC catalog. A real SOC keeps threat intel current
   from external feeds (MISP, AlienVault OTX, vendor advisories).
   `threat_intel_curator` owns the IoC catalog lifecycle.
5. **Forensic chain-of-custody.** vault_warden cleans up after
   incidents; chain-of-custody for the artifacts (memory dumps,
   pcap captures, log slices) is operator-manual today.
   `forensic_archivist` owns it.
6. **Purple-team exercises.** Continuous deception_duke is the
   red rail; there's no integrated blue-vs-red exercise loop.
   `purple_pete` runs scheduled adversary emulation against
   the swarm's detection coverage + reports gaps.

## Decision

**Decision 1 — Six new roles, three genres.**

| Role | Genre | Trait emphasis | Side-effects ceiling |
|---|---|---|---|
| `telemetry_steward` | observer | audit + thoroughness + double_checking | network (read-only by default; collects from local sockets) |
| `detection_engineer` | researcher | cognitive + thoroughness + lateral_thinking | filesystem (writes Sigma rule files to `config/detections/`) |
| `playbook_pilot` | actuator | caution + evidence_demand + formality | external (executes SOAR steps; every action operator-gated under YELLOW posture) |
| `threat_intel_curator` | researcher | thoroughness + research_thoroughness | network (pulls from allowlisted threat-intel feeds) |
| `forensic_archivist` | guardian | audit + double_checking | read_only (chain-of-custody is verification, not action) |
| `purple_pete` | actuator | caution + transparency + lateral_thinking | external (drives adversary emulation; explicitly sandboxed) |

**Decision 2 — Three new ADRs unlock the work.**

The D3 advanced rollout cannot ship in isolation — three
infrastructure ADRs must land first or alongside:

- **ADR-0064 telemetry pipeline.** Defines the continuous-
  ingest substrate that `telemetry_steward` operates against.
  Schema for telemetry events, retention policy, encryption-
  at-rest wiring, audit-chain entry types. Estimated 5-6
  bursts.
- **ADR-0065 detection-as-code.** Sigma rule format adoption,
  rule loader, MITRE ATT&CK tagging convention, rule-eval
  engine that `detection_engineer` writes against. Estimated
  5-6 bursts.
- **ADR-0066 SOAR playbook DSL.** YAML playbook format that
  `playbook_pilot` parameterizes + executes, with operator-
  approval gates at every action step. Builds on the existing
  skill manifest engine (ADR-0031). Estimated 4-5 bursts.

Each of these is a SEPARATE ADR with its own arc. ADR-0078
documents the rollup; the three infrastructure ADRs ship under
their own numbers.

**Decision 3 — Phased delivery.**

The 6 new agents have different infrastructure dependencies.
Phase the rollout:

- **Phase A (no new infra):** `forensic_archivist` (read-only
  chain-of-custody — uses existing audit_chain_verify +
  file_integrity tools). 1-2 bursts.
- **Phase B (after ADR-0064 lands):** `telemetry_steward`
  + `threat_intel_curator` (both consume the telemetry
  pipeline). 2-3 bursts.
- **Phase C (after ADR-0065 lands):** `detection_engineer`
  (writes Sigma rules against the rule engine). 2-3 bursts.
- **Phase D (after ADR-0066 lands):** `playbook_pilot`
  + `purple_pete` (both consume SOAR playbooks; purple_pete
  exercises the swarm's response paths). 2-3 bursts.

Each phase is operator-approved + birth-gated like D4 was.

**Decision 4 — Cascade rules.**

The D3 manifest already declares `handoff_targets`:
`d8_compliance` (SOC events → compliance evidence),
`d4_code_review` (detection rules contributed back),
`d2_daily_life_os` (behavior-change anomalies flag through).

`handoffs.yaml` gets new cascade rules once the infrastructure
ADRs land. T7 (after Phase D) wires:

```yaml
- source_domain: d3_local_soc
  source_capability: incident_response
  target_domain: d8_compliance
  target_capability: compliance_scan   # already in handoffs.yaml
  # (no change — pre-existing)

- source_domain: d3_local_soc
  source_capability: anomaly_detection
  target_domain: d2_daily_life_os
  target_capability: behavior_change_flag
  reason: "behavior anomalies surface to Daily Life OS"

- source_domain: d3_local_soc
  source_capability: detection_authoring
  target_domain: d4_code_review
  target_capability: review_signoff
  reason: "new detection rules code-reviewed before merge"
```

Note: the d3→d8 cascade exists in handoffs.yaml today (from
ADR-0067 T4); no change needed.

**Decision 5 — Posture defaults.**

- `forensic_archivist` (guardian, read-only): GREEN. Chain-of-
  custody verification is non-acting; gate is the operator's
  later use of the artifact.
- `telemetry_steward` (observer): GREEN. Continuous-ingest is
  the baseline pattern; the alert path is operator-readable.
- `detection_engineer` (researcher): YELLOW. New detection
  rules need operator review before they go live in the rule
  engine.
- `threat_intel_curator` (researcher): GREEN. IoC catalog
  updates are append-only; vetting happens at consumption
  time, not curation.
- `playbook_pilot` (actuator): YELLOW. Every SOAR action
  operator-gated regardless of posture; YELLOW posture adds
  bedding-in friction.
- `purple_pete` (actuator): YELLOW initially → GREEN once
  the operator has reviewed the first few exercise reports.
  RED automatic if any exercise targets a production system
  outside the sandbox boundary.

**Decision 6 — Use the D4 rollout pattern.**

Each phase follows D4's 10-burst template:
1. T1 ADR doc (this is the rollup; sub-ADRs ship per phase)
2. T2 trait_tree + genres + constitution_templates entries
3. T2b birth scripts per role
4. T3 handoffs.yaml wiring + integration test
5. T4 skill implementations (one per agent's primary capability)
6. T5 umbrella birth script + relevant SBOM/ops decisions
7. T6 operator runbook per phase

Per the D4 lessons (B335 keychain colon, B336 per-role kit,
B341 guardian→actuator correction) — Forest's infrastructure
is now hardened against those classes of bug. Each subsequent
phase should be ~7-9 bursts, not 10.

## Consequences

**Positive:**
- D3 becomes the platform's flagship security domain. Every
  Forest design assumption gets exercised at production-SOC
  rigor.
- The three infrastructure ADRs (0064/0065/0066) unlock
  capabilities that downstream domains will also use —
  telemetry feeds D5 (Smart Home) and D6 (Finance); SOAR
  playbooks feed D2 (Daily Life OS) automation; detection-
  as-code feeds D4 (Code Review) for security-policy linting.
- Purple-team exercises continuously validate the swarm's
  detection coverage — feedback loop the architecture
  desperately needs.

**Negative:**
- D3 is the biggest single domain rollout in the ten-domain
  plan. ~23 bursts (4 phases × ~6 bursts each + 3 infrastructure
  ADRs × ~5 bursts each).
- Resource budget: 6 new agents + the existing 9 = 15 active
  agents in the registry. The M4 mini's headroom is sufficient
  but operator should monitor `/healthz` capacity metrics
  after each phase.
- Infrastructure ADRs are not yet drafted. ADR-0078 commits to
  needing them but doesn't specify them; each gets its own
  arc.

**Open questions:**
- Should `purple_pete` automatically open issues when it finds
  a detection gap, or only report? Decision deferred to ADR-
  0066 (SOAR playbooks — same gating question applies).
- Threat-intel feed allowlist: which feeds does
  `threat_intel_curator` pull from by default? Default to
  none — operator opts into specific feeds (MISP / OTX /
  vendor) during T3 wiring.
- `forensic_archivist`'s artifact storage path: bundled into
  the audit chain's segment archive (ADR-0073), or a separate
  `data/forensics/` tree? Defer to Phase A's T2 burst.

## Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | This ADR (B342). Foundation. | 1 burst |
| T2-T6 (Phase A) | forensic_archivist rollout (no new infra) | ~5-6 bursts |
| ADR-0064 + Phase B | telemetry pipeline + telemetry_steward + threat_intel_curator | ~10-12 bursts |
| ADR-0065 + Phase C | detection-as-code + detection_engineer | ~7-9 bursts |
| ADR-0066 + Phase D | SOAR playbook DSL + playbook_pilot + purple_pete | ~7-9 bursts |

Total: ~30-37 bursts across the full D3 arc. Spans several
sessions.

## See Also

- ADR-0033 Security Swarm (the existing 9-agent blue team)
- ADR-0049 per-event signatures
- ADR-0050 encryption-at-rest
- ADR-0051 per-tool subprocess sandbox
- ADR-0062 supply-chain scanner
- ADR-0064 telemetry pipeline (queued)
- ADR-0065 detection-as-code (queued)
- ADR-0066 SOAR playbooks (queued)
- ADR-0077 D4 Code Review advanced rollout (the template this
  follows)
- `config/domains/d3_local_soc.yaml` (the domain manifest)
