#!/bin/bash
# Burst 266 — ADR-0050 T1: at-rest encryption master-key substrate.
#
# First tranche of the encryption-at-rest arc. ADR-0050 pairs with
# ADR-0049 (per-event signatures): 0049 stops forgery, 0050 stops
# disclosure. Both close the audit-chain-on-disk gap the 2026-05-05
# outside review flagged as "biggest practical hole."
#
# T1 ships the substrate — the master key. T2 (SQLCipher), T3
# (per-event audit chain encryption), T4 (memory body encryption)
# consume it. T1 alone changes no on-disk format; daemons that
# pick it up just store a 32-byte key in the existing SecretStore
# backend and stash it on app.state.master_key for downstream
# tranches to read.
#
# === What's in T1 ===
#
# 1. New file: src/forest_soul_forge/security/master_key.py
#    Mirrors operator_key.py's shape (different concern, identical
#    substrate). resolve_master_key() returns a process-cached
#    32-byte AES-256 key. Generates on first call via
#    secrets.token_bytes(32) and persists under reserved name
#    'forest_master_key:default' via the underlying SecretStore
#    backend (FileStore on linux/elsewhere, KeychainStore on
#    darwin per platform default). Distinct namespace from
#    forest_agent_key: and forest_operator_master: — lists in
#    each backend stay cleanly partitioned by prefix.
#
# 2. New env var: FSF_MASTER_KEY_BACKEND={keychain,file,passphrase,hsm}
#    'keychain' and 'file' are wired by T1. 'passphrase' and 'hsm'
#    are stubbed for future T6 / T16. Unknown values fall back to
#    the platform default ('keychain' on darwin, 'file' elsewhere)
#    so a typo never silently routes through an unintended
#    backend.
#
# 3. Daemon lifespan wiring: app.state.master_key set during
#    startup right after app.state.audit_chain. Failure is
#    non-fatal — the diagnostic surfaces 'degraded' status and
#    the daemon proceeds with at-rest encryption disabled. T6
#    will add a strict-mode flag that refuses to boot on master-
#    key unavailability.
#
# 4. New tests: tests/unit/test_master_key.py (16 cases). Mirror
#    test_operator_key.py's fresh_store fixture pattern.
#    Coverage:
#      - first-call generates 32 random bytes
#      - generated key passes randomness sanity (different stores
#        produce different keys)
#      - second-call is idempotent (cached or backend-read)
#      - reserved namespace doesn't pollute list_agent_ids
#      - reserved name format locked
#      - master + operator namespaces coexist in one backend
#      - explicit-store bypasses process cache
#      - persistence across simulated daemon restart (cache reset)
#      - malformed base64 payload raises (NOT silent regenerate)
#      - wrong-length payload raises
#      - FSF_MASTER_KEY_BACKEND env var routing (5 cases —
#        keychain / file / passphrase-reported-not-wired / unknown
#        falls back / unset returns platform default)
#
# === What's NOT in T1 ===
#
# - No SQLCipher integration (T2)
# - No audit chain encryption (T3)
# - No memory body encryption (T4)
# - No soul.md / constitution.yaml file encryption (T5)
# - No passphrase prompt or HSM backend (T6 / T16)
# - No CLI `fsf encrypt` family (T8)
# - No runbook (T7)
#
# === Tests expected ===
#
# 16 new in test_master_key.py. Existing tests unchanged. No
# regression — the lifespan wiring is wrapped in try/except so a
# fresh-checkout daemon without an existing master key generates
# one cleanly and proceeds.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/master_key.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_master_key.py \
        dev-tools/commit-bursts/commit-burst266-adr0050-t1-master-key.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0050 T1 — at-rest encryption master key (B266)

Burst 266. First tranche of ADR-0050 (encryption at rest).
ADR-0050 pairs with ADR-0049 — 0049 stops forgery, 0050 stops
disclosure. Both close the audit-chain-on-disk gap the
2026-05-05 outside review flagged as 'biggest practical hole.'

T1 ships the substrate without changing on-disk format. T2
(SQLCipher), T3 (per-event audit chain encryption), T4 (memory
body encryption) consume the key produced here.

What's in:

- New module security/master_key.py. resolve_master_key()
  returns a process-cached 32-byte AES-256 key. On first call,
  generates via secrets.token_bytes(32) and persists under
  reserved name 'forest_master_key:default' through the existing
  SecretStore backend (FileStore / KeychainStore / VaultWardenStore
  — substrate reused from ADR-0052). Namespace is distinct from
  forest_agent_key: and forest_operator_master: so listing in
  any backend stays cleanly partitioned by prefix.

- Mirror of operator_key.py's shape — different concern (symmetric
  AES key vs. ed25519 keypair), identical substrate pattern. The
  AgentKeyStore wrapper rejects colons in its public store()/fetch()
  API; master_key.py goes through the underlying SecretStoreProtocol
  backend directly, identical to operator_key.py's approach.

- New env var FSF_MASTER_KEY_BACKEND with values keychain / file /
  passphrase / hsm. T1 wires keychain (default on darwin) and file
  (default on non-darwin); passphrase + hsm are stubbed for T6 /
  T16. Unknown values fall back to platform default so a typo
  never silently routes through an unintended backend.

- Daemon lifespan wiring: app.state.master_key set right after
  app.state.audit_chain. Failure is non-fatal — startup_diagnostics
  carries 'degraded' status and the daemon proceeds with at-rest
  encryption disabled. A future T6 will add a strict-mode flag.

- 16 new tests in test_master_key.py mirroring test_operator_key.py:
  first-call generation, randomness sanity, second-call idempotency,
  namespace isolation (master / agent / operator coexist without
  collision), reserved-name format lock, explicit-store bypass,
  persistence across simulated daemon restart, corruption handling
  (malformed base64 / wrong length must raise, never silently
  regenerate), and env-var routing (5 cases).

What's NOT in T1:

- SQLCipher integration (T2)
- Audit chain encryption (T3)
- Memory body encryption (T4)
- File encryption (T5)
- Passphrase prompt or HSM backend (T6 / T16)
- CLI fsf encrypt family (T8)
- Runbook (T7)

Substrate is INERT until T2-T4 land. Operators see master_key
loaded in startup_diagnostics but no behavior change in dispatch /
audit / memory paths. The trusted-host model is unchanged."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 266 complete — ADR-0050 T1 master-key substrate shipped ==="
echo "Press any key to close."
read -n 1
