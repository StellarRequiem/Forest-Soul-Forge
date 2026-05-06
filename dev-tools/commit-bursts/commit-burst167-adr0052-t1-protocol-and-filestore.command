#!/bin/bash
# Burst 167 — ADR-0052 T1 — SecretStoreProtocol + FileStore +
# resolve_secret_store(). First implementation tranche of the
# pluggable-secrets-storage arc designed in ADR-0052 (B162).
#
# What ships:
#
#   src/forest_soul_forge/security/secrets/__init__.py:
#     Public surface re-exports: FileStore, SecretStoreError,
#     SecretStoreProtocol, resolve_secret_store.
#
#   src/forest_soul_forge/security/secrets/protocol.py:
#     - SecretStoreError exception (backend failure, distinct from
#       "secret not found")
#     - SecretStoreProtocol — @runtime_checkable Protocol covering
#       get / put / delete / list_names + a name ClassVar string.
#       Backends that pass the conformance test suite (below) plug
#       into the loader without further glue.
#
#   src/forest_soul_forge/security/secrets/file_store.py:
#     FileStore — plaintext YAML at ~/.forest/secrets/secrets.yaml
#     (overridable via FSF_FILE_SECRETS_PATH). INSECURE by design;
#     for CI / sandbox / spin-up-before-vault use. Defenses:
#       - chmod-600 enforced on reads (0o077 mode bits → refuse +
#         pointer at `chmod 600 {path}`)
#       - chmod 600 set on writes (atomic temp-then-rename so a
#         crashed daemon doesn't leave a half-written file)
#       - parent dir chmodded 0o700 on first write
#       - non-string YAML values raise rather than silently coerce
#         (catches hand-edits putting a number/list in the file)
#       - first-touch INSECURE warning to stderr (FileStore._warned
#         class flag — once per process)
#     Forest never logs values; logs the secret_name + backend
#     identifier per audit-chain conventions.
#
#   src/forest_soul_forge/security/secrets/resolver.py:
#     resolve_secret_store() — reads FSF_SECRET_STORE env var.
#     Recognized values:
#       file        → FileStore (default, T1 implements)
#       keychain    → SecretStoreError pointing at T2
#       vaultwarden → SecretStoreError pointing at T3
#       module:<dotted.path.Class> → BYO. importlib + structural
#         SecretStoreProtocol check + clean errors for
#         module-not-importable / class-not-in-module / class-doesn't-
#         satisfy-protocol.
#     Per-process cache (threading.Lock-guarded dict). Test seam
#     _reset_cache_for_tests() lets tests re-resolve after mutating
#     the env var.
#
#   tests/unit/test_secret_store_conformance.py (NEW):
#     TestSecretStoreConformance — shared contract every backend
#     must pass. T1 exercises FileStore; T2/T3 will parameterize
#     the same class against KeychainStore + VaultWardenStore so
#     a regression in any backend trips the same assertion line.
#     10 contract tests + 7 FileStore-specific tests (chmod
#     enforcement, env-var path, malformed YAML, non-string-value
#     handling, default-path tilde expansion).
#
#   tests/unit/test_secret_store_resolver.py (NEW):
#     13 tests covering: default → file, explicit file, whitespace
#     stripping, T2/T3 stubs raising with tranche pointers,
#     unknown-backend rejection, BYO module-path success path,
#     BYO failures (no dot, module-not-importable, class-not-found,
#     class-doesn't-satisfy-protocol), per-process cache + force-
#     reload bypass.
#
# What does NOT ship in T1 (queued for later tranches):
#   - T2: KeychainStore (macOS `security` CLI wrapper)
#   - T3: VaultWardenStore (HTTPS client + config-file loader)
#   - T4: plugin_loader integration (currently the loader's
#     secret-resolution path uses the pre-existing SecretsAccessor;
#     T4 swaps it to call resolve_secret_store() and emits the
#     secret_resolved audit event)
#   - T5: `fsf secret put|get|delete|list|backend` CLI
#   - T6: Settings UI (chat-tab assistant settings panel surfaces
#     the active backend + secret list)
#
# Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
# changes. The work lives in src/forest_soul_forge/security/secrets/
# as a new userspace module. No schema migrations. No new HTTP
# endpoints (T5 will add CLI; T6 will add UI). No new audit-chain
# event types yet (T4 emits secret_resolved using the existing
# event_data shape).
#
# Verification:
#   PYTHONPATH=src pytest tests/unit/test_secret_store_conformance.py
#                                tests/unit/test_secret_store_resolver.py
#                                tests/unit/test_soulux_computer_control_server.py
#                                tests/unit/test_example_plugins.py
#                                tests/unit/test_posture_gate_step.py
#                                tests/unit/test_conversation_helpers.py
#                                tests/unit/test_trait_engine.py
#                                tests/unit/test_genre_engine.py
#                                tests/unit/test_constitution.py
#                                tests/unit/test_tool_catalog.py
#                                tests/unit/test_registry_concurrency.py
#   -> 343 passed, 2 macOS-only skips
#
# T1 scope deliberately tight — protocol + reference + resolver
# + conformance test seam. T2/T3 stack onto this without
# re-shaping the public API.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/secrets/ \
        tests/unit/test_secret_store_conformance.py \
        tests/unit/test_secret_store_resolver.py \
        dev-tools/commit-bursts/commit-burst167-adr0052-t1-protocol-and-filestore.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(secrets): ADR-0052 T1 — SecretStoreProtocol + FileStore (B167)

