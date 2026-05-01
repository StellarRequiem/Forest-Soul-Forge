"""Unit tests for ADR-0022 v0.1+v0.2 + ADR-0027 + ADR-0033 — Memory class."""
from __future__ import annotations

import pytest

from forest_soul_forge.core.memory import (
    CLAIM_TYPES,
    CONFIDENCE_LEVELS,
    GENRE_CEILINGS,
    LAYERS,
    Memory,
    MemoryScopeViolation,
    RECALL_MODES,
    SCOPES,
    UnknownClaimTypeError,
    UnknownConfidenceError,
    UnknownLayerError,
    UnknownScopeError,
)
from forest_soul_forge.registry import Registry
from tests.unit.conftest import seed_stub_agent


# Instance ids used across the test cases below. Seeded into the
# ``agents`` table by the fixture so FK constraints on
# ``memory_entries.instance_id`` are satisfied. Phase A audit
# (2026-04-30) traced ~13 failures here to missing seeding.
_KNOWN_AGENT_IDS = ("i1", "i2", "A", "B", "C", "D")


@pytest.fixture
def memory(tmp_path):
    """A Memory bound to a fresh in-test registry.

    Seeds 6 stub agent rows the test cases reference. Tests don't
    need to call ``seed_stub_agent`` themselves.
    """
    db = tmp_path / "reg.sqlite"
    reg = Registry.bootstrap(db)
    for aid in _KNOWN_AGENT_IDS:
        seed_stub_agent(reg, aid)
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


# ===========================================================================
# ADR-0033 / ADR-0022 v0.2 — recall_visible_to + consent path
# ===========================================================================
def _seed_chain(memory):
    """Seed a four-agent topology: A is parent, B is mid, C is leaf, D is
    unrelated. Each writes one entry per scope (where allowed) so every
    visibility test below has the full surface to filter against."""
    entries = {}
    for aid in ("A", "B", "C", "D"):
        for scope in ("private", "lineage", "consented"):
            e = memory.append(
                instance_id=aid,
                agent_dna=aid * 12,
                content=f"{aid}-{scope}",
                layer="episodic",
                scope=scope,
            )
            entries[(aid, scope)] = e
    return entries


class TestRecallVisibleToPrivateMode:
    """mode='private' is the v0.1-equivalent backstop — owner-only,
    only scope='private' rows. Used as the default when no lineage
    chain or consent grant is in play."""

    def test_owner_sees_only_own_private(self, memory):
        _seed_chain(memory)
        seen = memory.recall_visible_to(
            reader_instance_id="B", mode="private",
        )
        contents = sorted(e.content for e in seen)
        # B's own scope='lineage' / 'consented' rows are NOT visible in
        # private mode — those need lineage/consented mode respectively.
        assert contents == ["B-private"], (
            f"private mode leaked beyond owner-private: {contents}"
        )

    def test_no_cross_agent_leak(self, memory):
        _seed_chain(memory)
        seen = memory.recall_visible_to(
            reader_instance_id="A", mode="private",
        )
        contents = [e.content for e in seen]
        for foreign in ("B-private", "C-private", "D-private"):
            assert foreign not in contents


class TestRecallVisibleToLineageMode:
    """mode='lineage' is the swarm escalation path. The reader sees:
      - own scope='private' rows
      - own scope='lineage' rows
      - lineage_chain peers' scope='lineage' rows
    Nothing else — no peer's private, no consented (without grant)."""

    def test_lineage_chain_peers_visible(self, memory):
        _seed_chain(memory)
        seen = memory.recall_visible_to(
            reader_instance_id="B", mode="lineage",
            lineage_chain=("A", "B", "C"),
        )
        contents = sorted(e.content for e in seen)
        # B sees: A-lineage, B-lineage, B-private, C-lineage.
        # B does NOT see: A-private (peer's private), A-consented
        # (no grant), D-anything (outside chain).
        assert contents == ["A-lineage", "B-lineage", "B-private", "C-lineage"], (
            f"lineage visibility drift: {contents}"
        )

    def test_outside_chain_isolated(self, memory):
        _seed_chain(memory)
        seen = memory.recall_visible_to(
            reader_instance_id="D", mode="lineage",
            lineage_chain=("D",),
        )
        contents = sorted(e.content for e in seen)
        assert contents == ["D-lineage", "D-private"], (
            f"D should only see own private+lineage: {contents}"
        )

    def test_empty_chain_falls_back_to_owner_only(self, memory):
        # An empty lineage_chain is the degenerate case — no peers means
        # the lineage clause adds nothing beyond the reader's own rows.
        _seed_chain(memory)
        seen = memory.recall_visible_to(
            reader_instance_id="B", mode="lineage",
            lineage_chain=(),
        )
        contents = sorted(e.content for e in seen)
        assert contents == ["B-lineage", "B-private"], (
            f"empty chain should be owner private+lineage only: {contents}"
        )

    def test_chain_containing_self_is_safe(self, memory):
        # Reader in own lineage_chain is dedup'd at SQL level via the
        # `set(chain) - {reader}` subtraction. No double-counting.
        _seed_chain(memory)
        seen = memory.recall_visible_to(
            reader_instance_id="B", mode="lineage",
            lineage_chain=("A", "B", "B", "C"),  # B doubled
        )
        ids = [e.entry_id for e in seen]
        assert len(ids) == len(set(ids)), (
            f"duplicate rows returned when reader is in chain: {ids}"
        )


