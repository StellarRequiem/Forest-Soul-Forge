# ADR-0023 — Benchmark Suite

- **Status:** Proposed
- **Date:** 2026-04-25
- **Supersedes:** —
- **Related:** ADR-0005 (audit chain — benchmark events live here), ADR-0006 (registry as derived index over canonical artifacts), ADR-0008 (local-first model provider — benchmarks must be comparable across backends), ADR-0017 (LLM voice — quality over time becomes measurable), ADR-0018 (tool catalog — `benchmark_run.v1` joins it), ADR-0020 (character sheet `benchmarks` section), ADR-0021 (genres carry per-genre battery), ADR-0022 (memory — benchmarks don't pollute working memory).

## Context

The Forge produces agents we configure with traits, equip with tools, and bind by constitutions. The configuration-and-equipment side is now well-served. The "is this agent actually good at its job" side is unanswered. An operator who tunes a network_watcher's `vigilance` from 70 to 85 has no way to know if the resulting agent detects more anomalies, takes longer to detect them, or simply produces more false positives. Without measurement, every trait knob is a knob into the dark.

The challenge is that "good at its job" varies by genre. An Observer's job is detection; an Investigator's job is correlation; a Communicator's job is faithful conveyance; a Companion's job is consistent presence and boundary-keeping. There is no single benchmark that measures all of those. **What we need is a per-genre battery** — each genre has its own canonical set of test scenarios with its own scoring criteria, and an agent's score on its battery is the operator's primary quality signal.

Three additional motivations:

1. **The LM Studio swap (per `dev-tools.md`) is half a tool.** Swapping the model backend from Ollama to LM Studio is currently a "does it still work" check. With benchmarks, it becomes a "is the new backend better, worse, faster, or different" measurement. The swap path matures into a tuning tool.

2. **Trait tuning becomes empirical.** Adjusting `caution` from 70 to 85 produces a measurable delta on the Observer's detection-vs-false-positive curve. Operators tune by data, not by feel.

3. **Drift becomes detectable.** An agent whose benchmark scores erode over weeks (likely due to consolidated memory drift per ADR-0022's downside section) shows it on the character sheet. The Guardian-class agent ADR-0021 hints at can use benchmark scores as a drift signal.

## Decision

### Per-genre benchmark batteries

Each genre has a canonical battery of scenarios. The seven v1 batteries:

```
Observer        signal_detection         — detection rate on a labeled traffic dataset
                false_positive_rate      — clean-traffic false alarms
                time_to_detection        — latency from event to alert
                tool_invocation_focus    — fraction of tool calls relevant to the signal

Investigator    correlation_recall       — N events known to correlate; investigator
                                            finds K of them
                hypothesis_quality       — generated hypotheses, judged by rubric
                alternative_consideration — does the agent consider non-obvious causes?

Communicator    conciseness              — output length normalized by input information
                accuracy_preservation    — facts in output match facts in input
                audience_calibration     — register matches declared audience tag

Actuator        pre_action_verification  — fraction of actions that get human approval
                                            BEFORE execution (should approach 100%)
                false_execution          — actions taken without expected approval
                escalation_appropriateness — escalates when uncertain (rubric)

Guardian        refusal_accuracy         — correctly blocks N policy-violating outputs
                false_refusal_rate       — over-blocks safe outputs
                policy_alignment         — refusal reasons match constitution.policies

Researcher      source_diversity         — fraction of distinct sources in citations
                citation_accuracy        — citations resolve to claimed content
                synthesis_quality        — rubric on the produced summary

Companion       empathy_alignment        — rubric on emotional acknowledgment
                boundary_keeping         — refuses out-of-scope user requests appropriately
                retention_fidelity       — memory_recall returns correct prior-session
                                            content (uses ADR-0022 memory subsystem)
```

Per-genre batteries are intentionally **archetype-coherent** — they measure the qualities a genre's mission depends on, not generic benchmarks borrowed from another community.

### Scenario shape

Each benchmark fixture is a YAML file under `benchmarks/{genre}/{name}.v{N}.yaml`:

```yaml
fixture_id: signal_detection.v1
genre: observer
name: signal_detection
version: "1"
description: |
  Replay 50 minutes of network traffic with N labeled anomalies.
  Score: detection rate (true positives / total positives) over the
  agent's emitted findings.

inputs:
  - { type: traffic_replay, source: fixtures/observer/traffic_50min.pcap }
  - { type: labels, source: fixtures/observer/traffic_50min_labels.json }

scoring:
  type: numerical
  function: detection_rate     # built-in scorers: detection_rate, rubric, latency,
                                # composite, exact_match. Operator can register more.
  threshold:
    pass: 0.7                  # below this, agent fails the fixture
    excellent: 0.9             # above this, agent excels

baseline:
  random_agent_score: 0.05
  templated_agent_score: 0.40  # an agent with no LLM enrichment, kit-matched tools
  human_analyst_score: 0.85    # rough reference point

provenance:
  fixture_authored_at: "2026-04-25"
  fixture_author: "Forest seed catalog"
  data_license: "synthetic — generated for benchmarking, no real user traffic"
```

Three classes of scoring function:
- **Numerical** (detection_rate, latency_ms, false_positive_rate, etc.) — deterministic.
- **Rubric** — LLM-as-judge against a structured criteria file. The judge runs locally per ADR-0008 unless explicitly switched. The rubric file lives alongside the fixture.
- **Composite** — weighted combination of the above. Used for batteries where the headline score is meaningful only as an aggregate.

Fixtures are **versioned** (`signal_detection.v1`, `signal_detection.v2`). Old fixtures are NEVER edited in place — same discipline as the tool catalog (ADR-0018). v1 stays exact; v2 is a parallel entry. Agents whose results reference v1 stay reasonable-about even after v2 lands.

### Run lifecycle

```
POST /agents/{instance_id}/benchmark
  body: { fixtures: [...], model_override: {...} }
  response: { run_id, started_at, status: queued|running|complete }

GET /agents/{instance_id}/benchmark/{run_id}
  response: { run_id, fixtures: [...per-fixture results...], aggregate_score, ... }

GET /agents/{instance_id}/benchmark
  response: list of recent runs with aggregate scores
```

A run on POST queues the fixture executions, runs them sequentially (parallel is a follow-on), and writes results when complete. Each fixture's result captures:
- Score
- Pass / fail / excellent flag against thresholds
- Runtime + tokens used + memory burn
- Model backend (provider + model tag, exactly)
- Reproducibility metadata (seeds, fixture version, agent's constitution_hash)

### Audit chain integration

```
benchmark_run_started        — { run_id, fixtures: [ids], model_backend }
benchmark_fixture_complete   — { run_id, fixture_id, score, pass_flag,
                                 runtime_ms, tokens_used }
benchmark_run_complete       — { run_id, aggregate_score, pass_count,
                                 fail_count, excellent_count }
benchmark_run_aborted        — { run_id, reason }
```

Audit-chain events carry the score + metadata, NOT the model output that produced the score (privacy + size). The full per-fixture output lives in `data/benchmark_runs/{run_id}/` as JSON files — readable, diff-able, but not in the chain.

### Storage layout

```
benchmarks/
    {genre}/
        {fixture_name}.v{N}.yaml       # canonical fixture definition
        rubrics/
            {fixture_name}.v{N}.yaml   # rubric criteria for rubric-scored fixtures
        fixtures/
            {fixture_name}/...         # input data files (pcaps, labeled docs, etc.)

data/
    benchmark_runs/
        {run_id}/
            metadata.json               # run-level info
            {fixture_id}.result.json    # per-fixture detail
            {fixture_id}.transcript.txt # raw model output (for debugging / rubric replay)
```

`benchmarks/` lives in the repo (canonical, version-controlled). `data/benchmark_runs/` is bind-mounted runtime state, rebuildable from the audit chain (events have run_id + score; full transcripts can be regenerated by re-running). Per ADR-0006 the audit chain is the canonical record; the run files are convenience.

### Registry mirror — `agent_benchmark_results`

A new registry table holds the **latest** score per (instance_id, fixture_id):

```sql
CREATE TABLE agent_benchmark_results (
    instance_id TEXT NOT NULL,
    fixture_id  TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    score       REAL NOT NULL,
    pass_flag   TEXT NOT NULL,        -- 'pass' | 'fail' | 'excellent'
    model_backend TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (instance_id, fixture_id)
);
```

Rebuildable from the audit chain (replay `benchmark_fixture_complete` events). Same pattern as the existing registry tables.

### Comparable-across-backends

A run records the exact `model_backend: "local:llama3.2:1b"` or `"local:llama3.1:8b"` or `"frontier:gpt-4o-mini"`. Comparing two runs of the same fixtures on the same agent with different backends gives a backend-quality delta.

The LM Studio swap path documented in `dev-tools.md` becomes a benchmarking tool: birth a network_watcher, run its battery on Ollama+llama3.2:1b, swap to LM Studio+qwen2.5-7b-instruct, re-run, compare.

### Per-genre performance budget

Each genre's `genres.yaml` (per ADR-0021) gains a `performance_budget` field:

```yaml
genres:
  observer:
    performance_budget:
      battery_pass_threshold: 0.7   # aggregate score required to be deployable
      flagged_below: 0.5            # operator warning threshold
      avg_latency_ms_max: 200
      max_tokens_per_session: 8000
```

Character sheet (ADR-0020) `benchmarks` section pulls from `agent_benchmark_results` and shows current vs. budget — green when above pass, amber between flagged and pass, red below flagged.

### Tool catalog: `benchmark_run.v1`

```yaml
benchmark_run.v1:
  name: benchmark_run
  version: "1"
  description: |
    Run a benchmark fixture (or full battery) against a target agent
    and return the aggregate score + per-fixture detail. Used by
    Guardian-class agents to assess other agents periodically and
    flag drift.
  side_effects: external          # produces durable benchmark run records
                                   # + audit events; treated as external
  archetype_tags: [guardian]
  input_schema:
    type: object
    required: [target_instance_id]
    properties:
      target_instance_id: { type: string }
      fixtures: { type: array, items: { type: string } }
      model_override: { type: object }
```

`side_effects: external` because benchmark runs produce durable state (run files, audit events) and trigger model invocations — operator should approve before a Guardian benchmarks every agent in the fleet. The constraint policy (ADR-0018 T2.5) handles this via the always-rule for `external`.

## Consequences

**Upside:**

- **Quality becomes measurable.** Trait tuning, model swaps, prompt iterations all produce comparable numbers. Operators stop tuning by feel.
- **Drift becomes detectable.** Periodic re-runs of the battery against a long-running agent show whether its quality is stable, improving (consolidated memory helping), or degrading (drift). Guardian-class agents can use benchmark scores as a structured signal.
- **Backend choices become empirical.** Ollama vs LM Studio vs frontier becomes "which combination produces the best aggregate score on the battery, at what latency cost." The local-first guarantee from ADR-0008 holds; the choice within local-first is data-driven.
- **Versioned fixtures preserve the audit trail.** An agent's results that reference `signal_detection.v1` stay meaningful even after v2 lands. Same versioning discipline as the tool catalog.
- **Genre-coherent batteries.** Each genre tests what it actually does — not borrowed metrics that don't fit. The Companion battery's `retention_fidelity` test depends on the memory subsystem (ADR-0022) working correctly; the integration is intentional and the benchmark serves as a property test.

**Downside:**

- **Fixture authoring is real work.** Each fixture needs input data, scoring criteria, baseline numbers, and (for rubrics) judge prompts. The seven batteries above sketch ~25 fixtures total. Authoring all of them is a multi-week effort. **Mitigation: ship one or two fixtures per genre in v1; expand over time. The framework lands; the fixture library grows.**
- **Rubric judging burns model time.** LLM-as-judge fixtures invoke the local provider (or frontier if explicitly allowed). A full battery run could take many minutes on consumer hardware, especially with a small model. **Mitigation: defaults bias toward numerical scoring; rubric is opt-in per fixture.**
- **Benchmark runs accumulate disk.** Each run produces JSON + transcript files. Long-running operators with daily benchmarks and many agents could see GB-scale accumulation. **Mitigation: retention policy on `data/benchmark_runs/` — keep the latest N runs per agent; older runs reconstructible from the audit chain.**
- **Synthetic data risks.** v1 fixtures use synthetic traffic / synthetic logs / synthetic scenarios. Real-world performance may differ. **Mitigation: real-world telemetry-derived fixtures (with privacy preservation) are a strong follow-on. v1 ships with synthetic, documented as such.**
- **Genre-specific complexity.** Companion's `retention_fidelity` requires the memory subsystem (ADR-0022) to be implemented before that battery can run. Cross-ADR dependencies are real. **We accept this — Companion's benchmarks land when ADR-0022 lands; the schema makes room now so consumers don't need to be rewritten later.**

**Out of scope for this ADR:**

- **Continuous benchmarking** (run battery on every commit, track over time as a graph). v1 is on-demand. Continuous runs are obvious follow-on.
- **A/B testing across agent populations** (spawn 10 variants with slightly different traits, run battery on each, pick winner). The infrastructure exists in spirit; productizing it is a separate design pass.
- **External benchmark integration** (HumanEval, BFCL, MMLU). These measure model capability, not Forest-agent capability. Different question. Defer.
- **Per-fixture cost / token budget enforcement.** Today fixtures cost what they cost; if a rubric judge runs long, no built-in stop-loss. Add cost ceilings if they become a problem.
- **Public benchmark result publication.** Operators may want to publish "this agent scored X on the Observer battery." Privacy + reproducibility considerations defer this — agents are local-first; their scores describe agents on a specific operator's hardware. Cross-operator standardization is far-future.

## Open questions

1. **Where does the LLM-as-judge for rubric scoring run?** Two options: (a) the agent being benchmarked judges itself (cheap, but biased — model judges its own output favorably); (b) a separate Guardian-class agent judges (independence, but requires Guardian infrastructure). **Lean (b) once Guardian-class is mature; (a) as MVP fallback with a clear "self-judged" flag in the result.**

2. **How are baselines authored?** A new fixture's baseline numbers (random_agent, templated_agent, human reference) need to come from somewhere. **Lean: at fixture authoring time, run a templated agent against the fixture once and record the score as the templated_agent baseline. Random / human reference are operator-supplied estimates.**

3. **Should benchmark runs themselves write to memory?** If a Guardian-class agent benchmarks 50 agents weekly, each run could produce a memory entry ("agent X scored 0.83 this week"). Useful for drift detection. **Lean yes, but only consolidated memory entries** — no episodic-level pollution, just summaries the Guardian's consolidation job produces.

4. **Reproducibility under stochastic models.** Many local models are non-deterministic without explicit seeds. Rubric scoring + non-deterministic generation = non-reproducible numerical scores. **Mitigation: each run records seed + temperature + sampling params; reruns with the same params are considered "same run" for diff purposes; runs without seed-pinning are flagged "not exactly reproducible."**

5. **Multi-tenant vs single-tenant battery library.** Different operators may want different fixtures. The repo ships a `forest/` battery; operators add `mycompany/` fixtures. Same versioning rules apply; loader merges. **Defer until there's a real second operator.**

## Implementation tranches

- **T1** — fixture YAML schema + loader + validation. Scoring functions module (numerical first; rubric is T4). Tests.
- **T2** — `POST /agents/{id}/benchmark` endpoint. Synchronous (queues + runs sequentially in-process). Audit chain `benchmark_run_*` events. Per-run files written to `data/benchmark_runs/{run_id}/`.
- **T3** — `agent_benchmark_results` registry table + ingest. Read endpoints (`GET .../benchmark` listing, `GET .../benchmark/{run_id}` detail).
- **T4** — Rubric scoring function. Judge invokes the local provider; rubric prompt + scoring criteria from per-fixture `rubrics/` files.
- **T5** — Per-genre batteries — author 2 fixtures per genre for an initial 14-fixture library across the 7 genres. Document fixture authoring conventions.
- **T6** — Performance budget per genre (genres.yaml `performance_budget` field). Character sheet (ADR-0020) `benchmarks` section pulls from registry + budget for green/amber/red.
- **T7** — `benchmark_run.v1` tool added to catalog. Guardian-class agents can run benchmarks on other agents.
- **T8** — Background batch runs (run battery weekly on every active agent; alerting when scores drop below `flagged_below`).
- **T9** — Cross-backend comparison view (frontend: pick agent + battery + two backend choices, see side-by-side scores).
- **T10** — Real-data fixture authoring path (telemetry-derived with privacy preservation). Long-tail.

T1+T2+T3+T5 is the "agents can be measured" milestone. T4 unblocks rubric-scored fixtures. T6+T7+T8 wire ongoing measurement into the rest of the system. T9+T10 are polish.
