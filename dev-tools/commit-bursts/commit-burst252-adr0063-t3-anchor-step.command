#!/bin/bash
# Burst 252 — ADR-0063 T3: RealityAnchorStep in governance pipeline.
#
# B251 shipped the substrate (ground_truth.yaml + verify_claim.v1).
# B252 wires verification into the dispatcher so every gated
# tool call gets cross-checked against operator-asserted truth
# BEFORE it executes. CRITICAL contradictions REFUSE; HIGH/
# MEDIUM/LOW WARN via audit event but proceed.
#
# Files:
#
# 1. src/forest_soul_forge/tools/governance_pipeline.py
#    NEW: RealityAnchorStep + _flatten_args_to_claim. Step
#    reads constitution opt-out, builds a claim from args
#    (top-level + 1-level nested + lists of strings),
#    runs the verifier, refuses on CRITICAL, warns on
#    HIGH/MEDIUM/LOW, no-ops on confirmed/not_in_scope/
#    unknown. Verifier exceptions + catalog load failures
#    degrade to GO so a broken Reality Anchor never blocks
#    legitimate work.
#
# 2. src/forest_soul_forge/tools/dispatcher.py
#    Wires RealityAnchorStep into _pipeline immediately
#    after HardwareQuarantineStep. Adds two helper closures:
#      _reality_anchor_verify(claim, agent_const) → verdict dict
#      _reality_anchor_opt_out(constitution_path) → bool
#    Both inlined here rather than in governance_pipeline.py
#    to keep the pipeline module free of ground-truth /
#    constitution-yaml coupling.
#
# 3. src/forest_soul_forge/core/audit_chain.py
#    KNOWN_EVENT_TYPES += reality_anchor_refused +
#    reality_anchor_flagged so AuditChain.verify doesn't
#    log a forward-compat warning on every gate emission.
#
# 4. tests/unit/test_reality_anchor_step.py (NEW)
#    20+ tests:
#      - event types registered
#      - clean claim → GO, no event
#      - CRITICAL → REFUSE + reality_anchor_refused
#      - HIGH → GO + reality_anchor_flagged
#      - not_in_scope → GO, no event
#      - empty args → GO, no event
#      - opt-out via constitution → no-op
#      - missing constitution → defaults to opt-in (gate fires)
#      - malformed constitution → defaults to opt-in
#      - verifier exception → GO + flag(verifier_raised)
#      - _flatten_args_to_claim: top-level strings, nested
#        dicts, lists of strings, non-strings skipped,
#        depth cap, edge cases
#
# 5. docs/decisions/ADR-0063-reality-anchor.md
#    Status: T1+T2+T3 shipped. T3 row marked DONE B252 with
#    the full implementation detail. Notes the deviation
#    from original spec (RealityAnchorStep runs on ALL
#    tools, not skip-read-only; that guidance applies to the
#    T5 conversation hook instead).
#
# End-to-end sandbox smoke (6 scenarios via standalone driver):
#   1. clean claim → verdict=GO ✓
#   2. critical → verdict=REFUSE, reason=reality_anchor_contradiction ✓
#   3. HIGH (MIT license) → verdict=GO ✓
#   4. not_in_scope → verdict=GO ✓
#   5. empty args → verdict=GO ✓
#   6. opted-out + critical → verdict=GO ✓
#   Audit chain: 1 refused + 1 flagged ✓
#
# Per ADR-0063 D1: refuse CRITICAL, warn HIGH/MEDIUM/LOW.
# Per ADR-0063 D2: on by default, per-agent constitutional opt-out.
# Per ADR-0063 D3: D3 (operator-global canonical + agent ADD)
#   inherited from B251 verify_claim.v1; the step doesn't
#   currently thread agent_constitution into the verifier
#   call (todo for T4 when the reality_anchor role lands).
# Per CLAUDE.md §0 Hippocratic gate: refusal limited to
#   CRITICAL — the tier with zero false-positive risk on
#   legitimate code. HIGH stays warn-only by design.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/governance_pipeline.py \
        src/forest_soul_forge/tools/dispatcher.py \
        src/forest_soul_forge/core/audit_chain.py \
        tests/unit/test_reality_anchor_step.py \
        docs/decisions/ADR-0063-reality-anchor.md \
        dev-tools/commit-bursts/commit-burst252-adr0063-t3-anchor-step.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(reality-anchor): ADR-0063 T3 RealityAnchorStep in pipeline (B252)

Burst 252. B251 shipped the verifier substrate; B252 wires it
into the dispatcher so every gated tool call gets cross-
checked against operator ground truth BEFORE it runs.

New RealityAnchorStep in governance_pipeline.py runs between
HardwareQuarantineStep and TaskUsageCapStep. Args are
flattened to a 'claim' via _flatten_args_to_claim (top-level
strings + 1-level nested dicts + lists of strings) and run
through _reality_anchor_verify (substrate-cost inline of
verify_claim.v1 semantics). Per ADR-0063 D1:
  - CRITICAL contradiction → REFUSE + reality_anchor_refused
  - HIGH/MEDIUM/LOW       → WARN (GO) + reality_anchor_flagged
  - everything else       → GO silently

Per ADR-0063 D2 the gate is ON by default with per-agent
constitutional opt-out via 'reality_anchor: {enabled: false}'.

Verifier exceptions + catalog load failures degrade to GO
(reality_anchor_flagged with reason verifier_raised) so a
broken Reality Anchor never blocks legitimate work. The
gate adds value but is NOT load-bearing.

KNOWN_EVENT_TYPES updated with reality_anchor_refused +
reality_anchor_flagged. 20+ unit tests cover every branch.

Sandbox smoke: 6 scenarios end-to-end (clean/critical/
high/not-in-scope/empty/opted-out) all hit expected verdicts;
chain emits 1 refused + 1 flagged event per the matrix.

ADR-0063 status: T1+T2+T3 shipped. T4 (reality_anchor role)
+ T5 (conversation runtime hook) + T6 (correction memory) +
T7 (SoulUX pane) queued."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 252 complete ==="
echo "=== ADR-0063 T3 live. Reality Anchor gates the pipeline. ==="
echo "Press any key to close."
read -n 1
