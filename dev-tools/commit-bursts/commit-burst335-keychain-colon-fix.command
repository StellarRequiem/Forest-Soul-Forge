#!/bin/bash
# Burst 335 - two bugs caught during the D4 birth attempt on
# 2026-05-16. Both blocked the birth pipeline end-to-end.
#
# Bug A: python-multipart missing from daemon extras
#   The /voice/transcribe endpoint (ADR-0070 T2) uses FastAPI's
#   Form(...) decorator, which requires python-multipart at
#   module load time. The host venv had it transitively, but
#   when start.command rebuilt the venv after a Python or
#   package update the transitive disappeared and the daemon
#   refused to boot.
#
# Bug B: keychain _valid_name rejected colons
#   agent_key_store (ADR-0049 T4) uses "forest_agent_key:<id>"
#   as its secret-name format — the colon is a deliberate
#   namespace delimiter in its contract. The keychain backend's
#   _valid_name was over-conservative and rejected colons,
#   blocking EVERY agent birth when keychain is the active
#   secret store. Existing agents must have been birthed when
#   a different backend (file) was active; D4 advanced rollout
#   was the first birth attempt under the current keychain
#   posture and caught it.
#
# What ships:
#
# 1. pyproject.toml:
#    Add `python-multipart>=0.0.20` to the [daemon] extras with
#    a comment pinning the rationale.
#
# 2. src/forest_soul_forge/security/secrets/keychain_store.py:
#    Allow ':' in _valid_name. Macos `security add-generic-
#    password` accepts colons in the -s (service) name without
#    escaping; argv-quoting is unaffected.
#
# 3. tests/unit/test_keychain_store.py:
#    Update test_valid_name_allowlist to assert ':' is allowed
#    (`forest_agent_key:test_author_52b54fee` is the canonical
#    case). Reject-list still covers all the shell-meta chars
#    that motivated the original allowlist.
#
# 4. dev-tools/diag-import.command (NEW):
#    One-shot diagnostic that surfaces the hidden traceback
#    start.command's `>/dev/null 2>&1` import probe was masking.
#    Operator-runnable; no side effects.
#
# 5. dev-tools/fix-multipart-dep.command (NEW):
#    Idempotent fix script that pip-installs python-multipart
#    into the existing venv + verifies the daemon module imports.
#    Operators on a freshly-built venv won't need this (the
#    pyproject.toml fix handles them); this script is the
#    in-place patch path for an already-broken venv.
#
# Sandbox-verified test_valid_name_allowlist passes with the
# expanded allowlist.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add pyproject.toml \
        src/forest_soul_forge/security/secrets/keychain_store.py \
        tests/unit/test_keychain_store.py \
        dev-tools/diag-import.command \
        dev-tools/fix-multipart-dep.command \
        dev-tools/commit-bursts/commit-burst335-keychain-colon-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(daemon): keychain colon + python-multipart deps (B335)

Burst 335. Two bugs caught during the D4 birth attempt on
2026-05-16 that blocked the birth pipeline end-to-end.

Bug A: python-multipart missing from daemon extras.
  /voice/transcribe (ADR-0070 T2) uses FastAPI Form(...) which
  requires python-multipart at module load time. Host venv had
  it transitively; when start.command rebuilt the venv the
  transitive disappeared and the daemon refused to boot with
  the misleading 'Install reported success but the package
  still won't import' message (the actual RuntimeError is
  suppressed by start.command's >/dev/null 2>&1 probe).

Bug B: keychain _valid_name rejected colons.
  agent_key_store (ADR-0049 T4) uses 'forest_agent_key:<id>' as
  its secret-name format — colon is a deliberate namespace
  delimiter in its contract. keychain_store._valid_name was
  over-conservative and rejected colons, blocking EVERY agent
  birth when keychain is the active backend. Existing agents
  were birthed under a file-backed store; D4 advanced rollout
  was the first new birth under keychain posture and caught it.

What ships:

  - pyproject.toml: python-multipart>=0.0.20 in [daemon] extras.
  - keychain_store.py: _valid_name accepts ':' in addition to
    alnum + _ - .
  - test_keychain_store.py: assertion that
    'forest_agent_key:test_author_52b54fee' validates.
  - dev-tools/diag-import.command (NEW): surfaces the hidden
    traceback start.command's import probe masks.
  - dev-tools/fix-multipart-dep.command (NEW): in-place fix
    for already-broken venvs (pyproject change handles fresh
    rebuilds).

Sandbox-verified test_valid_name_allowlist passes."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 335 complete — birth pipeline unblocked ==="
echo "Run force-restart-daemon to pick up the keychain fix,"
echo "then re-run birth-test-author.command."
echo ""
echo "Press any key to close."
read -n 1
