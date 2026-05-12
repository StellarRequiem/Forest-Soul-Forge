#!/bin/bash
# Burst 236 — integration-test fixture migration.
#
# Closes the X-FSF-Token gate gap the B206 unit-test fixture
# migration left open for integration tests. Also fixes the
# trait-count drift (test was pinned at 42 roles; reality is 44
# post-ADR-0047 assistant + ADR-0056 experimenter).
#
# Was: 2 passing, 10 failing across tests/integration/.
# Now: 12 passing, 0 failing.
#
# Two files touched:
#
# 1. tests/integration/conftest.py (NEW)
#    Session-scoped autouse fixture that rebinds
#    DaemonSettings.model_config['env_file'] to a nonexistent
#    path inside a tmp directory. Pydantic-settings silently
#    skips a missing env_file, so the repo-root .env (where the
#    operator's live FSF_API_TOKEN lives per B148 T25
#    auto-generation) doesn't leak into DaemonSettings.api_token
#    during tests. Belt-and-suspenders: also clears
#    FSF_API_TOKEN from os.environ + sets
#    FSF_INSECURE_NO_TOKEN=true.
#
#    Diagnosis trail: B148 added auto-token-generation; B206
#    migrated unit-test fixtures via api_token=None override but
#    didn't touch integration. DaemonSettings is a pydantic-
#    settings class with env_file='.env', so even after
#    monkeypatch.delenv('FSF_API_TOKEN') the .env content was
#    still being read from disk on every DaemonSettings(...) call.
#    The env_file-redirect technique is the surgical fix —
#    pydantic-settings silently skips a missing path.
#
# 2. tests/integration/test_genre_floor_and_audit_ordering.py
#    test_traits_endpoint_lists_42_roles → ..._44_roles. The
#    trait engine grew two roles since the B124 fix: assistant
#    (ADR-0047 / B135) and experimenter (ADR-0056 / B188).
#    Docstring updated with the role-addition history so future
#    bumps land cleanly.
#
# Per ADR-0001 D2: no identity surface touched (test infra only).
# Per ADR-0044 D3: zero ABI impact (test infra only).
# Per CLAUDE.md Hippocratic gate: no production code removed; the
#   fixture is purely additive at the test boundary.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add tests/integration/conftest.py \
        tests/integration/test_genre_floor_and_audit_ordering.py \
        dev-tools/commit-bursts/commit-burst236-integration-conftest.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "test(integration): session conftest closes X-FSF-Token gap (B236)

Burst 236. Integration-test parallel of the B206 unit-test fixture
migration. Was 2 passed + 10 failed; now 12 passed.

Root cause: B148 (T25 security hardening) auto-generates
FSF_API_TOKEN on first boot and writes it to .env. DaemonSettings
is a pydantic-settings class with env_file='.env', so even after
monkeypatch.delenv('FSF_API_TOKEN') the .env content was still
being read from disk on every DaemonSettings(...) call.

B206 migrated the unit-test fixtures by passing api_token=None
explicitly to every DaemonSettings(...) constructor. The four
integration files were not migrated; their HTTP clients
(TestClient) don't include the X-FSF-Token header, so every write
returned 401.

Fix: session-scoped autouse fixture in
tests/integration/conftest.py rebinds DaemonSettings.model_config
['env_file'] to a nonexistent path inside a tmp directory.
Pydantic-settings silently skips a missing env_file, so
api_token falls back to its Field(default=None), which the
auth gate treats as 'auth disabled, pass through.'

Belt-and-suspenders: also clears FSF_API_TOKEN from os.environ
and sets FSF_INSECURE_NO_TOKEN=true.

Plus: test_traits_endpoint_lists_42_roles renamed and updated to
44 roles (the role count grew via ADR-0047 assistant + ADR-0056
experimenter).

Per ADR-0001 D2: no identity surface touched (test infra only).
Per ADR-0044 D3: zero ABI impact.
Per CLAUDE.md Hippocratic gate: additive at the test boundary."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 236 complete ==="
echo "=== Integration suite: 12 of 12 passing. Auth gap closed. ==="
echo "Press any key to close."
read -n 1
