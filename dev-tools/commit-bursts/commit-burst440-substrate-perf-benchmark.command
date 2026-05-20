#!/usr/bin/env bash
# Burst 440 — substrate-perf benchmark dev-tool + first baseline.
#
# Adds a small, kernel-free measurement suite at
# dev-tools/benchmark/ that times the load-bearing HTTP paths
# against the running daemon. Pure HTTP-client timing; no schema
# changes, no new routes, no new event types. Lives entirely in
# userspace per ADR-0044 + ADR-0082.
#
# Scope clarification: this is the SUBSTRATE-PERF side of
# benchmarking (latency, throughput, signature-verification cost).
# It is NOT ADR-0023's per-genre quality battery system, which
# remains Proposed and is much larger scope (HTTP endpoints, audit
# event types, registry table, fixture YAML schema). Both are real
# benchmarks; this one is the cheap measurement that can ship now.
#
# Bundle:
#   * dev-tools/benchmark/run_substrate_perf.py (new) — Python
#     measurement runner. Hits /agents, /audit/tail, /tools/catalog,
#     /healthz; records p50/p95/p99 + serial throughput; writes
#     results.json + summary.md to data/test-runs/.
#   * dev-tools/benchmark/run-benchmarks.command (new) — Finder
#     cmd+O entry point.
#   * dev-tools/benchmark/README.md (new) — methodology +
#     interpretation + scope clarification (this != ADR-0023).
#   * docs/audits/2026-05-20-substrate-perf-baseline.md (new) —
#     captured baseline against HEAD dcd1d59. Numbers + observations
#     + future-optimization vectors.
#   * dev-tools/commit-bursts/commit-burst440-substrate-perf-benchmark.command
#     (this script).
#
# Baseline highlights (against dcd1d59 on Mac mini M4 16GB):
#   * dispatch_latency  p50=0.80ms  p95=1.49ms  p99=5.33ms
#   * audit_chain_read  p50=74.68ms p95=78.61ms p99=82.00ms
#   * registry_read     p50=0.89ms  p95=1.75ms  p99=2.45ms
#   * healthz throughput: 131 ops/sec serial
#
# The audit_chain_read p50 of ~75ms is the ADR-0049 per-event
# signature-verification load over 50 entries. Expected behavior;
# documented in the audit doc with candidate future optimizations
# (sig cache / ADR-0073 segmentation / lazy-verify flag).
#
# Hippocratic gate (CLAUDE.md sec0):
#   Prove harm: ADR-0023 has been Proposed since v0.1 with no
#     measurement story shipped; ChatGPT critique flagged. Any
#     future "did this slow the substrate" claim has no comparison
#     basis. Operator tunes by feel.
#   Prove non-load-bearing for kernel: dev-tools script only.
#     No schema, no events, no routes. Read-only HTTP client.
#   Prove alternative is worse: shipping ADR-0023's full per-genre
#     scope as MVP is multi-burst kernel work; defer that to its
#     own arc. Substrate-perf measurement is the cheaper, no-kernel
#     subset that can ship now AND inform future architectural work.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.."

echo "==========================================================="
echo "Burst 440 — substrate-perf benchmark + baseline"
echo "==========================================================="
echo

echo "Pre-commit: clear stale .git locks..."
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null && echo "  cleared" || echo "  none"
echo

git add dev-tools/benchmark/README.md
git add dev-tools/benchmark/run_substrate_perf.py
git add dev-tools/benchmark/run-benchmarks.command
git add docs/audits/2026-05-20-substrate-perf-baseline.md
git add dev-tools/commit-bursts/commit-burst440-substrate-perf-benchmark.command

echo "Staged:"
git diff --cached --stat
echo

git commit -m "feat(observability): substrate-perf benchmark suite + first baseline (B440)

Adds dev-tools/benchmark/ — a small, kernel-free measurement
suite that times the load-bearing HTTP paths against the running
daemon. Pure HTTP-client timing; no schema changes, no new routes,
no new event types. Lives entirely in userspace per ADR-0044 +
ADR-0082.

Scope clarification: this is the SUBSTRATE-PERF half of
benchmarking — latency, throughput, signature-verification cost.
It is NOT ADR-0023's per-genre quality battery system (which
remains Proposed and is multi-burst kernel work). Both scopes are
real; this is the cheap one that can ship now.

Four artifacts:
  * dev-tools/benchmark/run_substrate_perf.py — measurement runner
  * dev-tools/benchmark/run-benchmarks.command — Finder entry point
  * dev-tools/benchmark/README.md — methodology + scope notes
  * docs/audits/2026-05-20-substrate-perf-baseline.md — captured
    baseline against HEAD dcd1d59 on Mac mini M4

Baseline against dcd1d59 (Mac mini M4 16GB, loopback):
  dispatch_latency (/agents?limit=200)   p50=0.80ms  p95=1.49ms
  audit_chain_read (/audit/tail?n=50)    p50=74.68ms p95=78.61ms
  registry_read    (/tools/catalog)      p50=0.89ms  p95=1.75ms
  healthz_throughput                     131 ops/sec serial

The audit_chain_read p50 is two orders of magnitude slower than
the others — that is the ADR-0049 per-event signature verification
cost lighting up across 50 entries. Expected behavior; documented
with three candidate future optimizations (sig cache / ADR-0073
segmentation / lazy-verify cursor flag).

Run via:
  bash dev-tools/benchmark/run-benchmarks.command
Output lands at data/test-runs/benchmark-substrate-perf-<ts>/.

Hippocratic gate (CLAUDE.md sec0):
  Prove harm: ChatGPT critique flagged missing benchmark story;
    'did this slow the substrate' claims have had no comparison
    basis; operators tune by feel.
  Prove non-load-bearing: dev-tools/ script only. No schema, no
    events, no routes. Read-only HTTP client.
  Prove alternative worse: shipping ADR-0023's full per-genre
    quality scope as MVP is multi-burst kernel work; defer to its
    own arc. Substrate-perf measurement is the cheaper subset that
    informs future architectural work without touching kernel." || { echo "commit failed"; exit 1; }

echo
echo "==========================================================="
echo "Post-commit signature status:"
echo "==========================================================="
git log --format='%h %G? %s' -4

echo
echo "Pushing B440..."
git push origin main || { echo "push failed"; exit 1; }

echo
echo "Done. B440 pushed."
echo
echo "Press any key to close."
read -n 1 || true
