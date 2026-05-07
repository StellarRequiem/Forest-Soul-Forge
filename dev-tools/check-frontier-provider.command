#!/bin/bash
# One-shot diagnostic: confirm whether the frontier provider is wired
# and the API key is reachable. Prints provider STATE only — never
# the API key value. Read-only; no daemon mutations.

set +e   # don't exit on individual command failures — want all outputs

cd "$(dirname "$0")/.."

echo "======================================================================"
echo "Forest frontier-provider diagnostic — $(date)"
echo "======================================================================"

# ---------------------------------------------------------------------------
echo
echo "--- 1. FSF_FRONTIER env vars in current shell ---"
env | grep -E '^FSF_(FRONTIER|API_TOKEN|SECRETS_BACKEND|SECRETS_MASTER)' \
    | sed -E 's/=.*$/=<REDACTED>/'
[ -z "$(env | grep -E '^FSF_FRONTIER')" ] && echo "(no FSF_FRONTIER_* in this shell)"

# ---------------------------------------------------------------------------
echo
echo "--- 2. launchd plist for the daemon ---"
PLIST="$HOME/Library/LaunchAgents/dev.forest.daemon.plist"
if [ -f "$PLIST" ]; then
  echo "plist exists at $PLIST"
  grep -E "FSF_FRONTIER|FSF_SECRETS|FSF_API_TOKEN" "$PLIST" \
    | sed -E 's/<string>.*<\/string>/<string><REDACTED><\/string>/'
else
  echo "(no plist at $PLIST — daemon may be started by hand instead)"
fi

# ---------------------------------------------------------------------------
echo
echo "--- 3. Daemon /healthz ---"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' .env 2>/dev/null | cut -d= -f2)}"
HEALTHZ=$(curl -s --max-time 5 "http://127.0.0.1:7423/healthz" \
  ${TOKEN:+-H "X-FSF-Token: $TOKEN"} 2>&1)
if [ -z "$HEALTHZ" ]; then
  echo "(daemon unreachable at 127.0.0.1:7423)"
else
  # Pretty-print, redact any base_url that might contain a host path
  # (defensive — the daemon doesn't return keys in /healthz, but an
  # operator-customized provider might)
  echo "$HEALTHZ" | python3 -m json.tool 2>/dev/null || echo "$HEALTHZ"
fi

# ---------------------------------------------------------------------------
echo
echo "--- 4. Daemon /runtime/provider (active provider + frontier health) ---"
PROV=$(curl -s --max-time 5 "http://127.0.0.1:7423/runtime/provider" \
  ${TOKEN:+-H "X-FSF-Token: $TOKEN"} 2>&1)
if [ -z "$PROV" ]; then
  echo "(no response — endpoint missing or daemon down)"
else
  echo "$PROV" | python3 -m json.tool 2>/dev/null || echo "$PROV"
fi

# ---------------------------------------------------------------------------
echo
echo "--- 5. Configured secrets backend ---"
echo "FSF_SECRETS_BACKEND=${FSF_SECRETS_BACKEND:-(unset; defaults to file)}"

# Check FileStore directory if it's the default
if [ -z "$FSF_SECRETS_BACKEND" ] || [ "$FSF_SECRETS_BACKEND" = "file" ]; then
  if [ -d "$HOME/.fsf/secrets" ]; then
    echo "FileStore directory: $HOME/.fsf/secrets"
    echo "Entries (filenames only — no values):"
    ls -la "$HOME/.fsf/secrets/" 2>&1 | tail -n +2
  else
    echo "(no FileStore at $HOME/.fsf/secrets/ — backend may be Keychain or VaultWarden)"
  fi
fi

# ---------------------------------------------------------------------------
echo
echo "--- 6. macOS Keychain — entries under fsf service ---"
echo "(printing only account names + service; never the password)"
security dump-keychain 2>/dev/null \
  | grep -B 1 -A 1 -i 'forest\|fsf_\|anthropic\|openai' \
  | head -40 \
  || echo "(no matches in keychain dump)"

# Specific lookup attempts
for KEY in anthropic_api_key openai_api_key gemini_api_key; do
  RESULT=$(security find-generic-password -s "fsf_$KEY" 2>&1)
  if echo "$RESULT" | grep -q '^password:'; then
    echo "fsf_$KEY: PRESENT in Keychain"
  elif echo "$RESULT" | grep -q "could not be found"; then
    echo "fsf_$KEY: not in Keychain"
  else
    : # silent
  fi
done

# ---------------------------------------------------------------------------
echo
echo "--- 7. fsf CLI: list secrets via the supported channel ---"
# The fsf CLI's `secret list` lists names without revealing values.
# Use the venv's Python so packages are in scope.
VENV_PY=".venv/bin/python"
if [ -x "$VENV_PY" ]; then
  $VENV_PY -m forest_soul_forge.cli secret list 2>&1 | head -30 \
    || echo "(fsf secret list failed — see error above)"
else
  echo "(no .venv/bin/python — skipping CLI listing)"
fi

# ---------------------------------------------------------------------------
echo
echo "======================================================================"
echo "Diagnostic complete. Summary:"
echo "  - Look at section 4 ('/runtime/provider') for the active provider"
echo "    and whether 'frontier' is listed with status=ok."
echo "  - Section 7 lists secret NAMES (not values) — confirm"
echo "    'anthropic_api_key' is present."
echo "======================================================================"
echo
echo "Press any key to close this window."
read -n 1
