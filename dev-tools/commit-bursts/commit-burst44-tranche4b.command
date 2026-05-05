#!/usr/bin/env bash
# Burst 44 Tranche 4b: ADR-0021-am T3 — InitiativeFloorStep dispatcher.
#
# Adds InitiativeFloorStep to the R3 governance pipeline (between
# GenreFloorStep and CallCounterStep). v0.2 enforcement is opt-in
# per tool: tools that declare `required_initiative_level` get
# gated against the agent's initiative_level (loaded from the
# constitution YAML); tools that don't declare pass through.
#
# This earns the structural T3 deliverable without breaking the
# existing kits (Companion's memory_write at filesystem-tier doesn't
# trip; the catalog can opt tools in incrementally as audited).
#
# Test delta: 1546 -> 1561 passing (+15, 0 regressions).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 44 Tranche 4b — InitiativeFloorStep dispatcher ==="
echo
clean_locks
git add src/forest_soul_forge/tools/dispatcher.py \
        src/forest_soul_forge/tools/governance_pipeline.py \
        tests/unit/test_governance_pipeline.py \
        tests/unit/test_tool_dispatcher.py \
        commit-burst44-tranche4b.command
clean_locks
git status --short
echo
clean_locks
git commit -m "InitiativeFloorStep dispatcher (ADR-0021-am T3)

ADR-0021-amendment §5 — runtime check on the L0–L5 initiative
ladder, orthogonal to GenreFloorStep's side-effects ceiling.
Where GenreFloorStep answers 'how destructive can this agent's
actions be?', InitiativeFloorStep answers 'how autonomous is the
agent allowed to be in deciding to act?'

v0.2 enforcement is opt-in per tool. A tool that declares a
class-level ``required_initiative_level`` attribute (e.g. 'L4')
is gated against the agent's initiative_level. Tools without the
declaration pass through unaffected. This earns the T3 deliverable
without breaking existing kits — the side-effects axis on existing
tools (memory_write at filesystem-tier; web_fetch at network-tier;
etc.) does NOT auto-derive an initiative requirement.

Why opt-in: a naive side-effects-to-initiative mapping (filesystem
=> L4, external => L5) would refuse Companion's memory_write
(filesystem) since Companion's L1 < L4. Companion's defining
capability is private memory writes (ADR-0021-am §1 L1), so the
naive mapping breaks the genre. Per-tool annotation lets the
catalog reason about each tool's specific autonomy posture as
the audit catches up. v0.3 candidates: web_fetch / web_actuator /
shell_exec / browser_action declarations.

Dispatcher (src/forest_soul_forge/tools/dispatcher.py):
- New _load_initiative_level(constitution_path) helper. Reads
  agent.initiative_level from constitution YAML. Returns 'L5'
  (back-compat default — no initiative ceiling) on missing file,
  malformed YAML, missing field, or non-string field. Defensive
  on every read failure path.
- InitiativeFloorStep wired into GovernancePipeline AFTER
  GenreFloorStep so a side-effects-ceiling refusal fires before
  an initiative one (operators see the load-bearing ADR-0021 T5
  violation rather than the secondary initiative error when both
  would refuse).

Pipeline step (src/forest_soul_forge/tools/governance_pipeline.py):
- New _INITIATIVE_ORDER tuple + _initiative_index helper, mirroring
  the existing _SIDE_EFFECTS_TIER_ORDER pattern.
- New InitiativeFloorStep class. evaluate() reads
  dctx.tool.required_initiative_level (defaulting to '' / no-op
  when absent); when set, compares against the loader-read
  agent_level. Refuses with 'initiative_floor_violated' when
  required > agent. Detail message names the tool, the requirement,
  the agent's level, and explains the operator-initiate escape
  hatch (planned, not yet wired in v0.2).

Tests:
- test_governance_pipeline.py +8 cases in TestInitiativeFloorStep:
  tool without required_initiative_level passes; tool without the
  attribute at all (older un-audited tool) passes; required at or
  below agent passes; required equal to agent passes; required
  above agent refuses with named reason + detail; unknown required
  level fails closed (treated as L0); unknown agent level fails
  closed; loader_fn called with the dctx's constitution_path.
- test_tool_dispatcher.py +7 cases in TestLoadInitiativeLevel:
  missing file -> L5; missing field -> L5; explicit L1 -> L1;
  malformed YAML -> L5 (fail-open per ConstraintResolutionStep
  separately handling missing files); non-string field (int) -> L5;
  empty string -> L5; whitespace-trimmed value (' L3 ' -> 'L3').

Test delta: 1546 -> 1561 passing (+15). Zero regressions.

ADR-0021-am T3 status: implemented as opt-in. The structural
landing is complete; per-tool annotation is the catalog audit work
that converts opt-in to enforcement. Per-tool initiative_level
declarations are queued for v0.2 close (memory tools at L1,
web_fetch at L3, web_actuator at L5, etc.) but not in this commit
to keep the surface change tight."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Tranche 4b landed. ADR-0021-am T3 structural landing complete."
echo "Remaining: ADR-0038 T3 (Companion §honesty block)"
echo ""
read -rp "Press Enter to close..."
