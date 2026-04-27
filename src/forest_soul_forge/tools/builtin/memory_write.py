"""``memory_write.v1`` — write to the calling agent's own memory.

ADR-0022 v0.1 + ADR-0027 §1 — same-agent self-writes only. The
caller's genre is enforced as the privacy ceiling: a Companion
cannot widen scope past `private`, etc.

Side-effects classification: ``read_only``. The tool writes to the
local SQLite registry — same machinery the runtime already mutates
on every tool dispatch (counters, audit chain, accounting). It does
NOT touch the network, host filesystem outside the registry, or any
external service. The tier name is a bit misleading here; the
ADR-0030 / ADR-0019 tier ladder is calibrated to "what can leave
the host," and memory writes don't leave it. Operators looking at
the catalog can tell from the description that this writes locally.

Cross-agent disclosure (ADR-0027 §4) is a SEPARATE tool
(``memory_disclose.v1``, future) — this one is strictly local.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_VALID_LAYERS = ("episodic", "semantic", "procedural")
_VALID_SCOPES = ("private", "lineage", "realm", "consented")


class MemoryWriteTool:
    """Append an entry to the calling agent's memory.

    Args:
      content (str, required):  the memory body. ≤ 8 KB.
      layer   (str, required):  episodic | semantic | procedural.
      scope   (str, optional):  private (default) | lineage | realm |
                                consented. The genre ceiling caps
                                what's actually allowed; passing a
                                wider scope on a strict-genre agent
                                returns a refusal.
      tags    (list[str], optional): free-form labels for recall.

    Output:
      { entry_id, layer, scope, content_digest, created_at }
    """

    name = "memory_write"
    version = "1"
    side_effects = "read_only"  # see module docstring; local-only

    _MAX_CONTENT = 8 * 1024

    def validate(self, args: dict[str, Any]) -> None:
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ToolValidationError(
                "content must be a non-empty string"
            )
        if len(content) > self._MAX_CONTENT:
            raise ToolValidationError(
                f"content exceeds max {self._MAX_CONTENT} bytes; got {len(content)}"
            )
        layer = args.get("layer")
        if layer not in _VALID_LAYERS:
            raise ToolValidationError(
                f"layer must be one of {list(_VALID_LAYERS)}; got {layer!r}"
            )
        scope = args.get("scope", "private")
        if scope not in _VALID_SCOPES:
            raise ToolValidationError(
                f"scope must be one of {list(_VALID_SCOPES)}; got {scope!r}"
            )
        tags = args.get("tags")
        if tags is not None:
            if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                raise ToolValidationError(
                    "tags must be a list of strings when provided"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        memory = _resolve_memory(ctx)
        from forest_soul_forge.core.memory import MemoryScopeViolation
        try:
            entry = memory.append(
                instance_id=ctx.instance_id,
                agent_dna=ctx.agent_dna,
                content=args["content"],
                layer=args["layer"],
                scope=args.get("scope", "private"),
                tags=tuple(args.get("tags") or ()),
                genre=ctx.genre,
            )
        except MemoryScopeViolation as e:
            # Scope-violation raises ToolValidationError so the
            # dispatcher returns a refusal (HTTP 400 from the
            # endpoint) rather than a runtime crash. Per ADR-0027
            # §5 the genre ceiling cannot be operator-overridden
            # at this layer; the override path is a different tool
            # (memory_scope_override.v1, future) that emits its own
            # audit event.
            raise ToolValidationError(f"scope violation: {e}") from e
        return ToolResult(
            output={
                "entry_id":       entry.entry_id,
                "layer":          entry.layer,
                "scope":          entry.scope,
                "content_digest": entry.content_digest,
                "created_at":     entry.created_at,
            },
            metadata={
                "tags": list(entry.tags),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=f"wrote {entry.layer} memory entry",
        )


def _resolve_memory(ctx: ToolContext):
    """Mirror of memory_recall._resolve_memory — same pattern."""
    mem = getattr(ctx, "memory", None)
    if mem is not None:
        return mem
    mem = (ctx.constraints or {}).get("memory")
    if mem is not None:
        return mem
    raise ToolValidationError(
        "memory_write.v1: no Memory bound to ctx (daemon wiring "
        "missing)."
    )
