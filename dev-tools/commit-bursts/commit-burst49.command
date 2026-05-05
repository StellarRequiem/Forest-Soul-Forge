#!/usr/bin/env bash
# Burst 49: per-tool initiative_level annotations — round 2.
#
# Builds on Burst 46 (5 tools annotated). Round 2 adds 7 more covering
# the security-swarm apex tools, the delegation primitive, and the
# memory-mutation tools (verify / challenge / disclose).
#
# Test delta: 1577 -> 1589 passing (+12).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 49 — round-2 initiative annotations ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/isolate_process.py \
        src/forest_soul_forge/tools/builtin/jit_access.py \
        src/forest_soul_forge/tools/builtin/dynamic_policy.py \
        src/forest_soul_forge/tools/builtin/delegate.py \
        src/forest_soul_forge/tools/builtin/memory_disclose.py \
        src/forest_soul_forge/tools/builtin/memory_verify.py \
        src/forest_soul_forge/tools/builtin/memory_challenge.py \
        tests/unit/test_governance_pipeline.py \
        commit-burst49.command
clean_locks
git status --short
echo
clean_locks
git commit -m "Per-tool initiative_level annotations — round 2 (Path B Burst 49)

Builds on Burst 46 (5 tools: shell_exec L5, browser_action L5,
mcp_call L5, code_edit L4, web_fetch L3). Round 2 adds seven more
covering security-swarm apex tools, the delegation primitive, and
the memory-mutation surface.

Annotations added:

- isolate_process.v1 L4 — process containment is reversible (resume
  + reattach are operator-driven). security_mid (default L3) cannot
  autonomously isolate; security_high (default L3 ceiling L4) can be
  raised to L4 for deployment. ApprovalGate fires on top per
  ADR-0033 A4.

- jit_access.v1 L4 — JIT credential grants are reversible (revoke
  + expire). Same posture as isolate_process. security_mid + high
  reach by birthing at ceiling L4.

- dynamic_policy.v1 L4 — adding/removing firewall rules is
  reversible. security_high reaches at L4. Per-call
  approval is the second gate (ADR-0033 A4 already requires
  human approval for security_high external ops).

- delegate.v1 L3 — dispatches another agent's skill but doesn't
  itself mutate state. Reactive Companion (L1) and suggestion-class
  Communicator (L2) cannot autonomously delegate. The chained
  downstream tools each enforce their own initiative floor
  independently — delegation is NOT a back-door around per-tool
  requirements.

- memory_disclose.v1 L3 — cross-agent memory disclosure is a
  load-bearing decision (ADR-0027 §4 minimum-disclosure).
  Companion (L1) and Communicator (L2) cannot autonomously disclose.
  ADR-0027 §5 memory_ceiling is the orthogonal axis.

- memory_verify.v1 L3 — promoting an entry to verified ground truth
  is structural commitment. L1/L2 cannot autonomously promote
  inferences even with a verifier_id arg.

- memory_challenge.v1 L3 — scrutiny stamp; same posture as verify.
  Defers ADR-0027-am open question 4 (agent-self-challenge) until
  a concrete use case surfaces.

Tests (test_governance_pipeline.py +12 cases):
- 7 pinning tests (each tool's required_initiative_level is
  asserted; catches accidental drop on refactor).
- 5 behavioral tests:
    - Companion L1 blocks delegate (chained-tool floors don't help
      if delegation itself refuses).
    - Companion L2 blocks memory_disclose (Communicator default L2
      hits the L3 floor; structural backstop on top of ADR-0027 §4).
    - security_mid L3 blocks isolate_process.
    - security_high L4 passes isolate_process (genre ceiling reach).
    - Observer L3 passes delegate (existing delegation chains keep
      working at the genre default).

Test delta: 1577 -> 1589 passing (+12). Zero regressions.

Path B per docs/roadmap/2026-05-01-v0.2-close-plan.md. Burst 49
clears the prep queue; next up: Burst 50 (external-review-readiness
pass) before pivoting to Phase G.1.A programming primitives
(Bursts 53-62)."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 49 landed. 12 of 41 builtin tools now carry initiative annotations."
echo ""
read -rp "Press Enter to close..."
