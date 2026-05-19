#!/usr/bin/env bash
# Burst 422 — script consolidation pass (ChatGPT critique + ADR-0044 boundary).
#
# Motivation
# ----------
# ChatGPT external assessment (received 2026-05-19) identified script
# sprawl as the hardest concrete finding: "dozens of .command files
# = ad hoc operational workflows, manual glue logic, environment
# fragility, hidden dependencies." Verification showed 64 .command
# files at repo root (ChatGPT had said "dozens"; reality was worse).
#
# ADR-0044's kernel/userspace boundary doc already classifies
# repo-root *.command scripts as "userspace (operator)" — meaning
# they should be operator-daily entry points, not a junk drawer of
# historical one-shots. This burst operationalizes that doc by
# moving the one-shot historicals into proper homes under dev-tools/.
#
# Hippocratic gate (CLAUDE.md §0)
# -------------------------------
# 1. Prove harm: ChatGPT flagged this AND it contradicts ADR-0044's
#    explicit boundary classification. STATE.md's "68 .command at repo
#    root" claim from B233 ballooned to 64 today — drift in the wrong
#    direction. Operator can't tell daily entry points from per-burst
#    historicals at a glance. New contributors face 64 unsorted scripts.
# 2. Prove non-load-bearing: every moved file is either a per-burst
#    one-shot (verify-b143, diag-b264-tests, a5-finalize), a historical
#    live-test (live-test-y-full from Y-track 2026-04-30), or a debug
#    helper (sw-debug, dump-err-log). None are operator-daily. Two
#    external references found + updated (docs/runbooks/conversation-
#    runtime.md and a5-finalize's own exec call). All 38 moves preserve
#    git history via `git mv`.
# 3. Prove alternative is strictly better: could "leave it"; rejected
#    because ChatGPT will keep saying it (and so will any other
#    external integrator). Could "consolidate everything" — rejected
#    because operator entry points (start/stop/run/reset/push) need to
#    stay at root for the daily Finder type-jump workflow.
#
# Layout after this burst
# -----------------------
#   repo root          26 files (was 64) — operator-daily entry points
#                                          + Ollama mgmt + bring-up scripts
#                                          + paired launchagents lifecycle
#   dev-tools/
#     verify-archive/  12 files — per-burst verify/diag/fix one-shots
#                                  + a5-finalize (with path-fix patch)
#     live-tests/      16 files — live-test-*, live-fire-voice, live-triune-*
#     commit-bursts/   +1 file  — commit-burst128-command-archival
#                                  (was orphaned at repo root)
#     (top-level)      +9 files — birth-dashboard-watcher,
#                                  birth-specialist-stable, cleanup-bak,
#                                  close-stale-terminals, dump-err-log,
#                                  quit-bg-apps, sw-debug, track-sarahr1,
#                                  tune-priority
#
# Why this is part of a larger arc (B422-B424)
# --------------------------------------------
# B422 (this burst): script consolidation — visible "we heard you" signal.
# B423: refresh STATE.md + KERNEL.md (162 bursts of drift since B258).
# B424: ADR-0082 Kernel Freeze Posture — convert "uncontrolled ambition"
#       into "bounded ambition with explicit freeze posture." The
#       kernel/userspace boundary becomes the feature, not just the doc.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 422 — script consolidation pass"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo
echo "Staging:"
echo "  - 38 git-mv renames already staged by the move script"
echo "  - reference-update edits to:"
echo "      docs/runbooks/conversation-runtime.md"
echo "      dev-tools/verify-archive/a5-finalize.command (path-fix patch)"
echo

# Stage the two reference-update files explicitly.
# The 38 renames are already staged by git-mv. Modifications to other
# files (rebirth-reviewer-main.command, close-session-stale-terminals.
# command, examples/audit_chain.jsonl) are intentionally NOT staged
# here — they belong to other bursts.
git add docs/runbooks/conversation-runtime.md
git add dev-tools/verify-archive/a5-finalize.command
git add dev-tools/commit-bursts/commit-burst422-script-consolidation.command

