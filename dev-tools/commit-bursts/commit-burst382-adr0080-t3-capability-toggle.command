#!/bin/bash
# Burst 382 - ADR-0080 T3: capability-toggle endpoint + audit
# event + frontend toggle button.
#
# Files in this commit:
#
#   src/forest_soul_forge/daemon/routers/capability_tree.py (MOD)
#     POST /agents/{instance_id}/capability-toggle endpoint.
#     Body: { capability_key: str, enabled: bool }.
#     Validates:
#       - agent exists (404 on UnknownAgentError)
#       - capability_key resolves against the agent's tree
#         (404 if neither constitution-bound nor catalog-bound)
#       - hard_wired tools reject with 409 per ADR-0080 D5 +
#         CLAUDE.md constitution-hash invariant (rebirth is the
#         only path to remove)
#     On operator_toggleable: emits `capability_toggled` audit
#     chain event under write_lock with payload
#       { instance_id, capability_key, kind, binding,
#         requested_enabled, set_by, prior_state }
#     prior_state="unknown" today; T3b adds a small per-agent
#     overrides table + populates prior_state from it. The audit
#     trail IS the durable record until then; operators can
#     replay intent from the chain.
#
#   frontend/js/capability-tree.js (MOD)
#     Operator-toggleable node renders a small "enable"/"disable"
#     button next to its label. Click -> writeCall to
#     /capability-toggle -> refresh tree -> toast with audit seq.
#     Hard-wired nodes have no toggle button (the lock glyph 🔒
#     surfaces the binding visibly).
#
#   tests/unit/test_b380_capability_tree.py (MOD)
#     New _StubChain helper records appended events.
#     5 new tests in TestCapabilityToggle:
#       - 404 on unknown agent
#       - 404 on unknown capability_key
#       - 409 on hard_wired tool + no audit event emitted
#       - 200 on operator_toggleable skill + audit event recorded
#         with correct payload shape
#       - two-toggle sequence records two events with the
#         correct requested_enabled per call
#     Total: 12 tests pass (7 from B380 + 5 new).
#
# Audit-first / enforcement-later rationale:
#   The audit chain is append-only and tamper-evident; it's the
#   source of truth in every other Forest mutation. Shipping the
#   endpoint contract + audit emission first lets the frontend
#   mature against a stable shape before T3b adds the storage
#   layer. Operators see the toggle land immediately (event in
#   the audit tail) - T3b makes runtime listen to it.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T3: operator has no way to record
#     intent to disable a per-agent skill short of rebirth or
#     manually editing the catalog. The capability tree's binding
#     glyphs (🔒/☐) become aspirational.
#   Prove non-load-bearing:
#     - Hard-wired tools STILL can't be toggled (409 not 200).
#     - No constitution mutation. No new tables (T3b adds).
#     - All toggles record in audit chain with prior_state="unknown"
#       so T3b's runtime can backfill state by walking the chain.
#   Prove alternative is strictly better:
#     - Mutating constitution: violates ADR-0080 D5 + identity
#       invariant.
#     - Mutating posture: posture is yellow/green/red, not
#       per-capability granularity; would conflate scopes.
#     - Adding a table now without enforcement: schema bump
#       discipline cost without enforcement benefit. Audit-first
#       lets T2 mature freely.
#
# CLAUDE.md sec2 check:
#   No new dispatcher-owned ToolContext subsystem. The endpoint
#   uses chain via get_audit_chain dep + write_lock via
#   get_write_lock dep - both already populated by the existing
#   B350 ToolContext wiring. Section-06 probe unchanged.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest tests/unit/test_b380_capability_tree.py
#      Expected: 12 passed.
#   2. Force-restart daemon (loads new POST route).
#   3. Open frontend, Capabilities tab, pick any agent with a
#      skill. Skill row shows enable/disable button. Click it.
#      Toast shows "audit seq <N>". /audit/tail?n=1 shows the
#      capability_toggled event.
#   4. Try clicking the toggle on a tool (hard_wired): button
#      doesn't render (binding gate); curl directly with
#      capability_key=<tool> returns 409.
#
# What this UNBLOCKS:
#   T3b - per-agent capability_overrides table + runtime gating.
#   T5 - operator runbook + ADR-0080 CLOSE.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/capability_tree.py \
        frontend/js/capability-tree.js \
        tests/unit/test_b380_capability_tree.py \
        dev-tools/commit-bursts/commit-burst382-adr0080-t3-capability-toggle.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(agents): ADR-0080 T3 capability-toggle endpoint (B382)

Burst 382. POST /agents/{id}/capability-toggle wires the
operator-driven on/off action. Audit-first / enforcement-later:
the chain records intent; runtime gating lands in T3b.

Backend (capability_tree.py):
  POST /agents/{id}/capability-toggle accepts {capability_key,
  enabled}. Validates agent + capability_key against the tree.
  Hard_wired tools reject with 409 (rebirth is the only path
  to remove per ADR-0080 D5 + CLAUDE.md constitution-hash
  immutability). Operator_toggleable skills emit
  capability_toggled audit event under write_lock with
  {instance_id, capability_key, kind, binding,
  requested_enabled, set_by, prior_state='unknown'}.
  prior_state stays 'unknown' until T3b adds the overrides
  table; until then the audit trail IS the durable record.

Frontend (capability-tree.js):
  Operator_toggleable nodes render enable/disable button next
  to the label. Click -> writeCall /capability-toggle -> refresh
  tree -> toast with audit seq. Hard-wired nodes have no button
  (lock glyph surfaces the binding).

Tests (test_b380_capability_tree.py):
  +5 toggle tests on top of the 7 from B380:
    404 unknown agent / 404 unknown capability /
    409 hard_wired + no audit event /
    200 skill toggle + audit event recorded /
    two-toggle sequence records two events.
  Total 12 pass.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: operator can't record intent to disable a per-
    agent skill short of rebirth.
  Prove non-load-bearing: hard-wired still rejected; no
    constitution mutation; no new tables.
  Prove alternative is better: mutating constitution violates
    identity invariant; mutating posture conflates granularity;
    adding storage now without enforcement is schema-bump cost
    without benefit.

After this lands + restart:
  ADR-0080 T3 contract live.
  T3b (overrides table + runtime gating) is the natural next
    burst once the toggle UX stabilizes.
  T5 (runbook + CLOSE) is the closing burst."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 382 complete - capability-toggle endpoint ==="
echo "=========================================================="
echo "Verify:"
echo "  Force-restart daemon."
echo "  Open Capabilities tab; click an enable/disable button."
echo "  /audit/tail?n=1 shows capability_toggled event."
echo ""
echo "Press any key to close."
read -n 1 || true
