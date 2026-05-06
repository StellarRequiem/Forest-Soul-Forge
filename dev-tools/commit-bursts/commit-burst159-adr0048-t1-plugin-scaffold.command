#!/bin/bash
# Burst 159 — ADR-0048 T1 — soulux-computer-control plugin scaffold.
#
# First implementation tranche of ADR-0048 (Computer Control Allowance).
# Pairs with ADR-0047 (Persistent Assistant). T1 lands the
# substrate-only commit: directory + manifest + README + server stub,
# zero capabilities. T2 + T3 add the actual tool dispatch.
#
# Per ADR-0048 Decision 1 (userspace-only): zero kernel ABI surface
# changes. Uses the existing plugin protocol (ADR-0043), dispatcher
# governance (ADR-0019), posture (ADR-0045), grants (ADR-0043 #2),
# audit chain (ADR-0005) — no new event types, no new endpoints.
#
# What ships:
#
#   examples/plugins/soulux-computer-control/plugin.yaml — manifest:
#     - schema_version: 1, type: mcp_server, side_effects: external
#       (the plugin-level ceiling; per-tool side_effects added in T2/T3
#       are what the dispatcher actually checks)
#     - capabilities: [] — empty by design; T2 appends screenshot +
#       read_clipboard, T3 appends click + type + run_app + launch_url
#     - requires_human_approval: {} — empty; T2/T3 populate per-tool
#     - entry_point.command: ./server with placeholder sha256
#       (operator MUST recompute after T2 lands a real server binary;
#       loader rejects mismatched checksums per ADR-0043 supply-chain
#       defense)
#     - required_secrets: [] — macOS automation tools don't need auth
#
#   examples/plugins/soulux-computer-control/README.md — full tranche
#     roadmap (T1-T6), per-tool side-effect classification table,
#     companion-genre kit-tier ceiling explanation, install path
#     (after T2 ships), audit reverse-engineering guidance.
#
#   examples/plugins/soulux-computer-control/server — executable stub
#     that exits 1 with a pointer to the README + tranche roadmap.
#     Replaces with real MCP server in T2; sha256 in plugin.yaml MUST
#     be updated when that happens.
#
#   tests/unit/test_example_plugins.py — adds SCAFFOLD_ONLY_PLUGINS
#     allowlist; soulux-computer-control listed there until T2 ships
#     real tools. test_example_capabilities_non_empty skips for plugins
#     on the allowlist with a clear deprecation message ("MUST be
#     removed once tools land"). The other smoke tests (manifest
#     parses, name-matches-dir, type known, side_effects known) all
#     fire as normal.
#
# Why ship a substrate-only commit:
#
#   - Locks the plugin identity (name, version, manifest shape) before
#     T2 starts adding tools. Subsequent tranches APPEND to capabilities
#     rather than swap-and-rename.
#   - Documents the design intent in the manifest comments + README so
#     a future contributor reading the plugin.yaml understands why
#     capabilities is empty.
#   - Lets the existing plugin smoke tests run on the new entry from
#     day one. Catches manifest-shape regressions in T2/T3 before they
#     ship.
#
# Verification:
#   - PyYAML parse OK (yaml.safe_load)
#   - load_manifest() loads cleanly; schema_version, name, version,
#     license all populated
#   - PYTHONPATH=src pytest tests/unit/test_example_plugins.py
#       -> 31 passed, 1 skipped (the scaffold-allowlist skip)
#   - server stub exits 1 with the documented message when invoked
#
# Remaining ADR-0048 tranches:
#   T2 — read tools (computer_screenshot.v1, computer_read_clipboard.v1)
#   T3 — action tools (computer_click.v1, computer_type.v1,
#        computer_run_app.v1, computer_launch_url.v1)
#   T4 — Allowance UI in Chat-tab settings panel (closes ADR-0047 T4
#        full)
#   T5 — Posture clamp logic in PostureGateStep
#   T6 — Documentation + safety guide

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add examples/plugins/soulux-computer-control/plugin.yaml \
        examples/plugins/soulux-computer-control/README.md \
        examples/plugins/soulux-computer-control/server \
        tests/unit/test_example_plugins.py \
        dev-tools/commit-bursts/commit-burst159-adr0048-t1-plugin-scaffold.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugin): ADR-0048 T1 — soulux-computer-control scaffold (B159)

Burst 159. First implementation tranche of ADR-0048. Pairs with
ADR-0047 Persistent Assistant. T1 lands substrate only: directory +
manifest + README + server stub, zero capabilities. T2 + T3 add the
actual tool dispatch.

Per ADR-0048 Decision 1 (userspace-only): zero kernel ABI surface
changes. Uses existing plugin protocol (ADR-0043), dispatcher
governance (ADR-0019), posture (ADR-0045), grants (ADR-0043 #2),
audit chain (ADR-0005).

Ships at examples/plugins/soulux-computer-control/:
- plugin.yaml: schema_version 1, type mcp_server, side_effects
  external (plugin-level ceiling), capabilities [], requires_human_
  approval {}, entry_point with placeholder sha256, required_secrets [].
- README.md: full tranche roadmap T1-T6, per-tool side-effect
  table, companion-genre kit-tier ceiling, install path, audit
  guidance.
- server: executable Python stub that exits 1 with a pointer to
  the README + tranche roadmap. Replaced with real MCP server in
  T2; sha256 must be recomputed then per ADR-0043 supply-chain
  defense.
- tests/unit/test_example_plugins.py: SCAFFOLD_ONLY_PLUGINS
  allowlist; soulux-computer-control listed until T2 lands tools.
  test_example_capabilities_non_empty skips with 'MUST be removed
  once tools land' message. Other smoke tests fire normally.

Verification: load_manifest() loads cleanly; pytest
tests/unit/test_example_plugins.py -> 31 passed, 1 skipped.

Remaining ADR-0048 tranches:
- T2 read tools (screenshot + read_clipboard)
- T3 action tools (click + type + run_app + launch_url)
- T4 Allowance UI (closes ADR-0047 T4 full)
- T5 Posture clamp logic
- T6 Documentation + safety guide"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 159 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
