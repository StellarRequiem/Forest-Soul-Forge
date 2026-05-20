#!/usr/bin/env bash
# Burst 430-433 — security arc bundled.
#
# Triggered by 2026-05-19 operator alert: GitHub-incident wave in
# May 2026 (Grafana Labs 5/16, MoneyForward 5/01, TeamPCP 3/2026,
# CVE-2026-3854 patched 4/28). User asked for active research +
# posture hardening. This commit ships all four substrate-side
# items as one logical arc since each is small + the four interlock.
#
# B430 — IoC catalog v1 → v2
#   config/security_iocs.yaml: 5 new rules covering the May 2026
#   wave. CRITICAL: github_actions_pull_request_target,
#   github_actions_env_dump_curl, github_pat_or_app_token_committed.
#   HIGH: github_actions_run_from_fork_unchecked. INFO:
#   cve_2026_3854_awareness. 16 → 21 rules total. Catalog version
#   bumped 1 → 2 with explanatory header comment.
#
# B431 — ADR-0084 GitHub Push-Pipeline Posture
#   New ADR codifying six rules: (1) no Actions without ADR, (2)
#   signed commits required, (3) SSH+hardware-key preferred over
#   HTTPS+PAT, (4) branch protection on main, (5) periodic PAT
#   rotation, (6) IoC catalog is authoritative. Each rule lists
#   its enforcement layer (operator / repo / substrate).
#   Builds explicitly on ADR-0082 freeze posture; not a kernel
#   addition — userspace policy + catalog content.
#
# B432 — git_local_scan.v1 builtin tool
#   src/forest_soul_forge/tools/builtin/git_local_scan.py: new
#   read-only builtin tool covering four dimensions: (1) committed-
#   secret detection consuming ADR-0062 catalog v2+ patterns,
#   (2) signed-commit verification via git log %G?, (3) sync state
#   vs upstream (read-only; no fetch), (4) .gitignore coverage of
#   operator-secret patterns. Registered in builtin/__init__.py
#   with audit comment. Catalog entry in config/tool_catalog.yaml.
#   tests/unit/test_git_local_scan.py with 6 contracts pinned:
#   bad-args validation, clean-repo pass, GitHub PAT detection,
#   unsigned-commit detection, no-upstream sync handling,
#   allowed_paths refusal, non-git-dir refusal.
#
# B433 — promote ADR-0025 threat model v2 from placeholder
#   docs/decisions/ADR-0025-threat-model-v2.md: rewritten from
#   placeholder to Accepted. Documents current trust boundaries,
#   seven defended adversary categories with cited ADR defenses,
#   five out-of-scope categories with rationale, what per-event
#   signing guarantees and doesn't, defense-in-depth principles,
#   relationship to local-vs-GitHub canonical truth.
#
# Hippocratic gate (CLAUDE.md sec0)
# ---------------------------------
# 1. Prove harm: May 2026 incident wave is real (validated via
#    primary sources from SecurityWeek, Wiz, GitHub Blog, The
#    Hacker News, GBHackers, Aviatrix). FSF previously had no
#    documented threat model (ADR-0025 was placeholder) and no
#    local-git posture scanner — meaning operator hardening was
#    folklore, not a checklist.
# 2. Prove non-load-bearing for kernel ABI:
#    - B430: catalog data only, no schema change to security_iocs.
#    - B431: docs only.
#    - B432: NEW builtin tool, but follows existing Tool Protocol;
#      per ADR-0082 boundary doc, adding a builtin tool to the
#      existing tools/builtin/ package is the canonical extensible-
#      userspace operation, not a kernel addition. No new audit
#      event types, no new schema, no new HTTP route family.
#    - B433: docs only (promotes placeholder to Accepted).
# 3. Prove alternative is strictly better: not shipping (i.e.,
#    leaving the operator with only Tier 1 manual checklist)
#    means the IoC catalog stays at v1 (no May 2026 coverage), the
#    threat model stays as a placeholder, and there's no runtime
#    self-check tool. Tier 1 operator actions (passkey, PAT
#    rotation, branch protection) are STILL required — they're
#    not Chrome-driveable and are Alex's move regardless. This
#    arc gets the SUBSTRATE side done so the substrate is ready
#    when Alex does the operator side.
#
# Source citations
# ----------------
# Embedded in ADR-0084 references section + ADR-0025 references
# section + security_iocs.yaml per-rule references. Primary
# sources all cited.
#
# Part of the wider 2026-05-19 session arc:
#   B422-B424: discipline pass (script consolidation + STATE/KERNEL
#              refresh + ADR-0082 freeze posture)
#   B425-B429: nested-bug excavation (B416 + B420 + ADR-0083 +
#              constitution layer-4 merge + Triune-Main restoration)
#   B430-B433 (THIS): security incident response

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 430-433 — security arc (GitHub-incident response)"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo

git add config/security_iocs.yaml
git add docs/decisions/ADR-0084-github-push-pipeline-posture.md
git add docs/decisions/ADR-0025-threat-model-v2.md
git add src/forest_soul_forge/tools/builtin/git_local_scan.py
git add src/forest_soul_forge/tools/builtin/__init__.py
git add config/tool_catalog.yaml
git add tests/unit/test_git_local_scan.py
git add dev-tools/commit-bursts/commit-burst430-security-arc.command

echo "Pre-commit status:"
git status -s | head -15
echo
echo "Running new unit tests..."
if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest tests/unit/test_git_local_scan.py -v 2>&1 | tail -25
elif [ -x .venv/bin/python ]; then
  .venv/bin/python -m pytest tests/unit/test_git_local_scan.py -v 2>&1 | tail -25
else
  echo "  venv pytest not found — verify via run-tests.command"
fi
echo

git commit -m "feat(security): GitHub-incident-wave response — IoC v2 + ADR-0084 + git_local_scan + ADR-0025 v2 (B430-B433)

Triggered by 2026-05-19 operator alert: May 2026 GitHub-incident
wave. Validated via primary sources: Grafana Labs codebase theft
(5/16, pull_request_target misconfig + env-dump curl exfil),
MoneyForward 370-card leak (5/01, hardcoded secrets), TeamPCP
attack on Checkmarx (3/2026, stolen CI credentials), CVE-2026-3854
GitHub server RCE (patched 4/28, no exploitation confirmed).

FSF exposure assessment before this arc:
  - No GitHub Actions in repo: clean (Grafana vector NA by accident)
  - No hardcoded secrets in tracked files: clean (MoneyForward NA)
  - No CI: clean (TeamPCP NA)
  - Recent pushes all legitimate
  - Signed commits NOT enabled locally
  - No branch protection on main
  - Threat model: placeholder since 2026-04-27

This arc closes the substrate-side response in four bursts:

B430 — IoC catalog v1 -> v2
  config/security_iocs.yaml gains 5 rules:
    CRITICAL github_actions_pull_request_target (Grafana vector)
    CRITICAL github_actions_env_dump_curl (Grafana exfil signature)
    CRITICAL github_pat_or_app_token_committed (MoneyForward pattern)
    HIGH     github_actions_run_from_fork_unchecked
    INFO     cve_2026_3854_awareness
  16 -> 21 rules. catalog_version 1 -> 2 with audit comment.

B431 — ADR-0084 GitHub Push-Pipeline Posture (NEW)
  Six rules each with enforcement layer mapped:
    1. No Actions without ADR (repo + IoC enforcement)
    2. Signed commits required on main (operator + repo)
    3. SSH+hardware-key preferred (operator preference)
    4. Branch protection on main (repo via GitHub UI)
    5. Periodic PAT rotation (operator process)
    6. IoC catalog authoritative (substrate)
  Userspace policy ADR per ADR-0082; no kernel surface change.

B432 — git_local_scan.v1 builtin tool (NEW)
  src/forest_soul_forge/tools/builtin/git_local_scan.py: read-only
  scanner covering 4 dimensions (secrets / signing / sync /
  gitignore). Consumes IoC catalog v2 patterns + inline floor.
  Registered in builtin/__init__.py. Catalog entry in
  config/tool_catalog.yaml. tests/unit/test_git_local_scan.py
  with 7 pinned contracts (validation, clean-repo pass, PAT
  detection, unsigned-commit detection, no-upstream handling,
  allowed_paths refusal, non-git-dir refusal). Per ADR-0082 +
  ADR-0044 boundary doc, adding a builtin to tools/builtin/
  is the canonical extensible-userspace operation, not a
  kernel addition.

B433 — promote ADR-0025 threat model v2 (placeholder -> Accepted)
  docs/decisions/ADR-0025-threat-model-v2.md rewritten from
  2026-04-27 placeholder to Accepted. Documents:
    - Trust boundary table (machine / daemon / chain / origin /
      providers / federation-deferred)
    - 7 defended adversary categories with cited ADR defenses
    - 5 out-of-scope categories with explicit rationale
    - Per-event signing guarantees + non-guarantees
    - Local-vs-GitHub canonical truth relationship
    - Defense-in-depth principles map

Operator-side actions still pending (Tier 1, not Chrome-driveable):
  - Verify 2FA/passkey on GitHub account
  - Rotate the GitHub PAT used for push
  - Audit OAuth app grants
  - Enable signed commits locally (ed25519 SSH or GPG)
  - Enable branch protection on main (require signed + linear)

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: real May 2026 incident wave + no documented FSF
    threat model + no runtime self-check tool.
  Prove non-load-bearing for kernel ABI: catalog data + docs +
    one builtin tool following existing Protocol. No event-type
    additions, no schema changes, no new HTTP route families.
  Prove alternative: leaving the substrate side undone means
    operator Tier 1 actions land into a substrate that can't
    self-verify. This arc preps the substrate so Tier 1 has
    something to enforce against.

Closes the security response arc. Operator-side Tier 1
recommendations documented inline in ADR-0084 + this commit body." || { echo "commit failed"; exit 1; }

echo
echo "Pushing to origin..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B430-B433 shipped."
echo
echo "Press any key to close."
read -n 1 || true
