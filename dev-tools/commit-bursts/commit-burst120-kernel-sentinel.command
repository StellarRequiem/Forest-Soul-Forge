#!/bin/bash
# Burst 120 — ADR-0044 Phase 1.3: kernel/userspace import sentinel.
#
# dev-tools/check-kernel-userspace.sh — verifies the boundary
# contract from docs/architecture/kernel-userspace-boundary.md
# (Burst 118) on three axes:
#
#   1. Kernel Python imports only stdlib, declared third-party deps,
#      or other forest_soul_forge.* modules. AST-based detection so
#      docstring prose starting with 'from ' or 'import ' doesn't
#      false-positive.
#   2. Kernel Python has no code-level references to userspace paths
#      (apps/desktop, frontend/, dist/, or examples/ outside the
#      audit_chain.jsonl carve-out). String-literal detection that
#      filters docstrings via ast walk.
#   3. Userspace (apps/, frontend/) has no code references to kernel
#      src/ paths. Skips comment-only lines + markdown.
#
# Carve-outs documented in the script:
#   - examples/audit_chain.jsonl is the live default chain path per
#     daemon/config.py — historical legacy, not a userspace example.
#   - Allowlists track stdlib + third-party deps explicitly.
#     Tool-specific deps (playwright, tree_sitter, tree_sitter_languages)
#     are listed inline so it's clear which tool needs each.
#
# First-run discovery surfaced:
#   - 5 legitimate findings: ast (stdlib I missed in v1 of the
#     allowlist), pydantic_settings (daemon config), playwright
#     (browser_action.v1), tree_sitter + tree_sitter_languages
#     (tree_sitter_query.v1). All added to the allowlist.
#   - Zero actual violations after the allowlist update — kernel /
#     userspace boundary is clean as of v0.5.0/Burst 119.
#
# Verification:
#   - ./dev-tools/check-kernel-userspace.sh exits 0
#   - Full unit suite: 2,386 passing (no test changes; sentinel is
#     dev-tools only).

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/check-kernel-userspace.sh

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(dev-tools): kernel/userspace boundary sentinel (ADR-0044 P1.3)

Burst 120. dev-tools/check-kernel-userspace.sh — verifies the
boundary contract from docs/architecture/kernel-userspace-boundary.md
(Burst 118) on three axes:

1. Kernel Python imports only stdlib, declared third-party deps,
   or other forest_soul_forge.* modules. AST-based detection so
   docstring prose starting with 'from ' or 'import ' doesn't
   false-positive.

2. Kernel Python has no code-level references to userspace paths
   (apps/desktop, frontend/, dist/, or examples/ outside the
   audit_chain.jsonl carve-out). String-literal detection that
   filters docstrings via ast walk so doc prose mentioning
   'apps/desktop' for context doesn't false-positive.

3. Userspace (apps/, frontend/) has no code references to kernel
   src/ paths. Skips comment-only lines + markdown so README
   cross-references and JS comments noting where a schema lives
   don't false-positive.

Carve-outs documented in the script:
- examples/audit_chain.jsonl is the live default chain path per
  daemon/config.py — historical legacy, not a userspace example.
- Allowlists track stdlib + third-party deps explicitly.
  Tool-specific deps (playwright, tree_sitter, tree_sitter_languages)
  listed inline so it's obvious which tool needs each.

First-run discovery surfaced 5 legitimate findings:
- 'ast' (stdlib I missed in v1 of the allowlist)
- 'pydantic_settings' (daemon config)
- 'playwright' (browser_action.v1)
- 'tree_sitter' + 'tree_sitter_languages' (tree_sitter_query.v1)
All added to the allowlist; zero actual violations remained.

Kernel/userspace boundary is clean as of v0.5.0/Burst 119. The
sentinel runs in <1s and can be wired into CI / pre-commit hooks
in a future burst — current posture is ad-hoc operator runs +
periodic audits.

Verification:
- ./dev-tools/check-kernel-userspace.sh exits 0
- Full unit suite: 2,386 passing (sentinel is dev-tools only,
  no impact on tests)

This closes ADR-0044 Phase 1 (kernel/userspace boundary lock).
P1.1 doc, P1.2 KERNEL.md, P1.3 sentinel — boundary is now
documented + enforced. Phase 2 (formal kernel API spec under
docs/spec/v1/) is the next major arc opener."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 120 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
