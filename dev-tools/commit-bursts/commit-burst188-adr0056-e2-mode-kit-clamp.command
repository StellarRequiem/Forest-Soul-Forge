#!/bin/bash
# Burst 188 — ADR-0056 E2 — ModeKitClampStep + task_caps.mode.
#
# Wires the experimenter's three-mode discipline (explore /
# work / display) into the dispatcher pipeline. With this
# burst, a mode-tagged dispatch against Smith clamps the
# eligible tools per ADR-0056 D2:
#
#   explore — refuses any tool with side_effects != read_only.
#             Discovery + memory writes only. No code_edit,
#             no shell_exec, no computer-control mutations.
#   work    — full kit. Implementation cycles run unrestricted
#             within the existing posture + genre + per-tool
#             constraint stack. The mode tag still propagates
#             into audit metadata so downstream cycle-report
#             reconstruction (E5) can scope to work-mode events.
#   display — tight review-only allowlist. Five tools:
#             memory_recall, memory_tag_outcome, git_diff_read,
#             git_log_read, audit_chain_verify. Smith in
#             display mode is read-only review of its own
#             past cycles.
#
# Per ADR-0056 D2 framing: ONE Smith identity, three modes
# selected per-dispatch. The clamp is dynamic (set at task_caps
# time) rather than constitutional because constitutions are
# immutable per agent (ADR-0001 D2). The pipeline already
# supports per-call refusal, audit chain captures the
# mode_kit_clamp reason for after-the-fact analysis.
#
# What ships:
#
#   src/forest_soul_forge/daemon/schemas/dispatch.py:
#     - NEW Literal type ExperimenterMode = none|explore|work|display.
#     - TaskCaps gains a `mode` field with default 'none' so
#       every pre-E2 caller (or non-experimenter dispatcher
#       call) is unaffected.
#
#   src/forest_soul_forge/tools/governance_pipeline.py:
#     - DispatchContext gains a `mode: str = "none"` field
#       (additive, default no-op).
#     - NEW ModeKitClampStep class (~140 lines including
#       docstring). Six branches: off-experimenter passthrough,
#       none/empty passthrough, explore (read_only-only),
#       work (full passthrough), display (tight allowlist),
#       unknown-mode loud refuse.
#     - DISPLAY_ALLOWED_TOOLS = (memory_recall,
#       memory_tag_outcome, git_diff_read, git_log_read,
#       audit_chain_verify) — keep this list TIGHT; widening
#       weakens display-mode contract.
#
#   src/forest_soul_forge/tools/dispatcher.py:
#     - Imports ModeKitClampStep alongside the other steps.
#     - NEW ToolDispatcher field experimenter_role
#       (default 'experimenter'). Tests override to validate
#       the no-op-for-other-roles semantic.
#     - dispatch() reads task_caps['mode'] (case-insensitive,
#       whitespace-stripped) into dctx.mode. Missing/non-string
#       mode defaults to 'none'.
#     - ModeKitClampStep inserted into the pipeline AFTER
#       PostureGateStep, BEFORE ProceduralShortcutStep. Posture
#       refusals fire first when both would refuse (operator
#       sees the primary safety mechanism, not the secondary
#       kit clamp).
#
#   tests/unit/test_mode_kit_clamp.py (NEW):
#     22 unit tests across six classes:
#       TestRoleGate (2): off-experimenter + custom-experimenter
#         role selection
#       TestNoneMode (2): default + empty-string passthrough
#       TestExploreMode (4): pass read_only, refuse filesystem +
#         external + network
#       TestWorkMode (1): every tool passes
#       TestDisplayMode (2): allowlist passes, unlisted refuses
#       TestUnknownMode (2): garbage refuses + typo refuses
#       TestModeNormalization (2): uppercase + mixed-case-
#         with-whitespace
#       TestResolvedSideEffectsPrecedence (1): constitution
#         override wins over tool default — same precedence
#         used by GenreFloorStep + PostureGateStep.
#
# Per ADR-0044 D3: zero kernel ABI breakage. New TaskCaps.mode
# field is optional (default 'none'); pre-E2 callers proceed
# identically. Pipeline insertion is additive; dispatchers
# without mode tagging see one extra GO step before procedural-
# shortcut step. test_tool_dispatcher.py 53/53 still pass —
# proves backward compat.
#
# Per ADR-0001 D2: no agent identity touched. The clamp is
# applied at dispatch time, not at constitution time. Smith's
# constitution_hash + DNA stay constant across mode changes.
# Verified: build_app() imports clean, ModeKitClampStep is
# stateless, mode field defaults preserve every existing
# behavior.
#
# Verification:
#   PYTHONPATH=src:. pytest tests/unit/test_mode_kit_clamp.py
#                                tests/unit/test_governance_pipeline.py
#                                tests/unit/test_tool_dispatcher.py
#                                tests/unit/test_procedural_shortcut_dispatch.py
#   -> 170 passed
#
# build_app() imports clean.
#
# Smith currently in YELLOW posture; with E2 live, an explore-
# mode dispatch from the scheduler would clamp Smith's eligible
# tools to read_only-only AND then queue for operator approval
# under YELLOW. To run an unattended explore-mode timer, flip
# Smith to GREEN posture (operator gesture; not part of E2).
# Most operators leave YELLOW until cycle 3 or so to build trust.
#
# Next burst: B189 — E3 explore-mode scheduled tasks (adds
# entries to config/scheduled_tasks.yaml that fire Smith with
# task_caps.mode=explore on a configurable cadence).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/schemas/dispatch.py \
        src/forest_soul_forge/tools/governance_pipeline.py \
        src/forest_soul_forge/tools/dispatcher.py \
        tests/unit/test_mode_kit_clamp.py \
        dev-tools/commit-bursts/commit-burst188-adr0056-e2-mode-kit-clamp.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(experimenter): ADR-0056 E2 — ModeKitClampStep + task_caps.mode (B188)

