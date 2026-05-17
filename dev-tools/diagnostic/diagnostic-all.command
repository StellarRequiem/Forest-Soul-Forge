#!/usr/bin/env bash
# ADR-0079 T6 umbrella — runs all 13 diagnostic sections + writes
# an aggregated summary.md to data/test-runs/diagnostic-all-<ts>/.
#
# Per ADR-0079 D3: fail loud per section but the umbrella runs
# EVERY section regardless and surfaces all failures in the final
# summary. The operator wants the full punch list, not the first
# failure.
#
# Per ADR-0079 D2: sections are sequenced numerically. If a load-
# bearing prior section fails, later sections still run but the
# summary marks them as "ran-on-broken-substrate" so the operator
# knows downstream results are suspect.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
RUN_ROOT="$REPO_ROOT/data/test-runs/diagnostic-all-$TIMESTAMP"
SUMMARY="$RUN_ROOT/summary.md"
mkdir -p "$RUN_ROOT"

GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "no-git")

cat > "$SUMMARY" <<HEADER
# Diagnostic harness — full run

- timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
- git SHA: $GIT_SHA
- run id: diagnostic-all-$TIMESTAMP

HEADER

echo "=========================================================="
echo "ADR-0079 diagnostic harness — full run"
echo "  $TIMESTAMP"
echo "=========================================================="
echo

# Section list — order matters (later sections depend on earlier
# substrate being green). The runner always invokes ALL of them
# regardless of any individual failure; the summary marks broken
# substrate.
SECTIONS=(
  "01-static-config"
  "02-skill-manifests"
  "03-boot-health"
  "04-tool-registration"
  "05-agent-inventory"
  "06-ctx-wiring"
  "07-skill-smoke"
  "08-audit-chain-forensics"
  "09-handoff-routing"
  "10-cross-domain-orchestration"
  "11-memory-retention"
  "12-encryption-at-rest"
  "13-frontend-integration"
)

declare -a SECTION_RC=()
declare -a SECTION_DUR=()

for section in "${SECTIONS[@]}"; do
  script="$HERE/section-$section.command"
  if [ ! -x "$script" ]; then
    echo "[$section] MISSING script: $script"
    SECTION_RC+=("missing")
    SECTION_DUR+=("0")
    continue
  fi
  echo "[$section] running..."
  start=$(date +%s)
  bash "$script" < /dev/null > "$RUN_ROOT/section-$section.stdout.log" 2>&1
  rc=$?
  end=$(date +%s)
  dur=$((end - start))
  SECTION_RC+=("$rc")
  SECTION_DUR+=("$dur")
  case "$rc" in
    0) echo "[$section] PASS (${dur}s)" ;;
    *) echo "[$section] FAIL rc=$rc (${dur}s) — see report.md" ;;
  esac
done

# ---- Aggregate summary ----------------------------------------------------
{
  echo "## Section results"
  echo
  echo "| # | Section | Status | Duration | Report |"
  echo "|---|---|---|---|---|"
  for i in "${!SECTIONS[@]}"; do
    section="${SECTIONS[$i]}"
    rc="${SECTION_RC[$i]}"
    dur="${SECTION_DUR[$i]}s"
    case "$rc" in
      0) status="PASS" ;;
      missing) status="MISSING" ;;
      *) status="FAIL (rc=$rc)" ;;
    esac
    report_path="data/test-runs/diagnostic-${section}/report.md"
    echo "| $((i+1)) | $section | $status | $dur | \`$report_path\` |"
  done
  echo
} >> "$SUMMARY"

# Consolidated FAIL list — pull the FAIL lines out of each section's
# report.md so the operator gets a single punch list.
{
  echo "## Consolidated punch list (FAILs across all sections)"
  echo
  any=0
  for section in "${SECTIONS[@]}"; do
    report="$REPO_ROOT/data/test-runs/diagnostic-${section}/report.md"
    if [ -f "$report" ]; then
      fails=$(grep -E "^- \*\*\[FAIL\]\*\*" "$report" 2>/dev/null || true)
      if [ -n "$fails" ]; then
        any=1
        echo "### section $section"
        echo
        echo "$fails"
        echo
      fi
    fi
  done
  if [ "$any" -eq 0 ]; then
    echo "No FAILs surfaced. Substrate is green."
  fi
  echo
} >> "$SUMMARY"

# Final tally
pass=0; fail=0; missing=0
for rc in "${SECTION_RC[@]}"; do
  case "$rc" in
    0) pass=$((pass+1)) ;;
    missing) missing=$((missing+1)) ;;
    *) fail=$((fail+1)) ;;
  esac
done

{
  echo "## Tally"
  echo
  echo "- sections run: ${#SECTIONS[@]}"
  echo "- PASS: $pass"
  echo "- FAIL: $fail"
  if [ "$missing" -gt 0 ]; then
    echo "- MISSING: $missing"
  fi
} >> "$SUMMARY"

echo
echo "=========================================================="
echo "Summary:"
echo "  $pass PASS / $fail FAIL${missing:+ / $missing MISSING}"
echo "  Full summary: $SUMMARY"
echo "=========================================================="
echo
echo "Press any key to close."
read -n 1 || true

# Exit non-zero if any section failed — useful for shell composition.
if [ "$fail" -gt 0 ] || [ "$missing" -gt 0 ]; then
  exit 1
fi
exit 0
