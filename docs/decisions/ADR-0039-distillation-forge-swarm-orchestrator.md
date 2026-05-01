# ADR-0039 — Distillation Forge + Swarm Orchestrator pattern

- **Status:** Proposed (filed 2026-05-01; v0.4 candidate). Awaiting orchestrator promotion.
- **Date:** 2026-05-01
- **Supersedes:** —
- **Related:** ADR-0001 (DNA — distilled agents are NEW DNAs, not variants of existing ones, per §5 of this ADR), ADR-0002 (lineage — the substrate the swarm orchestrator's hierarchy walks), ADR-0004 (constitution builder — distilled agents get their own constitution), ADR-0008 (local-first model provider — Distillation Forge MUST run local-only on Companion-touching paths), ADR-0019 (tool execution runtime — distilled inference goes through the same dispatch path with its own audit overhead), ADR-0021 + amendment (genres + initiative ladder — Swarm Orchestrator is a new genre family), ADR-0030 (Tool Forge — the precedent for agent-driven artifact generation), ADR-0031 (Skill Forge — the precedent for chain-verified skill manifests), ADR-0033 (Security Swarm — the architectural precedent for hierarchical multi-agent topology), ADR-0034 (SW-track triune — precedent for explicit per-role privilege gradient), ADR-0035 (Persona Forge — the runtime-mutable counterpart distillation snapshots and freezes), ADR-0036 (Verifier Loop — load-bearing prerequisite per §8 of this ADR; distillation cannot ship before Verifier Loop has a runtime regression-detection story), ADR-0038 (companion harm model — H-2/H-3/H-4/H-7/H-8 mitigations apply to swarm orchestrator children too).
- **External catalysts:**
  - Internal-research analysis (2026-05-01) framing hierarchical 1-large + N-small pattern as feasible on FSF's existing primitives. Cites realistic M4-Mac-Mini hardware numbers and proposes the "Distillation Forge" naming + Swarm Orchestrator genre.
  - Internal-research literature survey (2026-05-01) grounding the pattern against published research. Critical citations: **Constitutional AI** (Bai et al. 2022, arXiv:2212.08073) — the foundation FSF's constitution concept descends from; **Orca** (Mukherjee et al. 2023, arXiv:2306.02707) — explanation-trace distillation, the strongest paper for FSF's "soul distillation" use case; **MemGPT** (Packer et al. 2023, arXiv:2310.08560) — hierarchical memory; **AutoGen** (Wu et al. 2023, arXiv:2308.08155) — multi-agent conversation patterns analog to delegate.v1. Full bibliography in appendix.

## Architectural rule for this ADR

**No god objects. Grow new branches grounded by a solid feature.**

Per orchestrator instruction (2026-05-01): if FSF gains a substrate as
significant as Distillation Forge + Swarm Orchestrator, it MUST come
in as a new architectural tree with its own root and clean
composition — not bolted onto existing core/* god-objects (writes.py,
dispatcher.py, conversations.py).

This ADR enforces that constraint by introducing **two grounding
features** — one per subsystem — and routing all new code through them:

1. **Distillation manifest** — content-addressed YAML capturing parent
   DNA, distillation parameters, trajectory data references, and output
   model fingerprint. Anchors the distillation tree.
2. **Orchestration manifest** — content-addressed YAML capturing
   orchestrator identity, permitted topology, per-tier constraints,
   escalation rules. Anchors the swarm tree.

Each manifest gets its own top-level path (`distillation/` and
`orchestration/`) parallel to existing trees (`souls/`, `constitutions/`,
`persona/` from ADR-0035). Each subsystem's code lives in its own
package (`src/forest_soul_forge/distillation/`,
`src/forest_soul_forge/orchestration/`). Existing core/* and daemon/*
files extend ONLY at clean composition seams (genre engine reads new
genre family; dispatcher reads optional manifest; etc.). No new
fields on Constitution, MemoryEntry, or any other existing dataclass.

This rule is non-negotiable. A future tranche that proposes adding
a `distillation_state` field to Constitution or `swarm_role` to
MemoryEntry violates it and gets rejected.

## Context

The internal analysis (2026-05-01) made a concrete proposal:

> One (or a few) larger orchestrator model(s) refining + supervising
> swarms of tiny, ultra-efficient small models that crank out
> high-volume parallel tasks — maps directly to hierarchical
> multi-agent patterns. 2026 agent-distillation research already
> shows this works for tool-using agents (large teacher generates
> trajectories/CoT + tool calls; small students are fine-tuned or
> distilled on that synthetic data to handle narrow, high-speed
> subtasks).

The proposal correctly identifies that FSF's existing primitives —
delegation/lineage (ADR-0002), governance pipeline (ADR-0019 + R3),
skill forging (ADR-0030 + ADR-0031), security swarm precedent
(ADR-0033), and constitution enforcement (ADR-0001 + ADR-0004) —
provide most of what's needed to build this pattern as a coherent
new subsystem.

The literature survey grounds the proposal:

- **Constitutional AI** (Bai et al. 2022) is FSF's constitution
  concept's direct ancestor. Adoption pattern for FSF's three-layer
  composition (role base → trait modifiers → flagged combos) maps
  cleanly to distilled agents inheriting parent constitutional
  behavior while gaining narrow-task specialization.
- **Orca** (Mukherjee et al. 2023) frames distillation via
  explanation traces rather than just outputs. This is the
  load-bearing technique for the proposed Distillation Forge —
  small agents learn the *reasoning pattern* of the parent, not
  just its surface answers.
- **AutoGen** (Wu et al. 2023) and **MetaGPT** (Hong et al. 2023)
  formalize multi-agent conversation patterns. FSF's `delegate.v1`
  + lineage + Y-track conversation runtime are FSF-specific
  implementations of the same pattern; the Swarm Orchestrator
  genre formalizes the orchestrator-supervises-workers topology.
- **Voyager** (Wang et al. 2023) shows skill libraries that grow
  from LLM-generated code, verified by execution. Maps to FSF's
  Tool Forge + Skill Forge pipeline. The Distillation Forge
  proposed here extends the same pattern from skill-shape
  artifacts to model-shape artifacts.

What's missing in FSF today is the structural pattern that lets:
(a) a large parent agent generate distillation trajectories, (b)
the distillation pipeline produce a small specialized child agent,
(c) a Swarm Orchestrator agent supervise N children doing parallel
work under constitutional + initiative constraints, (d) the
hierarchy stay auditable via the existing chain.

This ADR proposes the design for that pattern. Implementation is
v0.4+; v0.2/v0.3 work continues per the existing roadmap.

## Decision

### §1 — Distillation Forge subsystem

A new package `src/forest_soul_forge/distillation/` providing:

- **`distillation/manifest.py`** — `DistillationManifest` dataclass +
  YAML serializer/parser. The grounding feature for the entire tree.
- **`distillation/trajectory.py`** — `TrajectoryRecorder` that captures
  a parent agent's prompt → CoT → tool-calls → outcome sequences
  during operator-driven training sessions.
- **`distillation/trainer.py`** — wraps MLX (Apple Silicon) LoRA/QLoRA
  fine-tuning. Pure subprocess invocation; no PyTorch, no peft Python
  dep. The training step lives in its own subprocess so an OOM kill
  doesn't take down the daemon.
- **`distillation/registry.py`** — separate SQLite table
  `distilled_models` tracking (manifest_id, parent_dna, child_dna,
  fingerprint_sha256, created_at). Read-only from agent perspective;
  operator-writable through new `/distillation/*` endpoints.

The manifest shape:

```yaml
manifest_id:    <uuid>
schema_version: 1
parent:
  dna:              <parent agent's DNA>
  constitution_hash: <parent's hash at trajectory-capture time>
  instance_id:      <parent's instance_id>
trajectory:
  trace_count:      <number of recorded prompt→action sequences>
  total_tokens:     <token count across all traces>
  date_range:       (start_iso, end_iso)
  tool_call_classes: [<canonical-tool names that appeared>]
  audit_chain_refs: [<sequence numbers in audit chain that anchor traces>]
training:
  base_model:       <e.g. "gemma-2-2b-it">
  technique:        "lora_q4" | "qlora_q4" | "full_finetune"
  lora_rank:        <int>
  lora_alpha:       <int>
  epochs:           <int>
  validation_split: <float>
output:
  model_fingerprint:  <sha256 of merged model weights>
  loaded_size_bytes:  <int>
  context_window:     <int>
  created_at:         <iso>
proposed_by:    operator     # always; agent-self-distillation refused at v0.4
narrow_task_scope:
  description:    <prose, max 1000 chars>
  permitted_tools: [<tool catalog names>]   # subset of parent's kit
  forbidden_tools: [<tool catalog names>]   # explicit redact
  initiative_ceiling: L2 | L3   # NEVER higher than parent's; per §5
constitution_inheritance:
  base:           <"clone" | "narrow" | "rebuild">
  policy_subset:  [<policy ids from parent>]   # for "narrow" mode
status:         proposed | trained | ratified | archived
ratified_at:    <iso>
ratified_by:    <operator handle>
```

Manifests are append-only on disk (`distillation/<manifest_id>.yaml`).
The `distilled_models` registry table indexes them.

### §2 — Swarm Orchestrator genre family

Three new genres in `genres.yaml`:

```yaml
swarm_orchestrator:
  description: |
    Top-tier coordinator for a 1-large + N-small distillation swarm.
    Runs a 7-14B class local model (operator-tunable). Plans tasks,
    delegates to swarm_worker children, refines outputs. Doesn't
    directly execute high-volume tasks; that's the workers' job.
    Always operator-birthed; agent-spawned orchestrator refused.
  risk_profile:
    max_side_effects: read_only       # the orchestrator itself is read-only;
                                      # workers carry the side-effect surface
    provider_constraint: local_only
    memory_ceiling: lineage           # so workers can read the orchestrator's plan
    max_initiative_level: L3
    default_initiative_level: L3
  min_trait_floors:
    transparency:   60
    evidence_demand: 50
  default_kit_pattern: [orchestrator_planning, lineage_inspect, trajectory_record]
  trait_emphasis: [thoroughness, evidence_demand, lateral_thinking, transparency]
  memory_pattern: episodic_long
  spawn_compatibility: [swarm_orchestrator, swarm_controller, swarm_worker]
  roles: [orchestrator_alpha]   # exactly one role at v0.4

swarm_controller:
  description: |
    Mid-tier coordinator. Breaks down orchestrator plans into worker
    tasks; aggregates worker results. Runs a 3-7B class model. Spawned
    by orchestrator only (constitutional gate refuses operator-spawn).
  risk_profile:
    max_side_effects: network
    provider_constraint: local_only
    memory_ceiling: lineage
    max_initiative_level: L4
    default_initiative_level: L3
  min_trait_floors:
    evidence_demand: 50
    double_checking: 60
  default_kit_pattern: [task_decomposition, worker_dispatch, output_aggregation]
  trait_emphasis: [evidence_demand, double_checking, technical_accuracy]
  memory_pattern: episodic_focused
  spawn_compatibility: [swarm_controller, swarm_worker]
  roles: [controller_beta]

swarm_worker:
  description: |
    Bottom-tier executor. Runs a distilled 2-3.8B model specialized
    for one narrow task class. High volume, low context. Spawned by
    controller (or orchestrator directly, per topology). Strict
    initiative ceiling; can NOT autonomously decide what task class
    to work on — that comes from the controller's dispatch.
  risk_profile:
    max_side_effects: filesystem      # widest worker surface; per-role narrows
    provider_constraint: local_only
    memory_ceiling: lineage
    max_initiative_level: L2
    default_initiative_level: L1
  min_trait_floors:
    evidence_demand: 40               # workers can be terser than orchestrators
  default_kit_pattern: []              # populated per distilled-role
  trait_emphasis: [technical_accuracy, formality, directness]
  memory_pattern: short_retention
  spawn_compatibility: [swarm_worker]
  roles: []                            # populated dynamically from distilled models
```

Three new genres land in `genres.yaml` only when ADR-0039 is
Accepted; they don't get sneaked in as Proposed.

### §3 — The orchestration manifest (the second grounding feature)

A new package `src/forest_soul_forge/orchestration/`:

- **`orchestration/manifest.py`** — `OrchestrationManifest` dataclass
  + serialization. Captures the swarm topology, escalation rules,
  per-tier task classes.
- **`orchestration/topology.py`** — pure functions for validating a
  proposed swarm shape against ADR-0021-am genre rules + ADR-0033
  spawn-compatibility precedent.
- **`orchestration/dispatcher.py`** — orchestrator-side helper that
  packages a task for a worker subset, watches for completion, runs
  the aggregation step. Wraps existing `delegate.v1` rather than
  re-implementing dispatch.

The manifest:

```yaml
manifest_id:    <uuid>
schema_version: 1
orchestrator:
  instance_id:      <orchestrator's instance_id>
  dna:              <DNA>
  constitution_hash: <hash at swarm-birth time>
topology:
  shape:           "1+N" | "1+M+N" | "M+N"
  controller_count: <int>          # 0 for "1+N"
  worker_count:     <int>
  worker_distillations: [
    {
      manifest_id:  <distillation manifest id>
      role_alias:   <e.g. "log_classifier_worker">
      replica_count: <int>
    },
    ...
  ]
escalation_rules:
  worker_to_controller: [<conditions like 'confidence<0.5', 'tool_failure'>]
  controller_to_orchestrator: [<conditions>]
  worker_to_operator_via_chain: [<conditions; per ADR-0033 escalation precedent>]
hardware_budget:
  max_concurrent_models: <int>     # per ADR-0033 K6 hardware quarantine
  expected_unified_memory_gb: <float>
constitution_inheritance:
  controllers_inherit_orchestrator: bool
  workers_inherit_via_distillation: true   # always; per §5
status:         proposed | active | drained | archived
created_at:     <iso>
created_by:     <operator handle>     # operator-only; agents cannot create swarms
```

Manifests are append-only. `swarm_topologies` registry table indexes
them.

### §4 — Anti-god-object discipline (the architectural rule made concrete)

| New surface | Lives in | Existing surface it touches | How |
|---|---|---|---|
| Distillation manifest | `distillation/manifest.py` | — | Self-contained |
| Trajectory recorder | `distillation/trajectory.py` | audit chain | Reads via existing `audit/tail`; never writes |
| Trainer | `distillation/trainer.py` | — | Subprocess; no in-process state |
| Distilled-model registry | `distillation/registry.py` | `registry/schema.py` | New table only; no column adds to existing tables |
| Orchestration manifest | `orchestration/manifest.py` | — | Self-contained |
| Topology validator | `orchestration/topology.py` | `core/genre_engine.py` | Imports `genre_for(role)` only |
| Orchestrator dispatcher | `orchestration/dispatcher.py` | `tools/builtin/delegate.py` | Wraps; doesn't replace |
| Three new genres | `config/genres.yaml` | `core/genre_engine.py` | Loaded as data, not code |
| New skill manifests | `config/skills/swarm_*.yaml` | `forge/skill_engine.py` | Loaded as data |
| Daemon endpoints | `daemon/routers/distillation.py` + `daemon/routers/orchestration.py` | `daemon/app.py` | Two new routers; no extension of existing routers |

**Files NOT touched:** `core/constitution.py`, `core/memory.py`,
`core/dna.py`, `tools/dispatcher.py`, `tools/governance_pipeline.py`,
`daemon/routers/writes.py`, `daemon/routers/conversations.py`. If a
later tranche proposes editing any of these for distillation/swarm
reasons, that's a violation of the architectural rule and rejected.

The rule is enforceable via a test:
`tests/integration/test_no_god_object_growth.py` — imports the
distillation/* and orchestration/* packages, asserts that the
modules they import from `core/*` and `daemon/*` are limited to a
hardcoded allow-list. If a future module starts importing
`from forest_soul_forge.core.constitution import build` (for
example) the test fails.

### §5 — Constitution-hash semantics for distilled agents

**Distilled agents are NEW agents with NEW DNAs.** Not variants of
the parent. Not configuration of an existing constitution.

When the operator ratifies a distillation manifest:
1. The output model gets its own DNA derived from `(parent_dna,
   manifest_id, narrow_task_scope.description)` — content-addressed
   so re-distillation with same params produces same DNA.
2. A new constitution is built using `constitution.build()` with
   the manifest's `constitution_inheritance.base` mode:
   - `clone` → identical policies + risk_thresholds + out_of_scope
     + operator_duties (most permissive; agent-style replica)
   - `narrow` → subset of parent's policies per
     `policy_subset` list (most common; specialization)
   - `rebuild` → fresh derivation from role + traits, parent
     untouched (most divergent; default to OFF)
3. The new constitution gets its own hash, distinct from parent's.
4. A new soul.md narrative is rendered, citing the parent in
   frontmatter (`distilled_from: <parent_dna>`).
5. The new agent appears in the registry with its own
   `instance_id`, separate from parent's chain.

Audit chain emits `agent_distilled` event with both DNAs +
manifest_id. Lineage is recorded separately from spawn lineage —
distillation is a different relationship than parent→child spawn,
so a new `distillation_lineage` table is the registry surface
(distinct from `agent_ancestry`).

**Critical invariant preserved:** parent's constitution_hash is
unchanged by distillation. The parent does NOT inherit any of the
child's behavioral changes. Two-way isolation is structural.

### §6 — Dependency expansion (MLX-only at v0.4)

Forest Soul Forge's stack today: Ollama for inference + Python
stdlib + FastAPI. No MLX, vLLM, PyTorch, peft, bitsandbytes,
transformers (verified disk state at commit `061f63c`).

This ADR commits to **MLX-only** at v0.4. Specifically:
- Add `mlx-lm` as an optional dependency (`pyproject.toml` extras
  `[distillation]`).
- Distillation runs through subprocess invocation of `mlx_lm.lora`
  CLI. No in-process MLX import.
- Inference of distilled models runs through the existing Ollama
  provider — the trained LoRA gets merged into a base model and
  served as just another Ollama-pulled model. The provider doesn't
  know the model came from FSF's distillation pipeline.
- No PyTorch, no peft, no bitsandbytes, no Hugging Face
  transformers. These are large dependencies that pull in CUDA-only
  code paths; FSF stays Apple-Silicon-first.
- Linux operators who want distillation get a "not supported in
  v0.4" message; cross-platform distillation is a v0.5+ candidate.

**The dependency-expansion ADR is THIS ADR.** A separate "should
FSF add MLX" decision would be redundant; this ADR commits to it
under a constrained scope (subprocess-only, optional extra,
Apple-Silicon-only).

### §7 — ADR-0035 Persona Forge interaction

**Distillation snapshots; persona evolves separately.** Per
ADR-0035, an agent's persona is the runtime overlay of ratified
proposals on top of the constitution's trait_emphasis. Distilling
from a parent captures a snapshot of behavior at distillation time,
including the parent's persona at that moment.

The interaction:
- A distilled child has the parent's effective persona at the
  moment of distillation, frozen into model weights.
- The parent's persona keeps evolving via post-distillation
  proposals.
- The child does NOT have its own persona log. Persona is for
  agents that drift via interaction; distilled workers don't have
  meaningful drift surfaces (they execute narrow tasks).
- If the parent's persona drifts meaningfully, operator decides
  whether to re-distill. The decision is captured in a new
  distillation manifest with a `redistilled_from: <prior_manifest_id>`
  reference.

**This is option (a) from the architectural choice § of my
analytical read.** Distilled agents are frozen-at-distillation; not
persona-tracked. Justified by the worker pattern — workers are
short-lived, narrow-task, replicated. Tracking persona on each is
operationally meaningless.

### §8 — ADR-0036 Verifier Loop dependency (load-bearing)

**Distillation cannot ship before ADR-0036 is implemented.**

Reason: distillation produces small models with regressed behavior
relative to the parent. The constitution + initiative gates catch
*structural* violations (calling a forbidden tool, hitting
side-effect ceiling). They do NOT catch *behavioral* regression
(parent reliably classified X as Y; distilled child reliably
classifies X as Z).

ADR-0036 Verifier Loop's `memory_flag_contradiction.v1` tool +
auto-detected contradictions table is the runtime detection
surface for this kind of regression. A Verifier scanning a
worker's outputs against the orchestrator's expected behavior
flags divergence; operator reviews; accept/reject the worker.

Sequence dependency:
1. ADR-0036 lands first (T1+T2 = Verifier role + flag tool;
   minimum bar; deferred from v0.2 per close plan).
2. THEN ADR-0039 implementation begins (T1 = manifest schema; this
   ADR's tranche 1).

Implementing distillation without the Verifier surface ships an
unsupervised behavioral-regression vector. The §0 Hippocratic
gate refuses it: the harm is concrete (silent regression →
operator surprise), and the alternative (build Verifier first) is
strictly better.

### §9 — Hardware quarantine integration (ADR-0033 K6)

The existing `HardwareQuarantineStep` in the governance pipeline
gates dispatch on hardware fingerprint. It's a per-call check.

The Swarm Orchestrator pattern adds a **per-deployment** hardware
budget concern that the per-call check doesn't express:

- An orchestration manifest says `expected_unified_memory_gb: 14`.
- The deployment is on a 16GB Mac Mini.
- Currently 4 workers + 1 controller + 1 orchestrator are loaded
  simultaneously.
- A 5th worker birth attempts to load — would take total to 16.5GB,
  the system pages, throughput collapses.

The existing K6 step doesn't catch this. The Swarm Orchestrator
gets a **new pre-spawn check**:

- `orchestration/topology.py:check_hardware_budget()` reads the
  active manifest's `hardware_budget`, sums in-flight worker
  memory loads, refuses spawn that would exceed.
- Audit event `swarm_hardware_budget_exceeded` records the
  refusal.
- Operator sees the budget contention in the dashboard (ADR-0037
  surface — sequence dependency on that too).

This check lives in `orchestration/`, not in
`tools/governance_pipeline.py`. Per the architectural rule (§4) —
the existing pipeline doesn't gain a new step; the swarm
subsystem gates its own spawning.

### §10 — Throughput modeling (sequence to benchmark Burst)

The internal analysis estimated 10-25 parallel agent tasks/sec
with the proposed 1+M+N topology. **That number ignores FSF's
audit-heavy dispatch overhead.**

Each FSF dispatch:
- ≥1 audit chain entry (SHA-256 + JSONL append; serialized via
  single-writer SQLite lock)
- 9 governance pipeline steps
- Possibly approval queue interaction
- Lineage walks + memory recall on cross-agent calls

Realistic ceiling on 16GB M4 Mac Mini for FSF's audit-heavy
dispatch: **probably 2-5 sustained parallel tasks/sec** before
the audit chain becomes serialization bottleneck. The 10-25
number is a raw inference-throughput ceiling assuming thin runtime
overhead — which FSF doesn't have, by design.

A benchmark Burst (queued separately at
`docs/audits/2026-05-01-fsf-dispatch-overhead-benchmark-plan.md`)
will measure:
1. Per-dispatch overhead at quiet load (1 dispatch at a time).
2. Audit chain serialization cost under N parallel dispatches.
3. Memory recall cost at varying memory sizes.
4. Genre/initiative gate costs.

The benchmark precedes ADR-0039 implementation. If the measured
ceiling is 1 task/sec, the Swarm Orchestrator pattern is
operationally limited to a different shape (maybe 1 orchestrator
+ 2 workers, not 1+M+N). If 5+ task/sec, the proposed shape is
viable.

## Trade-offs and rejected alternatives

**Daemon-side distillation pipeline (in-process MLX import).**
Rejected. MLX is a 200MB+ dependency that pulls Apple-Silicon-only
code; importing in-process means a daemon startup that fails on
Linux. Subprocess-only keeps the daemon stack thin.

**PyTorch-based fine-tuning instead of MLX LoRA.** Rejected.
PyTorch is a 1GB+ dep with CUDA expectations. FSF is local-first;
adding PyTorch contradicts the local-first ethos AND adds heavy
disk + container size + cross-platform headaches. MLX is
Apple-Silicon-native, lightweight, and sufficient for the use case.

**Distilled agents share parent's constitution_hash.** Rejected.
Behavior diverges; hash should diverge. ADR-0001's
content-addressing principle says "same inputs → same hash"; a
distilled child has different inputs (different model weights,
different parameters) so it's a different agent. Pretending it's
the same agent breaks audit chain semantics.

**Single new genre `swarm` instead of three (orchestrator/
controller/worker).** Rejected. The three-tier topology mirrors
ADR-0033's security_low/mid/high precedent, which has proven
operationally clean. Collapsing to one genre loses the per-tier
posture distinctions (orchestrator is read_only L3; worker is
filesystem L1) that the multi-tier shape captures.

**Persona-track distilled workers.** Rejected per §7. Workers are
narrow-task, short-lived, replicated. Persona infrastructure adds
operational cost for no clear behavioral gain on workers.

**Implement before ADR-0036.** Rejected per §8. Behavioral
regression is the concrete harm; Verifier Loop is the only
mitigation surface. §0 Hippocratic gate refuses.

**Bolt distillation onto existing Tool Forge / Skill Forge
machinery.** Rejected per §4 architectural rule. Tool Forge
produces tool manifests; Skill Forge produces skill manifests;
both are agent-driven artifact pipelines. Distillation Forge
produces model weights — different artifact class, different
verification needs (model fingerprint vs. code review),
different runtime path. A new tree is the right shape.

**Ship `model_fine_tune.v1` as a regular tool.** Rejected. Fine-
tuning takes minutes-to-hours; FSF tool dispatch is meant for
seconds-class operations. A long-running training job has its own
lifecycle (queued, running, completed, failed) that doesn't fit
the dispatch shape. The orchestration/distillation routers handle
it as an async resource, more like ADR-0019 T3's pending-approval
queue than a synchronous tool call.

## Consequences

**Positive.**
- A new substantial subsystem grows in clean composition rather
  than bolted into existing god-objects. The architectural rule
  (§4) is the load-bearing discipline; the test in
  `tests/integration/test_no_god_object_growth.py` enforces it.
- FSF gains a credible hierarchical-multi-agent surface that scales
  per local hardware budget. Use cases include: SW-track Engineer
  pattern at scale (one Architect + several specialized workers
  for refactor / review / type-check); security swarm scaling
  (one mid-tier Investigator + several distilled log_classifiers);
  Companion-tier task delegation (orchestrator + workers
  doing background research while operator interacts).
- Distillation manifests are content-addressed and rebuildable.
  Operator can re-distill from same manifest and get same DNA;
  audit chain has the receipt.
- Constitutional AI's three-layer composition (Bai et al. 2022)
  becomes a citation point for FSF — every distilled agent's
  constitution descends from a parent's via well-defined
  inheritance modes (clone/narrow/rebuild).

**Negative.**
- New top-level packages (`distillation/`, `orchestration/`) increase
  surface area. Each is its own subsystem with its own tests +
  audit + lifecycle. v0.4 absorption is meaningful work.
- MLX dependency adds install complexity. Operators on non-Apple-
  Silicon hardware get a "distillation not supported on this
  platform" message at v0.4. Cross-platform comes later.
- Distillation cost: each distillation run is minutes-to-hours
  GPU/Apple-NPU time. FSF's existing dispatch path is sub-second.
  Operator UX for long-running training jobs needs explicit design
  (probably ADR-0037 dashboard surface — sequence dependency).
- Behavioral regression risk if Verifier Loop (ADR-0036) isn't
  ready. §8 makes this a hard sequence dependency.

**Neutral.**
- The audit chain gains five new event types: `agent_distilled`,
  `distillation_manifest_proposed`, `distillation_manifest_ratified`,
  `swarm_orchestrator_birthed`, `swarm_hardware_budget_exceeded`.
  Modest volume.

## Cross-references

- ADR-0001 — DNA + content-addressing (§5 distilled-agents-have-new-DNA semantics)
- ADR-0008 — local-first model provider (§6 MLX-only commitment)
- ADR-0021 + amendment — genres (§2 three new genres)
- ADR-0030 — Tool Forge precedent (architectural rule §4 reasoning)
- ADR-0031 — Skill Forge precedent
- ADR-0033 — Security Swarm precedent (§2 three-tier shape)
- ADR-0035 — Persona Forge (§7 interaction)
- ADR-0036 — Verifier Loop (§8 hard dependency)
- ADR-0037 — Observability dashboard (§9 + long-running-job UX)
- ADR-0038 — companion harm model (orchestrator + workers inherit harm taxonomy)

## Open questions

1. **What's the canonical "narrow_task_scope" vocabulary?** §1's
   manifest example uses prose. A controlled vocabulary (
   "log_classification" / "diff_summarization" / "test_failure_triage"
   / etc.) would help operators reason about which workers exist.
   v0.4 ships with prose; v0.5 may add a vocabulary file.

2. **Per-genre persona_proposals_allowed for swarm genres?**
   ADR-0035 §5 open question 4 proposes a per-genre opt-in. Swarm
   genres should be `false` per §7. Pin this in the genres.yaml
   when v0.4 ships.

3. **Cross-orchestrator workers.** Can a worker distilled by
   orchestrator A be reused by orchestrator B's swarm? Lean yes —
   the worker's distillation manifest is independent of any
   particular swarm, and the registry-of-distilled-models is
   global per-deployment. But there's a cross-lineage concern:
   if A and B have different constitutional postures, sharing a
   worker pollutes the chain. Defer to v0.5 with a concrete
   policy decision.

4. **Distillation from frontier models.** v0.4 ships with
   local-only distillation per ADR-0008. If an operator has an
   API key for a frontier model and wants to distill from there,
   should we support it? Lean no — local-first is non-negotiable.
   Frontier-distillation would be a separate ADR with its own
   threat model.

5. **Worker retirement / model garbage collection.** Distilled
   models accumulate disk space. When does an operator-archived
   worker get its merged model deleted? Lean: explicit operator
   action via `/distillation/<manifest_id>/archive`. v0.5 may add
   automatic cleanup of archived-for-N-days workers.

6. **Hardware budget enforcement granularity.** §9's pre-spawn
   check is per-orchestration-manifest. What about across
   manifests on the same machine? Lean: track per-deployment via
   a new `swarm_active_models` table; refuse spawns that would
   exceed sum-across-manifests budget. v0.4 candidate.

## Implementation tranches (deferred to v0.4)

- **T1** — `distillation/manifest.py` + manifest schema +
  YAML serializer/parser. New table `distilled_models`. Pure-data
  shape; no MLX dependency yet. Tests verify manifest round-trip.

- **T2** — `orchestration/manifest.py` + `topology.py` validators.
  Tests verify topology vs. genre rules.

- **T3** — Three new genres in `genres.yaml`. Loader extends to
  parse them (no new mechanic; reuses ADR-0021 + amendment shape).
  Tests verify the genres load + the spawn compatibility rules
  hold.

- **T4** — `distillation/trajectory.py` — TrajectoryRecorder reads
  audit chain to extract parent traces. No MLX yet; just data
  collection. Operator-ratifies which traces enter a distillation
  manifest. Tests verify trajectory shape.

- **T5** — `distillation/trainer.py` — MLX subprocess wrapper.
  This is the heaviest tranche. Adds optional `[distillation]`
  extra in pyproject. Subprocess-only; no in-process import. Tests
  use mock subprocess; live run requires Apple Silicon.

- **T6** — `orchestration/dispatcher.py` — wraps `delegate.v1`
  for orchestrator-side task packaging. Tests with stub workers.

- **T7** — Daemon endpoints `/distillation/*` + `/orchestration/*`.
  CRUD + ratify/archive. Tests via TestClient.

- **T8** — Architectural rule test
  `tests/integration/test_no_god_object_growth.py`. Asserts
  distillation/* and orchestration/* import only from a hardcoded
  allow-list of core/* and daemon/* modules.

- **T9** — Frontend `/swarm-orchestration` tab (post-ADR-0037
  dashboard work). Orchestrator detail view + per-worker status
  + budget gauge + ratify/archive buttons.

- **T10** — Five new audit-event types wired through emitters.

T1+T2+T3 = "data shape + genre exists" milestone.
T4+T5 = "distillation pipeline runs end-to-end" milestone.
T6+T7 = "operator can drive a swarm" milestone.
T8 = architectural-rule guard.
T9+T10 = polish + observability.

Estimated v0.4 cost: ~3-4 weeks of substantive work, post-ADR-0036
landing.

## Bibliography

The literature survey grounding ADR-0039 (received 2026-05-01).
Citations are by relevance to the proposed subsystem; full
context for each in the decision sections above.

### Primary references

- **Constitutional AI: Harmlessness from AI Feedback** —
  Bai et al., Anthropic 2022 — arXiv:2212.08073. Foundation for
  FSF's constitution concept; cited in §1 and the consequences
  section.
- **Orca: Progressive Learning from Complex Explanation Traces of
  GPT-4** — Mukherjee et al., Microsoft 2023 — arXiv:2306.02707.
  Strongest paper for FSF's distillation use case; cited in
  Context.
- **AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent
  Conversation** — Wu et al. 2023 — arXiv:2308.08155. Multi-agent
  conversation patterns analog to delegate.v1.
- **MetaGPT: Meta Programming for a Multi-Agent Collaborative
  Framework** — Hong et al. 2023 — arXiv:2308.00352. Structured
  role assignment formalism.
- **Voyager: An Open-Ended Embodied Agent with Large Language
  Models** — Wang et al. 2023 — arXiv:2305.16291. Skill library
  via LLM-generated code; precedent for FSF's Tool Forge / Skill
  Forge.

### Distillation technique references

- **Distilling Step-by-Step!** — Hsieh et al. 2023 —
  arXiv:2305.02301. Core distillation technique.
- **Specializing Smaller Language Models towards Multi-Step
  Reasoning** — Fu et al. 2023 — arXiv:2301.12726. CoT
  distillation into smaller models.
- **Lion: Adversarial Distillation of Proprietary Large Language
  Models** — Jiang et al. 2023 — arXiv:2305.12870. Adversarial
  imitation pattern.
- **Phi series** — Gunasekar et al., Microsoft 2023 —
  arXiv:2306.11644. Small-model-quality benchmark.

### Memory architecture references (informs hand-off with ADR-0027)

- **MemGPT: Towards LLMs as Operating Systems** —
  Packer et al. 2023 — arXiv:2310.08560. Hierarchical memory
  validates FSF's three-layer structure.
- **Generative Agents: Interactive Simulacra of Human Behavior** —
  Park et al. 2023 — arXiv:2304.03442. Memory stream + reflection
  + planning architecture.

### Foundational references (informs FSF's broader design)

- **ReAct** — Yao et al. 2022 — arXiv:2210.03629. Tool execution
  trace formalism.
- **Reward Modeling for Mitigating Overoptimization in RLHF** —
  Gao et al. 2022 — arXiv:2210.10760. Trait-slider →
  behavior-mapping reward-hacking analog.
- **Principle-Driven Self-Alignment** — Sun et al. 2023 —
  arXiv:2305.03047. Rule-based alignment without RLHF.
- **AgentBench** — Liu et al. 2023 — arXiv:2308.03688. Benchmark
  framing for ADR-0023 (queued).
- **Gorilla** — Patil et al. 2023 — arXiv:2305.15334. API
  taxonomy methodology.
- **ToolBench / ToolLLM** — Qin et al. 2023 — arXiv:2307.16789.
  Tool-chaining failure modes.

### Multi-agent coordination references

- **Emergent Cooperation and Strategy Adaptation with Large
  Language Models** — Lorè & Heydari 2023 — arXiv:2310.06927.
  Theoretical grounding for emergent multi-agent behavior.
- **Dynamic LLM-Agent Network** — Liu et al. 2023 —
  arXiv:2310.02170. Static-vs-dynamic topology tradeoffs.

## Attribution

This ADR's catalysts (2026-05-01):
- Internal-research analysis proposing the hierarchical 1-large +
  N-small pattern with realistic M4 hardware estimates.
- Internal-research literature survey grounding the pattern
  against published research and surfacing the bibliography
  above.

The architectural rule (§4 anti-god-object discipline; "no god
objects, grow new tree with branches grounded by a solid feature")
is the orchestrator's (Alex's) explicit constraint added at design
time. The implementation discipline this rule enforces — every new
significant subsystem gets its own grounding manifest +
content-addressed artifact tree, parallel to existing trees — is
the canonical FSF pattern this ADR formalizes.

Constitutional AI (Bai et al. 2022) is the published prior art
ADR-0004 (constitution builder) descends from; this ADR's §5
distilled-agent-constitution-inheritance modes (clone/narrow/rebuild)
are the FSF-specific application to distillation. Orca's
explanation-trace technique grounds §1's trajectory-recorder
design.

See `CREDITS.md` for the project's full attribution discipline.
