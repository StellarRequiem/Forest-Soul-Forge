"""Unit tests for memory_recall.v1 — Round 3c."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.core.memory import Memory
from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.memory_recall import MemoryRecallTool
from tests.unit.conftest import seed_stub_agent


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path):
    """Memory + ToolContext bound to a fresh registry."""
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    seed_stub_agent(reg, "agent_a")          # Phase A FK-seeding
    seed_stub_agent(reg, "other_agent")      # Cross-agent isolation tests
    memory = Memory(conn=reg._conn)  # noqa: SLF001
    ctx = ToolContext(
        instance_id="agent_a", agent_dna="d" * 12,
        role="researcher", genre="researcher", session_id="s1",
        constraints={}, memory=memory,
    )
    yield {"memory": memory, "ctx": ctx, "registry": reg}
    reg.close()


def _seed(memory, instance_id="agent_a"):
    memory.append(
        instance_id=instance_id, agent_dna="d" * 12,
        content="first thought", layer="episodic", tags=("tag-a",),
    )
    memory.append(
        instance_id=instance_id, agent_dna="d" * 12,
        content="semantic fact", layer="semantic",
    )
    memory.append(
        instance_id=instance_id, agent_dna="d" * 12,
        content="another episodic note", layer="episodic", tags=("tag-b",),
    )


class TestMemoryRecallValidate:
    def test_no_args_ok(self):
        MemoryRecallTool().validate({})

    def test_unknown_layer_rejected(self):
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"layer": "garbage"})

    def test_query_must_be_string(self):
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"query": 42})

    def test_limit_bounds(self):
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"limit": 0})
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"limit": 500})


class TestMemoryRecallExecute:
    def test_returns_all_entries_for_agent(self, env):
        _seed(env["memory"])
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        assert out.output["count"] == 3
        # newest first
        assert out.output["entries"][0]["content"] == "another episodic note"

    def test_layer_filter(self, env):
        _seed(env["memory"])
        out = _run(MemoryRecallTool().execute({"layer": "episodic"}, env["ctx"]))
        assert out.output["count"] == 2

    def test_query_substring(self, env):
        _seed(env["memory"])
        out = _run(MemoryRecallTool().execute({"query": "semantic"}, env["ctx"]))
        assert out.output["count"] == 1
        assert "semantic" in out.output["entries"][0]["content"]

    def test_limit_caps_results(self, env):
        for i in range(10):
            env["memory"].append(
                instance_id="agent_a", agent_dna="d" * 12,
                content=f"entry-{i}", layer="episodic",
            )
        out = _run(MemoryRecallTool().execute({"limit": 3}, env["ctx"]))
        assert out.output["count"] == 3

    def test_other_agents_memory_isolated(self, env):
        _seed(env["memory"], instance_id="other_agent")
        # Recall is scoped to ctx.instance_id; other_agent's entries
        # are invisible.
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        assert out.output["count"] == 0

    def test_pure_function_no_accounting(self, env):
        _seed(env["memory"])
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        assert out.tokens_used is None
        assert out.cost_usd is None
        assert out.side_effect_summary is None

    def test_no_memory_bound_raises(self):
        ctx = ToolContext(
            instance_id="x", agent_dna="0" * 12, role="r", genre=None,
            session_id="s", constraints={},
            # memory deliberately omitted
        )
        with pytest.raises(ToolValidationError, match="no Memory bound"):
            _run(MemoryRecallTool().execute({}, ctx))

    def test_test_fallback_via_constraints(self, tmp_path):
        """Fallback path: tests can pass Memory via constraints dict."""
        reg = Registry.bootstrap(tmp_path / "reg.sqlite")
        seed_stub_agent(reg, "x")  # Phase A FK-seeding
        memory = Memory(conn=reg._conn)
        memory.append(
            instance_id="x", agent_dna="0" * 12,
            content="hi", layer="episodic",
        )
        ctx = ToolContext(
            instance_id="x", agent_dna="0" * 12, role="r", genre=None,
            session_id="s", constraints={"memory": memory},
            # ctx.memory NOT set; falls back to constraints["memory"]
        )
        out = _run(MemoryRecallTool().execute({}, ctx))
        assert out.output["count"] == 1
        reg.close()


class TestRegistration:
    def test_memory_recall_registered_at_lifespan(self):
        from forest_soul_forge.tools import ToolRegistry
        from forest_soul_forge.tools.builtin import register_builtins
        reg = ToolRegistry()
        register_builtins(reg)
        assert reg.has("memory_recall", "1")
        assert reg.has("timestamp_window", "1")


# ===========================================================================
# ADR-0033 / ADR-0022 v0.2 — mode arg + lineage chain auto-discovery (T15)
# ===========================================================================
def _seed_chain(conn):
    """Plant A → B → C ancestry + D as an unrelated peer in agent_ancestry."""
    for aid in ("A", "B", "C", "D"):
        conn.execute(
            "INSERT INTO agents (instance_id, dna, dna_full, role, "
            "agent_name, soul_path, constitution_path, "
            "constitution_hash, created_at, status, sibling_index) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, aid * 12, aid * 64, "observer", aid,
             f"souls/{aid}.md", f"constitutions/{aid}.yaml", "0" * 64,
             "2026-04-27T00:00:00Z", "alive", 1),
        )
    for row in [
        ("A", "A", 0),
        ("B", "B", 0), ("B", "A", 1),
        ("C", "C", 0), ("C", "B", 1), ("C", "A", 2),
        ("D", "D", 0),
    ]:
        conn.execute(
            "INSERT INTO agent_ancestry(instance_id, ancestor_id, depth) VALUES (?, ?, ?)",
            row,
        )
    conn.commit()


@pytest.fixture
def chain_env(tmp_path):
    """4-agent ancestry topology: A → B → C; D unrelated. Each agent
    writes one entry per scope where allowed."""
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    conn = reg._conn  # noqa: SLF001
    _seed_chain(conn)
    memory = Memory(conn=conn)
    seeds = {}
    for aid in ("A", "B", "C", "D"):
        for scope in ("private", "lineage", "consented"):
            seeds[(aid, scope)] = memory.append(
                instance_id=aid, agent_dna=aid * 12,
                content=f"{aid}-{scope}", layer="episodic", scope=scope,
            )
    yield {"memory": memory, "registry": reg, "seeds": seeds}
    reg.close()


def _ctx(env, aid: str) -> ToolContext:
    return ToolContext(
        instance_id=aid, agent_dna=aid * 12,
        role="observer", genre=None,
        session_id="s1", constraints={}, memory=env["memory"],
    )


class TestComputeLineageChain:
    def test_chain_includes_ancestors_and_descendants(self, chain_env):
        from forest_soul_forge.tools.builtin.memory_recall import (
            _compute_lineage_chain,
        )
        chain = _compute_lineage_chain(chain_env["memory"].conn, "B")
        # B's ancestors: A. B's descendants: C. Plus B itself.
        assert set(chain) == {"A", "B", "C"}, chain

    def test_isolated_agent_chain_is_singleton(self, chain_env):
        from forest_soul_forge.tools.builtin.memory_recall import (
            _compute_lineage_chain,
        )
        assert _compute_lineage_chain(chain_env["memory"].conn, "D") == ("D",)

    def test_missing_ancestry_table_falls_back_to_self(self, tmp_path):
        # Memory.conn that lacks agent_ancestry — exercises the
        # defensive try/except path in the helper.
        import sqlite3
        from forest_soul_forge.tools.builtin.memory_recall import (
            _compute_lineage_chain,
        )
        conn = sqlite3.connect(":memory:")
        result = _compute_lineage_chain(conn, "alpha")
        assert result == ("alpha",)


class TestModeValidate:
    def test_omitted_mode_ok(self):
        MemoryRecallTool().validate({})

    def test_unknown_mode_rejected(self):
        with pytest.raises(ToolValidationError, match="mode must be"):
            MemoryRecallTool().validate({"mode": "bananas"})

    def test_realm_mode_rejected_with_h3_message(self):
        # mode='realm' is a tighter refusal than other unknown modes
        # because it's a valid scope name reserved for federation.
        with pytest.raises(ToolValidationError, match="Horizon 3"):
            MemoryRecallTool().validate({"mode": "realm"})

    def test_invalid_lineage_chain_rejected(self):
        with pytest.raises(ToolValidationError, match="lineage_chain"):
            MemoryRecallTool().validate({"lineage_chain": [1, 2, 3]})

    def test_lineage_chain_must_be_list(self):
        with pytest.raises(ToolValidationError, match="lineage_chain"):
            MemoryRecallTool().validate({"lineage_chain": "not-a-list"})


class TestModePrivate:
    """mode='private' is the default; v0.1-equivalent semantic but
    now strictly filters scope='private' (not all owner scopes)."""

    def test_private_returns_only_private_scope(self, chain_env):
        out = _run(MemoryRecallTool().execute({}, _ctx(chain_env, "B")))
        contents = [e["content"] for e in out.output["entries"]]
        assert contents == ["B-private"], contents
        assert out.output["mode"] == "private"
        assert out.metadata["cross_agent_count"] == 0
        assert out.metadata["lineage_chain_size"] == 0

    def test_private_owner_isolation(self, chain_env):
        # B's private mode must NOT surface A's anything.
        out = _run(MemoryRecallTool().execute({"mode": "private"}, _ctx(chain_env, "B")))
        for e in out.output["entries"]:
            assert e["instance_id"] == "B"


class TestModeLineage:
    def test_auto_chain_includes_ancestors_and_descendants(self, chain_env):
        # B's auto chain = {A, B, C}. So B sees own private+lineage
        # plus A-lineage and C-lineage. Not A-private (not in scope).
        out = _run(MemoryRecallTool().execute({"mode": "lineage"}, _ctx(chain_env, "B")))
        contents = sorted(e["content"] for e in out.output["entries"])
        assert contents == ["A-lineage", "B-lineage", "B-private", "C-lineage"], contents
        assert out.metadata["lineage_chain_size"] == 3
        # 2 cross-agent reads (A-lineage, C-lineage) — surfaced for the
        # runtime's audit emission.
        assert out.metadata["cross_agent_count"] == 2

    def test_explicit_chain_overrides_auto(self, chain_env):
        # Operator narrows the chain to just {B} — no peers visible.
        out = _run(MemoryRecallTool().execute(
            {"mode": "lineage", "lineage_chain": ["B"]}, _ctx(chain_env, "B"),
        ))
        contents = sorted(e["content"] for e in out.output["entries"])
        assert contents == ["B-lineage", "B-private"], contents
        assert out.metadata["cross_agent_count"] == 0

    def test_isolated_agent_lineage_is_self_only(self, chain_env):
        # D's auto chain = {D}. Only D's own private+lineage rows.
        out = _run(MemoryRecallTool().execute({"mode": "lineage"}, _ctx(chain_env, "D")))
        contents = sorted(e["content"] for e in out.output["entries"])
        assert contents == ["D-lineage", "D-private"], contents


class TestModeConsented:
    def test_grant_unlocks_consented_entry(self, chain_env):
        # Grant: A's consented entry → visible to B.
        a_cons = chain_env["seeds"][("A", "consented")]
        chain_env["memory"].grant_consent(
            entry_id=a_cons.entry_id, recipient_instance="B", granted_by="op",
        )
        out = _run(MemoryRecallTool().execute({"mode": "consented"}, _ctx(chain_env, "B")))
        contents = sorted(e["content"] for e in out.output["entries"])
        # Should include lineage's set + A-consented.
        assert "A-consented" in contents
        assert "B-private" in contents
        assert "B-lineage" in contents

    def test_no_grant_no_consented_visibility(self, chain_env):
        out = _run(MemoryRecallTool().execute({"mode": "consented"}, _ctx(chain_env, "B")))
        contents = sorted(e["content"] for e in out.output["entries"])
        # Without grants, mode='consented' = lineage's set (no extra
        # cross-agent consented rows).
        assert "A-consented" not in contents


class TestDisclosedCopySurfaces:
    """Disclosed-copy metadata flows through the tool output."""

    def test_disclosed_copy_metadata_in_entries(self, chain_env):
        from forest_soul_forge.tools.builtin.memory_disclose import MemoryDiscloseTool
        memory = chain_env["memory"]
        a_cons = chain_env["seeds"][("A", "consented")]
        memory.grant_consent(entry_id=a_cons.entry_id, recipient_instance="B", granted_by="op")
        _run(MemoryDiscloseTool().execute(
            {"source_entry_id": a_cons.entry_id,
             "recipient_instance": "B",
             "summary": "B told about anomaly_xyz"},
            _ctx(chain_env, "A"),
        ))
        out = _run(MemoryRecallTool().execute(
            {"mode": "consented"}, _ctx(chain_env, "B"),
        ))
        disclosed = [e for e in out.output["entries"] if e["is_disclosed_copy"]]
        assert len(disclosed) == 1
        d = disclosed[0]
        assert d["disclosed_from_entry"] == a_cons.entry_id
        assert "anomaly_xyz" in d["disclosed_summary"]


class TestBackwardCompatibility:
    """v0.1 callers (no mode arg, only private-scope writes) must keep
    seeing the same results — ADR-0033 must not break existing skills."""

    def test_v01_caller_default_mode_returns_private_owner_rows(self, env):
        # Replicate the v0.1 access pattern: write private-scope only,
        # call recall with no mode arg.
        env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="legacy entry", layer="episodic",
        )
        env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="another legacy entry", layer="episodic",
        )
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        contents = sorted(e["content"] for e in out.output["entries"])
        assert contents == ["another legacy entry", "legacy entry"]


# ===========================================================================
# ADR-0027-amendment §7 — epistemic metadata surfaces (T3)
# ===========================================================================
class TestEpistemicSurfaces:
    """Recall always exposes claim_type, confidence, last_challenged_at on
    every entry. Optional surface_contradictions + staleness_threshold_days
    parameters add per-entry contradictions / staleness flags."""

    def test_default_fields_always_surfaced(self, env):
        env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="default entry", layer="episodic",
        )
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        e = out.output["entries"][0]
        # Defaults match the schema CHECK column DEFAULTs.
        assert e["claim_type"] == "observation"
        assert e["confidence"] == "medium"
        assert e["last_challenged_at"] is None
        # Without surface_contradictions / staleness, those keys absent.
        assert "unresolved_contradictions" not in e
        assert "is_stale" not in e

    def test_explicit_claim_type_round_trips_through_recall(self, env):
        env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="my hunch", layer="semantic",
            claim_type="agent_inference", confidence="low",
        )
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        e = out.output["entries"][0]
        assert e["claim_type"] == "agent_inference"
        assert e["confidence"] == "low"

    def test_k1_verification_promotes_confidence_to_high(self, env):
        # ADR-0027-amendment §7.6 — verified entries surface as high
        # regardless of stored confidence.
        e = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="hunch verified externally", layer="semantic",
            claim_type="agent_inference", confidence="low",
        )
        env["memory"].mark_verified(
            entry_id=e.entry_id,
            verifier_id="external_operator",
            seal_note="confirmed via secondary channel",
        )
        out = _run(MemoryRecallTool().execute({}, env["ctx"]))
        # Stored confidence is 'low'; effective confidence is 'high'.
        surfaced = next(
            x for x in out.output["entries"]
            if x["entry_id"] == e.entry_id
        )
        assert surfaced["confidence"] == "high"
        # Stored confidence unchanged in DB:
        from_db = env["memory"].get(e.entry_id)
        assert from_db.confidence == "low"

    def test_surface_contradictions_attaches_open_conflicts(self, env):
        e1 = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="prefer mornings", layer="semantic",
            claim_type="preference",
        )
        e2 = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="actually I prefer evenings", layer="semantic",
            claim_type="preference",
        )
        env["memory"].conn.execute(
            "INSERT INTO memory_contradictions ("
            "    contradiction_id, earlier_entry_id, later_entry_id,"
            "    contradiction_kind, detected_at, detected_by"
            ") VALUES ('c1', ?, ?, 'updated', '2026-05-01 00:00:00Z', 'op');",
            (e1.entry_id, e2.entry_id),
        )
        out = _run(MemoryRecallTool().execute(
            {"surface_contradictions": True}, env["ctx"],
        ))
        # Both e1 and e2 reference the contradiction.
        for entry in out.output["entries"]:
            assert "unresolved_contradictions" in entry
            if entry["entry_id"] in (e1.entry_id, e2.entry_id):
                assert len(entry["unresolved_contradictions"]) == 1
                c = entry["unresolved_contradictions"][0]
                assert c["contradiction_kind"] == "updated"
                assert c["earlier_entry_id"] == e1.entry_id
                assert c["later_entry_id"] == e2.entry_id
        # Metadata records the count.
        assert out.metadata["contradicted_count"] == 2
        assert out.metadata["surface_contradictions"] is True

    def test_resolved_contradictions_not_surfaced(self, env):
        # Resolved contradictions are intentionally excluded — recall
        # only shows what's still open.
        e1 = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="x", layer="episodic",
        )
        e2 = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="y", layer="episodic",
        )
        env["memory"].conn.execute(
            "INSERT INTO memory_contradictions ("
            "    contradiction_id, earlier_entry_id, later_entry_id,"
            "    contradiction_kind, detected_at, detected_by,"
            "    resolved_at, resolution_summary"
            ") VALUES ('c2', ?, ?, 'direct', '2026-05-01 00:00:00Z', 'op',"
            "          '2026-05-01 12:00:00Z', 'operator decided e2 wins');",
            (e1.entry_id, e2.entry_id),
        )
        out = _run(MemoryRecallTool().execute(
            {"surface_contradictions": True}, env["ctx"],
        ))
        for entry in out.output["entries"]:
            assert entry["unresolved_contradictions"] == []
        assert out.metadata["contradicted_count"] == 0

    def test_staleness_threshold_flags_old_entries(self, env):
        # Entry created at a fixed past timestamp; with a tight
        # threshold the staleness check fires.
        e = env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="old entry", layer="semantic",
            claim_type="preference",
        )
        # Force its created_at to a date long ago via direct SQL —
        # the helper uses ISO-8601 string comparison so this is safe.
        env["memory"].conn.execute(
            "UPDATE memory_entries SET created_at='2020-01-01 00:00:00Z' "
            "WHERE entry_id=?;",
            (e.entry_id,),
        )
        out = _run(MemoryRecallTool().execute(
            {"staleness_threshold_days": 30}, env["ctx"],
        ))
        surfaced = next(
            x for x in out.output["entries"]
            if x["entry_id"] == e.entry_id
        )
        assert surfaced["is_stale"] is True
        assert out.metadata["stale_count"] == 1
        assert out.metadata["staleness_threshold_days"] == 30

    def test_staleness_threshold_does_not_flag_fresh_entries(self, env):
        env["memory"].append(
            instance_id="agent_a", agent_dna="d" * 12,
            content="fresh entry", layer="episodic",
        )
        out = _run(MemoryRecallTool().execute(
            {"staleness_threshold_days": 365}, env["ctx"],
        ))
        # Just-created → far inside the 365-day window.
        for e in out.output["entries"]:
            assert e["is_stale"] is False
        assert out.metadata["stale_count"] == 0

    def test_invalid_surface_contradictions_type_rejected(self):
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"surface_contradictions": "true"})

    def test_invalid_staleness_threshold_rejected(self):
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"staleness_threshold_days": 0})
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"staleness_threshold_days": -5})
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"staleness_threshold_days": 1.5})
        with pytest.raises(ToolValidationError):
            MemoryRecallTool().validate({"staleness_threshold_days": True})
