"""Read-only views into Forest's synaptic layer (ADR-0095).

Exposes the live trust graph the dispatch path feeds: per-(node, problem_class)
trust, quarantine candidates, the provenance behind any trust value, and a
chain-integrity check. Read-only by design — these endpoints never mutate trust
or convert it into capability (that boundary is human-gated; ADR-0095).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/synapse", tags=["synapse"])


def _graph(request: Request):
    tg = getattr(request.app.state, "trust_graph", None)
    if tg is None:
        raise HTTPException(503, "synaptic layer not wired (trust graph unavailable)")
    return tg


def _score(s) -> dict:
    lo, hi = s.interval()
    return {
        "node": s.node, "problem_class": s.problem_class,
        "trust": round(s.mean, 4), "interval": [round(lo, 4), round(hi, 4)],
        "observations": round(s.n, 2), "alpha": round(s.alpha, 4), "beta": round(s.beta, 4),
    }


@router.get("/trust")
def trust(request: Request, node: str | None = None, problem_class: str | None = None):
    """Trust scores. With both ``node`` and ``problem_class`` → that one; else the
    full set (optionally filtered), ranked by trust."""
    tg = _graph(request)
    if node and problem_class:
        return {"trust": _score(tg.trust(node, problem_class))}
    scores = [_score(s) for s in tg.scores()]
    if node:
        scores = [x for x in scores if x["node"] == node]
    if problem_class:
        scores = [x for x in scores if x["problem_class"] == problem_class]
    scores.sort(key=lambda x: x["trust"], reverse=True)
    return {"count": len(scores), "scores": scores}


@router.get("/quarantined")
def quarantined(request: Request, threshold: float = 0.4, min_n: float = 5.0):
    """Nodes confidently below ``threshold`` with ≥ ``min_n`` observations — the
    isolation the mesh may perform autonomously. Release is human-gated."""
    tg = _graph(request)
    return {"quarantine_candidates": [_score(s)
            for s in tg.quarantined(threshold=threshold, min_n=min_n)]}


@router.get("/why")
def why(request: Request, node: str, problem_class: str):
    """The provenance behind a trust value: every audited outcome that shaped it."""
    tg = _graph(request)
    outs = tg.why(node, problem_class)
    return {"node": node, "problem_class": problem_class, "n": len(outs),
            "outcomes": [{"seq": o.seq, "success": o.success, "weight": o.weight,
                          "evidence": o.evidence} for o in outs]}


@router.get("/verify")
def verify(request: Request):
    """Hash-chain integrity of the trust ledger — proves no past outcome was forged."""
    tg = _graph(request)
    ok, reason = tg.verify()
    return {"ok": ok, "reason": reason, "outcomes": tg._seq + 1}


@router.get("/nodes")
def nodes(request: Request):
    tg = _graph(request)
    return {"nodes": tg.nodes(), "problem_classes": tg.problem_classes()}
