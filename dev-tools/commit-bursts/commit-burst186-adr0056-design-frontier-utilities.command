#!/bin/bash
# Burst 186 — ADR-0056 Experimenter (Smith) design doc + frontier
# operational utilities (smoke test, base-url fix, setup script
# correction).
#
# Two coupled deliverables in one burst:
#
# 1. ADR-0056 design — Smith / Experimenter agent. Three-mode
#    system (explore / work / display), full catalog kit modulo
#    identity-touching, branch + path isolation, frontier provider
#    preference, self-augmentation flow with operator-approval
#    gate, posture YELLOW default. 7 Decisions + 6 implementation
#    tranches (E1-E6).
#
# 2. Frontier operational utilities — three .command scripts
#    covering the smoke test that validated the wiring end-to-end
#    + the base-url fix that corrects the setup script's previous
#    over-prefix bug.
#
# Why one burst: the smoke test was the load-bearing verification
# that ADR-0056's frontier-preference assumption is real (Smith
# defaults to claude-sonnet-4-6 only because we proved it works
# 2026-05-07). The doc references the smoke result in its
# Consequences section. Coupling them in one commit keeps the
# 'evidence -> doc' trail short.
#
# What ships:
#
#   docs/decisions/ADR-0056-experimenter-agent.md (NEW):
#     - Operator directive captured: full-capability sandboxed
#       agent with three modes + frontier model + self-recursive
#       improvement.
#     - 7 Decisions covering kit composition, mode tagging,
#       branch isolation, frontier preference, display UI,
#       self-augmentation flow, posture controls.
#     - 6 implementation tranches E1-E6 with B-burst targets.
#     - ADR-0001 D2 invariance verification (constitution_hash +
#       DNA stay immutable; tools_add grows per-instance state
#       only).
#     - ADR-0044 D3 forward-compat verification (ModeKitClampStep
#       is a new pipeline step; task_caps.mode is additive; older
#       daemons reading post-E2 constitutions just ignore the
#       unknown tag).
#     - ADR-0008 verification (frontier opt-in is per-agent via
#       Smith's constitution; Sage stays local).
#
#   dev-tools/smoke-test-frontier.command (NEW):
#     - Python one-shot via .venv. Resolves the Anthropic key
#       from the secrets store, instantiates FrontierProvider
#       directly (bypasses the agent + dispatcher layer), calls
#       Anthropic's /v1/chat/completions compat endpoint with a
#       5-word prompt, prints the response. Iterates over
#       candidate model names so a wrong-model error surfaces
#       cleanly. Validated 2026-05-07 with claude-sonnet-4-6.
#
#   dev-tools/fix-frontier-base-url.command (NEW):
#     - Single-purpose .env patcher. The original
#       setup-anthropic-frontier.command set
#       FSF_FRONTIER_BASE_URL=https://api.anthropic.com/v1 but
#       the FrontierProvider appends /v1/chat/completions
#       internally — would have produced a 404 from
#       .../v1/v1/chat/completions on real dispatches. This
#       script rewrites the entry to the correct
#       https://api.anthropic.com (no /v1) and restarts the
#       daemon. Idempotent; safe to re-run.
#
#   dev-tools/setup-anthropic-frontier.command:
#     - Two corrections from the original B185 ship:
#       (a) FSF_FRONTIER_BASE_URL no longer includes /v1 — same
#           bug-fix as the standalone fix script above; here it
#           prevents re-introducing the bug if the operator
#           re-runs setup.
#       (b) Fallback CLI invocation now uses
#           `python -m forest_soul_forge.cli.main` (with the
#           `.main` suffix) rather than the bare `forest_soul_forge.cli`
#           which fails because the package has no __main__.py.
#       Both bugs surfaced during the live B185 run on 2026-05-07.
#
# Verification:
#   - Smoke test passed end-to-end on 2026-05-07 02:59 UTC:
#       OK [step 1]: key resolved (108 chars, prefix=sk-ant-...)
#       OK [step 2]: base_url=https://api.anthropic.com
#       OK [step 3]: model=claude-sonnet-4-6 accepted
#       *** Response from Claude ***
#       Hello there, how are you?
#       *** End response ***
#       SMOKE TEST PASSED
#   - fix-frontier-base-url.command run completed on 2026-05-07
#     03:03 UTC; daemon restarted; .env patched to the correct
#     base URL. Frontier dispatches via the daemon path now
#     reach the right endpoint.
#
# No code tests added in this burst — the smoke test IS the
# verification. E2 (B188) adds proper unit tests for
# ModeKitClampStep.
#
# Per ADR-0044 D3: design-only doc + operator-tool scripts.
# Zero ABI changes. Pre-B186 daemons unaffected.
#
# Per ADR-0001 D2: documents Smith's identity-invariance
# verification posture; no code in this burst touches identity.
#
# Next burst: B187 — E1 (birth Smith, constitutional kit,
# branch isolation provisioning, frontier preference).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0056-experimenter-agent.md \
        dev-tools/smoke-test-frontier.command \
        dev-tools/fix-frontier-base-url.command \
        dev-tools/setup-anthropic-frontier.command \
        dev-tools/commit-bursts/commit-burst186-adr0056-design-frontier-utilities.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0056 — Experimenter (Smith) + frontier utilities (B186)

Burst 186. Pairs the ADR-0056 design doc with the frontier
operational utilities that validated its core assumption (Claude
Sonnet 4.6 reachable via Anthropic's compat endpoint with the
operator's Keychain-stored key). Two deliverables, one burst:

1. ADR-0056 — Experimenter (Smith). Three-mode agent (explore /
   work / display) with full catalog kit modulo identity-
   touching surfaces, branch + path isolation, frontier
   provider preference, self-augmentation flow with operator-
   approval gate, posture YELLOW default with red/green
   toggleable. 7 Decisions, 6 implementation tranches E1-E6
   targeting B187-B192.

2. Frontier utilities:
   - smoke-test-frontier.command (NEW): python one-shot via
     .venv. Resolves key from secrets store, instantiates
     FrontierProvider directly, calls Anthropic compat endpoint,
     prints response. Validated 2026-05-07 with
     claude-sonnet-4-6.
   - fix-frontier-base-url.command (NEW): one-shot .env patcher.
     The original setup script wrote
     FSF_FRONTIER_BASE_URL=https://api.anthropic.com/v1 but the
     FrontierProvider appends /v1/chat/completions internally —
     would have produced 404 on real dispatches. Rewrites to
     correct https://api.anthropic.com and restarts daemon.
   - setup-anthropic-frontier.command: two corrections from B185
     ship — base_url no longer includes /v1, fallback CLI
     invocation now uses python -m forest_soul_forge.cli.main
     (the package has no __main__.py so the bare path fails).
     Both bugs surfaced during the live B185 run.

Smoke test passed end-to-end 2026-05-07 02:59 UTC; daemon
restarted with corrected base URL 03:03 UTC. Frontier wiring
verified live through both the direct-Python path (smoke test)
and the daemon path (after fix).

Per ADR-0044 D3: design-only + operator scripts. Zero kernel ABI
changes.

Per ADR-0001 D2: ADR-0056 documents Smith's identity-invariance
posture (one constitution_hash + DNA at birth; tools_add grows
per-instance state only). No code in this burst touches
identity.

Next burst: B187 — E1 (birth Smith with kit + branch isolation
+ frontier preference + posture YELLOW default)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 186 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
