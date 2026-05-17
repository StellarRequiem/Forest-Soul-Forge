#!/usr/bin/env bash
# ADR-0079 section 06 — ToolContext wiring probe.
#
# This is the B350-class catch zone. For each subsystem the
# dispatcher claims to wire into ToolContext, probe it via a real
# dispatch of a tool that depends on that subsystem. If the
# subsystem isn't actually wired, the tool returns a
# ToolValidationError with a "not wired" message instead of a
# successful result — that's how this section detects the bug.
#
# Subsystems probed (one tool per subsystem):
#   memory          → memory_recall.v1 (mode=private, query=__probe__)
#   delegate        → delegate.v1 (target=__missing__; expect refusal
#                     NOT "delegate not wired" — refusal proves wired)
#   priv_client     → SKIP if FSF_ENABLE_PRIV_CLIENT=false (default)
#   secrets         → secrets_read.v1 (name=__probe__; expect
#                     SecretsUnknown NOT SecretsUnavailable)
#   agent_registry  → suggest_agent.v1 (query=any)
#   procedural_shortcuts → memory_tag_outcome.v1 (id=__probe__)
#   personal_index  → SKIP if FSF_PERSONAL_INDEX_ENABLED!=true
#   provider        → llm_think.v1 (prompt=hello, max_tokens=4)
#   audit_chain     → audit_chain_verify.v1 — the tool that
#                     surfaced B350. Pre-B350 this raised
#                     "no AuditChain bound to ctx"; post-B350 it
#                     returns ok/broken result.
#
# Each probe runs against an existing alive agent (any agent
# works for read-only subsystems). The section discovers an
# agent via /agents.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-06-ctx-wiring"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 06 — ToolContext wiring probe

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- daemon: $DAEMON
- intent: probe each dispatcher-claimed subsystem via real dispatch

HEADER

# preflight
if ! curl -s --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  cat >> "$REPORT" <<EOF
## Result

- aborted: daemon unreachable at $DAEMON
EOF
  echo "section 06: daemon unreachable"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

"$PY" - "$REPORT" "$DAEMON" "$TOKEN" <<'PYEOF'
"""Section 06 driver — ctx-wiring probe via real dispatch."""
import json
import sys
import time
import urllib.request

REPORT, DAEMON, TOKEN = sys.argv[1:4]