class TestRecallVisibleToConsentedMode:
    """mode='consented' = lineage's set + scope='consented' entries the
    reader has an active grant for via memory_consents."""

    def test_grant_unlocks_consented_entry(self, memory):
        seeds = _seed_chain(memory)
        # Without a grant, B cannot see A's consented entry.
        seen = memory.recall_visible_to(
            reader_instance_id="B", mode="consented",
            lineage_chain=("A", "B", "C"),
        )
        assert "A-consented" not in [e.content for e in seen]

        # Grant: A-consented → B
        memory.grant_consent(
            entry_id=seeds[("A", "consented")].entry_id,
            recipient_instance="B",
            granted_by="operator",
        )
        seen = memory.recall_visible_to(
            reader_instance_id="B", mode="consented",
            lineage_chain=("A", "B", "C"),
        )
        assert "A-consented" in [e.content for e in seen], (
            "grant did not unlock consented read"
        )

    def test_revoke_cuts_off_read(self, memory):
        seeds = _seed_chain(memory)
        eid = seeds[("A", "consented")].entry_id
        memory.grant_consent(entry_id=eid, recipient_instance="B", granted_by="op")
        # Sanity: granted.
        assert memory.is_consented(entry_id=eid, recipient_instance="B") is True
        # Revoke.
        assert memory.revoke_consent(entry_id=eid, recipient_instance="B") is True
        assert memory.is_consented(entry_id=eid, recipient_instance="B") is False
        seen = memory.recall_visible_to(
            reader_instance_id="B", mode="consented",
            lineage_chain=("A", "B", "C"),
        )
        assert "A-consented" not in [e.content for e in seen], (
            "revoked consent still leaks into recall"
        )

    def test_revoke_then_regrant_restores(self, memory):
        # ADR-0027 §2: consent is withdrawable AND re-grantable. The
        # UPSERT in grant_consent must clear revoked_at on the way back.
        seeds = _seed_chain(memory)
        eid = seeds[("A", "consented")].entry_id
        memory.grant_consent(entry_id=eid, recipient_instance="B", granted_by="op")
        memory.revoke_consent(entry_id=eid, recipient_instance="B")
        memory.grant_consent(entry_id=eid, recipient_instance="B", granted_by="op")
        assert memory.is_consented(entry_id=eid, recipient_instance="B") is True

    def test_revoke_returns_false_on_no_active_grant(self, memory):
        seeds = _seed_chain(memory)
        eid = seeds[("A", "consented")].entry_id
        # No grant exists yet.
        assert memory.revoke_consent(entry_id=eid, recipient_instance="B") is False

    def test_consent_does_not_grant_lineage_or_private(self, memory):
        # A grants B consent on A-consented, but A-private and A-lineage
        # are NOT consented entries — the grant is per-entry.
        seeds = _seed_chain(memory)
        memory.grant_consent(
            entry_id=seeds[("A", "consented")].entry_id,
            recipient_instance="B",
            granted_by="op",
        )
        seen = memory.recall_visible_to(
            reader_instance_id="B", mode="consented",
            lineage_chain=("B",),  # B alone — A is not in the chain
        )
        contents = [e.content for e in seen]
        assert "A-consented" in contents
        assert "A-private" not in contents
        assert "A-lineage" not in contents


class TestRecallVisibleToErrors:
    def test_realm_mode_raises(self, memory):
        # ADR-0033 §"What this ADR is not": realm scope is unreachable
        # until federation lands. A request for it should fail loudly,
        # not silently return empty results.
        with pytest.raises(UnknownScopeError, match="realm"):
            memory.recall_visible_to(reader_instance_id="A", mode="realm")

    def test_unknown_mode_raises(self, memory):
        with pytest.raises(UnknownScopeError, match="recall mode"):
            memory.recall_visible_to(reader_instance_id="A", mode="bananas")

    def test_unknown_layer_raises(self, memory):
        with pytest.raises(UnknownLayerError):
            memory.recall_visible_to(
                reader_instance_id="A", mode="private", layer="not_a_layer",
            )

    def test_recall_modes_constant_excludes_realm(self):
        # The exposed RECALL_MODES tuple must NOT include 'realm' until
        # federation lands. Pinning this guards against a quiet drift.
        assert "realm" not in RECALL_MODES
        assert set(RECALL_MODES) == {"private", "lineage", "consented"}


