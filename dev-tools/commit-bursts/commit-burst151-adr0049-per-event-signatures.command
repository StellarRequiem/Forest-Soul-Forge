#!/bin/bash
# Burst 151 — T27 ADR for per-event digital signatures.
#
# Phase 4 item #3 of the security-hardening arc. The 2026-05-05
# outside security review flagged: "audit chain is tamper-evident,
# not tamper-proof. Hash chain proves integrity if intact; root
# can rewrite + replay events. No per-event signatures."
#
# This ADR (ADR-0049) closes that by specifying ed25519 per-event
# signatures with the agent's private key. Schema is additive
# (kernel-compatible), uses existing primitives where possible,
# and keeps Forest's "agent identity is permanent" model intact.
#
# Pure design ADR; implementation is 6-8 bursts of follow-on work
# (8 tranches T1-T8 in the ADR). This commit is design-only.
#
# Decisions locked:
#   1. ed25519 keypair per agent, generated at birth (alongside DNA)
#   2. KeyStore Protocol abstraction; SoulUX default = macOS Keychain;
#      cross-platform fallback = encrypted SQLite via cryptography.fernet
#   3. Sign every event with agent_dna != None (operator-emitted
#      events stay unsigned)
#   4. Signature is OVER entry_hash, stored as SEPARATE field with
#      "ed25519:" algorithm prefix for future PQC migration
#   5. Verifier treats legacy (pre-ADR-0049) entries as "unsigned",
#      hash-chain-only verifiable; do NOT re-sign legacy entries
#   6. Schema is additive: new optional `signature` field on entries +
#      new `agents.public_key` column (registry v15 → v16). Per
#      ADR-0044 Decision 3 — additive schema migrations are kernel-
#      compatible.
#
# What ships:
#
#   docs/decisions/ADR-0049-per-event-signatures.md (~340 lines)
#     - 6 decisions + tradeoffs
#     - 8 implementation tranches with effort estimates
#     - Cross-references to ADR-0002/0005/0007/0025/0044/0050
#
# Implementation queued:
#   T1: KeyStore Protocol + memory_only backend
#   T2: encrypted_file backend (cryptography.fernet)
#   T3: keychain backend (macOS, via keyring lib)
#   T4: birth-time keypair generation + schema v15→v16
#   T5: sign-on-emit in core/audit_chain.py
#   T6: verifier extension (audit_chain_verify.v1)
#   T7: --strict mode flag for CLI verifier
#   T8: documentation + key-loss recovery runbook
#
# Pairs with ADR-0050 (encryption at rest) which addresses the
# read-side of the audit-chain-on-disk concern this ADR doesn't
# touch (ADR-0049 stops forgery; ADR-0050 stops disclosure).
#
# Closes T27 design phase. Phase 4 progress: T25 done (B148+B149),
# T26 done (B150), T27 done (B151). Remaining: T28 ADR
# (encryption at rest), T29 ADR (per-tool sandbox).

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0049-per-event-signatures.md \
        dev-tools/commit-bursts/commit-burst151-adr0049-per-event-signatures.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0049 — per-event digital signatures (B151)

Burst 151. Closes T27 design phase. Phase 4 item #3 from the
2026-05-05 outside security review: 'audit chain is tamper-evident,
not tamper-proof. Root can rewrite + replay events.'

ADR-0049 closes that by specifying ed25519 per-event signatures
with agent private keys. Schema is additive (kernel-ABI compatible
per ADR-0044 Decision 3). 'Agent identity is permanent' model
intact (no key rotation; archive + birth-successor for compromise
recovery).

6 decisions locked:
1. ed25519 keypair per agent, generated at birth alongside DNA
2. KeyStore Protocol; SoulUX default = macOS Keychain (Secure
   Enclave when available); fallback = encrypted SQLite via
   cryptography.fernet
3. Sign events where agent_dna != None; operator-emitted events
   stay unsigned (different substrate)
4. Signature OVER entry_hash, stored as SEPARATE field with
   'ed25519:' algorithm prefix (room for future PQC)
5. Legacy (pre-ADR-0049) entries treated as 'unsigned',
   hash-chain-only verifiable. Do NOT re-sign legacy entries.
6. Schema additive: new optional signature field + agents.public_key
   column (v15 → v16)

8 implementation tranches queued, 6-8 bursts total. Largest
implementation in the Phase 4 arc.

Pairs with ADR-0050 (encryption at rest) for read-side coverage.

Phase 4 progress: T25 done (B148+B149), T26 done (B150),
T27 done (B151). Remaining: T28 ADR encryption at rest, T29 ADR
per-tool sandbox."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 151 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
