#!/usr/bin/env bash
# Forest Soul Forge — Security Swarm bring-up + smoke test (live).
#
# Runs the full ADR-0033 Phase D + E1 sequence end-to-end:
#   1. birth all 9 swarm agents
#   2. install all 21 skill manifests
#   3. drive the synthetic-incident smoke test
#
# Double-click from Finder. Each step's output streams in order.
# Stops on the first failure — you don't end up with a half-installed
# state if step 1 errors.
#
# Prereqs: daemon up at $FSF_DAEMON_URL (default http://127.0.0.1:7423),
#          jq + curl on PATH. Optional: FSF_API_TOKEN if writes are gated.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

bar() { printf "\n========== %s ==========\n" "$1"; }
fail() { echo "FAILED at: $1" >&2; echo ""; echo "Press return to close."; read -r _; exit 1; }

# B214 — auto-load FSF_API_TOKEN from .env if not already in the
# environment. Post-B148 the daemon requires X-FSF-Token on every
# write endpoint; the sub-scripts already honor FSF_API_TOKEN if
# set, they just don't read .env on their own. Sourcing here means
# /birth + /skills/install + /agents/.../tools/call all inherit
# the token without per-script edits.
if [[ -z "${FSF_API_TOKEN:-}" && -f ".env" ]]; then
  TOK="$(grep -E '^FSF_API_TOKEN=' .env | head -1 | cut -d= -f2)"
  if [[ -n "$TOK" ]]; then
    export FSF_API_TOKEN="$TOK"
    echo "Auto-loaded FSF_API_TOKEN from .env (${TOK:0:8}…)."
  fi
fi

bar "0. Daemon health check"
DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
if ! curl -sf "$DAEMON/healthz" > /tmp/fsf-health.$$ 2>&1; then
  echo "daemon not reachable at $DAEMON — start it first"
  fail "healthz"
fi
# Print the diagnostics array verbatim so failures surface. Use jq because
# we already depend on it; previous heredoc form had a redirect-order bug
# (the file redirect won and Python tried to parse the JSON as source code).
jq -r '
  "  status: \(.status // "?")",
  "  startup: \(.startup_diagnostics | length) diagnostics",
  ( .startup_diagnostics[] |
      if .status == "ok" or .status == "disabled"
      then "    [\(.status)]       \(.component)"
      else "    [\(.status)] \(.component): \((.error // "") | .[0:140])"
      end
  )
' /tmp/fsf-health.$$
rm -f /tmp/fsf-health.$$
echo ""
echo "  NOTE: if any diagnostic above is failed/degraded for trait_engine,"
echo "  tool_runtime, or genre_engine — restart the daemon. The Phase D1"
echo "  commit added 9 new roles + archetype kits to the YAML configs;"
echo "  a daemon started before that commit won't know them."
echo ""

bar "1. Birth 9 swarm agents"
./scripts/security-swarm-birth.sh || fail "birth"

bar "2. Install 21 skill manifests"
./scripts/security-swarm-install-skills.sh || fail "install"

bar "3. Synthetic-incident smoke test"
./scripts/security-smoke.sh || fail "smoke"

bar "ALL THREE STEPS PASSED"
echo "Phase D + E1 verified live on this daemon."
echo "Next: promote ADR-0033 Proposed → Accepted, write the first audit doc."

echo ""
echo "Press return to close."
read -r _
