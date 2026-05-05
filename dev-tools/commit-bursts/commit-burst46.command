#!/usr/bin/env bash
# Burst 46: Per-tool initiative_level annotations.
#
# Converts ADR-0021-am T3 from opt-in (no-op for current tools) to
# real enforcement on the heaviest five tools. Existing kits stay
# compatible because each tool's required_initiative_level matches
# the genre-default of the kit owners.
#
# Test delta: 1567 -> 1577 passing (+10, 0 regressions).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 46 — per-tool initiative_level annotations ==="
echo
clean_locks
git add src/forest_soul_forge/tools/builtin/shell_exec.py \
        src/forest_soul_forge/tools/builtin/browser_action.py \
        src/forest_soul_forge/tools/builtin/mcp_call.py \
        src/forest_soul_forge/tools/builtin/code_edit.py \
        src/forest_soul_forge/tools/builtin/web_fetch.py \
        tests/unit/test_governance_pipeline.py \
        commit-burst46.command
clean_locks
git status --short
echo
clean_locks
git commit -m "Per-tool initiative_level annotations (ADR-0021-am T3 enforcement)

Converts ADR-0021-am T3's InitiativeFloorStep from opt-in (no-op
for current tools) to real enforcement on the five heaviest tools.

Annotations (added as required_initiative_level class attribute):

- shell_exec: L5 — destructive shell execution. Companion (L1) /
  Observer (L3) / Investigator (L3) cannot dispatch even if their
  kit happens to include it. Actuator (default L5) reaches.
- browser_action: L5 — driving a real browser UI is destructive
  in the worst case (form submits, payment flows, mutating clicks).
  Only web_actuator (default L5) reaches.
- mcp_call: L5 — MCP servers can perform arbitrary external
  mutations. Per-server side_effects override exists in the
  resolve path; initiative gate is uniform across all calls.
- code_edit: L4 — reversible-with-policy class. SW-track Engineer
  (Actuator genre, default L5) reaches; Researcher / Companion
  do not. ApprovalGateStep handles per-call approval on top.
- web_fetch: L3 — autonomous web reads need L3+. web_observer
  (default L3) and web_researcher (default L3 ceiling L4) reach.
  Companion (L1) cannot autonomously fetch — operator-initiated
  fetch path is a v0.3 escape hatch.

Existing kits stay compatible:
  - shell_exec is in software_engineer's kit (Actuator L5/L5) ✓
  - browser_action / mcp_call are in web_actuator_root's kit
    (web_actuator L5/L5) ✓
  - code_edit is in software_engineer's kit (Actuator L5/L5; L4 ≤ L5) ✓
  - web_fetch is in web_observer_root / web_researcher_root /
    web_actuator_root kits (L3/L4/L5 — all ≥ L3) ✓

The InitiativeFloorStep gate now FIRES for the first time in
production. A Companion (L1) constitution that lists web_fetch
in its tools (operator-supplied via tools_add at birth) would have
the dispatch refused with 'initiative_floor_violated' rather than
silently succeeding. This is the v0.3 'companion-tier real-time
A/V' surface's first concrete protection — it can't accidentally
fetch random URLs.

Tests (test_governance_pipeline.py +10 cases in
TestRealToolAnnotations):
- Each tool's required_initiative_level is pinned (catches
  accidental drop on refactor — the catalog/registry consistency
  test wouldn't catch a removed annotation since it's tool-side
  metadata, not catalog metadata).
- Companion L1 blocks web_fetch (concrete end-to-end refusal
  with the named reason + level details).
- Observer L3 passes web_fetch (equality is GO).
- Engineer L5 passes shell_exec.
- Observer L3 blocks shell_exec (cross-genre uniform gate).
- Researcher L3 blocks code_edit (within-genre gate fires when
  default is below tool requirement).

Test delta: 1567 -> 1577 passing (+10). Zero regressions.

Future per-tool annotations queued for v0.3 audit:
- delegate.v1 (case-by-case; effect depends on what's delegated)
- isolate_process.v1, jit_access.v1, dynamic_policy.v1 (security_high
  apex tools — L4 likely, but the genre's max is L4 default L3 so
  per-call posture matters)
- code_read (read_only — likely no annotation needed, L0 default)"

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 46 landed. Initiative gate now FIRES on 5 tools."
echo ""
read -rp "Press Enter to close..."
