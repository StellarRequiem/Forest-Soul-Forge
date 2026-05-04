#!/usr/bin/env bash
# Burst 103: ADR-0043 — MCP-first plugin protocol.
#
# Locks the design for the highest-leverage v0.5 integration
# move per the Burst 102 strategy doc. Same shape as ADR-0033
# (Security Swarm), ADR-0036 (Verifier Loop), ADR-0041
# (Set-and-Forget Orchestrator), ADR-0042 (v0.5 Product
# Direction).
#
# WHAT THE ADR LOCKS
#
# Architecture:
# - File-system layout under ~/.forest/plugins/installed/ +
#   disabled/ + registry-cache.json + secrets/ subdirs
# - plugin.yaml manifest schema (name, version, type=mcp_server|
#   tool|skill|genre, capabilities, side_effects per ADR-0019,
#   per-tool requires_human_approval map, entry_point with
#   sha256 pin, required_secrets)
# - CLI: fsf plugin install/list/enable/disable/uninstall/
#   secrets/search/info/verify/reload/update
# - HTTP: GET /plugins, GET /plugins/{name}, POST /plugins/reload,
#   POST /plugins/{name}/{enable,disable,verify} — all gated
#   by writes_enabled + api_token like the writes routes
# - 6 new audit events: plugin_installed/enabled/disabled/
#   uninstalled/secret_set/verification_failed
# - Hot-reload semantics — diff installed/ against runtime
#   catalog; mid-flight calls complete normally because of
#   write-lock serialization
# - Registry: separate forest-plugins Git repo with sparse-
#   checkout install. Zero infra; GitHub Pages serves the
#   catalog; community owns contributions.
#
# Why MCP-first (alternatives considered + rejected with
# reasoning):
# - Plain Python entry-point plugins: full-process privilege
#   risk; can mutate the dispatcher pipeline
# - OCI containers: requires container runtime on user's
#   machine; misaligned with SMB segment + Tauri installer
# - WASM plugins: Pyodide / wasi-py too rough; most third-party
#   tools won't compile cleanly
# - Direct subprocess (no protocol): re-invents Anthropic's MCP
#   without the network effect; community library doesn't
#   work as plugins for free
#
# 5-tranche plan:
#   T1 (this) — ADR filed
#   T2 (Burst 104) — Directory layout + plugin.yaml validator +
#     fsf plugin install/list/info/uninstall CLI (no daemon yet)
#   T3 (Burst 105) — Daemon hot-reload + /plugins HTTP endpoints
#     + bridge to existing mcp_servers.yaml runtime path
#   T4 (Burst 106) — Audit-chain integration; 6 event types emit
#   T5 (Burst 107) — Registry repo bootstrap with 3-5 canonical
#     plugins (filesystem, github, postgres, brave-search, slack)
#     as authoring examples
#
# Open questions deferred:
# - Additional sandboxing (macOS sandbox-exec / Linux seccomp)
#   — v0.6+; v0.5 trusts MCP's process-boundary isolation
# - Plugin signing infrastructure (cosign / sigstore / custom)
#   — decided in T5 when the registry needs a signing pipeline
# - Plugin auto-update vs operator-invoked — operator-invoked
#   for v0.5; auto-update follows daemon updater (ADR-0042 T5)
# - Skill plugins (type: skill) — same manifest, different
#   runtime story; follow-up ADR
# - Cross-plugin dependencies — not v0.5
#
# REFERENCES
# - ADR-0019 (side_effects classification)
# - ADR-003X Phase C4 (mcp_call.v1 — the foundation)
# - ADR-003X K1 (agent_secrets store)
# - ADR-0041 (same control-endpoint + audit-emit + hot-reload
#   patterns as the scheduler)
# - ADR-0042 (v0.5 Product Direction — SMB thesis demands
#   low-friction extension model)
# - docs/roadmap/2026-05-04-integrations-strategy.md (Burst
#   102 framing)
# - config/mcp_servers.yaml.example (existing layer upgraded)

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 103 — ADR-0043: MCP-first plugin protocol ==="
echo
clean_locks
git add docs/decisions/ADR-0043-mcp-plugin-protocol.md
git add commit-burst103-adr0043.command
clean_locks
git status --short
echo
clean_locks
git commit -m "docs(adr): ADR-0043 MCP-first plugin protocol

Locks the design for the highest-leverage v0.5 integration
move per the Burst 102 integrations strategy doc. Builds on
the existing mcp_call.v1 (ADR-003X Phase C4) + config/
mcp_servers.yaml foundation; upgrades from operator-curated
YAML editing to a proper plugin protocol.

Architecture:
- ~/.forest/plugins/{installed,disabled,secrets}/ + registry-
  cache.json filesystem layout
- plugin.yaml manifest schema (name, version, type=mcp_server/
  tool/skill/genre, capabilities, side_effects per ADR-0019,
  per-tool requires_human_approval map, sha256-pinned entry
  point, required_secrets)
- fsf plugin install/list/enable/disable/uninstall/secrets/
  search/info/verify/reload/update CLI
- GET /plugins, GET /plugins/{name}, POST /plugins/reload,
  POST /plugins/{name}/{enable,disable,verify} HTTP endpoints
  (writes_enabled + api_token gated)
- 6 new audit events: plugin_installed, plugin_enabled,
  plugin_disabled, plugin_uninstalled, plugin_secret_set,
  plugin_verification_failed
- Hot-reload: diff installed/ vs runtime catalog under the
  write lock so mid-flight tool calls complete normally
- Registry-as-Git-repo: separate forest-plugins repo, sparse-
  checkout install, zero infra to operate

Why MCP-first (alternatives considered + rejected):
- Plain Python entry-point plugins: full-process privilege
  risk; can mutate the dispatcher pipeline
- OCI containers: requires container runtime on user's
  machine; misaligned with SMB segment + Tauri installer
- WASM plugins: Pyodide / wasi-py too rough; most third-party
  tools won't compile
- Direct subprocess (no protocol): re-invents MCP without the
  network effect; community library doesn't work for free

5-tranche plan:
  T1 (this burst) — ADR filed
  T2 (Burst 104) — directory + plugin.yaml validator + fsf
    plugin install/list/info/uninstall CLI (no daemon-side
    wiring yet)
  T3 (Burst 105) — daemon hot-reload + /plugins endpoints +
    bridge to existing mcp_servers.yaml runtime path
  T4 (Burst 106) — audit-chain integration; 6 event types emit
  T5 (Burst 107) — registry repo bootstrap with 3-5 canonical
    plugins (filesystem, github, postgres, brave-search,
    slack) as authoring examples

Open questions deferred:
- Additional sandboxing (sandbox-exec / seccomp) — v0.6+
- Plugin signing infrastructure — T5 when registry needs it
- Auto-update vs operator-invoked — operator for v0.5
- Skill plugins (type: skill) — follow-up ADR
- Cross-plugin dependencies — not v0.5

References Burst 102 integrations roadmap, ADR-0019, ADR-003X
Phase C4 + K1, ADR-0041, ADR-0042."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 103 landed. ADR-0043 locks the MCP plugin protocol."
echo "Next: Burst 104 — T2 implementation (directory + plugin.yaml + CLI)."
echo ""
read -rp "Press Enter to close..."
