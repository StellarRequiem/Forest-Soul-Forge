#!/bin/bash
# Burst 331 - ADR-0077 D4 Code Review advanced rollout doc.
#
# First domain rollout post-Phase α. Documents the scope, the
# three new roles to birth (test_author / migration_pilot /
# release_gatekeeper), the capability + skill mappings, the
# cascade rule wiring to d8_compliance, and the 6-tranche
# implementation plan.
#
# What ships:
#
# 1. docs/decisions/ADR-0077-d4-code-review-advanced-rollout.md (NEW):
#    Status: Proposed. Decisions:
#      D1 — Birth three new roles into D4 (researcher/guardian
#           genre split per trait emphasis)
#      D2 — Capabilities + handoffs.yaml entries (test_proposal,
#           migration_safety, release_gating)
#      D3 — Cascade rule wiring: d4_code_review.review_signoff →
#           d8_compliance.compliance_scan + release_gating →
#           d1_knowledge_forge.index_artifact
#      D4 — Births require operator review (manual approval
#           queue, not auto). Cascade rules go through PR review.
#      D5 — Test posture: integration test exercises end-to-end
#           cascade before agents are birthed.
#    Tranches: T1 (this) → T2 roles + stub skills → T3 handoffs.
#    yaml + cascade tests → T4 implement skills → T5 SBOM + birth
#    command → T6 operator runbook. Total ~7-9 bursts.
#
# Doc-only burst — no code, no tests. Burst 332 starts T2.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0077-d4-code-review-advanced-rollout.md \
        dev-tools/commit-bursts/commit-burst331-adr0077-d4-rollout-doc.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(d4): ADR-0077 - D4 Code Review advanced rollout (B331)

Burst 331. First domain rollout post-Phase α. Documents the
scope, the three new roles to birth (test_author /
migration_pilot / release_gatekeeper), the capability + skill
mappings, the cascade rule wiring to d8_compliance, and the
6-tranche implementation plan.

Status: Proposed.

Decisions:
  D1 — Three new roles split across researcher (test_author)
       and guardian (migration_pilot, release_gatekeeper) genres
       per trait emphasis. Each role gets its own constitution
       template + skill kit in T2/T4.
  D2 — Capabilities: test_proposal, migration_safety,
       release_gating. Each maps to a new skill: propose_tests.v1,
       safe_migration.v1, release_check.v1.
  D3 — Cascade rules wire d4.review_signoff → d8.compliance_scan
       (every PR triggers compliance pass) + d4.release_gating →
       d1.index_artifact (release notes → knowledge index).
  D4 — Births require operator review (approval queue, not auto).
       Cascade rules go through normal PR review.
  D5 — Integration test exercises end-to-end cascade before any
       agent is birthed.

Tranches: T1 doc (this) → T2 roles + stub skills → T3 handoffs.
yaml + cascade tests → T4 skill implementations → T5 SBOM +
birth command → T6 runbook. ~7-9 bursts.

First step in the ten-domain rollout sequence (D4 → D3 → D8 →
D1 → D2 → D7 → D9 → D10 → D5 → D6)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 331 complete - ADR-0077 D4 rollout doc shipped ==="
echo "First domain rollout authored. T2 (roles + stub skills) is next."
echo ""
echo "Press any key to close."
read -n 1
