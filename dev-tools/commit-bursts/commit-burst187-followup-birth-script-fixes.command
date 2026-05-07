#!/bin/bash
# B187 follow-up — bug fixes in birth-smith.command surfaced
# during the live 2026-05-07 birth.
#
# Two fixes:
#
# 1. trait_values payload — first attempt invented trait names
#    (`self_review_rigor`, `humility`, etc.) that don't exist
#    in the trait registry. Birth POST returned 400 with
#    `unknown trait: self_review_rigor`. Fix: empty
#    trait_values dict and let the kernel compute defaults
#    from the experimenter role's domain_weights — same path
#    every other birth uses.
#
# 2. Constitution-patch Python invocation — phase 3 used
#    `python3 - <<'PY'` which resolves to /usr/bin/python3 on
#    macOS, which doesn't have PyYAML. ModuleNotFoundError
#    crashed the script BEFORE phase 4 (posture set) and
#    phase 5 (workspace clone). Fix: switch to `.venv/bin/python3`
#    which has PyYAML installed alongside the daemon's other
#    dependencies. Same pattern the install-soulux-computer-
#    control.command uses (B176 fix).
#
# After both fixes, re-running birth-smith.command on Alex's
# Mac produced:
#   - Smith already exists: experimenter_1de20e0840a2 -> skip birth
#   - Constraints patched: shell_exec, code_edit, web_fetch
#   - Posture set: yellow
#   - Workspace cloned to ~/.fsf/experimenter-workspace/
#   - Branch experimenter/cycle-1 created
#
# Smith identity:
#   instance_id:       experimenter_1de20e0840a2
#   dna:               1de20e0840a2
#   constitution_hash: 81a1731e9b1fc2d9aecd8f9fe5380733ffdf17c0d9d5a78873f3724db41f7f2a
#   created_at:        2026-05-07 03:22:40Z
#
# Per ADR-0001 D2: Smith's constitution_hash + DNA are immutable
# from this moment forward. Future cycle tool additions go
# through tools_add (per-instance state mutation, not identity).
#
# Verification:
#   - All 5 phases of birth-smith.command completed clean
#     2026-05-07 03:23.
#   - Smith visible via /agents endpoint (verify in chat tab
#     dashboard).
#
# Idempotency check: re-running birth-smith.command after this
# fix detects existing Smith, skips birth, re-applies the
# constitution patch (no-op if constraints already present),
# re-sets posture (no-op), re-checks workspace dir (no-op).
# Safe.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/birth-smith.command \
        dev-tools/commit-bursts/commit-burst187-followup-birth-script-fixes.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(experimenter): birth-smith.command live-run bug fixes (B187 followup)

Two fixes surfaced during the 2026-05-07 live birth of Smith:

1. trait_values payload — first attempt invented trait names
   that don't exist in the trait registry. Birth POST returned
   400 with 'unknown trait: self_review_rigor'. Fix: empty
   trait_values dict; kernel computes defaults from the
   experimenter role's domain_weights.

2. Constitution-patch Python invocation — phase 3 used system
   python3 which lacks PyYAML. Switched to .venv/bin/python3
   which has the daemon's deps installed. Same pattern as
   install-soulux-computer-control.command (B176 fix).

After both fixes, the script ran clean end-to-end. Smith is
alive:

  instance_id:       experimenter_1de20e0840a2
  dna:               1de20e0840a2
  constitution_hash: 81a1731e9b1fc2d9aecd8f9fe5380733ffdf17c0d9d5a78873f3724db41f7f2a
  role:              experimenter
  genre:             actuator
  posture:           yellow
  workspace:         /Users/llm01/.fsf/experimenter-workspace/Forest-Soul-Forge
  first branch:      experimenter/cycle-1

Per ADR-0001 D2: Smith's constitution_hash + DNA are now
immutable. Future tool additions go through tools_add
(per-instance state mutation).

Idempotent: re-running birth-smith.command detects existing
Smith and skips/re-applies cleanly."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== B187 followup commit + push complete ==="
echo "Press any key to close this window."
read -n 1
