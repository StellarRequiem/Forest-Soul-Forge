#!/usr/bin/env bash
# Burst 435c — set up allowedSignersFile so local SSH signature
# verification works, then push the signed B435 to origin.
#
# Bug surfaced: after the 435b amend signed B435 (raw object has
# the gpgsig SSH SIGNATURE block), `git log %G?` still shows N
# instead of G because:
#   error: gpg.ssh.allowedSignersFile needs to be configured and
#          exist for ssh signature verification
#
# That config tells git which (email -> SSH key) mappings count
# as trusted signers when verifying. Without it, git can't decide
# whether a signature is valid even though it exists. This is a
# verification-side concern, not a signing-side concern — the
# signature itself is fine and GitHub will verify it against the
# Signing Key we uploaded.
#
# Fix:
#   1. Write ~/.config/git/allowed_signers with one line mapping
#      our email to the ed25519 public key.
#   2. git config --global gpg.ssh.allowedSignersFile that path.
#   3. Re-check HEAD's %G? — expect G now.
#   4. git push origin main — let the remote signed-commits rule
#      validate against GitHub-side verification.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 435c — allowed_signers + push signed B435"
echo "==========================================================="
echo

ALLOWED_DIR="$HOME/.config/git"
ALLOWED="$ALLOWED_DIR/allowed_signers"
PUB="$HOME/.ssh/id_ed25519.pub"
EMAIL="$(git config --global --get user.email)"

if [ -z "$EMAIL" ]; then
  echo "ERROR: no user.email set in global config. Aborting."
  exit 1
fi

if [ ! -f "$PUB" ]; then
  echo "ERROR: $PUB not found. Aborting."
  exit 1
fi

mkdir -p "$ALLOWED_DIR"

# Format per ssh-keygen(1) / git docs:
#   <principals> <key-type> <key-base64> [comment]
# For signing, "principals" is just the email pattern that matches
# the commit author/committer. Single line is enough for one signer.
KEY_BODY="$(awk '{print $1, $2}' "$PUB")"
LINE="$EMAIL $KEY_BODY"

if [ -f "$ALLOWED" ] && grep -qxF "$LINE" "$ALLOWED"; then
  echo "allowed_signers already contains this line — no change."
else
  echo "Writing $ALLOWED with one signer line:"
  echo "  $LINE"
  printf '%s\n' "$LINE" >> "$ALLOWED"
fi
chmod 600 "$ALLOWED"
echo

echo "Setting gpg.ssh.allowedSignersFile..."
git config --global gpg.ssh.allowedSignersFile "$ALLOWED"
echo "  done. value = $(git config --global --get gpg.ssh.allowedSignersFile)"
echo

echo "Verifying HEAD's signature..."
git log --format='%h %G? %s' -3
echo
echo "If top SHA shows G (good), local verification is working."
echo

# Stage this script and amend so it lands in the history.
git add dev-tools/commit-bursts/commit-burst435c-allowed-signers-and-push.command
git commit --amend --no-edit || { echo "amend failed"; exit 1; }

echo "==========================================================="
echo "Post-amend (script staged into B435):"
echo "==========================================================="
git log --format='%h %G? %s' -3
echo

echo "Pushing to origin..."
git push origin main || { echo "push failed — capture the remote rejection above for diagnosis"; exit 1; }

echo
echo "Done. B435 (signed) pushed."
echo
echo "Press any key to close."
read -n 1 || true
