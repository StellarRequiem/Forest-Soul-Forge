#!/bin/bash
# Burst 273 — ADR-0050 T6: passphrase-backed master key.
#
# Sixth tranche of the encryption-at-rest arc. Closes the
# "Keychain isn't available everywhere" gap by adding a third
# backend for resolve_master_key: derive the 32-byte AES-256 key
# from an operator passphrase via Scrypt + persisted salt.
#
# After this burst lands, operators on Linux / headless boxes /
# CI runners / hardened deployments where the OS keychain isn't
# the right answer can opt into FSF_MASTER_KEY_BACKEND=passphrase
# and either:
#   - supply the passphrase via FSF_MASTER_PASSPHRASE env (non-
#     interactive boots — launchd, systemd, CI)
#   - enter it via getpass.getpass() prompt (interactive boots)
#   - refuse to boot cleanly when neither is available (rather
#     than silently downgrading the operator's posture)
#
# What ships:
#
# 1. security/passphrase_kdf.py (NEW):
#    - derive_key_from_passphrase(passphrase, salt) -> 32 bytes
#      Uses cryptography.hazmat.primitives.kdf.scrypt.Scrypt with
#      N=2**16, r=8, p=1, dklen=32. Memory cost ~64 MiB; runtime
#      ~250ms-1s on modern desktop hardware. Same inputs always
#      produce same output (deterministic).
#    - load_or_create_salt(path) -> 16 bytes
#      First-boot: secrets.token_bytes(16) + write with 0600 perms.
#      Subsequent boots: read existing. Wrong-length file raises
#      explicit error rather than silently regenerating (which
#      would orphan all data encrypted under the old key).
#    - default_salt_path(data_dir) -> <data_dir>/master_salt
#    - PassphraseKDFError taxonomy for empty passphrase + bad
#      salt failures.
#
# 2. security/master_key.py: passphrase backend branch in
#    resolve_master_key. Resolution order under
#    FSF_MASTER_KEY_BACKEND=passphrase:
#      a. FSF_MASTER_PASSPHRASE env var (whitespace-stripped; empty
#         after strip counts as absent)
#      b. interactive getpass.getpass() when stdin is a TTY
#      c. raise RuntimeError — non-interactive without env supply
#         is the most-likely-misconfig case; failing loud beats
#         silent fallback to a different backend (which would
#         create two parallel encrypted stores under different
#         keys)
#    Salt persists at <data_dir or ~/.forest>/master_salt; the
#    new optional ``data_dir`` kwarg on resolve_master_key lets
#    tests + non-default daemons point at their own location.
#    HSM backend (T16) raises NotImplementedError rather than
#    routing through the wrong backend.
#
# 3. New FSF_MASTER_PASSPHRASE_ENV constant in master_key.py for
#    the env-var name. Documented in the module docstring +
#    referenced from the passphrase resolution path.
#
# Why Scrypt and not Argon2id:
# ADR-0050 Decision 5 named Argon2id. Forest's `cryptography` dep
# already ships Scrypt; adding argon2-cffi means a new transitive
# dep + native build tooling. Scrypt is memory-hard, well-vetted
# (Litecoin, Tarsnap), and the only KDF in the existing dep set
# with the required properties. The decision to swap Argon2id →
# Scrypt is contained to T6 substrate; ADR-0050 will be amended
# at T8 close-out.
#
# Tests (test_passphrase_kdf.py — 14 cases):
#   - KDF determinism + sensitivity to passphrase + salt changes
#   - Empty passphrase + wrong-length salt rejection
#   - Salt round-trip (generate + reload identical bytes)
#   - Salt tamper detection (wrong-length file → explicit error)
#   - Master-key passphrase backend via env var (deterministic
#     across cache resets when salt persists)
#   - Different passphrases → different keys
#   - Non-interactive without env passphrase → clean RuntimeError
#   - HSM backend → NotImplementedError (reserved for T16)
#
# What's NOT in T6:
#   - macOS Keychain UX — already shipped via T1 (B266) +
#     ADR-0052 SecretStore (resolve_agent_key_store). The
#     keychain path is bit-identical to pre-T6.
#   - First-boot bootstrapping flow with explicit "create new
#     vs. unlock existing" prompts. The current passphrase mode
#     creates implicitly: first call with no salt + valid
#     passphrase → generate salt + derive key + encrypt going
#     forward. T7 documents this in the runbook.
#   - Lifespan integration changes. resolve_master_key honors
#     FSF_MASTER_KEY_BACKEND=passphrase from the daemon's
#     lifespan call without further plumbing. data_dir defaults
#     to ~/.forest which matches the SecretStore file-store
#     convention — operators with non-default data dirs can
#     symlink or wait for T7's runbook to document the override.
#
# Why this is its own burst (not folded into T8):
# §0 Hippocratic gate. Adding a new key-resolution backend is a
# coherent change with its own test surface; T8's CLI rotation
# flow is a separate coherent change that builds on top of T6's
# substrate. Splitting keeps each commit focused.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/master_key.py \
        src/forest_soul_forge/security/passphrase_kdf.py \
        tests/unit/test_passphrase_kdf.py \
        dev-tools/commit-bursts/commit-burst273-adr0050-t6-passphrase-kdf.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0050 T6 — passphrase-backed master key (B273)

