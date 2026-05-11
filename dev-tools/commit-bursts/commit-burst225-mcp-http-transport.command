#!/bin/bash
# Burst 225 — HTTP transport in mcp_call.v1.
#
# The universality unlock for the MCP arc. Pre-B225 the dispatcher
# refused any MCP server whose entry_point.type wasn't stdio:
#   server X url Y uses unsupported transport;
#   v1 supports only stdio: (subprocess JSON-RPC)
#
# Post-B225, http:// and https:// URLs route through HTTP JSON-RPC
# POST. The dispatcher gate is unchanged — same constitution check,
# same posture, same per-tool approval, same audit chain — HTTP
# just changes the wire format under the same governance.
#
# Implementation:
#
# 1. mcp_call.py — the existing stdio branch is preserved as-is.
#    A new branch handles http:// and https:// URLs:
#      - Builds the same JSON-RPC envelope (jsonrpc/id/method/params)
#      - Sends via httpx.AsyncClient POST with Content-Type +
#        Accept headers
#      - Supports auth_header_template (operator-supplied
#        "Bearer {SECRET_NAME}" format string substituted with
#        resolved auth_env values)
#      - Supports extra_headers (operator-supplied static headers
#        like x-api-version)
#      - Distinguishes transport-level failures (5xx, timeout,
#        malformed JSON) from JSON-RPC errors (200 with "error"
#        field — these flow through the existing isError handling
#        the same way stdio does)
#      - Unknown schemes still refuse with an updated error
#        message listing both supported transports
#
# 2. Tests — new tests/unit/test_mcp_call_http_transport.py with
#    10 cases:
#      TestHttpHappyPath:
#        - 200 OK returns the result
#        - Request payload shape (URL, JSON-RPC envelope, headers)
#      TestHttpJsonRpcError:
#        - JSON-RPC error in body → isError=True without raising
#      TestHttpTransportErrors:
#        - HTTP 5xx → McpCallError
#        - HTTP 4xx → McpCallError with "refused"
#        - Malformed JSON → McpCallError
#        - httpx.TimeoutException → McpCallError "timed out"
#      TestAuthHeaderTemplate:
#        - Secret substituted into Authorization header
#        - extra_headers pass through
#      TestUnknownTransport:
#        - ws:// URL refuses with the unsupported-transport error
#
# Tests use a monkeypatched httpx.AsyncClient stub so no real
# sockets bind. Existing stdio path's 10 tests still pass —
# total 47 in the focused regression sweep (HTTP + stdio + secrets
# + per-tool approval + plugin grants).
#
# Safety story:
#
# All existing governance gates fire identically for HTTP MCPs:
#   - ConstitutionGateStep (now with B219 catalog-grant fallback)
#   - posture × trust_tier matrix (ADR-0060 D4)
#   - per-tool requires_human_approval (ADR-0019)
#   - mcp_call_dispatched audit event
#   - allowed_mcp_servers constitution check
#   - allowlisted_tools per-server filter
#
# Transport-specific safety knobs added:
#   - auth_header_template gives operators an explicit, auditable
#     way to pass secrets WITHOUT putting them in the URL or body
#     (where they'd show up in logs / replay archives)
#   - 4xx HTTP responses surface as "refused" so an auditor reading
#     the chain can distinguish auth failure from a working tool's
#     legitimate "no" answer
#
# What this unlocks:
#
# Any Anthropic-spec MCP server that speaks HTTP+JSON-RPC is now
# operable. Includes: hosted MCPs (run by the maintainer), local
# Docker-containerized MCPs (forwarded via port), MCPs published
# as serverless functions. Plug-and-play story is now actually
# plug-and-play for the universe of MCPs rather than just the
# stdio subset.
#
# What's queued:
#   - SSE streaming for tool responses (the spec allows it; v1.x
#     HTTP path returns the complete response in one go)
#   - WebSocket transport (lower-priority; HTTP covers most servers)
#   - Operator UX for adding HTTP MCPs (the YAML registry already
#     accepts http:// URLs; a dedicated 'Add MCP server' form is
#     B226+ scope)
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: stdio path bit-for-bit unchanged. HTTP path
#                  adds new code; new auth_header_template +
#                  extra_headers optional config fields are
#                  read-with-default so existing stdio configs
#                  ignore them. Zero existing call-site changes.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/builtin/mcp_call.py \
        tests/unit/test_mcp_call_http_transport.py \
        dev-tools/commit-bursts/commit-burst225-mcp-http-transport.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(mcp_call): HTTP/HTTPS transport (B225)

Burst 225. The universality unlock for the MCP arc. mcp_call.v1
now routes http:// and https:// URLs as JSON-RPC POST in addition
to the existing stdio: subprocess path.

All existing governance gates fire identically — ConstitutionGate
(with B219 catalog-grant fallback), posture x trust_tier matrix
(ADR-0060 D4), per-tool requires_human_approval, allowed_mcp_servers,
allowlisted_tools, mcp_call_dispatched audit emission. HTTP just
swaps the wire layer.

New per-server optional config:
  - auth_header_template — e.g. 'Bearer {GITHUB_TOKEN}', substituted
    with resolved auth_env values (avoids putting secrets in URL/body)
  - extra_headers — static headers like X-Api-Version

Failure modes:
  - 4xx -> McpCallError 'refused' (auth/allowlist hint)
  - 5xx -> McpCallError with body preview
  - timeout -> McpCallError 'timed out'
  - malformed JSON -> McpCallError with raw preview
  - JSON-RPC error in 200 body -> ToolResult isError=True (same
    handling as stdio)

10 new tests via monkeypatched httpx.AsyncClient. 47 in the focused
regression sweep — no stdio regressions.

What this unlocks: any Anthropic-spec MCP that speaks HTTP+JSON-RPC
works without code changes — hosted MCPs, containerized MCPs,
serverless MCPs. The plug-and-play story is now universal across
the MCP spec, not just the stdio subset.

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: stdio path bit-for-bit unchanged; new config
                 fields are read-with-default."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 225 complete ==="
echo "=== HTTP/HTTPS MCP transport live. Any spec-compliant MCP now operable. ==="
echo "Press any key to close."
read -n 1
