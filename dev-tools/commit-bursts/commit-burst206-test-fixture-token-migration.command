#!/bin/bash
# Burst 206 — test fixture token migration + drift fixes.
#
# B148 (T25 security hardening) made write endpoints require an
# X-FSF-Token. Pre-B148 the daemon defaulted to no auth; post-B148 if
# api_token is unset, the daemon AUTO-GENERATES one on first boot and
# writes to .env. Operators opt out via FSF_INSECURE_NO_TOKEN=true.
#
# That broke 62 unit tests. The test fixtures used DaemonSettings(...)
# without setting api_token or insecure_no_token, so:
#   1. The daemon's lifespan auto-generated a token, OR
#   2. pydantic-settings loaded FSF_API_TOKEN from the project's .env
#      (the auto-generated value from a previous boot).
# Either way the test's POST requests sent no X-FSF-Token header, got
# back 401, and the assertions failed.
#
# B206 fix: every test fixture that builds a DaemonSettings without
# explicitly setting api_token now passes BOTH:
#   - api_token=None   (overrides FSF_API_TOKEN loaded from .env;
#                       constructor args win over env-loaded values
#                       per pydantic-settings precedence)
#   - insecure_no_token=True
# Together: no token required for the test's TestClient requests.
# Tests that explicitly verify token-required behavior (TestAuthGate
# in test_daemon_writes.py + test_daemon_verifier_scan.py +
# test_daemon_readonly.py) keep their api_token=... explicit.
#
# Files patched (one or more DaemonSettings call site each):
#   tests/unit/test_daemon_agent_posture.py
#   tests/unit/test_daemon_memory_consents.py
#   tests/unit/test_daemon_plugin_grants.py
#   tests/unit/test_daemon_readonly.py
#   tests/unit/test_daemon_skills_run.py
#   tests/unit/test_daemon_tool_dispatch.py
#   tests/unit/test_daemon_verifier_scan.py
#   tests/unit/test_daemon_writes.py
#
# Three additional drift fixes bundled because they surfaced once
# the auth gate stopped masking them:
#
#   tests/unit/test_secrets.py
#     The _agent helper used `with reg._conn:` to wrap an agent-row
#     INSERT in a transaction. Post-refactor, reg._conn is a
#     thread-local proxy (registry/registry.py L228), not a real
#     sqlite3.Connection, so it doesn't implement context-manager
#     protocol. The proxy has isolation_level=None, so each execute
#     auto-commits — explicit transaction wrapping is unnecessary
#     for a single INSERT. Dropped the `with` block.
#
#   tests/unit/test_daemon_readonly.py::TestAudit::test_audit_tail_returns_seeded_events
#     Asserted `body["count"] == 1` (only genesis). The daemon's
#     lifespan now emits additional events at startup (auth status,
#     forged_tool_loader from B202, scheduler events, etc.) so a
#     freshly-built TestClient sees more than just genesis. Relaxed
#     to `count >= 1` plus an explicit `chain_created in event_types`
#     check — the real invariant is "genesis is somewhere in the
#     tail," not "tail length is exactly 1."
#
#   tests/unit/test_trait_engine.py::TestLoading::test_expected_role_count
#     Asserted `len(engine.roles) == 43`. Bumped to 44 to account
#     for the ADR-0056 experimenter role (Smith agent, B188-B197)
#     that landed after this assertion was last refreshed.
#
# Suite result:
#   Pre-B206:  2,625 passed / 62 failed / 11 skipped / 1 xfailed
#   Post-B206: 2,738 passed /  0 failed / 11 skipped / 1 xfailed
#   Delta:     +113 (62 fixed + 51 collection errors fixed)
#
# What we deliberately did NOT do:
#   - Move the api_token=None / insecure_no_token=True pattern into a
#     conftest.py helper. Would centralize but obscure the per-fixture
#     decision; explicit-at-call-site is better for test legibility.
#     A future burst can refactor if the pattern proliferates further.
#   - Modify the source-side B148 auth behavior. Test isolation is
#     orthogonal to production auth; the explicit opt-out is the
#     supported path for test fixtures.
#
# Per ADR-0001 D2: no identity surface touched.
# Per ADR-0044 D3: zero ABI changes — pure test-fixture work plus
#                  three assertion-drift fixes, no source code modified.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/unit/test_daemon_agent_posture.py \
        tests/unit/test_daemon_memory_consents.py \
        tests/unit/test_daemon_plugin_grants.py \
        tests/unit/test_daemon_readonly.py \
        tests/unit/test_daemon_skills_run.py \
        tests/unit/test_daemon_tool_dispatch.py \
        tests/unit/test_daemon_verifier_scan.py \
        tests/unit/test_daemon_writes.py \
        tests/unit/test_secrets.py \
        tests/unit/test_trait_engine.py \
        dev-tools/commit-bursts/commit-burst206-test-fixture-token-migration.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "test(fixtures): B148 X-FSF-Token migration + drift fixes (B206)

Burst 206. The B148 security-hardening change made write endpoints
require X-FSF-Token, with auto-generation of a token if none is set.
That broke 62 unit tests whose fixtures didn't set api_token or
insecure_no_token. They sent no token, got 401, failed.

Fix shape: every test fixture that builds a DaemonSettings without
explicitly verifying token behavior now passes BOTH api_token=None
(to override the FSF_API_TOKEN value pydantic-settings loads from
.env) AND insecure_no_token=True (to opt out of B148 auto-generation
at lifespan). Tests that DO explicitly verify token-required
behavior (TestAuthGate in three files) keep api_token=... explicit.

Files patched: test_daemon_{agent_posture,memory_consents,
plugin_grants,readonly,skills_run,tool_dispatch,verifier_scan,
writes}.py — 10 DaemonSettings call sites total.

Three drift fixes bundled because they surfaced once the auth gate
stopped masking them:

  test_secrets.py — dropped \`with reg._conn:\` transaction wrapper.
  Post-refactor reg._conn is a thread-local proxy (not a real
  sqlite3.Connection), no __enter__/__exit__. Proxy auto-commits
  per execute (isolation_level=None) so a single INSERT doesn't
  need explicit transaction batching.

  test_daemon_readonly.py TestAudit — relaxed body[count] == 1 to
  >= 1 + explicit chain_created-in-event_types check. Daemon's
  lifespan now emits more startup events (forged_tool_loader from
  B202, etc.) so a fresh TestClient sees more than just genesis.

  test_trait_engine.py — bumped role count assertion 43 -> 44 for
  ADR-0056 experimenter role addition (Smith agent, B188-B197).

Suite result: 62 failed -> 0 failed. 2,625 passed -> 2,738 passed
(+113: 62 fixed + 51 collection errors that were blocking other
tests from even running).

Per ADR-0001 D2: no identity surface touched.
Per ADR-0044 D3: zero ABI changes — pure test-fixture work plus
                 three assertion-drift fixes, no source code modified."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 206 complete ==="
echo "=== Suite green: 2,738 passed / 0 failed / 11 skipped / 1 xfailed. ==="
echo "Press any key to close."
read -n 1
