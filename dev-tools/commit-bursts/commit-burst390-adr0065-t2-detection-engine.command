#!/bin/bash
# Burst 390 - ADR-0065 T2: DetectionEngine + AdapterIngestor wiring.
#
# Extends D3 Phase C arc. T1 (B389) shipped the parser; this burst
# wires the engine that consumes parsed rules + scans each telemetry
# batch + emits detection_fired audit events. T3 adds the
# detection_engineer role; T4 wires the harness; T5 ships runbook
# + starter rules; T6 closes.
#
# What lands:
#
#   src/forest_soul_forge/security/detection/engine.py (NEW)
#     DetectionEngine - holds rule set; ready() gate; scan() runs
#       rules against a batch + emits detection_fired chain events.
#     Per ADR-0065 D7: engine refuses to scan when load_errors is
#       non-empty. The lifespan caller MUST check ready() before
#       wiring into the ingestor.
#     scan() collapses N event matches per (rule, batch) into ONE
#       detection_fired chain event with matched_event_ids list.
#       Chain growth stays proportional to firing rules, not firing
#       events.
#     reload_from_dir() atomically swaps the rule set under
#       self._lock. ANY parse failure -> retain previous rule set
#       + record failures in load_errors. Silent fall-through is
#       the wrong default; operator must repair.
#     DetectionScanResult dataclass for test inspection +
#       harness consumption.
#
#   src/forest_soul_forge/security/detection/__init__.py (MOD)
#     Re-exports DetectionEngine + DetectionScanResult.
#
#   src/forest_soul_forge/security/telemetry/ingestor.py (MOD)
#     AdapterIngestor.__init__ gains optional detection_engine
#       parameter. Default None so legacy callers (no rules) work.
#     flush_pending(), after the telemetry_batch_ingested anchor
#       lands, calls detection_engine.scan(batch_id, batch,
#       audit_chain=, agent_dna=). Engine failure is non-fatal
#       (store-first/anchor-second posture inherited from B377).
#       stats.last_error captures the failure for operator review.
#
#   tests/unit/test_b390_detection_engine.py (NEW)
#     12 tests across 3 classes:
#       TestEngineConstruction (4): pre-loaded rules, dir loading,
#         reload refusal on failure, empty dir valid.
#       TestScan (7): no rules -> empty; matching rule -> 1 event;
#         N matches collapse to 1 event; 2 rules each fire ->
#         2 events; logsource mismatch skipped; not-ready refuses;
#         scan without audit_chain still returns matches.
#       TestIngestorIntegration (3): flush calls engine after anchor;
#         no engine = no detection events; engine exception doesn't
#         kill ingest.
#     All 12 pass. Combined with B377+B389 = 45 tests pass across
#     the telemetry+detection surface.
#
# What this does NOT do:
#   - No daemon-side lifespan wiring of DetectionEngine yet.
#     T-future (likely T3 or T5) wires the engine into the daemon's
#     lifespan and reads from config/detection_rules/. The engine
#     class is operational now; production hookup is a separate
#     deliberate move.
#   - No POST /detections/reload endpoint. Same deferral.
#   - No detection_engineer role. T3.
#   - No harness extensions. T4.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T2: T1's parser produces rules but
#     nothing consumes them. Detection-as-code is paper-only without
#     the engine + batch hook.
#   Prove non-load-bearing:
#     - detection_engine param is optional. Existing AdapterIngestor
#       callers (B377 path, test stubs) keep working unchanged.
#     - Engine failure is non-fatal; store + telemetry anchor are
#       durable regardless.
#     - Engine refuses to scan when load_errors present (ADR-0065 D7)
#       so a broken rule set doesn't silently scan with the previous
#       version.
#   Prove alternative is strictly better:
#     - Per-event chain events: 100x the chain growth rate; collapses
#       to per-(rule,batch) here for the same reason ADR-0064 D5
#       rejected per-event audit anchors for telemetry.
#     - Sync scan: synchronous in-process keeps the contract simple;
#       async queue land later if rule-set size pushes past the
#       per-batch budget.
#
# CLAUDE.md sec2 + sec3 check:
#   No new dispatcher ToolContext subsystem. No new builtin tool
#   with _VERSION. The engine plumbs through AdapterIngestor's own
#   constructor param + the existing audit_chain handle.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest \
#        tests/unit/test_b390_detection_engine.py \
#        tests/unit/test_b389_detection_parser.py \
#        tests/unit/test_b377_telemetry_chain_hookup.py
#      Expected: 45 passed.
#   2. No daemon code change (engine isn't wired into lifespan yet)
#      so no restart needed for THIS commit.
#   3. T3 (detection_engineer role) is the next burst; the role's
#      birth + skill close the operator-facing surface.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/detection/__init__.py \
        src/forest_soul_forge/security/detection/engine.py \
        src/forest_soul_forge/security/telemetry/ingestor.py \
        tests/unit/test_b390_detection_engine.py \
        dev-tools/commit-bursts/commit-burst390-adr0065-t2-detection-engine.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(detection): ADR-0065 T2 DetectionEngine + ingest hook (B390)

