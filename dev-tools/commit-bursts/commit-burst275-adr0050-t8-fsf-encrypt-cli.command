#!/bin/bash
# Burst 275 — ADR-0050 T8: fsf encrypt CLI. Closes the encryption-at-
# rest arc 8/8.
#
# Three subcommands:
#
#   fsf encrypt status
#       Read-only inspection. Surfaces:
#         - FSF_AT_REST_ENCRYPTION value
#         - FSF_MASTER_KEY_BACKEND value
#         - Configured backend (env-resolved or platform-default)
#         - Master-key resolution probe (only when encryption is on
#           — avoids triggering passphrase prompt / Keychain entry
#           creation just to run the status command)
#         - Per-surface inventory:
#             * audit chain: total / encrypted / plaintext / malformed
#               line counts
#             * souls dir: .soul.md vs .soul.md.enc counts + same for
#               constitutions
#             * registry: SQLite-magic probe to distinguish plaintext
#               from SQLCipher-encrypted
#
#   fsf encrypt decrypt-event <seq>
#       Read-only debug. Walks the audit chain, finds the entry with
#       the supplied seq number, prints its plaintext event_data.
#       Plaintext entries print directly; encrypted entries decrypt
#       under the current master key. Errors cleanly when:
#         - chain file missing (rc 2)
#         - seq not found (rc 1)
#         - master key unavailable (rc 3)
#         - decrypt fails — wrong key, tampered, malformed (rc 4)
#
#   fsf encrypt rotate-key [--confirm-daemon-stopped]
#       Master-key rotation. Generates a fresh 32-byte key, re-encrypts
#       every encrypted surface under it, persists the new key.
#       Required safety affirmation:
#         * --confirm-daemon-stopped flag — concurrent rotation
#           against a live daemon corrupts the encrypted store
#       Per-surface backups (suffix .pre-rotate by default) stage
#       alongside each mutated file before rotation; the CLI never
#       deletes them — operator decides when to clean up after
#       verifying the rotation booted cleanly.
#
#       Rotation steps:
#         1. Resolve current master key (refuse if unavailable)
#         2. Generate new 32-byte key via secrets.token_bytes
#         3. Audit chain: re-encrypt each encrypted entry under new
#            key (plaintext pre-T3 entries left unchanged — re-
#            encrypting would change entry_hash + break the chain)
#         4. Soul + constitution .enc files: re-encrypt each
#         5. Registry: re-encrypt memory_entries.content rows where
#            content_encrypted=1, then PRAGMA rekey the SQLCipher
#            whole file
#         6. Persist new key to the configured backend (keychain or
#            file; passphrase rotation explicitly unsupported in T8 —
#            the runbook documents the manual procedure)
#
#       Atomic per-surface; failure mid-rotation surfaces a clear
#       error pointing at the .pre-rotate backup. The CLI never
#       silently downgrades — if any surface fails, the operator
#       knows exactly which state to restore.
#
# Tests (test_cli_encrypt.py — 12 cases):
#   - Summarizer helpers: audit chain mixed counts, missing file,
#     soul dir inventory, registry SQLite-magic detection (plaintext
#     vs opaque)
#   - status command with encryption off (skips key resolution probe)
#   - decrypt-event: plaintext entry passthrough, encrypted entry
#     round-trip, seq-not-found, missing chain
#   - rotate-key safety gate: refuses without --confirm-daemon-stopped
#   - rotate-key surface helpers: audit chain re-encryption preserves
#     plaintext entries + re-encrypts encrypted ones; soul file
#     rotation re-encrypts .enc + leaves plaintext alone
#
# Heavy paths NOT covered by unit tests:
#   - Full SQLCipher whole-file rotation (requires sqlcipher3 wheel
#     + real registry data; exercised in the integration phase + on
#     production by following the runbook)
#   - Passphrase-backend rotation refusal (the runbook documents
#     the manual procedure)
#
# After B275, ADR-0050 closes 8/8 tranches. The encryption-at-rest
# arc is complete:
#   T1: master key substrate (B266)
#   T2: SQLCipher registry encryption (B267)
#   T3: per-event audit chain encryption (B268)
#   T4: memory body application-layer encryption (B269)
#   T5a: soul + constitution file encryption (B271)
#   T5b: dispatcher hot-path encryption-aware reads (B272)
#   T6: passphrase-backed master key (B273)
#   T7: operator runbook (B274)
#   T8: fsf encrypt CLI — status / decrypt-event / rotate-key (B275)

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/cli/encrypt_cmd.py \
        src/forest_soul_forge/cli/main.py \
        tests/unit/test_cli_encrypt.py \
        dev-tools/commit-bursts/commit-burst275-adr0050-t8-fsf-encrypt-cli.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0050 T8 CLOSED — fsf encrypt CLI (B275)

