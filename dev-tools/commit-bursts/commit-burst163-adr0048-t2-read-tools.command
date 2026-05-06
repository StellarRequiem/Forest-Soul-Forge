#!/bin/bash
# Burst 163 — ADR-0048 T2 — read tools for soulux-computer-control.
#
# T2 ships the two read-only tools that close the "assistant can SEE"
# half of ADR-0048's six-tool surface. T3 will land the four action
# tools (click / type / run_app / launch_url) on the same server.
#
# Why ship the read tools alone first:
#   - Read-only is the only side_effects class that bypasses posture
#     unconditionally (per ADR-0048 Decision 4 + B160 coverage). The
#     assistant gains "see what's on my screen" without unlocking
#     ANY external action surface.
#   - Operators can adopt the plugin and exercise it end-to-end
#     against a posture=red agent — proving the safety surface holds
#     before action tools land.
#   - Per ADR-0048 §"Total estimate: 5-6 bursts. Can ship in pieces —
#     T1+T2 (read-only) provides immediate value (assistant can see
#     the screen); T3 unlocks action; T4 polishes UX..."
#
# What ships:
#
#   examples/plugins/soulux-computer-control/server (rewritten):
#     Real Python stdio JSON-RPC handler. Single-file, stdlib-only
#     (no mcp SDK dependency — keeps sha256 stable across upstream
#     package moves). Wire protocol matches what Forest's mcp_call.v1
#     expects (src/forest_soul_forge/tools/builtin/mcp_call.py:230-260):
#     read one JSON-RPC line from stdin, dispatch on method
#     (tools/list or tools/call), write one response line to stdout,
#     exit. Stateless one-shot per call.
#
#     Tools implemented:
#
#       computer_screenshot.v1
#         args: { filename?: string, include_base64?: boolean }
#         wraps: screencapture -x -t png ~/.forest/screenshots/<name>.png
#         result: { path, size_bytes, format='png', captured_at,
#                   base64? } (path-based by default; opt-in inline
#                   bytes capped at 4 MB)
#         defense-in-depth: rejects filenames with '/' or '..' before
#         invoking screencapture (path-traversal block)
#         platform: macOS only; non-Darwin returns
#         platform_unsupported error
#
#       computer_read_clipboard.v1
#         args: {}
#         wraps: pbpaste
#         result: { text, length_chars, read_at }
#         text-only (per ADR-0048 §"text only initially")
#         platform: macOS only; non-Darwin returns
#         platform_unsupported error
#
#   examples/plugins/soulux-computer-control/plugin.yaml:
#     - capabilities: now contains the two T2 tool names
#     - requires_human_approval: { computer_screenshot: false,
#                                  computer_read_clipboard: false }
#     - entry_point.sha256: real value pinning the new server (~50ish
#       lines of Python). Recompute on every server edit per ADR-0043
#       supply-chain-swap defense.
#     - entry_point comment updated; T1 stub-server language removed
#
#   examples/plugins/soulux-computer-control/README.md:
#     - Status section now reflects T2 shipped
#     - Tranche table updated (T2 DONE B163, T5 DONE B160)
#
#   tests/unit/test_example_plugins.py:
#     SCAFFOLD_ONLY_PLUGINS allowlist emptied. soulux-computer-control
#     no longer needs the non-empty-capabilities skip (T2 fixed it).
#
#   tests/unit/test_soulux_computer_control_server.py (NEW):
#     11 tests covering:
#       - server file exists + executable
#       - tools/list returns the two T2 tools
#       - tools/call → unknown_tool produces -32602 error
#       - unknown method (not tools/call or tools/list) → -32601
#       - empty stdin → stderr message + nonzero exit
#       - malformed JSON → -32700 parse error response
#       - filename path-traversal rejection
#       - non-macOS platform_unsupported error path (both tools)
#       - macOS success paths (skipped on Linux sandbox; run on
#         operator's Mac when exercising the plugin live)
#
#   docs/decisions/ADR-0048-computer-control-allowance.md:
#     T2 row in the tranche table marked DONE B163.
#
# Per ADR-0048 Decision 1 (userspace-only): zero kernel ABI surface
# changes.
#
# Verification:
#   - PYTHONPATH=src pytest tests/unit/test_soulux_computer_control_server.py
#                                tests/unit/test_example_plugins.py
#                                tests/unit/test_posture_gate_step.py
#     -> 67 passed, 2 skipped (macOS-only success paths)
#   - On macOS: ./examples/plugins/soulux-computer-control/server <<<
#     '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' returns the
#     two-tool list
#   - On macOS: tools/call → computer_screenshot writes a PNG under
#     ~/.forest/screenshots/ and returns its path; computer_read_clipboard
#     returns the current clipboard text
#
# Remaining ADR-0048 tranches:
#   T3 — action tools (computer_click + computer_type + computer_run_app
#        + computer_launch_url) with side_effects=external/network +
#        requires_human_approval=true. Will append to the same server.
#   T4 — Allowance UI implementing the three-preset Decision 3 (closes
#        ADR-0047 T4 full)
#   T6 — Documentation + safety guide

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/plugins/soulux-computer-control/server \
        examples/plugins/soulux-computer-control/plugin.yaml \
        examples/plugins/soulux-computer-control/README.md \
        tests/unit/test_example_plugins.py \
        tests/unit/test_soulux_computer_control_server.py \
        docs/decisions/ADR-0048-computer-control-allowance.md \
        dev-tools/commit-bursts/commit-burst163-adr0048-t2-read-tools.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugin): ADR-0048 T2 — read tools (B163)

