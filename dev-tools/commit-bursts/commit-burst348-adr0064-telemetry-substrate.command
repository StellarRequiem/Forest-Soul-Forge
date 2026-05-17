#!/bin/bash
# Burst 348 - ADR-0064 T1: telemetry pipeline substrate.
#
# Long burst per the operator's "long bursts where possible"
# direction. Packs the decision doc + complete event substrate
# + complete storage backend + retention policy + 64 tests into
# one commit. Sets up D3 Phase B (telemetry_steward) without
# shipping the role itself (that's T4).
#
# Substrate is intentionally complete-but-isolated: no daemon
# wiring yet. The store class can be instantiated standalone +
# exercised via tests; the daemon hookup lands in T3 alongside
# the audit chain batch-ingest emit.
#
# What ships (9 files):
#
# 1. docs/decisions/ADR-0064-telemetry-pipeline.md (NEW):
#    7 decisions documented:
#      D1 — Canonical TelemetryEvent shape (10 fields + integrity_hash)
#      D2 — Closed 8-type enum + open `source` extension
#      D3 — SQLite store at data/telemetry.sqlite (separate from
#           registry: different lifecycle, lock discipline,
#           encryption salt)
#      D4 — Three retention classes (ephemeral 7d / standard 90d /
#           security_relevant 365d) + classifier rule table
#      D5 — Batch-ingest audit anchor (one chain entry per N events
#           with integrity_root, NOT per-event audit — that would
#           re-inflate the chain we just designed to keep small)
#      D6 — Adapter subprocess sandbox via ADR-0051
#      D7 — telemetry_steward as guardian-genre read-only role
#           (ingests, tags, anchors; does NOT analyze / respond /
#           alert — those are existing Security Swarm roles)
#    Tranches: T1 this burst + T2 macos_unified_log adapter + T3
#    chain integration + T4 telemetry_steward role + T5 runbook +
#    T6 threat_intel_curator (CLOSES Phase B). ~6 bursts total.
#
# 2. src/forest_soul_forge/security/telemetry/__init__.py (NEW):
#    Public-surface re-exports.
#
# 3. src/forest_soul_forge/security/telemetry/events.py (NEW):
#    EVENT_TYPES + SEVERITIES + RETENTION_CLASSES enums.
#    TelemetryEvent frozen dataclass with __post_init__ validators.
#    canonical_form() — deterministic JSON serialization
#    (sort_keys=True, separators=(',',':'), ensure_ascii=False) so
#    external ingestors compute the same bytes the daemon does.
#    compute_integrity_hash() — sha256 hex of canonical_form.
#    Excludes event_id + ingested_at (server-assigned).
#
# 4. src/forest_soul_forge/security/telemetry/store.py (NEW):
#    TelemetryStore Protocol + SqliteTelemetryStore reference impl.
#    SQLITE_SCHEMA_V1 with 6 indexes including composite
#    (retention_class, timestamp) for sweep speed.
#    ingest() — re-verifies hash before insert.
#    ingest_batch() — atomic; rolls back on any bad hash; returns
#    batch_id (uuid4 hex) for the audit chain anchor.
#    query() — N filters AND-combined; capped at 10000.
#    query_by_correlation() / query_by_batch() — purpose-built
#    walks. query_by_batch sorts by event_id ASC (deterministic for
#    integrity_root recomputation in the verifier path).
#    retention_sweep() — per-class delete; returns {class: count}
#    so caller emits ONE audit event with totals.
#    count_by_retention_class() — operator-dashboard helper.
#    Per-instance threading.Lock; WAL journal mode; separate from
#    registry's app.state.write_lock.
#
# 5. src/forest_soul_forge/security/telemetry/retention.py (NEW):
#    DEFAULT_RETENTION_TTLS — ADR-0064 D4 table.
#    RetentionPolicy frozen dataclass with cutoff_for() that raises
#    KeyError on unknown class (loud fail; silent zero would delete
#    everything matching).
#    classify_retention() — 5-rule decision matrix in declared
#    order: critical→security_relevant; auth/policy→security_
#    relevant; process_spawn+info→ephemeral; log_line+info→
#    ephemeral; default→standard.
#
# 6. tests/unit/test_telemetry_events.py (NEW): 26 assertions.
#    Enum exactness pins (size + content); all 7 validator failure
#    paths; canonical_form determinism across dict-order
#    permutations + nested-dict reorderings + non-ASCII payload;
#    integrity_hash matches manual sha256; lowercase invariant;
#    field-change sensitivity for severity + payload +
#    retention_class.
#
# 7. tests/unit/test_telemetry_store.py (NEW): 19 assertions.
#    Lifecycle (schema applies on construction, close idempotent);
#    single-ingest happy/dup/bad-hash; batch atomicity (empty
#    refuses; partial fail rolls back entire batch); 6 filter
#    combinations; timestamp DESC ordering; limit cap; batch sort
#    determinism for verifier; count helper.
#
# 8. tests/unit/test_telemetry_retention.py (NEW): 19 assertions.
#    TTL table pins; cutoff math for each class; unknown class
#    raises; classifier rule-matrix (8 cases including the
#    severity-override-type cases that make Rule 1 land first);
#    sweep delete/preserve/idempotence semantics integration-style
#    against a real SqliteTelemetryStore.
#
# 9. dev-tools/commit-bursts/commit-burst348-adr0064-telemetry-substrate.command
#    THIS SCRIPT.
#
# Test results: 64/64 green on first run.
#
# What's NOT in this burst (deferred to T2-T6):
#   - Adapter substrate (subprocess + parser contract): T2
#   - macos_unified_log_adapter reference impl: T2
#   - Audit chain hookup (telemetry_batch_ingested emission): T3
#   - `fsf telemetry verify <batch_id>` CLI: T3
#   - telemetry_steward role + birth + skill + handoffs: T4
#   - Operator runbook: T5
#   - Micro-batching layer + threat_intel_curator (CLOSE Phase B): T6

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0064-telemetry-pipeline.md \
        src/forest_soul_forge/security/telemetry/__init__.py \
        src/forest_soul_forge/security/telemetry/events.py \
        src/forest_soul_forge/security/telemetry/store.py \
        src/forest_soul_forge/security/telemetry/retention.py \
        tests/unit/test_telemetry_events.py \
        tests/unit/test_telemetry_store.py \
        tests/unit/test_telemetry_retention.py \
        dev-tools/commit-bursts/commit-burst348-adr0064-telemetry-substrate.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(telemetry): ADR-0064 T1 - telemetry pipeline substrate (B348)

