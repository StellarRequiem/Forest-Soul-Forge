#!/usr/bin/env bash
# Burst 437 — loose-ends sweep after the Tier 1 hardening session.
#
# Bundles four small changes that close items from the 2026-05-20
# audit:
#
#   1. CLAUDE.md sec5 — Per-repo .git/config overrides silently beat
#      global. The B435 unsigned-race lesson, written in the same
#      sec1-sec4 structure. Codifies the discipline that any
#      "global config change" claim must be verified against the
#      repo-local file before declaring it live.
#
#   2. config/tool_catalog.yaml — wire 2 of 3 long-standing orphan
#      tools (the deterministic-safe ones; the third stays deferred
#      pending operator disposition):
#        personal_recall.v1   -> archetypes.assistant.standard_tools
#                                (ADR-0076 T4; genre-gated to
#                                companion/assistant/operator_steward/
#                                domain_orchestrator. assistant is the
#                                first of the four to have a kit.)
#        security_scan.v1     -> archetypes.wiring_sentinel.standard_tools
#                                (ADR-0062 supply-chain IoC scanner.
#                                Read-only sibling to git_local_scan.v1
#                                which we wired here in P3 this session.)
#      operator_profile_write.v1 stays orphan — see audit doc for the
#      decision-pending rationale.
#
#   3. dev-tools/close-session-stale-terminals.command — housekeeping
#      modification has been in the working tree since the 2026-05-18
#      ADR-0081 session: 13 new stale-window patterns + the
#      force-restart-daemon keep-id guard. Pure additive maintenance.
#
#   4. docs/audits/2026-05-20-orphan-tool-disposition.md — new audit
#      doc capturing the orphan-tool triage decision so future
#      sessions don't re-derive the analysis. References the four
#      load-bearing ADRs.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm:
#     personal_recall + security_scan have been cataloged-but-unreachable
#     since their respective ADR tranches landed. No agent could call
#     them. Section-15 has flagged them as FAIL on every diagnostic-all
#     run for sessions. Each is a substrate-grade tool whose
#     unreachability is a latent capability gap.
#   Prove non-load-bearing for kernel:
#     Catalog data + docs. No schema, no event types, no HTTP routes.
#     Pure userspace per ADR-0044 + ADR-0082.
#   Prove alternative:
#     Retire the tools (rejected — substrate is sound and ADR-cited).
#     Wait for the right archetype kit to exist (rejected for
#     personal_recall + security_scan because assistant and
#     wiring_sentinel kits already exist and the gate lists match;
#     accepted for operator_profile_write because the operator-truth
#     write surface is non-trivial).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 437 — loose-ends sweep"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

echo "Signing config sanity-check (resolved, should show ssh + true):"
echo "  gpg.format        = $(git config --get gpg.format)"
echo "  user.signingkey   = $(git config --get user.signingkey)"
echo "  commit.gpgsign    = $(git config --get commit.gpgsign)"
echo "  gpg.ssh.allowedSignersFile = $(git config --get gpg.ssh.allowedSignersFile)"
echo

git add CLAUDE.md
git add config/tool_catalog.yaml
git add dev-tools/close-session-stale-terminals.command
git add docs/audits/2026-05-20-orphan-tool-disposition.md
git add dev-tools/commit-bursts/commit-burst437-loose-ends-sweep.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "chore(governance): loose-ends sweep — orphan tools + CLAUDE.md sec5 + housekeeping (B437)

Closes four items from the 2026-05-20 post-B436 loose-ends audit:

(1) CLAUDE.md sec5 — Per-repo .git/config overrides silently beat
    global. Codifies the B435 unsigned-race lesson in the same
    sec1-sec4 structure. Rule body, why with the specific B435
    example, how-to-apply with the verification recipe.

(2) config/tool_catalog.yaml — wire 2 of 3 long-standing orphan
    tools to natural-fit archetype kits:
      personal_recall.v1   -> assistant.standard_tools
        (ADR-0076 T4; genre-gated to companion/assistant/
        operator_steward/domain_orchestrator; assistant is the
        first of the four to have a kit in catalog.)
      security_scan.v1     -> wiring_sentinel.standard_tools
        (ADR-0062 IoC scanner; read-only sibling to
        git_local_scan.v1 wired in P3 this session; same
        guardian-genre scheduled-task pattern.)
    operator_profile_write.v1 deferred to operator decision;
    see docs/audits/2026-05-20-orphan-tool-disposition.md for
    the wire-or-retire rationale.

(3) dev-tools/close-session-stale-terminals.command — 13 new
    stale-window patterns from the 2026-05-18 ADR-0081 session
    plus the force-restart-daemon keep-id guard. Housekeeping
    that has been sitting in the working tree since then.

(4) docs/audits/2026-05-20-orphan-tool-disposition.md — new
    audit doc capturing the orphan-tool triage so future
    sessions don't re-derive the analysis. References ADR-0062,
    ADR-0068, ADR-0076, ADR-0081, ADR-0084.

Expected after this lands:
  * Section-15 orphan count narrows 3 -> 1 (only the deferred
    operator_profile_write.v1 remains FAIL; personal_recall +
    security_scan move to the 'in kit but no alive agent yet'
    INFO bucket).
  * Second signed commit on the repo (B436 was the first; chain
    is now signed-since-B436).

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: 2 cataloged-but-unreachable substrate tools;
    housekeeping drift sitting uncommitted; folklore unwritten
    after the B435 race surfaced it.
  Prove non-load-bearing: catalog data + scripts + docs.
    No schema, no event types, no HTTP routes.
  Prove alternative: retire tools (rejected; substrate sound);
    defer all three (rejected; assistant + wiring_sentinel kits
    already exist with matching genre ceilings)." || { echo "commit failed"; exit 1; }

echo
echo "==========================================================="
echo "Post-commit signature status:"
echo "==========================================================="
git log --format='%h %G? %s' -4
echo
echo "Expected: top SHA (B437) shows G; B436 shows G; B435 shows N."
echo

echo "Pushing B437 to origin..."
git push origin main || { echo "push failed — capture the remote rejection above"; exit 1; }

echo
echo "Done. B437 pushed."
echo
echo "Press any key to close."
read -n 1 || true
