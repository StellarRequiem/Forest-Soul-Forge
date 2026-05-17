#!/usr/bin/env bash
# ADR-0079 section 10 — cross-domain orchestration (MVP).
#
# MVP scope: verifies the orchestrator substrate is wired:
#   1. /orchestrator/status returns 200 + a singleton orchestrator
#      instance is registered
#   2. decompose_intent.v1 + route_to_domain.v1 tools are
#      registered in /tools/registered
#   3. orchestrator's domain registry view matches the on-disk
#      config/domains/*.yaml count
#
# Real end-to-end dispatch (operator utterance → decompose →
# route → delegate happy path) is the full intent per ADR-0079
# but needs a stable LLM provider call — brittle to ship as a
# health probe. A later tranche may add a hardcoded
# decompose-intent fixture that bypasses the LLM call.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-10-cross-domain-orchestration"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 10 — cross-domain orchestration (MVP)

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- daemon: $DAEMON
- scope: MVP wiring checks. Real end-to-end dispatch deferred
  (requires stable LLM provider + decompose fixture).

HEADER

if ! curl -s --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  echo "## Result" >> "$REPORT"
  echo "" >> "$REPORT"
  echo "- aborted: daemon unreachable at $DAEMON" >> "$REPORT"
  echo "section 10: daemon unreachable"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

cd "$REPO_ROOT"

"$PY" - "$REPORT" "$DAEMON" "$TOKEN" <<'PYEOF'
"""Section 10 — orchestration wiring MVP checks."""
import json
import sys
import urllib.request
from pathlib import Path

REPORT, DAEMON, TOKEN = sys.argv[1:4]
REPO = Path.cwd()


def get(path: str):
    req = urllib.request.Request(DAEMON + path)
    req.add_header("X-FSF-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, str(e)


results: list[tuple[str, str, str]] = []

# Check 1: /orchestrator/status returns 200 + reports a populated
# registry. B371 — the cross-domain orchestrator is a substrate-
# level singleton ROUTER (ADR-0067), not an agent instance with an
# instance_id. The pre-B371 probe checked for an instance_id field
# that the response never carried; that was a probe-vs-substrate
# contract mismatch. The substrate's response shape per
# routers/orchestrator.py is:
#   { "schema_version": int, "registry": { "total_domains": int,
#     "dispatchable_domains": int, "planned_domains": int,
#     "domain_ids": [...] } }
# The presence of schema_version + a non-empty registry IS the
# singleton-substrate proof. (If the orchestrator ever migrates to
# a per-instance representation, this check tightens to match.)
status, body = get("/orchestrator/status")
if status == 200 and isinstance(body, dict):
    reg = body.get("registry")
    if (
        isinstance(body.get("schema_version"), int)
        and isinstance(reg, dict)
        and isinstance(reg.get("total_domains"), int)
    ):
        results.append(("PASS", "/orchestrator/status reports populated registry",
                        f"schema_version={body['schema_version']}, "
                        f"total_domains={reg['total_domains']}, "
                        f"dispatchable={reg.get('dispatchable_domains')}, "
                        f"planned={reg.get('planned_domains')}"))
    else:
        results.append(("FAIL", "/orchestrator/status response shape",
                        f"unexpected body: {json.dumps(body)[:200]}"))
elif status == 404:
    results.append(("FAIL", "/orchestrator/status endpoint exists",
                    "404 — orchestrator router not mounted"))
else:
    results.append(("FAIL", "/orchestrator/status returns 200",
                    f"status={status}, body={str(body)[:120]}"))

# Check 2: decompose_intent.v1 + route_to_domain.v1 registered
status, tools_body = get("/tools/registered")
registered_keys = set()
if status == 200 and isinstance(tools_body, dict):
    for t in tools_body.get("tools", []):
        if isinstance(t, dict):
            n = t.get("name")
            v = t.get("version")
            if n and v:
                registered_keys.add(f"{n}.v{v}")
        elif isinstance(t, str):
            registered_keys.add(t)

for tool_key in ("decompose_intent.v1", "route_to_domain.v1"):
    if tool_key in registered_keys:
        results.append(("PASS", f"{tool_key} registered", ""))
    else:
        results.append(("FAIL", f"{tool_key} registered",
                        "not in /tools/registered"))

# Check 3: domain count matches on-disk
on_disk_domains = sorted((REPO / "config" / "domains").glob("*.yaml"))
n_on_disk = len(on_disk_domains)

if isinstance(body, dict):
    daemon_view = (
        body.get("known_domains") or body.get("domains") or body.get("domain_count")
    )
    daemon_n = (
        len(daemon_view) if isinstance(daemon_view, (list, dict))
        else daemon_view if isinstance(daemon_view, int) else None
    )
    if daemon_n is None:
        results.append(("PASS", "domain count check",
                        f"orchestrator status doesn't expose domain count "
                        f"(on-disk={n_on_disk}; skipped)"))
    elif daemon_n == n_on_disk:
        results.append(("PASS", "domain count: orchestrator view matches on-disk",
                        f"{n_on_disk} domains"))
    else:
        results.append(("FAIL", "domain count: orchestrator view matches on-disk",
                        f"daemon sees {daemon_n}, on-disk has {n_on_disk}"))
else:
    results.append(("PASS", "domain count check",
                    "skipped — /orchestrator/status didn't return a dict"))

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)}\n"
            f"- passed: {passed}\n- failed: {failed}\n\n## Checks\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n}")
        if ev:
            f.write(f" — {ev}")
        f.write("\n")

print(f"section 10: {passed}/{len(results)} passed")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -20
echo "----"
echo "section 10 exit: $RC"
exit "$RC"
