#!/usr/bin/env bash
# Burst 438 — operator_companion archetype kit + ADR status sweep
# + operator_profile_write disposition resolved.
#
# Closes three of the remaining loose ends from the 2026-05-20
# audit:
#
#   1. config/tool_catalog.yaml — adds the operator_companion
#      archetype kit. This is the role that owns operator-truth
#      mutation (operator_profile_write.v1 lives here). The kit
#      shape follows the companion-genre ceiling (max_side_effects
#      =network, provider_constraint=local_only, memory_ceiling=
#      private) and includes the canonical operator-companion
#      surface: memory + llm_think + personal_recall +
#      operator_profile_read/write + delegate + timestamp +
#      summarize. Existing live operator_companion agent keeps its
#      current constitution-direct grants per ADR-0044; the new
#      kit applies to future births.
#
#   2. ADR status-header sweep — five ADRs labeled Proposed in
#      their header but actually CLOSED per north-star + harness:
#        ADR-0050 (encryption-at-rest) — Phase α 8/8 closed
#        ADR-0064 (telemetry pipeline) — D3 Phase B 6/6 closed
#        ADR-0077 (D4 advanced rollout) — D4 6/6 closed
#        ADR-0078 (D3 advanced rollout) — Phase A closed
#        ADR-0079 (diagnostic harness) — 7/7 + umbrella closed
#      Each header rewritten to "Accepted (date, burst-range)"
#      with a one-line implementation summary. Documentation drift
#      that has been accumulating across sessions.
#
#   3. docs/audits/2026-05-20-orphan-tool-disposition.md —
#      updated to mark operator_profile_write.v1 as RESOLVED via
#      the new operator_companion kit. Section-15 should report
#      orphan_count=0 after this lands.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm:
#     orphan_count stays at 1 in the harness, keeping a daily-FAIL
#     signal that masks any future regressions in the same surface.
#     ADR drift accumulates and erodes audit-grade trust in "this
#     ADR is finished" claims.
#   Prove non-load-bearing for kernel:
#     Catalog data + docs. No schema, no event types, no HTTP
#     routes.
#   Prove alternative:
#     Wider availability (append profile_write to assistant kit)
#     was rejected — conflates assistant with operator-companion.
#     Continued deferral was rejected — operator has decided.
#     Documentation-only ADR sweep (no implementation change) is
#     the right granularity for the status drift.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 438 — operator_companion kit + ADR status sweep"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add config/tool_catalog.yaml
git add docs/decisions/ADR-0050-encryption-at-rest.md
git add docs/decisions/ADR-0064-telemetry-pipeline.md
git add docs/decisions/ADR-0077-d4-code-review-advanced-rollout.md
git add docs/decisions/ADR-0078-d3-local-soc-advanced-rollout.md
git add docs/decisions/ADR-0079-diagnostic-harness.md
git add docs/audits/2026-05-20-orphan-tool-disposition.md
git add dev-tools/commit-bursts/commit-burst438-operator-companion-kit-and-adr-sweep.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "feat(governance): operator_companion archetype kit + ADR status sweep + orphan-tool closure (B438)

Closes three loose-ends items from the 2026-05-20 audit:

(1) config/tool_catalog.yaml — adds the operator_companion
    archetype kit. Resolves operator_profile_write.v1 from
    orphan to wired. Kit shape (companion-genre, side_effects up
    to network):
      llm_think.v1 + memory_recall.v1 + memory_write.v1 +
      timestamp_window.v1 + text_summarize.v1 + personal_recall.v1
      + operator_profile_read.v1 + operator_profile_write.v1 +
      delegate.v1
    operator_profile_write's requires_human_approval=True gate
    provides per-call safety regardless of kit placement; the
    kit decision is about which role owns operator-truth write.
    Existing live operator_companion agent keeps its current
    constitution-direct tool grants per ADR-0044 layered-config
    semantics; new kit applies to future births.

(2) ADR status-header sweep — 5 ADRs corrected from Proposed to
    Accepted with implementation summary:
      ADR-0050 encryption-at-rest    Phase alpha 8/8 (B281-B330, 2026-05-15)
      ADR-0064 telemetry pipeline    D3 Phase B 6/6 (B348-B385, 2026-05-17)
      ADR-0077 D4 advanced rollout   D4 closed 6/6 (B331-B340, 2026-05-16)
      ADR-0078 D3 advanced rollout   Phase A closed (B342-B347, 2026-05-17)
      ADR-0079 diagnostic harness    7/7 + umbrella (B351-B357, 2026-05-17)
    Docs-only sweep; no implementation change. Reduces 'is this
    ADR finished?' lookup cost for future sessions.

(3) docs/audits/2026-05-20-orphan-tool-disposition.md — updated
    to mark operator_profile_write.v1 RESOLVED. Section-15
    orphan count goes 1 -> 0; ADR-0081 wiring-coverage sentinel
    can stop flagging the surface for daily-report noise.

Expected after this lands (next diagnostic-all):
  * Section-15 reports orphan_count=0 (was 1 in last run).
  * Section-04 reports registered=68 + forged=1 = 69; no change.
  * All 15 sections PASS.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 1 substrate-grade tool unreachable; ADR status
    drift erodes audit-grade trust; both have been in the queue
    multiple sessions.
  Prove non-load-bearing: catalog data + docs. No schema, no
    events, no routes.
  Prove alternative: assistant kit conflates roles (rejected);
    continued deferral rejected by operator decision; docs-only
    sweep is the right granularity for status drift." || { echo "commit failed"; exit 1; }

echo
echo "==========================================================="
echo "Post-commit signature status:"
echo "==========================================================="
git log --format='%h %G? %s' -4
echo

echo "Pushing B438..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B438 pushed."
echo
echo "Press any key to close."
read -n 1 || true
