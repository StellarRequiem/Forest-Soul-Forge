#!/bin/bash
# Burst 164 — ADR-0048 T3 — action tools for soulux-computer-control.
#
# Closes the 'assistant can ACT' half of ADR-0048's six-tool surface.
# T1 scaffolded the plugin (B159); T2 shipped the read tools (B163);
# T5 confirmed the existing posture-gate substrate covers all six
# tools (B160). T3 ships the four action tools that move the
# Persistent Assistant from "see-only" to "drive my Mac" — bound
# by Forest's existing governance discipline.
#
# Critical safety surface — read this if reviewing or extending T3:
#
#   1. All four action tools declare requires_human_approval=true
#      in plugin.yaml. The dispatcher's ApprovalGateStep fires
#      PENDING for every call by default. A per-(agent, plugin)
#      grant (ADR-0043 #1) at the standard tier can downgrade to
#      ungated (operator explicitly trusted this agent with this
#      plugin); higher trust tiers stay gated.
#
#   2. PostureGateStep clamps red posture to REFUSE for non-read-only
#      tools (per ADR-0048 Decision 4 + B160 coverage tests). All
#      four T3 tools have side_effects=external (or network for
#      launch_url) and therefore obey the clamp. Operator flips to
#      red as a global brake — assistant immediately stops acting.
#
#   3. Defense-in-depth at the SERVER level (NOT just the dispatcher):
#      - computer_click rejects non-int coords before invoking osascript
#      - computer_type caps text at 4000 chars + rejects non-string args
#      - computer_run_app rejects '/' or null-byte in app_name (so an
#        operator-granted run_app cannot be tricked into launching an
#        arbitrary executable file path via a malicious assistant
#        prompt — even on a misconfigured constitution)
#      - computer_launch_url enforces an http/https/mailto allowlist
#        — file:// and javascript:// are explicitly refused (common
#        local-file-exfil + XSS attack vectors). 14 tests cover these
#        rejections cross-platform.
#
#   4. macOS-only by design. All four tools surface a clean
#      platform_unsupported error on non-Darwin so a Linux operator
#      gets actionable guidance instead of a confusing osascript-
#      not-found failure.
#
# What ships:
#
#   examples/plugins/soulux-computer-control/server (extended):
#     Adds 4 new tool functions (computer_click, computer_type,
#     computer_run_app, computer_launch_url) + a shared
#     _osascript_run helper. tools/list response now includes all 6
#     v1 tools. TOOLS dispatch table extended. Single-file Python
#     stays stdlib-only (still no mcp SDK dep — keeps sha256 stable).
#
#   examples/plugins/soulux-computer-control/plugin.yaml:
#     - capabilities: appended 4 entries for the action tools
#     - requires_human_approval: appended 4 entries (all true)
#     - entry_point.sha256: recomputed for the larger server
#       (470806b8...). The plugin loader rejects mismatched checksums
#       per ADR-0043 supply-chain-swap defense.
#     - entry_point comment updated to reflect T3 completion
#
#   examples/plugins/soulux-computer-control/README.md:
#     - Status now shows the full v1 surface live
#     - Per-tool side-effect + approval table promoted to top
#     - Tranche table T3 marked DONE B164
#
#   tests/unit/test_soulux_computer_control_server.py:
#     +14 tests under TestActionToolArgValidation +
#     TestActionToolsOnNonMacOS:
#       - computer_click rejects non-integer coords
#       - computer_type rejects non-string text
#       - computer_type rejects text over 4000 chars
#       - computer_run_app rejects '/' in app_name
#       - computer_run_app rejects null byte in app_name
#       - computer_run_app rejects empty/whitespace name
#       - computer_launch_url rejects file:// scheme
#       - computer_launch_url rejects javascript:// scheme
#       - computer_launch_url accepts https:// (no scheme rejection)
#       - computer_launch_url accepts mailto:
#       - 4 tests confirming non-Darwin platform_unsupported error
#         fires for each action tool
#     Existing test_tools_list_returns_t2_tools renamed to
#     test_tools_list_returns_full_v1_surface and asserts all six
#     tool names. The macOS-only success-path tests for screenshot
#     + clipboard from B163 remain (skipped on Linux sandbox).
#
#   docs/decisions/ADR-0048-computer-control-allowance.md:
#     T3 row in tranche table marked DONE B164 with the defense-in-
#     depth note.
#
# Per ADR-0048 Decision 1 (userspace-only): zero kernel ABI surface
# changes. The action tools dispatch through the existing
# governance pipeline + per-(agent, plugin) grants + posture clamps.
# No new event types, no new endpoints, no schema migrations.
#
# Verification:
#   - PYTHONPATH=src pytest tests/unit/test_soulux_computer_control_server.py
#                                tests/unit/test_example_plugins.py
#                                tests/unit/test_posture_gate_step.py
#     -> 81 passed, 2 macOS-only skips
#   - Manual smoke on macOS:
#       echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
#         ./examples/plugins/soulux-computer-control/server
#     returns the six-tool list
#   - Each action tool tested live on operator's Mac when wiring
#     the plugin into a bound assistant agent (separate validation
#     pass after the install — accessibility permission grant
#     required for click + type to succeed)
#
# Remaining ADR-0048 tranches:
#   T4 — Allowance UI implementing the three-preset Decision 3
#        (closes ADR-0047 T4 full)
#   T6 — Documentation + safety guide (`docs/runbooks/`)

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/plugins/soulux-computer-control/server \
        examples/plugins/soulux-computer-control/plugin.yaml \
        examples/plugins/soulux-computer-control/README.md \
        tests/unit/test_soulux_computer_control_server.py \
        docs/decisions/ADR-0048-computer-control-allowance.md \
        dev-tools/commit-bursts/commit-burst164-adr0048-t3-action-tools.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugin): ADR-0048 T3 — action tools (B164)

