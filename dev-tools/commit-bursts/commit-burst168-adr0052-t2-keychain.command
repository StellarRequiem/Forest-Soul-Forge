#!/bin/bash
# Burst 168 — ADR-0052 T2 — KeychainStore for macOS Keychain
# integration. Operators on a Mac get the OS-native production-
# grade backend without any configuration; non-macOS hosts
# continue to default to FileStore (which fires its INSECURE
# banner).
#
# What ships:
#
#   src/forest_soul_forge/security/secrets/keychain_store.py:
#     KeychainStore — wraps `security` CLI (find-generic-password,
#     add-generic-password -U, delete-generic-password,
#     dump-keychain). Service prefix `forest-soul-forge:` so an
#     auditing operator can grep / inspect Forest entries via
#     Keychain Access without sifting through Wi-Fi passwords.
#
#     Wire format:
#       service: "forest-soul-forge:<secret_name>"
#       account: "forest-soul-forge"
#       value:   the secret string (UTF-8)
#
#     Defenses + behaviors:
#       - Constructor refuses non-Darwin with a clear actionable
#         error pointing operators at file or vaultwarden backends
#       - Name allowlist (alnum + _ - .) — rejects shell metachars
#         BEFORE invoking security so a misconfigured constitution
#         can't smuggle ';' or '`' through to the subprocess
#       - rc=44 (SecKeychainItemNotFound) → None for get(); no-op
#         for delete() (matches the ADR-0052 contract that
#         delete-of-absent is idempotent)
#       - Other nonzero rc → SecretStoreError carrying the
#         captured stderr so an operator can debug locked-keychain
#         / permission-denied situations
#       - put() uses `-U` upsert flag so re-storing the same name
#         overwrites rather than failing on duplicate
#       - list_names() parses dump-keychain output, filters on
#         service prefix; documented limitation that locked
#         keychains hang the call until timeout
#
#     Documented threat-model note: argv-exposure of secret values
#     during put() is a sub-second window visible via ps. Operators
#     concerned about that exposure use VaultWardenStore (T3, next
#     tranche) — secrets travel as HTTPS request bodies, not argv.
#
#   src/forest_soul_forge/security/secrets/resolver.py:
#     - Imports KeychainStore + the platform module
#     - _platform_default() helper picks `keychain` on Darwin and
#       `file` everywhere else. The default flip means a fresh
#       Forest install on a Mac gets OS-native storage out of the
#       box; on Linux/Windows the FileStore continues to fire its
#       INSECURE banner so operators know to install a vault
#     - The previous T1 'keychain → SecretStoreError' stub now
#       returns KeychainStore() instead
#
#   src/forest_soul_forge/security/secrets/__init__.py:
#     Adds KeychainStore to the public surface re-exports.
#
# Tests:
#
#   tests/unit/test_keychain_store.py — 17 unit tests + a
#   live-on-macOS class (4 tests skipped in Linux CI):
#
#     - constructor refuses non-Darwin (mocked platform.system)
#     - constructor succeeds on Darwin → SecretStoreProtocol
#       satisfaction
#     - _valid_name allowlist: accepts alnum + _ - .; refuses
#       space, $, /, \\, ;, `
#     - get() argv shape: 'security find-generic-password -a
#       <ACCOUNT> -s <SERVICE_PREFIX>+<name> -w'
#     - get() rc=44 → None; other nonzero → SecretStoreError
#     - get() strips trailing newline from security -w output
#     - get() invalid name rejected before subprocess fires
#     - put() argv has -U upsert flag; value lands at argv after
#       -w
#     - put() raises on subprocess failure; rejects non-string
#       value; rejects empty name
#     - delete() rc=44 = no-op (idempotent); rc=0 succeeds; other
#       nonzero raises
#     - list_names() parses dump-keychain output + filters on
#       SERVICE_PREFIX (test fixture covers Forest entry, non-
#       Forest entry, NULL svce edge case)
#     - list_names() raises on dump failure
#
#     TestKeychainStoreLive — real-keychain conformance smoke
#     gated behind @skipif(platform != Darwin). put/get/delete/
#     list contract roundtrip. Cleans up after itself in
#     teardown_method. Will fire when operator runs the suite
#     on the Mac.
#
#   tests/unit/test_secret_store_resolver.py:
#     - test_default_resolves_to_file_store renamed to
#       test_default_resolves_to_platform_backend; on Darwin
#       expects keychain, elsewhere expects file
#     - test_keychain_raises_with_pointer_to_t2 renamed to
#       test_keychain_on_darwin_resolves; verifies T2 actually
#       constructs KeychainStore on Darwin and surfaces the
#       macOS-only SecretStoreError on Linux
#
# Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
# changes. New module + resolver wiring; no schema migrations,
# no new HTTP endpoints, no new audit-chain event types.
#
# Verification:
#   PYTHONPATH=src pytest tests/unit/test_keychain_store.py
#                                tests/unit/test_secret_store_resolver.py
#                                tests/unit/test_secret_store_conformance.py
#   -> 47 passed, 4 macOS-only skips (the live integration class)
#
# Manual smoke on operator's Mac (after pulling B168):
#   FSF_SECRET_STORE=keychain python3 -c "
#   from forest_soul_forge.security.secrets import resolve_secret_store
#   s = resolve_secret_store(force_reload=True)
#   s.put('fsf_smoke', 'hello')
#   print(s.get('fsf_smoke'))   # expects 'hello'
#   print('fsf_smoke' in s.list_names())   # expects True
#   s.delete('fsf_smoke')
#   "
#   Verify in Keychain Access: there should be a "forest-soul-
#   forge:fsf_smoke" entry briefly + then it disappears.
#
# Remaining ADR-0052 tranches:
#   T3 VaultWardenStore (HTTPS client + config-file loader)
#   T4 plugin_loader integration + audit events
#   T5 fsf secret CLI
#   T6 settings UI surface

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/secrets/__init__.py \
        src/forest_soul_forge/security/secrets/resolver.py \
        src/forest_soul_forge/security/secrets/keychain_store.py \
        tests/unit/test_keychain_store.py \
        tests/unit/test_secret_store_resolver.py \
        dev-tools/commit-bursts/commit-burst168-adr0052-t2-keychain.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(secrets): ADR-0052 T2 — KeychainStore (B168)

Burst 168. Second ADR-0052 tranche. macOS-native production-grade
secret storage via the system 'security' CLI. Operators on a Mac
get OS-integrated storage out of the box (resolver default flips
to keychain on Darwin); non-macOS hosts continue with FileStore +
its INSECURE banner.

Ships KeychainStore wrapping 'security find-generic-password',
'add-generic-password -U', 'delete-generic-password',
'dump-keychain'. Service prefix 'forest-soul-forge:' so an
auditing operator can grep Forest entries in Keychain Access.

Defenses:
- Name allowlist (alnum + _-.) rejects shell metachars before
  invoking security
- rc=44 (SecKeychainItemNotFound) returns None for get(), no-op
  for delete() per the ADR-0052 contract
- put() uses -U upsert flag for idempotent overwrites
- Constructor refuses non-Darwin with platform-only error pointing
  at file/vaultwarden alternatives

Documented threat-model note: put() exposes the value via argv
briefly. Operators wanting argv-free transport use VaultWardenStore
(T3 next).

Tests: 17 unit tests with mocked subprocess + 4 live-on-macOS
integration tests gated behind platform skipif. Resolver tests
updated for the platform-default flip + T2 stub removal.

Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI changes.

Verification: 47 passed, 4 macOS-only skips.

Remaining ADR-0052 tranches:
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
echo "=== Burst 168 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
