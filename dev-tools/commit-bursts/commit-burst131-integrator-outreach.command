#!/bin/bash
# Burst 131 — ADR-0044 P6 outreach materials.
#
# P6 ('first external integrator') is the load-bearing v0.6+
# milestone per ADR-0044 Decision 4. The roadmap budgeted it as
# 'months, not bursts' because the actual integrator validation
# is a months-long recruiting + reporting process, not a coding
# arc. What CAN ship in a burst is the comms infrastructure that
# makes outreach possible.
#
# What ships:
#
#   docs/integrator-pitch.md (new) — 1-pager for inviting external
#     projects to build on Forest as their governance kernel.
#     Sections:
#       - What Forest is (kernel-shape responsibilities, the 7
#         ABI surfaces from KERNEL.md)
#       - What Forest is not (workflow designer, marketplace UI,
#         multi-provider auth UX, billing/tenancy, UX-first polish)
#       - Why integrate (different framings for workflow projects,
#         research/academic projects, security/compliance teams,
#         distribution builders)
#       - What we need from you (read the spec, run conformance,
#         build something, tell us what hurts)
#       - What we commit to in return (no breaking ABI without
#         major-bump signal + ADR + deprecation cycle, spec
#         maintenance, visible-by-default governance, Apache 2.0)
#       - What's at stake (multiple agent-OS projects chasing 'the
#         Linux of agent runtime'; we can't claim it, integrators
#         building on us would)
#       - How to start (links to spec, quickstart, headless install,
#         license ADR, kernel positioning ADR, contact)
#
#   docs/integrator-quickstart.md (new) — 30-minute walkthrough
#     from 'I read the pitch and want to try it' to 'I've
#     exercised the seven ABI surfaces.' Five steps:
#       1. Bring up the kernel headless (5 min)
#       2. Run the conformance suite (5 min)
#       3. Try a write endpoint — birth agent, inspect audit
#          chain, set posture (10 min)
#       4. Try a plugin grant — exercise the error envelope
#          shape (10 min)
#       5. Use the CLI (5 min)
#     Closes with 'tell us what hurts' — specific feedback the
#     project most wants from external integrators (failing
#     conformance tests, misleading error messages, surprising
#     ABI choices, documentation gaps).
#
# Per ADR-0044 P6's 'months not bursts' budget, this burst does
# NOT include actual integrator outreach (cold-emailing agnt-gg,
# AIOS, etc.). That's an orchestrator decision (Alex's call on
# tone, channels, timing). The pitch + quickstart give that
# outreach a target landing page when it happens.
#
# Concrete next outreach candidates per ADR-0044 §'Decision 4
# - External integrator path is the load-bearing milestone':
#   1. agnt-gg/agnt — most direct overlap, ~6 months ahead on
#      workflow UX. Pitch: 'you have product velocity, we have
#      governance discipline; here's the kernel API, want it?'
#   2. AIOS (agiresearch/AIOS) — academic project; if Forest's
#      governance discipline maps to their security/sandbox
#      interests, they could adopt or extend.
#   3. Internal second distribution — build a headless / server
#      variant of Forest in v0.7+ that exercises the kernel API
#      without the SoulUX shell. Validates the boundary by being
#      a second consumer.
#
# Verification:
#   - Markdown renders cleanly (visual review of both files).
#   - Cross-references resolve: pitch.md → quickstart.md, both
#     → spec/kernel-api-v0.6.md, runbooks/headless-install.md,
#     ADR-0044, ADR-0046, KERNEL.md, CONTRIBUTING.md.
#   - Quickstart's curl examples reference the actual endpoints
#     in the spec (no fictional surfaces).
#   - Pitch's '7 ABI surfaces' list aligns with KERNEL.md's
#     enumeration (no drift).
#
# What this delivers per ADR-0044 P6:
#   ✅ Outreach materials — concrete pitch + quickstart that any
#     external evaluator can follow without prior context.
#   ⏳ Actual external integrator validation — gated on Alex's
#     outreach decision. The pitch is the asset that unlocks
#     that conversation.
#
# This commit closes the v0.6 kernel arc work that's doable in
# bursts. Phases 1, 2, 3, 4, 5, 5.1 of ADR-0044 are all shipped
# (Bursts 117-130). Phase 6 is now operationally ready — pitch
# and quickstart are the entry points. Phase 7 (v1.0 stability
# commitment) is gated on P6's external validation arriving.
#
# v0.6 kernel arc summary:
#   ✅ P1 boundary doc + KERNEL.md + sentinel (B118-120)
#   ✅ P2 formal kernel API spec — 1,042-line contract (B127)
#   ✅ P3 headless mode + SoulUX clarification (B129)
#   ✅ P4 conformance test suite scaffold (B130)
#   ✅ P5 license + governance, ADR-0046 (B121)
#   ✅ P5.1 CONTRIBUTING + CoC (B122)
#   ✅ P6 outreach materials (B131 — this commit)
#   ⏳ P6 actual integrator validation — months, not bursts
#   ⏳ P7 v1.0 stability commitment — gated on P6 arriving

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/integrator-pitch.md \
        docs/integrator-quickstart.md \
        dev-tools/commit-bursts/commit-burst131-integrator-outreach.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs: integrator pitch + quickstart (ADR-0044 P6 outreach, B131)

