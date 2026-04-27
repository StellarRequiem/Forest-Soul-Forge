"""Unit tests for memory_disclose.v1 — ADR-0033 A2 T14.

Coverage:
- TestValidate          — per-arg validation rejects missing/empty/oversized
- TestRefusals          — every refusal path raises ToolValidationError with
                           a clear message (FK on memory_consents.recipient_instance
                           prevents the "missing recipient" case from being
                           reachable via grant_consent, but the disclose tool's
                           defensive check still fires when called directly)
- TestHappyPath         — successful disclose materializes a summary-only
                           copy on the recipient's store with the right
                           disclosed_* metadata
- TestPostDisclosure    — disclosed copy surfaces via recall_visible_to in
                           consented mode AND via the disclosed_summary
                           query path
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from forest_soul_forge.core.memory import Memory
from forest_soul_forge.registry import Registry
from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.memory_disclose import MemoryDiscloseTool


def _run(coro):
    return asyncio.run(coro)


def _seed_two_agents(conn):
    """Plant two minimum-viable agent rows so the consent FK has
    something to point at and the disclose tool's recipient existence
    check resolves."""
    for aid in ("A", "B"):
        conn.execute(
            "INSERT INTO agents (instance_id, dna, dna_full, role, "
            "agent_name, soul_path, constitution_path, constitution_hash, "
            "created_at, status, sibling_index) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                aid, aid * 12, aid * 64, "observer", aid,
                f"souls/{aid}.md", f"constitutions/{aid}.yaml", "0" * 64,
                "2026-04-27T00:00:00Z", "alive", 1,
            ),
        )
    conn.commit()


@pytest.fixture
def env(tmp_path):
    """Two-agent test bed — A is the discloser, B is the recipient."""
    reg = Registry.bootstrap(tmp_path / "reg.sqlite")
    conn = reg._conn  # noqa: SLF001
    conn.row_factory = sqlite3.Row
    _seed_two_agents(conn)
    memory = Memory(conn=conn)
    ctx_a = ToolContext(
        instance_id="A", agent_dna="A" * 12,
        role="observer", genre="security_low",
        session_id="s1", constraints={}, memory=memory,
    )
    ctx_b = ToolContext(
        instance_id="B", agent_dna="B" * 12,
        role="observer", genre="security_mid",
        session_id="s2", constraints={}, memory=memory,
    )
    yield {
        "memory": memory,
        "ctx_a": ctx_a,
        "ctx_b": ctx_b,
        "tool": MemoryDiscloseTool(),
    }
    reg.close()


# ===========================================================================
# Validate
# ===========================================================================
class TestValidate:
    def test_missing_required_field_refuses(self, env):
        tool = env["tool"]
        with pytest.raises(ToolValidationError, match="recipient_instance"):
            tool.validate({"source_entry_id": "x", "summary": "y"})

    def test_empty_string_refuses(self, env):
        tool = env["tool"]
        with pytest.raises(ToolValidationError, match="summary"):
            tool.validate(
                {"source_entry_id": "x", "recipient_instance": "y", "summary": ""}
            )

    def test_oversized_summary_refuses(self, env):
        tool = env["tool"]
        with pytest.raises(ToolValidationError, match="exceeds max"):
            tool.validate({
                "source_entry_id": "x",
                "recipient_instance": "y",
                "summary": "x" * 9000,
            })


# ===========================================================================
# Refusals — every guard rail in execute()
# ===========================================================================
class TestRefusals:
    def test_source_not_found(self, env):
        with pytest.raises(ToolValidationError, match="not found"):
            _run(env["tool"].execute(
                {"source_entry_id": "no-such-id",
                 "recipient_instance": "B",
                 "summary": "told"},
                env["ctx_a"],
            ))

    def test_source_not_owned_by_caller(self, env):
        # B writes a consented entry, A tries to disclose it — refused.
        # B's write of a non-private scope is fine here because the
        # genre on ctx_b is security_mid (ceiling=lineage); but for a
        # consented-scope write we use append's no-genre call to
        # bypass the ceiling check (the tool would do the same when
        # the operator overrides). Use a private write instead since
        # the ownership check fires before the scope check.
        memory = env["memory"]
        b_entry = memory.append(
            instance_id="B", agent_dna="B" * 12,
            content="b's secret", layer="episodic",
        )
        with pytest.raises(ToolValidationError, match="not owned"):
            _run(env["tool"].execute(
                {"source_entry_id": b_entry.entry_id,
                 "recipient_instance": "A",
                 "summary": "trying to leak B's"},
                env["ctx_a"],  # A is the caller, but doesn't own the entry
            ))

    def test_source_deleted(self, env):
        memory = env["memory"]
        e = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="x", layer="episodic", scope="consented",
        )
        memory.grant_consent(
            entry_id=e.entry_id, recipient_instance="B", granted_by="op",
        )
        memory.soft_delete(e.entry_id)
        with pytest.raises(ToolValidationError, match="deleted"):
            _run(env["tool"].execute(
                {"source_entry_id": e.entry_id,
                 "recipient_instance": "B",
                 "summary": "told"},
                env["ctx_a"],
            ))

    def test_source_scope_must_be_consented(self, env):
        # Private and lineage entries are NOT disclosable. Re-scoping
        # is the operator's call, not the disclose tool's.
        memory = env["memory"]
        for scope in ("private", "lineage"):
            e = memory.append(
                instance_id="A", agent_dna="A" * 12,
                content=f"{scope} entry", layer="episodic", scope=scope,
            )
            # Grant consent to make sure that's NOT what unblocks disclosure.
            memory.grant_consent(
                entry_id=e.entry_id, recipient_instance="B", granted_by="op",
            )
            with pytest.raises(ToolValidationError, match="consented"):
                _run(env["tool"].execute(
                    {"source_entry_id": e.entry_id,
                     "recipient_instance": "B",
                     "summary": "should refuse"},
                    env["ctx_a"],
                ))

    def test_recipient_must_exist(self, env):
        memory = env["memory"]
        e = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="x", layer="episodic", scope="consented",
        )
        # We deliberately do NOT grant consent first because a grant
        # to a nonexistent recipient would be blocked by the FK
        # constraint on memory_consents.recipient_instance. The
        # disclose tool's recipient-existence check is reached
        # without the grant in place.
        with pytest.raises(ToolValidationError, match="not found"):
            _run(env["tool"].execute(
                {"source_entry_id": e.entry_id,
                 "recipient_instance": "ghost",
                 "summary": "told ghost"},
                env["ctx_a"],
            ))

    def test_self_disclosure_refused(self, env):
        memory = env["memory"]
        e = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="x", layer="episodic", scope="consented",
        )
        with pytest.raises(ToolValidationError, match="differ"):
            _run(env["tool"].execute(
                {"source_entry_id": e.entry_id,
                 "recipient_instance": "A",
                 "summary": "self"},
                env["ctx_a"],
            ))

    def test_no_consent_grant_refuses(self, env):
        memory = env["memory"]
        e = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="x", layer="episodic", scope="consented",
        )
        # No grant → refused with a precise message.
        with pytest.raises(ToolValidationError, match="no active consent"):
            _run(env["tool"].execute(
                {"source_entry_id": e.entry_id,
                 "recipient_instance": "B",
                 "summary": "ungranted"},
                env["ctx_a"],
            ))

    def test_revoked_consent_refuses(self, env):
        memory = env["memory"]
        e = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="x", layer="episodic", scope="consented",
        )
        memory.grant_consent(
            entry_id=e.entry_id, recipient_instance="B", granted_by="op",
        )
        # First disclose succeeds.
        _run(env["tool"].execute(
            {"source_entry_id": e.entry_id,
             "recipient_instance": "B",
             "summary": "first one"},
            env["ctx_a"],
        ))
        # Revoke and try again — refused.
        memory.revoke_consent(entry_id=e.entry_id, recipient_instance="B")
        with pytest.raises(ToolValidationError, match="no active consent"):
            _run(env["tool"].execute(
                {"source_entry_id": e.entry_id,
                 "recipient_instance": "B",
                 "summary": "after revoke"},
                env["ctx_a"],
            ))


# ===========================================================================
# Happy path
# ===========================================================================
class TestHappyPath:
    def test_disclosure_creates_summary_only_copy(self, env):
        """The disclosed copy on B's store carries the SUMMARY, NOT
        the original source content. ADR-0027 §4 minimum disclosure."""
        memory = env["memory"]
        original = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="full original content with sensitive details",
            layer="episodic", scope="consented",
        )
        memory.grant_consent(
            entry_id=original.entry_id,
            recipient_instance="B", granted_by="op",
        )

        result = _run(env["tool"].execute(
            {"source_entry_id": original.entry_id,
             "recipient_instance": "B",
             "summary": "B told: anomaly_signature_xyz seen at 03:00"},
            env["ctx_a"],
        ))

        # Output shape pinning
        assert "disclosed_entry_id" in result.output
        assert result.output["recipient_instance"] == "B"
        assert result.output["source_entry_id"] == original.entry_id
        assert result.output["disclosed_at"]
        assert result.output["summary_digest"].startswith("sha256:")

        # Disclosed copy: summary is the content, original content NOT present
        copy_id = result.output["disclosed_entry_id"]
        copy = memory.get(copy_id)
        assert copy is not None
        assert copy.is_disclosed_copy is True
        assert copy.instance_id == "B"
        assert copy.scope == "consented"
        assert copy.disclosed_from_entry == original.entry_id
        assert (
            copy.content == "B told: anomaly_signature_xyz seen at 03:00"
        ), "disclosed copy content should be the summary, not the original"
        assert copy.disclosed_summary == copy.content
        assert "sensitive details" not in copy.content, (
            "ORIGINAL content leaked into disclosed copy — minimum-disclosure "
            "rule violated"
        )

    def test_disclosure_metadata_carries_audit_inputs(self, env):
        """The audit chain hashes ToolResult.metadata into the
        memory_disclosed event. Pin the metadata shape so a future
        refactor doesn't accidentally drop a field the chain
        depends on."""
        memory = env["memory"]
        e = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="x", layer="episodic", scope="consented",
        )
        memory.grant_consent(
            entry_id=e.entry_id, recipient_instance="B", granted_by="op",
        )
        result = _run(env["tool"].execute(
            {"source_entry_id": e.entry_id,
             "recipient_instance": "B",
             "summary": "told"},
            env["ctx_a"],
        ))
        meta = result.metadata
        assert meta["source_entry_id"] == e.entry_id
        assert meta["recipient_instance"] == "B"
        assert meta["summary_digest"].startswith("sha256:")
        assert meta["summary_length"] == len("told")
        assert result.side_effect_summary
        assert "B" in result.side_effect_summary


# ===========================================================================
# Post-disclosure read paths
# ===========================================================================
class TestPostDisclosure:
    def test_recipient_can_recall_disclosed_copy(self, env):
        """B's recall_visible_to in consented mode returns the
        disclosed copy on B's own store (without needing the
        consent grant on A's side, because B owns the copy)."""
        memory = env["memory"]
        original = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="full original", layer="episodic", scope="consented",
        )
        memory.grant_consent(
            entry_id=original.entry_id,
            recipient_instance="B", granted_by="op",
        )
        result = _run(env["tool"].execute(
            {"source_entry_id": original.entry_id,
             "recipient_instance": "B",
             "summary": "told B about the anomaly"},
            env["ctx_a"],
        ))
        copy_id = result.output["disclosed_entry_id"]

        # B's perspective with a chain of just B itself — B's own
        # consented entries are visible (which is what the disclosed
        # copy is, since it lives on B's store at scope=consented).
        seen = memory.recall_visible_to(
            reader_instance_id="B", mode="consented",
            lineage_chain=("B",),
        )
        ids = [e.entry_id for e in seen]
        assert copy_id in ids, (
            f"disclosed copy not visible to recipient: {ids}"
        )

    def test_disclosed_summary_is_searchable(self, env):
        """An operator searching memory by a term that lives only
        in the disclosed_summary still finds the disclosed copy
        (per ADR-0027 §4 — the original content stayed on the
        originator's store)."""
        memory = env["memory"]
        original = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="full original", layer="episodic", scope="consented",
        )
        memory.grant_consent(
            entry_id=original.entry_id,
            recipient_instance="B", granted_by="op",
        )
        _run(env["tool"].execute(
            {"source_entry_id": original.entry_id,
             "recipient_instance": "B",
             "summary": "anomaly fingerprint deadbeefcafe seen"},
            env["ctx_a"],
        ))
        hits = memory.recall_visible_to(
            reader_instance_id="B", mode="consented",
            lineage_chain=("B",), query="deadbeefcafe",
        )
        assert any(
            h.disclosed_summary and "deadbeefcafe" in h.disclosed_summary
            for h in hits
        ), "disclosed_summary search did not surface the disclosed copy"
