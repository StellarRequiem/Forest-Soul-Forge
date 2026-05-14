#!/bin/bash
# Burst 290 — ADR-0072 T1: behavior provenance + policy boundary substrate.
#
# Formalizes the four-layer rule hierarchy the 2026-05-14 design
# session locked in:
#   1. hardcoded_handoff  (config/handoffs.yaml; engineer via PR; highest)
#   2. constitutional     (constitution.yaml; operator at birth; immutable)
#   3. preference         (preferences.yaml; operator-edited)
#   4. learned            (learned_rules.yaml; agent auto-edit; RA-gated; lowest)
#
# Strict precedence — higher always overrides lower on conflict.
# Reality Anchor (ADR-0063) gates learned rule activation: rules
# land in pending_activation, RA verifies against operator-asserted
# ground truth, verified rules move to active.
#
# What ships:
#
# 1. docs/decisions/ADR-0072-behavior-provenance.md — full record.
#    Three decisions (precedence table, RA-gated learned rules,
#    behavior_change audit events). Five tranches T1-T5.
#
# 2. src/forest_soul_forge/core/behavior_provenance.py:
#    - Preference + LearnedRule frozen dataclasses
#    - PreferencesConfig + LearnedRulesConfig containers
#    - PRECEDENCE table: hardcoded=1000, constitutional=800,
#      preference=400, learned=100
#    - resolve_precedence(layer_a, layer_b) helper
#    - load_preferences + load_learned_rules (missing file soft;
#      schema mismatch hard; per-entry errors soft)
#    - save_preferences + save_learned_rules (atomic via .tmp +
#      rename)
#    - compute_behavior_change_delta(before, after) → {added,
#      modified, removed} payload for the behavior_change audit
#      event
#
# 3. src/forest_soul_forge/core/audit_chain.py: register three new
#    event types:
#      - behavior_change (any layer mutation; event_data carries
#        layer + source + delta + reason)
#      - learned_rule_activated (RA verified a pending rule)
#      - learned_rule_refused (RA contradicted a pending rule;
#        rule stays in pending_activation with verdict+reason)
#
# Tests (test_behavior_provenance.py — 14 cases):
#   PRECEDENCE:
#     - ordering is hardcoded > constitutional > preference > learned
#     - resolve_precedence picks the higher
#     - unknown layer raises
#   load_preferences:
#     - missing file soft
#     - schema mismatch hard
#     - happy path
#     - bad weight soft (entry dropped)
#     - duplicate id soft (first kept)
#   load_learned_rules:
#     - happy path with pending + active sections
#     - bad status soft (entry dropped)
#     - missing file soft
#   save round-trips:
#     - preferences round-trip
#     - learned rules round-trip
#   compute_behavior_change_delta:
#     - detects added entries
#     - detects modified entries with before/after fields
#     - detects removed entries
#   Audit chain:
#     - all three new event types registered
#
# What's NOT in T1 (queued):
#   T2: fsf operator preference get/set/delete CLI + audit emit
#   T3: learned-rule auto-edit substrate (agents propose rules)
#       + RA-gate cron (ADR-0041 scheduler-driven)
#   T4: orchestrator integration — resolve_route consults
#       preferences AND active learned rules with precedence
#   T5: frontend Provenance pane

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0072-behavior-provenance.md \
        src/forest_soul_forge/core/behavior_provenance.py \
        src/forest_soul_forge/core/audit_chain.py \
        tests/unit/test_behavior_provenance.py \
        dev-tools/commit-bursts/commit-burst290-adr0072-t1-behavior-provenance.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(provenance): ADR-0072 T1 — behavior provenance substrate (B290)

Burst 290. Formalizes the four-layer rule hierarchy the
2026-05-14 design session locked in. Strict precedence:
hardcoded_handoff > constitutional > preference > learned.
Reality Anchor gates learned rule activation.

What ships:

  - ADR-0072 full record. Three decisions: precedence table,
    RA-gated learned rules (rules land pending_activation, RA
    verifies, verified rules move to active), behavior_change
    audit event family. Five tranches T1-T5.

  - core/behavior_provenance.py: Preference + LearnedRule frozen
    dataclasses, PreferencesConfig + LearnedRulesConfig
    containers, PRECEDENCE table (1000/800/400/100),
    resolve_precedence helper, load + save for both
    preferences.yaml and learned_rules.yaml (missing file soft,
    schema mismatch hard, per-entry errors soft, duplicate ids
    soft with first-kept discipline). compute_behavior_change_delta
    diffs two configs into added/modified/removed payload for
    the audit event.

  - core/audit_chain.py: three new KNOWN_EVENT_TYPES —
    behavior_change (any layer mutation; layer/source/delta/reason
    in event_data), learned_rule_activated, learned_rule_refused.

Tests: test_behavior_provenance.py — 14 cases covering
precedence ordering + resolve helper + unknown-layer raise,
load_preferences missing/schema/happy/bad-weight/duplicate-id,
load_learned_rules happy/bad-status/missing, save round-trips
both files, delta detects added/modified/removed, audit
event types registered.

Queued T2-T5: fsf operator preference CLI, learned-rule auto-edit
+ RA-gate cron, orchestrator integration, frontend pane."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 290 complete — ADR-0072 T1 behavior provenance shipped ==="
echo "Next: T2 operator preference CLI OR T4 orchestrator integration."
echo ""
echo "Press any key to close."
read -n 1
