#!/bin/bash
# Burst 393 - ADR-0081: substrate wiring coverage + wiring_sentinel
# (doc-only proposal).
#
# Drafts the structural answer to the B363/B392 gap class. Doc-only;
# T1-T6 implementation tranches follow after operator green-light,
# mirroring the ADR-0079 / 0080 / 0065 / 0066 cadence.
#
# What ADR-0081 specifies:
#
# 1. section-15-wiring-cross-check.command - new harness section
#    that asks cross-cutting questions the existing 14 sections
#    don't:
#      - Every cataloged tool: is it in any archetype kit OR
#        any genre_default OR any alive agent constitution?
#      - Every installed skill: do the archetypes that should
#        run it (per handoff yaml) carry all required tools?
#      - Every handoff (domain, capability): does the skill exist
#        + do alive agents in entry_agents carry the required
#        tools end-to-end?
#      - Cataloged-but-orphan tools (zero kits, zero consumers).
#    Reports a markdown table + operator-actionable punch list.
#
# 2. wiring-coverage.html - umbrella-rendered single page with
#    tool/skill/role/handoff/frontend tables. Each row drilldowns
#    to which entities reference / carry / require it. The
#    "everything works and is wired" single read.
#
# 3. wiring_sentinel role - guardian-genre singleton agent (like
#    reality_anchor / verifier_loop / forge). Signature skill
#    wiring_audit.v1: prior_audits -> verify_chain -> cross_check
#    -> summarize -> escalate (delegate to operator queue) ->
#    record. Constitution policies enforce:
#      forbid_substrate_mutation
#      forbid_silent_audit
#      require_chain_verify_before_audit
#
# 4. Scheduled cadence: existing daily harness (8am Pacific)
#    auto-picks-up section-15. NEW scheduled task
#    forest-soul-forge-wiring-audit runs the sentinel on a
#    4-hour cadence so substrate changes surface within a
#    quarter-day.
#
# Decisions of note (full set in the ADR):
#   D5 sentinel does NOT auto-fix gaps. Operator owns substrate
#      mutation; sentinel owns finding. Inverting this would
#      violate every other constitution-immutability invariant.
#   D7 4-hour cadence (not hourly noise, not daily lag). The
#      B363 gap surfaced because the operator opened the UI;
#      4h means automation catches it within a quarter-day.
#
# Tranche plan (~6 bursts to close):
#   T1 section-15 cross-check + tests
#   T2 wiring-coverage.html generator + umbrella integration
#   T3 wiring_sentinel role (full wiring mirror of B391)
#   T4 wiring_audit.v1 signature skill
#   T5 scheduled task + runbook addendum
#   T6 CLOSE - live verify + north-star update + status: Accepted
#
# Why doc-only first (matches ADR-0079/0080/0065/0066 cadence):
#   Six-tranche arc has enough surface (harness section + HTML
#   generator + role + skill + schedule + runbook) that the
#   architectural decisions deserve operator green-light before
#   code lands. Plus this ADR's whole purpose is to never ship a
#   narrow read again — drafting in full BEFORE implementing is
#   the discipline this ADR is meant to systematize.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT writing this ADR: B363/B392 gap class will
#     recur. Every new tool / role / skill / handoff has the
#     potential to ship one layer of wiring without the others,
#     and the existing 14 sections + Capability tab don't
#     cross-cut to catch it.
#   Prove non-load-bearing: doc only. No substrate or frontend
#     change.
#   Prove alternative is strictly better:
#     - Ad-hoc one-off check after each burst: relies on me
#       remembering. The B363 gap survived months before
#       surfacing.
#     - Make section-13 do everything: conflates frontend
#       checks with substrate-wide checks; section-13's mental
#       model is per-tab.
#     - Operator-driven manual check: that's what happened with
#       B363; doesn't scale.
#
# Verification after this commit lands:
#   1. Read docs/decisions/ADR-0081-substrate-wiring-coverage.md
#      end-to-end.
#   2. Operator green-lights, amends, or rejects.
#   3. T1 starts as a separate burst once green-lit.
#
# What this UNBLOCKS / CLOSES:
#   ADR-0081 is the structural answer to operator's request for
#   a "page that shows everything working and wired correctly"
#   + the "special agent doing real work monitoring the project"
#   + the "schedule for it." Once tranches T1-T6 ship, the
#   B363-class gap surfaces automatically within 4 hours of
#   introduction.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0081-substrate-wiring-coverage.md \
        dev-tools/commit-bursts/commit-burst393-adr0081-wiring-coverage-proposal.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0081 substrate wiring coverage proposal (B393)

Burst 393. Structural answer to the B363/B392 gap class.
Doc-only; T1-T6 implementation follows green-light.

What ADR-0081 specifies:
  section-15-wiring-cross-check: new harness section asking
    cross-cutting questions (every cataloged tool: in any
    archetype kit OR genre_default OR constitution? every
    skill: do archetypes that should run it carry required
    tools? every handoff: end-to-end resolvable?). Reports
    markdown table + operator-actionable punch list.
  wiring-coverage.html: umbrella-rendered single page with
    tool/skill/role/handoff/frontend tables + drilldowns.
  wiring_sentinel role (guardian, singleton): runs the audit
    skill on a 4-hour schedule, escalates medium+ gaps via
    delegate to operator queue. Constitution policies forbid
    substrate mutation + silent audit; require chain verify.
  Schedule: existing daily harness auto-picks-up section-15;
    NEW forest-soul-forge-wiring-audit task runs sentinel at
    4-hour cadence so substrate changes surface within a
    quarter-day.

Tranche plan (~6 bursts): section-15 -> coverage.html ->
sentinel role -> audit skill -> scheduled task + runbook ->
CLOSE.

Decisions:
  D5 - sentinel does NOT auto-fix. Operator owns substrate
       mutation; sentinel owns finding. Auto-fix would invert
       every constitution-immutability invariant.
  D7 - 4-hour cadence (not hourly noise, not daily lag).

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: B363/B392 gap class will recur without the
    sentinel + cross-check.
  Prove non-load-bearing: doc only.
  Prove alternative is better: ad-hoc post-burst checks rely
    on memory; operator-driven manual checks don't scale.

This ADR's whole purpose is to never ship a narrow read again
(feedback_complete_over_narrow). Drafting in full BEFORE
implementing IS the discipline this ADR systematizes.

After this lands: operator reviews + green-lights T1."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 393 complete - ADR-0081 proposed ==="
echo "=========================================================="
echo "Review: docs/decisions/ADR-0081-substrate-wiring-coverage.md"
echo "Green-light opens T1 (section-15 cross-check)."
echo ""
echo "Press any key to close."
read -n 1 || true
