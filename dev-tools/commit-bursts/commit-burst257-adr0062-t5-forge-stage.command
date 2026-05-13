#!/bin/bash
# Burst 257 — ADR-0062 T5: forge-stage scanner.
#
# Closes ADR-0062 T5. Catches malicious LLM-generated artifacts
# at STAGE time — before the operator ever sees the propose
# result — in addition to the existing T4 install-time gate
# (B250). Two layers, two attack-vector classes:
#
#   T4 (install gate) — operator clicks install on staged
#                        proposal → CRITICAL findings refuse
#   T5 (stage gate)   — forge engine emits LLM output →
#                        CRITICAL findings refuse + write
#                        REJECTED.md to staged dir
#
# Files:
#
# 1. src/forest_soul_forge/daemon/forge_stage_scanner.py (NEW)
#    Shared helper. Public surface:
#      scan_forge_stage_or_refuse(staged_dir, forge_kind,
#                                 audit_chain, ...)
#        → scan summary on ALLOW
#        → ForgeStageRefused on CRITICAL (after writing REJECTED.md)
#      staged_dir_is_quarantined(staged_dir) → bool
#        — install endpoints call this to refuse promoting
#          a quarantined dir to live.
#    Reuses scan_install_or_refuse under the hood; only
#    differences are install_kind tag + REJECTED.md side-effect.
#
# 2. src/forest_soul_forge/daemon/routers/skills_forge.py
#    Imports the helper. Inserts the scan call between
#    forge_skill() return and forge_skill_proposed audit emit.
#    CRITICAL → 409 with structured findings + REJECTED.md
#    on disk. HIGH/MEDIUM/LOW → 200 with scan_summary in
#    ForgedSkillOut. Install endpoint refuses staged dirs
#    that contain REJECTED.md (separate 409).
#
# 3. src/forest_soul_forge/daemon/routers/tools_forge.py
#    Same wiring as skills_forge. Tool forge is HIGHEST-risk
#    because the staged artifact is LLM-generated Python.
#    ForgedToolOut.scan_summary added.
#
# 4. tests/unit/test_forge_stage_scanner.py (NEW)
#    12 tests:
#      - clean stage allows + no REJECTED.md
#      - HIGH-only allows + flags (no refuse)
#      - CRITICAL refuses + writes REJECTED.md
#      - REJECTED.md content includes severity tier + findings
#      - staged_dir_is_quarantined() predicate on all 3 states
#        (clean / manually-written / nonexistent dir)
#      - audit event lands in both allow + refuse paths
#        with install_kind set correctly per surface
#
# 5. docs/decisions/ADR-0062-supply-chain-scanner.md
#    Status: T1+T2+T3+T4+T5 shipped. T5 row marked DONE B257
#    with full implementation detail. Only T6 (SoulUX
#    Security tab) remains.
#
# Sandbox smoke (2 scenarios via standalone driver):
#   1. clean staged dir → allow ✓
#   2. critical staged dir → REFUSED + REJECTED.md written
#      + staged_dir_is_quarantined() returns True ✓
#
# Per ADR-0062 D1: refuse-only-on-CRITICAL policy preserved
#   at this surface. HIGH stays warn-only at stage time;
#   operator can pass strict=true at install time to escalate.
# Per ADR-0062 D2 (B250 ordering): the forge stage gate
#   runs FIRST (no operator approval yet); the install gate
#   runs SECOND (operator has approved the proposal). Two
#   layers, two protections.
# Per CLAUDE.md §0 Hippocratic gate: structural gate via
#   REJECTED.md marker prevents accidental install
#   bypass; only an operator who consciously deletes the
#   marker overrides.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/forge_stage_scanner.py \
        src/forest_soul_forge/daemon/routers/skills_forge.py \
        src/forest_soul_forge/daemon/routers/tools_forge.py \
        tests/unit/test_forge_stage_scanner.py \
        docs/decisions/ADR-0062-supply-chain-scanner.md \
        dev-tools/commit-bursts/commit-burst257-adr0062-t5-forge-stage.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0062 T5 forge-stage scanner (B257)

Burst 257. Closes ADR-0062 T5. Catches malicious LLM-generated
artifacts at STAGE time, before the operator ever sees the
propose result. Complements the existing T4 install-time gate
(B250) — two layers covering two attack-vector classes:
  - T4: operator clicks install on staged proposal
  - T5: forge engine emits LLM output to staged dir

New daemon/forge_stage_scanner.py helper wraps the install
scanner with two changes: install_kind tag is forge_skill_stage
/ forge_tool_stage (so chain queries separate stage vs install
refusals), and on CRITICAL refusal it writes a human-readable
REJECTED.md to the staged dir documenting findings + remediation.

Wired into both /skills/forge and /tools/forge between forge
engine return and audit emit. CRITICAL findings refuse with 409
+ REJECTED.md on disk. HIGH/MEDIUM/LOW findings flow into
ForgedSkillOut.scan_summary / ForgedToolOut.scan_summary so the
propose-card UI can surface a warning chip.

The install endpoints (/skills/install, /tools/install) gained
a staged_dir_is_quarantined() structural check: 409 if
REJECTED.md is present, forcing operators to consciously
delete the marker to bypass.

Tests: 12 cases covering allow / HIGH-only-allow / CRITICAL-
refuse / REJECTED.md content / quarantine predicate on three
states / audit-event landing in both paths with install_kind
set correctly per surface.

ADR-0062 status: T1+T2+T3+T4+T5 shipped. T6 (SoulUX Security
tab — analog of the closed ADR-0063 Reality tab) is the
final tranche.

Per CLAUDE.md §0 Hippocratic gate: structural gate via
REJECTED.md marker prevents accidental install bypass; only
an operator who consciously deletes it overrides."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 257 complete ==="
echo "=== ADR-0062 T5 live. Forge-stage scanner refuses CRITICAL output. ==="
echo "Press any key to close."
read -n 1
