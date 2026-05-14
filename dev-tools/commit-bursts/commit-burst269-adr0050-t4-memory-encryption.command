#!/bin/bash
# Burst 269 — ADR-0050 T4: memory body application-layer encryption.
#
# Fourth tranche of the encryption-at-rest arc. Reuses T3's
# at_rest_encryption primitives (encrypt_text / decrypt_text added
# this burst) and the schema migration pattern.
#
# What ships:
#
# 1. New encrypt_text / decrypt_text helpers in
#    core/at_rest_encryption.py. Symmetric to encrypt_event_data /
#    decrypt_event_data but operate on str payloads and produce a
#    single base64 string that round-trips cleanly through a
#    SQLite TEXT column.
#
# 2. Schema bump v20 → v21. Single migration adds
#    content_encrypted INTEGER NOT NULL DEFAULT 0 to memory_entries
#    (CHECK constraint enforces 0/1). 10 pinned-version test
#    locations updated.
#
# 3. Memory class gains optional ``encryption_config`` field:
#    - append() encrypts content + sets content_encrypted=1 when
#      config is set
#    - content_digest is computed over PLAINTEXT regardless of
#      encryption mode — the stable identity property across
#      modes (a plaintext-mode Memory and an encrypted-mode
#      Memory produce IDENTICAL digests for the same content)
#    - _row_to_entry detects the flag and decrypts transparently
#    - all 3 call sites in memory/__init__.py pass
#      encryption_config=self.encryption_config
#
# 4. Daemon lifespan T4-refinement: master key only stashed on
#    app.state.master_key when FSF_AT_REST_ENCRYPTION=true. B266
#    set it unconditionally — bug fix here. Single env var
#    authoritatively gates all three consumers (registry SQLCipher,
#    audit chain encryption, memory body encryption).
#
# 5. deps.py per-request Memory construction reads
#    app.state.master_key, builds EncryptionConfig, threads it
#    into the Memory instance the dispatcher uses.
#
# 6. Tests: test_memory_encryption.py with 9 cases covering
#    encrypted-write round-trip (raw row read; transparent
#    recall/get; digest-over-plaintext invariant), mixed plaintext+
#    encrypted on same table, failure shapes (encrypted row read
#    without config / wrong key), and string-level primitive
#    round-trips (unicode, empty, large, multi-line).
#
# === What's NOT in T4 ===
#
# - Soul + constitution file encryption (T5).
# - Passphrase + HSM backends (T6 / T16).
# - Runbook (T7).
# - CLI fsf encrypt family (T8) — including the rotate-key flow
#   that introduces date-stamped kids.
# - SQLCipher whole-DB-rekey on master-key rotation. T4 encrypts at
#   the application layer ON TOP OF SQLCipher; rotation has to
#   rewrite all encrypted rows + re-key SQLCipher. T8 ships the
#   tool.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/at_rest_encryption.py \
        src/forest_soul_forge/core/memory/__init__.py \
        src/forest_soul_forge/core/memory/_helpers.py \
        src/forest_soul_forge/daemon/app.py \
        src/forest_soul_forge/daemon/deps.py \
        src/forest_soul_forge/registry/schema.py \
        tests/unit/test_registry.py \
        tests/unit/test_daemon_readonly.py \
        tests/unit/test_plugin_grants.py \
        tests/unit/test_procedural_shortcuts.py \
        tests/unit/test_reality_anchor_corrections.py \
        tests/unit/test_memory_encryption.py \
        dev-tools/commit-bursts/commit-burst269-adr0050-t4-memory-encryption.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0050 T4 — memory body encryption + schema v21 (B269)

Burst 269. Fourth tranche of the encryption-at-rest arc.
Encrypts memory_entries.content at the application layer using
the same master key consumed by T2 (SQLCipher) and T3 (audit
chain). Defense-in-depth posture: a hypothetical SQLCipher
break still leaves memory bodies sealed under this layer, and
vice versa.

Per-row content_encrypted flag distinguishes plaintext (legacy +
pre-T4) entries from encrypted (post-T4) ones. Mixed
plaintext+encrypted entries coexist on the same table per ADR
Decision 6 — operators turning on FSF_AT_REST_ENCRYPTION
mid-lifecycle keep their old entries readable while new entries
land encrypted.

Implementation:

- New encrypt_text / decrypt_text helpers in
  core/at_rest_encryption.py: str payloads, base64'd JSON
  envelope; round-trip clean through SQLite TEXT.

- Schema v20 → v21 migration adds content_encrypted INTEGER
  NOT NULL DEFAULT 0 to memory_entries. SCHEMA_VERSION bumped;
  10 pinned-version test locations updated in 5 test files.

- Memory class accepts optional encryption_config. append()
  encrypts content + sets content_encrypted=1 when set.
  content_digest is computed over PLAINTEXT regardless of mode,
  preserving the stable-identity property across the
  plaintext-vs-encrypted opt-in.

- _row_to_entry takes an encryption_config kwarg; detects the
  content_encrypted flag and decrypts transparently. All 3 call
  sites in memory/__init__.py pass self.encryption_config
  through.

- Daemon lifespan T4-refinement: B266 stashed app.state.master_key
  unconditionally on resolution; T4 narrows that to only when
  FSF_AT_REST_ENCRYPTION=true. Bug fix — without this, T3 and
  T4 wired through deps.py would silently start encrypting
  whenever the master key existed, regardless of operator intent.

- deps.py per-request Memory construction reads
  app.state.master_key, builds EncryptionConfig if set, threads
  it through.

Tests: test_memory_encryption.py — 9 cases covering encrypted
write round-trip (raw row read; transparent recall/get;
digest-over-plaintext invariant), mixed plaintext+encrypted on
same table (operator opt-in mid-lifecycle), failure shapes
(encrypted row read without config / wrong key), and string-
level primitive round-trips (unicode, empty, large, multi-line).

Out of scope (later tranches):
- T5: soul + constitution file encryption (.md.enc / .yaml.enc)
- T6: passphrase + HSM backends
- T7: encryption-at-rest runbook
- T8: fsf encrypt CLI + plaintext→encrypted migration + key
       rotation (rewriting encrypted rows + re-keying SQLCipher)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 269 complete — ADR-0050 T4 memory encryption shipped ==="
echo "Press any key to close."
read -n 1
