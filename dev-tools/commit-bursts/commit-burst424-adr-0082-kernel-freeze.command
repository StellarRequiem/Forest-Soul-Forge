#!/usr/bin/env bash
# Burst 424 — ADR-0082 Kernel Freeze Posture.
#
# This is the load-bearing "make it a feature" move from the
# B422-B424 arc adapting to two independent external assessments:
#
#   Sonnet 4.6 (2026-05-17, pinned thread): "The real question is
#     whether you have the discipline to slow down and close gaps
#     before complexity outpaces your ability to hold it in your
#     head solo."
#
#   ChatGPT (2026-05-19, external review): "Currently resembles an
#     experimental research sandbox more than a coherent 'agentic
#     kernel.' The risk isn't 'fake.' The risk is uncontrolled
#     architectural ambition."
#
# Two independent assessors hitting the same point is signal.
#
# What ADR-0082 does
# ------------------
# 1. Declares the kernel surface functionally frozen as of Phase
#    alpha close (10/10 substrate ADRs closed across Bursts 281-330,
#    2026-05-15).
# 2. Extends KERNEL.md's seven ABI surfaces with seven additional
#    frozen abstractions: audit chain canonical form, constitution
#    hash derivation, DNA derivation, instance_id derivation,
#    additive-only schema migrations, singleton-per-forest role
#    list, side-effect classification.
# 3. Distinguishes domain rollouts (userspace ON kernel) from
#    kernel additions. Cites D4 (ADR-0077) as the canonical worked
#    example: 10-burst rollout, zero kernel-side changes.
# 4. Defines three triggers that unfreeze a specific kernel
#    addition: external integrator demand, operator-level safety
#    requirement, architectural bug discovery.
# 5. Specifies enforcement via ADR-0040 trust-surface rule + a
#    drift sentinel kernel-LoC budget check + ADR-0044 P6 outreach
#    materials.
#
# What ADR-0082 does NOT do
# -------------------------
# - It does NOT touch userspace (apps/desktop, frontend, dist,
#   repo-root .command scripts). SoulUX is free to evolve.
# - It does NOT prevent domain rollouts (D3 in flight, 7 more
#   queued per ADR-0067 dependency order).
# - It does NOT prevent ANY kernel addition — it raises the bar to
#   require an explicit external-trigger ADR.
#
# Hippocratic gate (CLAUDE.md sec0)
# ---------------------------------
# 1. Prove harm: two independent external assessments flagged the
#    "ambition exceeds proof" risk. The kernel/userspace boundary
#    doc (ADR-0044 P1.1) is aspirational without a forcing function.
#    Phase alpha closure removes the substrate-substantive-work
#    pressure; without an explicit freeze, every domain rollout
#    becomes a kernel-extension opportunity.
# 2. Prove non-load-bearing: this is a docs + discipline ADR. It
#    adds no code. It does not change any existing surface. It
#    operationalizes a posture that ADR-0044 already implied.
# 3. Prove alternative: leaving the rule aspirational is the
#    status quo and the failure mode. Stricter alternatives
#    (e.g., literal CI-enforced kernel-LoC budget) rejected as
#    premature; ADR ships the rule, sentinel enforcement is a
#    follow-on burst.
#
# Part of B422-B424 arc:
#   B422: script consolidation (64 -> 26 repo-root .command).
#   B423: STATE.md + KERNEL.md drift refresh.
#   B424 (this): ADR-0082 Kernel Freeze Posture.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 424 — ADR-0082 Kernel Freeze Posture"
echo "==========================================================="
echo
echo "Pre-commit: clearing stale .git lock files (if any)..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  removed" || echo "  none"
echo

git add docs/decisions/ADR-0082-kernel-freeze-posture.md
git add dev-tools/commit-bursts/commit-burst424-adr-0082-kernel-freeze.command

echo "Pre-commit status:"
git status -s | head -20
echo

git commit -m "feat(governance): ADR-0082 Kernel Freeze Posture (B424)

The load-bearing 'make it a feature' move from the B422-B424 arc
adapting to two independent external assessments.

Sonnet 4.6 (2026-05-17, pinned project-scan thread) and ChatGPT
(2026-05-19, external review) converged on the same critique:
ambition outpacing solidity, complexity outpacing the ability to
hold it solo. Two independent assessors hitting the same point is
signal, not noise.

ADR-0082 declares the kernel surface functionally frozen as of
Phase alpha close (10/10 substrate ADRs closed Bursts 281-330).
Extends KERNEL.md's seven ABI surfaces with seven additional
frozen abstractions:

  1. Audit chain canonical form (entry_hash derivation, GENESIS
     literal, timestamp exclusion).
  2. Constitution hash derivation (over policies + thresholds +
     scope + duties + drift + tools + genre; excludes triune block
     and posture/status).
  3. DNA derivation (dna_short + dna_full from canonical trait
     profile — role + trait values + domain weights).
  4. instance_id derivation (role + dna_short + optional
     sibling_index; PK on agents table).
  5. Strictly additive forward schema migrations only.
  6. Singleton-per-forest role list (reality_anchor +
     domain_orchestrator + wiring_sentinel).
  7. Side-effect classification (read_only / network /
     filesystem / external).

Distinguishes domain rollouts (userspace ON kernel) from kernel
additions. D4 (ADR-0077) cited as canonical worked example: 10-
burst rollout, zero kernel-side changes — three roles + three
skills + three birth scripts composed against frozen surface.

Defines three triggers that unfreeze a specific kernel addition:
  1. External integrator demand (per ADR-0044 Decision 4 + P6).
  2. Operator-level safety requirement (ADR-0050 cited as
     historical precedent).
  3. Architectural bug discovery (B416/B420 DNA-instance_id
     coupling cited as live example).

A demand that doesn't fit those three triggers does not unfreeze
the kernel. 'Would be cool' is not a trigger. 'Easier if we had X'
is not a trigger if it can be built in userspace, however
inelegantly.

Enforcement via ADR-0040 trust-surface decomposition rule + future
drift-sentinel kernel-LoC budget check + ADR-0044 P6 outreach
materials. Sentinel update deferred to follow-on burst.

Consequences:
  Positive: 'uncontrolled ambition' critique becomes 'bounded
    ambition with explicit freeze line.' v1.0 becomes describable.
    External integrator pitch sharpens.
  Negative: some useful kernel-side ideas deferred; userspace
    workarounds may be awkward. Acceptable cost of discipline.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: ChatGPT + Sonnet 4.6 convergent critique; ADR-0044
    boundary doc is aspirational without forcing function; Phase
    alpha close removes substrate-pressure, so without explicit
    freeze every domain rollout tempts kernel extension.
  Prove non-load-bearing: docs + discipline only; no code; no
    existing surface touched.
  Prove alternative: leaving rule aspirational is status quo and
    failure mode. Stricter CI-enforced sentinel rejected as
    premature; rule ships now, enforcement follows.

Closes B422-B424 arc:
  B422 (077610c+1): script consolidation (64 -> 26 repo-root).
  B423 (B422+1):    STATE.md + KERNEL.md drift refresh.
  B424 (this):      ADR-0082 the explicit freeze posture." || { echo "commit failed"; exit 1; }

echo
echo "Pushing to origin..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "==========================================================="
echo "B422-B424 arc closed."
echo "==========================================================="
echo
echo "Done. Verify with:"
echo "  ls *.command | wc -l                    # should be 26"
echo "  head -50 STATE.md                       # should show 'Burst 420' snapshot"
echo "  head -30 docs/decisions/ADR-0082*.md    # the freeze posture"
echo
echo "Press any key to close."
read -n 1 || true
