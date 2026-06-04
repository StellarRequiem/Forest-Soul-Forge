"""``route_recommend.v1`` — trust-based routing recommendation (ADR-0095).

Read-only. Given a problem_class and a candidate set, Thompson-samples each
candidate's trust posterior (from the live synaptic layer the daemon feeds on
every dispatch) and returns a ranked recommendation: under-tested nodes get
explored, proven nodes get exploited.

This is the *allowed* half of ADR-0095's routing boundary — routing INFORMS.
The tool ranks; it never selects an agent, delegates work, grants a permission,
or runs anything. The caller (agent or operator) decides what to do with the
ranking. Converting trust into capability stays human-gated.

Reads ``ctx.trust_graph``. Refuses cleanly (ToolValidationError) when no graph
is wired — test contexts, or a daemon that failed to build the synaptic layer.
"""
from __future__ import annotations

import random
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)

DEFAULT_TOP_K = 5
MAX_TOP_K = 50


class RouteRecommendTool:
    """Args:
      problem_class (str, required): the class to route (e.g. a tool key
        "llm_think.v1", or any label trust is tracked under).
      candidates (list[str], optional): node ids to rank. Default: every node
        with a track record for this problem_class.
      top_k (int, optional): max ranked entries. Default 5, max 50.
      seed (int, optional): seed the Thompson sampling for a reproducible
        ranking. Omit for live exploratory routing.

    Output:
      {
        "problem_class": str,
        "recommended": str | None,         # top node, or None if no candidates
        "ranking": [{"node": str, "sample": float, "trust": float,
                     "observations": float}, ...],
        "candidates": [str, ...],
        "note": str,                        # the ADR-0095 boundary reminder
      }
    """

    name = "route_recommend"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        pc = args.get("problem_class")
        if not isinstance(pc, str) or not pc.strip():
            raise ToolValidationError(
                "problem_class is required and must be a non-empty string")
        cand = args.get("candidates")
        if cand is not None and (
            not isinstance(cand, list) or not all(isinstance(c, str) for c in cand)
        ):
            raise ToolValidationError(
                "candidates must be a list of strings when provided")
        top_k = args.get("top_k", DEFAULT_TOP_K)
        if not isinstance(top_k, int) or top_k < 1 or top_k > MAX_TOP_K:
            raise ToolValidationError(
                f"top_k must be an integer in [1, {MAX_TOP_K}]; got {top_k!r}")
        seed = args.get("seed")
        if seed is not None and not isinstance(seed, int):
            raise ToolValidationError(
                f"seed must be an integer when provided; got {type(seed).__name__}")

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tg = ctx.trust_graph
        if tg is None:
            raise ToolValidationError(
                "route_recommend.v1: no trust graph wired into this dispatcher "
                "(test context, or the daemon failed to build the synaptic "
                "layer). Cannot produce a routing recommendation.")

        pc: str = args["problem_class"]
        top_k: int = int(args.get("top_k", DEFAULT_TOP_K))
        cand = args.get("candidates")
        if cand:
            candidates = [c.strip() for c in cand if c.strip()]
        else:
            # Default: everyone with a track record for this problem class.
            candidates = sorted({s.node for s in tg.scores() if s.problem_class == pc})

        if not candidates:
            return ToolResult(
                output={"problem_class": pc, "recommended": None, "ranking": [],
                        "candidates": [],
                        "note": "no candidates with a track record for this problem_class"},
                metadata={"problem_class": pc},
                side_effect_summary=f"route_recommend: 0 candidates for {pc!r}")

        seed = args.get("seed")
        rng = random.Random(seed) if seed is not None else None
        ranked = tg.rank(candidates, pc, rng=rng)[:top_k]
        ranking = []
        for node, sample in ranked:
            s = tg.trust(node, pc)
            ranking.append({
                "node": node, "sample": round(sample, 4),
                "trust": round(s.mean, 4), "observations": round(s.n, 2),
            })

        return ToolResult(
            output={
                "problem_class": pc,
                "recommended": ranked[0][0],
                "ranking": ranking,
                "candidates": candidates,
                "note": "routing informs; capability stays human-gated (ADR-0095)",
            },
            metadata={"problem_class": pc, "candidate_count": len(candidates),
                      "top_k": top_k},
            side_effect_summary=(
                f"route_recommend: {ranked[0][0]!r} top of {len(candidates)} "
                f"candidate(s) for {pc!r}"),
        )
