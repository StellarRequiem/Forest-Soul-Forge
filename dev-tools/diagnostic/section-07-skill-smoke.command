#!/usr/bin/env bash
# ADR-0079 section 07 — skill smoke.
#
# MVP scope: every installed skill manifest is listed in /skills.
# Catches: a skill that lives on disk under data/forge/skills/
# installed/ but didn't get picked up by the daemon's skill
# loader (silent miss). Same shape as section 04 does for tools.
#
# Real per-skill dispatch (the full intent per ADR-0079) is
# trickier — each skill has different input requirements, and
# picking a valid input set generically is brittle. Deferred to
# a future tranche where we ship per-skill "smoke fixtures" that
# capture a minimal valid args block. For now, the on-disk ↔
# /skills cross-check catches the same class as section 04 caught
# for tools and is genuinely useful.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-07-skill-smoke"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

DAEMON="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
ENV_FILE="$REPO_ROOT/.env"
TOKEN="${FSF_API_TOKEN:-$(grep -E '^FSF_API_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)}"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$REPORT" <<HEADER
# Diagnostic Section 07 — skill smoke (MVP)

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- daemon: $DAEMON
- scope: MVP — installed-on-disk ↔ /skills cross-check.
  Full per-skill dispatch deferred to a later tranche.

HEADER

if ! curl -s --max-time 5 "$DAEMON/healthz" >/dev/null 2>&1; then
  echo "## Result" >> "$REPORT"
  echo "" >> "$REPORT"
  echo "- aborted: daemon unreachable at $DAEMON" >> "$REPORT"
  echo "section 07: daemon unreachable"
  exit 1
fi

PY="$REPO_ROOT/.venv/bin/python3"
[ -x "$PY" ] || PY=python3

cd "$REPO_ROOT"

"$PY" - "$REPORT" "$DAEMON" "$TOKEN" <<'PYEOF'
"""Section 07 driver — installed-skill ↔ /skills cross-check."""
import json
import sys
import urllib.request
from pathlib import Path

REPORT, DAEMON, TOKEN = sys.argv[1:4]
REPO = Path.cwd()

INSTALL_DIR = REPO / "data" / "forge" / "skills" / "installed"
on_disk_files = sorted(INSTALL_DIR.glob("*.yaml"))
on_disk_names = set()
for p in on_disk_files:
    # filename is "<name>.v<version>.yaml"
    stem = p.stem  # e.g., "archive_evidence.v1"
    on_disk_names.add(stem)

# fetch /skills
req = urllib.request.Request(DAEMON + "/skills")
req.add_header("X-FSF-Token", TOKEN)
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
except Exception as e:
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(f"## Result\n\n- aborted: /skills fetch failed: {e}\n")
    print("section 07: /skills fetch failed")
    sys.exit(1)

registered = set()
for s in data.get("skills", []):
    if isinstance(s, dict):
        name = s.get("name")
        ver = s.get("version")
        if name and ver:
            registered.add(f"{name}.v{ver}")
    elif isinstance(s, str):
        registered.add(s)

missing = sorted(on_disk_names - registered)
extra = sorted(registered - on_disk_names)

results: list[tuple[str, str, str]] = []
if missing:
    results.append(("FAIL", "every on-disk skill listed in /skills",
                    f"{len(missing)} missing: {', '.join(missing[:8])}"))
else:
    results.append(("PASS", "every on-disk skill listed in /skills",
                    f"{len(on_disk_names)} installed skills, all registered"))

if extra:
    results.append(("FAIL", "no orphan /skills registrations",
                    f"{len(extra)} extra: {', '.join(extra[:8])}"))
else:
    results.append(("PASS", "no orphan /skills registrations",
                    f"{len(registered)} registered, all on disk"))

passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")

with open(REPORT, "a", encoding="utf-8") as f:
    f.write(f"## Result\n\n- total: {len(results)}\n")
    f.write(f"- passed: {passed}\n- failed: {failed}\n\n## Checks\n\n")
    for s, n, ev in results:
        f.write(f"- **[{s}]** {n} — {ev}\n")

print(f"section 07: {passed}/{len(results)} passed")
sys.exit(0 if failed == 0 else 1)
PYEOF

RC=$?
echo "----"
cat "$REPORT" | tail -20
echo "----"
echo "section 07 exit: $RC"
exit "$RC"
