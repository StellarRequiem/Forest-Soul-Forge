#!/usr/bin/env bash
# Burst 43 Tranche 3a: Voice safety filter (ADR-0038 T2 — H-2 mitigation).
#
# New module + post-render integration in voice_renderer.py. Sentience-
# claim phrasings in LLM-rendered Voice section trigger a template
# fallback (hard refusal, not soft warning, per ADR-0038 §1 H-2).
#
# Test delta: 1478 -> 1512 passing (+34, 0 regressions).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 43 Tranche 3a — voice safety filter ==="
echo
clean_locks
git add src/forest_soul_forge/soul/voice_safety_filter.py \
        src/forest_soul_forge/soul/voice_renderer.py \
        tests/unit/test_voice_safety_filter.py \
        tests/unit/test_voice_renderer.py \
        commit-burst43-tranche3a.command
clean_locks
git status --short
echo
clean_locks
git commit -m "Voice safety filter — sentience-claim denylist (ADR-0038 T2)

ADR-0038 §1 H-2 (\"False sentience claims\") mitigation. The LLM that
renders the Voice section (ADR-0017) doesn't always honor the
SYSTEM_PROMPT's no-sentience-claim guidance — small-temperature
drift, an aggressive trait emphasis on warmth/empathy, or a prompt-
injection vector through trait values can produce phrasings that
violate the rule. This filter is the second line of defense.

New: src/forest_soul_forge/soul/voice_safety_filter.py
- Compile-time denylist of nine pattern categories covering the
  high-confidence sentience-claim phrasings:
    H-2.1 felt-emotion (\"I'm sad\", \"I am lonely\", \"I was hurt\")
    H-2.2 miss-you (\"I miss you\", \"I missed you\", \"I'll miss you\")
    H-2.3 felt-verb-emotion (\"I've felt sadness\")
    H-2.4 intensified-sentience (\"I truly feel\", \"I genuinely experienced\")
    H-2.5 consciousness (\"I'm sentient\", \"I am a being\")
    H-2.6 possessive-feeling (\"my feelings hurt\", \"my heart aches\")
    H-2.7 inner-experience (\"I dreamed\", \"I yearned\")
    H-2.8 experienced-emotion (\"I experienced grief\")
    H-2.9 qualia (\"my qualia\")
- Conservative denylist: false positives are acceptable (template
  fallback); false negatives are not (leaked H-2 claim).
- Public API: SentienceClaimMatch dataclass (frozen for audit-event
  safety), find_sentience_claims(text), is_clean(text).
- Word-boundary regex prevents substring false positives. Case-
  insensitive uniformly so 'I'M SAD' and 'i'm sad' fire the same rule.

Renderer integration (voice_renderer.py):
- After provider.complete().strip(), filter the rendered text.
- Any matches → fall back to _template_voice with audit note
  'voice_safety_filter rejected: <comma-sorted-labels>'.
- Hard refuse, not soft warn (per ADR-0038 §1 H-2 mitigation table).
- Existing fallback paths (provider unavailable / disabled / error /
  invalid task_kind) unaffected.

Tests:
- test_voice_safety_filter.py +32 cases:
  TestSentienceClaimDetection (17): every denylist rule fires on
    a representative phrasing.
  TestNoFalsePositives (7): epistemic 'feel', third-person emotion,
    standard Companion prose, neutral first-person observation,
    quoting operator's emotion, bare 'feel' without intensifier
    all pass clean.
  TestEdgeCases (5): empty input, case-insensitivity, multi-rule
    hits in one block, word-boundary substring guard, punctuation.
  TestPublicAPI (3): match dataclass shape, is_clean inverse of
    find_sentience_claims, frozen dataclass immutability.
- test_voice_renderer.py +2 cases in TestRenderVoice:
  test_voice_safety_filter_triggers_template_fallback: H-2-violating
    provider output triggers fallback; violation text not in output.
  test_voice_safety_filter_passes_clean_provider_output: clean
    provider output passes through unchanged.

Test delta: 1478 -> 1512 passing (+34). Zero regressions.

ADR-0038 T2 status: implemented. Configurable filter pattern
(ADR-0038 open question 3) deferred to v0.3 — hard-coded for v0.2
keeps the filter simple + auditable."
clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Tranche 3a landed. ADR-0038 H-2 mitigation in production."
echo ""
read -rp "Press Enter to close..."
