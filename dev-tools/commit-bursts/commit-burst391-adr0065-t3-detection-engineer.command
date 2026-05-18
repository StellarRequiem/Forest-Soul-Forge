#!/bin/bash
# Burst 391 - ADR-0065 T3: detection_engineer role + signature
# skill + birth + wiring + tests.
#
# Mirrors B379 (telemetry_steward) + B385 (threat_intel_curator)
# role-wiring shape. Researcher genre (B386 pattern — kit needs
# web_fetch for ATT&CK reference reads, exceeds guardian's
# read_only ceiling; advisory stance enforced via constitution
# policies, not genre).
#
# What lands:
#
#   config/trait_tree.yaml (MOD)
#     detection_engineer role next to threat_intel_curator.
#     Cognitive-heavy (2.4) since rule synthesis is the
#     load-bearing skill. Security 2.4, audit 2.3.
#     Also fixes a stray duplicate domain_weights block on
#     threat_intel_curator that was left over from B385's edit
#     pattern (parser refused to load the file with the
#     duplicate; surfaced when test_b391 ran).
#
#   config/genres.yaml (MOD)
#     Joins researcher alongside threat_intel_curator. Inline
#     NOTE captures the B341/B386 pattern (kit needs network ->
#     genre with matching ceiling; policies enforce advisory).
#
#   config/constitution_templates.yaml (MOD)
#     Full template block. Five policies enforce lane discipline:
#       forbid_direct_rule_install - operator commits rules
#       forbid_engine_invocation - substrate runs rules
#       forbid_response_action - lane (response_rogue / Phase D)
#       require_attack_tag_in_proposals - ADR-0065 D3
#       require_evidence_in_proposal - no speculative rules
#
#   config/tool_catalog.yaml (MOD)
#     Archetype kit: llm_think + memory_recall + memory_write +
#     audit_chain_verify + delegate + text_summarize + web_fetch.
#     No code_edit / shell_exec / file_integrity. Read + propose
#     only; rule file commit is operator-driven.
#
#   config/handoffs.yaml (MOD)
#     (d3_local_soc, detection_authoring) -> propose_detection.v1.
#
#   config/domains/d3_local_soc.yaml (MOD)
#     entry_agents adds detection_engineer with
#     capability=detection_authoring. capabilities list adds
#     detection_authoring. Inline note distinguishes from
#     anomaly_ace (engineer authors detection content; ace
#     consumes detection_fired events for LLM follow-up).
#
#   examples/skills/propose_detection.v1.yaml (NEW)
#     Signature skill. Pipeline:
#       prior_proposals -> verify_chain_integrity ->
#       attack_reference (web_fetch) -> summarize_attack
#       (text_summarize) -> synthesize_rule (llm_think) ->
#       record_proposal (memory_write).
#     Inputs: technique (ATT&CK ID), focus (operator description),
#       attack_url (optional override), false_positive_examples
#       (optional).
#     Output: structured proposal with # CANDIDATE RULE / RATIONALE
#       / CONFIDENCE sections. Operator reads + commits the rule
#       file when accepted. Synthesizer prompt enforces the v1
#       Sigma subset (equality only, mandatory ATT&CK tag, no
#       modifiers / timeframe / aggregation); if the operator's
#       focus genuinely needs cross-batch correlation, the
#       synthesizer outputs CANDIDATE RULE: UNREPRESENTABLE with
#       a clear "needs v2" message.
#
#   dev-tools/birth-detection-engineer.command (NEW)
#     4-phase birth mirror. Posture YELLOW (external reach
#     gated until operator allowlists the ATT&CK URL via the
#     web_fetch constraint).
#
#   tests/unit/test_b391_detection_engineer_wiring.py (NEW)
#     8 wiring tests + policy presence checks. All 8 pass.
#     Combined with test_b385 (threat_intel_curator) = 16 wiring
#     tests across the two researcher-genre Phase B/C agents.
#
# What this UNBLOCKS / DOES NOT:
#   Operator can dispatch propose_detection.v1 once the agent is
#   birthed + the ATT&CK URL is allowlisted. The skill returns a
#   structured proposal the operator reviews + commits via git.
#   The DetectionEngine (B390) still needs lifespan wiring to
#   actually scan batches at runtime — T-future / T4 closes that.
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: D3 Phase C engine consumes rules but has no
#     operator-facing AUTHOR. Rules accumulate via ad-hoc git
#     commits without the structured proposal pipeline.
#   Prove non-load-bearing:
#     - Pure additive role wiring. Other roles + genres
#       unchanged.
#     - Skill is operator-driven (not scheduled); zero load
#       impact until dispatched.
#     - YELLOW posture gates external reach until allowlist is
#       configured.
#   Prove alternative is strictly better:
#     - Bundle into anomaly_ace: violates lane separation
#       (anomaly_ace consumes detection events; engineer authors
#       them). Anomaly_ace's actuator-class kit would also pull
#       the engineer into a higher-risk genre than it needs.
#     - No role at all: operator authors rules unaided; the
#       skill's evidence-required + chain-aware prompt eliminates
#       the speculative-rule pollution risk.
#
# Verification after this commit lands:
#   1. PYTHONPATH=src python3 -m pytest tests/unit/test_b391_detection_engineer_wiring.py
#      Expected: 8 passed.
#   2. force-restart-daemon (loads new role into trait_engine).
#   3. dev-tools/birth-detection-engineer.command - mints
#      DetectionEngineer-D3 instance.
#   4. dev-tools/diagnostic/diagnostic-all.command - section-05
#      picks up the new agent; sections 01/09 stay green.

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
        examples/skills/propose_detection.v1.yaml \
        dev-tools/birth-detection-engineer.command \
        tests/unit/test_b391_detection_engineer_wiring.py \
        dev-tools/commit-bursts/commit-burst391-adr0065-t3-detection-engineer.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(d3): ADR-0065 T3 detection_engineer role (B391)