echo "Pre-commit status:"
git status -s | head -50
echo

git commit -m "chore(repo): script consolidation pass (B422)

Move 38 historical/one-shot .command files from repo root into proper
homes under dev-tools/. Repo root .command count: 64 -> 26.

Motivation: ChatGPT external assessment (2026-05-19) called out script
sprawl as the hardest concrete finding. Verification showed 64 .command
files at root vs. ADR-0044 boundary doc's intent that repo-root scripts
be operator-daily entry points, not a junk drawer.

Moves (all via git mv, history preserved):

  dev-tools/verify-archive/  (NEW; 12 files)
    verify-b143, verify-b144, verify-b148, verify-burst86-scheduler,
    verify-end-to-end, verify-t22-scheduler-post-b143, diag-b264-tests,
    diagnose-chat, diagnose-import, fix-bug1-restart-and-reset,
    fix-cryptography-dep, a5-finalize

  dev-tools/live-tests/  (NEW; 16 files)
    live-test-d3-phase-a, live-test-fizzbuzz, live-test-g6-k5,
    live-test-k4, live-test-k6, live-test-r-rebuild, live-test-r2,
    live-test-r4, live-test-sw-coding-tools, live-test-sw-coding-triune,
    live-test-t2-tier, live-test-y-full, live-test-y2-conversation,
    live-test-y3-multi-agent, live-fire-voice, live-triune-file-adr-0034

  dev-tools/  (9 added top-level)
    birth-dashboard-watcher, birth-specialist-stable, cleanup-bak-files,
    close-stale-terminals, dump-err-log, quit-bg-apps, sw-debug,
    track-sarahr1-script, tune-priority

  dev-tools/commit-bursts/
    commit-burst128-command-archival (was orphan at repo root)

External references updated:
  docs/runbooks/conversation-runtime.md
    './live-test-y-full.command' -> './dev-tools/live-tests/...'
  dev-tools/verify-archive/a5-finalize.command
    cd \"\$HERE\" -> cd \"\$HERE/../..\" (reorient to repo root)
    './live-test-sw-coding-tools.command' -> './dev-tools/live-tests/...'

What stays at root (26 files): start/stop/run/reset/push/run-tests
+ run-tests-direct + start-demo + start-full-stack + clean-git-locks;
docker-up + frontend-rebuild + stack-rebuild; ollama-up + ollama-coder-up
+ ollama-status + kill-ollama + restart-ollama-pin; swarm-bringup;
install-launchagents + uninstall-launchagents (paired) +
activate-scheduled-tasks; t4-tests; open-in-chrome; soak;
web-research-demo.

Hippocratic gate (CLAUDE.md §0):
  Prove harm: 64 unsorted scripts contradicts ADR-0044 boundary doc;
    new contributor + ChatGPT both got it wrong about repo's discipline.
  Prove non-load-bearing: all moves are one-shots or per-burst
    historicals; two external refs found + updated; no in-source
    Python/script imports of these paths.
  Prove alternative is strictly better: leaving = ChatGPT-class
    critique keeps landing. Total consolidation rejected because
    entry-point scripts must stay at root for Finder type-jump
    workflow.

Part of B422-B424 arc adapting to ChatGPT feedback:
  B422 (this): consolidation pass.
  B423: refresh STATE.md + KERNEL.md (162 bursts of drift since B258).
  B424: ADR-0082 Kernel Freeze Posture (convert ambition -> bounded
        ambition with explicit freeze posture)." || { echo "commit failed"; exit 1; }

echo
echo "==========================================================="
echo "Pushing to origin..."
echo "==========================================================="
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. Verify with:"
echo "  ls *.command | wc -l   # should be 26"
echo "  ls dev-tools/verify-archive/ dev-tools/live-tests/"
echo
echo "Press any key to close."
read -n 1 || true
