# FSF dispatch overhead benchmark plan

**Date:** 2026-05-01
**Author:** Forest Soul Forge harness
**Purpose:** Specify a benchmark Burst that measures FSF's actual per-dispatch + per-audit overhead before ADR-0039 (Distillation Forge / Swarm Orchestrator) implementation begins.
**Status:** Plan; the benchmark itself is a future Burst.

## Why this benchmark precedes ADR-0039 implementation

ADR-0039 §10 explicitly punts on the question: "is FSF's audit-heavy
dispatch path fast enough to support 1+M+N swarm topologies on a
16GB Mac Mini?" The internal analysis estimated 10-25 parallel agent
tasks/sec; my analytical read suggested 2-5/sec under FSF's actual
overhead. **Both numbers are guesses.**

A measured number lets us:
1. Validate or reshape the proposed swarm topology in ADR-0039.
2. Identify hot spots in the dispatch path that would benefit from
   optimization independent of the swarm work.
3. Set realistic operator expectations for v0.4+ planning.

If the measurement comes back at 1 task/sec, the Swarm Orchestrator
pattern's viable shape is much narrower than 1+M+N (maybe 1+1+2). If
8+ task/sec, the proposed shape lands as designed. **Either way the
ADR-0039 implementation arc benefits from knowing the number first.**

## What to measure

### Primary metrics

1. **Quiet-load per-dispatch latency** — single agent dispatching
   one tool call, end-to-end (request → response):
   - p50 / p90 / p99
   - Broken down by pipeline step (HardwareQuarantine, TaskUsageCap,
     ToolLookup, ArgsValidation, ConstraintResolution, PostureOverride,
     GenreFloor, InitiativeFloor, CallCounter, ApprovalGate, +
     audit chain append, + tool execute).
   - Establishes the no-contention floor.

2. **Audit chain serialization cost** — N parallel dispatches
   contending for the single-writer SQLite lock:
   - Throughput at N = 1, 2, 4, 8, 16 concurrent dispatches.
   - p50 / p99 latency at each N.
   - Identifies where the audit lock becomes the bottleneck.

3. **Memory recall cost** — `memory_recall.v1` against agents with
   varying memory sizes:
   - 100 entries / 1k entries / 10k entries / 100k entries
   - Quiet load + concurrent (4 agents recalling simultaneously)
   - Particularly relevant: ADR-0027-am surface_contradictions +
     staleness_threshold_days passes — both make additional reads.

4. **Genre + initiative gate costs** — overhead per call from the
   two YAML reads (`_load_resolved_constraints` +
   `_load_initiative_level`):
   - Cold-cache vs warm-cache (file system cache effect).
   - Per-step microbenchmark vs the rest of the pipeline.

### Secondary metrics

