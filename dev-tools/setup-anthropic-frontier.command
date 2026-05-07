#!/bin/bash
# One-shot operator-driven setup for the Anthropic-via-frontier path.
# Updates .env, prompts for the API key (no echo), stores it in the
# resolved secrets backend (Keychain on macOS by default), restarts
# the daemon, verifies the frontier provider initialized.
#
# After B185, the daemon's _build_provider_registry pulls the
# frontier API key from the secrets store at startup when
# FSF_FRONTIER_API_KEY isn't set in env. So this script never writes
# the key to disk in cleartext — only the env-toggle + base URL +
# model land in .env; the key itself goes to Keychain.

set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE="$(pwd)/.env"
VENV_PY="$(pwd)/.venv/bin/python"
FSF_BIN="$(pwd)/.venv/bin/fsf"

echo "=========================================================="
echo "Forest — Anthropic frontier setup"
echo "=========================================================="
echo

# ---------------------------------------------------------------------------
# 1. Sanity checks before we touch anything.
# ---------------------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: no .env at $ENV_FILE — cowork session may not be"
  echo "       sourced from the expected directory."
  exit 1
fi
if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: no .venv/bin/python at $VENV_PY — daemon venv missing."
  exit 1
fi
if [ ! -x "$FSF_BIN" ]; then
  echo "INFO: no .venv/bin/fsf console script — using python -m forest_soul_forge.cli.main"
  FSF_BIN=""
fi

# ---------------------------------------------------------------------------
# 2. Backup current .env.
# ---------------------------------------------------------------------------
TS=$(date +%Y%m%d-%H%M%S)
cp "$ENV_FILE" "$ENV_FILE.bak-$TS"
echo "[1/6] Backed up .env -> .env.bak-$TS"

# ---------------------------------------------------------------------------
# 3. Compute the frontier env block. Skip lines already present so a
#    re-run is idempotent.
# ---------------------------------------------------------------------------
echo "[2/6] Patching .env with frontier toggles (no API key written here)"
add_if_missing() {
  local key="$1"
  local val="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    # Update in place so a re-run can change the model.
    # macOS sed needs the empty -i argument.
    sed -i.tmpbak "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    rm -f "$ENV_FILE.tmpbak"
    echo "      updated  $key=$val"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
    echo "      added    $key=$val"
  fi
}

add_if_missing "FSF_FRONTIER_ENABLED"           "true"
# IMPORTANT: do NOT include /v1 here — the FrontierProvider already
# appends /v1/chat/completions internally. Including /v1 in the
# base URL produces a double-prefix .../v1/v1/chat/completions and
# Anthropic returns 404. Validated 2026-05-07 via
# smoke-test-frontier.command.
add_if_missing "FSF_FRONTIER_BASE_URL"          "https://api.anthropic.com"
add_if_missing "FSF_FRONTIER_MODEL"             "claude-sonnet-4-6"
add_if_missing "FSF_FRONTIER_MODEL_GENERATE"    "claude-sonnet-4-6"
add_if_missing "FSF_FRONTIER_MODEL_CONVERSATION" "claude-sonnet-4-6"
add_if_missing "FSF_FRONTIER_API_KEY_SECRET_NAME" "anthropic_api_key"
# Make sure the secrets backend is the macOS Keychain default. If
# already set we don't overwrite — operator may have a different
# preference (file / vaultwarden).
if ! grep -qE "^FSF_SECRET_STORE=" "$ENV_FILE"; then
  echo "FSF_SECRET_STORE=keychain" >> "$ENV_FILE"
  echo "      added    FSF_SECRET_STORE=keychain"
fi

# ---------------------------------------------------------------------------
# 4. Prompt the operator for the key. -s suppresses echo so a screen
#    recording doesn't capture it; the password manager paste-shortcut
#    works fine into a -s prompt.
# ---------------------------------------------------------------------------
echo
echo "[3/6] Paste your Anthropic API key now."
echo "      (key is hidden; press Enter when done. Ctrl-C to abort.)"
echo
printf "  > "
# read -s on macOS bash 3.x respects pasted multi-line input poorly;
# we constrain to a single line which Anthropic keys always are.
read -rs ANTHROPIC_KEY
echo
echo

if [ -z "$ANTHROPIC_KEY" ]; then
  echo "ERROR: empty key. Aborting; .env was patched but no key stored."
  echo "       Re-run this script when ready to paste."
  exit 2
fi
# Quick sanity check on shape — Anthropic keys start with sk-ant-.
if [[ "$ANTHROPIC_KEY" != sk-ant-* ]]; then
  echo "WARN: the key doesn't start with 'sk-ant-' — that's the Anthropic"
  echo "      console format. Continuing anyway (custom gateway keys"
  echo "      may use a different prefix), but confirm this is the"
  echo "      right key if the verify step at the end fails."
  echo
