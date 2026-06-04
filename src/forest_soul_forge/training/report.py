"""Render a training run into a documentation artifact (ADR-0096 — the L4 tier).

The report IS the "documentation system" exercise: a deterministic, auditable
markdown + dict summary of the run — per-tier pass/fail, every step's reason and
audit_seq, and the integrity checks. A run that can't produce a well-formed report
is a failed run. Pure functions — no I/O; the caller decides where to write.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.training.runner import SuiteResult

TIER_NAMES = {
    0: "Baseline", 1: "L1 determinism", 2: "L2 audit",
    3: "L3 composition", 4: "L4 doc+integrity",
}


def to_dict(suite: SuiteResult, *, audit_ok: bool | None = None,
            trust_ok: bool | None = None) -> dict[str, Any]:
    return {
        "schema": "fsf.training_report.v1",
        "agent_id": suite.agent_id,
        "passed": suite.passed,
        "total": suite.total,
        "by_tier": {str(t): {"passed": p, "total": n}
                    for t, (p, n) in suite.by_tier().items()},
        "audit_chain_ok": audit_ok,
        "trust_graph_ok": trust_ok,
        "tasks": [
            {"id": r.id, "tier": r.tier, "problem_class": r.problem_class,
             "passed": r.passed,
             "steps": [{"tool": s.tool, "passed": s.passed, "reason": s.reason,
                        "status": s.status, "audit_seq": s.audit_seq} for s in r.steps]}
            for r in suite.results],
    }


def to_markdown(suite: SuiteResult, *, audit_ok: bool | None = None,
                trust_ok: bool | None = None) -> str:
    out = [f"# Training report — {suite.agent_id}", ""]
    out.append(f"**Score:** {suite.passed}/{suite.total} tasks passed")
    if audit_ok is not None:
        out.append(f"**Audit chain integrity:** {'OK' if audit_ok else 'FAILED'}")
    if trust_ok is not None:
        out.append(f"**Trust graph integrity:** {'OK' if trust_ok else 'FAILED'}")
    out += ["", "| Tier | Passed |", "|---|---|"]
    for t, (p, n) in suite.by_tier().items():
        out.append(f"| {t} · {TIER_NAMES.get(t, '?')} | {p}/{n} |")
    out.append("")
    for r in suite.results:
        out.append(f"## {'✅' if r.passed else '❌'} {r.id} "
                   f"(tier {r.tier} · `{r.problem_class}`)")
        for s in r.steps:
            out.append(f"- {'✓' if s.passed else '✗'} `{s.tool}` — {s.reason} "
                       f"(status={s.status}, audit_seq={s.audit_seq})")
        out.append("")
    return "\n".join(out)
