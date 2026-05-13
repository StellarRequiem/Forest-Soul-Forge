#!/bin/bash
# Burst 253 — ADR-0063 T4: reality_anchor role + singleton.
#
# B251 shipped the substrate (ground_truth + verify_claim).
# B252 wired the substrate-layer gate (RealityAnchorStep).
# B253 adds the OPTIONAL agent layer — a singleton-per-forest
# reality_anchor role other agents can delegate to via
# delegate.v1 when they want LLM-grade semantic verification
# beyond what the regex catalog can catch.
#
# Files:
#
# 1. config/trait_tree.yaml
#    NEW: roles.reality_anchor entry. Domain weights tilted
#    toward security/audit/cognitive (each at 2.0) and away
#    from emotional/embodiment (0.3 each). Sibling to
#    verifier_loop in the trait tree.
#
# 2. config/genres.yaml
#    Added reality_anchor to guardian.roles. Sits alongside
#    verifier_loop — both are guardian-genre verifiers, but
#    reality_anchor is pre-action (gates the dispatch) while
#    verifier_loop is post-hoc (scans memory).
#
# 3. config/tool_catalog.yaml
#    NEW: archetypes.reality_anchor with standard_tools:
#      verify_claim.v1, memory_recall.v1, audit_chain_verify.v1,
#      llm_think.v1, delegate.v1.
#    Kit is intentionally read-only — the anchor verifies, it
#    never acts.
#
# 4. config/constitution_templates.yaml
#    NEW: role_base.reality_anchor with 4 policies:
#      forbid_action_taking (the load-bearing safety constraint)
#      forbid_ground_truth_mutation (operator owns truth)
#      require_citation (every verdict cites fact_id)
#      forbid_low_confidence_contradicted (>=0.80 to emit)
#    risk_thresholds tighter than verifier_loop:
#      auto_halt_risk=0.50, escalate_risk=0.20
#    Plus reality_anchor.enabled=true so the anchor IS itself
#    checked against ground truth on its own claims.
#
# 5. src/forest_soul_forge/daemon/routers/writes/birth.py
#    Singleton enforcement: when role==reality_anchor at
#    _perform_create, check registry.list_agents(role=...,
#    status='active'). If any exist, refuse with 409 + detail
#    naming the existing instance_id. Archived anchors don't
#    count as 'active' so archive-then-rebirth works.
#
# 6. tests/unit/test_reality_anchor_role.py (NEW)
#    Coverage:
#      - role present in all 4 config files (trait_tree,
#        genres, tool_catalog, constitution_templates)
#      - 4 policies in the constitution template
#      - reality_anchor.enabled=true on the template
#      - first birth succeeds (201)
#      - second birth refuses (409) + names the existing id
#      - archive-then-rebirth succeeds
#      - other roles not blocked when an anchor exists
#      - anchor's constitution embeds verify_claim in its kit
#
# 7. docs/decisions/ADR-0063-reality-anchor.md
#    Status: T1+T2+T3+T4 shipped. T4 row marked DONE B253
#    with full implementation detail. T5+T6+T7 still queued.
#
# 8. diagnose-import.command (NEW) + fix-cryptography-dep.command (NEW)
#    Triage helpers from the cryptography-dep incident at the
#    start of this burst. The host's pip install -e . silently
#    skipped pulling 'cryptography' even though pyproject lists
#    it; daemon import chain failed with ModuleNotFoundError.
#    The two scripts surface + fix the issue. Kept on disk as
#    durable artifacts.
#
# Per ADR-0063 D6: substrate ALWAYS runs (B252's RealityAnchorStep);
#   agent is OPT-IN deep pass. This burst lands the agent.
# Per ADR-0063 D1: anchor refuses ANY action-taking (forbid_action_taking
#   policy) — its kit is read-only by template, this policy makes
#   it structural even if a future kit edit drifts.
# Per CLAUDE.md §0 Hippocratic gate: singleton is enforced
#   STRUCTURALLY (409 at birth), not by operator convention.
#   Same posture as we wished verifier_loop had had.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/trait_tree.yaml \
        config/genres.yaml \
        config/tool_catalog.yaml \
        config/constitution_templates.yaml \
        src/forest_soul_forge/daemon/routers/writes/birth.py \
        tests/unit/test_reality_anchor_role.py \
        docs/decisions/ADR-0063-reality-anchor.md \
        diagnose-import.command \
        fix-cryptography-dep.command \
        dev-tools/commit-bursts/commit-burst253-adr0063-t4-anchor-role.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(reality-anchor): ADR-0063 T4 reality_anchor role + singleton (B253)

Burst 253. B252 wired the substrate-layer gate (RealityAnchorStep
runs on every dispatch). B253 adds the OPTIONAL agent layer:
a singleton-per-forest reality_anchor role other agents can
delegate to via delegate.v1 when they want LLM-grade semantic
verification beyond the regex catalog.

Role added to trait_tree.yaml (domain weights tilted toward
security/audit/cognitive), genres.yaml (under guardian.roles
alongside verifier_loop), tool_catalog.yaml (read-only kit:
verify_claim.v1 + memory_recall.v1 + audit_chain_verify.v1 +
llm_think.v1 + delegate.v1), and constitution_templates.yaml
(4 policies: forbid_action_taking, forbid_ground_truth_mutation,
require_citation, forbid_low_confidence_contradicted; tighter
risk thresholds than verifier_loop; reality_anchor.enabled=true
so the anchor is itself checked against ground truth).

Singleton-per-forest structurally enforced in
daemon/routers/writes/birth.py::_perform_create — a second
active reality_anchor birth returns 409 with the existing
agent's instance_id in the detail. Archive-then-rebirth path
preserved. Other roles unaffected.

Tests: 11 cases covering catalog presence, singleton refusal,
archive-then-rebirth, no-block-on-other-roles, kit embedded
in constitution.

Also ships diagnose-import.command + fix-cryptography-dep.command
as durable triage helpers from the cryptography-dep incident
that surfaced at the start of this burst (host's pip install
silently skipped pulling cryptography; daemon import chain
failed with ModuleNotFoundError until the fix script ran).

ADR-0063 status: T1+T2+T3+T4 shipped. T5 (conversation
runtime pre-turn hook) + T6 (correction memory) + T7
(SoulUX pane) queued.

Per CLAUDE.md §0 Hippocratic gate: singleton enforced
STRUCTURALLY (409 at birth), not by operator convention."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 253 complete ==="
echo "=== ADR-0063 T4 live. Singleton reality_anchor role available. ==="
echo "Press any key to close."
read -n 1
