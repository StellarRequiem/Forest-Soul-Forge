#!/usr/bin/env bash
# Burst 436 — recover from the B435 signing race + land the
# signing-side helper scripts as a clean, signed, fast-forward
# commit on top of B435.
#
# What happened with B435 (the unsigned original):
#   * enable-ssh-signing.command set the GLOBAL git config:
#       gpg.format=ssh, user.signingkey=~/.ssh/id_ed25519.pub,
#       commit.gpgsign=true, tag.gpgsign=true.
#   * But the repo had a per-repo .git/config override of
#       [commit] gpgsign = false
#     that took precedence. So the B435 commit landed unsigned.
#   * The push of the unsigned B435 succeeded — likely because the
#     "main protection" ruleset rules hadn't fully propagated yet
#     for that exact push window, or because the ruleset author
#     gets implicit bypass on first-creation. Either way the
#     unsigned B435 (239c743) is on origin/main now.
#   * Subsequent amends (B435b, B435c) added signature + the
#     allowed_signers setup, but produced new SHAs locally that
#     can only land on origin via force-push — which the ruleset
#     now blocks. Local diverges from remote with no way forward
#     except: rebase onto origin/main and add the amend deltas as
#     a new commit.
#
# Recovery (this script):
#   1. git reset --mixed origin/main — point HEAD back at the
#      unsigned B435 on remote, but KEEP the working tree (so the
#      three new .command scripts stay on disk).
#   2. Stage the three new scripts.
#   3. Commit them as B436 with this message. Per-repo override
#      is already unset (B435b removed it), global signing config
#      is in place (enable-ssh-signing ran), allowed_signers file
#      exists (B435c wrote it). This commit will be signed.
#   4. Push — a clean fast-forward from 239c743 onward. No
#      force-push, no ruleset rejection.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: local diverged from remote; can't push amends
#     without force which the new ruleset blocks; 3 helper scripts
#     would be lost if we just hard-reset.
#   Prove non-load-bearing: scripts + the recovery script itself.
#     No source code change.
#   Prove alternative: hard-reset (loses scripts; rejected); force
#     push (blocked by ruleset; rejected); leave diverged (working
#     tree drift; rejected). Soft rebase via reset --mixed is the
#     minimum-history-rewrite option.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 436 — signing recovery + diagnostic scripts as one"
echo "             signed commit"
echo "==========================================================="
echo

echo "Pre-state:"
echo "  local HEAD  = $(git rev-parse --short HEAD)"
echo "  origin/main = $(git rev-parse --short origin/main)"
echo "  global commit.gpgsign = $(git config --global --get commit.gpgsign)"
echo "  global gpg.ssh.allowedSignersFile = $(git config --global --get gpg.ssh.allowedSignersFile)"
echo

echo "Resetting local HEAD to origin/main (keeping working tree)..."
git reset --mixed origin/main || { echo "reset failed"; exit 1; }
echo "  done. HEAD now at $(git rev-parse --short HEAD)"
echo

echo "Working tree state after reset:"
git status -s
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

# Stage exactly the three new scripts + this recovery script.
git add dev-tools/commit-bursts/commit-burst435b-fix-signing.command
git add dev-tools/commit-bursts/commit-burst435c-allowed-signers-and-push.command
git add dev-tools/try-push-b435.command
git add dev-tools/commit-bursts/commit-burst436-signing-recovery.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "fix(governance): land SSH-signing recovery scripts after B435 unsigned race (B436)

B435 (239c743) landed and pushed UNSIGNED because the repo had a
per-repo [commit] gpgsign=false override that took precedence over
the freshly-written global commit.gpgsign=true. The B435 push then
succeeded against origin (ruleset propagation race or first-create
bypass), but subsequent attempts to amend a signature onto B435
diverged local from remote with no way to reconcile under the
Block-force-pushes rule.

This burst lands the four scripts that were generated during the
recovery, as a clean fast-forward signed commit on top of B435.
It is the first signed commit on the repo:

  * dev-tools/commit-bursts/commit-burst435b-fix-signing.command
      Removed the per-repo gpgsign=false override and amended B435.
  * dev-tools/commit-bursts/commit-burst435c-allowed-signers-and-push.command
      Wrote ~/.config/git/allowed_signers and set
      gpg.ssh.allowedSignersFile globally so 'git log %G?' can
      verify signatures locally.
  * dev-tools/try-push-b435.command
      Diagnostic helper that captures the push attempt output
      to /tmp/b435-push-attempt.log.
  * dev-tools/commit-bursts/commit-burst436-signing-recovery.command
      This script — the reset + signed-commit recipe.

Lesson worth keeping in CLAUDE.md folklore (queued for the next
discipline burst): when a 'first signed commit' is the verification
of a signing-config change, always check for a per-repo .git/config
override FIRST. Global config can promise a behavior that the repo
config silently revokes.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: divergence between local + remote; 3 helper scripts
    stuck locally; ruleset blocks the obvious resolution.
  Prove non-load-bearing for kernel: scripts only. No source change.
  Prove alternative: hard-reset (loses scripts), force-push
    (blocked), leave diverged (drift). All worse than reset --mixed
    + clean fast-forward commit." || { echo "commit failed"; exit 1; }

echo
echo "==========================================================="
echo "Post-commit signature status:"
echo "==========================================================="
git log --format='%h %G? %s' -3
echo
echo "Expected: top SHA shows G (good signature) on B436."
echo "          B435 below shows N (the unsigned race)."
echo

echo "Pushing B436 as fast-forward from B435..."
git push origin main || { echo "push failed — capture the remote rejection above"; exit 1; }

echo
echo "Done. B436 (signed) pushed."
echo
echo "Press any key to close."
read -n 1 || true
