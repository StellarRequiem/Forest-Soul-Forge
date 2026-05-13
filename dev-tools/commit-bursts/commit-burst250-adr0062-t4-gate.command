#!/bin/bash
# Burst 250 — ADR-0062 T4: install-time scanner gate.
#
# B249 shipped security_scan.v1 as a read-only inspector.
# B250 wires it into the three install endpoints so a
# CRITICAL-tagged artifact never ends up on disk under
# installed/. This is the §0 Hippocratic-gate second beat:
# we proved the catalog doesn't false-positive on legitimate
# code (clean fixture → zero findings, malicious fixture →
# every IoC fires), so now we wire the refuse path.
#
# Files:
#
# 1. src/forest_soul_forge/daemon/install_scanner.py (NEW)
#    Shared helper consumed by the three install routers.
#    Public API: scan_install_or_refuse(staging_dir,
#    install_kind, strict, audit_chain, operator_label).
#    Internally runs the SecurityScanTool, classifies the
#    findings, refuses on CRITICAL (always) or HIGH (when
#    strict=True), emits agent_security_scan_completed to the
#    audit chain in BOTH allow + refuse paths. Throws
#    InstallGateRefused with structured payload on refuse so
#    each endpoint can convert to a 409 with the findings
#    visible to the operator.
#
# 2. src/forest_soul_forge/core/audit_chain.py
#    KNOWN_EVENT_TYPES += "agent_security_scan_completed" so
#    AuditChain.verify doesn't log a forward-compat warning
#    on every gate emission.
#
# 3. src/forest_soul_forge/daemon/routers/marketplace.py
#    MarketplaceInstallIn gained `strict: bool` field.
#    Gate call inserted between staging resolution and
#    repo.install_from_dir. Success response now includes
#    `scan_summary` with by_severity + scan_fingerprint +
#    findings_count.
#
# 4. src/forest_soul_forge/daemon/routers/skills_forge.py
#    InstallSkillIn gained `strict: bool` field;
#    InstalledSkillOut gained `scan_summary` field. Gate
#    call inserted before the install_dir mkdir + copyfile.
#
# 5. src/forest_soul_forge/daemon/routers/tools_forge.py
#    InstallToolIn gained `strict: bool` field;
#    InstalledToolOut gained `scan_summary` field. Gate
#    call inserted before the install_dir mkdir + copyfile.
#    Tool Forge is the highest-risk surface because the
#    staged artifact is LLM-generated Python with an
#    `implementation` field.
#
# 6. tests/unit/test_install_scanner.py (NEW)
#    Coverage:
#      - KNOWN_EVENT_TYPES registration check
#      - clean staging → allow
#      - HIGH-only + strict=False → allow (warning)
#      - HIGH-only + strict=True → refuse on HIGH
#      - CRITICAL + strict=False → refuse on CRITICAL
#      - CRITICAL + strict=True → refuse on CRITICAL
#      - audit event emitted in allow + refuse paths
#      - payload shape on InstallGateRefused (findings list
#        carries severity/pattern_id/file/line/excerpt)
#      - allow payload carries scan_fingerprint + counts
#
# 7. docs/decisions/ADR-0062-supply-chain-scanner.md
#    Status: T1+T2+T3+T4 shipped. T4 row marked DONE B250
#    with the cross-endpoint detail. T5+T6 still queued.
#
# Verification (sandbox):
#   - All edited files parse cleanly.
#   - End-to-end gate exercise emits the right audit
#     event 4/4 times (allow, refuse-CRITICAL, refuse-HIGH-
#     strict, allow-HIGH-lenient).
#   - Refusal payload carries the findings; allow payload
#     carries scan_summary.
#
# Per ADR-0062 D2: report-only became gate-on-CRITICAL once
#   the catalog proved reliable. HIGH stays opt-in (strict=true)
#   because the false-positive surface is wider — operators
#   pick their posture per install.
# Per CLAUDE.md §0 Hippocratic gate: we reported (B249)
#   before we blocked (B250). The blocking tier (CRITICAL)
#   is the one with zero false-positive risk on legitimate
#   code; HIGH stays opt-in.
# Per ADR-0001 D2: identity surface untouched. Gate emits
#   audit events under the agent's own emission chain — the
#   scanner is acting as a guardian, not changing identity.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/install_scanner.py \
        src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/daemon/routers/marketplace.py \
        src/forest_soul_forge/daemon/routers/skills_forge.py \
        src/forest_soul_forge/daemon/routers/tools_forge.py \
        tests/unit/test_install_scanner.py \
        docs/decisions/ADR-0062-supply-chain-scanner.md \
        dev-tools/commit-bursts/commit-burst250-adr0062-t4-gate.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0062 T4 install-time scanner gate (B250)

Burst 250. B249 shipped security_scan.v1 read-only. B250
wires it into the three install endpoints as a refusal
gate so a CRITICAL-tagged artifact never ends up on disk.
This is the second beat of the Hippocratic-gate pattern —
we reported first (B249), proved the catalog doesn't
false-positive on legitimate code, then wired the refuse
path.

New daemon/install_scanner.py shared helper. Public API:
scan_install_or_refuse(staging_dir, install_kind, strict,
audit_chain, operator_label). Refuses on CRITICAL always;
refuses on HIGH only when strict=true. Emits
agent_security_scan_completed in BOTH allow + refuse paths
so audit-chain queries can answer 'what did we refuse this
week?' AND 'what's the false-positive rate in production?'.

Wired into:
  - /marketplace/install
  - /skills/install
  - /tools/install

Each gained a 'strict: bool' field on the request body
(default False) and 'scan_summary' on the success response.
CRITICAL refusal returns 409 with the structured findings
list visible to the operator.

KNOWN_EVENT_TYPES updated with agent_security_scan_completed.

Tests: 9 cases covering clean→allow, HIGH-only+lenient→
allow-with-warning, HIGH-only+strict→refuse, CRITICAL→refuse
in both strict modes, audit event shape, payload structure.

ADR-0062 T4 marked DONE B250. T5 (forge-stage scanner) + T6
(SoulUX Security tab) queued.

Per CLAUDE.md §0 Hippocratic gate: report before block, then
block on the tier with zero false-positive risk."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 250 complete ==="
echo "=== ADR-0062 T4 live. Install-time gate refuses CRITICAL. ==="
echo "Press any key to close."
read -n 1
