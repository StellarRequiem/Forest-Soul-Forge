# Substrate performance baseline — 2026-05-20

**Driver:** B440 — first substrate-perf benchmark run
**HEAD:** `dcd1d59` (B439 — launchd wiring-audit installer + 15/15 harness green)
**Hardware:** Mac mini 2024, M4, 16GB RAM
**Daemon:** uvicorn on `http://127.0.0.1:7423` (single worker)

## Baseline numbers

### Latency benchmarks

| Benchmark | endpoint | samples | p50 | p95 | p99 | mean | max |
|---|---|---:|---:|---:|---:|---:|---:|
| `dispatch_latency` | `GET /agents?limit=200` | 50 | 0.80 | 1.49 | 5.33 | 0.98 | 5.33 |
| `audit_chain_read` | `GET /audit/tail?n=50` | 50 | 74.68 | 78.61 | 82.00 | 73.87 | 82.00 |
| `registry_read` | `GET /tools/catalog` | 50 | 0.89 | 1.75 | 2.45 | 1.01 | 2.45 |

All values in milliseconds. Loopback measurements; no network in path.

### Throughput

| Benchmark | endpoint | ops | window | ops/sec |
|---|---|---:|---:|---:|
| `healthz_throughput` | `GET /healthz` | 393 | 3.00s | 131 |

Serial-client ceiling (one request at a time, blocking).

## Observations

**dispatch_latency + registry_read are both sub-2ms p95.** The
load-bearing client read path is essentially "as fast as
loopback HTTP + a single SQLite SELECT plus YAML serialization."
Nothing to optimize here without much higher load.

**audit_chain_read is two orders of magnitude slower.** 74.68ms
p50 for `/audit/tail?n=50` is the per-event signature-verification
path of ADR-0049 lit up across 50 entries. ~1.5ms per signature
verification, 50 verifications per call.

This is *expected behavior* — verification IS the load — but
worth flagging as the load-bearing latency bottleneck for any
client that wants to scroll long audit tails. Three candidate
future optimizations:

1. **Signature verification caching** — cache (entry_hash →
   verified-bool) per-process; subsequent reads of the same entry
   skip verification. Trades memory for latency. Cheap; would
   bring chain_read p50 toward registry_read's <1ms.

2. **ADR-0073 chain segmentation** — old segments are sealed +
   verified once; reads of sealed segments skip per-entry
   verification because the segment hash certifies the contents.
   Architectural; bigger lift.

3. **Lazy-verify with cursor flag** — `/audit/tail?verify=false`
   for "just give me the JSON, I'll verify later if I care."
   Mostly useful for read-only UIs (audit timeline tab).

None of the above are needed today. Documenting so we know the
shape of the next optimization if `audit_chain_read` p95 ever
exceeds ~150ms (would suggest either chain growth or verifier
regression).

**healthz_throughput = 131 ops/sec serial.** This is the absolute
ceiling for blocking serial-client load on this hardware. Real
clients will see lower because they do meaningful work between
calls.

## Methodology notes

- **Warmup:** 5 untimed calls per benchmark before measurement.
- **Sample size:** 50 (latency) / 3-second window (throughput).
- **Serial only.** No concurrency. Load-test scaffolding is
  intentionally out of scope per
  `dev-tools/benchmark/README.md`.
- **Provenance:** results include the git SHA so future runs can
  be compared meaningfully.

## Reproducing

```
bash dev-tools/benchmark/run-benchmarks.command
```

or

```
python3 dev-tools/benchmark/run_substrate_perf.py
```

Output lands at `data/test-runs/benchmark-substrate-perf-<ts>/`
with `results.json` (full) + `summary.md` (table).

## Scope clarification

This benchmark suite is **substrate performance** — daemon
latency, audit chain read speed, registry read speed, healthz
throughput. It is **not** the per-genre quality battery system
specified in ADR-0023, which remains Proposed and would measure
agent behavioral quality (detection rates, rubric scores, etc.).

Both are real benchmarks; both are useful; both will eventually
ship. This commit lands the cheap, kernel-free one.

## Cross-references

- `dev-tools/benchmark/README.md` — methodology + interpretation
- `dev-tools/benchmark/run_substrate_perf.py` — measurement code
- `docs/decisions/ADR-0023-benchmark-suite.md` — per-genre
  quality battery scope (Proposed; separate scope)
- `docs/decisions/ADR-0049-per-event-signatures.md` — root cause
  of `audit_chain_read` latency
- `docs/decisions/ADR-0073-audit-chain-segmentation.md` —
  optimization vector if chain read latency ever needs to drop
