"""``personal_recall.v1`` — ADR-0076 T4 (B322).

Semantic + lexical retrieval over the operator's personal-context
PersonalIndex. The four ten-domain consumers (Knowledge Forge /
Content Studio / Learning Coach / Research Lab) call this tool when
they need to surface what the operator has said before that's
relevant to the current task.

## How this differs from memory_recall.v1

memory_recall.v1 is a SQL LIKE substring search over the caller's
visible memory rows. It works against the same Memory table but
is scoped to the calling agent's lineage and scopes
(private / lineage / consented). Lexical only.

personal_recall.v1 is a hybrid BM25 + cosine retrieval over the
PersonalIndex (a separate, vector-aware data structure). It only
sees scope='personal' rows — operator-context entries written by
genres in PERSONAL_SCOPE_ALLOWED_GENRES. Cross-instance: any
allowed-genre agent can ask "what has the operator said about
X?" and get the same answers.

## Genre gate

The tool refuses with ``not_authorized`` for any calling agent
whose genre is NOT in PERSONAL_SCOPE_ALLOWED_GENRES. This is the
read-side mirror of the write-side restriction in Memory.append
(B313). The operator-context surface belongs to:

  - companion              — the operator's daily-life assistant
  - assistant              — the operator's persistent-chat agent
  - operator_steward       — the agent that owns the profile substrate
  - domain_orchestrator    — the cross-domain router (ADR-0067)

Other genres see the tool but get rejected at validate-time;
the dispatcher emits ``tool_call_rejected`` with reason
``genre_not_allowed``.

## Modes

  - 'hybrid' (default)  — BM25 + cosine via RRF. Most balanced.
  - 'cosine'             — embedding-only. Best for paraphrase.
  - 'bm25'               — lexical-only. Best for exact phrases.

## Substrate requirement

When ``ctx.personal_index`` is None (operator hasn't enabled
FSF_PERSONAL_INDEX_ENABLED, or the lifespan failed to construct
the index), the tool refuses with ``substrate_unwired``. The
daemon stays up; only this tool degrades.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.core.memory._helpers import (
    PERSONAL_SCOPE_ALLOWED_GENRES,
)
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolError,
    ToolResult,
    ToolValidationError,
)


_VALID_MODES = ("hybrid", "cosine", "bm25")
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50


class PersonalRecallTool:
    """Semantic + lexical recall over operator-context memory.

    Args:
      query (str): non-empty natural-language or keyword query.
      limit (int, optional): max hits to return. Default 10, max 50.
      mode  (str, optional): hybrid (default) | cosine | bm25.

    Output:
      {
        "count": int,
        "mode":  str,
        "hits":  [
          {doc_id, text, source, tags, similarity}, ...
        ]
      }
    """

    name: str = "personal_recall"
    version: str = "1"
    side_effects: str = "read_only"
    requires_human_approval: bool = False
    sandbox_eligible: bool = True

    def validate(self, args: dict[str, Any]) -> None:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolValidationError(
                "query must be a non-empty string",
            )
        limit = args.get("limit", _DEFAULT_LIMIT)
        if not isinstance(limit, int) or limit < 1 or limit > _MAX_LIMIT:
            raise ToolValidationError(
                f"limit must be an int 1..{_MAX_LIMIT}; got {limit!r}",
            )
        mode = args.get("mode", "hybrid")
        if mode not in _VALID_MODES:
            raise ToolValidationError(
                f"mode must be one of {list(_VALID_MODES)}; got {mode!r}",
            )

    async def execute(
        self,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        # Genre gate. The dispatcher resolves ctx.genre from the
        # agent's constitution; tests pass it directly.
        genre = (ctx.genre or "").lower()
        if genre not in PERSONAL_SCOPE_ALLOWED_GENRES:
            raise ToolError(
                f"genre {genre!r} is not authorized to read operator "
                f"personal context. Allowed: "
                f"{sorted(PERSONAL_SCOPE_ALLOWED_GENRES)}"
            )

        # Substrate gate.
        index = ctx.personal_index
        if index is None:
            raise ToolError(
                "personal index not wired. Set FSF_PERSONAL_INDEX_ENABLED="
                "true and restart the daemon to enable semantic recall."
            )

        query = args["query"].strip()
        limit = int(args.get("limit", _DEFAULT_LIMIT))
        mode = args.get("mode", "hybrid")

        # PersonalIndex.search is sync but lazy-loads
        # sentence-transformers on first call (~3-5s). The
        # dispatcher's threadpool absorbs that latency; we don't
        # need run_in_executor here because the tool itself is
        # already running in the dispatcher's thread context.
        try:
            results = index.search(query, k=limit, mode=mode)
        except Exception as e:  # noqa: BLE001 — wrap any retrieval error
            raise ToolError(
                f"personal_recall failed: {type(e).__name__}: {e}"
            ) from e

        hits = [
            {
                "doc_id":     r.doc_id,
                "text":       r.text,
                "source":     r.source,
                "tags":       list(r.tags),
                "similarity": r.similarity,
            }
            for r in results
        ]

        return ToolResult(
            output={
                "count": len(hits),
                "mode":  mode,
                "hits":  hits,
            },
            metadata={
                "audit_payload": {
                    "query_hash": _hash_query(query),
                    "mode":       mode,
                    "limit":      limit,
                    "hit_count":  len(hits),
                    # NB: we do NOT log the raw query — operator
                    # privacy first. The hash is enough for
                    # debugging "are there repeated lookups?"
                    # without exposing what the operator asked.
                },
            },
            side_effect_summary=(
                f"personal_recall({mode}, limit={limit}) → {len(hits)} hits"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_query(query: str) -> str:
    """SHA-256 of the raw query so the audit chain can trace
    repeated lookups without storing the lookup text. Truncated
    to 16 hex chars (64 bits) — collision-resistant enough for
    debugging without bloating the chain."""
    import hashlib
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


# Module-level instance the registry imports.
personal_recall_tool = PersonalRecallTool()
