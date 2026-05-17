#!/usr/bin/env bash
# ADR-0079 section 11 — memory + retention.
#
# MVP scope: verifies the memory substrate is queryable for an
# alive agent + the retention/consolidation status endpoints
# respond:
#   1. /agents/{id}/memory returns 200 + a list shape for a
#      sample alive agent (memory scope readable per-agent)
#   2. /consolidation/status returns 200 if the endpoint is
#      mounted (ADR-0074 substrate is wired)
#   3. /scheduler/status returns 200 if mounted (ADR-0075
#      substrate is wired; the retention sweeps run via the
#      scheduler)
#
# Full per-scope writeability + retention-sweep delete-and-count
# test deferred — that's destructive and needs a sandbox table,
# not the live registry. A later tranche may ship it against a
# scratch sqlite for safety.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-11-memory-retention"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 11 — memory + retention (MVP)

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- daemon: $DAEMON
- scope: MVP — endpoint shape checks. Destructive retention test deferred.

HEADER

if ! curl -s --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  echo "## Result" >> "$REPORT"
  echo "" >> "$REPORT"
  echo "- aborted: daemon unreachable at $DAEMON" >> "$REPORT"
  echo "section 11: daemon unreachable"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

"$PY" - "$REPORT" "$DAEMON" "$TOKEN" <<'PYEOF'
"""Section 11 — memory + retention MVP."""
import json
import sys
import urllib.request

REPORT, DAEMON, TOKEN = sys.argv[1:4]


def get(path):
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

# Check 1: memory readable per-agent
status, agents = get("/agents?limit=5")
if status == 200 and isinstance(agents, dict):
    alive = [a for a in agents.get("agents", []) if a.get("status") == "active"]
    if not alive:
        results.append(("FAIL", "memory readable per-agent",
                        "no alive agent to probe"))
    else:
        probe = alive[0]
        pid = probe["instance_id"]
        s2, body = get(f"/agents/{pid}/memory?limit=5")
        if s2 == 200 and isinstance(body, dict) and "entries" in body:
            n = len(body.get("entries", []))
            results.append(("PASS", "memory readable per-agent",
                            f"agent={pid[:18]}: {n} entries in last 5"))
        else:
            results.append(("FAIL", "memory readable per-agent",
                            f"status={s2}, body shape: {type(body).__name__}"))
else:
    results.append(("FAIL", "memory readable per-agent",
                    f"/agents fetch failed: status={status}"))

# Check 2: /consolidation/status
status, body = get("/consolidation/status")
if status == 200:
    results.append(("PASS", "/consolidation/status mounted",
                    f"keys={list(body.keys())[:5] if isinstance(body, dict) else 'list'}"))
elif status == 404:
    results.append(("INFO", "/consolidation/status mounted",
                    "404 — endpoint not present (ADR-0074 may not be fully wired)"))
else:
    results.append(("FAIL", "/consolidation/status mounted",
                    f"unexpected status={status}"))

# Check 3: /scheduler/status
status, body = get("/scheduler/status")
if status == 200:
    results.append(("PASS", "/scheduler/status mounted",
                    f"keys={list(body.keys())[:5] if isinstance(body, dict) else 'list'}"))
elif status == 404:
    results.append(("INFO", "/scheduler/status mounted",
                    "404 — endpoint not present (ADR-0075 may not be fully wired)"))
else:
    results.append(("FAIL", "/scheduler/status mounted",
                    f"unexpected status={status}"))

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
info = sum(1 for r in results if r[0] == "INFO")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)}\n"
            f"- passed: {passed}\n- failed: {failed}\n- info: {info}\n\n## Checks\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n}")
        if ev:
            f.write(f" — {ev}")
        f.write("\n")

print(f"section 11: {passed}/{len(results)} passed")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -20
echo "----"
echo "section 11 exit: $RC"
exit "$RC"