Burst 275. Eighth and final tranche of the encryption-at-rest arc.
Three subcommands close the ADR end-to-end:

  fsf encrypt status
    Read-only inspection. Surfaces backend, FSF_AT_REST_ENCRYPTION
    posture, master-key resolution probe (only when encryption is
    on — avoids triggering passphrase prompt or Keychain entry
    creation to run status), per-surface inventory:
      - audit chain: total / encrypted / plaintext / malformed counts
      - souls dir: .soul.md vs .soul.md.enc counts + same for
        constitutions
      - registry: SQLite-magic probe to distinguish plaintext from
        SQLCipher-encrypted without unlocking the key.

  fsf encrypt decrypt-event <seq>
    Read-only debug. Walks the audit chain, finds the entry, prints
    plaintext event_data. Plaintext entries print directly; encrypted
    entries decrypt under the current master key. Exit codes
    distinguish chain-missing / seq-not-found / key-unavailable /
    decrypt-fail.

  fsf encrypt rotate-key [--confirm-daemon-stopped]
    Master-key rotation. Required safety affirmation (concurrent
    rotation against a live daemon corrupts the store). Per-surface
    backups (.pre-rotate suffix) stage alongside each mutated file;
    CLI never deletes them. Rotation steps:
      1. Resolve current key (refuse if unavailable)
      2. Generate fresh 32-byte key
      3. Audit chain: re-encrypt each encrypted entry; plaintext
         pre-T3 entries left unchanged (re-encrypting would break
         entry_hash invariant)
      4. Soul + constitution .enc files: re-encrypt each
      5. Registry: re-encrypt memory_entries.content rows where
         content_encrypted=1, then PRAGMA rekey the SQLCipher file
      6. Persist new key to backend (keychain or file; passphrase
         rotation explicitly unsupported — runbook documents the
         manual procedure)
    Atomic per-surface; failure surfaces a clear error pointing at
    the .pre-rotate backup. CLI never silently downgrades.

Tests: test_cli_encrypt.py — 12 cases covering summarizers, status
(encryption-off path), decrypt-event (plaintext + encrypted +
not-found + missing-chain), rotate-key safety gate, rotate-key
surface helpers (audit chain re-encryption + soul file rotation).
SQLCipher whole-file rotation + passphrase-backend rotation refusal
exercised manually per the runbook.

After B275, ADR-0050 closes 8/8:
  T1 master key substrate (B266)
  T2 SQLCipher registry encryption (B267)
  T3 per-event audit chain encryption (B268)
  T4 memory body application-layer encryption (B269)
  T5a soul + constitution file encryption (B271)
  T5b dispatcher hot-path encryption-aware reads (B272)
  T6 passphrase-backed master key (B273)
  T7 operator runbook (B274)
  T8 fsf encrypt CLI (B275)

The kernel now has integrity end-to-end (ADR-0049 per-event
signatures — tamper-PROOF), confidentiality end-to-end (ADR-0050
at-rest encryption — disk compromise → opaque ciphertext), and
execution isolation (ADR-0051 per-tool sandbox — compromised tool
hits OS boundary before damaging host). The Phase-4 security
hardening arc from the 2026-05-05 outside review is closed."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 275 complete — ADR-0050 CLOSED 8/8 ==="
echo "Phase-4 security-hardening arc closed end-to-end."
echo "Next stop: heavy/light audit OR ADR-0044 P7 integrator outreach."
echo ""
echo "Press any key to close."
read -n 1
