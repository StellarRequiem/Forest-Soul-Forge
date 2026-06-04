#!/usr/bin/env bash
# run-training.command — ADR-0096 tiered self-test ladder (Baseline + L1–4).
#
# Runs the deterministic, read-only training ladder in an ISOLATED, single-writer
# workspace (its own audit chain + trust ledger — never the live daemon's DB),
# records per-tier trust, verifies audit + trust integrity, and writes a
# documentation report. Read-only by construction, so it is safe to run
# autonomously / on a schedule (the ADR-0096 §1 auto-run rail).
#
# Exit 0 iff every tier passed AND audit + trust integrity hold — so a scheduler
# or CI can treat a non-zero exit as a regression signal.
#
# Usage:  ./dev-tools/run-training.command [OUTPUT_DIR]
#   OUTPUT_DIR defaults to data/training-runs/ (gitignored).
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"   # repo root
cd "$HERE"
OUT="${1:-$HERE/data/training-runs}"
mkdir -p "$OUT"

if [ ! -x "$HERE/.venv/bin/python" ]; then
  echo "[run-training] no .venv/bin/python — run from the repo with the venv built." 1>&2
  exit 2
fi

"$HERE/.venv/bin/python" - "$OUT" <<'PYEOF'
import sys, json, asyncio
from pathlib import Path
from forest_soul_forge.training import load_catalog, run_suite, report
from forest_soul_forge.training.harness import build_env

out = Path(sys.argv[1])
env = build_env(out / "workspace")
tasks = load_catalog("config/tasks/training.yaml")
suite = asyncio.run(run_suite(tasks, env.dispatch, agent_id=env.agent_id,
                              trust_graph=env.trust_graph))
t2 = next((r for r in suite.results if r.tier == 2), None)
audit_ok = bool(t2 and t2.passed)            # the audit system, exercised live
trust_ok = env.trust_graph.verify()[0]
md = report.to_markdown(suite, audit_ok=audit_ok, trust_ok=trust_ok)
d = report.to_dict(suite, audit_ok=audit_ok, trust_ok=trust_ok)
(out / "training-report.md").write_text(md, encoding="utf-8")
(out / "training-report.json").write_text(json.dumps(d, indent=2), encoding="utf-8")
print(md)
print(f"\n[run-training] {suite.passed}/{suite.total} passed; "
      f"audit_ok={audit_ok} trust_ok={trust_ok}")
print(f"[run-training] report -> {out / 'training-report.md'}")
sys.exit(0 if (suite.passed == suite.total and audit_ok and trust_ok) else 1)
PYEOF
rc=$?
echo "[run-training] exit $rc"
exit $rc
