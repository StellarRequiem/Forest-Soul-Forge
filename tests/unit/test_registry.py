"""Unit tests for the SQLite registry.

Tests cover: bootstrap (new + existing), schema version mismatch, rebuild
from synthetic artifacts, closure-table lineage queries, single-birth
registration, audit idempotency, and audit hash-mismatch detection.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest


def _pad_dna(short: str) -> str:
    """Expand a 12-char short DNA into a 64-char full DNA by zero-padding.

    Tests need dna_full[:12] == short_dna so audit-event resolution works.
    """
    if len(short) != 12:
        raise AssertionError(f"test helper expects 12-char short DNA, got {short!r}")
    return short + "0" * (64 - len(short))

from forest_soul_forge.registry import Registry, RegistryError
from forest_soul_forge.registry.ingest import (
    ParsedAuditEntry,
    parse_soul_file,
    synthesize_legacy_instance_id,
)
from forest_soul_forge.registry.registry import (
    DuplicateInstanceError,
    SchemaMismatchError,
    UnknownAgentError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _minimal_soul_text(
    *,
    dna: str,
    dna_full: str,
    role: str = "network_watcher",
    agent_name: str = "TestAgent",
    parent_dna: str | None = None,
    spawned_by: str | None = None,
    lineage: list[str] | None = None,
    lineage_depth: int = 0,
    created_at: str = "2026-04-23 12:00:00Z",
    instance_id: str | None = None,
    parent_instance: str | None = None,
    constitution_file: str = "test.constitution.yaml",
    constitution_hash: str = "0" * 64,
) -> str:
    """Emit soul-like frontmatter without using dedent — avoids indent traps."""
    lineage = lineage or []
    parent_dna_val = "null" if parent_dna is None else parent_dna
    spawned_by_val = "null" if spawned_by is None else f'"{spawned_by}"'

    lines: list[str] = [
        "---",
        "schema_version: 1",
        f"dna: {dna}",
        f'dna_full: "{dna_full}"',
        f"role: {role}",
        f'agent_name: "{agent_name}"',
        'agent_version: "v1"',
        f'generated_at: "{created_at}"',
        f'constitution_hash: "{constitution_hash}"',
        f'constitution_file: "{constitution_file}"',
        f"parent_dna: {parent_dna_val}",
        f"spawned_by: {spawned_by_val}",
    ]
    if lineage:
        lines.append("lineage:")
        lines.extend(f"  - {x}" for x in lineage)
    else:
        lines.append("lineage: []")
    lines.append(f"lineage_depth: {lineage_depth}")
    if instance_id:
        lines.append(f"instance_id: {instance_id}")
    if parent_instance:
        lines.append(f"parent_instance: {parent_instance}")
    lines.append("---")
    lines.append("")
    lines.append("# Body")
    lines.append("")
    lines.append("minimal test soul.")
    lines.append("")
    return "\n".join(lines)


def _write_soul(tmp_path: Path, name: str, **kwargs) -> Path:
    p = tmp_path / f"{name}.soul.md"
    p.write_text(_minimal_soul_text(**kwargs), encoding="utf-8")
    # Also drop a placeholder constitution.yaml so the path exists on disk.
    (tmp_path / kwargs.get("constitution_file", "test.constitution.yaml")).write_text(
        "# placeholder\n", encoding="utf-8"
    )
    return p


def _audit_entry(
    seq: int,
    *,
    event_type: str = "agent_created",
    agent_dna: str | None = None,
    entry_hash: str | None = None,
) -> ParsedAuditEntry:
    return ParsedAuditEntry(
        seq=seq,
        timestamp=f"2026-04-23T12:00:{seq:02d}Z",
        prev_hash="GENESIS" if seq == 0 else f"hash-{seq - 1}",
        entry_hash=entry_hash or f"hash-{seq}",
        agent_dna=agent_dna,
        event_type=event_type,
        event_data={"seq": seq},
    )


# ---------------------------------------------------------------------------
# Bootstrap / schema
# ---------------------------------------------------------------------------
class TestBootstrap:
    def test_fresh_db_creates_schema(self, tmp_path: Path):
        db = tmp_path / "reg.sqlite"
        with Registry.bootstrap(db) as r:
            assert r.schema_version() == 1
            assert r.list_agents() == []
            assert r.audit_tail() == []
        assert db.exists()

    def test_reopen_existing_db_is_idempotent(self, tmp_path: Path):
        db = tmp_path / "reg.sqlite"
        Registry.bootstrap(db).close()
        with Registry.bootstrap(db) as r:
            assert r.schema_version() == 1

    def test_empty_existing_file_gets_schema(self, tmp_path: Path):
        db = tmp_path / "reg.sqlite"
        db.touch()  # crashed bootstrap leaves a 0-byte file
        with Registry.bootstrap(db) as r:
            assert r.schema_version() == 1

    def test_schema_mismatch_raises(self, tmp_path: Path):
        import sqlite3
        db = tmp_path / "reg.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE registry_meta (key TEXT PRIMARY KEY, value TEXT);")
        conn.execute(
            "INSERT INTO registry_meta (key, value) VALUES ('schema_version', '99');"
        )
        conn.commit()
        conn.close()
        with pytest.raises(SchemaMismatchError):
            Registry.bootstrap(db)


# ---------------------------------------------------------------------------
# register_birth
# ---------------------------------------------------------------------------
class TestRegisterBirth:
    def test_mints_uuid_v4_when_absent(self, tmp_path: Path):
        soul_path = _write_soul(
            tmp_path,
            "a",
            dna="aaaaaaaaaaaa",
            dna_full="a" * 64,
        )
        soul = parse_soul_file(soul_path)
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            inst = r.register_birth(soul)
            # UUID v4: 36-char hex with dashes, version digit == "4"
            u = uuid.UUID(inst)
            assert u.version == 4
            agent = r.get_agent(inst)
            assert agent.dna == "aaaaaaaaaaaa"
            assert agent.legacy_minted is False

    def test_respects_explicit_instance_id(self, tmp_path: Path):
        explicit = str(uuid.uuid4())
        soul_path = _write_soul(
            tmp_path,
            "a",
            dna="bbbbbbbbbbbb",
            dna_full="b" * 64,
            instance_id=explicit,
        )
        soul = parse_soul_file(soul_path)
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            inst = r.register_birth(soul)
            assert inst == explicit

    def test_duplicate_instance_id_raises(self, tmp_path: Path):
        explicit = str(uuid.uuid4())
        soul_path = _write_soul(
            tmp_path, "a",
            dna="cccccccccccc", dna_full="c" * 64, instance_id=explicit,
        )
        soul = parse_soul_file(soul_path)
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.register_birth(soul)
            with pytest.raises(DuplicateInstanceError):
                r.register_birth(soul)

    def test_self_ancestry_edge_inserted(self, tmp_path: Path):
        soul_path = _write_soul(
            tmp_path, "a",
            dna="dddddddddddd", dna_full="d" * 64,
        )
        soul = parse_soul_file(soul_path)
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            inst = r.register_birth(soul)
            # Self-edge should be present at depth 0; no other ancestors.
            ancestors = r.get_ancestors(inst)
            assert ancestors == []
            descendants = r.get_descendants(inst)
            assert descendants == []


# ---------------------------------------------------------------------------
# Lineage / closure table
# ---------------------------------------------------------------------------
class TestLineage:
    def test_three_generation_lineage(self, tmp_path: Path):
        parent_inst = str(uuid.uuid4())
        child_inst = str(uuid.uuid4())
        grand_inst = str(uuid.uuid4())

        parent_soul = parse_soul_file(_write_soul(
            tmp_path, "parent",
            dna="111111111111", dna_full="1" * 64,
            instance_id=parent_inst,
            created_at="2026-04-23 12:00:00Z",
        ))
        child_soul = parse_soul_file(_write_soul(
            tmp_path, "child",
            dna="222222222222", dna_full="2" * 64,
            parent_dna="111111111111",
            parent_instance=parent_inst,
            lineage=["111111111111"],
            lineage_depth=1,
            instance_id=child_inst,
            created_at="2026-04-23 12:00:01Z",
        ))
        grand_soul = parse_soul_file(_write_soul(
            tmp_path, "grand",
            dna="333333333333", dna_full="3" * 64,
            parent_dna="222222222222",
            parent_instance=child_inst,
            lineage=["222222222222", "111111111111"],
            lineage_depth=2,
            instance_id=grand_inst,
            created_at="2026-04-23 12:00:02Z",
        ))

        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.register_birth(parent_soul)
            r.register_birth(child_soul)
            r.register_birth(grand_soul)

            assert [a.instance_id for a in r.get_ancestors(grand_inst)] == [
                child_inst, parent_inst,
            ]
            assert [a.instance_id for a in r.get_descendants(parent_inst)] == [
                child_inst, grand_inst,
            ]
            # Child has one ancestor (parent) and one descendant (grand).
            assert [a.instance_id for a in r.get_ancestors(child_inst)] == [parent_inst]
            assert [a.instance_id for a in r.get_descendants(child_inst)] == [grand_inst]


# ---------------------------------------------------------------------------
# Audit ingest
# ---------------------------------------------------------------------------
class TestAudit:
    def test_register_audit_event_appends(self, tmp_path: Path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.register_audit_event(_audit_entry(1))
            r.register_audit_event(_audit_entry(2))
            tail = r.audit_tail(10)
            assert [e.seq for e in tail] == [2, 1]

    def test_replayed_event_is_idempotent(self, tmp_path: Path):
        entry = _audit_entry(42)
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.register_audit_event(entry)
            r.register_audit_event(entry)  # same seq, same hash
            tail = r.audit_tail(10)
            assert len(tail) == 1

    def test_hash_mismatch_on_same_seq_raises(self, tmp_path: Path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.register_audit_event(_audit_entry(7, entry_hash="aaa"))
            with pytest.raises(RegistryError, match="entry_hash mismatch"):
                r.register_audit_event(_audit_entry(7, entry_hash="bbb"))


# ---------------------------------------------------------------------------
# Rebuild from artifacts
# ---------------------------------------------------------------------------
class TestRebuild:
    def test_rebuild_legacy_souls_mints_deterministic_instance_ids(self, tmp_path: Path):
        # Two legacy souls (no instance_id) with parent/child relationship.
        parent_full = _pad_dna("aa11aa11aa11")
        child_full = _pad_dna("bb22bb22bb22")
        _write_soul(
            tmp_path, "legacy_parent",
            dna="aa11aa11aa11", dna_full=parent_full,
            created_at="2026-04-01 10:00:00Z",
        )
        _write_soul(
            tmp_path, "legacy_child",
            dna="bb22bb22bb22", dna_full=child_full,
            parent_dna="aa11aa11aa11",
            lineage=["aa11aa11aa11"],
            lineage_depth=1,
            created_at="2026-04-01 10:01:00Z",
        )

        db = tmp_path / "reg.sqlite"
        audit = tmp_path / "audit.jsonl"
        audit.write_text("", encoding="utf-8")

        with Registry.bootstrap(db) as r:
            report = r.rebuild_from_artifacts(tmp_path, audit)
            assert report.agents_loaded == 2
            assert report.legacy_instance_ids_minted == 2
            assert report.orphaned_parent_refs == ()

            # Parent and child are linked via synthesized instance_ids.
            # rebuild_from_artifacts passes the soul path relative to
            # artifacts_dir into the synthesis function; mirror that here.
            expected_parent = synthesize_legacy_instance_id(
                parent_full, "2026-04-01 10:00:00Z", "legacy_parent.soul.md"
            )
            expected_child = synthesize_legacy_instance_id(
                child_full, "2026-04-01 10:01:00Z", "legacy_child.soul.md"
            )
            parent = r.get_agent(expected_parent)
            child = r.get_agent(expected_child)
            assert parent.legacy_minted is True
            assert child.legacy_minted is True
            assert child.parent_instance == expected_parent
            desc = r.get_descendants(expected_parent)
            assert [a.instance_id for a in desc] == [expected_child]

        # Reopen and run rebuild again — synthesized IDs are stable, so the
        # report should be identical.
        with Registry.bootstrap(db) as r:
            report2 = r.rebuild_from_artifacts(tmp_path, audit)
            assert report2.agents_loaded == 2
            assert report2.legacy_instance_ids_minted == 2

    def test_rebuild_orphan_parent_is_reported(self, tmp_path: Path):
        # Child references a parent_dna that isn't in the scan.
        _write_soul(
            tmp_path, "orphan_child",
            dna="cc33cc33cc33", dna_full=_pad_dna("cc33cc33cc33"),
            parent_dna="deaddeaddead",
            lineage=["deaddeaddead"],
            lineage_depth=1,
            created_at="2026-04-01 11:00:00Z",
        )
        db = tmp_path / "reg.sqlite"
        audit = tmp_path / "audit.jsonl"
        audit.write_text("", encoding="utf-8")
        with Registry.bootstrap(db) as r:
            report = r.rebuild_from_artifacts(tmp_path, audit)
            assert report.agents_loaded == 1
            assert len(report.orphaned_parent_refs) == 1

    def test_rebuild_disambiguates_parent_by_spawned_by(self, tmp_path: Path):
        # Two parent-candidate souls share the exact same short DNA and
        # created_at — a real case in examples/ where a role default and a
        # lineage root of the same role collide. The child names its
        # intended parent via spawned_by, so the registry must pick that one
        # rather than defaulting to alphabetical or temporal tie-break.
        shared_short = "aa11aa11aa11"
        shared_full = _pad_dna(shared_short)
        shared_ts = "2026-04-01 10:00:00Z"
        _write_soul(
            tmp_path, "role_default",
            dna=shared_short, dna_full=shared_full,
            agent_name="DefaultParent",
            created_at=shared_ts,
            constitution_file="role_default.constitution.yaml",
        )
        _write_soul(
            tmp_path, "lineage_root",
            dna=shared_short, dna_full=shared_full,
            agent_name="LineageRoot",
            created_at=shared_ts,
            constitution_file="lineage_root.constitution.yaml",
        )
        _write_soul(
            tmp_path, "child",
            dna="bb22bb22bb22", dna_full=_pad_dna("bb22bb22bb22"),
            agent_name="ChildOfLineageRoot",
            parent_dna=shared_short,
            spawned_by="LineageRoot",
            lineage=[shared_short],
            lineage_depth=1,
            created_at="2026-04-01 10:00:01Z",
            constitution_file="child.constitution.yaml",
        )

        db = tmp_path / "reg.sqlite"
        audit = tmp_path / "audit.jsonl"
        audit.write_text("", encoding="utf-8")
        with Registry.bootstrap(db) as r:
            report = r.rebuild_from_artifacts(tmp_path, audit)
            assert report.agents_loaded == 3
            assert report.orphaned_parent_refs == ()

            # The child should be parented to LineageRoot, not DefaultParent,
            # even though both candidates share DNA and timestamp.
            agents = r.list_agents()
            child = next(a for a in agents if a.agent_name == "ChildOfLineageRoot")
            parent = r.get_agent(child.parent_instance)
            assert parent.agent_name == "LineageRoot"

    def test_rebuild_ingests_audit_chain(self, tmp_path: Path):
        _write_soul(
            tmp_path, "a",
            dna="dd44dd44dd44", dna_full=_pad_dna("dd44dd44dd44"),
            created_at="2026-04-01 12:00:00Z",
        )
        audit = tmp_path / "audit.jsonl"
        lines = [
            json.dumps({
                "seq": 0,
                "timestamp": "2026-04-01T12:00:00Z",
                "prev_hash": "GENESIS",
                "entry_hash": "genesis-hash",
                "agent_dna": None,
                "event_type": "chain_created",
                "event_data": {},
            }),
            json.dumps({
                "seq": 1,
                "timestamp": "2026-04-01T12:00:01Z",
                "prev_hash": "genesis-hash",
                "entry_hash": "h1",
                "agent_dna": "dd44dd44dd44",
                "event_type": "agent_created",
                "event_data": {"role": "network_watcher"},
            }),
        ]
        audit.write_text("\n".join(lines) + "\n", encoding="utf-8")

        db = tmp_path / "reg.sqlite"
        with Registry.bootstrap(db) as r:
            report = r.rebuild_from_artifacts(tmp_path, audit)
            assert report.audit_events == 2
            tail = r.audit_tail(10)
            assert [e.seq for e in tail] == [1, 0]
            # Second event's instance_id got resolved from its DNA.
            second = [e for e in tail if e.seq == 1][0]
            assert second.instance_id is not None

    def test_update_status(self, tmp_path: Path):
        soul = parse_soul_file(_write_soul(
            tmp_path, "a",
            dna="ee55ee55ee55", dna_full=_pad_dna("ee55ee55ee55"),
        ))
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            inst = r.register_birth(soul)
            r.update_status(inst, "archived")
            assert r.get_agent(inst).status == "archived"
            actives = r.list_agents(status="active")
            assert actives == []
            archived = r.list_agents(status="archived")
            assert len(archived) == 1

    def test_update_status_unknown_agent_raises(self, tmp_path: Path):
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            with pytest.raises(UnknownAgentError):
                r.update_status("not-a-real-id", "archived")


# ---------------------------------------------------------------------------
# List / filter
# ---------------------------------------------------------------------------
class TestQueries:
    def test_list_agents_filters_by_role(self, tmp_path: Path):
        s1 = parse_soul_file(_write_soul(
            tmp_path, "w1",
            dna="ff66ff66ff66", dna_full=_pad_dna("ff66ff66ff66"),
            role="network_watcher",
        ))
        s2 = parse_soul_file(_write_soul(
            tmp_path, "a1",
            dna="ff77ff77ff77", dna_full=_pad_dna("ff77ff77ff77"),
            role="log_analyst",
            created_at="2026-04-23 12:00:01Z",
            constitution_file="a1.constitution.yaml",
        ))
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.register_birth(s1)
            r.register_birth(s2)
            assert len(r.list_agents(role="network_watcher")) == 1
            assert len(r.list_agents(role="log_analyst")) == 1
            assert len(r.list_agents()) == 2

    def test_get_agent_by_dna_returns_all_incarnations(self, tmp_path: Path):
        same_dna = "abcabcabcabc"
        same_full = _pad_dna(same_dna)
        s1 = parse_soul_file(_write_soul(
            tmp_path, "i1",
            dna=same_dna, dna_full=same_full,
            created_at="2026-04-23 12:00:00Z",
        ))
        s2 = parse_soul_file(_write_soul(
            tmp_path, "i2",
            dna=same_dna, dna_full=same_full,
            created_at="2026-04-23 12:00:01Z",
            constitution_file="i2.constitution.yaml",
        ))
        with Registry.bootstrap(tmp_path / "reg.sqlite") as r:
            r.register_birth(s1)
            r.register_birth(s2)
            rows = r.get_agent_by_dna(same_dna)
            assert len(rows) == 2
            rows_full = r.get_agent_by_dna(same_full)
            assert len(rows_full) == 2