Burst 131. Closes the v0.6 kernel arc work that's doable in
bursts. P6 ('first external integrator') is the load-bearing v0.6+
milestone per ADR-0044 Decision 4 but the actual validation is
months, not bursts. What CAN ship in a burst is the comms
infrastructure that makes outreach possible.

Ships:

- docs/integrator-pitch.md (new): 1-pager inviting external
  projects to build on Forest as their governance kernel.
  Frames the kernel/distribution split, what Forest is and isn't,
  why an integrator would care (different framings for workflow
  projects, research/academic, security/compliance, distribution
  builders), what we need (read spec, run conformance, build
  something, report back), what we commit to (no breaking ABI
  without signal, spec maintenance, visible-by-default
  governance, Apache 2.0). Closes with the strategic stake:
  multiple agent-OS projects chasing 'the Linux of agent
  runtime'; we can't claim it, integrators building on us would.

- docs/integrator-quickstart.md (new): 30-minute walkthrough
  from 'read the pitch and want to try it' to 'exercised seven
  ABI surfaces.' Five steps: headless install, conformance
  suite, write endpoint (birth agent → audit → posture), plugin
  grant (error envelope probe), CLI. Closes with explicit
  feedback channel — failing conformance tests, misleading error
  messages, surprising ABI choices, documentation gaps.

Per ADR-0044 P6's 'months not bursts' budget, this commit does
NOT include actual integrator outreach (cold-emailing agnt-gg,
AIOS, etc.). That's an orchestrator decision on tone, channels,
timing. The pitch + quickstart give that outreach a target
landing page when it happens.

Concrete recruiting candidates per ADR-0044 Decision 4:
1. agnt-gg/agnt — most direct overlap; complement-not-competitor
   pitch
2. AIOS (agiresearch/AIOS) — academic project
3. Internal second distribution — headless/server variant in v0.7+

v0.6 kernel arc state after this commit:
- P1 boundary doc + KERNEL.md + sentinel: shipped (B118-120)
- P2 formal kernel API spec: shipped (B127, 1042 lines)
- P3 headless mode + SoulUX clarification: shipped (B129)
- P4 conformance test suite: shipped (B130)
- P5 license + governance ADR-0046: shipped (B121)
- P5.1 CONTRIBUTING + CoC: shipped (B122)
- P6 outreach materials: shipped (this commit)
- P6 actual integrator validation: open (months)
- P7 v1.0 stability commitment: gated on P6"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 131 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