Burst 164. Closes ADR-0048 T3. Ships the four action tools that
move the Persistent Assistant from 'see-only' to 'drive my Mac':

- computer_click.v1 — osascript System Events click at int (x,y)
- computer_type.v1 — osascript System Events keystroke (4000-char cap)
- computer_run_app.v1 — open -a (rejects path separators + null bytes)
- computer_launch_url.v1 — open <url> (http/https/mailto allowlist;
  file:// + javascript:// refused)

All four declare requires_human_approval=true at the plugin manifest
level. PostureGateStep clamps red to REFUSE for non-read-only
(B160 coverage). Per-(agent, plugin) grants at standard tier can
downgrade to ungated; higher tiers stay gated.

Defense-in-depth at the server level — even on a misconfigured
constitution that grants action tools, malformed args produce
clean JSON-RPC errors before subprocess fires:
- non-int coords on click → bad_args
- non-string or >4000-char text on type → bad_args / text_too_long
- '/' or null byte in run_app's app_name → bad_app_name
- file:// or javascript:// on launch_url → scheme_disallowed

macOS-only; non-Darwin returns platform_unsupported with actionable
message.

14 new tests under TestActionToolArgValidation +
TestActionToolsOnNonMacOS. Existing tools/list test renamed to
assert all six v1 tools. Server sha256 recomputed (470806b8...).
SCAFFOLD_ONLY_PLUGINS allowlist remains empty (T2 cleared it).

Per ADR-0048 Decision 1: zero kernel ABI surface changes. Action
tools dispatch through existing governance pipeline + grants +
posture. No new event types, no new endpoints, no schema migrations.

Verification: pytest tests/unit/test_soulux_computer_control_server.py
tests/unit/test_example_plugins.py tests/unit/test_posture_gate_step.py
-> 81 passed, 2 macOS-only skips.

Remaining ADR-0048 tranches:
- T4 Allowance UI (three-preset; closes ADR-0047 T4 full)
- T6 Documentation + safety guide"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 164 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
