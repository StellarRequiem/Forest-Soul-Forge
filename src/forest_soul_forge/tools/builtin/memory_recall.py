"""``memory_recall.v1`` — read the calling agent's own memory.

ADR-0022 v0.1 + ADR-0027 §1 — same-agent self-reads are NOT
audited as ``memory_read``; the tool dispatcher's normal
``tool_call_dispatched`` / ``_succeeded`` events already record
that this agent read its own memory through this tool. Cross-agent
reads (when they exist post-ADR-0022 v0.2) will emit memory_read
explicitly.

The tool wires ``ctx.memory`` (a Memory instance) — the daemon
populates it via deps; tests inject directly. Runs read-only,
returns a list of entry summaries.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_VALID_LAYERS = ("episodic", "semantic", "procedural")


class MemoryRecallTool:
    """Read entries from the calling agent's memory store.

    Args:
      query  (str, optional): substring match against content + tags.
      layer  (str, optional): episodic | semantic | procedural. None
                              returns all layers.
      limit  (int, optional): max entries returned. Default 20, max 200.

    Output:
      {
        "count":   int,
        "entries": [
          {entry_id, layer, scope, content, tags, created_at}, ...
        ]
      }
    """

    name = "memory_recall"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        layer = args.get("layer")
        if layer is not None and layer not in _VALID_LAYERS:
            raise ToolValidationError(
                f"layer must be one of {list(_VALID_LAYERS)} or omitted; "
                f"got {layer!r}"
            )
        query = args.get("query")
        if query is not None and not isinstance(query, str):
            raise ToolValidationError(
                f"query must be a string when provided; got {type(query).__name__}"
            )
        limit = args.get("limit")
        if limit is not None:
            if not isinstance(limit, int) or limit < 1 or limit > 200:
                raise ToolValidationError(
                    f"limit must be an int 1..200; got {limit!r}"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        memory = _resolve_memory(ctx)
        entries = memory.recall(
            instance_id=ctx.instance_id,
            layer=args.get("layer"),
            query=args.get("query"),
            limit=int(args.get("limit") or 20),
        )
        out = [
            {
                "entry_id":    e.entry_id,
                "layer":       e.layer,
                "scope":       e.scope,
                "content":     e.content,
                "tags":        list(e.tags),
                "created_at":  e.created_at,
            }
            for e in entries
        ]
        return ToolResult(
            output={"count": len(out), "entries": out},
            metadata={
                "layer_filter": args.get("layer"),
                "query":        args.get("query"),
                "limit":        int(args.get("limit") or 20),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=None,
        )


def _resolve_memory(ctx: ToolContext):
    """Pull the Memory from ctx. The daemon wires it; tests inject
    via constraints['memory'] for in-memory exercises.

    ADR-0027 §1 — only the calling agent's own memory is reachable
    here. Cross-agent reads come through a different tool (memory_
    disclose) once v0.2 lands.
    """
    # Preferred: ctx.memory attribute (daemon wiring).
    mem = getattr(ctx, "memory", None)
    if mem is not None:
        return mem
    # Test fallback: stash on constraints dict.
    mem = (ctx.constraints or {}).get("memory")
    if mem is not None:
        return mem
    raise ToolValidationError(
        "memory_recall.v1: no Memory bound to ctx (daemon wiring "
        "missing). The daemon must populate ctx.memory before "
        "dispatching."
    )
