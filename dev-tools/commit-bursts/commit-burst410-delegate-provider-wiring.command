#!/bin/bash
# Burst 410 - delegate.v1 LLM-provider wiring fix.
#
# B409's wiring_audit_triage real-delegate upgrade surfaced the bug.
# delegate.v1 dispatched llm_pass.v1 on Reviewer-Main successfully,
# but llm_pass's text_summarize step refused with:
#   "text_summarize.v1: no LLM provider wired into this dispatcher.
#    Either the daemon was built without a provider, or the active
#    provider is offline (check GET /runtime/provider)."
#
# The provider WAS available — Engineer-Main's direct text_summarize
# call (step 1 engineer_extract) worked. The delegate path lost it.
#
# Root cause: deps.py:352 set provider_resolver to read
# `app.state.active_provider` — an attribute that is NEVER set
# anywhere in the daemon. The providers manager lives at
# `app.state.providers`; the active provider is
# `app.state.providers.active()`. skills_run.py:203 reads it
# correctly. The delegator factory read the wrong attribute and
# silently returned None, propagating "no provider" to every
# delegated LLM-backed tool call.
#
# This is the exact same wiring-discipline gap CLAUDE.md sec0 §2
# warns about (B350-class): the lambda wiring fired silently when
# the attribute was missing instead of erroring at startup. Unit
# tests would have passed if the test fixture constructed the
# context with the attribute. Live verify is what surfaced it.
#
# What this commit adds:
#
# 1. src/forest_soul_forge/daemon/deps.py
#    Replace the buggy lambda with a named helper
#    `_resolve_provider_for_delegate` that:
#      - Mirrors skills_run._resolve_active_provider exactly.
#      - Reads from app.state.providers (correct attribute).
#      - Calls .active() to fetch the live provider.
#      - Catches exceptions defensively (matches sibling helper).
#      - Documents the B410-followup origin in a long docstring
#        so future-me sees the load-bearing comment in place
#        rather than re-discovering the bug.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: every delegated LLM call fails. Triune scheduled
#     work cannot complete steps 2 + 3. ADR-0034 SW-track triune
#     skill flow (Engineer drafts -> Reviewer reviews) was
#     dead-on-arrival on the delegate path.
#   Prove non-load-bearing: one lambda swap in one helper. Same
#     return type, same call surface. Skills_run + tool_dispatch
#     paths unaffected (they were already reading from the right
#     attribute).
#   Prove alternative is strictly better:
#     (a) Set app.state.active_provider somewhere -- creates a
#         duplicate-source-of-truth bug. Two places claim to hold
#         the active provider; they drift.
#     (b) Pass provider through ctx.delegate -- changes the
#         delegate ABI; bigger blast radius.
#     (c) Fix the lambda -- minimal, mirrors the sibling helper,
#         single source of truth at app.state.providers.
#
# Verification after this commit lands:
#   1. force-restart-daemon (lambda is rebuilt at app startup).
#   2. bash dev-tools/run-triune-triage.command
#      Expected: all 4 steps succeed. reviewer_invoked_seq +
#      architect_invoked_seq populated. Audit chain shows
#      skill_invoked + tool_call_succeeded for llm_pass on
#      Reviewer-Main + Architect-Main.
#   3. Per CLAUDE.md sec0 §2 discipline: a section-06 ctx-wiring
#      probe should be added for delegator_factory.provider_resolver.
#      Queued separately.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/deps.py \
        dev-tools/commit-bursts/commit-burst410-delegate-provider-wiring.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(deps): delegator provider_resolver reads correct attr (B410)

Burst 410. B409's wiring_audit_triage real-delegate upgrade
surfaced this. delegate.v1 dispatched llm_pass.v1 on Reviewer-Main
successfully but text_summarize refused with 'no LLM provider
wired into this dispatcher.'

Root cause: deps.py:352 set provider_resolver to read
app.state.active_provider — an attribute that is NEVER set.
Providers manager lives at app.state.providers; .active() returns
the live provider. skills_run.py reads it correctly. Delegator
read the wrong attribute and silently returned None to every
delegated LLM-backed tool call.

Exact same wiring-discipline class as B350 (CLAUDE.md sec0 §2).
The lambda fired silently on a missing attr instead of failing
loud at startup. Unit tests would have passed if the fixture set
the attribute. Live verify is what found it.

Fix: replace the buggy lambda with named helper
_resolve_provider_for_delegate that mirrors
skills_run._resolve_active_provider exactly:
  - reads app.state.providers
  - calls .active()
  - catches exceptions defensively
  - docstring records the B410 origin so future-me sees the
    load-bearing comment in place.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: every delegated LLM call fails; SW-track triune
    skill flow dead on the delegate path.
  Prove non-load-bearing: one lambda swap; same return type.
  Prove alternative: setting app.state.active_provider creates
    a duplicate-truth bug; passing through ctx.delegate changes
    the ABI; fixing the lambda is minimal.

Queued separately: add a section-06 ctx-wiring probe for
delegator_factory.provider_resolver per CLAUDE.md sec0 §2.

After landing:
  force-restart-daemon
  bash dev-tools/run-triune-triage.command
  Expected: all 4 steps succeed; reviewer + architect
  invoked_seq populated."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 410 complete - delegate provider wiring fix ==="
echo "=========================================================="
echo "Next:"
echo "  1. bash dev-tools/force-restart-daemon.command"
echo "  2. bash dev-tools/run-triune-triage.command"
echo ""
echo "Press any key to close."
read -n 1 || true
