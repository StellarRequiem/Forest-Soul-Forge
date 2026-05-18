#!/bin/bash
# Burst 380 - ADR-0080 T1: per-agent capability tree backend.
#
# Land the read-only GET endpoint that backs the new
# Agent Capabilities tab (T2 ships the frontend module).
# Composes from constitution + genre + posture + tool registry +
# skill catalog without any new tables.
#
# What lands:
#
#   src/forest_soul_forge/daemon/routers/capability_tree.py (NEW)
#     GET /agents/{instance_id}/capability-tree
#     Response schema (Pydantic):
#       schema_version: 1
#       agent: { instance_id, role, genre, agent_name, posture }
#       tree:
#         tools:    [{ key, side_effects, status, binding,
#                      reason, constraints }]
#         skills:   [{ name, version, status, binding, reason,
#                      requires_tools, missing_tools, description }]
#         mcp_plugins: []   # T1 placeholder; T2/T4 will populate
#       summary: { tools_total, tools_live, tools_broken,
#                  skills_total, skills_live, skills_broken,
#                  mcp_plugins_total }
#
#     Composition (strict precedence per ADR-0080):
#       1. Constitution `tools` list (hard_wired binding).
#       2. Genre ceiling (currently passed through; T3 toggle
#          endpoint will use it for validation).
#       3. Per-agent posture (currently passed through in the
#          response header; T3 toggle endpoint will gate against it).
#       4. Runtime availability:
#            - tool_registry.has(name, version) -> live | broken
#            - skill: subset of requires not in agent's allowed_tools
#              -> broken with missing_tools list; otherwise live.
#
#     404 if instance_id unknown. Broken constitution path
#     degrades gracefully to empty tools list (the agent's
#     metadata is still available; broken kit is the diagnostic).
#
#   src/forest_soul_forge/daemon/app.py (MOD)
#     Adds capability_tree_router import + include_router call
#     alongside the existing 40 routers. Slot is right after
#     passport_router and before reality_anchor_router per the
#     per-agent route grouping.
#
#   tests/unit/test_b380_capability_tree.py (NEW)
#     7 tests via FastAPI TestClient + stubbed registry/tool
#     registry/skill catalog:
#       - 404 path for unknown agent
#       - all-live tools when registered
#       - broken tool when constitution claims it but registry lacks
#       - missing constitution path falls back to empty (not 500)
#       - skill broken when required tool not in agent's allowed_tools
#       - skill live when all required tools present
#       - mcp_plugins placeholder = [] today
#     All 7 pass.
#
# What this UNBLOCKS:
#   ADR-0080 T2 (frontend module) can now build against a stable
#   substrate contract. T3 (toggle endpoint + audit event) lands
#   in its own burst once T2's UX shape converges. T4 (inferred
#   tool->tool edges) is optional and may stay deferred.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T1: operator has no programmatic
#     way to ask 'what can THIS agent actually do RIGHT NOW?'
#     short of reading raw constitution YAML + the tool registry
#     dump and reconciling by hand. The global Tool Registry +
#     Skills tabs don't answer per-agent questions.
#   Prove non-load-bearing:
#     - Read-only endpoint. No registry mutation. No new tables.
#     - Composes from existing read paths (registry.get_agent +
#       tool_registry.has + skill_catalog.skills + constitution
#       YAML on disk).
#     - 404 / empty-constitution paths degrade gracefully; never
#       500.
#   Prove alternative is strictly better:
#     - Extending /agents/{id} response with kit data: conflates
#       agent metadata with kit composition; the kit is fluent
#       across registry + catalog + skill + posture surfaces
#       which the agent endpoint doesn't reach.
#     - Letting the frontend compose: forces the frontend to know
#       the precedence rules + posture semantics + runtime
#       liveness checks; thin clients (status badges, mobile)
#       can't all carry that logic.
#
# CLAUDE.md sec2 + sec3 check:
#   No new dispatcher-owned ToolContext subsystem. No new builtin
#   tool with _VERSION. The endpoint is pure read composition;
#   sec2/sec3 don't apply.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest tests/unit/test_b380_capability_tree.py
#      Expected: 7 passed.
#   2. force-restart-daemon - daemon picks up the new router.
#   3. curl GET /agents/{some_active_id}/capability-tree -
#      response shape matches CapabilityTreeOut.
#   4. diagnostic-all section-13 picks up the new endpoint if
#      section-13's probe list extends (T2 will likely add a
#      'Capabilities' tab probe).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/capability_tree.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_b380_capability_tree.py \
        dev-tools/commit-bursts/commit-burst380-adr0080-t1-capability-tree-backend.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(agents): ADR-0080 T1 capability tree endpoint (B380)

Burst 380. Read-only GET /agents/{id}/capability-tree backs the
new Agent Capabilities tab (T2 ships the frontend module).
Composes per-agent reach from constitution + genre + posture +
tool registry + skill catalog without any new tables.

Response shape (Pydantic):
  schema_version: 1
  agent:   { instance_id, role, genre, agent_name, posture }
  tree:
    tools:       [{ key, side_effects, status, binding,
                    reason, constraints }]
    skills:      [{ name, version, status, binding, reason,
                    requires_tools, missing_tools, description }]
    mcp_plugins: []   # T1 placeholder
  summary: { tools_total, tools_live, tools_broken,
             skills_total, skills_live, skills_broken,
             mcp_plugins_total }

Three states per node: live / broken / in_progress.
Two binding modes: hard_wired (constitution) / operator_toggleable
  (skills + future MCP).
Composition order (ADR-0080 strict precedence):
  1. Constitution allowed_tools (immutable, hard_wired).
  2. Genre risk_profile.max_side_effects ceiling.
  3. Per-agent posture (ADR-0036).
  4. Runtime availability (tool_registry.has + skill.requires
     intersection with allowed_tools).

404 on unknown agent. Broken constitution path -> empty tools
list (graceful degrade; agent metadata still surfaces).

Tests (7, all pass):
  404 / all-live / broken-tool / missing-constitution-path /
  skill-broken-when-required-tool-absent / skill-live-when-all-
  present / mcp_plugins-placeholder.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: no per-agent 'what can this agent do RIGHT NOW'
    query today; operator reconciles YAML + registry by hand.
  Prove non-load-bearing: read-only, no new tables, graceful
    degrade on broken constitution paths.
  Prove alternative is better: extending /agents/{id} conflates
    metadata with kit composition; letting frontend compose
    forces thin clients to carry precedence + posture + liveness
    logic.

After this lands:
  ADR-0080 T2 (frontend module) can build against a stable
  contract. T3 (toggle endpoint + audit event) is its own burst."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 380 complete - capability tree backend ==="
echo "=========================================================="
echo "Re-test:"
echo "  PYTHONPATH=src python3 -m pytest tests/unit/test_b380_capability_tree.py"
echo "Then:"
echo "  dev-tools/force-restart-daemon.command"
echo "  curl -H \"X-FSF-Token: \$TOKEN\" \"http://127.0.0.1:7423/agents/<id>/capability-tree\""
echo ""
echo "Press any key to close."
read -n 1 || true
