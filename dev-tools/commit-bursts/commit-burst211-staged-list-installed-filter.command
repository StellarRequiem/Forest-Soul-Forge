#!/bin/bash
# Burst 211 — filter installed artifacts out of staged-list endpoints.
#
# Live smoke today surfaced a UX wrinkle: after a successful install
# the staged dir survives on disk (it's the propose audit trail), so
# GET /skills/staged + GET /tools/staged/forged kept returning the
# just-installed artifact alongside genuinely pending proposals. The
# Approvals tab's 'Forged proposals' panel rendered installed
# artifacts as if they still needed review — confusing the operator
# about what's pending.
#
# Pre-B211 the panel showed:
#   - summarize_audit_chain.v1 (installed)      <- shouldn't be here
#   - smoke_blurb.v1            (old artifact)
#   - count_audit.v1            (old artifact)
#   - ...
#
# Post-B211 it shows ONLY entries that lack a corresponding
# data/forge/skills/installed/<name>.v<version>.yaml (or .../tools/...).
#
# Symmetric change in both endpoints:
#   src/forest_soul_forge/daemon/routers/skills_forge.py — list_staged_skills
#   src/forest_soul_forge/daemon/routers/tools_forge.py  — list_staged_tools
#
# Implementation: after parse_manifest / parse_spec succeeds, check
# if install_root / f"{name}.v{version}.yaml" exists. Skip if yes.
# Added a _resolve_install_root() helper to skills_forge.py mirroring
# the one already in tools_forge.py.
#
# What we deliberately did NOT do:
#   - Add an "installed: true" flag that callers could OPT INTO.
#     Current panels uniformly want "pending only," and the
#     installed-tools/skills lists are surfaced elsewhere (the
#     Tools tab + Skills tab show installed; Approvals shows
#     pending). Cleaner to make the endpoints semantically
#     "pending only" than to push the filter to clients.
#   - Add a DELETE /tools/installed or /skills/installed endpoint.
#     Different scope — a real uninstall path is its own burst with
#     audit-chain event design.
#
# 27 forge-router tests pass post-change.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: ABI change is semantic only (response shape
#                  unchanged, just narrower in content). Frontend
#                  panel that consumes these endpoints already
#                  treats missing entries as 'nothing pending,' so
#                  the narrower list is a strict improvement.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/skills_forge.py \
        src/forest_soul_forge/daemon/routers/tools_forge.py \
        dev-tools/commit-bursts/commit-burst211-staged-list-installed-filter.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(daemon): filter installed out of staged-list endpoints (B211)

Burst 211. After a successful install the staged dir stays on disk
(propose audit trail), but GET /skills/staged + /tools/staged/forged
kept returning installed artifacts alongside pending proposals. The
Approvals 'Forged proposals' panel rendered duplicates: artifacts
shown as if they still needed review when they were already running.

Both list endpoints now skip entries with an existing canonical
install yaml at data/forge/skills/installed/<name>.v<version>.yaml
(or .../tools/...). Added _resolve_install_root() helper to
skills_forge.py to mirror the one already in tools_forge.py.

27 forge-router tests pass.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: response shape unchanged, just semantically narrower
                 (pending-only). Strict UX improvement for the panel
                 consumer."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 211 complete ==="
echo "=== Staged-list endpoints now show pending only. ==="
echo "Press any key to close."
read -n 1