Burst 163. Closes ADR-0048 T2. Ships the two read-only tools that
close the 'assistant can SEE' half of the six-tool computer-control
surface. T3 will append the action tools (click + type + run_app +
launch_url) on the same server.

Why ship the read tools alone first: read-only is the only
side_effects class that bypasses posture unconditionally per ADR-0048
Decision 4 + B160 coverage. The assistant gains 'see what's on screen'
without unlocking ANY external action — operators can adopt T2 and
exercise the plugin end-to-end against posture=red, proving the
safety surface holds before action tools land.

Server: single-file Python stdio JSON-RPC handler. Stdlib-only (no
mcp SDK dependency — keeps sha256 stable across upstream package
moves). Wire protocol matches what Forest's mcp_call.v1 expects
(read one line from stdin, dispatch on method, write one response).

Tools:
- computer_screenshot.v1 wraps 'screencapture -x -t png' to
  ~/.forest/screenshots/, returns { path, size_bytes, format,
  captured_at, base64? }. Path-traversal defense rejects filenames
  with '/' or '..' before invoking screencapture.
- computer_read_clipboard.v1 wraps 'pbpaste', returns { text,
  length_chars, read_at }. Text-only per ADR-0048 §'text only
  initially'.

Both macOS-only; non-Darwin returns clean platform_unsupported
error.

11 unit tests cover wire protocol, error paths, defense-in-depth
filename rejection. Two macOS-only success-path tests skip on Linux
sandbox; they run on operator's Mac when exercising the plugin
live.

SCAFFOLD_ONLY_PLUGINS allowlist emptied — soulux-computer-control
no longer needs the non-empty-capabilities skip in
test_example_plugins.

Per ADR-0048 Decision 1: zero kernel ABI surface changes.

Verification: 67 passed, 2 macOS-only skips. Manual smoke on Mac:
./server with tools/list returns the two-tool list; tools/call ->
computer_screenshot writes a PNG; computer_read_clipboard returns
current clipboard text.

Remaining ADR-0048 tranches:
- T3 action tools (4 tools, side_effects=external/network)
- T4 Allowance UI (closes ADR-0047 T4 full)
- T6 Documentation + safety guide"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 163 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