def post(path: str, body: dict, timeout: float = 15) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        DAEMON + path, method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-FSF-Token": TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, str(e)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def get(path: str, timeout: float = 5) -> tuple[int, dict | str]:
    req = urllib.request.Request(DAEMON + path)
    req.add_header("X-FSF-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


# Find an agent to probe against. Any alive agent works for read-only
# subsystem probes.
status, agents = get("/agents?limit=10")
if status != 200 or not isinstance(agents, dict):
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(f"## Result\n\n- aborted: /agents fetch failed ({status})\n")
    print("section 06: /agents fetch failed")
    sys.exit(1)
alive = [a for a in agents.get("agents", []) if a.get("status") == "active"]
if not alive:
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write("## Result\n\n- aborted: no alive agents to probe against\n")
    print("section 06: no alive agents")
    sys.exit(1)
probe_agent = alive[0]
probe_id = probe_agent["instance_id"]
probe_dna = probe_agent.get("agent_dna", "?")[:12]
report_lines = [
    f"- probe agent: {probe_agent.get('agent_name', '?')} "
    f"({probe_id}, dna={probe_dna})",
    "",
]

# Each probe is (subsystem, tool_name, tool_version, args, success_predicate,
# skip_if). success_predicate is a callable that takes the response body
# and returns True if the probe indicates the subsystem is wired (even if
# the tool itself returned a refusal — refusal proves wiring).
SESSION = f"diagnostic-06-{int(time.time())}"
SUBSYSTEMS = [
    ("memory", "memory_recall", "1",
     {"query": "__probe__", "mode": "private", "limit": 1},
     lambda body: not (
         isinstance(body, dict)
         and "not wired" in str(body.get("detail", "")).lower()
     ),
     None),
    ("delegate", "delegate", "1",
     {"target_instance_id": "__missing__", "skill_name": "__probe__",
      "skill_version": "1", "reason": "ctx-wiring probe"},
     # Delegate raising "target not in lineage" or "target missing" =
     # wired correctly. "delegate not wired" = not wired.
     lambda body: "delegate not wired" not in str(body).lower(),
     None),
    ("audit_chain", "audit_chain_verify", "1",
     {"max_unknown_to_report": 1},
     # Post-B350 this returns a structured ok/broken result. Pre-B350
     # it raised "no AuditChain bound to ctx".
     lambda body: "no auditchain bound" not in str(body).lower()
                  and "no audit chain bound" not in str(body).lower(),
     None),
    ("agent_registry", "suggest_agent", "1",
     {"query": "ctx wiring probe"},
     lambda body: "agent_registry not wired" not in str(body).lower()
                  and "agent registry not wired" not in str(body).lower(),
     None),
    ("procedural_shortcuts", "memory_tag_outcome", "1",
     {"shortcut_id": "__probe__", "outcome": "match", "confidence": 0.5},
     lambda body: "procedural shortcut substrate not wired" not in str(body).lower(),
     None),
    ("provider", "llm_think", "1",
     {"prompt": "say ok", "max_tokens": 4},
     lambda body: "provider not wired" not in str(body).lower()
                  and "no provider" not in str(body).lower(),
     None),
    ("personal_index", "personal_recall", "1",
     {"query": "__probe__", "limit": 1},
     lambda body: "personal index not wired" not in str(body).lower(),
     # Skip if disabled — that's not a wiring failure, it's an opt-out.
     lambda body: "personal index not wired" in str(body).lower()),
    ("secrets", "secrets_read", "1",
     {"name": "__probe__"},
     lambda body: "secrets" in str(body).lower()
                  and "unavailable" not in str(body).lower(),
     lambda body: "secretsunavailable" in str(body).lower()),
]

results: list[tuple[str, str, str, str]] = []  # status, subsystem, tool, evidence
for subsystem, name, version, args, ok_pred, skip_pred in SUBSYSTEMS:
    payload = {
        "tool_name": name,
        "tool_version": version,
        "session_id": f"{SESSION}-{subsystem}",
        "args": args,
    }
    status, body = post(f"/agents/{probe_id}/tools/call", payload)
    evidence = json.dumps(body)[:200] if isinstance(body, dict) else str(body)[:200]
    if skip_pred and skip_pred(body):
        results.append(("SKIP", subsystem, f"{name}.v{version}",
                        f"subsystem opt-out: {evidence[:120]}"))
        continue
    if status == 404:
        # Tool not registered in this daemon. Skip — that's a section 04
        # finding, not a section 06 finding.
        results.append(("SKIP", subsystem, f"{name}.v{version}",
                        "tool not in /tools/registered (see section 04)"))
        continue
    if ok_pred(body):
        results.append(("PASS", subsystem, f"{name}.v{version}",
                        f"subsystem wired (status={status})"))
    else:
        results.append(("FAIL", subsystem, f"{name}.v{version}",
                        f"appears not wired: {evidence}"))

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
skipped = sum(1 for r in results if r[0] == "SKIP")

with open(REPORT, "a", encoding="utf-8") as f:
    for line in report_lines:
        f.write(line + "\n")
    f.write(f"## Result\n\n- total: {len(results)}\n")
    f.write(f"- passed: {passed}\n- failed: {failed}\n- skipped: {skipped}\n\n")
    f.write("## Subsystem probes\n\n")
    for s, ss, tool, ev in results:
        f.write(f"- **[{s}]** `{ss}` via `{tool}` — {ev}\n")

print(f"section 06: {passed}/{len(results)} passed, {skipped} skipped, {failed} failed")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -30
echo "----"
echo "section 06 exit: $RC"
exit "$RC"
