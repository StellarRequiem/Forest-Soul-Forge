#!/bin/bash
# Burst 387 - ADR-0065 D3 Phase C proposal (doc-only).
#
# Drafts the detection-as-code arc that opens D3 Phase C. Doc-only
# commit; implementation tranches T1-T6 land as separate bursts
# after operator green-light, mirroring the ADR-0080 (B374 proposal
# -> T1+T2+T3+T5 implementation) cadence.
#
# What ADR-0065 specifies:
#
#   Sigma-subset rule format (logsource + detection.selection +
#   detection.condition + level + tags). Industry-standard format;
#   subset keeps engine scope tight.
#
#   MITRE ATT&CK tagging mandatory on every rule. Operators who
#   don't know the technique use attack.unknown explicitly so the
#   steward's coverage-gap signal isn't lost to untagged rules.
#
#   detection_engineer role (researcher genre — network reach for
#   ATT&CK ref + LLM for rule synthesis; no action surface).
#   Signature skill propose_detection.v1: recall prior detections
#   + chain verify + LLM synthesize -> memory_write the proposed
#   rule for operator review. Engineer NEVER writes
#   config/detection_rules/ directly; operator commits.
#
#   DetectionEngine runs synchronously in-process after each
#   telemetry_batch_ingested (B377 anchor). For each active rule,
#   evaluates against the batch's events; matches emit
#   detection_fired audit chain events with {rule_id, batch_id,
#   matched_event_ids, technique, severity, rule_version, evidence}.
#   rule_version is sha256(rule body) so chain history pins the
#   exact rule that fired.
#
#   Engine refuses to run if the rule set fails to parse. Operator-
#   facing: single broken rule blocks the engine — silent skip
#   would hide drift. section-01 harness extends to validate rules
#   at static-config check; section-08 gains detection_fired
#   awareness.
#
# Tranche plan (5-6 bursts to close):
#   T1 ADR doc + Sigma-subset parser + DetectionRule dataclass + tests
#   T2 DetectionEngine + scan() integration in AdapterIngestor
#   T3 detection_engineer role + signature skill + birth + wiring
#   T4 section-01 + section-08 harness extensions
#   T5 operator runbook + starter rule library (5-10 rules)
#   T6 CLOSE - tests + north-star update + status: Accepted
#
# Why doc-only first (matches ADR-0079 + ADR-0080 pattern):
#   Six-tranche arc has enough surface (parser + engine + role +
#   harness + runbook) that design needs operator green-light
#   before code lands.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT writing this ADR: D3 Phase C has no paper
#     trail; future sessions would re-derive the rule-format choice
#     (Sigma vs custom DSL vs full SIEM lift) + the runtime hook
#     point + the role/genre placement.
#   Prove non-load-bearing: doc only. No code change.
#   Prove alternative is better: chat-only loses design fidelity;
#     ad-hoc impl skips the architectural decisions the ADR records.
#
# Verification after this commit lands:
#   1. Read docs/decisions/ADR-0065-detection-as-code.md.
#   2. Operator green-lights or amends.
#   3. T1 starts as a separate burst once green-lit.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0065-detection-as-code.md \
        dev-tools/commit-bursts/commit-burst387-adr0065-detection-as-code-proposal.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0065 detection-as-code proposal (B387)

Burst 387. D3 Phase C ADR. Doc-only; T1-T6 implementation
tranches follow after operator green-light.

What ADR-0065 specifies:
  Sigma-subset rule format (industry-standard SIEM lingua franca).
  MITRE ATT&CK tagging mandatory; engineer's coverage-gap signal.
  detection_engineer role (researcher genre — LLM synthesis +
    ATT&CK ref reads; no action surface).
  DetectionEngine runs in-process after each
    telemetry_batch_ingested anchor (B377 hook); matches emit
    detection_fired audit chain events with rule_version=
    sha256(rule body) so chain history pins the exact rule.
  Engine refuses to run if any rule fails to parse — silent skip
    would hide drift; harness section-01 extends to validate.

Tranche plan (5-6 bursts):
  T1 parser + DetectionRule dataclass + tests
  T2 engine + scan() integration in AdapterIngestor
  T3 detection_engineer role + signature skill + birth
  T4 section-01 + section-08 harness extensions
  T5 runbook + starter rule library
  T6 CLOSE

Why doc-only first (matches ADR-0079 + ADR-0080 cadence):
  Six-tranche arc has enough surface that design needs operator
  green-light before code lands.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: D3 Phase C has no paper trail; future sessions
    re-derive Sigma-vs-DSL choice + hook point + role placement.
  Prove non-load-bearing: doc only.
  Prove alternative is better: chat-only loses fidelity; ad-hoc
    impl skips architectural decisions.

After this lands: operator reviews + green-lights T1."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 387 complete - ADR-0065 proposed ==="
echo "=========================================================="
echo "Review: docs/decisions/ADR-0065-detection-as-code.md"
echo "Green-light opens T1 (Sigma-subset parser burst)."
echo ""
echo "Press any key to close."
read -n 1 || true