Burst 390. D3 Phase C continues. Engine consumes parsed rules
(B389 parser) + scans each telemetry batch + emits detection_fired
audit chain events. 12 new tests pass; 45 total across the
detection+telemetry surface.

src/forest_soul_forge/security/detection/engine.py (NEW):
  DetectionEngine - rule set + ready() gate + scan().
    Per ADR-0065 D7: ready()=False when ANY rule fails to parse;
      scan() returns empty result rather than running an
      incomplete rule set.
    scan(batch_id, events, audit_chain, agent_dna) - rules-first
      loop with cheap logsource short-circuit; per-rule match
      collection; ONE detection_fired event per (rule, batch)
      collapsing N event matches into one anchor's
      matched_event_ids list (chain growth proportional to
      firing rules, not firing events).
    reload_from_dir() - atomic swap under self._lock. Parse
      failure retains previous rule set + records failures in
      load_errors. Silent fall-through forbidden.
  DetectionScanResult - inspection-friendly summary
    (rules_evaluated, events_scanned, matches_by_rule,
    audit_event_seqs, scan_ms).

src/forest_soul_forge/security/telemetry/ingestor.py (MOD):
  AdapterIngestor.__init__ gains optional detection_engine
    parameter (default None -> back-compat with all existing
    callers). flush_pending() invokes engine.scan() AFTER the
    telemetry_batch_ingested anchor lands. Engine failure is
    non-fatal (store-first/anchor-second posture inherited
    from B377) — stats.last_error captures it.

tests/unit/test_b390_detection_engine.py (NEW):
  TestEngineConstruction (4) - rules / dir / reload-refuse /
    empty-dir-valid.
  TestScan (7) - no rules / matching rule / N matches collapse
    to 1 / 2 rules each fire / logsource skip / not-ready
    refusal / no-chain inspection.
  TestIngestorIntegration (3) - flush invokes engine after
    anchor / no engine = no detection / engine exception is
    survived.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: T1's parser produces rules but nothing consumes
    them. D3 Phase C is paper-only without T2.
  Prove non-load-bearing: detection_engine param optional;
    engine failure non-fatal; not-ready refusal short-circuits.
  Prove alternative is better: per-event chain anchors blow
    chain growth 100x; sync scan keeps contract simple, async
    lands later if size demands.

After this lands: T3 (detection_engineer role) is the next
burst — wires the rule-authoring + review surface."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 390 complete - DetectionEngine + hook ==="
echo "=========================================================="
echo "Re-test:"
echo "  PYTHONPATH=src python3 -m pytest tests/unit/test_b390_detection_engine.py"
echo "Expected: 12 passed"
echo ""
echo "Press any key to close."
read -n 1 || true
