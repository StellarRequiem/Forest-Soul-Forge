#!/bin/bash
# Burst 169 — ADR-0052 T5 — `fsf secret` CLI. Closes the operator-
# facing surface for managing pluggable secrets. Combined with T1
# (FileStore) + T2 (KeychainStore), operators can now CRUD secrets
# through a friendly command-line surface against whichever
# backend FSF_SECRET_STORE selects.
#
# What ships:
#
#   src/forest_soul_forge/cli/secret_cmd.py — new CLI module:
#     fsf secret put <name>     # prompts for value via getpass
#                                 (no echo); --from-stdin pipe-mode
#     fsf secret get <name>     # masked print (first 4 + last 4
#                                 for >12 chars; full asterisks for
#                                 short tokens); --reveal prints
#                                 plaintext (no trailing newline,
#                                 pipe-friendly)
#     fsf secret delete <name>  # confirmation prompt; --yes skips
#                                 (idempotent — delete-of-absent
#                                 succeeds per the ADR-0052 contract)
#     fsf secret list           # names sorted, no values; empty
#                                 state shows actionable hint
#     fsf secret backend        # active store + selection source
#                                 (explicit env var vs. platform
#                                 default) + backend-specific config
#                                 (file path / keychain prefix)
#
#     Talks DIRECTLY to the resolved SecretStoreProtocol — no
#     daemon HTTP round-trip. Per ADR-0052 §"CLI uses the SAME
#     protocol as the loader — no special operator-escalation
#     path." If the backend fails, the CLI fails with the same
#     error the loader would see at plugin launch.
#
#     Exit codes (matches the existing fsf agent_cmd pattern):
#       0 success
#       1 operator-cancelled (Ctrl-C, EOF on prompt)
#       4 bad-argument (empty value, etc.)
#       5 backend SecretStoreError on operation
#       6 secret not found (get of unknown name)
#       7 backend not available (resolver raised)
#
#   src/forest_soul_forge/cli/main.py:
#     Registers the new subparser via the established
#     `add_subparser(sub)` pattern (matches plugin_cmd, agent_cmd,
#     etc.).
#
# Tests:
#
#   tests/unit/test_cli_secret_cmd.py — 16 tests:
#     - backend command: shows active store; identifies platform
#       default vs. explicit env-var selection
#     - put: stdin path + prompt path; rejects empty value (both
#       paths); --from-stdin strips trailing newline
#     - get: unknown returns rc=6 with actionable hint; default
#       output masks the value (first 4 + last 4 for long secrets;
#       full asterisks for ≤12 chars); --reveal prints plaintext
#       with no trailing newline (pipe-friendly)
#     - delete: --yes skips prompt; without --yes the input prompt
#       fires (test mocks input); 'no' answer aborts; missing-name
#       deletion is idempotent (rc=0 with "deleted (or already
#       absent)" message)
#     - list: empty state shows actionable hint; populated state
#       prints names sorted alphabetically
#
#   isolated _isolated_secret_store fixture: each test gets its own
#   FileStore at tmp_path with a freshly-cleared resolver cache so
#   tests don't leak state into each other.
#
# What does NOT ship in T5:
#   - Audit-chain emission for secret_put / secret_resolved /
#     secret_delete events. T4 (loader integration) wires the
#     audit callback into the resolver path; until then CLI ops
#     don't emit chain entries. This is the §0 Hippocratic-gate
#     order — landing the surface before the audit hook means
#     the audit hook lands ONCE in T4 against a stable surface
#     rather than getting added in T5 + reshaped in T4.
#
# Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
# changes. New userspace CLI module; no schema migration, no new
# HTTP endpoints, no new audit-chain events (yet — coming in T4).
#
# Verification:
#   PYTHONPATH=src pytest tests/unit/test_cli_secret_cmd.py
#                                tests/unit/test_keychain_store.py
#                                tests/unit/test_secret_store_resolver.py
#                                tests/unit/test_secret_store_conformance.py
#   -> 62 passed, 4 macOS-only skips
#
# Manual smoke (operator's Mac):
#   $ fsf secret backend
#   backend: keychain
#   selected via: platform default for 'Darwin'
#   service prefix: forest-soul-forge:
#   ...
#
#   $ fsf secret put openai_key
#   value for 'openai_key': [hidden — getpass prompt]
#   stored 'openai_key' via backend=keychain
#
#   $ fsf secret list
#   openai_key
#
#   $ fsf secret get openai_key
#   openai_key: sk-0…cdef (51 chars)  (use --reveal to print plaintext)
#
#   $ fsf secret delete openai_key --yes
#   deleted 'openai_key' (or already absent) from backend=keychain
#
# Remaining ADR-0052 tranches:
#   T3 VaultWardenStore (HTTPS client + config-file loader)
#   T4 plugin_loader integration + audit events
#   T6 settings UI surface (chat-tab assistant settings panel
#      surfaces the active backend + secret list)

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/cli/secret_cmd.py \
        src/forest_soul_forge/cli/main.py \
        tests/unit/test_cli_secret_cmd.py \
        dev-tools/commit-bursts/commit-burst169-adr0052-t5-cli.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(cli): ADR-0052 T5 — fsf secret CLI (B169)

Burst 169. Operator-facing put/get/delete/list/backend subcommands
for the ADR-0052 pluggable secret store. Combined with T1
(FileStore) + T2 (KeychainStore), operators can now CRUD secrets
via a friendly CLI against whichever backend FSF_SECRET_STORE
selects.

Ships:
- src/forest_soul_forge/cli/secret_cmd.py — argparse subparsers
  for put/get/delete/list/backend. Talks directly to the resolved
  SecretStoreProtocol (no daemon round-trip). Per ADR-0052
  §'CLI uses the SAME protocol as the loader.'
- get masks values by default (first 4 + last 4 for >12 chars;
  full asterisks for ≤12 chars); --reveal prints plaintext with
  no trailing newline for pipe-friendly composition.
- delete confirmation prompt with --yes bypass; idempotent (per
  the ADR-0052 contract).
- backend command identifies active store + selection source +
  backend-specific config (file path / keychain prefix) for
  debugging.
- src/forest_soul_forge/cli/main.py — registers the new
  subparser via the established add_subparser(sub) pattern.

Test coverage: 16 tests via capsys + monkeypatched getpass /
input / sys.stdin / FSF_FILE_SECRETS_PATH. Each test gets its
own FileStore at tmp_path with a freshly-cleared resolver cache.

Audit-chain emission for secret_put / secret_resolved /
secret_delete will land in T4 (loader integration) — wiring it
through the resolver path keeps the audit hook a single change
against a stable CLI surface.

Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
changes.

Verification: 62 passed, 4 macOS-only skips.

Remaining ADR-0052 tranches:
- T3 VaultWardenStore (HTTPS client)
- T4 plugin_loader integration + audit events
- T6 settings UI surface"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 169 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
