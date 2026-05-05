#!/bin/bash
# Forest kernel headless smoke test.
#
# Validates the kernel/userspace boundary: this script exercises
# the running daemon via curl alone, no frontend assumptions, no
# Tauri shell, no browser. If it passes against a `python -m
# forest_soul_forge.daemon` invocation (no SoulUX scripts),
# the headless install path holds.
#
# Used by:
#   - ADR-0044 P3 manual verification
#   - External integrator validation (run this against any
#     Forest-kernel build to verify the seven ABI surfaces work)
#   - Future P4 conformance test suite (this is a starting point)
#
# Usage:
#   ./scripts/headless-smoke.sh                    # default 127.0.0.1:7423
#   FSF_DAEMON_URL=http://other-host:7423 ./scripts/headless-smoke.sh
#
# Exits 0 on full pass, non-zero on first failure.

set -euo pipefail

URL="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"

pass() { printf "  ✓ %s\n" "$1"; }
fail() { printf "  ✗ %s\n" "$1" >&2; exit 1; }

echo "=== Forest kernel headless smoke ==="
echo "Target: $URL"
echo ""

# --- §1: lifespan + health -------------------------------------------------
echo "[1/6] Lifespan + health"
if curl -fsS "$URL/healthz" >/dev/null 2>&1; then
  status=$(curl -fsS "$URL/healthz" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])')
  if [ "$status" = "ok" ]; then
    pass "/healthz reports ok"
  else
    fail "/healthz reports '$status' (expected 'ok')"
  fi
else
  fail "/healthz unreachable — daemon not running on $URL"
fi

# --- §2: kernel API surface — read endpoints ------------------------------
echo "[2/6] Read endpoints respond"
for endpoint in /genres /tools /traits /skills /plugins /agents; do
  if curl -fsS "$URL$endpoint" >/dev/null 2>&1; then
    pass "$endpoint"
  else
    fail "$endpoint failed (status $(curl -s -o /dev/null -w '%{http_code}' "$URL$endpoint"))"
  fi
done

# --- §3: audit chain — kernel canonical surface ---------------------------
echo "[3/6] Audit chain reachable"
events=$(curl -fsS "$URL/audit/tail?n=5" | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("events", [])))')
if [ "$events" -ge 0 ]; then
  pass "/audit/tail returned $events events (chain reachable)"
else
  fail "/audit/tail unparseable"
fi

# --- §4: tool catalog integrity --------------------------------------------
echo "[4/6] Tool catalog integrity"
tool_count=$(curl -fsS "$URL/tools" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["tools"]))')
if [ "$tool_count" -ge 50 ]; then
  pass "$tool_count tools registered (≥ 50 expected at v0.5+)"
else
  fail "Only $tool_count tools — catalog incomplete"
fi

# --- §5: genre engine ------------------------------------------------------
echo "[5/6] Genre engine"
genre_count=$(curl -fsS "$URL/genres" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d.get("genres", {})))')
if [ "$genre_count" -ge 13 ]; then
  pass "$genre_count genres registered (≥ 13 expected post-Burst-124)"
else
  fail "Only $genre_count genres — engine incomplete"
fi

# --- §6: trait engine ------------------------------------------------------
echo "[6/6] Trait engine"
role_count=$(curl -fsS "$URL/traits" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d.get("roles", {})))')
if [ "$role_count" -ge 42 ]; then
  pass "$role_count roles registered (≥ 42 expected post-Burst-124)"
else
  fail "Only $role_count roles — engine incomplete"
fi

echo ""
echo "=== Headless smoke PASSED ==="
echo "Kernel/userspace boundary holds: all six surfaces responded"
echo "without any frontend / Tauri / SoulUX userspace assumptions."
