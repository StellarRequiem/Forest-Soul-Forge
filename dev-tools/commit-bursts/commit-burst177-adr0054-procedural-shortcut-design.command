#!/bin/bash
# Burst 177 — ADR-0054 — procedural-shortcut dispatch path
# (DESIGN). Responds to the 2026-05-06 outside assessment's §5
# framing: "fast pattern-match → react pathway (bypass heavy LLM
# when possible)."
#
# Drives option I from the post-assessment direction list.
# Implementation queued in 6 tranches (~4-5 bursts total).
#
# Six decisions locked:
#
# 1. Schema. New sibling table memory_procedural_shortcuts (v15
#    to v16, additive). Sibling not column-extension because
#    procedural-shortcut access pattern (vector search) differs
#    from episodic/semantic memory (text search), and ADR-0040
#    trust-surface decomposition prefers separate tables for
#    separately-grantable capabilities.
#
# 2. Match algorithm. Two-stage filter:
#      cosine(embedding) >= FSF_PROCEDURAL_COSINE_FLOOR (0.92)
#      success_count - failure_count >= REINFORCEMENT_FLOOR (2)
#    Conservative defaults — false-negative (fall through to LLM)
#    preferred over false-positive (skip with wrong answer) until
#    operator calibrates.
#
# 3. Pipeline integration. New ProceduralShortcutStep in
#    governance_pipeline before LookupStep. New StepResult.shortcut
#    verdict alongside GO/REFUSE/PENDING. Eligibility gates:
#    llm_think + task_kind=conversation + domain=assistant +
#    posture != red + master switch on. Posture interaction:
#    green permissive, yellow emits caution-event, red blocks.
#
# 4. Audit-chain visibility. New event_type tool_call_shortcut
#    emitted alongside the substituted action's tool_call_succeeded
#    event. Carries shortcut_id, cosine score, reinforcement state,
#    tokens-saved estimate, llm_round_trip_skipped flag. Both
#    events use existing event_data shape per ADR-0005 — additive
#    only, no schema migration on the chain itself.
#
# 5. Reinforcement (operator feedback loop). Two paths to populate
#    the table:
#      - Auto-capture: opt-in via FSF_PROCEDURAL_AUTO_CAPTURE.
#        Every llm_think reply auto-stores a shortcut row at
#        success_count=0 (ineligible until reinforced).
#      - Operator-tagged: new memory_tag_outcome.v1 tool reading
#        thumbs-up/down from chat UI; updates success/failure
#        counters. Soft-deletes when failures > successes.
#
# 6. Operator overrides. Four env-var knobs:
#      FSF_PROCEDURAL_SHORTCUT_ENABLED=0  (master, off by default)
#      FSF_PROCEDURAL_AUTO_CAPTURE=0      (off by default)
#      FSF_PROCEDURAL_COSINE_FLOOR=0.92
#      FSF_PROCEDURAL_REINFORCEMENT_FLOOR=2
#    Plus a Chat-tab settings card (T6) for review + per-row
#    delete.
#
# Implementation tranches (4-5 bursts total):
#   T1 schema + table accessor (v15 to v16 migration)
#   T2 embedding adapter (nomic-embed-text via existing llm_think
#      embed task; NumPy cosine + reinforcement gate)
#   T3 ProceduralShortcutStep + StepResult.shortcut verdict +
#      dispatcher branch
#   T4 audit emission + tool_call_shortcut event type
#   T5 reinforcement tools (memory_tag_outcome.v1 +
#      memory_forget_shortcut.v1) + chat-tab thumbs surface
#   T6 settings UI (review/delete) + docs/runbooks/procedural-
#      shortcuts.md operator guide
#
# Doctrinal point worth highlighting: this ADR is COMPATIBLE
# with ADR-0001 D2 identity immutability. Procedural memory is
# per-instance state, not identity. Constitution_hash + DNA stay
# stable; only what the agent KNOWS evolves, not what it IS.
# This is the doctrine line the assessment's "soul.md gradual
# evolution" idea crossed; this ADR explicitly does not.
#
# Auto-capture safety: shortcuts only store what the assistant
# already produced through the FULL constitution + governance
# path. Replaying a shortcut is the same as re-running llm_think
# and getting the same answer — just faster. Constitution checks
# are not bypassed; they ran once at storage time and the result
# was constitution-clean.
#
# Latency win: 50ms shortcut vs. 2-5s llm_think round-trip.
# Operator-visible. Pairs with the trainability framing — the
# operator shapes the assistant's behavior by tagging turns,
# not by retraining a model.
#
# This commit is design only. No code touched, no tests added.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0054-procedural-shortcut-dispatch.md \
        dev-tools/commit-bursts/commit-burst177-adr0054-procedural-shortcut-design.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0054 — procedural shortcut dispatch (B177)

Burst 177. Drives option I from the 2026-05-06 outside-assessment
direction list. Closes the assessment's §5 'fast pattern-match
to react' framing with substrate that's compatible with Forest's
identity invariants.

ADR-0054 locks 6 decisions:
1. Schema: new sibling table memory_procedural_shortcuts (v15
   to v16, additive). Sibling not column-extension per ADR-0040.
2. Match algorithm: cosine >= 0.92 + reinforcement >= 2 (both
   gates required; conservative defaults).
3. Pipeline integration: new ProceduralShortcutStep before
   LookupStep + new StepResult.shortcut verdict. Eligibility
   gates llm_think conversation domain=assistant posture not red
   master switch on. Posture: green permissive, yellow caution
   event, red blocks.
4. Audit visibility: new tool_call_shortcut event_type emitted
   alongside the substituted tool_call_succeeded. Both additive
   per ADR-0005 canonical-form contract.
5. Reinforcement: auto-capture (opt-in) + operator-tagged
   thumbs-up/down via memory_tag_outcome.v1.
6. Operator overrides: 4 env-var knobs + settings UI for
   review/delete.

Six implementation tranches queued (T1-T6, ~4-5 bursts total):
schema, embedding adapter, pipeline step, audit emission,
reinforcement tools, settings UI.

Doctrinal point: this ADR is COMPATIBLE with ADR-0001 D2
identity immutability. Procedural memory is per-instance state,
not identity. Constitution_hash + DNA stay stable; only what
the agent KNOWS evolves, not what it IS. The assessment's
soul.md gradual-evolution idea crossed that line; this ADR
explicitly does not.

Auto-capture safety: shortcuts only store actions the
constitution + governance path ALREADY approved. Replaying a
shortcut is the same as re-running llm_think and getting the
same answer — just faster. Constitution checks are not bypassed.

Latency win: ~50ms shortcut vs 2-5s llm_think round-trip.
Trainability: operator shapes behavior by tagging turns
good/bad, not by retraining a model.

Per ADR-0044 D3: implementation will be userspace-only with one
additive schema migration. No new HTTP endpoints required for
the substrate (T5/T6 add chat-tab UI; the daemon-side reads
ride the existing /memory/* surface)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 177 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
