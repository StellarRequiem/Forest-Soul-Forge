#!/bin/bash
# Burst 118 — ADR-0044 Phase 1.1: kernel/userspace boundary doc +
# README rebrand header.
#
# First sub-burst of the v0.6 kernel arc. Pure documentation —
# no code changes, no schema changes, no behavior changes. The
# point is to formalize the boundary that the existing code
# already mostly respects, so future contributors know where
# the lines are.
#
# What ships:
#
#   docs/architecture/kernel-userspace-boundary.md — new doc
#     (~125 LoC). Full directory map labeling every top-level
#     directory as kernel / userspace / kernel-adjacent / operator
#     state. Re-states the seven v1.0 ABI surfaces from ADR-0044
#     Decision 3. Contributor-facing guidance: how to know if a
#     change is touching the kernel surface or just userspace.
#
#   README.md — adds a strategic-posture note to the header
#     pointing at ADR-0044 and naming SoulUX as the flagship
#     distribution. Tagline rewritten from "local-first agent
#     foundry" framing to "agent governance kernel" framing,
#     reflecting the v0.6 strategic shift.
#
# What this burst does NOT do:
#   - Add KERNEL.md (Phase 1.2+).
#   - Audit kernel-touches-userspace / userspace-touches-internals
#     (Phase 1.2+).
#   - Add a check-kernel-userspace.sh sentinel (Phase 1.2+).
#   - Publish docs/spec/v1/ (Phase 2 of ADR-0044 roadmap).
#   - Rename the repo / package / CLI (NOT planned for v0.6 per
#     ADR-0044 Decision 2 — light-touch rebrand only).
#
# Verification:
#   - Full unit suite: 2,386 passing (docs-only commit, expected
#     no test impact).
#   - No code changes; no schema changes; no behavior changes.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/architecture/kernel-userspace-boundary.md README.md

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: kernel/userspace boundary + README rebrand header (ADR-0044 P1.1)

Burst 118 — first sub-burst of ADR-0044 Phase 1 (kernel/userspace
boundary lock). Pure documentation; no code, schema, or behavior
changes.

What ships:

- docs/architecture/kernel-userspace-boundary.md (new, ~125 LoC).
  Full directory map labeling every top-level directory as
  kernel / userspace / kernel-adjacent / operator state. Re-states
  the seven v1.0 ABI surfaces from ADR-0044 Decision 3.
  Contributor-facing guidance: how to know if a change touches the
  kernel API or just userspace; default-to-userspace rule for
  ambiguous new code. Lists Phase 1.2+ queued work
  (KERNEL.md, import audit, sentinel script).

- README.md header. Strategic-posture note added pointing at
  ADR-0044 + naming SoulUX as the flagship distribution. Tagline
  rewritten from 'local-first agent foundry' to 'agent governance
  kernel' framing — reflects the v0.6 strategic shift from product
  to substrate posture. The Forest:SoulUX relationship is named
  explicitly (Linux:Ubuntu, Postgres:Supabase parallel).

What this does NOT do:
- Add KERNEL.md (Phase 1.2+).
- Audit imports for kernel/userspace violations (Phase 1.2+).
- Add check-kernel-userspace.sh sentinel (Phase 1.2+).
- Publish docs/spec/v1/ formal kernel API spec (Phase 2).
- Rename the repo / Python package / CLI (per ADR-0044 Decision 2,
  light-touch rebrand only at v0.6).

Verification: full unit suite 2,386 passing (docs-only, expected
no test impact)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 118 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
