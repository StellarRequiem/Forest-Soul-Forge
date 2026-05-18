#!/bin/bash
# Burst 377 - ADR-0064 T3: telemetry batch → audit chain hookup +
# `fsf telemetry verify <batch_id>` CLI.
#
# What lands:
#
#   src/forest_soul_forge/security/telemetry/ingestor.py (MOD)
#     AdapterIngestor.__init__ gains `audit_chain` + `chain_agent_dna`
#     optional parameters. flush_pending now, on a successful
#     store.ingest_batch, ALSO emits a `telemetry_batch_ingested`
#     audit chain entry with:
#       batch_id          - returned by ingest_batch
#       source            - adapter.SOURCE
#       event_count       - len(batch)
#       integrity_root    - sha256 over sorted concat of each event's
#                           integrity_hash (Merkle-like anchor)
#       first_timestamp   - min(batch.timestamps)
#       last_timestamp    - max(batch.timestamps)
#     Chain emission failure is non-fatal to the store insert; the
#     store transaction commits FIRST, then the chain append runs.
#     If chain append fails, the failure is recorded in
#     stats.last_error and the verify CLI surfaces the mid-flush-
#     crash window as CHAIN_ENTRY_MISSING. The store-first / anchor-
#     second order is intentional: anchor-without-data is worse
#     than data-without-anchor (chain entry referencing nonexistent
#     batch is harder to reason about than store batch missing its
#     anchor).
#
#     _compute_integrity_root() helper - hashes integrity_hashes,
#     not full event payloads. Sorting before concat makes the
#     root permutation-invariant within a batch.
#
#   src/forest_soul_forge/security/telemetry/verify.py (NEW)
#     `forest_soul_forge.security.telemetry.verify` module +
#     `main()` entry. Classifies one of:
#       OK                   - store + chain agree
#       MISMATCH             - integrity_root differs (real corruption)
#       CHAIN_ENTRY_MISSING  - store has batch, chain lacks anchor
#       BATCH_EMPTY          - batch_id not in store
#       STORE_UNAVAILABLE    - couldn't open the telemetry store
#     Exit codes (0/1/2/3/4 in that order) let operator scripts
#     branch on the verdict without reparsing the JSON.
#     Linear scan of the chain JSONL is sufficient for today's
#     chain size; ADR-0073 segment-aware scan can replace this
#     when the linear-scan budget is exceeded.
#     The integrity_root recompute is INTENTIONALLY duplicated
#     from ingestor (not imported) - the verifier's independence
#     from the writer is load-bearing. Drift in either formula
#     would surface as MISMATCH.
#
#   dev-tools/fsf-telemetry-verify.command (NEW)
#     Operator-facing wrapper. Pass batch_id; outputs JSON +
#     human summary line. Reads default paths
#     (data/telemetry.sqlite + examples/audit_chain.jsonl);
#     --telemetry-db and --chain-path override.
#
#   tests/unit/test_b377_telemetry_chain_hookup.py (NEW)
#     10 tests across 3 test classes:
#       TestChainEmission (4) - flush_pending emits the right shape
#         when audit_chain is supplied; no emission when None;
#         chain.append exception doesn't lose store data; agent_dna
#         passes through.
#       TestIntegrityRoot (2) - root invariant under event permutation;
#         root changes when event content changes.
#       TestVerify (4) - OK / BATCH_EMPTY / CHAIN_ENTRY_MISSING /
#         MISMATCH classifications via real AuditChain + real
#         SqliteTelemetryStore.
#     All 10 pass in 0.25s on the local box.
#
# What this UNBLOCKS:
#   D3 Phase B - telemetry_steward (T4) now has the chain hookup
#   it needs to record what got ingested. threat_intel_curator (T6)
#   gains the same hook for free. Phase B is unblocked end-to-end.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T3: telemetry events sit in the
#     store without an audit-chain anchor. Tampering is undetectable;
#     no proof of when a batch was ingested or by which source.
#     D3 Phase B can't ship because telemetry_steward has nothing
#     to operate against.
#   Prove non-load-bearing for the additions:
#     - audit_chain parameter is OPTIONAL with default None.
#       T2 tests + legacy callers keep working unchanged.
#     - integrity_root formula matches the canonical-form
#       discipline (sorted-before-hash for permutation invariance).
#     - verify is read-only; never mutates store or chain.
#   Prove alternative is strictly better:
#     - Skip the chain hookup entirely: D3 Phase B blocked
#       indefinitely.
#     - Anchor-before-store (reverse order): anchor-without-data
#       corruption is harder to recover from than data-without-
#       anchor (which the verify CLI surfaces precisely).
#     - Per-event chain anchoring instead of per-batch: 100x the
#       chain growth rate; ADR-0064 D5 explicitly rejected this.
#
# CLAUDE.md sec2 (B350 wiring discipline) check:
#   This commit does NOT add a new dispatcher-owned ToolContext
#   subsystem. The chain hookup is via the ingestor's own
#   constructor param, not via ToolContext. So no typed field /
#   dispatcher wire / section-06 probe is needed.
#
# CLAUDE.md sec3 (bare version strings) check:
#   No new tool registrations. Not applicable to this commit.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest tests/unit/test_b377_telemetry_chain_hookup.py
#      Expected: 10 passed.
#   2. force-restart-daemon (when an adapter is actually wired into
#      the daemon's lifespan, telemetry_batch_ingested events start
#      appearing in examples/audit_chain.jsonl).
#   3. dev-tools/fsf-telemetry-verify.command <batch_id> verifies
#      any batch the ingestor recorded.
#   4. diagnostic-all section-08 - the new telemetry_batch_ingested
#      event type appears as a known-event (no unknown_event_types
#      noise).

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/telemetry/ingestor.py \
        src/forest_soul_forge/security/telemetry/verify.py \
        dev-tools/fsf-telemetry-verify.command \
        tests/unit/test_b377_telemetry_chain_hookup.py \
        dev-tools/commit-bursts/commit-burst377-adr0064-t3-telemetry-chain-hookup.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(telemetry): ADR-0064 T3 chain hookup + verify CLI (B377)

