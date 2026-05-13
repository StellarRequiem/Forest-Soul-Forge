#!/bin/bash
# Burst 264 — ADR-0051 T4+T5+T6+T7: dispatcher integration.
# THE SUBSTRATE GOES LIVE.
#
# Bundles four ADR tranches that all touch the dispatcher hot path:
#
#   T4 — dispatcher integration (read FSF_TOOL_SANDBOX, look up
#        sandbox_eligible on the ToolDef, route eligible tools
#        through default_sandbox().run(...) instead of in-process
#        tool.execute(...))
#   T5 — profile generation FROM CONSTITUTION (ctx.constraints'
#        allowed_paths / allowed_commands / allowed_hosts flow into
#        build_profile() at dispatch time)
#   T6 — audit chain extension (additive sandbox_mode / sandbox_used
#        / sandbox_violation / sandbox_error_kind / sandbox_stderr /
#        sandbox_skipped_reason fields on existing event types)
#   T7 — permissive-mode fallback (try sandbox, on setup_failed run
#        in-process with skipped_reason annotation; strict refuses)
#
# Default FSF_TOOL_SANDBOX=off preserves bit-identical pre-T4
# behavior — every existing dispatcher test still passes. Operators
# opt in via env var when they want the OS-level guarantee.
#
# === What this changes in the dispatcher ===
#
# 1. New imports: os, pickle, sandbox + sandbox_context modules.
# 2. New module-level helpers:
#    - _SANDBOX_MODES frozenset
#    - _read_sandbox_mode() — env-var reader, unknown values → "off"
#    - SandboxRefused(ToolError) exception
#    - SandboxExecutionOutcome dataclass
#    - _sandbox_meta() — composes additive event_data dict
# 3. ToolDispatcher gets two new fields:
#    - sandbox: Sandbox | None = None (tests inject FakeSandbox;
#                                       production picks default_sandbox())
#    - sandbox_mode_fn: callable() -> str | None (tests inject fixed
#                                       lambda; default reads env var)
# 4. New dispatcher methods:
#    - _resolve_sandbox_mode() — closure or env read
#    - _lookup_sandbox_eligible(key) — reads from tool_catalog
#    - _resolve_sandbox() — injected impl or default_sandbox()
#    - _execute_tool_maybe_sandboxed() — the routing logic
# 5. BOTH tool.execute() call sites (primary dispatch + resumed-from-
#    ticket) now route through _execute_tool_maybe_sandboxed().
# 6. ALL audit events on dispatched/succeeded/failed carry the
#    sandbox metadata fields per ADR Decision 6 (additive schema).
#
# === Decision matrix ===
#
#   FSF_TOOL_SANDBOX=off               → in-process, sandbox_mode=off
#   sandbox_eligible=False             → in-process,
#                                        skipped_reason=ineligible
#   no platform sandbox + strict       → REFUSE (SandboxRefused)
#   no platform sandbox + permissive   → in-process fallback,
#                                        skipped_reason=no_sandbox_on_platform
#   sandbox.run() setup_failed + strict     → REFUSE
#   sandbox.run() setup_failed + permissive → in-process fallback,
#                                        skipped_reason=setup_failed_permissive_fallback
#   sandbox.run() tool_error           → unpickle + raise (caller's
#                                        existing EVENT_FAILED catches)
#   sandbox.run() sandbox_violation    → REFUSE in BOTH modes
#                                        (violation = real block, not
#                                         platform unavailability)
#   sandbox.run() success              → unpickle ToolResult, emit
#                                        with sandbox_used=True
#
# === Tests ===
#
# 10 new test classes/methods in test_tool_dispatcher.py:
#   - TestSandboxOffMode (1)
#   - TestSandboxStrictMode (1)
#   - TestSandboxNoSandboxOnPlatform (2)
#   - TestSandboxSetupFailed (2)
#   - TestSandboxViolation (2)
#   - TestSandboxModeFromEnv (2)
# Plus _FakeSandbox helper + _pickle_tool_result helper.
#
# Existing dispatcher tests (53) all still pass — verified pre-push.
#
# Expected diag-b264 count: 117 → 127 (+10).
#
# === What T1-T8 status looks like after this push ===
#
# T1 ✅ B261 — substrate (Sandbox Protocol, MacOSSandboxExec,
#               SerializableToolContext, sandbox_eligible field)
# T2 ✅ B262 — Linux bwrap
# T3 ✅ B263 — 5 catalog entries annotated sandbox_eligible: false
# T4 ✅ B264 — dispatcher integration ← THIS BURST
# T5 ✅ B264 — profile from constitution ← THIS BURST
# T6 ✅ B264 — audit chain additive fields ← THIS BURST
# T7 ✅ B264 — permissive fallback ← THIS BURST
# T8 ⏸  next — runbook (docs/runbooks/tool-sandbox.md)

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/dispatcher.py \
        tests/unit/test_tool_dispatcher.py \
        diag-b264-tests.command \
        dev-tools/commit-bursts/commit-burst264-adr0051-t4-substrate-live.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0051 T4+T5+T6+T7 — dispatcher integration (B264)

