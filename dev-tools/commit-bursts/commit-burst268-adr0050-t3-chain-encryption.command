#!/bin/bash
# Burst 268 — ADR-0050 T3: per-event audit chain encryption.
#
# Third tranche of the encryption-at-rest arc. T1 (B266) shipped
# the master-key substrate. T2 (B267) wired it into SQLCipher for
# the registry SQLite file. T3 (this burst) wires it into the
# audit chain JSONL on a per-event basis.
#
# Per ADR-0050 Decision 3, the chain stays one-line-per-event and
# the envelope (seq/timestamp/prev_hash/entry_hash/agent_dna/
# event_type/signature) stays plaintext-readable so the chain
# verifier can walk hash links without unlocking the master key.
# Only ``event_data`` gets the AES-256-GCM treatment:
#
# Pre-T3 entry (plaintext):
#   {"seq":...,"event_data":{...},...}
#
# Post-T3 entry (encrypted):
#   {"seq":...,
#    "encryption":{"alg":"AES-256-GCM","kid":"master:default",
#                  "nonce":"...","ct":"..."},
#    ...}
#
# === Hash-chain integrity invariant ===
#
# entry_hash is computed over PLAINTEXT event_data. Encryption
# happens AFTER hash computation. This is the property that lets:
#   1. Hash-chain verify continue to work after re-encrypting
#      under a new kid (T8 rotation).
#   2. The chain stay verifiable end-to-end even when readers
#      decrypt with different keys for different entry ranges.
#   3. The verifier produce identical hash inputs whether the
#      entry was originally written under T3 or pre-T3.
#
# === Mixed legacy + encrypted chains ===
#
# ADR-0050 Decision 6: pre-T3 plaintext entries coexist with T3
# encrypted entries on the same chain file. Operators turning on
# FSF_AT_REST_ENCRYPTION mid-lifecycle: existing entries stay
# plaintext (no rewrite — that would break the append-only
# invariant), new entries land encrypted. The reader detects the
# envelope via ``is_encrypted_entry()`` and decrypts only when
# present.
#
# === What's in T3 ===
#
# 1. New module: core/at_rest_encryption.py
#    - EncryptionConfig dataclass (master_key + kid)
#    - encrypt_event_data() → envelope dict
#    - decrypt_event_data() → plaintext dict
#    - is_encrypted_entry() — envelope-detection predicate
#    - DEFAULT_KID = "master:default" (T8 introduces rotation)
#    - EncryptionError + CipherUnavailableError + DecryptError
#      taxonomy
#    - Uses cryptography.hazmat.primitives.ciphers.aead.AESGCM
#      (already in [daemon] extras for ADR-0049 signatures)
#
# 2. core/audit_chain.py:
#    - AuditChain._encryption optional config field
#    - AuditChain.set_encryption(config) wiring method
#    - AuditChain._encrypted_json_line() — writes envelope form
#    - AuditChain._write_line() — picks plaintext vs envelope
#      based on config
#    - _entry_from_dict() — new encryption_config kwarg.
#      Detects envelope, decrypts. Mixed chains supported. None
#      config + encrypted entry on disk = clean AuditChainError.
#    - All 5 _entry_from_dict() call sites updated to thread
#      self._encryption through.
#
# 3. daemon/app.py lifespan:
#    - When _registry_master_key is set (T2 path; happens when
#      FSF_AT_REST_ENCRYPTION=true), the audit chain gets
#      set_encryption() called immediately after construction.
#    - startup_diagnostics gains an at_rest_encryption descriptor
#      so /healthz shows whether encryption is active.
#    - Pre-existing AuditChain construction (line 292) and
#      _registry_master_key resolution (T2 block) are reused —
#      T3 only ADDS the wiring call.
#
# 4. tests/unit/test_audit_chain_encryption.py:
#    - 14 cases:
#      a. Substrate: encrypt_event_data / decrypt_event_data
#         round-trip; nested structures; unique nonces per call
#      b. Failure shapes: tampered ciphertext; wrong key; unknown
#         kid; unsupported alg
#      c. Integration: appended entries write envelope form on
#         disk; round-trip plaintext via read_all; hash-chain
#         verify still passes under encryption; mixed
#         legacy+encrypted chain (operator opt-in mid-lifecycle);
#         missing config when chain has encrypted entries raises;
#         wrong key on read raises
#
# === What's NOT in T3 ===
#
# - Memory body application-layer encryption (T4 — defense in
#   depth on top of SQLCipher; reuses the
#   encrypt_event_data/decrypt_event_data primitives shipped
#   here).
# - Soul + constitution file encryption (T5).
# - Passphrase / HSM backends (T6 / T16).
# - Runbook (T7).
# - CLI fsf encrypt family (T8 — including the rotate-key flow
#   that introduces date-stamped kids).
#
# === Operator note ===
#
# Default FSF_AT_REST_ENCRYPTION=false preserves bit-identical
# pre-T3 chain shape. Turning on the env var encrypts only NEW
# entries — existing plaintext entries stay readable (mixed
# chains are explicit per ADR Decision 6). Turning the env var
# back OFF stops emitting encrypted entries but the old encrypted
# entries remain unreadable without the master key (operator
# needs to keep the key or accept data loss).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/at_rest_encryption.py \
        src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_audit_chain_encryption.py \
        dev-tools/commit-bursts/commit-burst268-adr0050-t3-chain-encryption.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0050 T3 — audit chain per-event encryption (B268)

