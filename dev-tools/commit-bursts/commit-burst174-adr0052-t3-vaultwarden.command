#!/bin/bash
# Burst 174 — ADR-0052 T3 — VaultWardenStore via bw CLI. Closes
# the final ADR-0052 implementation tranche. Full pluggable
# secret-store arc (T1-T6) is now shipped end-to-end.
#
# Mirrors KeychainStore design: subprocess CLI wrapper rather than
# reimplementing Bitwarden master-key + SRP login flow from scratch.
# Operators with VaultWarden / Bitwarden running locally get
# integration without Forest carrying a parallel Bitwarden client.
#
# What ships:
#
#   src/forest_soul_forge/security/secrets/vaultwarden_store.py:
#     VaultWardenStore — wraps bw CLI commands:
#       - bw get item <prefix><name>   — JSON item; extract login.password
#       - bw create item <base64-json> — when item absent
#       - bw edit item <id> <base64-json> — when item present (upsert)
#       - bw delete item <id>          — idempotent on rc=4 / Not found
#       - bw list items --search <prefix> — server-side filter
#       - bw sync                      — durability after writes
#
#     Service prefix "forest-soul-forge:" matches KeychainStore so
#     operators switching backends never have to rename secrets.
#     Login.password field stores the value (encrypted by Bitwarden
#     at rest); notes is a fallback for hand-edited items.
#
#     Threat-model improvement over KeychainStore: values travel
#     via base64-encoded JSON on argv (bw create/edit syntax),
#     never as plaintext argv. KeychainStore's `security add -w
#     VALUE` exposes the value briefly via ps; VaultWardenStore
#     does not.
#
#     Operator setup documented in module docstring:
#       1. npm install -g @bitwarden/cli
#       2. (optional) bw config server https://vault.example.com
#       3. bw login
#       4. export BW_SESSION=$(bw unlock --raw)
#       5. set FSF_SECRET_STORE=vaultwarden
#
#     Constructor refuses when bw isn't on PATH. Surfaces a
#     stderr warning when BW_SESSION isn't set so the most
#     common 'why does this not work' case has a discoverable
#     hint. Common bw errors mapped to actionable
#     SecretStoreError messages:
#       - "Vault is locked." → bw unlock pointer
#       - "Not found." → None per the contract
#
#   src/forest_soul_forge/security/secrets/resolver.py:
#     The previous T3 stub (raised SecretStoreError pointing at
#     T3) now returns VaultWardenStore() instead.
#
#   src/forest_soul_forge/security/secrets/__init__.py:
#     Adds VaultWardenStore to the public surface re-exports.
#
# Tests:
#
#   tests/unit/test_vaultwarden_store.py — 23 unit tests + 2
#   live-on-operator-host tests:
#
#     Constructor: refuses when bw missing; warns when
#     BW_SESSION unset.
#
#     _valid_name allowlist: matches KeychainStore (alnum + _-.).
#
#     get(): argv shape (bw get item prefix+name); rc=4 → None;
#     "Not found." in stderr → None; "Vault is locked." → SecretStoreError
#     with bw unlock pointer; parses login.password from JSON;
#     falls back to notes; returns None when neither set; rejects
#     bad name chars before subprocess fires.
#
#     put(): creates when absent (3 subprocess calls: list / create /
#     sync); edits when present (list / edit / sync); payload is
#     base64-encoded JSON with type=1 (Login) + login.password set;
#     rejects non-string values + empty names + bad name chars;
#     locked-vault errors map to bw unlock pointer.
#
#     delete(): idempotent on absent (only the find_item_id list
#     fires; no bw delete invocation); invokes bw delete with the
#     correct item id when present; idempotent on race (rc=1
#     "Not found." doesn't raise).
#
#     list_names(): filters server-side via --search; strips
#     SERVICE_PREFIX from each name; defensive against substring
#     hits where the prefix appears mid-name (only entries
#     starting with the prefix count); locked-vault → SecretStoreError.
#
#     TestVaultWardenStoreLive: gated behind bw-on-PATH +
#     BW_SESSION set. put/get/list roundtrip on a real vault;
#     cleans up after itself. Skipped in CI / Linux sandbox.
#
#   tests/unit/test_secret_store_resolver.py:
#     Renamed test_vaultwarden_raises_with_pointer_to_t3 to
#     test_vaultwarden_resolves_or_errors_on_missing_bw which
#     branches on shutil.which("bw"): on hosts with bw, expects
#     successful resolve; on hosts without, expects clear error
#     pointing at install path.
#
# Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
# changes. New userspace module + resolver wiring; no schema
# migrations; no new HTTP endpoints; no new audit-chain event
# types.
#
# Verification:
#   PYTHONPATH=src pytest tests/unit/test_vaultwarden_store.py
#                                tests/unit/test_secret_store_resolver.py
#                                tests/unit/test_secret_store_conformance.py
#                                tests/unit/test_keychain_store.py
#                                tests/unit/test_cli_secret_cmd.py
#                                tests/unit/test_secrets_router.py
#   -> 90 passed, 6 platform-gated skips
#
# Manual smoke (operator's host):
#   $ npm install -g @bitwarden/cli
#   $ bw login
#   $ export BW_SESSION=$(bw unlock --raw)
#   $ FSF_SECRET_STORE=vaultwarden fsf secret put smoke_test
#   value for 'smoke_test': [hidden]
#   stored 'smoke_test' via backend=vaultwarden
#   $ FSF_SECRET_STORE=vaultwarden fsf secret list
#   smoke_test
#   $ FSF_SECRET_STORE=vaultwarden fsf secret get smoke_test --reveal
#   [the value]
#   Verify in VaultWarden web UI: there's a "forest-soul-forge:
#   smoke_test" Login item under the operator's account.
#
# ADR-0052 IMPL COMPLETE. All six tranches plus T4 audit-trail
# follow-up shipped:
#   T1 B167 — Protocol + FileStore + resolver
#   T2 B168 — KeychainStore
#   T3 B174 — VaultWardenStore (this commit)
#   T4 B170 — plugin loader integration
#   T4 audit B171 — required_secrets_resolved metadata via ToolResult
#   T5 B169 — fsf secret CLI
#   T6 B173 — chat-tab Secrets card

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/secrets/__init__.py \
        src/forest_soul_forge/security/secrets/resolver.py \
        src/forest_soul_forge/security/secrets/vaultwarden_store.py \
        tests/unit/test_vaultwarden_store.py \
        tests/unit/test_secret_store_resolver.py \
        dev-tools/commit-bursts/commit-burst174-adr0052-t3-vaultwarden.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(secrets): ADR-0052 T3 — VaultWardenStore (B174)

