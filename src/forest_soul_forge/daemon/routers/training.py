"""``/training`` — Operator Console backend for the tiered self-test ladder (ADR-0096).

- ``GET /training/tasks`` — the catalog (the tiered ladder), for the console list.
- ``POST /training/run`` — execute the full ladder in an ISOLATED workspace (its
  own audit chain + trust ledger — never the live DB) and return the report.

Safe by construction: the ladder is deterministic + read-only, so running it has
no side effects on the live system. The workspace is created + torn down per call.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException

from forest_soul_forge.training import load_catalog, run_suite
from forest_soul_forge.training import report as _report
from forest_soul_forge.training.harness import build_env

router = APIRouter(prefix="/training", tags=["training"])

_CATALOG = Path("config/tasks/training.yaml")


@router.get("/tasks")
def tasks() -> dict:
    """The training catalog — the tiered ladder, ready for the console to group
    by tier. Read-only."""
    try:
        cat = load_catalog(_CATALOG)
    except Exception as e:  # malformed / missing catalog
        raise HTTPException(500, f"failed to load training catalog: {e}")
    return {
        "count": len(cat),
        "tasks": [
            {"id": t.id, "tier": t.tier, "problem_class": t.problem_class,
             "description": t.description, "side_effects": t.side_effects,
             "steps": [{"tool": s.tool, "version": s.version} for s in t.steps]}
            for t in cat],
    }


@router.post("/run")
async def run() -> dict:
    """Run the full ladder in a fresh isolated workspace; return the report.

    The ladder is deterministic + read-only (ADR-0096 §1), so this is safe to
    invoke from the console. Records trust + verifies audit/trust integrity in
    the workspace; the live daemon's DB is untouched."""
    base = Path("data/training-runs")
    base.mkdir(parents=True, exist_ok=True)
    ws = Path(tempfile.mkdtemp(prefix="console-", dir=str(base)))
    try:
        cat = load_catalog(_CATALOG)
        env = build_env(ws)
        suite = await run_suite(cat, env.dispatch, agent_id=env.agent_id,
                                trust_graph=env.trust_graph)
        t2 = next((r for r in suite.results if r.tier == 2), None)
        return _report.to_dict(
            suite, audit_ok=bool(t2 and t2.passed),
            trust_ok=env.trust_graph.verify()[0])
    except Exception as e:
        raise HTTPException(500, f"training run failed: {e}")
    finally:
        shutil.rmtree(ws, ignore_errors=True)
