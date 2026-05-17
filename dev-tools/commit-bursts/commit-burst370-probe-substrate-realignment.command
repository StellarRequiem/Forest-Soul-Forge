#!/bin/bash
# Burst 370 - probe-substrate realignment across sections 08 + 10 + 11.
#
# Bundles three probe-design fixes that share a single theme:
# the harness was asserting contracts the substrate never promised.
# Each is a probe-side adjustment (no daemon change) - bringing the
# probe's expectations in line with what the substrate actually
# emits/exposes.
#
# Three bugs in one rationale:
#
#   Section 08 - signature coverage spot-check:
#     Pre-B370: 'every entry has a signature field' → 200/200
#     missing → FAIL on every run.
#     Reality (ADR-0049 T5 / B244): signatures are OPTIONAL,
#     present only on events emitted by an agent with a registered
#     public key. Most chain entries are system-emitted
#     (chain_created, scheduler_lag, scheduled_task_completed) and
#     CANNOT have signatures. Most agent-emitted entries also
#     lack signatures because few agents have a public key
#     registered today.
#     Fix: three-bucket classification (system_emitted /
#     agent_with_key / agent_no_key). FAIL only when an agent that
#     HAS a registered key emits an unsigned entry. INFO when the
#     sample has no keyed-agent entries (no expectation). PASS
#     when at least one keyed-agent entry is correctly signed.
#     Coverage metrics are surfaced in the evidence string so the
#     operator gets a signal-quality readout.
#
#   Section 10 - /orchestrator/status singleton check:
#     Pre-B371: 'response body has instance_id field' → no
#     instance_id ever in the response → FAIL on every run.
#     Reality (ADR-0067): the cross-domain orchestrator is a
#     substrate-level singleton router, not an agent instance.
#     The response shape is {schema_version, registry: {...}};
#     there's no instance_id because there's no instance.
#     Fix: check for schema_version (int) + registry (dict with
#     total_domains) as the singleton-substrate proof. If the
#     orchestrator ever migrates to a per-instance representation,
#     this check tightens to match.
#
#   Section 11 - memory readable per-agent:
#     Pre-B372: GET /agents/{id}/memory?limit=5 → 404 → FAIL.
#     Reality: that exact route doesn't exist. The substrate's
#     per-agent memory surface today is /agents/{id}/memory/consents
#     (memory_consents.py) - the route the frontend Memory tab
#     actually uses.
#     Fix: probe /agents/{id}/memory/consents; accept either
#     entries or count in the response per the router's actual
#     schema. A future /agents/{id}/memory collection (if added)
#     becomes a separate check.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: 3 FAILs daily on intended substrate behavior; the
#     probes were asserting contracts that don't exist.
#   Prove non-load-bearing: pure probe correction, no daemon
#     change. Each check still surfaces genuinely-broken state
#     (keyed agent emits unsigned event → FAIL; orchestrator
#     response missing schema_version → FAIL; memory route 404
#     → still FAIL because the consents route IS supposed to exist).
#   Prove alternative is strictly better: leaving in place means
#     three section FAILs forever on intended substrate state -
#     the daily summary cannot ever reach 0-FAIL.
#
# Verification after this commit lands:
#   1. section-08-audit-chain-forensics.command - signature coverage
#      flips from FAIL to PASS or INFO depending on whether the
#      sample contains keyed-agent entries.
#   2. section-10-cross-domain-orchestration.command - status check
#      flips to PASS with schema_version + registry counts shown.
#   3. section-11-memory-retention.command - consents check flips
#      to PASS for any active agent (or INFO if none alive).
#   4. diagnostic-all.command - 3 FAILs cleared.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-08-audit-chain-forensics.command \
        dev-tools/diagnostic/section-10-cross-domain-orchestration.command \
        dev-tools/diagnostic/section-11-memory-retention.command \
        dev-tools/commit-bursts/commit-burst370-probe-substrate-realignment.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(harness): probe-substrate realignment 08/10/11 (B370)

Burst 370. Bundles three probe-design fixes sharing a single
theme: the harness was asserting contracts the substrate never
promised.

Section 08 signature coverage (was B370):
  Pre-fix: blanket 'every entry signed' FAIL on intended state.
  ADR-0049 T5 made signatures optional - present only on agent-
  emitted events whose agent has a registered public key.
  Three-bucket classification (system_emitted / agent_with_key
  / agent_no_key). FAIL only when keyed agent emits unsigned.
  INFO when sample has no keyed entries. PASS when at least one
  keyed-agent entry is signed.

Section 10 /orchestrator/status (was B371):
  Pre-fix: 'response has instance_id' FAIL - orchestrator is a
  substrate-level singleton router not an agent instance, no
  instance_id by design. Check schema_version + registry shape
  per the route's actual response per ADR-0067.

Section 11 memory readable per-agent (was B372):
  Pre-fix: probe hit non-existent /agents/{id}/memory?limit=5.
  Real route is /agents/{id}/memory/consents - the one the
  frontend Memory tab uses. Accept entries OR count in response.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 3 FAILs daily on intended substrate behavior.
  Prove non-load-bearing: pure probe correction; each still
    surfaces genuine drift (keyed unsigned emit / orchestrator
    schema break / consents route 404 still FAIL).
  Prove alternative is better: leaving in place blocks daily
    summary from ever reaching 0-FAIL.

After this lands: sections 08 + 10 + 11 drop 3 FAILs total
(may become PASS or INFO depending on live agent state)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 370 complete - probe-substrate realignment ==="
echo "=========================================================="
echo "Re-test:"
echo "  dev-tools/diagnostic/section-08-audit-chain-forensics.command"
echo "  dev-tools/diagnostic/section-10-cross-domain-orchestration.command"
echo "  dev-tools/diagnostic/section-11-memory-retention.command"
echo ""
echo "Press any key to close."
read -n 1 || true
