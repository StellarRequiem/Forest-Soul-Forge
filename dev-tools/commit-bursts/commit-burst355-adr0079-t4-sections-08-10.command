#!/bin/bash
# Burst 355 - ADR-0079 T4: diagnostic sections 08-10.
#
# 1. section-08-audit-chain-forensics.command:
#    Three checks against the live chain:
#      a. audit_chain_verify end-to-end (catches the known seq
#         gap at 3728->3729 the live-test surfaced)
#      b. signature coverage spot-check (last 200 entries)
#      c. body_hash present on turn events post-Y7 summarization
#    Reads examples/audit_chain.jsonl directly; FSF_AUDIT_CHAIN_PATH
#    override honored.
#
# 2. section-09-handoff-routing.command:
#    Pure on-disk cross-reference (no daemon needed):
#      a. every (domain, capability) in handoffs.yaml points at
#         an existing domain
#      b. every domain manifest's entry_agents reference roles
#         that exist in trait_engine AND are claimed by some genre
#      c. cascade rules resolve (source/target domains exist,
#         target_capability is in target's capabilities OR target
#         status=planned)
#      d. INFO: domain capabilities without handoff mapping
#         (expected during rollouts; not a FAIL)
#
# 3. section-10-cross-domain-orchestration.command (MVP):
#    Three wiring checks against the live daemon:
#      a. /orchestrator/status returns 200 + singleton id
#      b. decompose_intent.v1 + route_to_domain.v1 registered
#      c. domain count: orchestrator view matches on-disk
#    Real end-to-end dispatch (operator utterance → decompose →
#    route → delegate) deferred — requires a hardcoded decompose
#    fixture that bypasses the LLM provider call for stability.
#    Later tranche adds it.
#
# Expected first-run findings:
#   - Section 08 should FAIL on chain verify due to the known seq
#     gap at 3728→3729 (matches what archive_evidence.v1's
#     verify_chain_integrity step found via live-test). Signature
#     + body_hash checks should PASS.
#   - Section 09 will likely report INFO for unmapped capabilities
#     (D10 roles still upstream, etc.); should PASS the three
#     structural checks.
#   - Section 10 should PASS all three MVP checks if the
#     orchestrator singleton is alive.
#
# Any unexpected FAIL is a real bug worth a focused fix-burst.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-08-audit-chain-forensics.command \
        dev-tools/diagnostic/section-09-handoff-routing.command \
        dev-tools/diagnostic/section-10-cross-domain-orchestration.command \
        dev-tools/commit-bursts/commit-burst355-adr0079-t4-sections-08-10.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): ADR-0079 T4 - sections 08-10 (B355)

Burst 355. Three section drivers for the diagnostic harness:

  08 audit-chain-forensics  three checks against live chain:
                            chain verify end-to-end, signature
                            coverage spot-check on last 200
                            entries, body_hash present on turn
                            events. Reads examples/audit_chain.
                            jsonl by default; FSF_AUDIT_CHAIN_PATH
                            override honored. Will catch the
                            known seq gap at 3728->3729.

  09 handoff-routing        pure on-disk cross-reference:
                            handoffs.yaml maps to known domains;
                            entry_agents reference real claimed
                            roles; cascade rules resolve (both
                            domains exist, target_capability
                            present OR target status=planned);
                            INFO for unmapped capabilities
                            (expected during rollouts).

  10 cross-domain-          MVP wiring checks:
     orchestration          /orchestrator/status returns 200 +
                            singleton id; decompose_intent.v1 +
                            route_to_domain.v1 registered; domain
                            count matches on-disk. Real end-to-end
                            dispatch deferred (needs hardcoded
                            decompose fixture for stability).

Expected first-run:
  - 08 FAIL on chain verify (known seq gap at 3728->3729);
    signature + body_hash should PASS
  - 09 INFO on unmapped capabilities (rollouts incomplete);
    structural checks PASS
  - 10 PASS all three MVP checks if orchestrator singleton alive

Next: B356 - T5 sections 11-13 (memory-retention + encryption-at-
rest + frontend-integration). Then B357 umbrella + runbook
(CLOSES ADR-0079)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 355 complete - sections 08-10 shipped ==="
echo "Next: B356 - T5 sections 11-13."
echo ""
echo "Press any key to close."
read -n 1 || true