Burst 268. Wires T1's master-key substrate into the audit chain
on a per-event basis. Default FSF_AT_REST_ENCRYPTION=false
preserves bit-identical pre-T3 behavior — new entries land
plaintext exactly as they did before. Operators opt in via env
var; new entries get AES-256-GCM envelope on disk, old entries
stay readable (mixed chains are explicit per ADR Decision 6).

ADR-0050 Decision 3 envelope shape:

  Pre-T3:  {seq, event_data, prev_hash, entry_hash, ...}
  Post-T3: {seq, encryption: {alg, kid, nonce, ct},
            prev_hash, entry_hash, ...}

entry_hash is computed over PLAINTEXT event_data — encryption
happens AFTER hash computation. This invariant lets hash-chain
verify continue to work under any key generation (T8 rotation),
and lets pre-T3 and post-T3 entries coexist + verify together.

Implementation:

- New module core/at_rest_encryption.py with:
  * EncryptionConfig dataclass (master_key + kid)
  * encrypt_event_data() / decrypt_event_data() primitives
    using cryptography.hazmat.primitives.ciphers.aead.AESGCM
  * is_encrypted_entry() detection predicate
  * EncryptionError / CipherUnavailableError / DecryptError
    taxonomy

- AuditChain.set_encryption() wires the config. Append path
  computes entry_hash over plaintext THEN encrypts on write
  via the new _encrypted_json_line() shape. Read path
  (_entry_from_dict) takes a new encryption_config kwarg,
  threaded through all 5 call sites; detects envelope, decrypts.
  Mixed legacy+encrypted chains explicitly supported.

- daemon/app.py lifespan: when the T2 master-key resolution
  succeeded (FSF_AT_REST_ENCRYPTION=true), the audit chain gets
  set_encryption() called right after construction. Single
  env-var gates BOTH registry SQLCipher (T2) and audit chain
  encryption (T3).

Tests: 14 cases in test_audit_chain_encryption.py covering
substrate round-trip, failure shapes (tampered/wrong-key/
unknown-kid/unsupported-alg), and AuditChain integration
(envelope on disk; plaintext recovered via read_all; verify
passes under encryption; mixed chain works; missing config on
encrypted chain raises; wrong key raises).

Out of scope (next tranches):
- T4: memory body application-layer encryption (reuses
  encrypt/decrypt primitives shipped here)
- T5: .md.enc / .yaml.enc file encryption
- T6: passphrase + HSM backends
- T7: encryption-at-rest runbook
- T8: ``fsf encrypt`` CLI + key rotation flow (introduces
  date-stamped kids)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 268 complete — ADR-0050 T3 audit chain encryption shipped ==="
echo "Press any key to close."
read -n 1
