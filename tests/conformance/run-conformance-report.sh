#!/bin/bash
# Run the conformance suite + emit a markdown report keyed to spec
# section numbers. ADR-0044 P4 tooling.
#
# Usage:
#   ./tests/conformance/run-conformance-report.sh                  # prints to stdout
#   ./tests/conformance/run-conformance-report.sh -o report.md     # writes to file
#   FSF_DAEMON_URL=https://my-build.example.com ./tests/conformance/run-conformance-report.sh
#
# External integrators run this against their own kernel build and
# share the resulting report.md with a 'this is what conformance
# looks like for our build' message.

set -euo pipefail

OUT="-"
while [ $# -gt 0 ]; do
  case "$1" in
    -o|--output) OUT="$2"; shift 2 ;;
    -h|--help)
      head -16 "$0" | tail -15
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

URL="${FSF_DAEMON_URL:-http://127.0.0.1:7423}"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GIT_SHA=$(git -C "$(dirname "$0")/../.." rev-parse --short HEAD 2>/dev/null || echo "unknown")

# Run pytest with junit XML output for machine-parseable results.
JUNIT_OUT=$(mktemp -t conformance.XXXXXX.xml)
trap 'rm -f "$JUNIT_OUT"' EXIT

pushd "$(dirname "$0")/../.." > /dev/null
PYTHONPATH=src python3 -m pytest tests/conformance/ \
  --junitxml="$JUNIT_OUT" \
  -q \
  --tb=no \
  --no-header \
  > /dev/null 2>&1 || true  # we want the report even if some tests fail
popd > /dev/null

# Parse junit XML and emit markdown.
python3 - "$JUNIT_OUT" "$URL" "$TS" "$GIT_SHA" "$OUT" <<'PYEOF'
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict

junit_path, daemon_url, ts, git_sha, out_path = sys.argv[1:6]
tree = ET.parse(junit_path)
root = tree.getroot()

# pytest produces <testsuite> directly under <testsuites>.
suite = root.find("testsuite") if root.tag == "testsuites" else root

total = int(suite.get("tests", 0))
failures = int(suite.get("failures", 0))
errors = int(suite.get("errors", 0))
skipped = int(suite.get("skipped", 0))
passed = total - failures - errors - skipped
duration = float(suite.get("time", 0.0))

# Group tests by spec section (file naming convention test_sectionN_*).
by_section = defaultdict(list)
for tc in suite.findall("testcase"):
    classname = tc.get("classname", "")
    name = tc.get("name", "")
    # classname like "tests.conformance.test_section3_plugin_manifest"
    section = "?"
    parts = classname.split(".")
    for p in parts:
        if p.startswith("test_section") and len(p) > 12:
            try:
                section = p[12]  # the digit after "test_section"
            except IndexError:
                section = "?"
            break

    status = "PASS"
    failure_msg = None
    if tc.find("failure") is not None:
        status = "FAIL"
        failure_msg = tc.find("failure").get("message", "")
    elif tc.find("error") is not None:
        status = "ERROR"
        failure_msg = tc.find("error").get("message", "")
    elif tc.find("skipped") is not None:
        status = "SKIP"
        failure_msg = tc.find("skipped").get("message", "")
    by_section[section].append((name, status, failure_msg))

# Render markdown.
SECTIONS = {
    "1": "Tool dispatch protocol",
    "2": "Audit chain schema",
    "3": "Plugin manifest schema v1",
    "4": "Constitution.yaml schema",
    "5": "HTTP API contract",
    "6": "CLI surface",
    "7": "Schema migrations",
}

lines = [
    "# Forest kernel API conformance report",
    "",
    f"- **Daemon URL:** `{daemon_url}`",
    f"- **Run timestamp (UTC):** {ts}",
    f"- **Forest commit (kernel under test):** `{git_sha}`",
    "",
    "## Summary",
    "",
    f"- **Total:** {total}",
    f"- **Passed:** {passed}",
    f"- **Failed:** {failures}",
    f"- **Errored:** {errors}",
    f"- **Skipped:** {skipped}",
    f"- **Duration:** {duration:.2f}s",
    "",
    "## Per-section results",
    "",
    "Tests are organized by spec section in `docs/spec/kernel-api-v0.6.md`.",
    "",
]

for section_num in sorted(SECTIONS.keys()):
    section_name = SECTIONS[section_num]
    tests = by_section.get(section_num, [])
    if not tests:
        lines.append(f"### §{section_num} — {section_name} (no tests run)")
        lines.append("")
        continue

    section_passed = sum(1 for _, s, _ in tests if s == "PASS")
    section_failed = sum(1 for _, s, _ in tests if s in ("FAIL", "ERROR"))
    section_skipped = sum(1 for _, s, _ in tests if s == "SKIP")

    lines.append(
        f"### §{section_num} — {section_name} "
        f"({section_passed}/{len(tests)} passed, "
        f"{section_failed} failed, {section_skipped} skipped)"
    )
    lines.append("")
    lines.append("| Test | Result | Notes |")
    lines.append("|---|---|---|")
    for name, status, msg in tests:
        emoji = {"PASS": "✓", "FAIL": "✗", "ERROR": "⚠", "SKIP": "—"}[status]
        notes = ""
        if msg and status in ("FAIL", "ERROR"):
            # Truncate long failure messages.
            notes = msg.replace("\n", " ").strip()[:140]
        elif status == "SKIP" and msg:
            notes = msg[:80]
        lines.append(f"| `{name}` | {emoji} {status} | {notes} |")
    lines.append("")

if failures > 0 or errors > 0:
    lines.append("## Conformance verdict: NOT FULLY COMPATIBLE")
    lines.append("")
    lines.append(
        f"This build fails {failures + errors} test(s) against kernel API "
        "spec v0.6. See per-section failures above. Each failure cites the "
        "specific spec subsection it enforces."
    )
else:
    lines.append("## Conformance verdict: COMPATIBLE")
    lines.append("")
    lines.append(
        "This build passes the v0.6 conformance suite. ABI-compatible "
        "with the spec at the surfaces tested."
    )
lines.append("")

output = "\n".join(lines)
if out_path == "-":
    print(output)
else:
    Path(out_path).write_text(output)
    print(f"Report written to {out_path}", file=sys.stderr)

PYEOF
