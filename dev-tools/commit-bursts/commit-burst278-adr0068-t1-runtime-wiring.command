#!/bin/bash
# Burst 278 — ADR-0068 T1.1: wire operator_profile_read.v1 into the
# runtime. Closes the integration gap B277 explicitly left queued.
#
# Three pieces:
#
# 1. config/tool_catalog.yaml — register operator_profile_read.v1 as
#    a builtin tool. side_effects=read_only, archetype_tags cover
#    companion / researcher / communicator / guardian / observer /
#    assistant so most domain genres get the tool by default. The
#    catalog entry is what makes the tool dispatch-callable; without
#    it the module was dead code.
#
# 2. src/forest_soul_forge/tools/builtin/__init__.py — import the
#    OperatorProfileReadTool class + register the instance in the
#    builtin registry init pattern. Mirror of how VerifyClaimTool
#    (B251) wired in.
#
# 3. src/forest_soul_forge/core/ground_truth.py — load_ground_truth()
#    now ALSO loads the operator profile + merges profile-derived
#    facts into the returned list. The operator's personal truth
#    (name/email/timezone/work_hours) flows transparently into every
#    Reality Anchor consumer:
#      - dispatcher RealityAnchorStep (per-tool-call gating)
#      - conversation gate (per-turn gating)
#      - /reality-anchor/* router endpoints
#      - verify_claim.v1 tool
#    Single change, broad reach — the same pattern merge_agent_additions
#    uses for per-agent ground-truth extensions.
#
#    Failure mode: profile missing or malformed → silent skip,
#    non-fatal note in the errors list. Operator-global catalog still
#    loads; Reality Anchor degrades cleanly to "no personal facts" rather
#    than crashing the dispatcher.
#
#    Collision discipline: catalog id wins over profile-derived id.
#    Operator's explicit catalog edit is more authoritative than the
#    derived seed. Mirrors the merge_agent_additions discipline (per-
#    agent additions never override operator-global). Collision noted
#    in errors so /reality-anchor/status surfaces it.
#
# Tests (test_ground_truth_operator_merge.py — 4 cases):
#   - happy-path merge: catalog + profile facts both present
#   - missing profile: soft failure, error logged, catalog still loads
#   - source field: profile-derived facts carry source='operator_profile'
#   - id collision: catalog wins, conflict in errors
#
# What this completes:
#   B277 shipped operator_profile_read.v1 module + schema + CLI +
#   ground-truth seed generation. B278 wires it into the running
#   daemon so:
#     (a) agents can dispatch operator_profile_read.v1 (catalog reg)
#     (b) personal facts join the Reality Anchor catalog at every
#         load_ground_truth() call (merge logic)
#
# What's NOT in this burst:
#   - Daemon lifespan boot-time profile load + cache. Not needed
#     for correctness — load_ground_truth() is already called per-
#     dispatch and reads the profile each time. A cache optimization
#     can ship later if profile-reads-per-dispatch shows up in
#     telemetry as hot. The substrate is correct without it.
#   - Frontend operator profile pane. Queued for D2 Daily Life OS
#     domain rollout.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/tool_catalog.yaml \
        src/forest_soul_forge/tools/builtin/__init__.py \
        src/forest_soul_forge/core/ground_truth.py \
        tests/unit/test_ground_truth_operator_merge.py \
        dev-tools/commit-bursts/commit-burst278-adr0068-t1-runtime-wiring.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(operator): ADR-0068 T1.1 — runtime wiring for operator_profile (B278)

Burst 278. Closes the integration gap B277 explicitly left queued.

Three pieces that turn the operator profile substrate from a module
into a live-running runtime capability:

  1. config/tool_catalog.yaml: register operator_profile_read.v1
     with side_effects=read_only and archetype_tags covering
     companion / researcher / communicator / guardian / observer /
     assistant. Without the catalog entry the B277 module was dead
     code; the catalog is what makes a tool dispatch-callable.

  2. tools/builtin/__init__.py: import + register
     OperatorProfileReadTool in the builtin registry init. Mirrors
     VerifyClaimTool's wiring pattern from B251.

  3. core/ground_truth.py: load_ground_truth() now ALSO loads the
     operator profile + merges profile-derived facts into the
     returned list. Single change, broad reach — the same pattern
     merge_agent_additions uses for per-agent ground-truth
     extensions. Every Reality Anchor consumer (dispatcher
     RealityAnchorStep, conversation gate, /reality-anchor router,
     verify_claim.v1) now sees the operator's personal facts
     (name/email/timezone/work_hours) transparently.

Failure modes (all soft):
  - profile missing → non-fatal error logged, catalog still loads
  - profile malformed → same; OperatorProfileError caught explicitly
  - id collision with catalog → catalog wins (mirror of
    merge_agent_additions discipline), conflict noted in errors
  - any other exception → caught + logged as non-fatal error so
    Reality Anchor degrades cleanly rather than crashing dispatch

Source attribution: profile-derived facts carry
source='operator_profile' so audit-chain queries can distinguish
them from the operator-global catalog.

Tests: test_ground_truth_operator_merge.py — 4 cases covering happy-
path merge, soft failure on missing profile, source attribution,
id-collision wins-catalog discipline.

What this completes: B277 + B278 together make operator_profile_read.v1
fully runtime-callable, AND personal facts flow into Reality Anchor
verification on every gated tool call + conversation turn. The
substrate is ready for Phase α downstream consumers (ADR-0067 cross-
domain orchestrator, the ten-domain agent roster) to read."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 278 complete — operator profile fully runtime-wired ==="
echo "Next on Phase α: ADR-0067 cross-domain orchestrator (B279+)."
echo ""
echo "Press any key to close."
read -n 1
