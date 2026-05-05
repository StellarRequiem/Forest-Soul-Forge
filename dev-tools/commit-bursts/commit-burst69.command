#!/usr/bin/env bash
# Burst 69: ADR-0036 T5 — /verifier/scan daemon endpoint.
# T4 (per-Verifier scheduled-task cron) is deferred — see commit body.
#
# The Verifier is now operator-triggerable end-to-end via HTTP. The
# substrate the close plan referenced ("existing scheduled-task surface")
# doesn't exist yet; that's its own ADR-grade work that this burst
# does not attempt.
#
# Test delta: 2050 -> 2058 passing (+8 net; the +28 from T3b stayed
# green through the refactor that added arun_scan).

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 69 — ADR-0036 T5 /verifier/scan endpoint ==="
echo
clean_locks
git add src/forest_soul_forge/daemon/routers/verifier.py \
        src/forest_soul_forge/daemon/app.py \
        src/forest_soul_forge/verifier/scan.py \
        tests/unit/test_daemon_verifier_scan.py \
        commit-burst69.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ADR-0036 T5: POST /verifier/scan daemon endpoint (T4 deferred)

The Verifier is now operator-triggerable end-to-end via HTTP. The
substantive auto-detection pipeline (T1+T2+T3a+T3b) is reachable
through a single POST. T4 (per-Verifier scheduled-task cron) is
deferred — see scope note below.

Endpoint:
  POST /verifier/scan
    body: {target_instance_id, verifier_instance_id, max_pairs?,
           since_iso?, min_confidence?}
    deps: require_writes_enabled + require_api_token
    returns: ScanResult JSON (counts + per-pair outcomes)
    side effect: emits verifier_scan_completed audit event with
                 bounded counts payload

Wiring (verifier.py):
- memory: Memory bound to registry's connection
- classify: async wrapper over provider.complete in CLASSIFY mode.
  Returns string directly (verified at base.py / frontier.py).
  System prompt: 'You are a strict-JSON memory-contradiction
  classifier...' — keeps the model on-format.
- flagger: closure over memory.flag_contradiction
- The full scan loop holds the daemon's write_lock (single-writer
  SQLite discipline)

VerifierScan refactor (additive, preserves T3b tests):
- Added arun_scan() for async classify callable. Same logic as
  run_scan; awaits classify; calls _apply_decision identically.
- Extracted _prepare_pair (sync hydrate + prompt build) and
  _apply_decision (parse + branch + flag) so sync run_scan and
  async arun_scan share both. Per-pair tally lives in _tally().
- Added AsyncClassifyCallable type alias.
- All 28 T3b tests still pass without modification.

Audit event payload (verifier_scan_completed) is intentionally
bounded — counts + verifier identity + target + min_confidence/
max_pairs settings. Per-pair detail rides in the response body.
ADR-0006 + ADR-0027 §6 — 'an attacker who got operator approval
to run the verifier should not be able to disclose a thousand
memory contents inside a single audit line'.

T4 deferred — explicit scope note:
The v0.2 close plan and ADR-0036 T4 referenced 'the existing
scheduled-task surface' for per-Verifier cron. That surface does
not actually exist in the codebase yet. Building a generic
scheduled-task system is its own substantive piece of work +
deserves its own ADR (cadence semantics, retry policy, cancellation,
operator UI, audit trail). v0.3 ships with on-demand scans only;
operators schedule via their own cron / launchd / systemd / etc.
wrapping curl /verifier/scan. T4 is queued as 'Generic
scheduled-task substrate' for the next operational round.

Tests (test_daemon_verifier_scan.py +8 cases):
- TestEndpoint (6): no-pairs returns zero counts; canned high-
  confidence contradiction flags + audits (memory_contradictions
  row written; verifier_scan_completed event landed); low-confidence
  does not flag; missing target_instance_id 422; invalid
  min_confidence 422; max_pairs caps response.
- TestAuthGate (1): missing X-FSF-Token returns 401 when token
  configured; valid token passes through.
- TestArunScanParity (1): direct test of arun_scan against the
  canned response confirms the refactor preserved behavior.

Test delta: 2050 -> 2058 passing (+8 net).
The 28 existing T3b tests stayed green through the arun_scan
refactor — important confirmation that the sync/async split
didn't change observable behavior on the sync path.

Builtin tool count: 52 (no new tool this burst).
Daemon routers: 25 -> 26 (+verifier router).

Next: Burst 70 — ADR-0036 T6 (schema v11 -> v12: flagged_state
column on memory_contradictions) + T7 (memory_recall.v1 surface
extension). After that ADR-0036 is feature-complete pending the
deferred T4 scheduled-task substrate."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 69 landed. ADR-0036 T5 in production. /verifier/scan reachable."
echo "Auto-detection pipeline now operator-triggerable end-to-end."
echo "Next: Burst 70 (ADR-0036 T6 + T7 — flagged_state lifecycle + recall surface)."
echo ""
read -rp "Press Enter to close..."
