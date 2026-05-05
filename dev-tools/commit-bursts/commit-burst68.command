#!/usr/bin/env bash
# Burst 68: ADR-0036 T3b — LLM-dispatching scan runner.
#
# Closes the auto-detection minimum bar. The Verifier can now walk
# pairs from find_candidate_pairs (T3a), classify each via an LLM,
# and stamp contradictions when high-confidence cases surface — all
# pure logic with injected callables, fully unit-testable.
#
# T4 (scheduler) and T5 (daemon endpoint) wire this runner into the
# operator-facing surfaces.
#
# Test delta: 2022 -> 2050 passing (+28).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 68 — ADR-0036 T3b VerifierScan runner ==="
echo
clean_locks
git add src/forest_soul_forge/verifier/__init__.py \
        src/forest_soul_forge/verifier/scan.py \
        src/forest_soul_forge/core/audit_chain.py \
        tests/unit/test_verifier_scan.py \
        commit-burst68.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ADR-0036 T3b: VerifierScan runner — LLM classification + flag

The runner that consumes pairs from Memory.find_candidate_pairs
(T3a), classifies each via an LLM call, and stamps contradictions
when the classification surfaces same-topic + contradictory at
confidence above the operator's threshold.

Pure logic with injected callables — does NOT depend on the
dispatcher, daemon, or providers. The classify_callable is wired
by the caller:
  - In production, the daemon's verifier service binds it to
    llm_think.v1 going through the governance pipeline.
  - In tests, a small mock returns canned LLM responses per pair.

Why this design (consistent with ADR-0039 §4 'no god objects'):
  - Scan logic is independent of LLM transport. Testing the
    classification-act logic doesn't need a real model or even
    the dispatcher.
  - The flagger callable is similarly injected. In production it
    points at memory.flag_contradiction; in tests, a mock that
    records flags.
  - The scheduler (T4) and the on-demand endpoint (T5) are both
    callers of this module. They don't subclass it; they bind
    their own callables.

Per-pair behavior (ADR-0036 §2.3):
  - same_topic + contradictory + confidence >= threshold -> flag
  - same_topic + contradictory + confidence <  threshold -> noop
    (low-confidence cases skipped, not auto-flagged — §4.1
    false-positive mitigation)
  - same_topic + non-contradictory -> noop
  - different_topics -> noop

LLM-prompt design (build_classification_prompt):
- Constrains the model to a strict JSON shape: same_topic /
  contradictory / kind / confidence / reasoning. Inlines the four-
  way kind taxonomy from ADR-0027-am §7.3 (direct / updated /
  qualified / retracted) with one-line semantics for each.
- Includes optional [claim_type] tags so the model knows whether
  it's looking at a preference vs. user_statement vs. agent_inference.
- Explicit instruction NOT to inflate confidence beyond honest
  uncertainty — load-bearing for ADR-0036 §4 false-positive control.

LLM-response parser (parse_llm_classification):
- Tolerant: extracts the first {...} block (some models prefix
  prose), validates keys, clamps confidence into [0.0, 1.0].
- Defense-in-depth: forces kind=None when not (same_topic AND
  contradictory) — even if the LLM emits a kind despite
  same_topic=false.
- Returns a low-confidence ClassificationResult tagged with
  PARSE_ERROR on malformed output (rather than raising). The
  caller's action='error' branch records it cleanly.

Output shape (ScanResult):
- target_instance_id
- pairs_considered / pairs_classified / flags_written
- low_confidence_skipped / unrelated_skipped /
  no_contradiction_skipped / errors
- outcomes: list[PairOutcome] with per-pair classification +
  action + (when flagged) contradiction_id

Suitable as the payload for a verifier_scan_completed audit event;
the operator's review surface (ADR-0037) can filter by detected_by
and tally these counts.

Audit event added: verifier_scan_completed in KNOWN_EVENT_TYPES per
ADR-0036 §2.4.

Tests (test_verifier_scan.py +28 cases):
- TestPromptBuilder (5): contains both entries; contains kind
  taxonomy; includes claim_type tags when provided; omits when
  blank; instructs strict JSON.
- TestParser (9): well-formed; prose-prefixed (extracts first {});
  kind forced null when not contradictory; invalid kind returns
  PARSE_ERROR; clamps confidence high/low; non-numeric confidence;
  empty response; no-JSON-block; invalid JSON.
- TestVerifierScanInit (2): min_confidence range check;
  verifier_id required.
- TestRunScan (10): no-pairs returns empty; high-confidence
  contradiction flags (incl. flagger gets correct earlier/later/
  kind/detected_by); low-confidence skipped; unrelated skipped;
  same-topic-no-contradiction skipped; classify-error recorded;
  flagger-error recorded; aggregation across multiple pairs (6
  pairs, mixed outcomes); max_pairs caps scan; dedup against
  existing flags (already-flagged pairs filtered before classify);
  outcomes carry classification when classified.
- TestAuditEventType (1): verifier_scan_completed in
  KNOWN_EVENT_TYPES.

Test delta: 2022 -> 2050 passing (+28). Zero regressions.

Next: Burst 69 — ADR-0036 T4 (per-Verifier scheduled-task cron)
+ T5 (/verifier/scan daemon endpoint). Likely one combined burst
since both are wiring jobs over this module's interface."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 68 landed. ADR-0036 T3b in production. VerifierScan runner ready."
echo "Auto-detection minimum bar closed: pairs -> classify -> flag pipeline complete."
echo "Next: Burst 69 (ADR-0036 T4+T5 — scheduler + daemon endpoint)."
echo ""
read -rp "Press Enter to close..."