Burst 348. Long burst per operator direction. Substrate for the
continuous-ingest telemetry pipeline that D3 Phase B
(telemetry_steward) operates against. ADR + events + store +
retention + 64 tests in one commit.

docs/decisions/ADR-0064-telemetry-pipeline.md (NEW):
  7 decisions. TelemetryEvent shape; closed 8-type enum;
  separate SQLite store from registry; 3 retention classes;
  batch-ingest audit anchor (not per-event); adapter subprocess
  sandbox; telemetry_steward as guardian-genre read-only role.
  Tranches T1-T6 mapped; ~6 bursts to close Phase B.

src/forest_soul_forge/security/telemetry/events.py (NEW):
  TelemetryEvent frozen dataclass + 7-validator __post_init__.
  canonical_form() — deterministic JSON (sort_keys=True,
  ensure_ascii=False) so external ingestors compute the same
  bytes the daemon does. compute_integrity_hash() — sha256 hex.
  Both exclude event_id + ingested_at (server-assigned).

src/forest_soul_forge/security/telemetry/store.py (NEW):
  SqliteTelemetryStore reference impl. 6 indexes including
  composite (retention_class, timestamp) for sweep speed.
  ingest() re-verifies hash; ingest_batch() is atomic +
  returns uuid4 batch_id for the audit anchor; query() supports
  6 AND-combined filters capped at 10000; query_by_batch()
  sorts by event_id ASC for deterministic integrity_root
  recomputation; retention_sweep() returns {class: count} for
  one-audit-event-per-sweep emission. Per-instance threading.Lock;
  WAL mode; separate from registry write_lock.

src/forest_soul_forge/security/telemetry/retention.py (NEW):
  DEFAULT_RETENTION_TTLS pinned (7/90/365 days). RetentionPolicy
  with cutoff_for() that raises KeyError on unknown class.
  classify_retention() — 5-rule matrix in declared order:
  critical -> security_relevant; auth/policy -> security_relevant;
  process_spawn+info -> ephemeral; log_line+info -> ephemeral;
  default -> standard.

Tests (64/64 green on first run):
  test_telemetry_events.py:    26 assertions
  test_telemetry_store.py:     19 assertions
  test_telemetry_retention.py: 19 assertions

What's NOT here (deferred):
  T2 adapter substrate + macos_unified_log reference adapter
  T3 audit chain batch-ingest event emission + verify CLI
  T4 telemetry_steward role + birth + skill + handoffs
  T5 operator runbook
  T6 micro-batching + threat_intel_curator (CLOSES Phase B)"

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 348 complete - ADR-0064 T1 telemetry substrate shipped ==="
echo "Next: B349 - T2 adapter substrate + macos_unified_log reference adapter."
echo ""
echo "Press any key to close."
read -n 1 || true
