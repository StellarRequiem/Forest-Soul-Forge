#!/bin/bash
# Burst 267 — ADR-0050 T2: SQLCipher integration for registry.sqlite.
#
# Second tranche of the encryption-at-rest arc. T1 (B266) shipped
# the master-key substrate; T2 wires it into the registry so the
# SQLite file becomes encrypted-at-rest under SQLCipher's AES-256-
# CBC page-level cipher when the operator opts in.
#
# Default FSF_AT_REST_ENCRYPTION=false preserves bit-identical
# pre-T2 behavior — stdlib sqlite3 + plaintext file, no
# sqlcipher3 import. Operators opt in by setting the env var
# (true). The daemon refuses to boot if encryption is enabled
# but the master key can't be resolved — silent fallback would
# downgrade the operator's posture without their knowledge.
#
# === What's in T2 ===
#
# 1. registry/registry.py:
#    - New RegistryEncryptionError class (RegistryError subclass).
#      Raised on sqlcipher3 import failure, wrong key, plaintext-
#      DB-but-key-supplied, or PRAGMA cipher_version returning
#      empty (vanilla SQLite build linked against sqlcipher3).
#    - _ThreadLocalConn gains a ``master_key: bytes | None`` kwarg.
#      When set, _get() opens the connection via sqlcipher3.dbapi2
#      and runs PRAGMA key with the hex-encoded key as the FIRST
#      statement, then probes PRAGMA cipher_version to surface
#      setup failures cleanly. When None: bit-identical pre-T2
#      stdlib sqlite3 path.
#    - Lazy import of sqlcipher3.dbapi2 — only when master_key is
#      set. Test envs and lightweight operators without the daemon
#      extras installed never need the wheel.
#    - Registry.bootstrap() gains a ``master_key: bytes | None``
#      keyword arg, passed through to _ThreadLocalConn.
#
# 2. daemon/app.py lifespan:
#    - Reads FSF_AT_REST_ENCRYPTION env (default false).
#    - When true: resolves the master key via T1's
#      resolve_master_key() BEFORE Registry.bootstrap, passes it
#      through. Failure to resolve is FATAL (raise) — silent
#      fallback to plaintext would downgrade posture.
#    - When false: passes master_key=None, registry stays plaintext.
#
# 3. pyproject.toml:
#    - sqlcipher3-binary>=0.5.4 added to [daemon] extras (not
#      base deps — tests / dev envs without the extras keep
#      working). Cross-platform: prebuilt wheels for macOS
#      arm64/x86_64 and Linux x86_64/arm64; Windows operators
#      compile manually (out of scope for v1 per ADR Decision 2).
#
# 4. tests/unit/test_registry_sqlcipher.py:
#    - Hard-gated with pytest.importorskip('sqlcipher3'). The
#      whole file skips when the binding isn't installed (sandbox
#      test envs).
#    - 6 cases covering:
#      a. bootstrap_with_key_creates_encrypted_db (raw sqlite3
#         can't read the schema)
#      b. bootstrap_with_same_key_reopens_db (daemon-restart
#         survives)
#      c. bootstrap_with_wrong_key_raises
#      d. bootstrap_with_key_on_plaintext_db_raises (operator
#         flipped the env var without running T8 migration)
#      e. bootstrap_without_key_is_bit_identical_pre_T2 (raw
#         sqlite3 reads the schema fine)
#      f. missing sqlcipher3 import raises with install hint
#
# === What's NOT in T2 ===
#
# - Plaintext→encrypted migration tool (T8: ``fsf encrypt
#   migrate-registry``).
# - Audit chain per-event encryption (T3).
# - Memory body application-layer encryption (T4 — defense in
#   depth on top of the SQLCipher layer this burst ships).
# - Soul + constitution file encryption (T5).
# - Passphrase / HSM backends (T6 / T16).
# - Runbook (T7).
# - CLI fsf encrypt family (T8).
#
# === Operator note ===
#
# Existing deployments with a plaintext data/registry.sqlite stay
# plaintext under FSF_AT_REST_ENCRYPTION=false (default). Turning
# the env var on against an existing plaintext DB raises
# RegistryEncryptionError at daemon startup — operator must
# either run the T8 migration tool when it ships, or accept the
# legacy plaintext window for the existing file and only encrypt
# from a fresh DB onward.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/registry/registry.py \
        src/forest_soul_forge/daemon/app.py \
        pyproject.toml \
        tests/unit/test_registry_sqlcipher.py \
        dev-tools/commit-bursts/commit-burst267-adr0050-t2-sqlcipher.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0050 T2 — SQLCipher at-rest encryption for registry (B267)

Burst 267. Wires T1's master-key substrate into Registry.bootstrap
so the SQLite file becomes encrypted-at-rest under SQLCipher's
AES-256-CBC page-level cipher when FSF_AT_REST_ENCRYPTION=true.

Default false preserves bit-identical pre-T2 behavior — stdlib
sqlite3 + plaintext file, no sqlcipher3 import. Operators opt in
by env var; daemon refuses to boot if encryption is enabled but
the master key can't be resolved (silent fallback would downgrade
the operator's posture without their knowledge).

Implementation:

- RegistryEncryptionError class (RegistryError subclass) raised
  on sqlcipher3 import failure, wrong key, plaintext-DB-with-key-
  supplied, or empty PRAGMA cipher_version (vanilla SQLite linked
  against sqlcipher3).

- _ThreadLocalConn gains master_key kwarg. Per-thread connections
  open via sqlcipher3.dbapi2 when set, running 'PRAGMA key =
  \"x'<hex>'\"' as the first statement. Probes PRAGMA
  cipher_version to surface setup failures cleanly; raises
  RegistryEncryptionError on probe failure with the connection
  closed.

- Lazy import — sqlcipher3.dbapi2 only imports when master_key is
  set. Test envs without the daemon extras keep importing
  registry.py fine; only operators who enable encryption pay the
  wheel cost.

- Registry.bootstrap gains master_key kwarg passed through.

- Daemon lifespan reads FSF_AT_REST_ENCRYPTION env, calls
  resolve_master_key() BEFORE Registry.bootstrap when on, raises
  on resolution failure.

- pyproject [daemon] extras gains sqlcipher3-binary>=0.5.4.

Tests: 6 cases in test_registry_sqlcipher.py, hard-gated with
pytest.importorskip('sqlcipher3'). Cover encrypted-mode bootstrap
(raw sqlite3 can't read schema), same-key reopen (daemon restart),
wrong-key refusal, plaintext-DB-with-key refusal (operator turned
on env var without migration), plaintext-mode bit-identical
behavior, and the import-failure path with install hint.

Out of scope (later tranches):
- T3: audit chain per-event encryption (substrate available at
  app.state.master_key from B266; T3 ships the wrapper)
- T4: memory body application-layer encryption
- T5: soul + constitution file encryption (.md.enc / .yaml.enc)
- T6: passphrase / HSM backends
- T7: encryption-at-rest runbook
- T8: ``fsf encrypt`` CLI family + plaintext→encrypted migration

Operator note: existing plaintext registry.sqlite stays plaintext
under the default env var. Flipping FSF_AT_REST_ENCRYPTION=true
against an existing plaintext DB refuses with a clear error —
T8 ships the migration tool. Operators can accept the legacy
plaintext window for the existing file and encrypt from a fresh
DB onward."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 267 complete — ADR-0050 T2 SQLCipher integration shipped ==="
echo "Press any key to close."
read -n 1