Burst 167. First implementation tranche of ADR-0052 (pluggable
secrets storage). Lands the protocol + reference backend + resolver
that T2/T3/T4/T5/T6 stack onto without re-shaping the public API.

Ships src/forest_soul_forge/security/secrets/:
- protocol.py: SecretStoreProtocol (@runtime_checkable) + name
  ClassVar + SecretStoreError. Contract: get/put/delete/list_names.
- file_store.py: FileStore — plaintext YAML at
  ~/.forest/secrets/secrets.yaml (overridable via
  FSF_FILE_SECRETS_PATH). INSECURE by design; for CI / sandbox /
  spin-up-before-vault. Defenses: chmod-600 enforcement on read
  (refuses 0o077 mode bits with chmod-pointer error), atomic
  temp+rename writes that always land 0o600, parent dir 0o700,
  non-string YAML values raise rather than coerce, first-touch
  INSECURE warning to stderr.
- resolver.py: resolve_secret_store() reads FSF_SECRET_STORE env
  var. file → FileStore (default). keychain/vaultwarden → SecretStoreError
  with tranche pointers (T2/T3 will replace stubs). module:<dotted.path>
  → BYO via importlib + structural Protocol check. Per-process cache;
  force_reload test seam.

Test infrastructure:
- TestSecretStoreConformance (test_secret_store_conformance.py) —
  shared contract every backend must pass (10 contract tests).
  T2/T3 parameterize the same class against their fixtures.
- TestFileStoreSpecific (same file) — 7 FileStore-specific tests
  for chmod enforcement, env-var path, malformed YAML, etc.
- test_secret_store_resolver.py — 13 tests: defaults, explicit
  backend selection, whitespace stripping, T2/T3 stub errors,
  unknown-backend rejection, BYO success/failure modes, cache
  behavior.

Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
changes. New userspace module. No schema migrations, no new HTTP
endpoints, no new audit-chain event types (T4 will emit
secret_resolved using the existing event_data shape).

Verification: 343 passed across all touched modules.

Remaining ADR-0052 tranches:
- T2 KeychainStore (macOS security CLI wrapper)
- T3 VaultWardenStore (HTTPS client)
- T4 plugin_loader integration + audit events
- T5 fsf secret CLI
- T6 settings UI surface"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 167 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