fi

# ---------------------------------------------------------------------------
# 5. Store via fsf CLI's --from-stdin path so the key never touches
#    argv (which would be visible in `ps`). The CLI resolves the
#    secret store via FSF_SECRET_STORE — the line we just added means
#    keychain on this Mac.
# ---------------------------------------------------------------------------
echo "[4/6] Storing key in secrets backend (Keychain)"
# Export the in-shell vars so the CLI sees them.
export FSF_SECRET_STORE=keychain
if [ -n "$FSF_BIN" ]; then
  # printf '%s\n' adds the trailing newline that --from-stdin
  # strips. Without it the secret_cmd parser raises EmptyError
  # because read() returns the un-newlined value as a malformed
  # input on some platforms. printf '%s\n' is the documented
  # safe shape (per `fsf secret put --help`).
  printf '%s\n' "$ANTHROPIC_KEY" | "$FSF_BIN" secret put anthropic_api_key --from-stdin
else
  # Module path is forest_soul_forge.cli.main (top-level main()).
  # PYTHONPATH=src:. picks up the installed-or-editable package.
  printf '%s\n' "$ANTHROPIC_KEY" | PYTHONPATH=src:. "$VENV_PY" -m forest_soul_forge.cli.main secret put anthropic_api_key --from-stdin
fi
unset ANTHROPIC_KEY   # belt-and-suspenders — the env var goes away
                     # when this shell exits anyway

# ---------------------------------------------------------------------------
# 6. Restart the daemon so it re-reads .env + lifespan-resolves the
#    secret. Without this the running daemon still has frontier
#    disabled.
# ---------------------------------------------------------------------------
echo "[5/6] Restarting Forest daemon via launchd"
PLIST_LABEL="dev.forest.daemon"
if launchctl print "gui/$(id -u)/${PLIST_LABEL}" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/${PLIST_LABEL}"
  echo "      ok — kickstarted ${PLIST_LABEL}"
  # Give the daemon ~6s to come back up + initialize providers.
  sleep 6
else
  echo "      WARN: ${PLIST_LABEL} not registered with launchd."
  echo "      If you start the daemon by hand, restart it now and"
  echo "      re-run check-frontier-provider.command to verify."
fi

# ---------------------------------------------------------------------------
# 7. Verify. Hit /runtime/provider; expect frontier in 'known' AND
#    health.status == ok if frontier is the active provider, OR
#    a successful (non-error) health check on the frontier provider
#    when local stays default.
# ---------------------------------------------------------------------------
echo "[6/6] Verifying"
TOKEN=$(grep -E "^FSF_API_TOKEN=" "$ENV_FILE" | cut -d= -f2)
RESP=$(curl -s --max-time 8 "http://127.0.0.1:7423/runtime/provider" \
       -H "X-FSF-Token: $TOKEN" 2>&1)

if [ -z "$RESP" ]; then
  echo "      ERROR: daemon didn't respond on 127.0.0.1:7423 within 8s."
  echo "      Wait another minute and re-run check-frontier-provider.command."
  exit 3
fi

# Pretty-print to stdout so the operator can see it.
echo
echo "$RESP" | "$VENV_PY" -m json.tool 2>/dev/null || echo "$RESP"
echo

# Cheap success check.
if echo "$RESP" | grep -q '"frontier"'; then
  echo "----------------------------------------------------------"
  echo "Setup complete. Frontier provider is registered."
  echo
  echo "To make it the ACTIVE provider for an agent or specific"
  echo "task_kind, swap via:"
  echo "  curl -X POST http://127.0.0.1:7423/runtime/provider \\"
  echo "       -H 'X-FSF-Token: \$FSF_API_TOKEN' \\"
  echo "       -H 'Content-Type: application/json' \\"
  echo "       -d '{\"name\": \"frontier\"}'"
  echo
  echo "Or set FSF_DEFAULT_PROVIDER=frontier in .env and restart."
  echo "----------------------------------------------------------"
else
  echo "----------------------------------------------------------"
  echo "Setup MOSTLY complete but verify output didn't mention"
  echo "frontier. Inspect the JSON above; if 'frontier' isn't in"
  echo "the 'known' list, the provider didn't initialize. Common"
  echo "causes: typo in the key, daemon didn't restart, or the"
  echo "secrets backend isn't actually keychain (check"
  echo "FSF_SECRET_STORE)."
  echo "----------------------------------------------------------"
fi

echo
echo "Press any key to close this window."
read -n 1
