#!/bin/bash
# Burst 385 - ADR-0064 T6: threat_intel_curator role +
# micro-batching contract. CLOSES ADR-0064 + D3 Phase B.
#
# What lands:
#
#   config/trait_tree.yaml (MOD)
#     threat_intel_curator role next to telemetry_steward.
#     Security-weighted (2.4) + audit-weighted (2.2).
#
#   config/genres.yaml (MOD)
#     Joins guardian alongside telemetry_steward.
#
#   config/constitution_templates.yaml (MOD)
#     Full template block. Four policies enforce:
#       forbid_runtime_event_analysis - lane discipline
#         (anomaly_ace's job)
#       forbid_response_action - lane discipline (response_rogue)
#       require_provenance_attestation - source URL + retrieval
#         timestamp + signature hash mandatory on every cache write
#       forbid_silent_feed_substitution - operator owns allowlist
#
#   config/tool_catalog.yaml (MOD)
#     Archetype kit: web_fetch (gated by per-agent allowlist),
#     file_integrity (verify downloaded artifacts), memory_recall,
#     memory_write, delegate, audit_chain_verify, llm_think,
#     text_summarize. No shell_exec / code_edit / browser_action.
#
#   config/handoffs.yaml (MOD)
#     (d3_local_soc, threat_intel_curation) -> threat_intel_refresh.v1.
#
#   config/domains/d3_local_soc.yaml (MOD)
#     entry_agents adds threat_intel_curator;
#     capabilities adds threat_intel_curation.
#
#   examples/skills/threat_intel_refresh.v1.yaml (NEW)
#     Signature skill: prior_intel -> verify_chain_integrity ->
#     fetch_feed (web_fetch) -> verify_artifact (file_integrity) ->
#     summarize_intel (text_summarize) -> write_intel.
#
#   dev-tools/birth-threat-intel-curator.command (NEW)
#     4-phase birth mirror. Posture: YELLOW (external reach
#     gated until operator configures source allowlist).
#
#   src/forest_soul_forge/security/telemetry/adapter.py (MOD)
#     New Adapter.parse_many(line) -> list[TelemetryEvent].
#     Default implementation delegates to parse() so existing
#     adapters keep working. Concrete adapters override only
#     when source format genuinely emits multiple events per
#     stdout line.
#
#   src/forest_soul_forge/security/telemetry/ingestor.py (MOD)
#     _handle_line now calls parse_many (when present) with a
#     getattr fallback to single-event parse() for duck-typed
#     adapters that don't subclass Adapter. The per-event
#     retention + rebuild + append logic moved into a new
#     _handle_event helper so micro-batched results all flow
#     through the same path.
#
#   tests/unit/test_b385_threat_intel_curator_wiring.py (NEW)
#     8 wiring tests across trait_tree / genre / template /
#     kit / handoff / domain / skill manifest / static-config
#     shape. All 8 pass.
#
#   docs/decisions/ADR-0064-telemetry-pipeline.md (MOD)
#     Adds "Status update (2026-05-18) — Accepted. Closed in B385"
#     section. Names all 6 tranches + their commit SHAs.
#     Documents D3 Phase B as closed; Phases C + D remain
#     (ADR-0065 + ADR-0066 not yet drafted).
#
# What this UNBLOCKS / CLOSES:
#   ADR-0064 closed.
#   D3 Phase B closed (telemetry_steward + threat_intel_curator
#   both alive).
#   D3 Phase C/D are ADR-0065 + ADR-0066 (not drafted yet); the
#   ten-domain dependency order continues D8 -> D1 -> D2 -> D7
#   -> D9 -> D10 -> D5 -> D6 after D3 Phase C/D ship.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T6: telemetry_steward observes
#     the local pipeline but the SOC has no automated path to
#     ingest external intel feeds. Operator does it manually
#     today (curl + grep + memory.write).
#   Prove non-load-bearing:
#     - Additive role + skill + kit. No existing config
#       behavior changes.
#     - parse_many's default implementation delegates to
#       parse(), so existing single-event adapters keep working
#       unchanged. The getattr fallback in the ingestor handles
#       duck-typed adapters (test stubs).
#     - Per-tool constraints enforced at birth time + the
#       require_provenance_attestation policy block stops the
#       curator from polluting the cache with un-sourced rows.
#   Prove alternative is strictly better:
#     - Bundling intel curation into anomaly_ace: violates lane
#       separation (anomaly_ace looks at runtime events).
#     - Operator-script-only: works but accumulates ad-hoc
#       state outside the audit chain. The curator's chain-
#       backed memory entries give the operator queryable history.
#
# CLAUDE.md sec2 + sec3 check:
#   No new dispatcher subsystem. No new builtin tool with
#   _VERSION. parse_many is on the Adapter Protocol, not a
#   ToolContext attribute. sec2/sec3 don't apply.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest \
#        tests/unit/test_b385_threat_intel_curator_wiring.py \
#        tests/unit/test_b377_telemetry_chain_hookup.py \
#        tests/unit/test_b379_telemetry_steward_wiring.py
#      Expected: 26 passed.
#   2. force-restart-daemon (loads new role + parse_many path).
#   3. dev-tools/birth-threat-intel-curator.command - mints
#      ThreatIntelCurator-D3 instance.
#   4. dev-tools/diagnostic/diagnostic-all.command - all 14
#      sections + the new agent visible in section-05.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add config/trait_tree.yaml \
        config/genres.yaml \
        config/constitution_templates.yaml \
        config/tool_catalog.yaml \
        config/handoffs.yaml \
        config/domains/d3_local_soc.yaml \
        examples/skills/threat_intel_refresh.v1.yaml \
        dev-tools/birth-threat-intel-curator.command \
        src/forest_soul_forge/security/telemetry/adapter.py \
        src/forest_soul_forge/security/telemetry/ingestor.py \
        tests/unit/test_b385_threat_intel_curator_wiring.py \
        docs/decisions/ADR-0064-telemetry-pipeline.md \
        dev-tools/commit-bursts/commit-burst385-adr0064-t6-threat-intel-curator-CLOSE.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d3): ADR-0064 T6 threat_intel_curator + micro-batching CLOSE (B385)

