#!/bin/bash
# Burst 306 - ADR-0074 T3: ConsolidationSummarizer.
#
# Builds on B302's selector. Takes the candidate batch from
# select_consolidation_candidates + an LLM provider, produces a
# SummaryDraft. Does NOT touch the database - that's T4 (the
# runner). Keeping summarization pure with respect to SQL lets
# T4 compose the SQL transaction atomically around the LLM call.
#
# What ships:
#
# 1. src/forest_soul_forge/core/memory_consolidation.py:
#    - SourceEntry frozen dataclass: one memory_entry row's worth
#      of consolidation input (entry_id + content + layer +
#      claim_type + created_at).
#    - SummaryDraft frozen dataclass: the rollup output
#      (content + source_entry_ids tuple for lineage + layer +
#      claim_type=agent_inference per ADR-0027 amendment + the
#      prompt the LLM saw).
#    - ConsolidationSummarizerError: soft-fail signal for the
#      runner. T4 treats this as 'batch stays pending, try next
#      pass'. Persistent failure surfaces via ADR-0041
#      max_consecutive_failures.
#    - _render_summary_prompt: minimal numbered-observations
#      prompt with a layer header + an instruction to produce a
#      single-paragraph faithful rollup (no advice/analysis).
#    - summarize_consolidation_batch(sources, *, provider,
#      max_tokens=200): async function calling provider.complete
#      with TaskKind.GENERATE (with a string-fallback for tests
#      that mock providers without importing daemon code).
#      Refuses on:
#        * empty source batch
#        * any source with empty/whitespace-only content
#        * multi-layer batch (single-layer is the runner contract)
#        * empty/whitespace-only provider response (would write
#          junk summary)
#      Wraps provider exceptions in ConsolidationSummarizerError
#      so the runner's circuit-breaker counts correctly. Strips
#      whitespace from the response so audit-chain digests stay
#      stable across whitespace drift.
#
# 2. tests/unit/test_memory_consolidation_selector.py - 10 new T3 cases:
#    Happy path:
#      - summarize_returns_summary_draft_with_lineage
#      - prompt_carries_observations_and_layer (prompt-shape pin)
#      - forwards_max_tokens_and_task_kind (provider sees the
#        canonical TaskKind.GENERATE + operator-tunable max_tokens)
#    Refusal paths:
#      - empty batch
#      - empty content
#      - multi-layer batch
#      - provider exception wrapped
#      - empty response
#    Polish:
#      - whitespace-stripped response
#      - SummaryDraft is frozen
#
# Sandbox-verified all 8 functional cases via asyncio.run + a
# deterministic _MockProvider.
#
# What's NOT in T3 (queued):
#   T4: end-to-end runner. Composes selector -> summarizer ->
#       SQL transaction that (a) inserts the summary row in
#       state='summary', (b) flips every source to state=
#       'consolidated' + sets consolidated_into + sets
#       consolidation_run, (c) emits the three audit events
#       (run_started + per-entry memory_consolidated + run_completed)
#       under the daemon write_lock. Wired as a scheduled task
#       with ADR-0075 budget cap.
#   T5: /memory/consolidation/status endpoint + operator runbook
#       + fsf memory pin/unpin CLI.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/memory_consolidation.py \
        tests/unit/test_memory_consolidation_selector.py \
        dev-tools/commit-bursts/commit-burst306-adr0074-t3-summarizer.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(memory): ADR-0074 T3 - ConsolidationSummarizer (B306)

Burst 306. Builds on B302 selector. Takes the candidate batch +
a provider, produces a SummaryDraft via LLM. Does NOT touch the
database - that's T4 (the runner). Keeping summarization pure
with respect to SQL lets T4 compose the SQL transaction
atomically around the LLM call.

What ships:

  - memory_consolidation.py: SourceEntry + SummaryDraft frozen
    dataclasses (lineage tuple + layer + claim_type='agent_inference'
    per ADR-0027 amendment + prompt-the-LLM-saw for audit digest).
    ConsolidationSummarizerError for soft-fail (batch stays
    pending; persistent failures surface via ADR-0041 circuit
    breaker). _render_summary_prompt builds a minimal numbered-
    observations prompt with layer header + an instruction to
    produce a single-paragraph faithful rollup (no advice).

    summarize_consolidation_batch async fn calls provider.complete
    with TaskKind.GENERATE (with string-fallback for test mocks
    that dont import daemon code). Refuses on empty batch / empty
    content / multi-layer batch (single-layer is the runner
    contract) / empty provider response. Wraps provider exceptions
    so the runner's circuit-breaker counts correctly. Strips
    response whitespace for stable audit-chain digests.

Tests: test_memory_consolidation_selector.py - 10 new T3 cases
covering happy path + lineage + prompt shape + max_tokens/
task_kind forwarding + 5 refusal paths + whitespace strip +
SummaryDraft frozen-ness.

Sandbox-verified all 8 functional cases via asyncio.run + a
deterministic _MockProvider.

Queued T4-T5: end-to-end runner (selector -> summarizer ->
atomic SQL transaction flipping source rows + inserting summary
+ emitting the three audit events under write_lock; scheduled
via ADR-0075 budget-capped task), /memory/consolidation/status
endpoint + operator runbook + fsf memory pin/unpin CLI."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 306 complete - ADR-0074 T3 summarizer shipped ==="
echo ""
echo "Press any key to close."
read -n 1
