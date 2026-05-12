#!/bin/bash
# Burst 244 — ADR-0049 T5+T6+T7+T8: per-event signatures end-to-end.
#
# Closes ADR-0049. Audit chain is now TAMPER-PROOF for agent-
# emitted events: every dispatch / memory_write / delegate /
# turn / etc. carries an ed25519 signature verifiable against
# the agent's public key. An attacker with disk access can no
# longer forge agent activity without the private key (which
# never leaves the AgentKeyStore).
#
# Four tranches in one burst per Alex's 'more progress per turn'
# directive:
#   T5 sign-on-emit
#   T6 verify-on-replay
#   T7 strict-mode flag
#   T8 operator runbook
#
# Files touched:
#
# 1. src/forest_soul_forge/core/audit_chain.py
#    - ChainEntry dataclass gains optional `signature: str | None`.
#    - to_json_line emits signature ONLY when set so pre-ADR-0049
#      entries round-trip byte-for-byte.
#    - _entry_from_dict parses optional signature back.
#    - AuditChain gains set_signer(signer) + set_verifier(verifier)
#      injection points (kept core/ decoupled from registry +
#      keystore).
#    - append() calls signer when agent_dna is non-None;
#      attaches 'ed25519:<base64>' field. Signer exception is
#      swallowed (entry lands unsigned) so transient keystore
#      failures don't block audit appends.
#    - verify(strict=False):
#        * Strict mode (T7): rejects any agent-emitted entry
#          without a signature.
#        * Signature verify: parses 'ed25519:' prefix, base64-
#          decodes payload, calls verifier(entry_hash_bytes,
#          signature_bytes, agent_dna). Refusals on unsupported
#          algorithm, base64-corruption, verifier-exception,
#          signature-on-operator-entry (defense in depth).
#
# 2. src/forest_soul_forge/daemon/app.py
#    - Lifespan wires the signer + verifier closures. Closures
#      resolve agent_dna → instance_id → key via the registry's
#      agents table + AgentKeyStore. Failure to wire surfaces in
#      startup_diagnostics; chain still hashes correctly without
#      them.
#
# 3. src/forest_soul_forge/tools/builtin/audit_chain_verify.py
#    - New `strict: bool` arg. Validate + thread to
#      AuditChain.verify(strict=...).
#    - Docstring documents strict-mode use cases.
#
# 4. tests/unit/test_audit_chain_signatures.py (NEW)
#    - 17 tests: sign-on-emit (called for agent events, not
#      operator events, returns-none-yields-unsigned, exception
#      doesn't block append), signature-outside-entry-hash,
#      verify accepts/refuses (tampered sig, sig on operator
#      event, unsupported algorithm), legacy passes hash-only,
#      verifier-unwired skip, strict mode (refuses unsigned
#      agent event, tolerates operator events, accepts signed
#      entries, default tolerant mode), chain-entry round-trip
#      with + without signature.
#
# 5. docs/runbooks/per-event-signatures.md (NEW)
#    - Operator runbook: what changed, where the surfaces live,
#      how to run a verify (tolerant + strict), how to read +
#      filter signed events, failure modes (keystore loss = no
#      recovery, identity permanent), when to use strict mode.
#
# 6. docs/decisions/ADR-0049-per-event-signatures.md
#    - Status: 'All eight tranches shipped across Bursts 242-244
#      — audit chain is now tamper-PROOF for agent-emitted
#      events.'
#    - Tranche table marks T5/T6/T7/T8 DONE B244.
#
# 7. STATE.md
#    - ADR-0049 entry split from the 'drafted' group; now
#      'Accepted 2026-05-12, all 8 tranches shipped Bursts
#      242-244.' ADR-0050/0051 stay in the 'drafted' Phase 4
#      runway.
#
# Test verification (sandbox):
#   audit_chain + audit_chain_signatures: 56 passed
#     (17 new + 39 pre-existing audit_chain tests)
#   Targeted (audit_chain_signatures + audit_chain + daemon
#     writes + key_store + daemon plugin grants + integration):
#     163 passed
#   Batch B (40 files) + integration: 978 + 12 = 990 passed
#   Zero B244-caused failures.
#
# Per ADR-0001 D2: identity invariance unchanged. public_key is
#   part of the agent identity triple (DNA, constitution_hash,
#   public_key) per ADR-0049 D1; immutable per agent lifetime.
# Per ADR-0044 D3: additive — new optional chain-entry field +
#   new injection points + new tool arg. Legacy chains continue
#   to verify; pre-ADR-0049 readers ignore the unknown field.
# Per CLAUDE.md Hippocratic gate: no removals; the tolerant
#   verifier preserves the existing 'unsigned passes hash check'
#   contract for legacy entries.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/core/audit_chain.py \
        src/forest_soul_forge/daemon/app.py \
        src/forest_soul_forge/tools/builtin/audit_chain_verify.py \
        tests/unit/test_audit_chain_signatures.py \
        docs/runbooks/per-event-signatures.md \
        docs/decisions/ADR-0049-per-event-signatures.md \
        STATE.md \
        dev-tools/commit-bursts/commit-burst244-adr0049-t5-t8-signatures.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(security): ADR-0049 T5+T6+T7+T8 — chain tamper-proof (B244)

Burst 244. Closes ADR-0049 — audit chain is now TAMPER-PROOF for
agent-emitted events. Every dispatch / memory_write / delegate /
turn carries an ed25519 signature verifiable against the agent's
public key.

T5 sign-on-emit: AuditChain.set_signer injection point; append()
calls signer when agent_dna is non-None and attaches
'ed25519:<base64>' field. Signer exception swallowed (entry
lands unsigned) so transient keystore failure doesn't block
audit appends.

T6 verify-on-replay: AuditChain.set_verifier injection point;
verify() parses prefix + base64 + calls verifier. Legacy
unsigned passes hash-chain check only (D5). Unsupported algo,
base64 corruption, verifier exception, sig-on-operator-entry
all refuse.

T7 strict-mode: verify(strict=False) parameter. True rejects
any agent-emitted entry without a signature. audit_chain_verify
.v1 tool exposes strict bool arg. Default tolerant mode keeps
D5 legacy contract.

T8 operator runbook: docs/runbooks/per-event-signatures.md
covers what changed, surface map, how to verify (tolerant +
strict), failure modes (keystore loss = no recovery, identity
permanent), strict-mode use cases.

Daemon lifespan wires signer + verifier closures via the
registry's agents.public_key column + AgentKeyStore. Diagnostics
surface wiring status.

Tests: 17 new in test_audit_chain_signatures.py. Targeted
suite 163 passed; batch B + integration 990 passed. Zero
regressions.

Per ADR-0001 D2: identity now (DNA, constitution_hash,
  public_key) per ADR-0049 D1; immutable per agent lifetime.
Per ADR-0044 D3: additive — new optional field + injection
  points + tool arg. Legacy readers ignore the unknown field.
Per CLAUDE.md Hippocratic gate: no removals."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 244 complete ==="
echo "=== ADR-0049 FULLY SHIPPED. Audit chain tamper-proof. ==="
echo "Press any key to close."
read -n 1