class TestDisclosedCopyMetadata:
    """ADR-0027 §4 minimum-disclosure rows: a recipient's store carries
    a reference + summary, not the original content. The MemoryEntry
    must surface that distinction so UIs and audits don't conflate
    disclosed copies with original observations."""

    def test_originating_row_is_not_disclosed(self, memory):
        e = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="original", layer="episodic",
        )
        loaded = memory.get(e.entry_id)
        assert loaded is not None
        assert loaded.is_disclosed_copy is False
        assert loaded.disclosed_from_entry is None
        assert loaded.disclosed_summary is None
        assert loaded.disclosed_at is None

    def test_disclosed_copy_surfaces_metadata(self, memory):
        # Plant an original then hand-write a disclosed copy. The
        # disclose tool (T14) lands later; this test exercises just
        # the read path on a disclosed-shape row.
        original = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="full original text", layer="episodic",
            scope="consented",
        )
        memory.conn.execute(
            "INSERT INTO memory_entries (entry_id, instance_id, agent_dna, "
            "layer, scope, content, content_digest, tags_json, "
            "consented_to_json, created_at, disclosed_from_entry, "
            "disclosed_summary, disclosed_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("disc-1", "B", "B" * 12, "episodic", "consented",
             "minimum-summary", "sha256:abc", "[]", "[]",
             "2026-04-27T01:00:00Z",
             original.entry_id,
             "B was told about A's finding",
             "2026-04-27T01:00:00Z"),
        )
        loaded = memory.get("disc-1")
        assert loaded is not None
        assert loaded.is_disclosed_copy is True
        assert loaded.disclosed_from_entry == original.entry_id
        assert loaded.disclosed_summary == "B was told about A's finding"
        assert loaded.disclosed_at == "2026-04-27T01:00:00Z"

    def test_query_searches_disclosed_summary(self, memory):
        # An operator searching memory by a term that appears only in
        # the disclosed_summary (because the full content stayed on
        # the originator's store) must still find the disclosed copy.
        original = memory.append(
            instance_id="A", agent_dna="A" * 12,
            content="full original", layer="episodic", scope="consented",
        )
        memory.conn.execute(
            "INSERT INTO memory_entries (entry_id, instance_id, agent_dna, "
            "layer, scope, content, content_digest, tags_json, "
            "consented_to_json, created_at, disclosed_from_entry, "
            "disclosed_summary, disclosed_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("disc-2", "B", "B" * 12, "episodic", "consented",
             "summary content", "sha256:def", "[]", "[]",
             "2026-04-27T01:00:00Z",
             original.entry_id,
             "lateral_movement_signature_xyz",
             "2026-04-27T01:00:00Z"),
        )
        hits = memory.recall_visible_to(
            reader_instance_id="B", mode="consented",
            lineage_chain=("B",),
            query="lateral_movement_signature_xyz",
        )
        ids = [e.entry_id for e in hits]
        assert "disc-2" in ids, (
            f"disclosed_summary not searched in query: {ids}"
        )


