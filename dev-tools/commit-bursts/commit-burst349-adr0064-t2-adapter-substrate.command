#!/bin/bash
# Burst 349 - ADR-0064 T2: adapter substrate + macos_unified_log
# reference adapter.
#
# Long burst per operator direction. Packs the Adapter ABC +
# AdapterIngestor + allowlist loader + the reference adapter +
# allowlist config + 78 new tests into one commit. The T2 work
# completes the "telemetry can ingest from a real source" loop;
# T3 then wires audit chain emission per ADR-0064 D5.
#
# Also folds in live-test-d3-phase-a.command that's been sitting
# untracked at repo root since B347 (per the operator's punch
# list — the driver Alex runs after the D3 Phase A commit cycle
# to autonomously verify the birth + first dispatch).
#
# What ships (10 source/config + 5 tests + 1 driver + 1 commit script):
#
# 1. src/forest_soul_forge/security/telemetry/adapter.py (NEW):
#    Adapter ABC. Four contract members:
#      SOURCE (class attr)
#      command() -> argv
#      parse(line) -> TelemetryEvent | None  (MUST NOT raise)
#      retention_override(event) -> str | None  (optional)
#    Plus make_event() helper that auto-computes event_id +
#    integrity_hash so subclasses can't accidentally desync the hash
#    from the canonical_form.
#    __init_subclass__ enforces SOURCE attr at class creation
#    (concrete subclasses can opt out via _ABSTRACT=True for
#    intermediate base classes).
#
# 2. src/forest_soul_forge/security/telemetry/sources.py (NEW):
#    Allowlist loader for config/telemetry_sources.yaml.
#    SourceSpec + SourcesConfig frozen dataclasses. load_sources()
#    distinguishes HARD errors (raise AdapterError; file unusable)
#    from SOFT errors (return in list; operator gets full punch
#    list rather than crash-on-first). resolve_adapter_class()
#    walks "module:ClassName" paths + validates Adapter subclass.
#    instantiate_adapters() honors only_enabled by default + soft-
#    skips entries with mismatched SOURCE or constructor TypeErrors.
#
# 3. src/forest_soul_forge/security/telemetry/ingestor.py (NEW):
#    AdapterIngestor — drives one adapter into one store.
#    Subprocess lifecycle via subprocess.Popen + a worker thread
#    that drains stdout line-by-line. Batches into the store via
#    ingest_batch() at either batch_size (default 100) or
#    flush_interval_s (default 5s), whichever comes first.
#    inject_lines() test-only path bypasses subprocess for unit
#    testing the parser → store wiring without timing complexity.
#    Catches adapter parse() exceptions defensively (the contract
#    says MUST NOT raise; defensive catch means a misbehaving
#    adapter doesn't take down the ingestor).
#    Retention-override path rebuilds the event with a fresh
#    integrity_hash when the override produces a different class —
#    critical because the chain anchor includes retention_class in
#    its canonical form.
#
# 4. src/forest_soul_forge/security/telemetry/adapters/__init__.py
#    (NEW): package marker for reference adapters.
#
# 5. src/forest_soul_forge/security/telemetry/adapters/macos_unified_log.py
#    (NEW): MacosUnifiedLogAdapter — drives
#    `log stream --style ndjson --predicate ...`. Default predicate
#    covers auth + xprotect + Error/Fault messages. Parses ndjson
#    into TelemetryEvent with subsystem-aware event_type mapping
#    (securityd/authd/opendirectoryd → auth_event; xprotect →
#    policy_decision; everything else → log_line) and message_type
#    aware severity mapping (Error → warn; Fault → critical;
#    everything else → info). retention_override pins
#    auth_event + policy_decision to security_relevant so the
#    intent is in the chain rather than inferred from the central
#    classifier's defaults.
#
# 6. src/forest_soul_forge/security/telemetry/__init__.py (UPDATED):
#    Re-exports the new public surface (Adapter, AdapterIngestor,
#    sources loaders) so callers import from one place.
#
# 7. config/telemetry_sources.yaml (NEW):
#    Allowlist. Ships with macos_unified_log entry at enabled: false
#    so the operator has to flip the bit consciously after reading
#    the predicate.
#
# 8. tests/unit/test_telemetry_adapter.py (NEW): 10 assertions on
#    ABC enforcement + make_event helper + retention_override default.
#
# 9. tests/unit/test_telemetry_sources.py (NEW): 23 assertions on
#    load_sources hard/soft errors + resolve_adapter_class
#    validation + instantiate_adapters happy/error paths + the
#    real config/telemetry_sources.yaml smoke-loads cleanly.
#
# 10. tests/unit/test_macos_unified_log_adapter.py (NEW): 36
#     assertions on construction + parse edge cases (empty,
#     non-JSON, malformed JSON, non-dict, missing timestamp) +
#     severity mapping (parametrized × 7) + event_type mapping
#     (parametrized × 8) + correlation_id handling +
#     retention_override.
#
# 11. tests/unit/test_telemetry_ingestor.py (NEW): 9 assertions on
#     construction validation + inject_lines + flush + auto-flush
#     at batch_size + parse-exception defense + retention override
#     rebuilds event with fresh hash + classifier-default fall-
#     through.
#
# 12. live-test-d3-phase-a.command (NEW, from B347 era; folded in
#     here): autonomous smoke driver for D3 Phase A. Operator runs
#     this AFTER B343-B347 commits + force-restart-daemon +
#     birth-d3-phase-a.command. Verifies: skill install, test
#     artifact creation, archive_evidence.v1 dispatch, ATTEST
#     verdict, memory entry, audit chain tail.
#
# Test results: 142/142 green (78 new + 64 regression).
#
# Bug-fix note: the test_telemetry_sources.py tests originally
# used "tests.unit.test_telemetry_sources:FixtureAdapterAlpha" as
# the dotted import path; pytest imports test files as top-level
# modules (no tests/__init__.py exists in this repo) so
# importlib.import_module returned a SECOND class object distinct
# from the in-file FixtureAdapterAlpha and isinstance/is checks
# failed. Fix: drop the tests.unit. prefix to match pytests
# actual import name. Test-only change; production loader is
# correct.
#
# What's NOT in this burst (deferred to T3-T6):
#   T3 — audit chain telemetry_batch_ingested emission +
#        `fsf telemetry verify <batch_id>` CLI
#   T4 — telemetry_steward role + birth script + skill +
#        handoffs wiring
#   T5 — operator runbook
#   T6 — micro-batching layer + threat_intel_curator (CLOSES Phase B)

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/security/telemetry/adapter.py \
        src/forest_soul_forge/security/telemetry/sources.py \
        src/forest_soul_forge/security/telemetry/ingestor.py \
        src/forest_soul_forge/security/telemetry/__init__.py \
        src/forest_soul_forge/security/telemetry/adapters/__init__.py \
        src/forest_soul_forge/security/telemetry/adapters/macos_unified_log.py \
        config/telemetry_sources.yaml \
        tests/unit/test_telemetry_adapter.py \
        tests/unit/test_telemetry_sources.py \
        tests/unit/test_macos_unified_log_adapter.py \
        tests/unit/test_telemetry_ingestor.py \
        live-test-d3-phase-a.command \
        dev-tools/commit-bursts/commit-burst349-adr0064-t2-adapter-substrate.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(telemetry): ADR-0064 T2 - adapter substrate + macos_unified_log (B349)

