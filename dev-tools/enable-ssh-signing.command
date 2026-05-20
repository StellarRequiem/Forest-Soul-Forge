#!/usr/bin/env bash
# B6 — enable SSH-based commit signing locally.
#
# Pre-reqs (already satisfied this session):
#   - ~/.ssh/id_ed25519(.pub) exists.
#   - The .pub key is uploaded to GitHub as a SIGNING KEY (A4).
#   - The "main protection" ruleset enforces Require signed commits (A5).
#
# After this script: every commit (and tag) gets signed with the
# ed25519 key. `git log --show-signature` and `git log --format='%G?'`
# will show 'G' (good signature) on commits made after this point.
#
# Non-destructive: this is pure `git config --global` writes + a
# verification read-back. No commits are created, no history is
# touched. Existing unsigned commits (everything up through B434)
# remain unsigned — only NEW commits get the signature treatment.

set -uo pipefail

echo "=========================================================="
echo "B6 — enable SSH-based commit signing"
echo "=========================================================="
echo

KEY_PUB="$HOME/.ssh/id_ed25519.pub"

if [ ! -f "$KEY_PUB" ]; then
  echo "ERROR: $KEY_PUB not found. Aborting."
  exit 1
fi

echo "Using public key: $KEY_PUB"
echo "  $(cat "$KEY_PUB")"
echo

echo "=== BEFORE (current signing config) ==="
echo "  gpg.format        = $(git config --global --get gpg.format 2>/dev/null || echo '(unset)')"
echo "  user.signingkey   = $(git config --global --get user.signingkey 2>/dev/null || echo '(unset)')"
echo "  commit.gpgsign    = $(git config --global --get commit.gpgsign 2>/dev/null || echo '(unset)')"
echo "  tag.gpgsign       = $(git config --global --get tag.gpgsign 2>/dev/null || echo '(unset)')"
echo

echo "=== Applying ==="
git config --global gpg.format ssh
git config --global user.signingkey "$KEY_PUB"
git config --global commit.gpgsign true
git config --global tag.gpgsign true
echo "  done."
echo

echo "=== AFTER ==="
echo "  gpg.format        = $(git config --global --get gpg.format)"
echo "  user.signingkey   = $(git config --global --get user.signingkey)"
echo "  commit.gpgsign    = $(git config --global --get commit.gpgsign)"
echo "  tag.gpgsign       = $(git config --global --get tag.gpgsign)"
echo

# Smoke: verify the key is loadable for signing. `git` shells out to
# `ssh-keygen -Y sign` under the hood; if the key needs a passphrase
# and isn't in the agent, future commits will prompt — fine, but
# worth knowing now.
echo "=== Signing smoke (sign a tiny payload to /tmp) ==="
KEY_PRIV="${KEY_PUB%.pub}"
if echo "test" | ssh-keygen -Y sign -n git -f "$KEY_PRIV" > /tmp/_fsf_sign_test.sig 2>&1; then
  echo "  PASS — key signs without prompt; ssh-agent is happy."
  rm -f /tmp/_fsf_sign_test.sig
else
  echo "  WARN — signing prompted or errored. Likely the key needs"
  echo "         a passphrase that isn't loaded into ssh-agent."
  echo "         Fix: run 'ssh-add --apple-use-keychain $KEY_PRIV'"
  echo "         once; macOS Keychain will then unlock it for you."
fi
echo

# Next-commit verification helper: print the command the operator
# can run AFTER their next commit to confirm 'G' appears.
echo "=== Verify after your next commit ==="
echo "  Run: git log --format='%h %G? %s' -3"
echo "  Expected: latest commit shows 'G' (good) instead of 'N' (none)."
echo
echo "=========================================================="
echo "Done. Close this window when finished."
echo "=========================================================="
