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
  # B366 - browser-driven smoke (Playwright + DOM text inspection).
  # Catches frontend boot regressions section-13's API-level
  # probe can't see (raw fetch bypass, boot-asymmetry, JS exceptions
  # mid-render). Slower (~20s) because it drives a real Chromium;
  # gracefully degrades to SKIPPED when playwright/chromium aren't
  # installed (offline operator boxes).
  "14-browser-smoke"
  # B394 / ADR-0081 T1 - substrate wiring cross-check. Asks
  # cross-cutting questions the per-layer sections (04/05/09) can't:
  # cataloged tools without any carrier, skills routed via handoffs
  # that no archetype can actually run, orphan tools, and handoff
  # routes whose terminal skill or required tools are missing. The
  # B363/B392 gap class surfaces here within seconds of introduction.
  # Daemon-independent; reads disk only.
  "15-wiring-cross-check"
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

# ---- B367: single browsable index.html ------------------------------------
# Operator-friendly UX: a single page with the tally at top, per-section
# deep-links into each report.md, and inline thumbnails for the section-14
# tab screenshots. Markdown stays the canonical machine-readable artifact;
# the HTML is its visual sibling.
INDEX_HTML="$RUN_ROOT/index.html"
SHOT_DIR_REL="../diagnostic-14-browser-smoke/screenshots"

# Build per-section status badge HTML.
section_rows=""
for i in "${!SECTIONS[@]}"; do
  section="${SECTIONS[$i]}"
  rc="${SECTION_RC[$i]}"
  dur="${SECTION_DUR[$i]}s"
  case "$rc" in
    0)        status="PASS"; color="#2e7d32" ;;
    missing)  status="MISSING"; color="#888" ;;
    *)        status="FAIL"; color="#c62828" ;;
  esac
  rpt_rel="../diagnostic-${section}/report.md"
  section_rows="$section_rows<tr><td>$((i+1))</td><td>$section</td><td style=\"background:$color;color:#fff;padding:2px 8px;border-radius:3px;\">$status</td><td>$dur</td><td><a href=\"$rpt_rel\">report.md</a></td></tr>"
done

# Build screenshot gallery from section-14 (B366) outputs, if present.
shots_block=""
shots_dir="$REPO_ROOT/data/test-runs/diagnostic-14-browser-smoke/screenshots"
if [ -d "$shots_dir" ]; then
  shots_block="<h2>Tab screenshots (section 14)</h2><div style=\"display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;\">"
  for shot in "$shots_dir"/*.png; do
    [ -e "$shot" ] || continue
    base=$(basename "$shot" .png)
    shots_block="$shots_block<figure style=\"margin:0;\"><img src=\"$SHOT_DIR_REL/$(basename "$shot")\" style=\"width:100%;height:auto;border:1px solid #ccc;border-radius:4px;\"><figcaption style=\"font-family:monospace;font-size:12px;text-align:center;padding:4px 0;\">$base</figcaption></figure>"
  done
  shots_block="$shots_block</div>"
fi

# Pull the consolidated FAIL list out of summary.md verbatim so the
# index mirrors the markdown.
fail_block=$(awk '/^## Consolidated punch list/,/^## Tally/' "$SUMMARY" | sed '$d')

cat > "$INDEX_HTML" <<EOF
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>FSF diagnostic — $TIMESTAMP</title>
  <style>
    body { font-family: -apple-system, system-ui, sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 16px; color: #222; }
    h1 { font-size: 22px; margin: 0 0 6px; }
    h2 { font-size: 16px; margin: 24px 0 8px; }
    .meta { color: #666; font-size: 13px; margin-bottom: 16px; }
    table { border-collapse: collapse; width: 100%; font-size: 14px; }
    th, td { padding: 6px 10px; border-bottom: 1px solid #eee; text-align: left; }
    .tally { font-size: 14px; padding: 12px; background: #f5f5f5; border-radius: 4px; margin: 12px 0 24px; }
    .tally b { font-size: 18px; }
    pre { background: #f8f8f8; padding: 10px; border-radius: 4px; white-space: pre-wrap; font-size: 13px; }
    a { color: #1565c0; }
  </style>
</head>
<body>
  <h1>FSF diagnostic — $TIMESTAMP</h1>
  <div class="meta">
    git SHA: <code>$GIT_SHA</code>
    &nbsp;&middot;&nbsp; run id: <code>diagnostic-all-$TIMESTAMP</code>
    &nbsp;&middot;&nbsp; sections: ${#SECTIONS[@]}
  </div>
  <div class="tally">
    <b>$pass PASS</b> &nbsp;/&nbsp; <b style="color:#c62828">$fail FAIL</b>${missing:+ &nbsp;/&nbsp; <b style="color:#888">$missing MISSING</b>}
  </div>
  <h2>Section results</h2>
  <table>
    <thead><tr><th>#</th><th>Section</th><th>Status</th><th>Duration</th><th>Report</th></tr></thead>
    <tbody>$section_rows</tbody>
  </table>
  <h2>Consolidated punch list</h2>
  <pre>$fail_block</pre>
  $shots_block
  <h2>Source artifacts</h2>
  <ul>
    <li>Machine-readable summary: <a href="summary.md">summary.md</a></li>
    <li>Per-section stdout: <code>section-*.stdout.log</code> in this directory</li>
    <li>Per-section reports: linked in the table above</li>
  </ul>
</body>
</html>
EOF

echo
echo "=========================================================="
echo "Summary:"
echo "  $pass PASS / $fail FAIL${missing:+ / $missing MISSING}"
echo "  Full summary: $SUMMARY"
echo "  Browsable:    $INDEX_HTML"
echo "=========================================================="
echo
echo "Press any key to close."
read -n 1 || true

# Exit non-zero if any section failed — useful for shell composition.
if [ "$fail" -gt 0 ] || [ "$missing" -gt 0 ]; then
  exit 1
fi
exit 0
