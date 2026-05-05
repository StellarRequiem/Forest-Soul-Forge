#!/usr/bin/env bash
# Burst 67: ADR-0036 T3a — candidate-pair pre-filter helper.
#
# The cheap first stage of the Verifier Loop's scan. Returns pairs of
# memory entries that share enough vocabulary to plausibly be talking
# about the same topic, so the LLM-classification stage (T3b, follow-on
# burst) can spend its budget on high-quality candidates.
#
# Test delta: 1998 -> 2022 passing (+24).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 67 — ADR-0036 T3a candidate-pair pre-filter ==="
echo
clean_locks
git add src/forest_soul_forge/core/memory.py \
        tests/unit/test_memory_find_candidate_pairs.py \
        commit-burst67.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ADR-0036 T3a: Memory.find_candidate_pairs pre-filter helper

The cheap first stage of the Verifier Loop's scan per ADR-0036 §2.1.
Returns pairs of memory entries that share enough vocabulary to
plausibly be talking about the same topic, so the LLM-classification
stage (T3b — follow-on burst) can spend its budget on high-quality
candidates rather than burning tokens on obviously-unrelated pairs.

T3b (the LLM-dispatching scan runner that consumes these pairs and
acts on classifications) is a separate burst. T3a stays pure-Python
+ pure-SQLite — no provider dependency, fully unit-testable.

Memory.find_candidate_pairs design:
- Args: instance_id (required); since_iso (optional, strictly-after
  for incremental scans); max_pairs (default 20 per ADR §3); min_overlap
  (default 2 distinct overlapping non-stopword tokens).
- Eligibility: same instance_id (cross-agent scan deferred to v0.4
  per ADR §6); claim_type ∈ {preference, user_statement,
  agent_inference} per §2.1 (observation + external_fact aren't
  paired — directly logged events / external authority are operator
  review territory, not Verifier classification territory).
- Dedup against memory_contradictions: a pair whose two entry_ids
  both appear in any single contradictions row is excluded
  (resolved AND unresolved both block — operators don't want
  re-flag noise on a row they already rejected/resolved).
- Word-overlap heuristic: lowercase + tokenize on non-alphanumerics
  + drop stopwords (curated 50ish English high-impact set) + drop
  short tokens (<3 chars). Two entries pair when their token sets
  share >= min_overlap distinct words.
- Output: list of {earlier_entry_id, later_entry_id,
  earlier_claim_type, later_claim_type, shared_words, overlap_size},
  earlier-first by created_at, sorted by descending overlap (most-
  similar first), capped at max_pairs.
- Implementation: pure-Python in-memory join. O(n²) but n is bounded
  (typical agent has hundreds of entries); v0.4 may revisit if a
  concrete operator surfaces with > 1k entries / agent.

since_iso semantic is strictly-after (>) not inclusive (>=). Callers
pass last_scan_at and want to exclude entries already considered in
that prior scan.

_tokenize_for_overlap helper lives at module-level for testability.
Stopword list is intentionally narrow — the goal is signal-
preservation for the heuristic, not full NLP. Embedding-similarity
matching is deferred to v0.4 per ADR-0036 'trade-offs and rejected
alternatives'.

Tests (test_memory_find_candidate_pairs.py +24 cases):
- TestTokenize (6): basic words, lowercases, drops stopwords, drops
  short tokens, punctuation stripped, numbers kept.
- TestEligibility (5): only-eligible-claim-types pair (observation/
  external_fact don't); eligible types pair; mixed types can pair;
  only-same-instance (cross-agent scan deferred); since_iso
  far-future excludes all; since_iso far-past includes all.
  (The since_iso filter is tested via far-future / far-past cutoffs
  rather than relative timestamps because _now_iso has second-
  resolution; sub-second sleep dances are fragile.)
- TestOverlap (4): no-overlap empty; min_overlap=1 single-word
  match; min_overlap=2 default rejects single-word; shared_words
  alphabetically sorted.
- TestDedup (2): already-flagged pair excluded after
  flag_contradiction; other pairs in the same scan still surface.
- TestOrderingAndCap (5): earlier/later by created_at; sorted by
  descending overlap; max_pairs cap (5 entries → C(5,2)=10
  combinations capped to 3); zero max_pairs returns empty;
  single-entry returns empty; no-entries returns empty.

Test delta: 1998 -> 2022 passing (+24). Zero regressions.

Next: Burst 68 — ADR-0036 T3b. The scan-runner that takes pairs
from this helper, dispatches llm_think.v1 per pair with a
constrained classification prompt, and (when LLM confidence ≥ 0.80)
calls memory_flag_contradiction.v1. Will likely also need a
verifier_scan_completed audit event type."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 67 landed. ADR-0036 T3a in production. Candidate-pair filter ready."
echo "Next: Burst 68 (ADR-0036 T3b — LLM-dispatching scan runner)."
echo ""
read -rp "Press Enter to close..."
