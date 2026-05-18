#!/bin/bash
# Burst 379 - ADR-0064 T4: telemetry_steward role + signature
# skill + birth script + handoffs wiring.
#
# What lands:
#
#   config/trait_tree.yaml (MOD)
#     Adds telemetry_steward role under D3 Phase B section.
#     Domain weights emphasize audit (2.6) - verify-and-summarize
#     is audit work. emotional=0.4 (schema floor).
#
#   config/genres.yaml (MOD)
#     telemetry_steward joins the guardian genre alongside
#     forensic_archivist (sibling D3 Phase A/B guardians).
#     Inline NOTE captures why the kit fits guardian's read_only
#     ceiling without exception.
#
#   config/constitution_templates.yaml (MOD)
#     Full template block - policies, risk_thresholds, out_of_scope,
#     operator_duties, drift_monitoring. Four policies:
#       forbid_event_analysis - lane discipline (anomaly_ace's job)
#       forbid_response_action - lane discipline (response_rogue)
#       require_batch_anchor_verification - audit_chain_verify gate
#       forbid_silent_pipeline_pause - no covert throttling
#
#   config/tool_catalog.yaml (MOD)
#     New archetype kit: llm_think + memory_recall + memory_write +
#     delegate + audit_chain_verify + text_summarize. No file_integrity
#     (batch metadata is in SQLite, not files). No shell_exec or
#     code_edit. Strict read_only.
#
#   config/handoffs.yaml (MOD)
#     Maps (d3_local_soc, telemetry_oversight) -> telemetry_steward_brief.v1.
#
#   config/domains/d3_local_soc.yaml (MOD)
#     Adds telemetry_steward to entry_agents and
#     telemetry_oversight to capabilities. Inline note
#     distinguishes from anomaly_ace's anomaly_detection
#     (steward=batch metadata; anomaly_ace=event content).
#
#   examples/skills/telemetry_steward_brief.v1.yaml (NEW)
#     Signature skill: prior_briefs -> verify_chain_integrity ->
#     summarize_batches (llm_think classify) -> write_brief.
#     Inputs: recent_batches (list of chain anchor metadata),
#     window_label, operator_focus. Output: structured markdown
#     brief with per-source summary + flags (volume_drop /
#     missing_source / freshness_stall / unverified_anchor) +
#     observation-only recommendation. Never instructs.
#
#   dev-tools/birth-telemetry-steward.command (NEW)
#     4-phase birth: kickstart -> /birth POST -> constitution
#     parse check -> posture set GREEN. Mirrors birth-forensic-
#     archivist.command. Idempotent.
#
#   tests/unit/test_b379_telemetry_steward_wiring.py (NEW)
#     8 tests: trait_tree, genre membership, constitution template
#     blocks, tool kit (positive + forbidden checks), handoff
#     routing, domain manifest, skill manifest, section-01-shape
#     static-config check. All 8 pass.
#
# What this UNBLOCKS:
#   D3 Phase B now has its operator-facing role. With B377's chain
#   hookup giving telemetry batches a tamper-evident anchor, and
#   B379's steward able to summarize that anchor stream, the SOC
#   operator can finally see "is telemetry healthy right now?"
#   from the substrate side. Phase B's second role
#   (threat_intel_curator) ships in T6 alongside the micro-batching
#   layer.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm of NOT shipping T4: T3's chain anchors exist but
#     have no operator-readable summary surface. The verify CLI
#     answers 'is THIS batch tampered?'; the steward answers 'is
#     the pipeline healthy in aggregate?' - different question,
#     daily-operator scope, no other role covers it.
#   Prove non-load-bearing:
#     - Pure additive across configs. Other roles, genres,
#       capabilities, handoffs all unchanged.
#     - Skill is operator-driven (not scheduled); zero load
#       impact until the operator dispatches it.
#     - Birth script is idempotent.
#   Prove alternative is strictly better:
#     - Skip the role: T3's anchors stay opaque to the operator
#       except via raw chain reads.
#     - Bundle into anomaly_ace: violates lane separation. Steward
#       reviews metadata only; anomaly_ace reviews content.
#     - Bundle into forensic_archivist: archivist's chain-of-
#       custody discipline is per-artifact; steward's is per-
#       batch-anchor. Different audit grains.
#
# CLAUDE.md sec2 + sec3 check:
#   No new dispatcher-owned subsystem. No new builtin tool with
#   _VERSION. Wiring is config-only + skill manifest + birth
#   script. sec2/sec3 don't apply.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest tests/unit/test_b379_telemetry_steward_wiring.py
#      Expected: 8 passed.
#   2. force-restart-daemon (loads new role into trait_engine).
#   3. dev-tools/birth-telemetry-steward.command (mints
#      TelemetryStreward-D3 instance).
#   4. dev-tools/diagnostic/diagnostic-all.command
#      Expected: section-01 still PASS (new role validates);
#                section-04 still PASS (catalog unchanged);
#                section-05 picks up the new agent as PASS
#                after birth runs;
#                section-09 PASS (new handoff entry resolves).

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
        examples/skills/telemetry_steward_brief.v1.yaml \
        dev-tools/birth-telemetry-steward.command \
        tests/unit/test_b379_telemetry_steward_wiring.py \
        dev-tools/commit-bursts/commit-burst379-adr0064-t4-telemetry-steward.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d3): ADR-0064 T4 telemetry_steward role (B379)

Burst 379. Closes D3 Phase B's first role. Operator can now
dispatch telemetry_steward_brief.v1 to get a metadata-level
health summary of recent batch ingestion.

Wiring (config-only + skill manifest):
  trait_tree.yaml - telemetry_steward role, audit-weighted (2.6).
  genres.yaml - joins guardian alongside forensic_archivist.
  constitution_templates.yaml - 4 policies enforce lane
    discipline (no event analysis, no response actions, require
    chain verify, no silent pipeline pause).
  tool_catalog.yaml - archetype kit (audit_chain_verify +
    memory_recall + memory_write + delegate + llm_think +
    text_summarize). Strict read_only.
  handoffs.yaml - (d3_local_soc, telemetry_oversight) ->
    telemetry_steward_brief.v1.
  domains/d3_local_soc.yaml - entry_agent + capability.

Skill (examples/skills/telemetry_steward_brief.v1.yaml):
  prior_briefs -> verify_chain_integrity (REQUIRED by
  constitution policy) -> summarize_batches (llm_think
  classify; produces structured markdown brief with per-source
  summary + flags + observation-only recommendation) ->
  write_brief.

Birth (dev-tools/birth-telemetry-steward.command):
  4-phase mirror of birth-forensic-archivist. Idempotent.

Tests (tests/unit/test_b379_telemetry_steward_wiring.py):
  8 tests across trait_tree / genre / template / kit / handoff
  / domain / skill / section-01 shape. All 8 pass.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: T3 anchors are opaque to operator without this;
    no other role covers per-batch-anchor health summaries.
  Prove non-load-bearing: pure additive; other roles + skills
    + capabilities unchanged.
  Prove alternative is better: bundling into anomaly_ace
    (event-content lane) or forensic_archivist (per-artifact
    lane) would dilute audit attribution.

After this lands + force-restart + birth:
  TelemetryStreward-D3 alive, posture green.
  D3 Phase B second role (threat_intel_curator) is T6 territory."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 379 complete - telemetry_steward role ==="
echo "=========================================================="
echo "Next:"
echo "  dev-tools/force-restart-daemon.command"
echo "  dev-tools/birth-telemetry-steward.command"
echo "  dev-tools/diagnostic/diagnostic-all.command"
echo ""
echo "Press any key to close."
read -n 1 || true