Burst 391. D3 Phase C continues. Operator-facing rule author.
Researcher genre (B341/B386 pattern — kit needs web_fetch for
ATT&CK reference; advisory stance via constitution policies).

Wiring:
  trait_tree.yaml - detection_engineer role with cognitive 2.4
    (rule synthesis is the load-bearing skill). Fixes stray
    duplicate domain_weights on threat_intel_curator left from
    B385's edit pattern.
  genres.yaml - joins researcher alongside threat_intel_curator.
  constitution_templates.yaml - 5 policies enforce lane:
    forbid_direct_rule_install (operator commits),
    forbid_engine_invocation (substrate runs rules),
    forbid_response_action (Phase D's lane),
    require_attack_tag_in_proposals (ADR-0065 D3),
    require_evidence_in_proposal (no speculation).
  tool_catalog.yaml - kit: llm_think + memory_recall +
    memory_write + audit_chain_verify + delegate +
    text_summarize + web_fetch. No code_edit / shell_exec.
  handoffs.yaml - (d3_local_soc, detection_authoring) ->
    propose_detection.v1.
  domains/d3_local_soc.yaml - entry_agent + capability.

Skill (examples/skills/propose_detection.v1.yaml):
  prior_proposals -> verify_chain_integrity -> attack_reference
  (web_fetch) -> summarize_attack -> synthesize_rule (llm_think
  classify) -> record_proposal (memory_write).
  Synthesizer prompt enforces v1 Sigma subset: equality only,
  mandatory attack.<technique> tag, no modifiers / timeframe /
  aggregation. If operator focus needs v2 features, output
  CANDIDATE RULE: UNREPRESENTABLE rather than fake a v1 rule.

Birth (dev-tools/birth-detection-engineer.command):
  4-phase mirror of birth-threat-intel-curator. Posture YELLOW
  until operator allowlists ATT&CK URL.

Tests (test_b391_detection_engineer_wiring.py):
  8 wiring tests + policy presence checks. Combined with B385
  test set: 16 tests across the two researcher-genre Phase B/C
  agents. All pass.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: D3 Phase C engine has no operator-facing author.
  Prove non-load-bearing: additive; YELLOW posture gates
    external reach.
  Prove alternative is better: bundling into anomaly_ace
    violates lane (consume vs. author); no role at all loses
    the evidence-required + chain-aware proposal pipeline.

After this lands: T4 harness extensions + lifespan wiring of
DetectionEngine are the next bursts. T5 ships runbook + starter
rules. T6 closes Phase C."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=========================================================="
echo "=== Burst 391 complete - detection_engineer role ==="
echo "=========================================================="
echo "Next:"
echo "  dev-tools/force-restart-daemon.command"
echo "  dev-tools/birth-detection-engineer.command"
echo "  dev-tools/diagnostic/diagnostic-all.command"
echo ""
echo "Press any key to close."
read -n 1 || true
