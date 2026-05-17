#!/usr/bin/env bash
# ADR-0079 section 05 — agent inventory.
#
# For every alive agent in /agents:
#   - Constitution file exists + parses
#   - Every tool in the constitution exists in tool_catalog.yaml
#   - The agent's kit doesn't violate its genre's max_side_effects
#     ceiling (the B341 / B336 failure mode generalized)
#
# Replays the per-agent shape of the bugs we've found by hand:
# B336 narrow-kit (TestAuthor-D4 had only timestamp_window.v1),
# B341 kit-tier violation (migration_pilot in guardian but kit
# included shell_exec which is external).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-05-agent-inventory"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 05 — agent inventory

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- daemon: $DAEMON

HEADER

if ! curl -s --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  echo "## Result" >> "$REPORT"
  echo "" >> "$REPORT"
  echo "- aborted: daemon unreachable at $DAEMON" >> "$REPORT"
  echo "section 05: daemon unreachable"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

cd "$REPO_ROOT"

"$PY" - "$REPORT" "$DAEMON" "$TOKEN" <<'PYEOF'
"""Section 05 — agent inventory per-agent checks."""
import json
import sys
import urllib.request
from pathlib import Path

REPORT, DAEMON, TOKEN = sys.argv[1:4]
REPO = Path.cwd()
sys.path.insert(0, str(REPO / "src"))

from forest_soul_forge.core.tool_catalog import load_catalog
from forest_soul_forge.core.genre_engine import load_genres

catalog = load_catalog(REPO / "config" / "tool_catalog.yaml")
genres = load_genres(REPO / "config" / "genres.yaml")
tool_keys = {td.key for td in catalog.tools.values()}
tool_side_effects = {td.key: td.side_effects for td in catalog.tools.values()}

# genre.max_side_effects → which side_effect levels are <= ceiling.
SIDE_EFFECT_LEVELS = ["read_only", "filesystem", "network", "external"]


def within_ceiling(tool_se: str, ceiling: str) -> bool:
    try:
        return SIDE_EFFECT_LEVELS.index(tool_se) <= SIDE_EFFECT_LEVELS.index(ceiling)
    except ValueError:
        return False


def get(path: str):
    req = urllib.request.Request(DAEMON + path)
    req.add_header("X-FSF-Token", TOKEN)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


# discover alive agents
data = get("/agents?limit=200")
agents = [a for a in data.get("agents", []) if a.get("status") == "active"]

results: list[tuple[str, str, str]] = []  # status, agent_name, evidence

import yaml
for ag in agents:
    name = ag.get("agent_name", "?")
    role = ag.get("role", "?")
    iid = ag.get("instance_id", "?")
    # fetch full agent detail to get constitution_path
    try:
        detail = get(f"/agents/{iid}")
    except Exception as e:
        results.append(("FAIL", name, f"detail fetch failed: {e}"))
        continue
    const_path = detail.get("constitution_path")
    if not const_path or not Path(const_path).exists():
        results.append(("FAIL", name, f"constitution missing: {const_path}"))
        continue
    try:
        doc = yaml.safe_load(Path(const_path).read_text(encoding="utf-8"))
    except Exception as e:
        results.append(("FAIL", name, f"constitution parse failed: {e}"))
        continue

    # tools listed in constitution
    tools = doc.get("tools") or []
    if not isinstance(tools, list):
        results.append(("FAIL", name, f"tools field not a list: {type(tools)}"))
        continue

    bad_tools = []
    ceiling_violations = []
    genre = genres.genre_for(role)
    ceiling = (
        getattr(getattr(genre, "risk_profile", None), "max_side_effects", None)
        if genre else None
    )
    for t in tools:
        if not isinstance(t, dict):
            continue
        tname = t.get("name")
        tver = t.get("version", "1")
        key = f"{tname}.v{tver}"
        if key not in tool_keys:
            bad_tools.append(key)
            continue
        if ceiling:
            se = tool_side_effects.get(key, "?")
            if not within_ceiling(se, ceiling):
                ceiling_violations.append(f"{key}({se})")

    if bad_tools:
        results.append(("FAIL", name,
                        f"role={role}; tools not in catalog: {bad_tools[:5]}"))
    elif ceiling_violations:
        results.append(("FAIL", name,
                        f"role={role}, genre={genre.name if genre else '?'} "
                        f"ceiling={ceiling}; kit violates: {ceiling_violations[:5]}"))
    else:
        results.append(("PASS", name,
                        f"role={role}, {len(tools)} tools, "
                        f"genre={genre.name if genre else '?'}"))

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total agents: {len(results)}\n")
    f.write(f"- passed: {passed}\n- failed: {failed}\n\n## Per-agent\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** `{n}` — {ev}\n")

print(f"section 05: {passed}/{len(results)} passed across {len(agents)} agents")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -30
echo "----"
echo "section 05 exit: $RC"
exit "$RC"
