#!/bin/bash
# Burst 184 — ADR-0055 M1 — kernel GET /marketplace/index endpoint.
#
# First implementation tranche of ADR-0055. Lands the read side of
# the marketplace API surface that the frontend Browse pane (M4)
# will consume.
#
# Per ADR-0055 Decision 4, the kernel owns exactly TWO marketplace
# endpoints. M1 ships the first (GET /marketplace/index); M3 will
# ship the second (POST /marketplace/install).
#
# Everything else — UI, registry schema, signing tools, ratings —
# lives in the forest-marketplace sibling repo (scaffolded
# locally, no remote yet; HEAD a15a913).
#
# What ships:
#
#   src/forest_soul_forge/daemon/schemas/marketplace.py (NEW):
#     - MarketplaceContributesTool, MarketplaceContributes,
#       MarketplaceReview, MarketplaceEntryOut,
#       MarketplaceIndexOut Pydantic read models.
#     - SideEffectTier literal enum (read_only/network/
#       filesystem/external) reused from the kernel's tool-side
#       schema.
#     - Defensive deserialization: extra fields silently dropped
#       so a future schema bump in the marketplace repo doesn't
#       crash the kernel.
#
#   src/forest_soul_forge/daemon/schemas/__init__.py:
#     - Export the five new schema classes + SideEffectTier.
#
#   src/forest_soul_forge/daemon/config.py:
#     - DaemonSettings gains 3 marketplace fields:
#         marketplace_registries: list[str]   (default empty —
#           operator opts in via env var or settings UI)
#         marketplace_trusted_keys: list[str] (M6 forward-compat)
#         marketplace_cache_ttl_s: int        (default 3600)
#
#   src/forest_soul_forge/daemon/routers/marketplace.py (NEW):
#     - GET /marketplace/index endpoint.
#     - Aggregator: fans out across configured registries,
#       parses YAML, validates, merges, stores on
#       app.state.marketplace_cache.
#     - Per-registry fetcher supports file:// + https://
#       schemes. Tests use file:// fixtures; httpx is the
#       remote-fetch path.
#     - Two registry shapes accepted:
#         (a) flat list of inline entries
#         (b) {schema_version, entries: [...]} where each entry
#             is either inline OR a string filename relative to
#             the registry's directory (file:// only — HTTPS
#             registries must inline).
#     - Path-escape defense on filename references — entries
#       referencing ../ outside the registry root are refused.
#     - Bad-entry tolerance: a malformed individual entry is
#       dropped; the registry as a whole still serves the
#       valid entries. M6 will surface per-entry errors via a
#       structured /marketplace/diagnostics endpoint.
#     - Caching: per-process app.state cache with TTL. Within
#       TTL, returns cached without re-fetching. On TTL expiry,
#       re-fetches synchronously. On per-registry failure
#       during refresh, falls back to per-registry last-known-
#       good and surfaces stale=true + failed_registries=[url].
#     - Trust posture (M1-only): every entry returns
#       trusted=false. M6 will compute this from
#       manifest_signature against marketplace_trusted_keys.
#
#   src/forest_soul_forge/daemon/app.py:
#     - Import + include the new marketplace_router. Sits
#       AFTER secrets_router in the include order (last in
#       the chain).
#
#   tests/unit/test_marketplace_index.py (NEW):
#     - 13 unit tests across four classes:
#
#       TestAggregator (6 tests):
#         - empty registries → empty + stale=false
#         - inline registry parses + tags source_registry
#         - filename-reference registry resolves + parses
#         - multiple registries merge with separate
#           source_registry per entry
#         - missing-file registry → stale=true, entries=[]
#         - LKG fallback when registry disappears between calls
#
#       TestCache (2 tests):
#         - within TTL: no re-fetch (deleting the file mid-
#           interval still returns cached entry)
#         - ttl=0: re-fetch every call (LKG kicks in on disappear)
#
#       TestEndpointSemantics (2 tests):
#         - missing X-FSF-Token → 401/403
#         - response shape matches MarketplaceIndexOut +
#           MarketplaceEntryOut field set
#
#       TestEntryParsing (3 tests):
#         - missing required field on one entry drops THAT
#           entry; registry serves the others
#         - completely malformed YAML marks the whole registry
#           failed
#         - ../ path escape attempt in a filename reference is
#           refused (the entry parses to nothing; security
#           fence verified)
#
# Per ADR-0044 D3: zero kernel ABI changes that affect existing
# daemons. New endpoint is additive; new settings default to the
# pre-M1 behavior (empty registries → empty browse). No schema
# migrations.
#
# Per ADR-0001 D2: read-only endpoint. No state mutation, no
# audit emission, no agent identity touched.
#
# Companion-repo state: forest-marketplace at /Users/llm01/
# forest-marketplace HEAD a15a913 — local commit, no remote
# pushed yet. M3 (POST /marketplace/install) will pin
# download_sha256 hashes against published .plugin releases;
# operators publishing the official registry need to set the
# remote URL on forest-marketplace and push before M3 install
# can verify against published artifacts. M1 reads-only so it
# works against the local file:// path today.
#
# Verification:
#   PYTHONPATH=src:. pytest tests/unit/test_marketplace_index.py
#                                tests/unit/test_tool_dispatcher.py
#                                tests/unit/test_governance_pipeline.py
#                                tests/unit/test_procedural_shortcut_dispatch.py
#   -> 167 passed
#
# Plus: build_app() imports clean (no startup regression).
#
# Substrate ready for M3 (POST /marketplace/install). The cached
# index is the primary input to that endpoint — install resolves
# (registry_id, entry_id, version) by looking up the cached
# entry, validating SHA, then delegating to the existing
# ADR-0043 POST /plugins/install handler.
#
# Remaining ADR-0055 tranches:
#   M3 — POST /marketplace/install + marketplace_plugin_installed
#        audit event type
#   M4 — frontend Marketplace pane (browse + filter + install)
#   M5 — grant-to-agent flow with auto-derived trust_tier
#   M6 — ed25519 signing pipeline + untrusted-entry confirmation
#   M7 — operator ratings + reviews (DEFERRED)

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/schemas/marketplace.py \
        src/forest_soul_forge/daemon/schemas/__init__.py \
        src/forest_soul_forge/daemon/config.py \
        src/forest_soul_forge/daemon/routers/marketplace.py \
        src/forest_soul_forge/daemon/app.py \
        tests/unit/test_marketplace_index.py \
        dev-tools/commit-bursts/commit-burst184-adr0055-m1-marketplace-index.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(marketplace): ADR-0055 M1 — GET /marketplace/index (B184)

