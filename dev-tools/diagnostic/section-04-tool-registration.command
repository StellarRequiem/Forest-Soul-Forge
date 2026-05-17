#!/usr/bin/env bash
# ADR-0079 section 04 — tool registration.
#
# For each tool in tool_catalog.yaml, confirm it actually shows up
# in /tools/registered. Catches the case where a tool ships in the
# catalog YAML but its Python class never registered (silent miss).
# Also reports any tool registered but NOT in the catalog (drift in
# the other direction).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-04-tool-registration"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 04 — tool registration

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- daemon: $DAEMON

HEADER

# preflight: daemon reachable
if ! curl -s --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  cat >> "$REPORT" <<EOF
## Result

- total: 0
- passed: 0
- failed: 1 (daemon unreachable)

Cannot enumerate /tools/registered without a live daemon.
EOF
  echo "section 04: 0/1 (daemon unreachable)"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

cd "$REPO_ROOT"

"$PY" - "$REPORT" "$DAEMON" "$TOKEN" <<'PYEOF'
"""Section 04 driver — tool catalog ↔ /tools/registered cross-check."""
import json
import sys
import urllib.request
from pathlib import Path

REPORT, DAEMON, TOKEN = sys.argv[1:4]
REPO = Path.cwd()
sys.path.insert(0, str(REPO / "src"))

from forest_soul_forge.core.tool_catalog import load_catalog

catalog = load_catalog(REPO / "config" / "tool_catalog.yaml")
catalog_keys = {td.key for td in catalog.tools.values()}  # name.vN

# pull /tools/registered
req = urllib.request.Request(DAEMON + "/tools/registered")
if TOKEN:
    req.add_header("X-FSF-Token", TOKEN)
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
except Exception as e:
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(f"\n## Result\n\n- total: 0\n- failed: 1\n\n"
                f"- **[FAIL]** /tools/registered fetch — {type(e).__name__}: {e}\n")
    print(f"section 04: 0/1 (fetch failed)")
    sys.exit(1)

# Tolerate both schemas: {"tools": [{"name", "version", ...}, ...]}
# and {"tools": ["name.vN", ...]}
tools = data.get("tools", [])
registered_keys = set()
for t in tools:
    if isinstance(t, dict):
        name = t.get("name")
        ver = t.get("version")
        if name and ver:
            registered_keys.add(f"{name}.v{ver}")
    elif isinstance(t, str):
        registered_keys.add(t)

results: list[tuple[str, str, str]] = []

missing = sorted(catalog_keys - registered_keys)
extra = sorted(registered_keys - catalog_keys)

if missing:
    results.append((
        "FAIL",
        f"every catalog tool registered ({len(missing)} missing)",
        ", ".join(missing[:10]) + ("..." if len(missing) > 10 else ""),
    ))
else:
    results.append((
        "PASS",
        "every catalog tool registered",
        f"{len(catalog_keys)} tools in catalog all registered",
    ))

if extra:
    results.append((
        "FAIL",
        f"no orphan registrations ({len(extra)} extra)",
        ", ".join(extra[:10]) + ("..." if len(extra) > 10 else ""),
    ))
else:
    results.append((
        "PASS",
        "no orphan registrations",
        f"{len(registered_keys)} registered tools all in catalog",
    ))

# Count match (informational PASS regardless)
results.append((
    "PASS" if len(catalog_keys) == len(registered_keys) else "FAIL",
    "catalog count == registered count",
    f"catalog={len(catalog_keys)}, registered={len(registered_keys)}",
))

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)}\n- passed: {passed}\n"
            f"- failed: {failed}\n\n## Checks\n\n")
    for s, n, e in results:
        f.write(f"- **[{s}]** {n}")
        if e:
            f.write(f" — {e}")
        f.write("\n")

print(f"section 04: {passed}/{len(results)} passed")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -25
echo "----"
echo "section 04 exit: $RC"
exit "$RC"
