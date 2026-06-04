#!/usr/bin/env bash
# run-benchmark.command — ADR-0096 LLM benchmark (known-answer llm_think tasks).
#
# Runs config/tasks/benchmark.yaml against the CONFIGURED local model (ollama) and
# reports per-task correctness — the empirical "is this model good enough?" signal,
# scored deterministically (no LLM grading): a task passes iff the model's response
# CONTAINS the known answer. Needs ollama + the model resident. Read-only.
#
# Swap the model (config/settings.toml local_model or FSF_LOCAL_MODEL) and re-run to
# compare models head-to-head on the same tasks.
#
# Usage:  ./dev-tools/run-benchmark.command [OUTPUT_DIR]
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
OUT="${1:-$HERE/data/benchmark-runs}"
mkdir -p "$OUT"

if [ ! -x "$HERE/.venv/bin/python" ]; then
  echo "[run-benchmark] no .venv/bin/python — run from the repo with the venv built." 1>&2
  exit 2
fi

"$HERE/.venv/bin/python" - "$OUT" <<'PYEOF'
import sys, json, asyncio
from pathlib import Path
from forest_soul_forge.training import load_catalog, run_suite, report
from forest_soul_forge.training.harness import build_env, build_local_provider, BENCHMARK_TOOLS

out = Path(sys.argv[1])
provider = build_local_provider()
env = build_env(out / "workspace", tools=BENCHMARK_TOOLS, provider=provider, agent_id="benchmark")
cat = load_catalog("config/tasks/benchmark.yaml")
suite = asyncio.run(run_suite(cat, env.dispatch, agent_id=env.agent_id,
                              trust_graph=env.trust_graph))

print(f"=== LLM benchmark — model map: {provider.models} ===")
print(f"correctness: {suite.passed}/{suite.total}")
for r in suite.results:
    mark = "PASS" if r.passed else "FAIL"
    why = "" if r.passed else " — " + "; ".join(s.reason for s in r.steps if not s.passed)
    print(f"  [{mark}] {r.id} ({r.problem_class}){why}")

d = report.to_dict(suite)
(out / "benchmark-report.json").write_text(json.dumps(d, indent=2), encoding="utf-8")
print(f"report -> {out / 'benchmark-report.json'}")
# Benchmark is a measurement, not a gate — always exit 0 unless something errored.
PYEOF
rc=$?
echo "[run-benchmark] exit $rc"
exit $rc
