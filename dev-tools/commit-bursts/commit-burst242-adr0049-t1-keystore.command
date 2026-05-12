#!/bin/bash
# Burst 242 — ADR-0049 T1: AgentKeyStore wrapper.
#
# Phase 4 security hardening opens. ADR-0049 is the per-event
# digital-signatures arc that flips the audit chain from
# tamper-evident to tamper-proof. T1 ships the per-agent
# private-key storage surface (ed25519 keypairs live here once
# T4 wires birth-time keygen).
#
# Design adjustment from the ADR draft: T1+T2+T3 collapse into
# a thin wrapper over the ADR-0052 SecretStoreProtocol. The
# secrets store already covers all three backends ADR-0049's
# draft called for (file / keychain / vaultwarden); building a
# parallel substrate would have duplicated code + given the
# operator two "where do secrets live" surfaces to think about.
# Documented in the ADR-0049 status block.
#
# Files added:
#
# 1. src/forest_soul_forge/security/keys/__init__.py
#    Package docstring covers the design adjustment + threat-
#    model boundary (base64 is encoding, not encryption; the
#    backend's job to keep the key bytes confidential).
#
# 2. src/forest_soul_forge/security/keys/agent_key_store.py
#    AgentKeyStore class with store/fetch/fetch_strict/delete/
#    list_agent_ids. SECRET_NAME_PREFIX="forest_agent_key:" is
#    the locked on-disk format. Backend errors wrapped as
#    AgentKeyStoreError; not-found-strict raises
#    AgentKeyNotFoundError. resolve_agent_key_store() factory
#    with explicit-backend bypass for test isolation.
#
# 3. tests/unit/test_agent_key_store.py
#    19 tests over a FileStore-backed wrapper in tmpdir.
#    Covers round-trip, overwrite, multi-agent isolation,
#    namespace prefix lock, list_agent_ids filtering of non-
#    agent secrets, base64-corruption error surfacing,
#    factory cache bypass, and instance_id input validation.
#
# 4. docs/decisions/ADR-0049-per-event-signatures.md
#    Status flipped from Proposed to Accepted with the design
#    adjustment documented inline. Tranche table marks T1 DONE
#    + T2/T3 as superseded (subsumed by ADR-0052).
#
# Test verification (sandbox):
#   AgentKeyStore tests: 19 passed
#   Adjacent secrets + keychain + vaultwarden + keystore
#     suites: 148 passed, 6 skipped (env-gated keychain/bw
#     CLI tests that only run on Alex's Mac)
#   Zero B242-caused failures.
#
# Per ADR-0001 D2: no identity surface touched (T1 is storage
#   substrate; birth pipeline keygen is T4).
# Per ADR-0044 D3: additive — new package + new test file. Zero
#   existing call-site changes.
# Per CLAUDE.md Hippocratic gate: no removals; T2/T3 marked
#   superseded in the ADR table, not deleted from the doc.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/keys/__init__.py \
        src/forest_soul_forge/security/keys/agent_key_store.py \
        tests/unit/test_agent_key_store.py \
        docs/decisions/ADR-0049-per-event-signatures.md \
        dev-tools/commit-bursts/commit-burst242-adr0049-t1-keystore.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0049 T1 AgentKeyStore wrapper (B242)

Burst 242. ADR-0049 T1 — per-agent ed25519 private-key storage
substrate. Phase 4 security hardening opens.

Design adjustment from the ADR draft: T1+T2+T3 collapse into
a thin wrapper over the ADR-0052 SecretStoreProtocol. The
secrets store already covers all three backends ADR-0049's
draft called for (file / keychain / vaultwarden). One operator-
facing surface for 'where do secrets live'; one CLI for both
plugin-secret and agent-key management.

AgentKeyStore.store/fetch/fetch_strict/delete/list_agent_ids
exposes bytes in/out; base64-encoded at the wrapper boundary
so the underlying string-valued SecretStoreProtocol handles
transport. Agent keys land under the secret name
'forest_agent_key:<instance_id>' (the prefix is the locked
on-disk format — changing it is a backwards-incompatible
migration).

Error surface: AgentKeyStoreError wraps backend failures so
callers don't have to import from the secrets package;
AgentKeyNotFoundError is the strict-fetch path's signal.

Tests: 19 new + 148 adjacent secrets/keychain/vaultwarden
tests stay green proving the wrapper integrates without
disturbing the substrate.

Per ADR-0001 D2: no identity surface touched (T1 is storage;
keygen comes in T4).
Per ADR-0044 D3: additive — new package + new test file.
Per CLAUDE.md Hippocratic gate: no removals."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 242 complete ==="
echo "=== ADR-0049 T1 substrate live. T4 (birth-time keygen) queued. ==="
echo "Press any key to close."
read -n 1
