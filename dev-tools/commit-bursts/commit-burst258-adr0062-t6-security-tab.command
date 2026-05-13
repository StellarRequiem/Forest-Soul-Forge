#!/bin/bash
# Burst 258 — ADR-0062 T6: SoulUX Security tab.
#
# Closes ADR-0062 entirely. Operator-facing view of the
# supply-chain scanner substrate. Same shape as ADR-0063 T7's
# Reality tab (B256). Two ADRs, two operator-facing panes,
# parallel architecture.
#
# Files:
#
# 1. src/forest_soul_forge/daemon/routers/security.py (NEW)
#    Five endpoints under /security:
#      GET  /status — combined summary card
#      GET  /iocs — full IoC catalog, CRITICAL-first
#      GET  /recent-scans — agent_security_scan_completed events
#      GET  /quarantined — staged dirs with REJECTED.md
#      POST /reload — hot-reload catalog
#    Read-only by design. Surface_counts shows which gate
#    fired (marketplace install vs forge stage vs etc.) so
#    the operator can answer 'where are refusals happening?'
#
# 2. src/forest_soul_forge/daemon/app.py
#    Imports + mounts the new router.
#
# 3. frontend/index.html
#    New 'Security' tab in the nav. Four panel sections:
#    status card, IoC catalog table, recent scans timeline,
#    quarantined proposals list. Reload + Refresh buttons.
#
# 4. frontend/js/security.js (NEW)
#    Module wires the panel. Lazy-loads on first tab click
#    (no fetch cost when the tab is unused). Reuses
#    severity-chip + table styles from B256.
#
# 5. frontend/js/app.js
#    Imports + starts the new module.
#
# 6. frontend/css/style.css
#    ~50 lines of new CSS — security-specific cells
#    (surface chips, decision colors, quarantine row, marker
#    excerpt). Reuses ra-table + ra-event-row + chip--sev-*
#    from the Reality pane.
#
# 7. tests/unit/test_daemon_security.py (NEW)
#    11 endpoint tests covering every surface:
#      - GET /status returns rule count + 24h counts + ADR list
#      - GET /iocs returns full rule shape
#      - GET /iocs orders CRITICAL first
#      - GET /recent-scans empty chain → empty
#      - GET /recent-scans filters non-scan events
#      - GET /quarantined empty when clean
#      - POST /reload returns post-reload state
#
# 8. docs/decisions/ADR-0062-supply-chain-scanner.md
#    Status: CLOSED 2026-05-12. T6 row marked DONE B258.
#    Total: 6/6 shipped across 4 bursts (B249, B250, B257, B258).
#
# Per ADR-0062 D1: operator owns the IoC catalog via the YAML
#   file. UI is read-only + reload; no in-UI editing.
# Per CLAUDE.md §0 Hippocratic gate: closure tranche adds
#   visibility, not new refuse-paths. The substrate gates
#   (T3, T4, T5) remain the only refuse points.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/routers/security.py \
        src/forest_soul_forge/daemon/app.py \
        frontend/index.html \
        frontend/js/security.js \
        frontend/js/app.js \
        frontend/css/style.css \
        tests/unit/test_daemon_security.py \
        docs/decisions/ADR-0062-supply-chain-scanner.md \
        dev-tools/commit-bursts/commit-burst258-adr0062-t6-security-tab.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0062 CLOSED — SoulUX Security tab T6 (B258)

Burst 258. Closes ADR-0062 entirely. 6/6 tranches shipped
across B249, B250, B257, B258. Supply-chain scanner is now
operator-facing at every surface:
  - T1-T2-T3 IoC catalog + security_scan.v1 builtin (B249)
  - T4 install-time gate (B250)
  - T5 forge-stage scanner + REJECTED.md quarantine (B257)
  - T6 SoulUX Security tab + /security/* router (B258)

New /security router exposes five operator-facing endpoints:
GET /status (combined summary with 24h refused/allowed/
critical counts + per-surface breakdown + quarantined-dir
count), /iocs (catalog sorted CRITICAL-first), /recent-scans
(agent_security_scan_completed events from the chain),
/quarantined (staged dirs with REJECTED.md including the
marker excerpt so operator sees WHY without opening files),
POST /reload (hot-reload catalog).

New SoulUX 'Security' tab with four sections: status card,
IoC catalog table with severity chips, recent scans timeline
showing decision/install_kind/finding counts, quarantined
proposals panel. Lazy-loads on first tab click. Reuses
the severity-chip + table styles from B256's Reality pane.

Read-only by design per ADR-0062 D1 — operator edits
config/security_iocs.yaml on disk and clicks Reload. No
in-UI editing.

Tests: 11 endpoint cases covering every surface.

ADR-0062 status: CLOSED 2026-05-12. Forest now ships full
defense-in-depth against the 2025-26 npm Shai-Hulud /
PyPI / Axios / MCP-STDIO supply-chain attack family at
every artifact-lifecycle stage: propose, install, dispatch."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 258 complete ==="
echo "=== ADR-0062 CLOSED. Security pane live in SoulUX. ==="
echo "Press any key to close."
read -n 1