5. **Cross-agent delegate.v1 chain depth** — orchestrator-style
   call: A → delegate(B) → delegate(C). Measure end-to-end at
   depths 1, 2, 3, 4 (mirrors ADR-0033's canonical 4-level chain).

6. **Voice renderer latency** — when ADR-0017 voice rendering
   is enabled, how long does birth take? Affects how often
   operators tolerate the enrich-narrative path.

7. **Genre-floor-step impact under InitiativeFloor refusal** —
   does an early refusal save measurable downstream cost (vs.
   running the whole pipeline before refusing)?

## How to measure

### Test harness

A new `tests/benchmark/` directory parallel to existing
`tests/unit/` and `tests/integration/`. Modules:

- `tests/benchmark/conftest.py` — fixtures: pre-warmed registry +
  N seeded agents + N pre-built constitution YAMLs on tmp_path.
- `tests/benchmark/bench_dispatch_quiet.py` — primary metric 1.
  Uses `time.perf_counter_ns()` deltas around each pipeline step
  (instrumented via a benchmark-only `BenchmarkObservingPipeline`
  wrapper that records per-step timing without changing
  semantics).
- `tests/benchmark/bench_audit_serialization.py` — primary metric 2.
  Uses `concurrent.futures.ThreadPoolExecutor` to drive N parallel
  dispatches against a shared registry; measures aggregate
  throughput via successful events / wall time.
- `tests/benchmark/bench_memory_recall.py` — primary metric 3.
- `tests/benchmark/bench_gate_costs.py` — primary metric 4.
- `tests/benchmark/bench_delegate_chain.py` — secondary metric 5.
- `tests/benchmark/bench_voice_renderer.py` — secondary metric 6.

Each module prints results in a structured format
(`metric: <name>; p50=<ns>; p90=<ns>; p99=<ns>; n=<count>`)
that's grep-friendly + machine-parseable for CI tracking.

### Instrumentation discipline

The benchmark code MUST NOT change the dispatch path's semantics.
Per ADR-0009 audit-chain integrity, we don't allow shadow-paths
that bypass audit. So:

- Benchmark instrumentation lives in
  `tests/benchmark/instrumentation.py` as a `BenchmarkObservingPipeline`
  that wraps `GovernancePipeline` and times each step's
  `evaluate()` via subclass + decorator pattern.
- The wrapper is benchmark-only — never installed into a daemon
  in production.
- Audit chain emissions still happen in full; we measure the cost,
  we don't skip it.
- Baseline measurement runs the un-instrumented pipeline to get
  the "no overhead from instrumentation" reference point. The
  instrumentation overhead itself gets subtracted.

### Hardware target

- **Primary:** M4 Mac Mini 16GB (matches Alex's stock hardware +
  the analysis's hardware assumption). Reproducible per-machine via
  `system_profiler SPHardwareDataType` capture into the bench
  output.
- **Secondary:** any other operator-running hardware with > 16GB
  unified memory or PCIe NVMe — useful to disambiguate "limited by
  CPU" vs "limited by I/O" but not required for v0.4 planning.

## Sequencing

The benchmark Burst is a single session of work:

1. Build `tests/benchmark/conftest.py` + `instrumentation.py`.
2. Implement primary metrics 1-4 (quiet, serialization, memory,
   gate cost).
3. Implement secondary metrics 5-6 if time permits; otherwise
   defer.
4. Run on Alex's M4 Mac Mini. Capture results in a date-stamped
   audit doc (`docs/audits/2026-05-XX-fsf-dispatch-overhead-results.md`).
5. Update ADR-0039 §10 with the measured numbers + decide whether
   the proposed 1+M+N topology is viable.

Estimated cost: 1 substantive session (build) + 1 measurement
session (run + analyze).

## Success criteria

The benchmark Burst is "done" when:

1. All four primary metrics produce measurements with reasonable
   confidence (≥1000 samples per metric, low variance).
2. Per-step pipeline cost breakdown is published (where the
   dispatch overhead actually lives).
3. The audit-serialization curve shows the inflection point (at
   what N does throughput plateau / decline?).
4. The audit doc is committed alongside the bench results,
   citable from ADR-0039 §10 + future v0.4 planning.

## Possible outcomes + their consequences

### Outcome A: 5+ tasks/sec sustained at N=4 parallel
Proposed 1+M+N topology is viable as designed. ADR-0039
implementation proceeds without topology revision. Realistic v0.4
swarm: 1 orchestrator + 2 controllers + 4-6 workers.

### Outcome B: 2-5 tasks/sec sustained at N=4 parallel
Topology needs gentle adjustment. Realistic v0.4 swarm: 1
orchestrator + 1 controller + 3-4 workers. The "10-25 tasks/sec"
internal-analysis estimate is invalidated; ADR-0039 §10 documents
the realistic ceiling.

### Outcome C: <2 tasks/sec sustained at N=4 parallel
The audit chain is more of a bottleneck than expected. ADR-0039's
proposed swarm shape is operationally limited. Two paths:
- (i) Keep the topology small (1+1+2). Useful but limited.
- (ii) Pursue audit-chain optimization separately as its own ADR
  candidate (batched commits, async append, etc.) before swarm
  work. Adds an architectural surface that wasn't on the roadmap.

### Outcome D: Variance is high; the average doesn't converge
Likely cause: garbage collection, file system cache thrash,
or other non-FSF-overhead noise. Re-run after diagnosing; possibly
need OS-level tuning notes in the bench output.

## What this benchmark is NOT

- Not a load test of any real workload. We're measuring overhead,
  not "how many real tasks per second can FSF do."
- Not a comparison vs other agent frameworks. AutoGen / LangChain /
  etc. have different overhead profiles; benchmarking them isn't
  what this Burst is for.
- Not a security review. The benchmark only measures dispatch
  overhead, not the security properties (audit integrity, sudo
  helper safety, etc.) covered elsewhere.
- Not a production readiness gate. v0.2 / v0.3 ship without this
  benchmark; it's specifically scoped to inform v0.4 ADR-0039 work.

## Cross-references

- ADR-0039 §10 — pointer to this plan; the consumer of this
  benchmark's outputs.
- ADR-0019 — tool execution runtime; the path being measured.
- ADR-0009 — audit chain; the dependency that's load-bearing on
  serialization cost.
- ADR-0023 — benchmark suite (Proposed; broader scope). This plan
  is a focused subset; ADR-0023's eventual implementation may
  absorb these benchmarks or build separately.
