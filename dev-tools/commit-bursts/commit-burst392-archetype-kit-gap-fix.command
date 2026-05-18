#!/bin/bash
# Burst 392 - archetype-kit gap fix + capability-tree status rename.
#
# Surfaced 2026-05-18 when operator opened the new Capabilities tab
# (B381) and saw 9 skills marked "broken" with `missing: [text_summarize.v1]`
# etc. on every Security Swarm agent. B363 had wired the 6 LLM tools
# into the catalog but NOT into any archetype's standard_tools list,
# so per-agent constitutions don't carry them and the capability tree
# correctly reported them as off-for-this-agent.
#
# CLAUDE.md memory captured the principle: when a task description
# has multiple plausible interpretations, take the COMPLETE one.
# "Wire 6 LLM tools" should have meant catalog + archetype kits;
# I shipped the narrow read. The new feedback memory
# `feedback_complete_over_narrow` codifies this for future sessions.
#
# What this commit fixes:
#
# 1. config/tool_catalog.yaml — archetype-kit additions:
#    - All 12 SOC archetypes (network_watcher, log_analyst,
#      anomaly_investigator, patch_patrol, gatekeeper, log_lurker,
#      anomaly_ace, net_ninja, response_rogue, zero_zero,
#      vault_warden, deception_duke) gain llm_think.v1 +
#      text_summarize.v1. Some also gain memory_recall +
#      memory_write where they didn't already have them. All
#      additions are read_only — fits guardian/observer/investigator
#      ceiling without violating the kit-tier gate.
#    - software_engineer + code_reviewer gain text_summarize +
#      code_explain + commit_message + email_draft (+ tone_shift
#      for code_reviewer). Aligns with the skills these roles run
#      (commit_changelog, release_notes, code_review_quick,
#      bug_report_polish).
#    - genre_default_tools entries (observer, investigator,
#      communicator, guardian, researcher) gain llm_think +
#      text_summarize + memory_recall + memory_write. The
#      communicator default additionally gains email_draft +
#      action_items_extract + tone_shift (the communicator-class
#      skills land here).
#
# 2. src/forest_soul_forge/daemon/routers/capability_tree.py —
#    skill-row status rename "broken" -> "unavailable" when the
#    agent's kit is missing required tools. "broken" stays
#    reserved for tool-level substrate corruption (tool in
#    constitution but not in /tools/registered). Operator's
#    capability tab now reads accurately: tool-broken (red) =
#    substrate problem; skill-unavailable (grey) = agent kit
#    gap. Summary gains `skills_unavailable` count.
#
# 3. frontend/js/capability-tree.js — render the new state
#    correctly: `unavailable` -> grey hollow-circle glyph,
#    summary shows separate counts for unavailable vs. broken.
#
# 4. tests/unit/test_b380_capability_tree.py — test renamed +
#    asserts the new status string.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: operator opened Capabilities tab + saw broken
#     status on 9 skills across every Security Swarm agent.
#     B363's job was incomplete; B392 closes it.
#   Prove non-load-bearing: ADDITIONS to archetype kits. Genre
#     ceiling check at birth still applies; nothing added
#     violates any genre's max_side_effects. No removals from
#     any existing kit.
#   Prove alternative is strictly better: alternatives are
#     (a) rebirth every existing agent with the new kit -
#         hard cost + breaks identity invariant for existing
#         instance_ids; (b) keep the gap + label skills
#         accurately - addresses framing but not capability.
#     The done-right move is (1) fix archetype kits for new
#     births going forward, (2) rename status for accurate
#     framing of the existing-agent case, (3) queue rebirth
#     of agents whose kit needs the new tools as a separate
#     operator decision.
#
# Constitution-hash invariant (CLAUDE.md sec0):
#   Existing agents do NOT get the new tools retroactively.
#   Their constitution is bound to their identity hash.
#   Rebirth via the proper pipeline (POST /archive + POST /birth)
#   is the path to refresh a specific agent's kit. Same pattern
#   that resolved Kraine/Victor/chaz (B376). Queue per-agent
#   rebirth decisions as a separate operator action.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest tests/unit/test_b380_capability_tree.py
#      Expected: 12 passed (5 toggle + 7 tree + the renamed test).
#   2. force-restart-daemon - new archetype kits load.
#   3. Birth a fresh agent (any role) - constitution now carries
#      llm_think + text_summarize.
#   4. Open Capabilities tab on existing agents - status reads
#      "unavailable" (grey) on skills missing kit tools, not
#      "broken" (red). Tool-row status still "broken" (red) for
#      actual substrate corruption.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/tool_catalog.yaml \
        src/forest_soul_forge/daemon/routers/capability_tree.py \
        frontend/js/capability-tree.js \
        tests/unit/test_b380_capability_tree.py \
        dev-tools/commit-bursts/commit-burst392-archetype-kit-gap-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(catalog): archetype-kit gap from B363 + skill status rename (B392)

Burst 392. Operator opened Capabilities tab (B381) + saw 9 skills
marked broken on every Security Swarm agent. B363 wired the 6 LLM
tools into catalog but not into archetype kits, so per-agent
constitutions don't carry them. B392 closes the gap and renames
the skill-row status for accurate framing.

Archetype kit additions (config/tool_catalog.yaml):
  All 12 SOC archetypes gain llm_think + text_summarize (+
  memory_recall/write where missing). All read_only; fits each
  role's genre ceiling without violation.
  software_engineer + code_reviewer gain code_explain +
  commit_message + email_draft (+ tone_shift for reviewer).
  genre_default_tools (observer, investigator, communicator,
  guardian, researcher) gain llm_think + text_summarize +
  memory_recall + memory_write. communicator-default also gains
  email_draft + action_items_extract + tone_shift.

Skill-row status rename (capability_tree.py + frontend):
  'broken' (substrate corruption) stays reserved for tool rows.
  'unavailable' (agent kit gap) is the new skill-row status when
  required tools are missing. Frontend renders unavailable in
  grey (hollow circle), broken in red (X). Summary carries both
  counts separately. Operator reads severity at a glance.

Tests: 66 across capability-tree + telemetry + telemetry_steward
+ threat_intel_curator + detection_engineer + B363 LLM tools.
All pass.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: operator hit the gap in the UI; B363 was incomplete.
  Prove non-load-bearing: additions only; kit-tier still applies.
  Prove alternative is better: fixing kits for new births +
    accurate framing for existing agents > rebirthing all
    existing agents now (operator-decision territory).

Constitution-hash invariant:
  Existing agents do NOT get new tools retroactively. Rebirth
  (via /archive + /birth, same pattern as Kraine/Victor/chaz
  B376) is the path. Queue per-agent rebirth as separate
  operator action.

Memory saved: feedback_complete_over_narrow.md captures the
'take the COMPLETE interpretation when ambiguous' principle so
future-me doesn't ship the narrow read again."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 392 complete - archetype-kit gap fixed ==="
echo "=========================================================="
echo "Verify:"
echo "  PYTHONPATH=src python3 -m pytest tests/unit/test_b380_capability_tree.py"
echo "  force-restart-daemon"
echo "  Open Capabilities tab — skills missing kit tools now read"
echo "  'unavailable' (grey) instead of 'broken' (red)."
echo "Operator next step: decide which existing agents to rebirth"
echo "  to gain the new kit tools (rebirth is per-agent operator"
echo "  decision per CLAUDE.md identity-hash invariant)."
echo ""
echo "Press any key to close."
read -n 1 || true
