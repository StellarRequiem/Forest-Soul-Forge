#!/usr/bin/env bash
# Burst 423 — STATE.md + KERNEL.md drift refresh (B258 → B420 reconcile).
#
# Motivation
# ----------
# STATE.md last updated B258 (2026-05-13). HEAD is B420 today
# (2026-05-19) — 162 bursts of drift. Every headline number is wrong:
#
#   Surface              STATE.md claim    Disk reality
#   ----------------     --------------    ----------------
#   Commits              389               575    (+186)
#   Python LoC           59,602            87,188 (+27,586 / +46%)
#   ADRs filed           57                78     (+21)
#   Schema version       v20               v23    (+3)
#   Builtin tools        54                69     (+15)
#   Skill manifests      36                46/38  (+10/+2)
#   Audit chain entries  ~8,870            19,211 (+10,341)
#   Repo-root .command   68                26     (-42 post-B422)
#   Total agents         23 active         38 total / 34 active
#
# This drift creates two harms:
#   (a) STATE.md is the doc external integrators read; it lies.
#   (b) The drift hides actual progress — Phase α 10/10 substrate
#       closed, D4 rolled out, D3 Phase A+B closed, ADR-0079/0080/0081
#       all closed. The doc undersells the project to anyone reading.
#
# This burst inserts a current-state snapshot at the TOP of STATE.md
# without rewriting the 540-line body. The body still describes the
# B258 baseline; the snapshot at the top reflects today's truth. A
# subsequent refresh burst will reconcile the body if needed.
#
# KERNEL.md gets three surgical updates:
#   - Status section gains the v0.6 substrate buildout (Phase α 10/10).
#   - 70+ event-type schemas → 80+ (160+ string occurrences).
#   - Schema v15 → v23.
#
# Hippocratic gate (CLAUDE.md §0)
# -------------------------------
# 1. Prove harm: STATE.md drift was a documented concern from the
#    ChatGPT external assessment received 2026-05-19. The doc's
#    "v0.5/57 ADRs/23 agents" framing actively misrepresents the
#    current state to any reader. Also feedback memory's
#    [feedback_north_star_update_after_milestones] says to update
#    after every milestone — Phase α close + 3 closed ADRs in B420
#    were milestones.
# 2. Prove non-load-bearing: this is a docs-only update. Snapshot
#    added at the TOP of STATE.md preserves the existing B258 body
#    verbatim. KERNEL.md edits are targeted; the 7 ABI surfaces and
#    their canonical-location table are unchanged.
# 3. Prove alternative: could do a full STATE.md rewrite. Rejected
#    because (a) bigger diff = more review surface, (b) the existing
#    body contains useful B258-era context we don't want to lose, and
#    (c) inserting a fresh snapshot at the top is the same pattern
#    the CLAUDE.md "north star update protocol" describes.
#
# Part of B422-B424 arc adapting to ChatGPT feedback.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 423 — STATE.md + KERNEL.md refresh"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo

git add STATE.md KERNEL.md
git add dev-tools/commit-bursts/commit-burst423-state-kernel-refresh.command

echo "Pre-commit status:"
git status -s | head -20
echo

git commit -m "docs(state-kernel): refresh after B258 -> B420 drift (B423)

STATE.md last updated B258 (2026-05-13); HEAD is B420 today.
162 bursts of drift. Every headline number is wrong.

Drift snapshot:
  Commits              389  ->  575    (+186)
  Python LoC           59,602  ->  87,188 (+27,586 / +46%)
  ADRs filed           57   ->  78     (+21)
  Schema version       v20  ->  v23    (+3)
  Builtin tools        54   ->  69     (+15)
  Skill manifests      36   ->  46/38  (+10 examples / +2 installed)
  Audit chain entries  8,870  ->  19,211 (+10,341)
  Repo-root .command   68   ->  26     (-42 post-B422)
  Total agents         23 active  ->  38 total / 34 active

STATE.md: inserted current-state snapshot at the top covering the
Phase alpha substrate close (10/10 ADRs 0050+0067-0076), D4 Code
Review rollout (ADR-0077), D3 Local SOC Phase A+B (ADR-0078, 0064),
and the tooling-discipline closures (ADR-0079 diagnostic harness,
ADR-0080 capability tree, ADR-0081 wiring coverage). Preserves the
B258 baseline body unchanged so existing context survives.

KERNEL.md: three surgical updates to the Status section + event-type
count + schema version. Notes the kernel is functionally frozen as of
Phase alpha close — substrate additions now require external-
integrator demand per ADR-0082 (the explicit freeze posture coming
in B424).

Motivation: ChatGPT external assessment (2026-05-19) flagged the
'ambition exceeds proof' risk; honest accounting of current scope
+ closure status is the first move in adapting to that critique.
Refresh-protocol memory says north-star update after every
milestone -- Phase alpha close + D4 rollout + 3 tooling-discipline
ADRs were milestones we hadn't reflected.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: STATE.md misrepresents project to external readers.
  Prove non-load-bearing: docs-only; B258 body preserved verbatim.
  Prove alternative: full rewrite rejected (bigger surface, loses
    B258 context, and CLAUDE.md north-star protocol prescribes
    snapshot-at-top pattern).

Part of B422-B424 arc adapting to ChatGPT feedback:
  B422: script consolidation pass (64 -> 26 repo-root .command).
  B423 (this): STATE.md + KERNEL.md refresh.
  B424: ADR-0082 Kernel Freeze Posture (codify kernel-add
        discipline as the explicit feature)." || { echo "commit failed"; exit 1; }

echo
echo "Pushing to origin..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. Verify with:"
echo "  head -50 STATE.md"
echo "  head -50 KERNEL.md"
echo
echo "Press any key to close."
read -n 1 || true
