#!/bin/bash
# Burst 352 - ADR-0079 T2: diagnostic sections 01-04.
#
# Four section drivers, each a standalone .command writing a
# structured report.md to data/test-runs/diagnostic-NN-name/.
# Per ADR-0079 D6, sections are section-as-script (not section-as-
# library) — same shape as the existing live-test-*.command files.
#
# What ships:
#
# 1. dev-tools/diagnostic/section-01-static-config.command:
#    Loads + cross-references every load-bearing YAML config.
#    Checks: trait_tree (6 domains per role, weights in range),
#    genres (every trait-engine role claimed exactly once),
#    constitution_templates (required blocks per role),
#    tool_catalog (every archetype kit tool exists), handoffs
#    (loads cleanly), domain manifests (entry_agents reference
#    real roles). Pure on-disk; needs no daemon.
#
# 2. dev-tools/diagnostic/section-02-skill-manifests.command:
#    Globs examples/skills/ + data/forge/skills/installed/.
#    For each: parse_manifest succeeds, requires-tools exist
#    in catalog, step ids are unique. Catches the
#    archive_evidence.v1 'handoff_to' bug class proactively.
#
# 3. dev-tools/diagnostic/section-03-boot-health.command:
#    Hits /healthz on the live daemon. Checks daemon reachable,
#    startup_diagnostics all-green, daemon SHA matches local HEAD
#    (catches the stale-daemon failure mode that bit us when B350
#    was committed but daemon was still on pre-B350 code).
#
# 4. dev-tools/diagnostic/section-04-tool-registration.command:
#    Cross-checks tool_catalog.yaml against /tools/registered on
#    the live daemon. Surfaces tools claimed in YAML but not
#    registered (silent miss). Also reports orphans (registered
#    but not in catalog).
#
# Section 03 + 04 require a live daemon; 01 + 02 don't. Each
# section is independently runnable. The umbrella runner
# (T6, B357) wires them into one diagnostic-all.command.
#
# Expected first-run findings (based on what live-test surfaced):
#   - Section 02 should flag archive_evidence.v1's handoff_to bug
#     (missing-on-acquire input ref).
#   - Section 03 should be GREEN (daemon was just restarted on B350).
#   - Section 04 may surface drift if any tool entries shipped in
#     the catalog without a registered class.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/diagnostic/section-01-static-config.command \
        dev-tools/diagnostic/section-02-skill-manifests.command \
        dev-tools/diagnostic/section-03-boot-health.command \
        dev-tools/diagnostic/section-04-tool-registration.command \
        dev-tools/commit-bursts/commit-burst352-adr0079-t2-sections-01-04.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(diagnostic): ADR-0079 T2 - sections 01-04 (B352)

Burst 352. Four section drivers for the diagnostic harness:

  01 static-config       — trait_tree / genres / templates /
                           catalog / handoffs / domain manifests
                           parse + cross-reference (pure on-disk;
                           no daemon required)
  02 skill-manifests     — every example + installed skill parses
                           through parse_manifest; requires-tools
                           exist; step ids unique
  03 boot-health         — /healthz reachable; startup_diagnostics
                           all-green; daemon SHA matches local HEAD
                           (catches stale-daemon failure mode)
  04 tool-registration   — every tool_catalog entry registered in
                           /tools/registered; no orphans

Per ADR-0079 D6 each section is a standalone .command driver
(section-as-script, not section-as-library) writing a structured
report.md to data/test-runs/diagnostic-NN-name/. Sections 01-02
need no daemon; 03-04 do. Each is independently runnable. The
umbrella runner lands in T6 (B357).

Expected first-run findings based on whats already surfaced:
  - Section 02 should flag archive_evidence.v1 handoff_to bug
    (missing-on-acquire input ref discovered in live-test)
  - Section 03 should be GREEN (daemon just restarted on B350)
  - Section 04 may surface tool-catalog drift if anything shipped
    a YAML entry without a registered class

Next: B353 - T3 sections 05-07 (agent-inventory + ctx-wiring +
skill-smoke; the B350-class catch zone)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 352 complete - sections 01-04 shipped ==="
echo "Try running individual sections from Finder to surface the"
echo "real punch list — they each exit non-zero on FAIL + write"
echo "report.md to data/test-runs/."
echo ""
echo "Press any key to close."
read -n 1 || true