# ===========================================================================
# v11 epistemic metadata (ADR-0027-amendment §7.1 + §7.2)
# ===========================================================================
class TestEpistemicMetadata:
    """ADR-0027-amendment §7 — every memory entry carries claim_type +
    confidence + last_challenged_at. Defaults match the schema CHECK
    column DEFAULTs (observation / medium / NULL) so old call sites
    continue to work. Validated at write time so typos surface
    immediately rather than on read."""

    def test_default_claim_type_is_observation(self, memory):
        # Every existing call site that doesn't pass claim_type lands at
        # 'observation' — the safest classification.
        e = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="watched something happen", layer="episodic",
        )
        assert e.claim_type == "observation"
        assert e.confidence == "medium"
        assert e.last_challenged_at is None

    def test_explicit_claim_type_round_trips(self, memory):
        e = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="operator seems to prefer mornings", layer="semantic",
            claim_type="agent_inference",
            confidence="low",
        )
        # ADR-0027-am §7.1: agent_inference is the load-bearing
        # distinction — the agent's derived guess is NOT the operator's
        # stated word. Reading back from the DB confirms it sticks.
        round_trip = memory.get(e.entry_id)
        assert round_trip is not None
        assert round_trip.claim_type == "agent_inference"
        assert round_trip.confidence == "low"

    def test_all_six_claim_types_accepted(self, memory):
        # All six CLAIM_TYPES values are valid at the API + the schema.
        for ct in CLAIM_TYPES:
            e = memory.append(
                instance_id="i1", agent_dna="d" * 12,
                content=f"a {ct} entry", layer="episodic",
                claim_type=ct,
            )
            assert e.claim_type == ct

    def test_all_three_confidence_levels_accepted(self, memory):
        for level in CONFIDENCE_LEVELS:
            e = memory.append(
                instance_id="i1", agent_dna="d" * 12,
                content=f"confidence {level}", layer="episodic",
                confidence=level,
            )
            assert e.confidence == level

    def test_unknown_claim_type_raises(self, memory):
        with pytest.raises(UnknownClaimTypeError):
            memory.append(
                instance_id="i1", agent_dna="d" * 12,
                content="bad type", layer="episodic",
                claim_type="rumor",  # not in CLAIM_TYPES
            )

    def test_unknown_confidence_raises(self, memory):
        with pytest.raises(UnknownConfidenceError):
            memory.append(
                instance_id="i1", agent_dna="d" * 12,
                content="bad confidence", layer="episodic",
                confidence="0.73",  # float-string; should reject
            )

    def test_recall_surfaces_claim_type_and_confidence(self, memory):
        # The read path must propagate the new fields; otherwise
        # downstream UI / voice renderer can't distinguish inferences.
        memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="operator said they prefer X", layer="semantic",
            claim_type="user_statement", confidence="high",
        )
        rows = memory.recall(instance_id="i1")
        assert len(rows) == 1
        assert rows[0].claim_type == "user_statement"
        assert rows[0].confidence == "high"

    def test_schema_check_constraint_rejects_invalid_claim_type(self, memory):
        # The schema-level CHECK constraint is the second line of defense
        # if the Python validator is bypassed (e.g. a future tool writes
        # raw SQL). A direct INSERT with an invalid claim_type must fail.
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            memory.conn.execute(
                "INSERT INTO memory_entries (entry_id, instance_id, "
                "agent_dna, layer, scope, content, content_digest, "
                "tags_json, consented_to_json, created_at, claim_type) "
                "VALUES (?, ?, ?, 'episodic', 'private', '', '', '[]', '[]', '', 'rumor');",
                ("eX", "i1", "d" * 12),
            )

    def test_schema_check_constraint_rejects_invalid_confidence(self, memory):
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            memory.conn.execute(
                "INSERT INTO memory_entries (entry_id, instance_id, "
                "agent_dna, layer, scope, content, content_digest, "
                "tags_json, consented_to_json, created_at, confidence) "
                "VALUES (?, ?, ?, 'episodic', 'private', '', '', '[]', '[]', '', 'absolute');",
                ("eY", "i1", "d" * 12),
            )

    def test_memory_contradictions_table_exists(self, memory):
        # ADR-0027-amendment §7.3 — the contradictions table is part of
        # v11. Verify the schema is in place + a row can be inserted.
        import uuid
        e1 = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="prefer mornings", layer="semantic",
            claim_type="preference",
        )
        e2 = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="actually I prefer evenings", layer="semantic",
            claim_type="preference",
        )
        cid = str(uuid.uuid4())
        memory.conn.execute(
            "INSERT INTO memory_contradictions ("
            "    contradiction_id, earlier_entry_id, later_entry_id,"
            "    contradiction_kind, detected_at, detected_by"
            ") VALUES (?, ?, ?, 'updated', '2026-05-01T00:00:00Z', 'operator');",
            (cid, e1.entry_id, e2.entry_id),
        )
        rows = memory.conn.execute(
            "SELECT contradiction_kind, resolved_at FROM memory_contradictions "
            "WHERE contradiction_id = ?;",
            (cid,),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "updated"   # contradiction_kind
        assert rows[0][1] is None        # resolved_at NULL = open

    def test_memory_contradictions_invalid_kind_rejected(self, memory):
        # Schema CHECK rejects unknown contradiction_kind values.
        import sqlite3, uuid
        e1 = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="x", layer="episodic",
        )
        e2 = memory.append(
            instance_id="i1", agent_dna="d" * 12,
            content="y", layer="episodic",
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            memory.conn.execute(
                "INSERT INTO memory_contradictions ("
                "    contradiction_id, earlier_entry_id, later_entry_id,"
                "    contradiction_kind, detected_at, detected_by"
                ") VALUES (?, ?, ?, 'fictional', '2026-05-01T00:00:00Z', 'op');",
                (str(uuid.uuid4()), e1.entry_id, e2.entry_id),
            )
