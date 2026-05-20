# Substrate performance benchmark

A lightweight measurement tool for the load-bearing latency and
throughput characteristics of the FSF daemon as it runs.

## What this is — and isn't

**This is** the "system perf" side of benchmarking: how fast does
the dispatcher return, how fast does the chain read, how fast does
the registry serve typical client reads. Pure HTTP-client timing
against the running daemon. Lives entirely in `dev-tools/`. No
kernel changes; no schema changes; no new HTTP routes.

**This is NOT** ADR-0023's per-genre quality battery system. That
ADR remains Proposed and is much larger scope — it would add HTTP
endpoints (`POST /agents/{id}/benchmark`), audit-chain event types
(`benchmark_run_started`, `benchmark_fixture_complete`, etc.), a
new registry table (`agent_benchmark_results`), and a fixture YAML
schema, all to measure **agent behavioral quality** rather than
substrate speed. The two scopes are complementary; this one is the
cheap measurement side that can ship now.

## Usage

```
bash dev-tools/benchmark/run-benchmarks.command
# OR directly:
python3 dev-tools/benchmark/run_substrate_perf.py
```

Daemon must be running at `http://127.0.0.1:7423` (override with
`FSF_DAEMON_URL`). Reads `FSF_API_TOKEN` from `.env`.

Output lands in
`data/test-runs/benchmark-substrate-perf-<timestamp>/`:

- `results.json` — full structured results (per-benchmark
  samples + percentiles + provenance)
- `summary.md` — human-readable summary table

## Benchmarks

| Name | Endpoint | What it measures | Interpretation |
|---|---|---|---|
| `dispatch_latency` | `GET /agents?limit=200` | Typical client-facing list read | p95 > ~50ms → registry / constitution-parse slowing |
| `audit_chain_read` | `GET /audit/tail?n=50` | Chain reader + signature verifier per entry | Stable p95 → ADR-0049 signing perf is healthy |
| `registry_read` | `GET /tools/catalog` | Heaviest read most clients make (68+ tool catalog) | Sets upper bound for tool-catalog-aware UI tab load |
| `healthz_throughput` | `GET /healthz` over 3s | Absolute serial-client ceiling | If <80% of baseline, event-loop / socket layer regressed |

## Methodology

- **Warmup:** 5 untimed calls before measurement, so cold-cache
  effects don't pollute the percentile distribution.
- **Sample size:** 50 for latency benchmarks. Reasonable for
  p50/p95/p99 stability; not enough for p99.9 (we don't bother).
- **Throughput window:** 3 seconds for the `/healthz` ceiling
  benchmark. Whole suite finishes under a minute.
- **No concurrency:** serial requests only. The numbers are
  serial-client baselines, not load-test results. (Load testing
  is a different question — out of scope.)
- **Provenance:** every result records the git SHA at run time so
  baselines can be compared across substrate changes.

## When to run

- Before/after any substrate change that touches the dispatcher,
  audit chain writer, or registry path.
- Quarterly as a drift sanity check — if `dispatch_latency` p95
  has crept up 2x since the previous baseline, something accreted.
- As part of release-readiness gates if/when ADR-0023 lands its
  release-gate machinery (the two can share the same output dir).

## Baselines

Run-to-run variance on consumer hardware can be 10-20% from
process scheduling alone. Trust trends, not single values. The
first captured baseline is at
`docs/audits/2026-05-20-substrate-perf-baseline.md`.

## Future work (deliberately out of scope)

- **Concurrent-client load test** — measure under N parallel
  clients. Different question; needs proper load-test scaffolding.
- **Tool-dispatch latency** — `POST /agents/{id}/tools/call` with a
  cheap deterministic tool. Adds side effects to the chain on
  every run; needs operator opt-in.
- **End-to-end skill execution latency** — across a full skill
  run with N steps. Would partially overlap ADR-0023's quality
  measurement; defer until that ADR's scope is decided.
