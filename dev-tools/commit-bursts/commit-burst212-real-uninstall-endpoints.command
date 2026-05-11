#!/bin/bash
# Burst 212 — real uninstall endpoints for forged skills and tools.
#
# Pre-B212 the only way to uninstall a forged artifact was:
#   1. rm data/forge/{skills,tools}/installed/<name>.v<version>.yaml
#   2. Restart the daemon (lifespan re-scans the installed dir)
#
# That left the audit chain silent — no record of who removed what
# when — and required a process bounce for the in-memory registry
# to drop the artifact. Operationally OK during dev; not acceptable
# once auditors care.
#
# B212 adds two symmetric endpoints:
#   DELETE /skills/installed/{name}/{version}
#   DELETE /tools/installed/{name}/{version}
#
# Both:
#   - require_writes_enabled + require_api_token (same gates as install)
#   - run under write_lock
#   - 404 if the canonical install path doesn't exist
#   - unlink the yaml
#   - emit forge_skill_uninstalled / forge_tool_uninstalled (NEW
#     audit event types — see KNOWN_EVENT_TYPES update)
#   - best-effort in-process registry/catalog drop so the next
#     dispatch sees the removal without a daemon restart
#
# The skill endpoint reloads the skill catalog after the unlink so
# GET /skills reflects the new state immediately.
#
# The tool endpoint unregisters BEFORE the unlink so a concurrent
# /agents/{id}/tools/call can't race a dispatch against a half-
# removed file — the write_lock and the in-memory map shape
# guarantee clean cutover.
#
# Staged proposals (data/forge/{skills,tools}/staged/<name>.v<n>/)
# are NOT touched by uninstall — they're the propose audit trail,
# and the existing DELETE /skills/staged or DELETE
# /tools/staged/forged is the discard path for those. An operator
# can chain: DELETE /skills/installed first, then DELETE
# /skills/staged if they want both gone.
#
# Audit chain gains two new event types:
#   forge_tool_uninstalled, forge_skill_uninstalled
# KNOWN_EVENT_TYPES grows from 71 -> 73.
#
# 66 forge-router + audit-chain tests pass post-change.
#
# What we deliberately did NOT do:
#   - Cancel currently-dispatching skills that use the uninstalled
#     artifact. Dispatch decisions are made at start-of-run; a
#     mid-flight skill keeps its references. The next dispatch
#     sees the new state.
#   - Roll back installs to a previous version. Versioning is the
#     operator's job — install_v2 + uninstall_v1 is two calls.
#   - Add a frontend uninstall button. UX work is its own burst.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: ABI grows additively — two new endpoints, two
#                  new audit event types. Existing callers see
#                  no behavior change.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/skills_forge.py \
        src/forest_soul_forge/daemon/routers/tools_forge.py \
        src/forest_soul_forge/core/audit_chain.py \
        dev-tools/commit-bursts/commit-burst212-real-uninstall-endpoints.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(daemon): real uninstall endpoints for forged artifacts (B212)

Burst 212. Pre-B212 the only way to uninstall a forged skill or tool
was rm + daemon restart — silent in the audit chain, required a
process bounce. B212 adds two symmetric endpoints with full audit
coverage:

  DELETE /skills/installed/{name}/{version}
  DELETE /tools/installed/{name}/{version}

Both run under write_lock, return 404 if the canonical install path
doesn't exist, unlink the yaml, emit forge_skill_uninstalled or
forge_tool_uninstalled chain events, and best-effort drop the
in-process registry/catalog entry so the next dispatch sees the
removal without daemon restart.

The skill endpoint reloads the skill catalog post-unlink. The tool
endpoint unregisters before the unlink so a concurrent dispatch
can't race against a half-removed file.

Staged proposals stay — they're the propose audit trail, separate
discard path via DELETE /skills/staged or /tools/staged/forged.

KNOWN_EVENT_TYPES grows 71 -> 73 with forge_skill_uninstalled +
forge_tool_uninstalled.

66 forge-router + audit-chain tests pass.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: ABI grows additively — two new endpoints, two new
                 audit event types. Existing callers unchanged."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 212 complete ==="
echo "=== Real uninstall paths live for forged skills + tools. ==="
echo "Press any key to close."
read -n 1