Burst 188. Wires the experimenter's three-mode discipline
(explore / work / display) into the dispatcher pipeline. With
this burst a mode-tagged dispatch against Smith clamps the
eligible tools per ADR-0056 D2.

explore: refuses any tool with side_effects != read_only.
work: full kit, mode tag propagates into audit metadata.
display: 5-tool allowlist (memory_recall, memory_tag_outcome,
git_diff_read, git_log_read, audit_chain_verify).

Per ADR-0056 D2: ONE Smith identity, three modes selected
per-dispatch. The clamp is dynamic (task_caps level) rather
than constitutional because constitutions are immutable per
agent (ADR-0001 D2).

Ships:

dispatch.py: NEW Literal ExperimenterMode + TaskCaps.mode
field (default 'none' — pre-E2 callers unaffected).

governance_pipeline.py: DispatchContext.mode field +
NEW ModeKitClampStep with six branches (off-experimenter,
none, explore, work, display, unknown). DISPLAY_ALLOWED_TOOLS
kept tight per the contract.

dispatcher.py: experimenter_role field (default
'experimenter'), task_caps['mode'] read into dctx.mode,
ModeKitClampStep inserted after PostureGateStep / before
ProceduralShortcutStep so posture refusals fire first.

Tests: 22 unit tests across role gate, none-mode pass-through,
explore (read_only-only), work (full passthrough), display
(tight allowlist), unknown-mode loud refuse, normalization
(case + whitespace), and resolved.side_effects precedence
matching the rest of the pipeline.

Per ADR-0044 D3: zero kernel ABI breakage. TaskCaps.mode is
optional; pipeline insertion is additive; test_tool_dispatcher
53/53 still passes.

Per ADR-0001 D2: no identity touched. Clamp at dispatch time,
not constitution time.

Verification: 170 passed across the touched-modules sweep +
build_app() imports clean.

Next burst: B189 — E3 explore-mode scheduled tasks (adds
entries to scheduled_tasks.yaml that fire Smith with
task_caps.mode=explore on a configurable cadence)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 188 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
