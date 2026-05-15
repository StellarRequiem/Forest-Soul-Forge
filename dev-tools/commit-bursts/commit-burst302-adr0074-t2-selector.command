#!/bin/bash
# Burst 302 - ADR-0074 T2: ConsolidationSelector.
#
# First runner on B294 substrate. Pure-function candidate-batch
# selector: given a SQLite connection + policy + now-anchor,
# return the list of entry_ids eligible for the next consolidation
# pass. No LLM yet, no writes - this is the substrate that T3
# (summarizer) and T4 (scheduled runner) compose.
#
# What ships:
#
# 1. src/forest_soul_forge/core/memory_consolidation.py:
#    - ConsolidationPolicy frozen dataclass with operator-tunable
#      knobs:
#        * min_age_days (default 14) - newer entries stay pending
#        * max_batch_size (default 200) - one pass yields ~one
#          summary per agent per layer
#        * eligible_layers (default ('episodic',)) - working is
#          ephemeral, consolidated is already summary
#        * eligible_claim_types (default observation+user_statement)
#          - promises and preferences are higher-stakes and not
#            auto-consolidated until operator opt-in (ADR-0027
#            amendment alignment)
#      __post_init__ rejects: negative min_age_days, zero/negative
#      max_batch_size, empty eligible_layers, empty eligible_
#      claim_types.
#
#    - select_consolidation_candidates(conn, *, policy, now=None):
#      SQL query joining all filter conditions (state='pending',
#      deleted_at IS NULL, layer IN ..., claim_type IN ...,
#      created_at < cutoff). ORDER BY created_at ASC (FIFO -
#      oldest first), LIMIT max_batch_size. Returns plain list
#      of entry_ids - trivially testable + portable.
#
# 2. tests/unit/test_memory_consolidation_selector.py - 14 cases:
#    Happy path:
#      - empty DB -> []
#      - 20-day episodic observation eligible
#    Filter gates:
#      - young (5-day) filtered
#      - min_age_days=0 still requires strict-less-than (1-day
#        passes, 0-day excluded)
#      - working-layer filtered
#      - custom eligible_layers honored (operator widens)
#      - promise/preference/agent_inference/external_fact filtered
#      - consolidated/summary/pinned/purged all filtered
#      - deleted_at != NULL filtered
#    Ordering + batch:
#      - oldest first (FIFO)
#      - batch_size=3 over 10 rows picks oldest 3
#    Policy validation:
#      - negative min_age refused
#      - zero batch_size refused
#      - empty eligible_layers refused
#      - empty eligible_claim_types refused
#      - default values pinned to ADR specification
#
# Sandbox-verified all 14 scenarios.
#
# What's NOT in T2 (queued):
#   T3: ConsolidationSummarizer - LLM call producing the summary
#       content for a batch of source entry_ids.
#   T4: scheduled-task wiring (uses ADR-0075 budget cap) +
#       end-to-end runner that flips state from pending ->
#       consolidated, sets consolidated_into + consolidation_run.
#   T5: /memory/consolidation/status endpoint + runbook +
#       fsf memory pin/unpin CLI.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/memory_consolidation.py \
        tests/unit/test_memory_consolidation_selector.py \
        dev-tools/commit-bursts/commit-burst302-adr0074-t2-selector.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0074 T2 - ConsolidationSelector (B302)

Burst 302. First runner on B294 substrate. Pure-function
candidate-batch selector for the consolidation runner: given a
SQLite connection + policy + now-anchor, return the list of
entry_ids eligible for the next consolidation pass. No LLM, no
writes - this is the substrate T3 (summarizer) and T4
(scheduled runner) compose.

What ships:

  - memory_consolidation.py: ConsolidationPolicy frozen
    dataclass with operator-tunable knobs (min_age_days,
    max_batch_size, eligible_layers, eligible_claim_types).
    __post_init__ validates all four against negative /
    zero / empty inputs at construction time so invalid
    policies fail loud rather than producing surprising SQL.

    select_consolidation_candidates(conn, *, policy, now=None)
    runs one SQL query AND-combining: state='pending',
    deleted_at IS NULL, layer IN policy.eligible_layers,
    claim_type IN policy.eligible_claim_types, created_at <
    (now - min_age_days). ORDER BY created_at ASC (FIFO -
    oldest first matches the operator mental model). LIMIT
    max_batch_size. Returns a plain list[str] of entry_ids.

Tests: test_memory_consolidation_selector.py - 14 cases
covering empty DB, happy path, age gate (including the
strict-less-than-cutoff semantics at min_age_days=0), all
non-eligible layers / claim_types / states / deleted-at
filtering, oldest-first ordering, batch-size cap honor, and
all four ConsolidationPolicy validation rejections.

Sandbox-verified all 14 scenarios.

Queued T3-T5: ConsolidationSummarizer (LLM rollup), scheduled
runner (ADR-0075 budget-capped), operator runbook + endpoint
+ pin/unpin CLI."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 302 complete - ADR-0074 T2 selector shipped ==="
echo ""
echo "Press any key to close."
read -n 1