Burst 184. First implementation tranche of ADR-0055. Lands the
read side of the kernel's two-endpoint marketplace API. Frontend
Browse pane (M4) is the primary consumer.

Per Decision 4: kernel owns exactly TWO marketplace endpoints.
M1 ships GET /marketplace/index; M3 will ship
POST /marketplace/install. Everything else — UI, registry
schema, signing, ratings — lives in forest-marketplace sibling
repo.

Ships:
- 5 Pydantic read models (MarketplaceEntryOut + 4 supporting)
  in daemon/schemas/marketplace.py with defensive
  deserialization (unknown fields silently dropped — forward-
  compat with marketplace-side schema bumps).
- 3 DaemonSettings fields: marketplace_registries (default
  empty — operator opts in), marketplace_trusted_keys (M6
  forward-compat, default empty), marketplace_cache_ttl_s
  (default 3600).
- daemon/routers/marketplace.py: aggregator that fans out
  across configured registries (file:// + https:// supported),
  parses YAML, validates, merges, caches on app.state. Two
  registry shapes accepted: flat list of inline entries, OR
  {schema_version, entries: [...]} with inline-or-filename
  reference per entry. Filename refs only supported for
  file:// registries with path-escape defense (../ refused).
  Bad-entry tolerance: malformed individual entries dropped,
  registry serves the rest. Per-registry LKG cache means a
  network blip serves stale=true with the last-known-good
  entries instead of going empty.
- M1-only trust posture: trusted=false on every entry. M6
  will compute from manifest_signature against
  marketplace_trusted_keys.

Tests: 13 unit tests across TestAggregator, TestCache,
TestEndpointSemantics, TestEntryParsing. file:// fixtures
cover the local-clone case (operator pinning their own
forest-marketplace clone); httpx-stub coverage deferred to
M3 sweep when remote-fetch becomes critical.

Per ADR-0044 D3: additive endpoint + additive settings;
zero kernel ABI changes that affect pre-M1 daemons.
Per ADR-0001 D2: read-only endpoint, no agent identity
touched.

Companion-repo state: /Users/llm01/forest-marketplace HEAD
a15a913 (M2 scaffold). No remote pushed yet — operators
pin a file:// URL until forest-marketplace publishes.

Verification: 167 passed across the touched-modules sweep
+ build_app() imports clean.

Remaining ADR-0055 tranches:
- M3 POST /marketplace/install + marketplace_plugin_installed
  audit event type
- M4 frontend Marketplace pane
- M5 grant-to-agent flow
- M6 ed25519 signing pipeline
- M7 operator ratings (DEFERRED)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 184 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
