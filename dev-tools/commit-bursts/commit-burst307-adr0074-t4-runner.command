#!/bin/bash
# Burst 307 - ADR-0074 T4: end-to-end consolidation runner.
#
# Composes the B302 selector + B306 summarizer into the atomic
# SQL pass. One pass:
#   selector -> candidate batch
#   -> fetch source rows, group by (instance_id, layer)
#   -> for each group:
#        summarizer.summarize_consolidation_batch(...)
#        WITH conn:  # atomic per group
#          INSERT summary row (state='summary', run_id stamped)
#          UPDATE sources -> state='consolidated' + lineage links
#        emit memory_consolidated per source
#   -> emit run_started / run_completed bookends
#
# Caller (T5 scheduled task) holds the write_lock per single-
# writer SQLite discipline. The function does not lock itself.
#
# What ships:
#
# 1. src/forest_soul_forge/core/memory_consolidation.py:
#    - ConsolidationRunResult frozen dataclass: run_id +
#      started_at + completed_at + batches_processed +
#      summaries_created + sources_consolidated + errors tuple.
#    - _fetch_source_entries: pulls (entry_id, instance_id,
#      content, layer, claim_type, created_at) for candidates.
#      Filters content_encrypted=1 (no decryption key in the
#      runner - those stay pending, forward-compat skip).
#    - _group_by_instance_and_layer: (instance_id, layer)
#      partitioning. The summarizer enforces single-layer; this
#      keeps lineage clean by also splitting per-agent.
#    - _content_digest: SHA-256 matching the column convention.
#    - run_consolidation_pass(conn, *, policy, provider,
#      audit_chain, agent_dna_for_summary=None, now=None):
#      async runner. Mints UUID4 run_id, emits run_started,
#      runs the selector, groups, summarizes + writes per group
#      inside `with conn:` (Python's sqlite3 context manager
#      auto-commits on clean exit + auto-rollbacks on exception,
#      avoids the "cannot start a transaction within a
#      transaction" sqlite3 driver collision with explicit
#      BEGIN/COMMIT), emits memory_consolidated per source AFTER
#      commit, emits run_completed at the end. Per-group errors
#      accumulate as soft (instance_id, layer, message) tuples;
#      a failed group leaves its sources in pending state for
#      next pass.
#    - _emit + _wall_clock_ms helpers (audit emit is best-effort
#      per ADR-0041 scheduler posture; wall clock for the
#      run_completed payload).
#
# 2. tests/unit/test_memory_consolidation_selector.py - 9 new T4 cases:
#    Happy path:
#      - end-to-end two agents -> two groups -> two summaries +
#        all sources flipped + correct counts
#      - lineage links consolidated_into the summary in the
#        same instance_id
#      - bookend audit events bracket per-entry events + all
#        three share run_id
#    Empty pass:
#      - bookend pair still emits (no chain gap on idle runs)
#    Skip paths:
#      - content_encrypted=1 sources stay pending
#    Error paths:
#      - provider failure -> sources stay pending, soft error
#        accumulates
#      - partial success: flaky provider succeeds for one group,
#        fails for the other; successful group commits + failed
#        group's sources stay pending
#    Invariants:
#      - summary row claim_type='agent_inference' regardless of
#        source claim_types (ADR-0027 amendment alignment)
#      - every touched row carries the same consolidation_run
#        UUID
#
# Sandbox-verified all 5 end-to-end scenarios including the
# multi-agent / encrypted-skip / partial-failure cases against
# the v23 schema with in-memory SQLite + mock provider + mock
# audit chain.
#
# What's NOT in T4 (queued):
#   T5: /memory/consolidation/status endpoint + scheduled-task
#       wiring (uses ADR-0075 budget cap to throttle pass
#       frequency + ADR-0041 circuit breaker on persistent
#       errors) + operator runbook + fsf memory pin/unpin CLI.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/memory_consolidation.py \
        tests/unit/test_memory_consolidation_selector.py \
        dev-tools/commit-bursts/commit-burst307-adr0074-t4-runner.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0074 T4 - consolidation runner (B307)

Burst 307. Composes B302 selector + B306 summarizer into the
end-to-end atomic SQL pass. One run mints a UUID4 run_id,
emits run_started, selects candidates, groups by (instance_id,
layer), summarizes per group, atomically inserts the summary
row + flips source rows + tags everything with the run_id,
emits memory_consolidated per source, emits run_completed.
Caller (T5 scheduled task) holds the write_lock per Forest's
single-writer SQLite discipline.

What ships:

  - memory_consolidation.py: ConsolidationRunResult frozen
    dataclass (run_id + bookend timestamps + batches +
    summaries + sources + errors tuple). _fetch_source_entries
    pulls source row tuples, filtering encrypted=1 (no key in
    the runner - those stay pending, forward-compat skip).
    _group_by_instance_and_layer partitions for clean lineage
    (an agents memories dont fold into another agents summary).

    run_consolidation_pass async runner: uses Python sqlite3
    connection-as-context-manager for the per-group transaction
    so commit/rollback is automatic and we dont collide with
    sqlite3s implicit transaction state (explicit BEGIN
    triggers 'cannot start a transaction within a transaction').
    Audit emits land AFTER COMMIT so chain hash doesnt couple
    to SQL state. Per-group errors accumulate as soft tuples;
    failed groups leave sources pending for next pass.

Tests: test_memory_consolidation_selector.py - 9 new T4 cases
covering end-to-end multi-agent, lineage link verification,
bookend audit emit, empty pass (still emits bookends), encrypted-
source skip, provider-failure rollback, partial success across
groups, agent_inference claim_type on summary, run_id tagging on
every touched row.

Sandbox-verified all 5 end-to-end scenarios including multi-
agent / encrypted-skip / partial-failure against v23 schema +
in-memory SQLite + mock provider + mock audit chain.

ADR-0074 progress: 4/5 (T1 substrate + T2 selector + T3
summarizer + T4 runner). T5 endpoint + runbook + pin/unpin
CLI queued."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 307 complete - ADR-0074 T4 runner shipped ==="
echo ""
echo "Press any key to close."
read -n 1
