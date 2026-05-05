#!/bin/bash
# Burst 122 — CONTRIBUTING.md + CODE_OF_CONDUCT.md + small docstring
# alignment fix in tests/unit/test_plugin_grants.py.
#
# Per ADR-0046 §"Code of Conduct" and §"Governance," this burst
# finishes the integrator-facing artifact set. The CONTRIBUTING +
# CoC files complete what an external integrator expects to see
# before evaluating a project.
#
# Also includes a small stale-docstring fix to test_plugin_grants.py:
# the file's docstring + the schema-version test function name said
# "v14" but the assertion body asserts == 15 (schema was bumped at
# Burst 114). Aligns the documentation to the assertion.
#
# What ships:
#
#   CONTRIBUTING.md (new) — practical guide for contributors:
#     - Pre-read pointers (boundary doc, KERNEL.md, ADRs)
#     - Three change-class workflows (kernel-internal refactor /
#       userspace / ABI-touching) with the bar for each
#     - Test discipline (FK seed pattern, xfail vs skip honesty)
#     - Conventional Commits + Signed-off-by (DCO) posture
#     - Bug report / feature request / security disclosure paths
#     - Forking + distribution conventions ("Forest" + "SoulUX"
#       names reserved socially, not legally)
#     - Quick-reference table of where to look first
#
#   CODE_OF_CONDUCT.md (new) — Contributor Covenant 2.1, restated
#     locally per ADR-0046 §"Code of Conduct." Project-specific
#     norms section adds Forest-relevant guidance: ADR criticism
#     posture, assume-good-faith on technical disputes,
#     documentation-over-ephemeral-arguments.
#
#   tests/unit/test_plugin_grants.py (fix) — docstring updated from
#     "v14 stamp" to reflect the v14→v15 history; function rename
#     from test_schema_version_is_14 to test_schema_version_is_15
#     (was a stale name; the assertion body always asserted == 15
#     after Burst 114 bumped the schema). Documentation/code
#     alignment fix.
#
# Verification:
#   - Full unit suite: 2,386 passing (the test rename doesn't
#     change the test count or behavior; it's a docs-grade fix).
#   - Sentinel: still green.
#
# This closes ADR-0046 §"Code of Conduct" follow-up and the
# integrator-facing-artifact-set work the v0.6 documentation
# foundation needed.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add CONTRIBUTING.md CODE_OF_CONDUCT.md tests/unit/test_plugin_grants.py

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: CONTRIBUTING + CODE_OF_CONDUCT + test_plugin_grants docstring fix (B122)

Burst 122. Finishes the integrator-facing artifact set per
ADR-0046 §'Code of Conduct' and §'Governance' — what an external
integrator expects to see when evaluating Forest as a kernel they
might bet on.

What ships:

- CONTRIBUTING.md (new): practical guide for contributors. Three
  change-class workflows (kernel-internal refactor / userspace /
  ABI-touching) with the bar for each. Test discipline (FK seed
  pattern, xfail vs skip honesty). Conventional Commits +
  Signed-off-by (DCO) posture. Bug report / feature request /
  security disclosure paths. Forking + distribution conventions
  ('Forest' + 'SoulUX' names reserved socially, not legally).
  Quick-reference table mapping common contributor questions to
  the canonical file that answers them.

- CODE_OF_CONDUCT.md (new): Contributor Covenant 2.1, restated
  locally per ADR-0046 §'Code of Conduct.' Project-specific norms
  section adds Forest-relevant guidance: ADR criticism posture
  (criticize the work, not the person), assume-good-faith on
  technical disputes, document disagreements in ADR comment
  threads (durable + searchable beats Slack-ephemeral).

- tests/unit/test_plugin_grants.py: stale docstring + function-
  name fix. The file's docstring said 'v14 stamp' and the test
  function was named test_schema_version_is_14, but the assertion
  body always asserted == 15 (post Burst 114 schema bump for
  ADR-0045 agents.posture). Renamed to test_schema_version_is_15
  with a docstring explaining the v14→v15 history. Pure
  documentation/code alignment; no test count or behavior change.

Verification:
- Full unit suite: 2,386 passing (rename + docstring; no impact).
- Sentinel: green.

Integrator-facing artifact set is now complete:
  README              — strategic posture
  KERNEL.md           — technical surfaces
  LICENSE             — Apache 2.0
  CONTRIBUTING.md     — how to participate
  CODE_OF_CONDUCT.md  — community standard
  CHANGELOG.md        — recent history
  CREDITS.md          — attribution
  STATE.md            — live current-reality snapshot
  docs/decisions/     — design record (47 ADRs)
  docs/architecture/  — boundary doc + others
  dev-tools/          — sentinel + drift checks

ADR-0044 7-phase progress:
  P1 kernel/userspace boundary  ✓ Bursts 118-120
  P5 license + governance       ✓ Burst 121
  P5.1 CONTRIBUTING + CoC       ✓ Burst 122 (this commit)
  P2 formal kernel API spec     next
  P3 headless + SoulUX split    queued
  P4 conformance test suite     queued
  P6 first external integrator  months, not bursts
  P7 v1.0 stability commitment  gated on P6"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 122 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
