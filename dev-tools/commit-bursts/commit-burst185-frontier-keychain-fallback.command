#!/bin/bash
# Burst 185 — frontier provider keychain-fallback + Anthropic
# setup script.
#
# Operator wants the Anthropic API key stored once via the secrets
# store (Keychain on macOS by default) and the daemon to read it at
# lifespan rather than via FSF_FRONTIER_API_KEY env (which would
# require either plaintext .env or launchd-plist injection — both
# uncomfortable for a real key).
#
# What ships:
#
#   src/forest_soul_forge/daemon/config.py:
#     - frontier_base_url docstring updated with the Anthropic
#       compatibility-endpoint hint ('https://api.anthropic.com/v1').
#     - NEW field: frontier_api_key_secret_name (default
#       'anthropic_api_key'). Operators store under this name via
#       `fsf secret put`; the daemon looks it up at lifespan when
#       FSF_FRONTIER_API_KEY env var is unset. Configurable for
#       OpenAI / xAI / etc. installs.
#
#   src/forest_soul_forge/daemon/app.py:
#     - _build_provider_registry now resolves the frontier API key
#       in three steps: env (FSF_FRONTIER_API_KEY) -> secrets store
#       (resolve_secret_store().get(secret_name)) -> None. Failure
#       to read the secrets store (e.g. Keychain locked, Vault-
#       Warden offline) NEVER crashes the daemon; the FrontierProvider
#       just reports 'no API key configured' on dispatch.
#
#   dev-tools/check-frontier-provider.command (NEW):
#     - Read-only diagnostic. Prints provider state (without ever
#       revealing the key) so the operator can see whether the
#       frontier provider initialized correctly.
#
#   dev-tools/setup-anthropic-frontier.command (NEW):
#     - Interactive setup. Backs up .env, patches in
#       FSF_FRONTIER_ENABLED=true + base_url=https://api.anthropic.com/v1
#       + model=claude-sonnet-4-6 + FSF_SECRET_STORE=keychain (if
#       unset). Prompts operator for the API key with `read -s`
#       (no echo). Stores via `fsf secret put anthropic_api_key
#       --from-stdin` so the key never touches argv. Restarts the
#       daemon via launchctl kickstart. Verifies via /runtime/provider.
#       Idempotent — re-running updates the model or rotates the
#       key cleanly.
#
# Per ADR-0052 D2: secret store is the source of truth; env is the
# fallback. B185 inverts the env-first / store-fallback ordering
# from pre-B185 — operators now store keys in Keychain by default
# and only need to inject via env in CI / headless containers.
#
# Per ADR-0001 D2: no agent identity touched. Provider construction
# is identity-agnostic substrate.
#
# Note on daemon behavior: pre-B185 daemons reading post-B185 .env
# files just ignore the new FSF_FRONTIER_API_KEY_SECRET_NAME env
# variable. Post-B185 daemons reading pre-B185 .env files behave
# identically to pre-B185 (env-only frontier API key) because the
# default secret_name 'anthropic_api_key' lookup just returns None
# when nothing's in the store. Both directions backward-compat.
#
# Verification:
#   PYTHONPATH=src:. python3 -c "
#     from forest_soul_forge.daemon.app import build_app, _build_provider_registry;
#     from forest_soul_forge.daemon.config import DaemonSettings;
#     s = DaemonSettings(frontier_enabled=True);
#     _build_provider_registry(s)"
#   -> exits 0
#
#   PYTHONPATH=src:. pytest tests/unit/test_tool_dispatcher.py -q
#   -> 53 passed
#
# Operator-facing follow-up (NOT in this commit):
#   1. Run dev-tools/setup-anthropic-frontier.command
#   2. Paste API key when prompted
#   3. Verify via dev-tools/check-frontier-provider.command

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/config.py \
        src/forest_soul_forge/daemon/app.py \
        dev-tools/check-frontier-provider.command \
        dev-tools/setup-anthropic-frontier.command \
        dev-tools/commit-bursts/commit-burst185-frontier-keychain-fallback.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(secrets): frontier API key fallback to secret store (B185)

Burst 185. The frontier provider now resolves its API key in three
steps at daemon lifespan: env var FSF_FRONTIER_API_KEY -> resolved
secret store (Keychain on macOS by default) -> None. Operators
store the key once via 'fsf secret put anthropic_api_key' and the
daemon picks it up on every restart. Failure to read the secrets
store never crashes the daemon — the FrontierProvider reports 'no
API key configured' on dispatch.

Per ADR-0052 D2: secret store is the source of truth; env is the
fallback. B185 inverts the env-first / store-fallback ordering
from pre-B185.

Ships:

config.py: NEW frontier_api_key_secret_name field (default
'anthropic_api_key'). Configurable for OpenAI / xAI / etc.
deployments. Updated frontier_base_url docstring with the
Anthropic compatibility-endpoint hint.

app.py: _build_provider_registry resolves the API key via env ->
secrets store, only constructs FrontierProvider with whatever
api_key resolved (None is a valid 'unset' state — the provider
reports the right error).

check-frontier-provider.command (NEW): read-only diagnostic.
Prints provider state without ever revealing the key value.

setup-anthropic-frontier.command (NEW): interactive setup. Patches
.env (FSF_FRONTIER_ENABLED + base_url + model + secret store
backend), prompts operator for the API key with read -s (no
echo), stores via fsf secret put --from-stdin so the key never
touches argv, restarts the daemon, verifies via /runtime/provider.
Idempotent.

Per ADR-0001 D2: no agent identity touched. Pre-B185 .env files
work unchanged with post-B185 daemons; post-B185 .env files work
unchanged with pre-B185 daemons (the new secret_name field is
just ignored).

Verification: build_app() imports clean, registry construction
with frontier_enabled=True succeeds, test_tool_dispatcher 53/53
pass.

Operator-facing follow-up (NOT in this commit):
1. Run dev-tools/setup-anthropic-frontier.command
2. Paste API key when prompted
3. Verify via dev-tools/check-frontier-provider.command"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 185 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
