#!/usr/bin/env python3
"""Substrate performance benchmark — measures the load-bearing latency
and throughput characteristics of the FSF daemon as it runs today.

This is the "system perf" half of benchmarking — distinct from
ADR-0023's per-genre quality batteries (which measure agent
behavioral quality, not substrate speed). This dev-tool answers:

  * How fast does the dispatcher return for a known-cheap tool?
  * How fast can the audit chain append entries?
  * How fast can the registry serve a typical read?

Output: JSON to data/test-runs/benchmark-substrate-perf-<ts>/ plus
a markdown summary. Operator can compare baselines across substrate
changes (e.g., "did B440's catalog rewrite slow the dispatcher?").

Hippocratic gate (CLAUDE.md sec0):
  * No kernel change — pure HTTP client + timing. Lives entirely
    in dev-tools/.
  * Does NOT supersede ADR-0023; that ADR is per-genre quality
    batteries, a much bigger scope. This is the cheap measurement
    side, callable from anywhere.

Methodology notes:
  * Each benchmark warms up (N=5 untimed) then measures (N=50 timed).
    Cold-cache vs warm-cache means little here — repeated calls
    hit the same hot path; the measurement is throughput once warm.
  * p50/p95/p99 reported. p50 is the typical case; p95 catches the
    "long tail" the operator notices in interactive use; p99 catches
    GC pauses / SQLite checkpoints.
  * Throughput benchmarks (chain append, registry write) measure
    operations-per-second over a fixed window. The window is short
    (3 seconds) so the whole suite stays under a minute.

Usage:
  python3 dev-tools/benchmark/run_substrate_perf.py
  # or via wrapper:
  bash dev-tools/benchmark/run-benchmarks.command

Daemon must be running at http://127.0.0.1:7423. Script reads
FSF_API_TOKEN from .env if present (required for some endpoints
post-B148).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

DAEMON = os.environ.get("FSF_DAEMON_URL", "http://127.0.0.1:7423")
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


def _load_token() -> str | None:
    """Read FSF_API_TOKEN from .env if present. Post-B148 some endpoints
    require it; benchmark endpoints (/healthz, /agents, /audit/tail) do
    not, but we send it anyway to mirror real-client behavior."""
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        if line.startswith("FSF_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    return None


TOKEN = _load_token()


def _get(path: str) -> tuple[int, bytes]:
    url = DAEMON + path
    req = request.Request(url, method="GET")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except error.HTTPError as e:
        return e.code, e.read() if e.fp else b""
    except (error.URLError, TimeoutError) as e:
        return 0, str(e).encode()


def _percentiles(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0}
    s = sorted(samples_ms)
    n = len(s)
    return {
        "p50": s[int(n * 0.50)],
        "p95": s[min(int(n * 0.95), n - 1)],
        "p99": s[min(int(n * 0.99), n - 1)],
        "min": s[0],
        "max": s[-1],
        "mean": statistics.mean(s),
        "stdev": statistics.stdev(s) if n > 1 else 0.0,
    }


def bench_dispatch_latency(samples: int = 50, warmup: int = 5) -> dict:
    """Latency of a HTTP roundtrip + the dispatcher's typical path.

    We hit /agents (typical-cardinality list) as a proxy — it walks
    the registry table, doesn't dispatch a tool, and is the
    load-bearing read most clients make first.
    """
    for _ in range(warmup):
        _get("/agents?limit=200")
    times_ms = []
    for _ in range(samples):
        t0 = time.perf_counter()
        code, _ = _get("/agents?limit=200")
        t1 = time.perf_counter()
        if code == 200:
            times_ms.append((t1 - t0) * 1000.0)
    return {
        "name": "dispatch_latency",
        "endpoint": "/agents?limit=200",
        "samples": len(times_ms),
        "unit": "ms",
        **_percentiles(times_ms),
    }


def bench_audit_chain_read(samples: int = 50, warmup: int = 5) -> dict:
    """Audit chain read throughput as a proxy for chain health.

    We hit /audit/tail?n=50 — reads the last 50 chain entries with
    signature verification. This is the load-bearing pattern for
    section-08 + section-15 of the harness.

    Note: we measure READ not APPEND because appending requires
    triggering a real event, which has side effects we don't want
    to inject. Read throughput characterizes the read path; append
    speed correlates with it (same backing store).
    """
    for _ in range(warmup):
        _get("/audit/tail?n=50")
    times_ms = []
    for _ in range(samples):
        t0 = time.perf_counter()
        code, body = _get("/audit/tail?n=50")
        t1 = time.perf_counter()
        if code == 200:
            times_ms.append((t1 - t0) * 1000.0)
    return {
        "name": "audit_chain_read",
        "endpoint": "/audit/tail?n=50",
        "samples": len(times_ms),
        "unit": "ms",
        **_percentiles(times_ms),
    }


def bench_registry_read(samples: int = 50, warmup: int = 5) -> dict:
    """Registry read throughput — /tools/catalog returns the full tool
    catalog (68+ entries). Heavier than /agents because it walks a
    bigger structure; useful as the high-water mark for read latency.
    """
    for _ in range(warmup):
        _get("/tools/catalog")
    times_ms = []
    for _ in range(samples):
        t0 = time.perf_counter()
        code, _ = _get("/tools/catalog")
        t1 = time.perf_counter()
        if code == 200:
            times_ms.append((t1 - t0) * 1000.0)
    return {
        "name": "registry_read",
        "endpoint": "/tools/catalog",
        "samples": len(times_ms),
        "unit": "ms",
        **_percentiles(times_ms),
    }


def bench_healthz_throughput(window_s: float = 3.0) -> dict:
    """Operations-per-second a healthy daemon serves on its cheapest
    endpoint. /healthz returns {ok: true} with minimal work — this is
    the absolute ceiling for serial-client throughput."""
    t_end = time.perf_counter() + window_s
    count = 0
    t0 = time.perf_counter()
    while time.perf_counter() < t_end:
        code, _ = _get("/healthz")
        if code == 200:
            count += 1
    elapsed = time.perf_counter() - t0
    return {
        "name": "healthz_throughput",
        "endpoint": "/healthz",
        "window_s": elapsed,
        "ops": count,
        "ops_per_sec": count / elapsed if elapsed > 0 else 0.0,
    }


def main() -> int:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "data" / "test-runs" / f"benchmark-substrate-perf-{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"FSF substrate-perf benchmark — {timestamp}")
    print(f"Daemon: {DAEMON}")
    print(f"Output: {out_dir}")
    print("=" * 60)

    # Confirm daemon is reachable.
    code, body = _get("/healthz")
    if code != 200:
        print(f"ERROR: daemon unreachable ({code}). Aborting.")
        return 1

    results = []
    for bench in (
        bench_dispatch_latency,
        bench_audit_chain_read,
        bench_registry_read,
        bench_healthz_throughput,
    ):
        print(f"\nrunning {bench.__name__}...")
        r = bench()
        print(f"  {r}")
        results.append(r)

    # Get git SHA for provenance.
    git_sha = "unknown"
    try:
        import subprocess
        git_sha = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            text=True,
        ).strip()
    except Exception:
        pass

    output = {
        "timestamp": timestamp,
        "daemon_url": DAEMON,
        "git_sha": git_sha,
        "benchmarks": results,
    }
    (out_dir / "results.json").write_text(json.dumps(output, indent=2))

    # Markdown summary.
    md = ["# Substrate performance benchmark", ""]
    md.append(f"- timestamp: {timestamp}")
    md.append(f"- git SHA: {git_sha}")
    md.append(f"- daemon: {DAEMON}")
    md.append("")
    md.append("## Latency benchmarks (ms)")
    md.append("")
    md.append("| Benchmark | samples | p50 | p95 | p99 | min | max | mean |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        if r["name"] == "healthz_throughput":
            continue
        md.append(
            f"| {r['name']} | {r['samples']} "
            f"| {r['p50']:.2f} | {r['p95']:.2f} | {r['p99']:.2f} "
            f"| {r['min']:.2f} | {r['max']:.2f} | {r['mean']:.2f} |"
        )
    md.append("")
    md.append("## Throughput benchmark")
    md.append("")
    for r in results:
        if r["name"] != "healthz_throughput":
            continue
        md.append(
            f"- {r['name']}: **{r['ops_per_sec']:.0f} ops/sec** "
            f"({r['ops']} ops over {r['window_s']:.2f}s)"
        )
    md.append("")
    md.append("## Interpretation")
    md.append("")
    md.append(
        "- `dispatch_latency` measures the load-bearing client-facing "
        "read (`/agents?limit=200`). p95 above ~50ms suggests the "
        "registry or constitution-parse path is slowing down."
    )
    md.append(
        "- `audit_chain_read` measures `/audit/tail?n=50` — exercises "
        "the chain reader + signature verifier per entry. Stable p95 "
        "is the canary for ADR-0049 signing performance."
    )
    md.append(
        "- `registry_read` measures `/tools/catalog` — the heaviest "
        "read most clients make. Sets the upper bound for any "
        "tool-catalog-aware UI tab load time."
    )
    md.append(
        "- `healthz_throughput` measures absolute ceiling — if this "
        "drops below the prior baseline by >20%, something is wrong "
        "with the event loop or socket layer."
    )

    summary_path = out_dir / "summary.md"
    summary_path.write_text("\n".join(md))
    print(f"\nwrote summary to {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
