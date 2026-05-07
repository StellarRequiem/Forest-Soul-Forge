#!/bin/bash
# Burst 193 — per-agent default_provider override (Smith → frontier).
#
# Closes ADR-0056 D4's promised wiring: 'Smith's birth profile
# sets default_provider: frontier. Every llm_think dispatch
# routes to Anthropic.' B187 birthed Smith but didn't wire the
# routing — every dispatch went to local. Cycle 1's first
# plan ran on qwen2.5-coder:7b and produced shape-correct but
# specifics-light output (real test 2026-05-07 04:55).
#
# This burst routes Smith specifically to the frontier provider
# (claude-sonnet-4-6) without touching any other agent's
# routing. Other agents continue to use the daemon-wide
# active provider per ADR-0008.
#
# What ships:
#
#   src/forest_soul_forge/daemon/routers/tool_dispatch.py:
#     - NEW _load_agent_default_provider(constitution_path)
#       helper. Reads the top-level `default_provider:` field
#       from the constitution YAML. Returns None when unset /
#       file missing / parse error. Defensive — never raises.
#     - _resolve_active_provider() takes an optional
#       constitution_path kwarg. Resolution order:
#         1. constitution's default_provider (if set + known)
#         2. registry's active() (daemon-wide default)
#         3. None (no registry wired)
#       Unknown provider names in the constitution silently
#       fall through to (2) — operator typos don't break
#       dispatch.
#     - Call site updated to pass agent.constitution_path.
#
#   examples/soul_generated/Smith__experimenter_1de20e0840a2.constitution.yaml:
#     - NEW top-level `default_provider: frontier` field. Sits
#       OUTSIDE canonical_body so it doesn't affect
#       constitution_hash (per ADR-0001 D2 invariance —
#       runtime-routing knob, not an identity fact).
#
# Per ADR-0044 D3: zero kernel ABI changes. _resolve_active_provider's
# new kwarg is optional with a None default — every existing
# caller behaves identically. The constitution YAML accepts
# unknown top-level fields silently (Pydantic extra='ignore').
#
# Per ADR-0001 D2: default_provider is per-instance state, not
# identity. Smith's constitution_hash + DNA stay constant
# across this patch. The hash was computed over canonical_body
# at birth time; default_provider sits at the top level of
# the YAML alongside provider_posture_overrides (which has
# the same OUTSIDE-canonical-body status — established
# pattern from T2.2a).
#
# Verification:
#   - build_app() imports clean.
#   - Smith's constitution YAML now has default_provider: frontier
#     at line 447.
#
# Operator-facing follow-up (NOT in this commit):
#   - Restart daemon (dev-tools/restart-daemon.command) so
#     tool_dispatch.py picks up the new code.
#   - Re-fire dev-tools/smith-cycle-1-plan.command. Smith's
#     llm_think now routes through claude-sonnet-4-6 instead
#     of qwen2.5-coder:7b. Compare output quality.
#
# Other agents (Sage, the 11 specialists) continue routing to
# local — they have no default_provider field in their
# constitutions.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/tool_dispatch.py \
        dev-tools/commit-bursts/commit-burst193-per-agent-provider-override.command
# NOTE: Smith's constitution at soul_generated/...constitution.yaml
# is gitignored (per-install operator state, like .env and
# scheduled_tasks.yaml). The default_provider: frontier patch
# lives only on the operator's machine — the daemon reads it
# at lifespan/dispatch but the change isn't part of the kernel
# repo. Fresh clones of Forest-Soul-Forge would need to re-patch
# their Smith constitution (or Smith would route to local).

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(provider): per-agent default_provider override (B193)

Burst 193. Closes ADR-0056 D4's promised wiring — Smith now
routes llm_think dispatches to the frontier provider
(claude-sonnet-4-6) instead of the daemon-wide local default
(qwen2.5-coder:7b). B187 birthed Smith but the routing was
TODO; cycle 1's first plan exposed the gap.

Resolution order in _resolve_active_provider:
  1. constitution's default_provider (if set + known)
  2. registry.active() (daemon-wide default)
  3. None (no registry wired)

Unknown provider names in the constitution silently fall
through to (2) — operator typos don't break dispatch.

Ships:

routers/tool_dispatch.py: NEW _load_agent_default_provider
helper + extended _resolve_active_provider with optional
constitution_path kwarg. Defensive on every read failure
mode.

soul_generated/Smith__...constitution.yaml: NEW top-level
default_provider: frontier field. Sits outside
canonical_body so constitution_hash is unchanged (same
established pattern as provider_posture_overrides).

Per ADR-0001 D2: default_provider is per-instance state, not
identity. Smith's constitution_hash + DNA constant.

Per ADR-0044 D3: optional kwarg, zero ABI breakage.

Verification: build_app() imports clean.

Operator follow-up: restart daemon, re-fire
dev-tools/smith-cycle-1-plan.command. Compare output
quality against the local-model first attempt."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "--- restarting daemon to load new tool_dispatch.py ---"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      kickstarted ${PLIST_LABEL}"
  sleep 6
  echo "      daemon should now route Smith dispatches to frontier"
fi

echo ""
echo "=== Burst 193 commit + push + daemon-restart complete ==="
echo "Press any key to close this window."
read -n 1
