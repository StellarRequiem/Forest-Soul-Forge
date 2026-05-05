#!/bin/bash
# Burst 119 — ADR-0044 Phase 1.2: KERNEL.md root-level ABI summary.
#
# Distills the seven v1.0 ABI surfaces from ADR-0044 + the
# directory-level boundary doc (Burst 118) into a single root-level
# reference. Pure documentation; no code changes.
#
# What ships:
#   KERNEL.md (~210 LoC) — canonical entry point for an external
#     integrator or contributor:
#     - What the kernel commits to at v1.0 (the seven surfaces).
#     - What the kernel does NOT commit to (internals).
#     - Where to find each surface in code (table mapping surface
#       name → canonical file path).
#     - How to make a kernel change (3-step decision tree).
#     - How to integrate against the kernel from outside.
#   Each section cross-links to the relevant ADR / boundary doc.
#
# This is the reference an integrator opens FIRST. README.md sets
# the strategic context (kernel posture, SoulUX naming); KERNEL.md
# is the technical surface they target.

set -euo pipefail

cd "$(dirname "$0")"

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add KERNEL.md

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: KERNEL.md root-level ABI summary (ADR-0044 P1.2)

Burst 119. Distills the seven v1.0 ABI surfaces from ADR-0044 +
the directory-level boundary doc (Burst 118) into a single root-
level reference an external integrator opens FIRST.

What ships:
- KERNEL.md (new, ~210 LoC) at repo root.
- Sections: status (v0.5.0 ships substantive kernel work; v1.0
  commitment lands when external integrator validation arrives),
  the seven kernel ABI surfaces (tool dispatch, audit chain,
  plugin manifest, constitution.yaml, HTTP API, CLI, schema
  migrations), what the kernel does NOT commit to, where to find
  each surface in code (canonical-file-path table), how to make a
  kernel change (3-step decision tree), how to integrate against
  the kernel from outside.

The seven surfaces re-state ADR-0044 Decision 3 with concrete
file paths and integration patterns. Each section cross-links to
the relevant ADR for the design rationale.

README.md sets the strategic context (kernel posture, SoulUX
naming). KERNEL.md is the technical surface integrators target.
docs/architecture/kernel-userspace-boundary.md is the directory-
level boundary that complements both.

No code changes; no schema; no behavior."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 119 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
