#!/bin/bash
# Burst 280 — ADR-0067 T2: decompose_intent.v1 builtin tool.
#
# LLM-driven cross-domain decomposer. Reads the domain registry
# (ADR-0067 T1 / B279), prompts the local model with the live
# catalog, parses JSON output, classifies each sub-intent's
# routability based on registry status + confidence threshold.
#
# Hard rule: this tool is READ-ONLY (side_effects=read_only). It
# never dispatches anything — only decides "what does this utterance
# contain and where would each piece go." T3 ships route_to_domain.v1
# which actually fires delegate.v1; T2 is the decomposer it depends on.
#
# What ships:
#
# 1. tools/builtin/decompose_intent.py:
#    - DecomposeIntentTool class (async execute path)
#    - validate(): utterance bounded length 2-4000, threshold in [0,1]
#    - execute():
#        a. Load domain registry fresh (operator may have edited)
#        b. Build prompt: catalog of every domain + capabilities +
#           1-2 example intents per domain + the operator utterance
#        c. Call ctx.provider.complete with task_kind=CLASSIFY
#        d. Parse JSON response (robust to markdown fence / prose
#           wrap / bare array / parse failures)
#        e. Classify each sub-intent's status:
#             - routable: domain dispatchable + confidence >= threshold
#             - ambiguous: domain dispatchable + confidence < threshold
#             - planned_domain: domain in registry but status=planned
#             - no_match: domain not in registry
#        f. Return {utterance, subintents, ambiguity_count, model,
#                   elapsed_ms} + PII-safe audit_payload (utterance
#                   HASH, not raw text)
#
# 2. config/tool_catalog.yaml: register decompose_intent.v1 with
#    side_effects=read_only, archetype_tags=[companion, assistant]
#    (only orchestrator-class agents need this tool).
#
# 3. tools/builtin/__init__.py: import DecomposeIntentTool + register
#    in the builtin tool registry init pattern.
#
# Tests (test_decompose_intent.py — 17 cases):
#   Validation: missing/non-string/length-floor/length-ceiling/threshold-range
#   Response parsing: strict JSON / markdown fence / embedded in prose /
#     bare array / empty / garbage / missing fields (padded with defaults)
#   Execute flow with mock provider + temp registry:
#     - high confidence routes → status='routable'
#     - low confidence → status='ambiguous'
#     - unknown domain → status='no_match'
#     - planned domain → status='planned_domain'
#   Audit safety: payload contains utterance_hash, not raw utterance
#   Failure mode: no provider wired → ToolValidationError, not crash
#
# What's NOT in T2 (queued):
#   T3: route_to_domain.v1 — wraps delegate.v1, gates on status='routable',
#       emits domain_routed audit event with the decomposition decision
#   T4: full routing engine — handoffs.yaml hardcoded rail + learned
#       routes adapter
#   T5: domain_orchestrator agent role + birth (singleton, companion
#       genre, has decompose + route in its constitution)

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add src/forest_soul_forge/tools/builtin/decompose_intent.py \
        src/forest_soul_forge/tools/builtin/__init__.py \
        config/tool_catalog.yaml \
        tests/unit/test_decompose_intent.py \
        dev-tools/commit-bursts/commit-burst280-adr0067-t2-decompose-intent.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(orchestrator): ADR-0067 T2 — decompose_intent.v1 tool (B280)

Burst 280. LLM-driven cross-domain decomposer. Reads the live domain
registry (ADR-0067 T1), prompts the local model with the catalog,
parses JSON output, classifies each sub-intent's routability based
on registry status + operator-tunable confidence threshold.

Hard rule: read-only side-effect tier. The tool never dispatches
anything — only decides 'what does this utterance contain and where
would each piece go.' T3 ships route_to_domain.v1 which actually
fires delegate.v1; T2 is the decomposer it depends on.

What ships:

  - tools/builtin/decompose_intent.py:
    DecomposeIntentTool (async). validate() bounds utterance length
    + threshold range. execute() loads the registry fresh, builds a
    per-call prompt enumerating every domain + capabilities + 1-2
    example intents, calls ctx.provider.complete with
    task_kind=CLASSIFY, parses JSON output robustly (markdown fence
    / prose wrap / bare array / parse-failure all handled),
    classifies sub-intents into 4 status buckets:
      - routable (dispatchable + above threshold)
      - ambiguous (dispatchable + below threshold)
      - planned_domain (status=planned)
      - no_match (domain_id not in registry)
    Returns {utterance, subintents, ambiguity_count, model,
    elapsed_ms} + PII-safe audit payload (utterance HASH, not raw
    text).

  - config/tool_catalog.yaml: register decompose_intent.v1 with
    archetype_tags=[companion, assistant] — only orchestrator-class
    agents need this tool.

  - tools/builtin/__init__.py: import + register
    DecomposeIntentTool in the builtin registry init.

Tests: test_decompose_intent.py — 17 cases covering validation,
response parsing across all common LLM output shapes, full execute
path with mock provider + temp registry exercising all 4 status
classifications, audit-payload PII safety, no-provider refusal.

Queued T3-T5: route_to_domain.v1 (gates on status='routable', wraps
delegate.v1, emits domain_routed audit event), full routing engine
with handoffs.yaml hardcoded rail, domain_orchestrator agent role
+ singleton birth."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 280 complete — ADR-0067 T2 decompose_intent.v1 shipped ==="
echo "Next: T3 route_to_domain.v1 — actually fires delegate.v1 on routable subs."
echo ""
echo "Press any key to close."
read -n 1
