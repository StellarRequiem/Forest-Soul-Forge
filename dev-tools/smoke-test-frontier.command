#!/bin/bash
# Smoke test for the Anthropic frontier wiring.
#
# Verifies in order:
#   1. The Anthropic key is in the resolved secrets store (Keychain).
#   2. The FrontierProvider can authenticate against Anthropic's
#      OpenAI-compat endpoint at /v1/chat/completions.
#   3. claude-sonnet-4-6 (the model name we configured) is accepted.
#
# Bypasses the agent + dispatcher layer entirely — pulls the key
# from Keychain, instantiates FrontierProvider directly, calls
# .complete() with a 5-word prompt, prints the response.
#
# If anything fails the error message tells you exactly which step
# (key not stored, network down, wrong model name, etc.).

set -euo pipefail

cd "$(dirname "$0")/.."

VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "ERROR: no .venv/bin/python at $VENV_PY"
  exit 1
fi

export FSF_SECRET_STORE=keychain

PYTHONPATH=src:. "$VENV_PY" - <<'PY'
import asyncio
import sys

# Step 1: resolve key from secrets store.
try:
    from forest_soul_forge.security.secrets import resolve_secret_store
    store = resolve_secret_store()
    api_key = store.get("anthropic_api_key")
except Exception as e:
    print(f"FAIL [step 1]: secrets-store resolution raised {type(e).__name__}: {e}")
    sys.exit(2)

if not api_key:
    print("FAIL [step 1]: anthropic_api_key not present in store "
          "(check Keychain entry forest-soul-forge:anthropic_api_key)")
    sys.exit(3)

print(f"OK [step 1]: key resolved ({len(api_key)} chars, "
      f"prefix={api_key[:7]}...)")

# Step 2 + 3: instantiate FrontierProvider with hardcoded base_url
# (https://api.anthropic.com — the FrontierProvider appends
# /v1/chat/completions internally so the base_url should NOT
# include /v1).
from forest_soul_forge.daemon.providers.frontier import FrontierProvider
from forest_soul_forge.daemon.providers.base import (
    TaskKind,
    ProviderDisabled,
    ProviderError,
    ProviderUnavailable,
)

# Try the configured model first; if it fails, surface the error
# clearly so we can iterate.
candidate_models = [
    "claude-sonnet-4-6",                    # primary target
    "claude-3-5-sonnet-latest",             # fallback A
    "claude-3-5-sonnet-20241022",           # fallback B (dated)
]

base_url = "https://api.anthropic.com"
print(f"OK [step 2]: base_url={base_url} "
      f"(provider will append /v1/chat/completions)")


async def try_model(model_name):
    provider = FrontierProvider(
        enabled=True,
        base_url=base_url,
        api_key=api_key,
        models={
            TaskKind.CLASSIFY:     model_name,
            TaskKind.GENERATE:     model_name,
            TaskKind.SAFETY_CHECK: model_name,
            TaskKind.CONVERSATION: model_name,
            TaskKind.TOOL_USE:     model_name,
        },
        timeout_s=20.0,
    )
    try:
        text = await provider.complete(
            "Say hello in exactly 5 words.",
            task_kind=TaskKind.CONVERSATION,
            max_tokens=40,
        )
        return ("ok", text)
    except ProviderError as e:
        return ("provider_error", str(e))
    except ProviderUnavailable as e:
        return ("unavailable", str(e))
    except ProviderDisabled as e:
        return ("disabled", str(e))
    except Exception as e:
        return ("other", f"{type(e).__name__}: {e}")


async def main():
    for model in candidate_models:
        print(f"\n--- Trying model: {model} ---")
        kind, msg = await try_model(model)
        if kind == "ok":
            print(f"OK [step 3]: model={model} accepted.")
            print(f"\n*** Response from Claude ***")
            print(msg)
            print(f"*** End response ***")
            print(f"\nSMOKE TEST PASSED — frontier wiring is live.")
            print(f"Suggested action: keep this model in .env "
                  f"(FSF_FRONTIER_MODEL={model}).")
            return 0
        else:
            print(f"FAIL [{kind}]: {msg[:300]}")
    print("\nSMOKE TEST FAILED — none of the candidate models worked.")
    print("Possible causes:")
    print("  - The compat endpoint requires a different model name format;")
    print("    check https://docs.claude.com for the current list.")
    print("  - The API key has been revoked or the account has no credit.")
    print("  - Network blocked outbound to api.anthropic.com.")
    return 4


sys.exit(asyncio.run(main()))
PY

EXIT=$?

echo
echo "Press any key to close this window."
read -n 1
exit $EXIT
