#!/usr/bin/env bash
# ADR-0079 section 02 — skill manifest integrity.
#
# Globs every example skill + every installed skill. For each:
#   - parses through the production parse_manifest loader
#   - every `requires:` tool exists in the catalog
#   - every step.id is unique
# Catches drift between skills and the catalog they depend on.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-02-skill-manifests"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 02 — skill manifest integrity

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- targets: examples/skills/*.yaml + data/forge/skills/installed/*.yaml

HEADER

cd "$REPO_ROOT"

"$PY" - "$REPORT" <<'PYEOF'
"""Section 02 driver — skill manifest integrity."""
import sys
from pathlib import Path

REPORT = Path(sys.argv[1])
REPO = Path.cwd()
sys.path.insert(0, str(REPO / "src"))

from forest_soul_forge.core.tool_catalog import load_catalog
from forest_soul_forge.forge.skill_manifest import parse_manifest, ManifestError

catalog = load_catalog(REPO / "config" / "tool_catalog.yaml")
tool_names = {td.name for td in catalog.tools.values()}

results: list[tuple[str, str, str]] = []

def add(status: str, name: str, evidence: str = ""):
    results.append((status, name, evidence))

paths = sorted([
    *((REPO / "examples" / "skills").glob("*.yaml")),
    *((REPO / "data" / "forge" / "skills" / "installed").glob("*.yaml")),
])

for path in paths:
    rel = path.relative_to(REPO)
    try:
        skill = parse_manifest(path.read_text(encoding="utf-8"))
    except ManifestError as e:
        add("FAIL", f"{rel}: parse_manifest", f"{e}")
        continue
    except Exception as e:
        add("FAIL", f"{rel}: parse_manifest", f"{type(e).__name__}: {e}")
        continue
    add("PASS", f"{rel}: parse_manifest", f"{len(skill.steps)} steps")

    # requires-tool existence
    import yaml
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    requires = raw.get("requires") or []
    missing = []
    for req in requires:
        # req is "tool_name.vN" — strip the version to compare against
        # catalog tool names (which don't include the version suffix).
        base = req.rsplit(".v", 1)[0] if ".v" in req else req
        if base not in tool_names:
            missing.append(req)
    if missing:
        add("FAIL", f"{rel}: requires-tool exists",
            f"missing from catalog: {missing}")
    else:
        add("PASS", f"{rel}: requires-tool exists",
            f"{len(requires)} tools")

    # step.id uniqueness
    ids = [s.id for s in skill.steps]
    dups = [x for x in set(ids) if ids.count(x) > 1]
    if dups:
        add("FAIL", f"{rel}: step ids unique", f"duplicates: {dups}")
    else:
        add("PASS", f"{rel}: step ids unique", "")

if not paths:
    add("FAIL", "skill files found", "no skills at examples/ or installed/")

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")

with REPORT.open("a", encoding="utf-8") as f:
    f.write(f"## Result\n\n")
    f.write(f"- total: {len(results)}\n- passed: {passed}\n- failed: {failed}\n\n")
    f.write(f"## Checks ({len(paths)} skill files)\n\n")
    for status, name, ev in results:
        f.write(f"- **[{status}]** {name}")
        if ev:
            f.write(f" — {ev}")
        f.write("\n")

print(f"section 02: {passed}/{len(results)} passed across {len(paths)} skills")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -30
echo "----"
echo "section 02 exit: $RC"
exit "$RC"
