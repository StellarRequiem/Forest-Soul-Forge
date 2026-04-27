"""Unit tests for ADR-0022 v0.1 + ADR-0027 — Memory class."""
from __future__ import annotations

import pytest

from forest_soul_forge.core.memory import (
    GENRE_CEILINGS,
    LAYERS,
    Memory,
    MemoryScopeViolation,
    SCOPES,
    UnknownLayerError,
    UnknownScopeError,
)
from forest_soul_forge.registry import Registry


@pytest.fixture
def memory(tmp_path):
    """A Memory bound to a fresh in-test registry."""
    db = tmp_path / "reg.sqlite"
    reg = Registry.bootstrap(db)
    yield Memory(conn=reg._conn)  # noqa: SLF001 — test peeks at internals
    reg.close()


class TestAppend:
    def test_basic_append_round_trips(self, memory):
        e = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="remembering this", layer="episodic",
        )
        assert e.entry_id
        assert e.scope == "private"
        got = memory.get(e.entry_id)
        assert got is not None and got.content == "remembering this"
        assert got.content_digest.startswith("sha256:")

    def test_unknown_layer_rejected(self, memory):
        with pytest.raises(UnknownLayerError):
            memory.append(
                instance_id="i1", agent_dna="d" * 12,
                content="x", layer="garbage",
            )

    def test_unknown_scope_rejected(self, memory):
        with pytest.raises(UnknownScopeError):
            memory.append(
                instance_id="i1", agent_dna="d" * 12,
                content="x", layer="episodic", scope="public",
            )

    def test_companion_cannot_widen_beyond_private(self, memory):
        # ADR-0027 §5 — Companion ceiling is `private`. Even if the
        # caller passes scope="lineage", the write refuses.
        with pytest.raises(MemoryScopeViolation):
            memory.append(
                instance_id="i1", agent_dna="d" * 12,
                content="therapy notes", layer="episodic",
                scope="lineage", genre="companion",
            )

    def test_observer_can_use_lineage_but_not_realm(self, memory):
        # Observer ceiling is `lineage`. lineage works, realm doesn't.
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="x", layer="episodic",
            scope="lineage", genre="observer",
        )
        with pytest.raises(MemoryScopeViolation):
            memory.append(
                instance_id="i1", agent_dna="d" * 12,
                content="x", layer="episodic",
                scope="realm", genre="observer",
            )

    def test_genre_ceiling_constants_match_adr(self):
        # Hard-pin so a future drift is loud.
        assert GENRE_CEILINGS["companion"] == "private"
        assert GENRE_CEILINGS["observer"] == "lineage"
        assert "researcher" in GENRE_CEILINGS


class TestRecall:
    def test_recall_returns_newest_first(self, memory):
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="first", layer="episodic",
        )
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="second", layer="episodic",
        )
        out = memory.recall(instance_id="i1")
        assert [e.content for e in out] == ["second", "first"]

    def test_recall_filters_by_layer(self, memory):
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="ep", layer="episodic",
        )
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="se", layer="semantic",
        )
        out = memory.recall(instance_id="i1", layer="episodic")
        assert [e.content for e in out] == ["ep"]

    def test_recall_query_substring_match(self, memory):
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="the cat sat on the mat", layer="episodic",
        )
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="something else entirely", layer="episodic",
        )
        out = memory.recall(instance_id="i1", query="cat")
        assert len(out) == 1 and "cat" in out[0].content

    def test_recall_per_agent_isolation(self, memory):
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="i1's note", layer="episodic",
        )
        memory.append(
            instance_id="i2", agent_dna="e" * 12,
            content="i2's note", layer="episodic",
        )
        out = memory.recall(instance_id="i1")
        assert [e.content for e in out] == ["i1's note"]

    def test_recall_excludes_deleted_by_default(self, memory):
        e = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="going away", layer="episodic",
        )
        memory.soft_delete(e.entry_id)
        assert memory.recall(instance_id="i1") == []
        with_del = memory.recall(instance_id="i1", include_deleted=True)
        assert len(with_del) == 1 and with_del[0].is_deleted


class TestDelete:
    def test_soft_delete_clears_content(self, memory):
        e = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="secret", layer="episodic",
        )
        assert memory.soft_delete(e.entry_id) is True
        got = memory.get(e.entry_id)
        assert got is not None
        assert got.is_deleted
        assert got.content == ""  # tombstone clears content

    def test_purge_removes_row(self, memory):
        e = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="going", layer="episodic",
        )
        assert memory.purge(e.entry_id) is True
        assert memory.get(e.entry_id) is None

    def test_double_soft_delete_is_noop(self, memory):
        e = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="x", layer="episodic",
        )
        assert memory.soft_delete(e.entry_id) is True
        assert memory.soft_delete(e.entry_id) is False


class TestCount:
    def test_count_excludes_deleted(self, memory):
        a = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="a", layer="episodic",
        )
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="b", layer="episodic",
        )
        memory.soft_delete(a.entry_id)
        assert memory.count("i1") == 1
        assert memory.count("i1", include_deleted=True) == 2
