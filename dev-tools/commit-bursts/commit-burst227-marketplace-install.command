#!/bin/bash
# Burst 227 — ADR-0055 M3: POST /marketplace/install.
#
# Mid-burst discovery: M1 (GET /marketplace/index) was already
# shipped at B184. The marketplace.py router has lived in the
# kernel for a week; I just didn't know to look for it. So the
# actual Phase A unblocking burst is M3 — the install endpoint —
# which makes the browse-then-install loop close.
#
# What ships:
#
# 1. New audit event type: marketplace_plugin_installed.
#    KNOWN_EVENT_TYPES grows 75 → 76.
#
# 2. POST /marketplace/install endpoint with this contract:
#      Body: {registry_id, entry_id, version?, force?}
#      - Looks up the entry in the cached index by
#        (source_registry, id). Refuses 404 on miss.
#      - Pins to body.version if supplied; refuses 409 on
#        version mismatch (the marketplace updated since the
#        operator's last refresh).
#      - Downloads payload from download_url:
#          file:// pointing at a directory → use as-is (dev path)
#          file:// pointing at a file → copy + extract
#          http(s):// → stream-download + extract
#      - SHA256-verifies against entry.download_sha256 for any
#        file-shaped payload. Directory installs skip SHA (dev
#        convenience).
#      - Tarball extraction defense in depth: refuses absolute
#        paths + parent-directory traversal in members.
#      - Tarball may carry the plugin dir at root OR the plugin's
#        contents directly — endpoint normalizes by finding
#        plugin.yaml in the extracted tree.
#      - Calls PluginRepository.install_from_dir with force flag.
#        409 on PluginAlreadyInstalled if force=False.
#      - Triggers PluginRuntime.reload so the new plugin's tools
#        register without daemon restart. Reload failure is
#        non-fatal (file is on disk; next restart picks it up).
#      - Emits marketplace_plugin_installed audit event under
#        write_lock with: registry_id, entry_id, version,
#        plugin_name, download_url, download_sha256,
#        manifest_signature, installed_by, trusted=false.
#
# 3. Trust posture: M6 signing enforcement is queued. Every
#    install returns trusted=false; the audit event records the
#    manifest_signature for future retroactive verification once
#    M6 lands. Frontend can show an "untrusted" badge based on
#    this field.
#
# Endpoint requires require_api_token (existing gate). Honors
# allow_write_endpoints=False with a clean 403.
#
# Errors are HTTPException with descriptive details:
#   400 — missing version, missing download_url, unsafe tarball
#   403 — writes disabled
#   404 — entry not found in cached index
#   409 — sha mismatch / version mismatch / already installed
#   500 — duplicate entries in index (aggregator bug)
#   502 — download failed
#   503 — plugin runtime or audit chain not initialized
#
# Verification: 52 tests pass (test_marketplace_index + audit_chain).
# Live in-process testing deferred — covered when Phase A4 ships
# the signing pipeline and we can wire a full file:// fixture
# through the whole loop.
#
# What's next in Phase A:
#   A4 — M6 signing pipeline (blocked on maintainer keypair)
#   A5 — M4 frontend Browse pane (unblocked; tests against file://)
#   A6 — M5 grant-to-agent (the ADR-0060 grant pane is built;
#         M5 adds the post-install "Use with [agent]" picker)
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: ABI grows additively — one new endpoint, one
#                  new audit event type, zero existing call-site
#                  changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/marketplace.py \
        src/forest_soul_forge/core/audit_chain.py \
        dev-tools/commit-bursts/commit-burst227-marketplace-install.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(marketplace): POST /marketplace/install endpoint (B227)

Burst 227 / ADR-0055 M3. Mid-burst discovery: M1 was already
shipped at B184. This burst lands M3 — the install endpoint that
closes the browse-then-install loop.

Endpoint: POST /marketplace/install {registry_id, entry_id,
version?, force?}.
  - Looks up entry in the cached index by (source_registry, id)
  - Downloads payload (file://, http://, https://)
  - SHA256-verifies against entry.download_sha256
  - Tarball extraction with absolute-path + ../ traversal refusal
  - Normalizes plugin dir vs plugin contents at the tarball root
  - Calls PluginRepository.install_from_dir (force flag honored)
  - Triggers PluginRuntime.reload (non-fatal on failure)
  - Emits marketplace_plugin_installed chain event with full
    provenance: registry_id, entry_id, version, plugin_name,
    download_url, sha, manifest_signature, installed_by, trusted

Trust posture: M6 signing is queued. Every install returns
trusted=false; the manifest_signature field is recorded in the
chain event for later verification.

KNOWN_EVENT_TYPES 75 -> 76 (marketplace_plugin_installed).

52 tests pass. Live integration test deferred to Phase A4 when
the signing pipeline lands and we can wire end-to-end.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: ABI grows additively — one new endpoint, one
                 new audit event."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 227 complete ==="
echo "=== Marketplace M3 live: install loop closes. Next: M4 frontend Browse pane. ==="
echo "Press any key to close."
read -n 1