Burst 349. Long burst. Adapter ABC + AdapterIngestor + allowlist
loader + macos_unified_log reference adapter + 78 new tests in
one commit. Also folds in live-test-d3-phase-a.command (the
autonomous smoke driver for D3 Phase A that was uncommitted
since B347).

src/forest_soul_forge/security/telemetry/adapter.py (NEW):
  Adapter ABC. SOURCE class attr + command() + parse() +
  optional retention_override(). make_event() helper auto-
  computes event_id + integrity_hash so subclasses cant
  accidentally desync the hash from canonical_form.
  __init_subclass__ enforces SOURCE at class creation.

sources.py (NEW):
  Allowlist loader. Distinguishes HARD errors (raise; file
  unusable) from SOFT errors (return in list; operator gets
  full punch list). resolve_adapter_class walks module:ClassName
  paths + validates Adapter subclass. instantiate_adapters
  soft-skips SOURCE mismatch + constructor TypeError.

ingestor.py (NEW):
  AdapterIngestor drives one adapter into one store via Popen +
  a worker thread. Batches at batch_size (100) or
  flush_interval_s (5s). inject_lines() test path bypasses
  subprocess. Catches parse exceptions defensively. Retention
  override rebuilds the event with a fresh integrity_hash when
  the class changes (critical: chain anchor includes
  retention_class in canonical form).

adapters/macos_unified_log.py (NEW):
  Drives \`log stream --style ndjson --predicate ...\`. Default
  predicate covers auth + xprotect + Error/Fault. Subsystem-aware
  event_type + message_type-aware severity mappings.
  retention_override pins auth_event + policy_decision to
  security_relevant so adapter intent is in the chain.

config/telemetry_sources.yaml (NEW):
  Allowlist. macos_unified_log ships at enabled: false so the
  operator has to flip the bit consciously.

Tests (142/142 green = 78 new + 64 regression):
  test_telemetry_adapter.py            10
  test_telemetry_sources.py            23
  test_macos_unified_log_adapter.py    36
  test_telemetry_ingestor.py            9

Bug-fix note: test_telemetry_sources.py originally used
\"tests.unit.test_telemetry_sources:FixtureAdapterAlpha\" paths;
pytest imports test files as top-level modules (no tests/
__init__.py here) so importlib returned a SECOND class object
distinct from the in-file class + isinstance failed. Fix: drop
tests.unit. prefix to match pytests actual import name.
Test-only change; production loader is correct.

live-test-d3-phase-a.command (NEW; uncommitted since B347):
  Autonomous smoke driver. Runs AFTER B343-B347 + daemon restart
  + birth. Verifies skill install + test artifact creation +
  archive_evidence.v1 dispatch + ATTEST verdict + memory entry
  + audit chain tail.

Deferred: T3 (audit chain telemetry_batch_ingested + verify CLI),
T4 (telemetry_steward role), T5 (runbook), T6 (micro-batching +
threat_intel_curator; CLOSES Phase B)."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 349 complete - ADR-0064 T2 adapter substrate shipped ==="
echo "Next: B350 - T3 audit chain hookup + verify CLI."
echo ""
echo "Press any key to close."
read -n 1 || true
