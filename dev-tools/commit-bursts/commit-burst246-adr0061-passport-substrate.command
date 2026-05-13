#!/bin/bash
# Burst 246 — ADR-0061 Agent Passport: substrate (T1-T3 + T5
# tests). First commit under ELv2 that actually ships code.
#
# Closes the cryptographic primitive layer for cross-machine
# agent roaming. T4 (trust config + K6 quarantine integration),
# T6 (HTTP endpoint), and T7 (CLI subcommand) ship in follow-up
# bursts. The substrate today enables: programmatic mint via
# Python API, programmatic verify, operator master keypair
# bootstrap at daemon startup.
#
# Files added:
#
# 1. docs/decisions/ADR-0061-agent-passport.md
#    Full ADR. Decisions 1-5 (operator master keypair, passport
#    JSON format, mint surface, trust config, K6 quarantine
#    integration). 7 tranches T1-T7. Failure modes + consequences.
#
# 2. src/forest_soul_forge/security/operator_key.py
#    resolve_operator_keypair() returns (priv_bytes, pub_b64).
#    First-call generates fresh ed25519 keypair + stores private
#    under reserved name "forest_operator_master:default" in the
#    AgentKeyStore's underlying SecretStoreProtocol backend.
#    Process-cached. Explicit-store path for tests.
#
# 3. src/forest_soul_forge/security/passport.py
#    mint_passport() + verify_passport() pure cryptographic
#    primitives. Canonical-JSON-then-sign over body-without-
#    signature. ed25519 substrate from ADR-0049 reused. Mint
#    rejects mismatched priv/pub pairs before producing un-
#    verifiable passports. Verify short-circuits with human-
#    readable reason on first failure.
#
# 4. tests/unit/test_operator_key.py
#    7 tests: first-call generates; second-call returns same;
#    pub matches priv; namespace doesn't pollute agent list;
#    reserved name shape locked; explicit-store bypasses
#    cache; keypair persists across simulated restarts.
#
# 5. tests/unit/test_passport.py
#    19 tests: mint round-trip; field shape locked; JSON
#    round-trip; input validation (empty fields, wrong key
#    lengths, priv/pub mismatch, non-base64); verify shape
#    errors (missing fields, wrong version, malformed sig
#    prefix); trust failures; cryptographic tampering
#    (signature, body); expiry (refused when past, accepted
#    when future, accepted when None); hardware fingerprint
#    (wrong fp refused, multi-fp authorization).
#
# Test verification (sandbox):
#   passport + operator_key: 26 passed
#   passport + operator_key + key_store + audit_chain_sigs +
#     daemon_writes + integration: 133 passed
#   Zero regressions.
#
# Per ADR-0001 D2: no identity surface touched (passport sits
#   alongside identity, doesn't replace it). The operator
#   master IS a new key surface but it's a deployment-level
#   identity, not an agent identity.
# Per ADR-0044 D3: additive. New security/ submodules. Zero
#   existing call-site changes.
# Per CLAUDE.md Hippocratic gate: no removals. K6 quarantine
#   integration (T4) is queued separately + will be backward-
#   compatible (legacy agents without passport pass via the
#   existing hardware-binding match path).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0061-agent-passport.md \
        src/forest_soul_forge/security/operator_key.py \
        src/forest_soul_forge/security/passport.py \
        tests/unit/test_operator_key.py \
        tests/unit/test_passport.py \
        dev-tools/commit-bursts/commit-burst246-adr0061-passport-substrate.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0061 agent passport substrate (B246)

Burst 246. ADR-0061 — cross-machine agent roaming via Forge-
signed passports. Ships T1 + T2 + T3 + T5 (tests). T4 trust
config + K6 quarantine integration, T6 HTTP endpoint, T7 CLI
subcommand queued for follow-up bursts.

Architecture: operator master ed25519 keypair (bootstrapped at
daemon startup, stored in AgentKeyStore under reserved name
'forest_operator_master:default') signs passport JSON
documents. Passports bind an agent (dna + instance_id + public
key) to a set of authorized hardware fingerprints + an optional
expiration. Receiving daemons verify: signature, issuer trust,
expiry, current host fingerprint in authorized set.

T1 operator_key.py: resolve_operator_keypair() returns
(priv_bytes, pub_b64). First-call generates + persists; cached
per-process; explicit-store path for tests.

T2 passport.py::mint_passport: pure cryptographic primitive
returning the passport dict ready to JSON-serialize. Rejects
mismatched priv/pub pairs before producing un-verifiable
passports.

T3 passport.py::verify_passport(passport, trusted_pubkeys,
current_hw_fp): returns (valid, reason). Short-circuits on
first failure with human-readable diagnostic.

T5 tests: 26 tests (7 operator_key + 19 passport). Round-trip,
input validation, shape errors, trust failures, tamper
detection (signature + body), expiry edge cases, hardware
fingerprint authorization.

This is the foundation the ADR-0046 ELv2 business model needs:
agents born on the operator's Forge service can roam to other
machines the operator owns, but only via explicit passport.
Hardware binding (ADR-003X K6) stays as the default protection;
passport is the explicit-roaming escape hatch.

Per ADR-0001 D2: agent identity unchanged; operator master is
  deployment-scoped new key surface.
Per ADR-0044 D3: additive substrate.
Per CLAUDE.md Hippocratic gate: no removals; K6 integration
  (T4) is forward-compatible."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 246 complete ==="
echo "=== ADR-0061 passport substrate live. T4 quarantine wiring queued. ==="
echo "Press any key to close."
read -n 1
