#!/bin/bash
# Burst 121 — ADR-0046 License Posture + Governance.
#
# Phase 5 of the ADR-0044 7-phase kernel-positioning roadmap.
# Confirms Apache 2.0 (already in LICENSE file) as the deliberate
# license choice for kernel-shape positioning, locks the governance
# model that v0.6+ work executes against.
#
# Three decisions:
#
#   1. License: Apache 2.0. Justified vs GPL/AGPL (closes
#      integration paths), BSL/Commons Clause (wrong shape, signals
#      commercial product), MIT/BSD (no patent grant), dual-license
#      (premature). Compatibility matrix: anyone can integrate.
#
#   2. Governance: single steward (Alex) for v0.6. ADRs as the
#      public RFC mechanism. Transition triggers to multi-maintainer
#      named explicitly (5+ merged PRs over 3 months from external,
#      OR a second internal distribution shipping, OR steward
#      request). No CLA at v0.6 — DCO posture (Linux-style). Code
#      of Conduct adoption stub deferred to a follow-up.
#
#   3. Kernel API spec location: docs/spec/v1/ in-repo, versioned
#      alongside code. Public on GitHub. Separate forest-spec repo
#      deferred — could revisit at v1.0 if cadence diverges.
#
# What this ADR does NOT do:
#   - Create CONTRIBUTING.md (follow-up burst).
#   - File CODE_OF_CONDUCT.md (follow-up).
#   - File trademark applications (deferred).
#   - Pick maintainer succession candidates (only defines the
#     trigger).
#   - Commit to a release-signing identity (gated on Tauri T5).
#
# This closes ADR-0044 Phase 5. Phase 1 (kernel/userspace
# boundary) closed Bursts 118-120. Remaining roadmap: Phase 2
# (formal kernel API spec under docs/spec/v1/) → Phase 3 (headless
# mode + SoulUX frontend split) → Phase 4 (conformance test suite)
# → Phase 6 (first external integrator — months) → Phase 7 (v1.0).

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0046-license-and-governance.md

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0046 License Posture + Governance (ADR-0044 Phase 5)

Burst 121. Phase 5 of the ADR-0044 7-phase kernel-positioning
roadmap. Confirms Apache 2.0 (already in LICENSE file) as the
deliberate license choice for kernel-shape positioning, locks the
governance model that v0.6+ work executes against.

Three decisions:

1. License: Apache 2.0.
   The LICENSE file already carries Apache 2.0; this ADR
   justifies the choice. Why Apache vs alternatives:
   - GPL/AGPL: forces commercial integrators to GPL their
     entire distribution. Closes integration paths Forest
     specifically wants (per ADR-0044 Decision 4).
   - BSL/Commons Clause: signals 'we plan to monetize this
     directly.' Wrong shape for kernel positioning.
   - MIT/BSD: no patent retaliation clause. Real gap in a
     kernel handling trust + audit governance.
   - Dual-license: premature; adds CLA admin overhead.
   Apache 2.0 is the OpenTelemetry / Envoy / etcd posture —
   modern kernel-shape projects choose permissive.
   Compatibility matrix: anyone can integrate (proprietary,
   GPL, AGPL, BSL all work as downstreams).

2. Governance: single steward (Alex) at v0.6.
   ADRs are the public RFC mechanism — anyone can propose,
   steward decides, rejected ADRs stay visible. Transition
   triggers to multi-maintainer named explicitly:
   (a) external integrator with 5+ merged PRs over 3 months,
   (b) second internal distribution shipping,
   (c) steward request.
   No CLA at v0.6 — DCO posture (Linux-style 'Signed-off-by').
   Code of Conduct adoption (Contributor Covenant 2.1) deferred
   to a follow-up burst.
   Conflict resolution: steward decides at single-maintainer
   stage; two-maintainer concurrence required after transition.

3. Kernel API spec location: docs/spec/v1/ in-repo.
   Versioned alongside code. Public on GitHub. Separate
   forest-spec repo deferred — could revisit at v1.0 if
   spec cadence diverges from impl cadence.

What this ADR does NOT do:
- Create CONTRIBUTING.md or CODE_OF_CONDUCT.md (follow-up).
- File trademark applications (deferred).
- Pick maintainer succession candidates (defines the trigger).
- Commit to a release-signing identity (gated on Tauri T5 /
  Apple Developer decision).

ADR-0044 progress:
  Phase 1 (kernel/userspace boundary)  closed Bursts 118-120
  Phase 5 (license + governance)        closed Burst 121
  Phase 2 (formal kernel API spec)      next
  Phase 3 (headless + SoulUX split)     queued
  Phase 4 (conformance test suite)      queued
  Phase 6 (external integrator)         months, not bursts
  Phase 7 (v1.0 stability commitment)   gated on Phase 6

This closes the documentation foundation an external
integrator needs before betting on Forest:
  README        — strategic posture
  KERNEL.md     — technical surfaces
  LICENSE       — usage rights
  ADR-0044       — kernel positioning
  ADR-0046       — license + governance"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 121 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
