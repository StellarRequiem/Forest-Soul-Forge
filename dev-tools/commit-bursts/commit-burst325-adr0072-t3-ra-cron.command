#!/bin/bash
# Burst 325 - ADR-0072 T3: Reality-Anchor pass over learned rules.
#
# Cron-runnable substrate that walks learned_rules.yaml's
# pending_activation bucket and promotes / refuses / leaves-pending
# each rule based on the Reality-Anchor verdict against the
# operator's ground-truth catalog.
#
# What ships:
#
# 1. src/forest_soul_forge/core/learned_rule_ra_pass.py (NEW):
#    Pure-function policy layer. run_ra_pass(config, verifier) →
#    RAPassResult(new_config, outcomes, started_at, finished_at).
#    Verdict matrix:
#      - confirmed     → status='active' (RA endorses)
#      - not_in_scope  → status='active' (no conflict)
#      - contradicted  → status='refused' with verdict + reason stamped
#      - unknown       → stays pending, no field change (operator review)
#      - unrecognized  → stays pending (defensive)
#      - verifier exc  → stays pending + verifier_error outcome
#    Original LearnedRulesConfig never mutated (frozen dataclasses).
#
# 2. src/forest_soul_forge/daemon/scheduler/task_types/learned_rule_ra_pass.py (NEW):
#    Scheduler task type wrapper. Loads learned_rules.yaml +
#    ground_truth.yaml, builds a verifier closure that mirrors
#    verify_claim.v1's _evaluate_fact pattern matching (skips the
#    tool-runtime envelope so the scheduler runs lean), invokes
#    run_ra_pass, saves new YAML if anything changed, emits per-
#    rule audit events (learned_rule_activated /
#    learned_rule_refused) when audit_chain is wired. ok=False
#    only on hard failure (yaml unreadable, catalog load crash).
#
# 3. src/forest_soul_forge/daemon/scheduler/task_types/__init__.py:
#    Exports the runner.
#
# 4. src/forest_soul_forge/daemon/app.py:
#    Registers task_type='learned_rule_ra_pass' on the scheduler
#    at lifespan. Operators add a scheduled_tasks.yaml entry like:
#      - id: learned_rules_ra_pass_nightly
#        schedule: every 24h
#        type: learned_rule_ra_pass
#        config: {}
#
# Tests (test_learned_rule_ra_pass.py - 16 cases):
#   run_ra_pass policy matrix (10):
#     confirmed→promoted, not_in_scope→promoted, contradicted→
#     refused (in pending bucket), unknown→stays pending,
#     unrecognized verdict→stays pending, verifier exception
#     captured, empty pending zero counts, existing active
#     preserved, mixed three-verdict batch, original not mutated,
#     ISO timestamps populated
#   scheduler runner (5):
#     load failure → ok=False, empty pending → clean, no-op pass
#     doesn't touch disk, audit emits for promotion+refusal with
#     correct payload shape, missing audit_chain doesn't crash
#
# Sandbox-verified 16/16 pass.
#
# === ADR-0072 progress: T3 RA cron shipped ===
# Tranche scorecard: T1 substrate (B290) + T2 fsf provenance CLI
# (B303) + T3 RA cron (B325, this) all shipped. T4 orchestrator
# integration + T5 frontend pane remain.
#
# Phase α scorecard: 7/10 closed; ADR-0072 now at 3/5.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/learned_rule_ra_pass.py \
        src/forest_soul_forge/daemon/scheduler/task_types/learned_rule_ra_pass.py \
        src/forest_soul_forge/daemon/scheduler/task_types/__init__.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_learned_rule_ra_pass.py \
        dev-tools/commit-bursts/commit-burst325-adr0072-t3-ra-cron.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(provenance): ADR-0072 T3 - Reality-Anchor cron over pending learned rules (B325)

Burst 325. Cron-runnable substrate that walks learned_rules.yaml's
pending_activation bucket and promotes / refuses / leaves-pending
each rule based on the Reality-Anchor verdict against the
operator's ground-truth catalog.

What ships:

  - core/learned_rule_ra_pass.py (NEW): pure-function policy
    layer. run_ra_pass(config, verifier) walks pending, applies
    the verdict matrix (confirmed/not_in_scope → promoted to
    active; contradicted → status='refused' kept in pending with
    verdict+reason stamped; unknown → stays pending untouched
    for operator review; verifier exception → still_pending +
    verifier_error outcome), returns RAPassResult with counts.
    Frozen dataclasses; original config never mutated.

  - daemon/scheduler/task_types/learned_rule_ra_pass.py (NEW):
    scheduler task type wrapper. Loads YAML + ground_truth,
    builds a verifier closure that mirrors verify_claim.v1's
    _evaluate_fact pattern matching (no tool-runtime envelope
    needed), invokes run_ra_pass, saves new YAML only if
    something changed, emits learned_rule_activated /
    learned_rule_refused audit events per outcome.

  - daemon/scheduler/task_types/__init__.py: exports runner.
  - daemon/app.py: registers task_type='learned_rule_ra_pass'
    at lifespan. Operators wire a scheduled_tasks.yaml entry
    with schedule='every 24h'.

Tests: test_learned_rule_ra_pass.py — 16 cases covering 10
policy-matrix branches (confirmed/not_in_scope/contradicted/
unknown/unrecognized/verifier_exc + empty/preserve_active/
mixed_batch/no_mutation/timestamps) and 5 scheduler runner
cases (load_failure ok=False, empty clean, no-op preserves
mtime, audit emits with correct payload, missing audit_chain
no-crash). Sandbox-verified 16/16 pass.

ADR-0072 progress: T1 substrate (B290) + T2 fsf provenance CLI
(B303) + T3 RA cron (B325, this) shipped. T4 orchestrator
integration + T5 frontend pane remain.

Phase α scorecard: 7/10 closed; ADR-0072 now at 3/5."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 325 complete - ADR-0072 T3 RA cron shipped ==="
echo "Phase alpha: 7/10 scale ADRs closed; ADR-0072 at 3/5."
echo ""
echo "Press any key to close."
read -n 1