Burst 174. Closes the final ADR-0052 tranche. Full pluggable
secret-store arc (T1-T6) is now shipped end-to-end.

VaultWardenStore wraps the Bitwarden bw CLI. Mirrors KeychainStore
design (subprocess CLI wrapper) rather than reimplementing
Bitwarden master-key + SRP login flow.

Operations:
- get: bw get item <prefix><name>; parses login.password
- put: list to find existing; create or edit with base64-encoded
  JSON payload; bw sync for durability
- delete: find_item_id then bw delete; idempotent on absent
- list_names: bw list items --search <prefix>; strips prefix

Service prefix forest-soul-forge: matches KeychainStore so
operators switching backends never rename secrets. Login.password
field holds the value (encrypted by Bitwarden at rest).

Threat-model improvement over KeychainStore: values travel via
base64 JSON on argv (bw create/edit), never as plaintext argv.

Operator setup documented in the module docstring:
1. npm install -g @bitwarden/cli
2. bw login (and bw config server for self-hosted VaultWarden)
3. export BW_SESSION=\$(bw unlock --raw)
4. FSF_SECRET_STORE=vaultwarden

Common bw errors mapped:
- Vault is locked → SecretStoreError with bw unlock pointer
- Not found → None per contract (idempotent delete; missing get)

Tests: 23 unit tests via mocked subprocess + 2 live-on-operator-
host tests gated behind bw-on-PATH + BW_SESSION set. Resolver
test updated to branch on shutil.which.

Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
changes.

Verification: 90 passed, 6 platform-gated skips.

ADR-0052 IMPL COMPLETE:
- T1 B167 Protocol + FileStore + resolver
- T2 B168 KeychainStore
- T3 B174 VaultWardenStore (this commit)
- T4 B170 plugin loader integration
- T4 audit B171 required_secrets_resolved via ToolResult metadata
- T5 B169 fsf secret CLI
- T6 B173 chat-tab Secrets card"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 174 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