Burst 385. CLOSES ADR-0064. D3 Phase B closes alongside.

Two artifacts:

threat_intel_curator role (full wiring across trait_tree,
genres, constitution_templates, tool_catalog, handoffs,
d3_local_soc + signature skill + birth script + tests):
  Guardian genre, read-only ceiling. Pulls operator-configured
  intel feeds, verifies provenance, tags freshness, writes to
  local intel cache. NEVER analyzes runtime events (anomaly_ace
  lane) NEVER acts on findings (response_rogue lane). Four
  constitution policies enforce: forbid_runtime_event_analysis,
  forbid_response_action, require_provenance_attestation (every
  cache write has source URL + retrieval timestamp + signature),
  forbid_silent_feed_substitution (operator owns allowlist).
  Posture YELLOW at birth (external reach gated until allowlist
  configured).

Micro-batching contract (Adapter.parse_many):
  New Adapter.parse_many(line) -> list[TelemetryEvent]. Default
  implementation delegates to parse() so existing adapters keep
  working unchanged. High-frequency adapters can override to
  emit multiple events per stdout line. Ingestor._handle_line
  uses getattr fallback for duck-typed adapters (test stubs
  without parse_many).

Wiring tests: 8 new in B385 + 18 existing (B377+B379) all pass.
Total 26 passed across the telemetry surface.

ADR-0064 status: Accepted. Closed in B385. T1-T6 named in the
ADR's Status update block.

D3 Phase B closed. Phase C (detection_engineer, ADR-0065) +
Phase D (playbook_pilot + purple_pete, ADR-0066) are next, but
neither ADR is drafted yet. Ten-domain dependency order
continues D8 -> D1 -> D2 -> D7 -> D9 -> D10 -> D5 -> D6 after
D3 Phase C/D ship.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: SOC has no automated external-intel ingest path
    without the curator.
  Prove non-load-bearing: additive; parse_many defaults to
    parse(); the ingestor fallback handles duck-typed stubs.
  Prove alternative is better: bundling into anomaly_ace
    violates lane discipline; operator-script-only loses
    chain-backed queryable history.

After this lands + restart + birth: ThreatIntelCurator-D3
alive YELLOW; ADR-0064 closed; D3 Phase B closed."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 385 complete - ADR-0064 CLOSED ==="
echo "=========================================================="
echo "Next:"
echo "  dev-tools/force-restart-daemon.command"
echo "  dev-tools/birth-threat-intel-curator.command"
echo "  dev-tools/diagnostic/diagnostic-all.command"
echo ""
echo "Press any key to close."
read -n 1 || true
