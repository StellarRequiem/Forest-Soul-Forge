#!/bin/bash
# Burst 279 — ADR-0067 T1: cross-domain orchestrator substrate.
#
# Foundation for the ten-domain platform arc routing layer. T1
# ships the domain registry + manifest format + 10 seed manifests
# (one per domain) so the orchestrator (T5) and decompose_intent.v1
# (T2) have a stable surface to query.
#
# What ships:
#
# 1. docs/decisions/ADR-0067-cross-domain-orchestrator.md
#    Full ADR: 5 decisions (registry as routing source of truth,
#    status field gates dispatch, routing as delegate call, LLM
#    decomposition, hardcoded + learned dual rail) + 8 tranches
#    T1-T8 (T1 this burst).
#
# 2. src/forest_soul_forge/core/domain_registry.py
#    DomainRegistry + Domain + EntryAgent frozen dataclasses.
#    load_domain_registry() reads every *.yaml in config/domains/,
#    validates required fields + status enum + cross-references on
#    handoff_targets. Returns (registry, errors) — hard fail only
#    on missing directory or path-is-file; per-manifest problems
#    surface as soft errors so one bad file doesn't kill the load.
#
# 3. config/domains/ — 10 seed manifests:
#    - d1_knowledge_forge — Researcher + Archivist + Synthesizer + Verifier
#    - d2_daily_life_os — Coordinator + Inbox + Time + Tasks + Reflector
#    - d3_local_soc — STATUS=partial (9-agent blue team alive)
#    - d4_code_review — STATUS=partial (SW-track triune alive)
#    - d5_smart_home — Home/Energy/Comfort/Sentinel stewards
#    - d6_finance — Budget/Tracker/Investment/Risk/Bill agents
#    - d7_content_studio — Writer/Researcher/Editor/Style/Distribution
#    - d8_compliance — Scanner/Enforcer/Reporter/Evidence/Archivist
#    - d9_learning_coach — Mentor/Curriculum/Assessor/SpacedRep/Socratic
#    - d10_research_lab — Gatherer/Analyst/Critic/Synthesizer/Experimenter/Moderator
#
#    Each manifest carries: domain_id, name, status (planned by
#    default; SOC + Code Review = partial since their swarms are
#    already alive), description, entry_agents (role + capability),
#    capabilities, example_intents (operator utterances that should
#    route here), depends_on_substrate (ADR refs), depends_on_connectors
#    (MCP plugins), handoff_targets (cross-domain compounds), notes.
#
# Tests (test_domain_registry.py — 13 cases):
#   Hard failures:
#     - missing directory raises DomainRegistryError
#     - path-is-file raises DomainRegistryError
#   Soft warnings:
#     - empty directory → registry with no domains + warning
#     - duplicate domain_id → first kept, second logged
#     - dangling handoff_target → logged
#     - invalid status → manifest dropped
#     - malformed YAML → manifest dropped
#     - missing required field → manifest dropped
#   Lookup helpers:
#     - by_id (positive + negative cases)
#     - dispatchable_ids (live + partial included; planned excluded)
#     - by_capability (matches via capabilities OR entry_agents)
#     - Domain.is_dispatchable property
#   Real seeds:
#     - all 10 seed manifests load without errors + with expected ids
#
# What's NOT in T1 (queued):
#   T2: decompose_intent.v1 builtin — LLM-driven sub-intent extractor
#       reading the registry to populate domain enumeration
#   T3: route_to_domain.v1 builtin — wraps delegate.v1; emits
#       domain_routed audit event
#   T4: full routing engine — handoffs.yaml hardcoded rail +
#       learned_routes.yaml adapter
#   T5: domain_orchestrator agent role + birth
#   T6: cross-domain handoff coordinator (multi-domain dispatch)
#   T7: frontend Orchestrator pane
#   T8: /orchestrator/status health surface
#
# After T1 lands the manifests are queryable; the substrate
# downstream consumers (T2-T8) can build against a stable surface.

set -euo pipefail
cd "$(dirname "$0")/../.."

if [ -e .git/index.lock ]; then
  echo "ERROR: .git/index.lock present. Run ./clean-git-locks.command first."
  exit 2
fi

git add docs/decisions/ADR-0067-cross-domain-orchestrator.md \
        src/forest_soul_forge/core/domain_registry.py \
        config/domains/d1_knowledge_forge.yaml \
        config/domains/d2_daily_life_os.yaml \
        config/domains/d3_local_soc.yaml \
        config/domains/d4_code_review.yaml \
        config/domains/d5_smart_home.yaml \
        config/domains/d6_finance.yaml \
        config/domains/d7_content_studio.yaml \
        config/domains/d8_compliance.yaml \
        config/domains/d9_learning_coach.yaml \
        config/domains/d10_research_lab.yaml \
        tests/unit/test_domain_registry.py \
        dev-tools/commit-bursts/commit-burst279-adr0067-t1-domain-registry.command

echo "--- staged for commit ---"
git diff --cached --stat
echo "-------------------------"

git commit -m "feat(orchestrator): ADR-0067 T1 — domain registry substrate (B279)

Burst 279. Foundation for the ten-domain platform arc routing layer.
T1 ships the domain registry + manifest format + 10 seed manifests
so the orchestrator (T5) and decompose_intent.v1 (T2) have a stable
surface to query.

What ships:

  - ADR-0067 full record: 5 decisions + 8 tranches (T1 this burst).
    Routing is a delegate call; registry is the source of truth;
    status field gates dispatch (planned / partial / live);
    decomposition uses local LLM; hardcoded + learned routes
    co-exist with hardcoded always winning on conflict.

  - core/domain_registry.py: DomainRegistry + Domain + EntryAgent
    frozen dataclasses. load_domain_registry() reads
    config/domains/*.yaml, validates fields + status enum +
    cross-references handoff_targets. Returns (registry, errors) —
    hard fail only on missing directory; per-manifest problems
    surface as soft errors.

  - config/domains/ with 10 seed manifests (one per domain D1-D10).
    Two are STATUS=partial (D3 SOC and D4 Code Review — swarms
    already alive); the other 8 are STATUS=planned. Each manifest
    carries entry_agents, capabilities, example_intents, ADR + MCP
    dependencies, handoff_targets to neighboring domains.

Tests: test_domain_registry.py — 13 cases covering hard failures
(missing dir, path-is-file), soft warnings (empty dir, duplicate
id, dangling handoff, invalid status, malformed YAML, missing
required field), lookup helpers (by_id, dispatchable_ids,
by_capability, is_dispatchable), AND a real-seed test that loads
all 10 shipped manifests + verifies zero errors + expected ids.

Queued T2-T8: decompose_intent.v1, route_to_domain.v1, routing
engine, orchestrator agent, handoff coordinator, frontend pane,
health surface."

echo "--- commit landed ---"
git log --oneline -1

echo ""
echo "--- pushing to origin ---"
git push origin main

echo ""
echo "=== Burst 279 complete — ADR-0067 T1 domain registry shipped ==="
echo "Next: ADR-0067 T2 decompose_intent.v1 (LLM-driven sub-intent extractor)."
echo ""
echo "Press any key to close."
read -n 1