Burst 264. THE SUBSTRATE GOES LIVE.

Bundles four ADR-0051 tranches that all touch the dispatcher
hot path (split into separate ADR tranches for clarity but
shipped together since they're tightly coupled):

  T4 — dispatcher reads FSF_TOOL_SANDBOX, looks up
       sandbox_eligible on the ToolDef, routes eligible tools
       through default_sandbox().run(...) instead of in-process
       tool.execute(...).

  T5 — sandbox profile derived from ctx.constraints'
       allowed_paths / allowed_commands / allowed_hosts at
       dispatch time, per ADR Decision 4. Fresh profile each
       call so a constitution mutation post-birth (plugin grant
       / posture change) is reflected immediately.

  T6 — audit chain additive fields: sandbox_mode, sandbox_used,
       sandbox_violation, sandbox_error_kind, sandbox_stderr,
       sandbox_skipped_reason on existing tool_call_dispatched /
       _succeeded / _failed event types. Per ADR Decision 6 the
       schema stays additive — pre-T4 readers see chains they
       still understand (ignore unknown fields gracefully).

  T7 — permissive-mode fallback: try sandbox, on setup_failed
       (binary missing, user-namespaces unavailable) run
       in-process with skipped_reason annotation. Strict refuses
       on the same failure shape. Sandbox violations are
       refused in BOTH modes — a violation means the tool tried
       to do something outside its declared scope, which is a
       real failure the operator wants to see.

Default FSF_TOOL_SANDBOX=off preserves bit-identical pre-T4
behavior. Every existing dispatcher test still passes
(verified: 117 pre-burst, 127 post-burst — 53 dispatcher tests
unchanged + 10 new sandbox-integration tests added). Operators
opt in via env var when they want the OS-level guarantee.

Implementation details:

- _read_sandbox_mode() reads FSF_TOOL_SANDBOX; unknown values
  fall back to 'off' so a typo doesn't silently escalate a
  daemon into strict mode.
- ToolDispatcher.sandbox + sandbox_mode_fn are new fields that
  default to None (use platform default + env var). Tests
  inject a FakeSandbox + a fixed-value lambda to deterministically
  exercise every error_kind branch without spawning real
  subprocesses.
- _execute_tool_maybe_sandboxed() is the single routing method.
  Both call sites (primary dispatch + resumed-from-ticket)
  route through it, so sandbox metadata is emitted identically
  across both paths.
- SandboxRefused is a ToolError subclass — caller's existing
  EVENT_FAILED branches catch it via the ToolError except;
  the dispatcher then adds sandbox_violation=true and
  sandbox_error_kind on the audit event_data.

Out-of-scope for B264 (only T8 remains in ADR-0051):
- docs/runbooks/tool-sandbox.md — the per-platform operator
  setup runbook. Future burst.

Verification: 127 passed, 1 skipped (Linux integration on
macOS) in diag-b264-tests collection."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 264 complete — ADR-0051 substrate is LIVE ==="
echo "=== Default FSF_TOOL_SANDBOX=off; operators opt in via env var ==="
echo "Press any key to close."
read -n 1
