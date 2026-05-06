#!/bin/bash
# Burst 173 — ADR-0052 T6 — chat-tab Secrets card. Surfaces the
# active backend + secret name list (NEVER values) inside the
# assistant settings panel so an operator can see at a glance
# what Forest has access to without leaving the chat surface.
#
# Mutating operations stay CLI-only per the ADR-0052 design — the
# chat tab is intentionally not a destructive surface for
# credentials. Operators run `fsf secret put <name>` for adds and
# `fsf secret delete <name>` for removes; values never round-trip
# the browser.
#
# What ships:
#
#   src/forest_soul_forge/daemon/routers/secrets.py (NEW):
#     Two read-only endpoints under /secrets prefix:
#     - GET /secrets/backend → {name, selection_source,
#       selection_via}. Mirrors `fsf secret backend` CLI output.
#     - GET /secrets/names → {backend, count, names}. Names ONLY,
#       sorted for deterministic UI rendering.
#     Backend resolution failures (bad FSF_SECRET_STORE) → 503;
#     backend .list_names() failures → 502 with backend identifier
#     in the detail message.
#
#   src/forest_soul_forge/daemon/app.py:
#     Imports + registers the secrets_router via the existing
#     include_router pattern (matches plugin_grants_router /
#     agent_posture_router conventions).
#
#   frontend/index.html:
#     New "Plugin secrets" card in the assistant settings panel,
#     below the Memory consents card. Contains:
#     - Backend status line (#chat-assistant-secrets-backend)
#     - Name list area (#chat-assistant-secrets-names)
#     - Inline note pointing at the CLI for add/delete
#
#   frontend/js/chat.js:
#     New renderAssistantSecrets() — fetches /secrets/backend +
#     /secrets/names independently (one card's failure doesn't
#     block the other), renders backend + sorted name list with
#     count. Empty-state copy points at `fsf secret put <name>`.
#     Wired into loadAssistantSettings()'s Promise.allSettled
#     fan-out alongside identity / posture / consents / allowances.
#
#   frontend/css/style.css:
#     .chat-assistant-secrets-list — compact monospaced grid of
#     bullet items. auto-fill columns at 180px min so names wrap
#     gracefully on narrow viewports without becoming a wall.
#
# Tests:
#
#   tests/unit/test_secrets_router.py (NEW):
#     7 tests via FastAPI TestClient + tmp-path FileStore:
#     - GET /secrets/backend returns active store with explicit
#       selection_source + selection_via fields
#     - GET /secrets/backend returns platform_default source when
#       FSF_SECRET_STORE is unset
#     - GET /secrets/names empty when no secrets stored
#     - GET /secrets/names returns sorted list with count
#     - GET /secrets/names defense-in-depth: never includes the
#       value, even with values present in the underlying store
#     - GET /secrets/backend 503 on bad FSF_SECRET_STORE
#     - GET /secrets/names 503 on bad FSF_SECRET_STORE
#
# Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
# changes. New userspace HTTP endpoints (additive); existing
# audit-chain event vocabulary unchanged; no schema migrations.
#
# Verification:
#   PYTHONPATH=src pytest tests/unit/test_secrets_router.py
#   -> 7 passed
#   JS parse OK; HTML parse OK
#
# Closes ADR-0052 T6 — the user-facing chat surface for secrets
# is now functional. Combined with T1 (FileStore), T2 (KeychainStore),
# T4 (loader integration + audit trail in B170/B171), T5 (CLI), the
# operator workflow is end-to-end:
#
#   1. Active backend visible in the assistant settings panel
#   2. Stored secret names visible in the same card
#   3. Add / remove via CLI (deliberate terminal action)
#   4. Plugin loader resolves required_secrets at launch via the
#      backend, sets env_vars on the subprocess
#   5. Audit chain captures the resolution via tool_call_succeeded
#      metadata.required_secrets_resolved
#
# Remaining ADR-0052 tranche:
#   T3 VaultWardenStore (bw CLI wrapper — coming in B174)

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/secrets.py \
        src/forest_soul_forge/daemon/app.py \
        frontend/index.html \
        frontend/js/chat.js \
        frontend/css/style.css \
        tests/unit/test_secrets_router.py \
        dev-tools/commit-bursts/commit-burst173-adr0052-t6-secrets-card.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(chat): ADR-0052 T6 — chat-tab Secrets card (B173)

Burst 173. Closes ADR-0052 T6. Surfaces the active secret-store
backend + name list inside the assistant settings panel. Names
ONLY traverse the HTTP surface — values stay in the backend (and
never round-trip the browser by design). Mutating ops stay
CLI-only per the ADR-0052 framing.

Ships:
- daemon/routers/secrets.py: GET /secrets/backend (name +
  selection source) and GET /secrets/names (sorted list, count).
  503 on resolver failure, 502 on backend list_names() failure.
  Both failure paths surface the backend identifier in the
  detail message for operator debugging.
- daemon/app.py: registers secrets_router via the established
  include_router pattern.
- frontend/index.html: new Plugin secrets card under the existing
  Memory consents card.
- frontend/js/chat.js: renderAssistantSecrets() — independent
  fetches for backend + names; empty-state copy points at
  fsf secret put. Wired into loadAssistantSettings's
  Promise.allSettled fan-out.
- frontend/css/style.css: .chat-assistant-secrets-list compact
  monospaced grid (auto-fill 180px columns).

Tests: 7 via FastAPI TestClient + tmp-path FileStore. Coverage
includes the defense-in-depth assertion that values are NEVER
in the response body even when the underlying store has them.

Per ADR-0052 Decision 1 + ADR-0044 D3: zero kernel ABI surface
changes. Additive HTTP endpoints; no schema migrations; no new
audit-chain event types.

Verification: 7 passed; JS + HTML parse clean.

Closes ADR-0052 T6. The operator workflow is end-to-end: backend
visible in chat → stored names visible → add/remove via CLI →
plugin loader resolves at launch → audit chain captures
resolution via tool_call_succeeded metadata.

Remaining ADR-0052 tranche:
- T3 VaultWardenStore (bw CLI wrapper, coming in B174)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 173 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
