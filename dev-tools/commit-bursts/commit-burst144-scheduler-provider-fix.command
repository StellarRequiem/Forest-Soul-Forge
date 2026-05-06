#!/bin/bash
# Burst 144 — fix scheduler provider lookup + sibling DispatchFailed.reason
# drift in scenario_runtime.
#
# Surfaced live 2026-05-05 by Task #22 audit-chain query. After B142
# unmasked the real exception type (had been hidden by an
# AttributeError on outcome.reason), the scheduler's tool_call
# dispatch returned outcome=failed with:
#
#   exception_type: ToolValidationError
#   exception_message: "llm_think.v1: no LLM provider wired into
#                       this dispatcher. Either the daemon was built
#                       without a provider, or the active provider
#                       is offline (check GET /runtime/provider)."
#
# Root cause: API drift in two places.
#
# 1. scheduler/task_types/tool_call.py:132
#    Read `getattr(app.state, "active_provider", None)`. Nothing in
#    the daemon ever sets `app.state.active_provider` (the lifespan
#    at app.py:395 stores the provider REGISTRY as `app.state.providers`,
#    not a single provider). The chat / tool-dispatch / skills-run /
#    pending-calls / conversation paths all use a `_resolve_active_provider`
#    helper that reads `app.state.providers.active()`. The scheduler
#    never picked up the same pattern (or drifted from it).
#
#    Effect: every scheduled task dispatch had provider=None. llm_think
#    refused with ToolValidationError. The 3 active scheduled tasks
#    (B141) all returned outcome=failed and tripped circuit breakers
#    after 3 retries.
#
# 2. scheduler/scenario_runtime.py:428
#    `f"dispatch failed: {outcome.reason}"` on a DispatchFailed
#    object that has no `.reason` attribute. Same bug class as
#    B142 in tool_call.py:209, but for the scenario task type
#    instead of the tool_call task type. Discovered during the
#    Task #24 audit triggered by Task #22's investigation.
#
# What ships:
#
#   src/forest_soul_forge/daemon/scheduler/task_types/tool_call.py
#     - Lines 127-149: provider resolution rewritten to mirror the
#       chat path's pattern (read app.state.providers, call
#       .active(), default to None on any error). Inline comment
#       documents the bug + B144 fix date.
#
#   src/forest_soul_forge/daemon/scheduler/scenario_runtime.py
#     - Line 428: outcome.reason → outcome.exception_type with
#       contextual fields (tool_key + audit_seq) for parity with
#       B142's tool_call.py fix. Inline comment documents the
#       sibling drift.
#
#   verify-b144.command (new at repo root) — focused end-to-end
#     verification. Restarts daemon (loads B144), resets all 3 active
#     scheduled task breakers, triggers each one, asserts outcome=
#     succeeded (was failed pre-B144). Shows final scheduler state
#     + tail of audit chain to confirm tool_call_succeeded events
#     actually fired.
#
# Verified live 2026-05-05:
#   - Pre-B144: all 3 scheduled tasks returned outcome=failed,
#     dashboard_watcher tripped breaker after 3 fires (audit
#     entries 1190-1206)
#   - Post-B144: all 3 scheduled tasks return outcome=succeeded
#     (audit entries 1212/1215/1218 are real tool_call_succeeded
#     events; the LLM (qwen2.5-coder:7b) actually responded for
#     each)
#   - Final state: succ=1 / fail=existing-counter for each task
#
# Closes Task #22 (post-B142 dispatch failures) and Task #24
# (B142-style API drift audit).
#
# Phase 1 stability hardening (per outside review's
# 'lock/concurrency issues' flag) is now actually complete:
#   B142 — DispatchFailed.reason → exception_type
#   B143 — per-thread sqlite3 connection proxy
#   B144 — scheduler provider lookup + scenario_runtime sibling drift
#
# The 24/7 specialist stable + chat tab + scheduler now all work
# end-to-end on the live Mac mini.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/daemon/scheduler/task_types/tool_call.py \
        src/forest_soul_forge/daemon/scheduler/scenario_runtime.py \
        verify-b144.command \
        dev-tools/commit-bursts/commit-burst144-scheduler-provider-fix.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "fix(scheduler): provider lookup + scenario_runtime DispatchFailed drift (B144)

Burst 144. Surfaced live 2026-05-05 by Task #22 audit-chain query.
After B142 unmasked the real exception type, the scheduler's
tool_call dispatch returned outcome=failed with ToolValidationError
'no LLM provider wired into this dispatcher'.

Root cause: API drift in two places.

1. scheduler/task_types/tool_call.py:132 read
   getattr(app.state, 'active_provider', None) — an attribute
   nothing in the daemon ever sets. The lifespan stores the
   provider registry as app.state.providers (not a single provider
   object). The chat / tool-dispatch / skills-run / pending-calls /
   conversation paths all use a _resolve_active_provider helper
   that reads app.state.providers.active(). Scheduler drifted.
   Effect: every scheduled task got provider=None, llm_think
   refused with ToolValidationError, the 3 active scheduled tasks
   (B141) all failed and tripped breakers.

2. scheduler/scenario_runtime.py:428 had the same B142-style drift
   (outcome.reason on DispatchFailed which has no .reason). Found
   via the Task #24 audit triggered by Task #22.

Ships:
- tool_call.py:127-149: provider resolution mirrors chat pattern
  (app.state.providers, call .active(), default None on error)
- scenario_runtime.py:428: outcome.reason → outcome.exception_type
  with tool_key + audit_seq for parity with B142
- verify-b144.command: end-to-end verify. Restart, reset breakers,
  trigger all 3 scheduled tasks, assert outcome=succeeded

Verified live 2026-05-05:
- Pre-B144: all 3 scheduled tasks outcome=failed
- Post-B144: all 3 outcome=succeeded; audit chain shows real
  tool_call_succeeded for each (qwen2.5-coder:7b responded)

Closes Task #22 (post-B142 dispatch failures) and Task #24
(B142-style API drift audit).

Phase 1 stability hardening complete:
  B142 — DispatchFailed.reason → exception_type
  B143 — per-thread sqlite3 connection proxy
  B144 — scheduler provider lookup + scenario_runtime sibling

Chat + scheduler + 24/7 specialist stable all work end-to-end."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 144 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
