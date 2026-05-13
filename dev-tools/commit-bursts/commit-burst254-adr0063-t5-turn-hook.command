#!/bin/bash
# Burst 254 — ADR-0063 T5: conversation runtime pre-turn hook.
#
# B252's RealityAnchorStep gates every tool call. B253 added the
# reality_anchor agent role + singleton. B254 closes the third
# integration point: every assistant turn gets cross-checked
# against operator ground truth BEFORE it lands in the
# conversation's turn log + reaches the operator.
#
# Files:
#
# 1. src/forest_soul_forge/daemon/reality_anchor_turn.py (NEW)
#    Shared helper. Public surface:
#      check_turn_against_anchor(response_text,
#                                constitution_path, audit,
#                                conversation_id,
#                                speaker_instance_id,
#                                speaker_agent_dna)
#        → TurnAnchorResult(decision, payload, audit_emitted)
#    Reuses the same _reality_anchor_verify + _reality_anchor_opt_out
#    closures the dispatcher pipeline step uses (no duplication
#    of the pattern-match logic). CRITICAL → refuse + emit
#    reality_anchor_turn_refused. HIGH/MEDIUM/LOW → allow +
#    emit reality_anchor_turn_flagged. Failures degrade to
#    allow — anchor is NOT load-bearing for turn flow.
#
# 2. src/forest_soul_forge/daemon/routers/conversations.py
#    Imports the new helper. Inserts the check between
#    `response_text = result_output.get(...)` and
#    `registry.conversations.append_turn(...)`. On refuse:
#    skip the append, set any_failed=True to break the chain.
#    The audit event tells the operator why the turn didn't
#    show up.
#
# 3. src/forest_soul_forge/core/audit_chain.py
#    KNOWN_EVENT_TYPES += reality_anchor_turn_refused +
#    reality_anchor_turn_flagged. Distinct from T3's
#    reality_anchor_refused / reality_anchor_flagged so an
#    auditor can answer "what TURNS got blocked?" separately
#    from "what TOOL CALLS got blocked?" without parsing
#    event_data.
#
# 4. tests/unit/test_reality_anchor_turn.py (NEW)
#    11 tests covering:
#      - event types registered
#      - clean turn → allow, no event
#      - empty turn → allow, no event
#      - CRITICAL → refuse + reality_anchor_turn_refused emitted
#      - HIGH → allow + reality_anchor_turn_flagged emitted
#      - not_in_scope → allow silently
#      - opt-out skips the check even when CRITICAL
#      - missing constitution defaults to opt-in (gate fires)
#      - refuse payload carries fact_id + statement + matched_terms
#      - body_excerpt bounded to 500 chars in audit event
#
# 5. docs/decisions/ADR-0063-reality-anchor.md
#    Status: T1+T2+T3+T4+T5 shipped. T5 row marked DONE B254
#    with the full implementation detail. T6 + T7 still queued.
#
# Sandbox smoke (4 scenarios via standalone driver):
#   1. CRITICAL → decision=refuse, audit=turn_refused ✓
#   2. HIGH → decision=allow, audit=turn_flagged ✓
#   3. clean → decision=allow, audit=None ✓
#   4. opted-out + CRITICAL → decision=allow, audit=None ✓
#   Audit chain: 1 refused + 1 flagged event per matrix ✓
#
# Per ADR-0063 D1: same refuse-CRITICAL / warn-HIGH policy as T3.
# Per ADR-0063 D2: same per-agent constitutional opt-out.
# Per ADR-0063 D6: substrate ALWAYS runs (T3 + T5 together cover
#   both the tool-call surface AND the turn-emit surface). The
#   reality_anchor AGENT (T4) is still opt-in deep-pass.
# Per CLAUDE.md §0 Hippocratic gate: refusal limited to CRITICAL
#   (zero false-positive risk). HIGH stays warn-only.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/reality_anchor_turn.py \
        src/forest_soul_forge/daemon/routers/conversations.py \
        src/forest_soul_forge/core/audit_chain.py \
        tests/unit/test_reality_anchor_turn.py \
        docs/decisions/ADR-0063-reality-anchor.md \
        dev-tools/commit-bursts/commit-burst254-adr0063-t5-turn-hook.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(reality-anchor): ADR-0063 T5 pre-turn hook (B254)

Burst 254. B252 gates tool calls; B253 added the agent role.
B254 closes the third integration point: every assistant turn
gets cross-checked against operator ground truth BEFORE it
lands in the conversation log + reaches the operator.

New daemon/reality_anchor_turn.py helper consumed by
routers/conversations.py. Inserts between llm_think's
response_text and the registry.conversations.append_turn call.

Policy (ADR-0063 D1, same as T3):
- CRITICAL contradiction → REFUSE the turn (skip append,
  end chain, emit reality_anchor_turn_refused)
- HIGH/MEDIUM/LOW → ALLOW + emit reality_anchor_turn_flagged
- everything else → ALLOW silently

Per-agent constitutional opt-out (reality_anchor.enabled=false)
same as T3. Failures degrade to allow — anchor is NOT
load-bearing for turn flow.

Distinct event-type pair from T3:
- reality_anchor_turn_refused / reality_anchor_turn_flagged
  (conversation surface)
- vs T3's reality_anchor_refused / reality_anchor_flagged
  (dispatcher surface)

Lets an auditor answer 'what TURNS got blocked?' separately
from 'what TOOL CALLS got blocked?' without parsing event_data.

Tests: 11 cases covering every verdict, opt-out, missing
constitution, payload-shape, bounded body_excerpt.

ADR-0063 status: T1+T2+T3+T4+T5 shipped. T6 (correction memory
+ repeat-offender detection) + T7 (SoulUX Reality Anchor pane)
queued."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 254 complete ==="
echo "=== ADR-0063 T5 live. Pre-turn anchor active. ==="
echo "Press any key to close."
read -n 1
