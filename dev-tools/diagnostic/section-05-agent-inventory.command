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

# Quarantine manifest (B369) — instance_id -> reason. Agents listed
# here have known broken constitutions; the operator has acknowledged
# them and is awaiting a decision (archive / repair / retire).
# Parse failures for quarantined agents land as INFO not FAIL so
# the daily summary doesn't churn on already-tracked state.
QUARANTINE: dict[str, str] = {}
import yaml as _yaml_for_quar
_q_path = REPO / "config" / "agent_quarantine.yaml"
if _q_path.exists():
    try:
        _q = _yaml_for_quar.safe_load(_q_path.read_text(encoding="utf-8")) or {}
        for _e in (_q.get("entries") or []):
            _iid = _e.get("instance_id")
            if _iid:
                QUARANTINE[_iid] = (_e.get("reason") or "").strip()
    except Exception:
        # A broken quarantine file shouldn't blow up the harness.
        # The empty dict means "no quarantine" — strict checks apply
        # to all agents as the fallback safe posture.
        pass

# Forged tools (ADR-0058) — install via the forge pipeline and live
# at data/forge/tools/installed/<name>.v<ver>.yaml rather than the
# static catalog. Section 05 must treat these as legitimate kit
# entries, otherwise every agent that uses a forged tool (Translator
# Sandbox, etc.) gets flagged.
import yaml as _yaml_for_forge  # alias to avoid colliding with `yaml` import below
forged_dir = REPO / "data" / "forge" / "tools" / "installed"
if forged_dir.exists():
    for p in sorted(forged_dir.glob("*.yaml")):
        try:
            fd = _yaml_for_forge.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        fname = fd.get("name")
        fver = fd.get("version")
        if not (fname and fver):
            continue
        key = f"{fname}.v{fver}"
        tool_keys.add(key)
        tool_side_effects[key] = fd.get("side_effects", "read_only")

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
        # B369 — constitution parse failures land as INFO (not
        # FAIL) for agents present in config/agent_quarantine.yaml.
        # The operator's quarantine entry IS the paper trail; the
        # harness shouldn't keep flagging it FAIL once the operator
        # has acknowledged the broken state. Untracked parse
        # failures still surface as FAIL.
        q_reason = QUARANTINE.get(iid)
        if q_reason:
            short = q_reason.splitlines()[0] if q_reason else ""
            results.append(("INFO", name,
                            f"constitution parse failed (quarantined): {short}"))
        else:
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