Burst 377. Telemetry batch ingestion now anchors in the audit
chain. Unblocks D3 Phase B (telemetry_steward).

ingestor.py (MOD):
  AdapterIngestor gains optional audit_chain + chain_agent_dna
  parameters. flush_pending, after successful store.ingest_batch,
  emits a telemetry_batch_ingested chain entry with batch_id,
  source, event_count, integrity_root (Merkle-like sha256 over
  sorted event integrity_hashes), and first/last_timestamp.
  Chain emission failure is non-fatal - store commits first;
  failures surface via stats.last_error and verify CLI.

verify.py (NEW):
  forest_soul_forge.security.telemetry.verify module classifies:
    OK | MISMATCH | CHAIN_ENTRY_MISSING | BATCH_EMPTY |
    STORE_UNAVAILABLE
  Recompute formula is INTENTIONALLY duplicated from ingestor -
  writer/verifier independence is load-bearing. Exit codes
  0/1/2/3/4 let operator scripts branch on verdict.

dev-tools/fsf-telemetry-verify.command (NEW):
  Operator-facing wrapper; reads default paths
  (data/telemetry.sqlite + examples/audit_chain.jsonl).

tests/unit/test_b377_telemetry_chain_hookup.py (NEW):
  10 tests across emission / root / verify surfaces. All pass.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: D3 Phase B blocked without this; telemetry has no
    tamper evidence.
  Prove non-load-bearing: audit_chain param optional (legacy
    callers unchanged); verify is read-only.
  Prove alternative is better: anchor-before-store would create
    harder corruption mode; per-event chain anchoring blows up
    chain growth 100x (ADR-0064 D5 rejected).

After this lands:
  Telemetry batch ingestion produces tamper-evident records.
  D3 Phase B (telemetry_steward + threat_intel_curator) can
  proceed."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 377 complete - ADR-0064 T3 chain hookup ==="
echo "=========================================================="
echo "Re-test:"
echo "  PYTHONPATH=src python3 -m pytest tests/unit/test_b377_telemetry_chain_hookup.py"
echo "Expected: 10 passed"
echo ""
echo "Press any key to close."
read -n 1 || true
