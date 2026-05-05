#!/usr/bin/env bash
# Burst 51: ADR-0039 Distillation Forge + Swarm Orchestrator + benchmark plan + tracking.
#
# Three new design artifacts in response to the 2026-05-01 internal-research
# analysis + literature survey:
#
# 1. docs/decisions/ADR-0039-distillation-forge-swarm-orchestrator.md
#    Comprehensive design ADR (Proposed; v0.4 candidate). Captures the
#    hierarchical 1-large + N-small pattern, the Distillation Forge
#    subsystem, the Swarm Orchestrator genre family. Architectural rule
#    (§4) is the orchestrator's "no god objects, grow new tree" constraint.
#
# 2. docs/audits/2026-05-01-fsf-dispatch-overhead-benchmark-plan.md
#    Specifies the benchmark Burst that ADR-0039 §10 depends on. Measures
#    actual per-dispatch + audit-chain serialization overhead before
#    swarm topology decisions. Pre-implementation cost: ~2 sessions.
#
# 3. CREDITS.md + v0.2 close plan updated to track ADR-0039 as v0.4
#    candidate alongside ADR-0035/0036/0037.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

clean_locks() {
  rm -f .git/index.lock 2>/dev/null && true
  rm -f .git/HEAD.lock 2>/dev/null && true
  find .git/objects -name 'tmp_obj_*' -type f -delete 2>/dev/null
}

echo "=== Burst 51 — ADR-0039 + benchmark plan + v0.4 tracking ==="
echo
clean_locks
git add docs/decisions/ADR-0039-distillation-forge-swarm-orchestrator.md \
        docs/audits/2026-05-01-fsf-dispatch-overhead-benchmark-plan.md \
        CREDITS.md \
        docs/roadmap/2026-05-01-v0.2-close-plan.md \
        commit-burst51.command
clean_locks
git status --short
echo
clean_locks
git commit -m "ADR-0039 Distillation Forge + Swarm Orchestrator (Proposed; v0.4)

Three artifacts in response to the 2026-05-01 internal-research
analysis (proposing hierarchical 1-large + N-small pattern with
M4-Mac-Mini hardware estimates) + literature survey (grounding
the pattern against Constitutional AI / Orca / AutoGen / MetaGPT /
Voyager / MemGPT / etc.).

ADR-0039 (docs/decisions/ADR-0039-distillation-forge-swarm-orchestrator.md):
- Architectural rule §4 — 'no god objects, grow new tree with branches
  grounded by a solid feature' is the orchestrator's (Alex's) explicit
  constraint, baked in as a non-negotiable design rule. The two
  grounding features: distillation manifest + orchestration manifest,
  each anchoring its own top-level package.
- Distillation Forge (§1): new src/forest_soul_forge/distillation/
  package with manifest + trajectory recorder + MLX subprocess
  trainer + distilled_models registry table. Subprocess-only;
  no in-process MLX import.
- Swarm Orchestrator genre family (§2): three new genres
  (swarm_orchestrator L3/L3 read_only, swarm_controller L4/L3
  network, swarm_worker L2/L1 filesystem). Mirrors ADR-0033's
  three-tier security swarm precedent.
- Orchestration manifest (§3): grounds the swarm tree.
  src/forest_soul_forge/orchestration/ package separate from
  distillation/ — two trees, two grounding features, no
  cross-pollution.
- Anti-god-object discipline (§4): explicit table of which
  existing files do + do NOT get touched by the new subsystems.
  Enforced via a future test
  (tests/integration/test_no_god_object_growth.py).
- Constitution-hash semantics (§5): distilled agents are NEW
  agents with NEW DNAs. Three inheritance modes: clone / narrow
  / rebuild. Parent's hash is unchanged by distillation;
  bidirectional isolation is structural.
- Dependency expansion (§6): MLX-only at v0.4. Adds optional
  [distillation] extra in pyproject. No PyTorch / peft /
  bitsandbytes / Hugging Face transformers. Apple-Silicon-first;
  cross-platform is v0.5+.
- Persona Forge interaction (§7): distillation snapshots; persona
  evolves separately. Workers don't have persona logs.
- Verifier Loop sequence dependency (§8): ADR-0036 must ship
  first. Distillation produces small models with regressed
  behavior the constitution + initiative gates won't catch;
  ADR-0036's runtime contradiction-detection is the only
  mitigation surface.
- Hardware quarantine integration (§9): per-orchestration-manifest
  budget check; new pre-spawn gate in orchestration/topology.py.
  Lives in orchestration package, NOT bolted onto
  governance_pipeline.py.
- Throughput modeling (§10): pointer to benchmark plan;
  measured numbers replace estimates before implementation
  begins.
- Bibliography appendix: 18 papers from the literature survey,
  organized by relevance to FSF subsystems. Constitutional AI
  (Bai et al. 2022, arXiv:2212.08073) is the foundational
  reference for the existing constitution concept; Orca
  (Mukherjee et al. 2023, arXiv:2306.02707) is the strongest
  citation for the distillation use case.

Benchmark plan (docs/audits/2026-05-01-fsf-dispatch-overhead-benchmark-plan.md):
- Specifies the benchmark Burst that ADR-0039 §10 consumes.
- Measures: quiet-load per-dispatch latency (with per-pipeline-step
  breakdown), audit-chain serialization curve under N parallel
  dispatches, memory recall cost at varying memory sizes, gate
  costs (genre/initiative YAML reads).
- Instrumentation discipline: BenchmarkObservingPipeline wraps
  GovernancePipeline without changing semantics. Audit chain
  emissions still happen in full.
- Hardware target: M4 Mac Mini 16GB primary; >16GB secondary.
- Outcome scenarios: A (5+ tasks/sec sustained = topology
  viable), B (2-5 = mild revision), C (<2 = audit-chain
  optimization becomes prerequisite ADR), D (high variance =
  re-run after diagnosis).
- Estimated cost: 1 substantive session (build) + 1 measurement
  session (run). Pre-implementation gate for ADR-0039 work.

CREDITS.md:
- New 'Internal-research catalysts' section documenting the
  analysis + bibliography survey provenance.
- ADR-0039 row + benchmark-plan row.
- Maintains separation: SarahR1 is external; Distillation Forge
  catalysts are internal-research.

v0.2 close plan:
- ADR-0039 added to 'What v0.2 explicitly does NOT include' list.
- Pre-implementation gates noted (Verifier Loop + benchmark Burst
  must run first).

No code changes. No test changes. Test count unchanged (1589).
Pure design + planning + attribution artifacts.

This is v0.4 candidate work, properly captured at Proposed status
so future work has a clean starting line. Implementation gates on
ADR-0036 + the benchmark Burst landing first."

clean_locks
git push origin main
clean_locks
git log -1 --oneline
echo
echo "Burst 51 landed. ADR-0039 Distillation Forge filed (Proposed; v0.4)."
echo "Pre-impl gates: ADR-0036 + dispatch-overhead benchmark Burst."
echo ""
read -rp "Press Enter to close..."
