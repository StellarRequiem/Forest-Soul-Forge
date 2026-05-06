#!/bin/bash
# Burst 150 — T26 security hardening: SBOM + pinned-deps workflow.
#
# Phase 4 item #2 from the 2026-05-05 outside security review. The
# review flagged: "no SBOM, no pinned hashes, no reproducible builds."
# This commit ships the WORKFLOW (scripts + docs); operator runs the
# scripts on the venv to generate the actual artifacts.
#
# Why ship workflow not artifacts: the artifacts (requirements*.txt
# with hashes, dependencies/sbom.json) are venv-state-dependent and
# need to be (re-)generated whenever pyproject.toml changes. Each
# operator's venv may have different optional extras installed; a
# committed lockfile that's stale is worse than no lockfile. The
# scripts make regeneration a one-double-click action.
#
# What ships:
#
#   dev-tools/pin-deps.command — uses pip-tools (pip-compile) to
#     generate hash-pinned requirements.txt + per-extras files
#     (daemon, dev, browser, conformance) from pyproject.toml.
#     Auto-installs pip-tools into .venv if missing. Run any time
#     pyproject.toml dependencies change; commit the regenerated
#     lockfiles for CI reproducibility.
#
#   dev-tools/generate-sbom.command — uses cyclonedx-bom to generate
#     CycloneDX 1.5 JSON SBOM at dependencies/sbom.json from the
#     active venv's installed packages. Auto-installs cyclonedx-bom
#     if missing. Re-run after pip install / upgrade.
#
#   docs/runbooks/dependency-management.md — full workflow documentation.
#     Why pin, how to regenerate, CI usage, CVE-response workflow,
#     limits (cross-platform, transitive provenance, etc.).
#
#   .gitignore — adds a comment block reserving requirements*.txt and
#     dependencies/sbom.json as tracked-not-ignored. Flag for future
#     greps if anyone wonders why they aren't gitignored.
#
# What this commit does NOT ship:
#
#   - Generated requirements*.txt — operator runs pin-deps.command
#     against their venv. Lockfile reflects THEIR install state at
#     commit time. Could ship a baseline next session if the venv
#     state stabilizes.
#
#   - Generated dependencies/sbom.json — same reason. Operator runs
#     generate-sbom.command after `pip install -e ".[daemon,dev,browser,
#     conformance]"`.
#
#   - CI integration — a GitHub Actions workflow that runs both
#     scripts on every PR could catch dep-tree drift, but Forest's
#     local-first model + zero-CI-today posture means this is operator
#     responsibility for now. CI workflow file = follow-on burst if
#     external integrators want it.
#
# Closes T26 design + tooling. Actual lockfile generation is a 5-min
# operator action when ready (run pin-deps + generate-sbom + commit
# the artifacts).
#
# Related future Phase 4 work:
#   - T27 ADR per-event signatures
#   - T28 ADR encryption at rest
#   - T29 ADR per-tool subprocess sandbox
#   - CVE monitoring loop (pip-audit / trivy fs .) — separate operator
#     workflow

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add dev-tools/pin-deps.command \
        dev-tools/generate-sbom.command \
        docs/runbooks/dependency-management.md \
        .gitignore \
        dev-tools/commit-bursts/commit-burst150-sbom-pinned-deps.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): T26 — SBOM + pinned-deps workflow (B150)

Burst 150. Phase 4 item #2 from 2026-05-05 outside security review:
'no SBOM, no pinned hashes, no reproducible builds.' Ships the
workflow (scripts + docs); operator runs scripts to generate
artifacts.

Why workflow not artifacts: lockfiles are venv-state-dependent and
need to be regenerated whenever pyproject.toml changes. Stale
committed lockfile is worse than no lockfile. Scripts make
regeneration one-double-click.

Ships:
- dev-tools/pin-deps.command: pip-tools / pip-compile generates
  hash-pinned requirements.txt + per-extras files (daemon, dev,
  browser, conformance) from pyproject.toml. Auto-installs pip-tools
  if missing.
- dev-tools/generate-sbom.command: cyclonedx-bom generates
  CycloneDX 1.5 JSON SBOM at dependencies/sbom.json from active
  venv's packages. Auto-installs cyclonedx-bom if missing.
- docs/runbooks/dependency-management.md: full workflow docs. Why
  pin, how to regenerate, CI usage, CVE-response workflow, scope
  limits.
- .gitignore: comment reserving requirements*.txt + dependencies/
  sbom.json as tracked-not-ignored.

Operator-side: run pin-deps + generate-sbom against the venv
(after installing all extras), commit the artifacts. ~5 min.

Closes T26 tooling. Phase 4 progress: T25 done (B148+B149), T26 done
(B150). Remaining: T27 (per-event signatures ADR), T28 (encryption
at rest ADR), T29 (per-tool sandbox ADR)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 150 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
