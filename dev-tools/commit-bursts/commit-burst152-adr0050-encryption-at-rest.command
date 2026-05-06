#!/bin/bash
# Burst 152 — T28 ADR for encryption at rest.
#
# Phase 4 item #4 of the security-hardening arc. The 2026-05-05
# outside security review flagged "no encryption at rest" as the
# BIGGEST PRACTICAL HOLE: audit chain, registry, memory, soul.md
# all plaintext on disk. Any process with read access can cat the
# entire agent history.
#
# ADR-0050 closes that with a mixed-encryption posture: clear
# envelope (seq, hash, signature) + encrypted payload (event_data,
# memory body, etc.). Lets ADR-0049's chain-verifier still work
# without master-key access. Pairs with ADR-0049 (integrity);
# together they close the audit-chain disclosure + forgery gaps.
#
# Pure design ADR; implementation is 7-9 bursts of follow-on work
# (8 tranches T1-T8 in the ADR). This commit is design-only.
#
# Decisions locked:
#   1. Three-tier classification: sensitive (encrypted), structural
#      (plaintext for verifier access), public (plaintext, intended
#      readable)
#   2. SQLCipher for registry.sqlite — drop-in encrypted SQLite,
#      whole-file AES-256-CBC
#   3. Per-event encryption for audit chain — JSONL line stays one
#      per event; envelope clear, event_data ciphertext via AES-
#      256-GCM. Append-only preserved.
#   4. Memory body + soul.md + constitution.yaml encrypted at write
#      (defense in depth: SQLCipher file-level + per-cell)
#   5. Master key in OS keychain (Secure Enclave on M-series Macs);
#      passphrase fallback (Argon2id) cross-platform; reuses ADR-
#      0049's KeyStore Protocol
#   6. Mixed legacy/encrypted chain — no rewrites. Pre-ADR-0050
#      plaintext entries stay plaintext; verifier handles both.
#      Operator can archive legacy chain + start fresh if concerned.
#   7. Schema is additive (kernel ABI compatible per ADR-0044 D3):
#      new optional encryption field + new schema columns. Registry
#      migration v16 → v17.
#
# What ships:
#
#   docs/decisions/ADR-0050-encryption-at-rest.md (~430 lines)
#     - 7 decisions + tradeoffs
#     - 8 implementation tranches with effort estimates
#     - Three-tier classification table
#     - Master-key option ranking
#     - Cross-references to ADR-0005/0006/0007/0022/0025/0027/0033/
#       0042/0043/0044/0049
#
# Implementation queued:
#   T1: KeyStore master-key extension
#   T2: sqlcipher3 integration
#   T3: per-event encryption in audit_chain.py
#   T4: memory body encryption
#   T5: soul + constitution file encryption
#   T6: operator UX (passphrase prompt + Keychain entry)
#   T7: migration runbook + key-backup workflow
#   T8: CLI fsf encrypt family (status / rotate-key / decrypt-event)
#
# Pairs with ADR-0049 (per-event signatures). ADR-0049 stops
# forgery; ADR-0050 stops disclosure. Both close the audit-chain-
# on-disk concern from the outside review.
#
# Closes T28 design phase. Phase 4 progress: T25 done (B148+B149),
# T26 done (B150), T27 done (B151), T28 done (B152). Remaining:
# T29 ADR per-tool sandbox.

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0050-encryption-at-rest.md \
        dev-tools/commit-bursts/commit-burst152-adr0050-encryption-at-rest.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "docs(adr): ADR-0050 — encryption at rest (B152)

Burst 152. Closes T28 design phase. Phase 4 item #4 from the
2026-05-05 outside security review: 'no encryption at rest = biggest
practical hole. Disk compromise = game over.'

ADR-0050 closes that with a mixed-encryption posture: clear
envelope (seq, hash, signature) + encrypted payload (event_data,
memory body, etc.). Pairs with ADR-0049 (per-event signatures);
ADR-0049 stops forgery, ADR-0050 stops disclosure.

7 decisions locked:
1. Three-tier classification: sensitive / structural / public
2. SQLCipher for registry.sqlite (drop-in AES-256-CBC)
3. Per-event encryption for audit chain (envelope clear,
   event_data ciphertext, append-only preserved)
4. Memory body + soul.md + constitution.yaml encrypted at write
   (defense in depth)
5. Master key in OS keychain (Secure Enclave on M-series);
   passphrase fallback via Argon2id; reuses ADR-0049 KeyStore
6. Mixed legacy/encrypted chain — no rewrites. Pre-ADR-0050
   entries stay plaintext.
7. Schema additive: encryption field + schema v16→v17

8 implementation tranches queued, 7-9 bursts total.

Pairs with ADR-0049 — together they close the audit-chain-on-disk
gap from the security review.

Phase 4 progress: T25 (B148+B149), T26 (B150), T27 (B151),
T28 (B152) — all done. Remaining: T29 per-tool sandbox ADR."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 152 commit + push complete ==="
echo "Press any key to close this window."
read -n 1