Burst 273. Sixth tranche of the encryption-at-rest arc. Adds a
third backend for resolve_master_key: derive the 32-byte AES-256
key from an operator passphrase via Scrypt + persisted salt. Closes
the gap left by T1-T5 — Keychain only works on macOS, SecretStore
file backend stores the raw key on disk, and headless / hardened
deployments need a way to enable encryption-at-rest without either.

What ships:

  - security/passphrase_kdf.py: derive_key_from_passphrase
    (Scrypt N=2**16/r=8/p=1, ~64 MiB memory cost, ~250ms-1s wall
    time), load_or_create_salt (16-byte random, persisted with
    0600 perms; tamper-detected with explicit-error rather than
    silent regeneration), default_salt_path, PassphraseKDFError.

  - security/master_key.py: passphrase backend branch in
    resolve_master_key. Under FSF_MASTER_KEY_BACKEND=passphrase
    the resolver tries FSF_MASTER_PASSPHRASE env first
    (non-interactive boots), falls back to getpass.getpass() when
    stdin is a TTY, refuses cleanly otherwise. HSM backend (T16)
    raises NotImplementedError. Optional data_dir kwarg lets
    tests + non-default daemons control salt location; production
    defaults to ~/.forest matching SecretStore file-store path.

  - FSF_MASTER_PASSPHRASE_ENV constant for the non-interactive
    supply env-var name. Documented in module docstring.

Why Scrypt instead of Argon2id (ADR Decision 5 said Argon2id):
existing 'cryptography' dep already ships Scrypt. Adding
argon2-cffi means a new transitive dep + native build. Scrypt is
memory-hard, well-vetted (Litecoin / Tarsnap), and the only KDF
in the existing dep set with the required properties. Substrate
decision contained to T6; ADR text amended at T8 close-out.

Tests: test_passphrase_kdf.py — 14 cases covering KDF determinism,
salt round-trip + tamper-detection, env-supplied passphrase happy
path, non-interactive refusal, HSM-not-implemented.

After T6, the four-burst encryption arc remaining (T7 runbook /
T8 CLI rotation) can ship as docs + CLI surfaces. The substrate
is complete: chain (T3) + registry (T2) + memory (T4) + files
(T5a+T5b) + Keychain/passphrase/HSM backends (T1+T6+T16-stub)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 273 complete — ADR-0050 T6 passphrase backend shipped ==="
echo "Remaining: T7 (runbook), T8 (fsf encrypt CLI)."
echo ""
echo "Press any key to close."
read -n 1
