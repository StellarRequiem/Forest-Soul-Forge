#!/usr/bin/env bash
# ADR-0093 — section-16 chronological-canon drift gate.
#
# Re-measures disk and checks STATE.md's CANON block + the README headline
# against it. FAILs if any documented content count (LoC, ADRs, builtin tools,
# structural test count, package version, latest tag) has drifted from disk.
# Provenance (commit sha/count) + runtime (agents/audit) are informational and
# never fail this section. Daemon-independent: reads disk only.
#
# Wired into the daily 8am diagnostic harness so doc-vs-disk drift is caught
# automatically, not only at commit (pre-commit hook) or in CI (canon.yml).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
RUN_ID="diagnostic-16-canon-drift"
TARGET="$REPO_ROOT/data/test-runs/$RUN_ID"
REPORT="$TARGET/report.md"
mkdir -p "$TARGET"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
PY="$REPO_ROOT/.venv/bin/python3"; [ -x "$PY" ] || PY=python3

cat > "$REPORT" <<HEADER
# Diagnostic Section 16 — chronological-canon drift gate (ADR-0093)

- timestamp: $TIMESTAMP
- git SHA: $GIT_SHA
- scope: STATE.md CANON block + README headline vs disk. Daemon-independent.

HEADER

cd "$REPO_ROOT"
GATE_OUT=$("$PY" dev-tools/state_canon.py --check 2>&1); RC=$?
echo "$GATE_OUT" > "$TARGET/gate.stdout.log"

{
  echo "## Result"
  echo
  if [ "$RC" -eq 0 ]; then
    echo "- **[PASS]** chronological canon — STATE.md CANON + README headline match disk on all gated content fields."
  else
    echo "- **[FAIL]** chronological canon — documented counts drifted from disk. Fix: \`python3 dev-tools/state_canon.py --emit\` then commit. Drifted fields:"
    echo
    echo '```'
    echo "$GATE_OUT" | grep -E "⛔|DRIFT|README:" || true
    echo '```'
  fi
  echo
  echo "## Full gate output"
  echo
  echo '```'
  echo "$GATE_OUT"
  echo '```'
} >> "$REPORT"

echo "----"; tail -24 "$REPORT"; echo "----"
echo "section 16 exit: $RC"
exit "$RC"
