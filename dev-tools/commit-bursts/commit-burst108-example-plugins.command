#!/bin/bash
# Burst 108 — ADR-0043 T5: Canonical example plugins + contribution guide
#
# Closes the registry-bootstrap layer of the plugin protocol.
# Three working-shape examples (forest-echo / brave-search /
# filesystem-reference) cover the spectrum of governance postures:
# read_only / network / filesystem. Each is a starting template
# AND a documentation surface for new authors.
#
# Plus the registry submission flow (CONTRIBUTING.md) so the
# community-publishing path is locked + reviewable before the
# forest-plugins repo gets stood up.
#
# What ships:
#   - examples/plugins/README.md (manifest format reference)
#   - examples/plugins/CONTRIBUTING.md (registry submission flow)
#   - examples/plugins/forest-echo/plugin.yaml (minimal template)
#   - examples/plugins/brave-search/plugin.yaml (network read-only)
#   - examples/plugins/filesystem-reference/plugin.yaml (filesystem
#     with per-tool gating)
#   - tests/unit/test_example_plugins.py (25 parametrized smoke
#     tests — every example parses, follows naming convention,
#     uses known type/side_effects)
#
# Verification:
#   - Full unit suite: 2289 passed (was 2264 + 25 = 2289 ✓)
#   - 0 regressions; 1 xfail unchanged (pre-existing v6→v7 sandbox
#     SQLite issue, F-7 in Phase A audit)
#
# This closes ADR-0043 implementation-complete. Outstanding items
# (per-tool requires_human_approval mirroring, allowed_mcp_servers
# auto-grant, frontend Tools-tab plugin awareness, plugin_secret_set
# audit event) are documented in the ADR and tracked as follow-ups.

set -euo pipefail

cd "$(dirname "$0")"

# Sandbox can't always remove .git/index.lock; if one exists, abort
# and tell the operator how to clear it (per CLAUDE.md sandbox-vs-host
# gotchas).
if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

# Stray test artifact (out.txt) — collateral from a scenario test
# run. Not part of T5; drop it.
if [ -f out.txt ]; then
  rm -f out.txt
fi

# Stage T5 deliverables only. The audit_chain.jsonl drift in the
# working tree is unrelated test-run collateral and will be
# committed separately.
git add examples/plugins/README.md
git add examples/plugins/CONTRIBUTING.md
git add examples/plugins/forest-echo/plugin.yaml
git add examples/plugins/brave-search/plugin.yaml
git add examples/plugins/filesystem-reference/plugin.yaml
git add tests/unit/test_example_plugins.py

# Confirm nothing else snuck in.
echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(plugins): canonical example plugins + contribution guide (ADR-0043 T5)

Registry-bootstrap layer for the plugin protocol. Three example
manifests covering the governance posture spectrum:

- forest-echo: minimal authoring template (read_only, 1 capability,
  0 secrets). The starting point in CONTRIBUTING's submission flow.
- brave-search: third-party API with auth (network read-only,
  BRAVE_API_KEY required_secret, no per-call gating).
- filesystem-reference: wraps modelcontextprotocol/servers#filesystem
  (filesystem side_effects, per-tool requires_human_approval map
  with read ungated and write/move/delete gated).

Plus:
- examples/plugins/README.md — manifest format reference covering
  every field, sha256 verification, capability namespacing, and
  the side_effects classification ladder.
- examples/plugins/CONTRIBUTING.md — registry submission flow,
  Ed25519 signature scheme (sigstore deferred), naming rules,
  unverified-plugin posture, quality bar.
- tests/unit/test_example_plugins.py — 25 parametrized smoke tests:
  every example's manifest parses cleanly, name matches dir,
  type/side_effects are valid enum members, capabilities follow
  the mcp.<plugin>.<tool> namespace, sha256 has the right shape,
  README + CONTRIBUTING are present.

Verification:
- Full unit suite: 2289 passed, 3 skipped, 1 xfailed (32.38s).
  Was 2264 before T5 — net +25 matches the parametrized test
  count exactly, no spillover into other modules.

This closes ADR-0043 T5. Remaining ADR-0043 follow-ups
(per-tool approval mirroring, allowed_mcp_servers auto-grant,
frontend Tools-tab plugin awareness, plugin_secret_set audit
event) are documented in the ADR and deferred to next bursts."

echo "--- commit landed ---"
git log --oneline -1

# Audit chain test-run collateral — separate commit so the T5
# burst stays focused. This is purely test-execution drift; no
# new architectural intent.
if ! git diff --quiet examples/audit_chain.jsonl; then
  echo ""
  echo "--- staging audit chain test-run drift ---"
  git add examples/audit_chain.jsonl
  git commit -m "chore(audit): refresh examples/audit_chain.jsonl from test runs

Append-only drift from SW-track coding-tools tests + plugin
runtime exercise (timestamps 2026-05-03T06:48:05Z forward).
Pure test-execution collateral; no architectural changes.

Per CLAUDE.md, examples/audit_chain.jsonl IS the live chain
(daemon/config.py default). Test runs that exercise
birth/dispatch will append. Periodic commits keep the working
tree clean."
  echo "--- audit-chain commit landed ---"
  git log --oneline -1
fi

echo ""
echo "=== Burst 108 commit complete ==="
echo "Press any key to close this window."
read -n 1
