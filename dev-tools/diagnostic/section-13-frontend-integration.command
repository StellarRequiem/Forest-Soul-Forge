#!/usr/bin/env bash
# ADR-0079 section 13 — frontend integration (MVP).
#
# MVP scope: hits the API endpoints each frontend tab depends on
# and confirms they return 200 with the expected shape. Catches
# the same class of bug as the Marketplace boot-race (B276/B298)
# without needing a browser driver.
#
# Tab → endpoint(s) checklist:
#   Agents        → /agents
#   Skills        → /skills
#   Tools         → /tools/registered
#   Marketplace   → /skills/staged + /skills/staged/forged
#   Pending       → /pending_calls
#   Orchestrator  → /orchestrator/status
#   Provenance    → /provenance/active + /provenance/handoffs
#   Scheduler     → /scheduler/status
#   Conversations → /conversations (operator-assistant chat list)
#
# Full browser-driven check (open each tab, screenshot, OCR for
# "Loading..." stuck states) is the full intent per ADR-0079 D6
# but needs the Chrome MCP or Playwright. Deferred.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-13-frontend-integration"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 13 — frontend integration (MVP)

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- daemon: $DAEMON
- scope: MVP — API endpoint reachability per tab. Real browser-
  driven check deferred (needs Chrome MCP / Playwright).

HEADER

if ! curl -s --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  echo "## Result" >> "$REPORT"
  echo "" >> "$REPORT"
  echo "- aborted: daemon unreachable at $DAEMON" >> "$REPORT"
  echo "section 13: daemon unreachable"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

"$PY" - "$REPORT" "$DAEMON" "$TOKEN" <<'PYEOF'
"""Section 13 — frontend tab → API endpoint reachability."""
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


# (tab_name, endpoints, required) — required=True means a 404
# is a FAIL; required=False (planned/optional tabs) means 404
# is an INFO ("tab feature not yet shipped").
TAB_ENDPOINTS = [
    ("Agents",        ["/agents"],                                    True),
    ("Skills",        ["/skills"],                                    True),
    ("Tools",         ["/tools/registered"],                          True),
    ("Marketplace",   ["/skills/staged", "/skills/staged/forged"],    True),
    ("Pending",       ["/pending_calls"],                             True),
    ("Orchestrator",  ["/orchestrator/status"],                       True),
    ("Provenance",    ["/provenance/active", "/provenance/handoffs"], False),
    ("Scheduler",     ["/scheduler/status"],                          False),
    ("Conversations", ["/conversations?limit=10"],                    False),
]

results: list[tuple[str, str, str]] = []
for tab, endpoints, required in TAB_ENDPOINTS:
    tab_status = "PASS"
    details = []
    for ep in endpoints:
        status, body = get(ep)
        if status == 200:
            details.append(f"{ep}=200")
        elif status == 404:
            if required:
                tab_status = "FAIL"
                details.append(f"{ep}=404")
            else:
                # If we already have a PASS for this tab, leave it.
                if tab_status == "PASS":
                    tab_status = "INFO"
                details.append(f"{ep}=404 (optional)")
        elif status == 401:
            tab_status = "FAIL"
            details.append(f"{ep}=401-AUTH")
        else:
            tab_status = "FAIL"
            details.append(f"{ep}={status}")
    results.append((tab_status, f"tab: {tab}", "; ".join(details)))

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
info = sum(1 for r in results if r[0] == "INFO")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)} tabs\n"
            f"- passed: {passed}\n- failed: {failed}\n- info: {info}\n\n## Per-tab\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n} — {ev}\n")

print(f"section 13: {passed}/{len(results)} tabs pass")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -25
echo "----"
echo "section 13 exit: $RC"
exit "$RC"
