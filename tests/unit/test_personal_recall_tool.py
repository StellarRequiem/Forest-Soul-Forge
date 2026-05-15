"""ADR-0076 T4 (B322) — personal_recall.v1 tool tests.

Coverage:
  validate (args shape):
    - missing query rejected
    - empty/whitespace-only query rejected
    - non-string query rejected
    - limit out of bounds rejected
    - unknown mode rejected
    - hybrid is the default mode
    - mode='cosine' + 'bm25' accepted
    - default limit 10

  execute (runtime gating + delegation):
    - allowed genre + wired index returns hits
    - genre NOT in PERSONAL_SCOPE_ALLOWED_GENRES is refused
    - personal_index=None is refused with substrate_unwired
    - mode 'cosine' is forwarded to the index
    - mode 'bm25' is forwarded to the index
    - hits include doc_id, text, source, tags, similarity
    - audit_payload records query_hash NOT raw query (privacy)
    - side_effect_summary mentions mode + limit + count
    - retrieval exception is wrapped as ToolError
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import pytest

from forest_soul_forge.core.memory._helpers import (
    PERSONAL_SCOPE_ALLOWED_GENRES,
)
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolError,
    ToolValidationError,
)
from forest_soul_forge.tools.builtin.personal_recall import (
    PersonalRecallTool,
    _hash_query,
)


class _StubIndex:
    """Captures search() calls + returns a deterministic result list."""

    def __init__(self, results=None, raise_exc=None):
        self.calls: list[dict[str, Any]] = []
        self._results = results or []
        self._raise_exc = raise_exc

    def search(self, query, k=10, mode="cosine", **kw):
        self.calls.append({"query": query, "k": k, "mode": mode, **kw})
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._results


def _result(doc_id, text, source="memory:episodic:personal",
            tags=("trust",), similarity=0.42):
    """Stand-in for SearchResult — duck-typed because the tool
    only reads the fields, never the type."""
    class R:
        pass
    r = R()
    r.doc_id = doc_id
    r.text = text
    r.source = source
    r.tags = tags
    r.similarity = similarity
    return r


def _ctx(*, genre="companion", personal_index=None):
    return ToolContext(
        instance_id="ag1",
        agent_dna="dna_abc",
        role="companion",
        genre=genre,
        session_id="s1",
        personal_index=personal_index,
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_missing_query():
    tool = PersonalRecallTool()
    with pytest.raises(ToolValidationError, match="query"):
        tool.validate({})


def test_validate_empty_query():
    tool = PersonalRecallTool()
    with pytest.raises(ToolValidationError, match="query"):
        tool.validate({"query": "   "})


def test_validate_non_string_query():
    tool = PersonalRecallTool()
    with pytest.raises(ToolValidationError, match="query"):
        tool.validate({"query": 42})


def test_validate_limit_lower_bound():
    tool = PersonalRecallTool()
    with pytest.raises(ToolValidationError, match="limit"):
        tool.validate({"query": "x", "limit": 0})


def test_validate_limit_upper_bound():
    tool = PersonalRecallTool()
    with pytest.raises(ToolValidationError, match="limit"):
        tool.validate({"query": "x", "limit": 200})


def test_validate_unknown_mode():
    tool = PersonalRecallTool()
    with pytest.raises(ToolValidationError, match="mode"):
        tool.validate({"query": "x", "mode": "fuzzy"})


def test_validate_accepts_all_valid_modes():
    tool = PersonalRecallTool()
    for m in ("hybrid", "cosine", "bm25"):
        tool.validate({"query": "alpha", "mode": m})


def test_validate_default_mode_is_implicit():
    """Omitting mode = hybrid; should pass validate."""
    tool = PersonalRecallTool()
    tool.validate({"query": "alpha"})


# ---------------------------------------------------------------------------
# execute — gating
# ---------------------------------------------------------------------------


def test_genre_not_allowed_is_refused():
    tool = PersonalRecallTool()
    ctx = _ctx(genre="network_watcher", personal_index=_StubIndex())
    with pytest.raises(ToolError, match="not authorized"):
        asyncio.run(tool.execute({"query": "x"}, ctx))


def test_no_personal_index_is_refused():
    tool = PersonalRecallTool()
    ctx = _ctx(genre="companion", personal_index=None)
    with pytest.raises(ToolError, match="personal index not wired"):
        asyncio.run(tool.execute({"query": "x"}, ctx))


@pytest.mark.parametrize("genre", sorted(PERSONAL_SCOPE_ALLOWED_GENRES))
def test_every_allowed_genre_can_recall(genre):
    tool = PersonalRecallTool()
    idx = _StubIndex(results=[_result("d1", "alpha")])
    ctx = _ctx(genre=genre, personal_index=idx)
    result = asyncio.run(tool.execute({"query": "alpha"}, ctx))
    assert result.output["count"] == 1


# ---------------------------------------------------------------------------
# execute — delegation
# ---------------------------------------------------------------------------


def test_default_mode_is_hybrid():
    tool = PersonalRecallTool()
    idx = _StubIndex(results=[])
    ctx = _ctx(personal_index=idx)
    asyncio.run(tool.execute({"query": "alpha"}, ctx))
    assert idx.calls[0]["mode"] == "hybrid"
    assert idx.calls[0]["k"] == 10  # default limit


def test_mode_cosine_forwarded():
    tool = PersonalRecallTool()
    idx = _StubIndex(results=[])
    ctx = _ctx(personal_index=idx)
    asyncio.run(tool.execute(
        {"query": "alpha", "mode": "cosine", "limit": 5}, ctx,
    ))
    assert idx.calls[0]["mode"] == "cosine"
    assert idx.calls[0]["k"] == 5


def test_mode_bm25_forwarded():
    tool = PersonalRecallTool()
    idx = _StubIndex(results=[])
    ctx = _ctx(personal_index=idx)
    asyncio.run(tool.execute(
        {"query": "routing number", "mode": "bm25"}, ctx,
    ))
    assert idx.calls[0]["mode"] == "bm25"


def test_hits_include_full_metadata():
    tool = PersonalRecallTool()
    idx = _StubIndex(results=[
        _result("d1", "operator likes coffee", source="memory:episodic:personal",
                tags=("daily", "habit"), similarity=0.81),
    ])
    ctx = _ctx(personal_index=idx)
    result = asyncio.run(tool.execute({"query": "coffee"}, ctx))
    assert result.output["count"] == 1
    hit = result.output["hits"][0]
    assert hit["doc_id"] == "d1"
    assert hit["text"] == "operator likes coffee"
    assert hit["source"] == "memory:episodic:personal"
    assert hit["tags"] == ["daily", "habit"]
    assert hit["similarity"] == 0.81


def test_audit_payload_records_query_hash_not_raw_query():
    """Operator-privacy invariant: the raw query MUST NOT land
    on the chain. Only the hash + meta."""
    tool = PersonalRecallTool()
    idx = _StubIndex(results=[])
    ctx = _ctx(personal_index=idx)
    raw = "my secret bank account number 123456789"
    result = asyncio.run(tool.execute({"query": raw}, ctx))
    payload = result.metadata["audit_payload"]
    # The raw query MUST NOT appear in the payload.
    assert raw not in str(payload)
    # The hash MUST match _hash_query(raw).
    assert payload["query_hash"] == _hash_query(raw)
    assert payload["mode"] == "hybrid"
    assert payload["limit"] == 10
    assert payload["hit_count"] == 0


def test_side_effect_summary_mentions_mode_limit_count():
    tool = PersonalRecallTool()
    idx = _StubIndex(results=[
        _result("d1", "x"), _result("d2", "y"),
    ])
    ctx = _ctx(personal_index=idx)
    result = asyncio.run(tool.execute(
        {"query": "x", "mode": "hybrid", "limit": 7}, ctx,
    ))
    s = result.side_effect_summary
    assert "hybrid" in s
    assert "7" in s
    assert "2" in s


def test_retrieval_exception_is_wrapped():
    tool = PersonalRecallTool()
    idx = _StubIndex(raise_exc=RuntimeError("embedder unavailable"))
    ctx = _ctx(personal_index=idx)
    with pytest.raises(ToolError, match="embedder unavailable"):
        asyncio.run(tool.execute({"query": "x"}, ctx))


# ---------------------------------------------------------------------------
# Static configuration
# ---------------------------------------------------------------------------


def test_tool_metadata():
    tool = PersonalRecallTool()
    assert tool.name == "personal_recall"
    assert tool.version == "1"
    assert tool.side_effects == "read_only"
    assert tool.requires_human_approval is False


def test_hash_query_is_stable_and_short():
    h1 = _hash_query("alpha beta gamma")
    h2 = _hash_query("alpha beta gamma")
    assert h1 == h2
    assert len(h1) == 16  # 64-bit truncation


def test_hash_query_differs_for_different_inputs():
    assert _hash_query("alpha") != _hash_query("beta")
