#!/usr/bin/env bash
# ADR-0079 section 13 — frontend integration (MVP).
#
# B360+B373: extended from 9 tabs to all 15 + corrected the two
# wrong-URL probes (/skills/staged/forged didn't exist; /pending_calls
# is per-agent only, not a collection). The probe now mirrors the
# real frontend/index.html tab inventory and the real daemon route
# table. Catches the same class of bug as the Marketplace boot-race
# (B276/B298) without needing a browser driver.
#
# Full tab inventory (15) → endpoint(s) checklist:
#   Agents          → /agents
#   Forge           → /traits + /genres + /tools/catalog
#                     (forge form depends on all three; trait_tree
#                     gates the entire forge tab per app.js boot
#                     contract)
#   Skills          → /skills
#   Tool Registry   → /tools/registered
#   Audit           → /audit/tail?n=1
#   Marketplace     → /skills/staged + /marketplace/index
#                     (was /skills/staged/forged - removed; that
#                     route doesn't exist; only /tools/staged/forged
#                     does, and the marketplace tab fetches
#                     /skills/staged not /skills/staged/forged)
#   Pending         → /agents (then /agents/{first_id}/pending_calls)
#                     (was /pending_calls - that's not a route;
#                     pending_calls is per-agent only)
#   Memory          → /agents (then /agents/{first_id}/memory/consents)
#                     (per-agent like Pending - probe samples one)
#   Orchestrator    → /orchestrator/status + /orchestrator/domains
#   Provenance      → /provenance/active + /provenance/handoffs
#   Reality Anchor  → /reality-anchor/status + /reality-anchor/ground-truth
#   Security        → /security/status
#   Operator Wizard → /operator/profile/connectors
#   Voice           → /voice/status
#   Chat (Conv.)    → /conversations?limit=10
#
# A few tabs sample a per-agent endpoint by first fetching /agents
# and picking the first active agent's instance_id. This catches
# the per-agent route shape without requiring the operator to
# hand-curate an agent_id in the probe config. If /agents returns
# zero active agents, those probes degrade to INFO ("no active
# agent to sample - per-agent route shape not verifiable").
#
# Full browser-driven check (open each tab, screenshot, OCR for
# "Loading..." stuck states) is B366 territory per ADR-0079 D6
# and complements (not replaces) this static endpoint check.

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


# Sample one active agent's instance_id for per-agent probes.
# Pending and Memory tabs both hit /agents/{id}/<endpoint> — we
# need a valid agent_id to verify those routes.
sample_agent_id = None
status, body = get("/agents?limit=1")
if status == 200 and isinstance(body, dict):
    agents = body.get("agents") or []
    for a in agents:
        if a.get("status") == "active":
            sample_agent_id = a.get("instance_id")
            break

def _per_agent(template: str) -> str | None:
    """Substitute the sampled agent_id into a path template like
    ``/agents/{aid}/pending_calls``. Returns None if no active agent
    was found so the caller can degrade to INFO instead of probing
    an obviously-malformed URL."""
    if not sample_agent_id:
        return None
    return template.replace("{aid}", sample_agent_id)


# (tab_name, [endpoint_or_(template, _per_agent)], required)
#   - bare string: probed directly
#   - ("{aid}-template", _per_agent_marker): substitutes the sample
#     agent_id; degrades to INFO if no active agent exists
# required=True means a 404 is FAIL; required=False means 404 is
# INFO ("optional / planned tab").
TAB_ENDPOINTS = [
    # Forge depends on all three trait/genre/catalog fetches — any
    # 404 breaks the form. Treat as required.
    ("Agents",         ["/agents"],                                                     True),
    ("Forge",          ["/traits", "/genres", "/tools/catalog"],                        True),
    ("Skills",         ["/skills"],                                                     True),
    ("Tool Registry",  ["/tools/registered"],                                           True),
    ("Audit",          ["/audit/tail?n=1"],                                             True),
    # Marketplace's frontend module fetches /skills/staged +
    # /marketplace/index (NOT /skills/staged/forged — that route
    # never existed; B373 corrected the probe).
    ("Marketplace",    ["/skills/staged", "/marketplace/index"],                        True),
    # Pending + Memory probe a sampled agent; if none active, that
    # tab can't be exercised end-to-end and the probe degrades to
    # INFO rather than FAIL.
    ("Pending",        [("{aid}", "/agents/{aid}/pending_calls")],                      True),
    ("Memory",         [("{aid}", "/agents/{aid}/memory/consents")],                    True),
    ("Orchestrator",   ["/orchestrator/status", "/orchestrator/domains"],               True),
    ("Provenance",     ["/provenance/active", "/provenance/handoffs"],                  False),
    ("Reality Anchor", ["/reality-anchor/status", "/reality-anchor/ground-truth"],      False),
    ("Security",       ["/security/status"],                                            False),
    ("Operator Wizard", ["/operator/profile/connectors"],                               False),
    ("Voice",          ["/voice/status"],                                               False),
    ("Chat",           ["/conversations?limit=10"],                                     False),
]

results: list[tuple[str, str, str]] = []
for tab, endpoints, required in TAB_ENDPOINTS:
    tab_status = "PASS"
    details = []
    for ep in endpoints:
        # Handle per-agent template entries.
        if isinstance(ep, tuple) and len(ep) == 2 and ep[0] == "{aid}":
            resolved = _per_agent(ep[1])
            if resolved is None:
                # No active agent available - the tab depends on
                # one but we can't verify the route shape today.
                # Surface as INFO so the harness still draws the
                # operator's attention to "this tab is only
                # exercisable with an agent live."
                if tab_status == "PASS":
                    tab_status = "INFO"
                details.append(f"{ep[1]} (no active agent to sample)")
                continue
            ep = resolved
        status, body = get(ep)
        if status == 200:
            details.append(f"{ep}=200")
        elif status == 404:
            if required:
                tab_status = "FAIL"
                details.append(f"{ep}=404")
            else:
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
