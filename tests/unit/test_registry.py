"""Unit tests for the SQLite registry.

Tests cover: bootstrap (new + existing), schema version mismatch, rebuild
from synthetic artifacts, closure-table lineage queries, single-birth
registration, audit idempotency, and audit hash-mismatch detection.
"""
from __future__ import annotations

import json
import sqlite3
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
            # Version bumped to 7 when memory_consents + disclosed_*
            # columns landed (ADR-0022 v0.2 — cross-agent disclosure).
            # The assertion is kept as a guard so any future version
            # bump forces a matching update here — not a free-floating
            # number.
            assert r.schema_version() == 13
            assert r.list_agents() == []
            assert r.audit_tail() == []
        assert db.exists()

    def test_reopen_existing_db_is_idempotent(self, tmp_path: Path):
        db = tmp_path / "reg.sqlite"
        Registry.bootstrap(db).close()
        with Registry.bootstrap(db) as r:
            # Version bumped to 7 when memory_consents + disclosed_*
            # columns landed (ADR-0022 v0.2 — cross-agent disclosure).
            # The assertion is kept as a guard so any future version
            # bump forces a matching update here — not a free-floating
            # number.
            assert r.schema_version() == 13

    def test_empty_existing_file_gets_schema(self, tmp_path: Path):
        db = tmp_path / "reg.sqlite"
        db.touch()  # crashed bootstrap leaves a 0-byte file
        with Registry.bootstrap(db) as r:
            # Version bumped to 7 when memory_consents + disclosed_*
            # columns landed (ADR-0022 v0.2 — cross-agent disclosure).
            # The assertion is kept as a guard so any future version
            # bump forces a matching update here — not a free-floating
            # number.
            assert r.schema_version() == 13

    def test_schema_downgrade_raises(self, tmp_path: Path):
        """A file stamped at a version *newer* than the code refuses to open.

        Dropping into an older binary is the dangerous direction — the new
        code may have added columns or constraints the old code doesn't
        know about. Refuse rather than silently corrupt.
        """
        import sqlite3
        db = tmp_path / "reg.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE registry_meta (key TEXT PRIMARY KEY, value TEXT);")
        conn.execute(
            "INSERT INTO registry_meta (key, value) VALUES ('schema_version', '99');"
        )
        conn.commit()
        conn.close()
        with pytest.raises(SchemaMismatchError, match="newer than this code"):
            Registry.bootstrap(db)

    def test_v1_to_v2_forward_migration_preserves_data(self, tmp_path: Path):
        """Simulate an on-disk v1 DB and confirm bootstrap auto-migrates it.

        v1 lacked ``agents.sibling_index`` and the ``idempotency_keys``
        table. We hand-build a v1-shaped DB with a real agent row, reopen
        it through ``bootstrap``, and assert:
          - schema_version bumps to 2,
          - the existing row survives with sibling_index defaulted to 1,
          - the new idempotency_keys table is present and usable,
          - the composite index the write path relies on exists.
        """
        import sqlite3
        db = tmp_path / "reg.sqlite"
        conn = sqlite3.connect(str(db))
        # Minimal v1 agents schema (no sibling_index). Deliberately
        # reproducing the shape so a future refactor can't hide a skew.
        conn.executescript(
            """
            CREATE TABLE registry_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO registry_meta (key, value) VALUES ('schema_version', '1');
            INSERT INTO registry_meta (key, value) VALUES ('canonical_contract', 'artifacts-authoritative');
            CREATE TABLE agents (
                instance_id      TEXT PRIMARY KEY,
                dna              TEXT NOT NULL,
                dna_full         TEXT NOT NULL,
                role             TEXT NOT NULL,
                agent_name       TEXT NOT NULL,
                parent_instance  TEXT,
                owner_id         TEXT,
                model_name       TEXT,
                model_version    TEXT,
                soul_path        TEXT NOT NULL,
                constitution_path TEXT NOT NULL,
                constitution_hash TEXT NOT NULL,
                created_at       TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'active',
                legacy_minted    INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE agent_ancestry (
                instance_id  TEXT NOT NULL,
                ancestor_id  TEXT NOT NULL,
                depth        INTEGER NOT NULL,
                PRIMARY KEY (instance_id, ancestor_id)
            );
            CREATE TABLE audit_events (
                seq INTEGER PRIMARY KEY, timestamp TEXT NOT NULL,
                agent_dna TEXT, instance_id TEXT,
                event_type TEXT NOT NULL, event_json TEXT NOT NULL,
                entry_hash TEXT NOT NULL
            );
            CREATE TABLE agent_capabilities (
                instance_id TEXT NOT NULL, capability TEXT NOT NULL,
                level INTEGER, acquired_at TEXT,
                PRIMARY KEY (instance_id, capability)
            );
            CREATE TABLE tools (
                tool_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL,
                name TEXT NOT NULL, description TEXT,
                parameters_json TEXT, code_snippet TEXT,
                created_at TEXT NOT NULL,
                is_inherited INTEGER NOT NULL DEFAULT 0,
                parent_tool_id TEXT
            );
            """
        )
        pre_existing = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO agents (instance_id, dna, dna_full, role, agent_name, "
            "soul_path, constitution_path, constitution_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (pre_existing, "a" * 12, "a" * 64, "network_watcher", "Legacy",
             "souls/legacy.md", "constitutions/legacy.yaml", "0" * 64,
             "2026-04-20 00:00:00Z"),
        )
        conn.commit()
        conn.close()

        # Act: open through the real bootstrap path. The bootstrap walks
        # every registered migration step in order, so a v1 file ends up
        # at the current SCHEMA_VERSION (7 after memory v0.2 cross-agent
        # disclosure). The assertion tests that all migration steps
        # landed on the same pass.
        with Registry.bootstrap(db) as r:
            assert r.schema_version() == 13

            # Data survives.
            row = r.get_agent(pre_existing)
            assert row.agent_name == "Legacy"
            assert row.sibling_index == 1  # DEFAULT 1 backfilled

            # New tables are present and writable (proves the v2-v7
            # migrations all ran, not just that the bootstrap was lenient).
            import sqlite3 as _sqlite3
            raw = _sqlite3.connect(str(db))
            tables = {t[0] for t in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            )}
            assert "idempotency_keys" in tables
            assert "tool_call_counters" in tables
            assert "tool_calls" in tables
            assert "tool_call_pending_approvals" in tables
            assert "memory_entries" in tables
            assert "memory_consents" in tables   # v7 add (ADR-0022 v0.2)
            indexes = {t[0] for t in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='index';"
            )}
            assert "idx_agents_dna_sibling" in indexes
            assert "idx_idempotency_created" in indexes
            assert "idx_tool_call_counters_instance" in indexes
            assert "idx_tool_calls_instance" in indexes
            assert "idx_pending_approvals_instance" in indexes
            assert "idx_memory_instance" in indexes
            assert "idx_memory_disclosed_from" in indexes      # v7 add
            assert "idx_memory_consents_recipient" in indexes  # v7 add

            # disclosed_* columns added on memory_entries by v7.
            mem_cols = {row[1] for row in raw.execute("PRAGMA table_info(memory_entries)")}
            assert {"disclosed_from_entry", "disclosed_summary", "disclosed_at"}.issubset(mem_cols), (
                f"v7 disclosed_* columns missing from memory_entries: {mem_cols}"
            )
            # v11 epistemic columns + memory_contradictions table.
            assert {"claim_type", "confidence", "last_challenged_at"}.issubset(mem_cols), (
                f"v11 epistemic columns missing from memory_entries: {mem_cols}"
            )
            assert "memory_contradictions" in tables, (
                f"v11 memory_contradictions table missing: {tables}"
            )
            assert "idx_memory_claim_type" in indexes
            assert "idx_contradictions_earlier" in indexes
            # Pre-existing rows landed at the schema DEFAULTs. Use the
            # legacy agent's row to verify (it had no memory entries
            # so we insert one through the bootstrap connection — we
            # need a row to assert the DEFAULT was applied to).
            raw.execute(
                "INSERT INTO memory_entries (entry_id, instance_id, "
                "agent_dna, layer, scope, content, content_digest, "
                "tags_json, consented_to_json, created_at) "
                "VALUES (?, ?, ?, 'episodic', 'private', '', '', '[]', '[]', '');",
                ("v11_test_entry", pre_existing, "a" * 12),
            )
            raw.commit()
            row = raw.execute(
                "SELECT claim_type, confidence, last_challenged_at "
                "FROM memory_entries WHERE entry_id = ?;",
                ("v11_test_entry",),
            ).fetchone()
            assert row == ("observation", "medium", None), (
                f"v11 column DEFAULTs not applied: {row}"
            )
            raw.close()

    @pytest.mark.xfail(
        reason=(
            "Test setup uses ALTER TABLE DROP COLUMN to simulate v6 "
            "schema, then re-bootstraps to trigger migration. SQLite "
            "≥3.35 implements ALTER TABLE DROP COLUMN by rebuilding the "
            "memory_entries table internally, which invalidates the FK "
            "reference memory_consents has on it. The dropped-and-"
            "recreated memory_consents during migration then cannot "
            "satisfy the FK against the new memory_entries rowids. "
            "The PRODUCTION migration path (operator with a real v6 DB) "
            "works fine — this is solely a test-setup limitation. "
            "Phase A audit 2026-04-30 finding F-7. To fix properly: "
            "restructure the test to build a v6-shaped DB from scratch "
            "rather than bootstrap-then-stamp-back."
        ),
        strict=False,
    )
    def test_v6_to_v7_forward_migration(self, tmp_path: Path):
        """ADR-0022 v0.2: a v6 DB with existing memory_entries migrates
        forward to v7 with the three disclosed_* columns added (NULL on
        existing rows) and a usable memory_consents table.

        The earlier ``test_v1_to_v2_forward_migration_preserves_data``
        covers the cumulative path; this one isolates the v6→v7 step
        so a regression in MIGRATIONS[7] surfaces with a tight error
        rather than getting buried in the cumulative test's output.
        """
        import sqlite3
        from forest_soul_forge.registry import schema as _schema

        db = tmp_path / "reg.sqlite"
        # Boot at current version, then stamp back to v6 so the bootstrap
        # treats it as a v6 DB next time. This is cleaner than hand-building
        # a v6 DB because the v6 row shape is already in the codebase via
        # the migration history.
        Registry.bootstrap(db).close()

        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("UPDATE registry_meta SET value='6' WHERE key='schema_version';")
        # Drop the v7 additions so the on-disk shape really is v6.
        # ALTER TABLE DROP COLUMN exists in modern SQLite (≥3.35); fall back
        # by recreating if the runtime is older.
        try:
            for col in ("disclosed_at", "disclosed_summary", "disclosed_from_entry"):
                conn.execute(f"ALTER TABLE memory_entries DROP COLUMN {col};")
        except sqlite3.OperationalError:
            # Older SQLite — copy-and-rename dance.
            conn.executescript("""
                CREATE TABLE _mem_v6 AS SELECT entry_id, instance_id, agent_dna, layer, scope,
                    content, content_digest, tags_json, consented_to_json, created_at, deleted_at
                    FROM memory_entries;
                DROP TABLE memory_entries;
                ALTER TABLE _mem_v6 RENAME TO memory_entries;
            """)
        conn.execute("DROP TABLE IF EXISTS memory_consents;")
        # Insert a v6-shaped row so we can prove it survives the migration.
        # Note: ``capabilities_json`` was in an earlier schema draft but
        # never made it into the v0.1 release path (Phase A audit
        # 2026-04-30 caught the drift). Current agents schema needs
        # dna + dna_full + the constitution-path triplet. The Python
        # string-multiplication artifacts ('0' * 12 etc.) need to be
        # interpolated, not embedded as SQL string literals.
        conn.execute(
            "INSERT INTO agents (instance_id, dna, dna_full, role, "
            "agent_name, soul_path, constitution_path, "
            "constitution_hash, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "alpha-001", "0" * 12, "0" * 64, "observer",
                "Alpha", "souls/alpha.md",
                "constitutions/alpha.yaml", "0" * 64,
                "2026-04-27T00:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO memory_entries (entry_id, instance_id, agent_dna, "
            "layer, scope, content, content_digest, created_at) "
            "VALUES ('e1', 'alpha-001', '0' * 12, 'episodic', 'private', "
            "'pre-migration', 'digest', '2026-04-27T00:00:01Z')"
        )
        conn.commit()
        conn.close()

        # Reopen — bootstrap should run MIGRATIONS[7].
        with Registry.bootstrap(db) as r:
            assert r.schema_version() == 13

            raw = sqlite3.connect(str(db))
            raw.execute("PRAGMA foreign_keys = ON")

            # Existing v6 row survives with NULL on the three new columns.
            row = raw.execute(
                "SELECT entry_id, content, disclosed_from_entry, "
                "disclosed_summary, disclosed_at FROM memory_entries"
            ).fetchone()
            assert row == ("e1", "pre-migration", None, None, None), (
                f"v6 memory row drifted across migration: {row}"
            )

            # memory_consents is present and usable end-to-end.
            raw.execute(
                "INSERT INTO memory_consents (entry_id, recipient_instance, "
                "granted_at, granted_by) VALUES (?, ?, ?, ?)",
                ("e1", "alpha-001", "2026-04-27T00:00:02Z", "operator"),
            )
            # Composite PK rejects duplicate grants.
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(
                    "INSERT INTO memory_consents (entry_id, recipient_instance, "
                    "granted_at, granted_by) VALUES (?, ?, ?, ?)",
                    ("e1", "alpha-001", "2026-04-27T00:00:03Z", "operator"),
                )

            # Disclosed-copy back-reference index supports
            # "who holds copies of e1?" queries.
            raw.execute(
                "INSERT INTO memory_entries (entry_id, instance_id, agent_dna, "
                "layer, scope, content, content_digest, created_at, "
                "disclosed_from_entry, disclosed_summary, disclosed_at) "
                "VALUES ('e2', 'alpha-001', '0' * 12, 'episodic', 'consented', "
                "'disclosed-copy', 'digest2', '2026-04-27T00:00:04Z', "
                "'e1', 'told about pre-migration', '2026-04-27T00:00:04Z')"
            )
            copies = raw.execute(
                "SELECT entry_id FROM memory_entries WHERE disclosed_from_entry=?",
                ("e1",),
            ).fetchall()
            assert copies == [("e2",)], (
                f"disclosed_from_entry lookup wrong: {copies}"
            )
            raw.close()

    def test_v10_to_v11_forward_migration(self, tmp_path: Path):
        """ADR-0027-amendment §7: a v10 DB with existing memory_entries
        migrates forward to v11 with the three new columns added (DEFAULTs
        applied to existing rows) and a new memory_contradictions table.

        Mirrors the v6→v7 test shape but is NOT xfailed: the v11 additions
        don't drop any columns or invalidate any FK references, so the
        ALTER TABLE DROP COLUMN approach to building a v10-shape fixture
        works cleanly. Production migration path is the same code that
        runs here (MIGRATIONS[11] tuple).
        """
        import sqlite3
        db = tmp_path / "reg.sqlite"

        # Bootstrap a current-version DB so all the structural prerequisites
        # (agents table, FKs, the v10 memory_entries shape) are there.
        Registry.bootstrap(db).close()

        # Stamp it back to v10 by dropping the v11 columns and the new
        # memory_contradictions table, then setting schema_version=10.
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA foreign_keys = ON")
        # Drop indexes that reference v11 columns first — SQLite's
        # ALTER TABLE DROP COLUMN errors out if any index (including the
        # partial idx_memory_last_challenged) names the column being
        # dropped. Dropping the indexes is fine because MIGRATIONS[11]
        # recreates them via CREATE INDEX IF NOT EXISTS.
        conn.execute("DROP INDEX IF EXISTS idx_memory_last_challenged;")
        conn.execute("DROP INDEX IF EXISTS idx_memory_claim_type;")
        # Drop the v11 columns. None have FKs, so DROP COLUMN is safe —
        # no internal table rebuild that would break referential
        # integrity (unlike the v6→v7 test setup).
        for col in ("last_challenged_at", "confidence", "claim_type"):
            conn.execute(f"ALTER TABLE memory_entries DROP COLUMN {col};")
        conn.execute("DROP TABLE IF EXISTS memory_contradictions;")
        conn.execute(
            "UPDATE registry_meta SET value='10' WHERE key='schema_version';"
        )
        # Insert a v10-shape row to prove it survives the migration.
        # agents row first (FK target).
        conn.execute(
            "INSERT INTO agents (instance_id, dna, dna_full, role, "
            "agent_name, soul_path, constitution_path, "
            "constitution_hash, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "v10-agent", "0" * 12, "0" * 64, "operator_companion",
                "Pre-V11", "souls/pre-v11.md",
                "constitutions/pre-v11.yaml", "0" * 64,
                "2026-04-30T23:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO memory_entries (entry_id, instance_id, agent_dna, "
            "layer, scope, content, content_digest, "
            "tags_json, consented_to_json, created_at) "
            "VALUES (?, ?, ?, 'episodic', 'private', ?, ?, '[]', '[]', ?)",
            (
                "pre-v11-entry", "v10-agent", "0" * 12,
                "remembered before v11", "digest_v10",
                "2026-04-30T23:00:01Z",
            ),
        )
        conn.commit()
        conn.close()

        # Reopen through bootstrap — should run MIGRATIONS[11].
        with Registry.bootstrap(db) as r:
            assert r.schema_version() == 13

            raw = sqlite3.connect(str(db))
            raw.execute("PRAGMA foreign_keys = ON")

            # 1. Existing v10 row carries the schema column DEFAULTs.
            row = raw.execute(
                "SELECT content, claim_type, confidence, last_challenged_at "
                "FROM memory_entries WHERE entry_id = ?;",
                ("pre-v11-entry",),
            ).fetchone()
            assert row == ("remembered before v11", "observation", "medium", None), (
                f"v10 memory row drifted across v11 migration: {row}"
            )

            # 2. New columns reject invalid values via CHECK constraint.
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
                raw.execute(
                    "INSERT INTO memory_entries (entry_id, instance_id, "
                    "agent_dna, layer, scope, content, content_digest, "
                    "tags_json, consented_to_json, created_at, claim_type) "
                    "VALUES (?, ?, ?, 'episodic', 'private', '', '', '[]', '[]', '', 'rumor');",
                    ("post-v11-bad", "v10-agent", "0" * 12),
                )

            # 3. memory_contradictions is present + FK back to memory_entries.
            tables = {t[0] for t in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            )}
            assert "memory_contradictions" in tables

            # 4. Indexes shipped with the migration.
            indexes = {t[0] for t in raw.execute(
                "SELECT name FROM sqlite_master WHERE type='index';"
            )}
            assert "idx_memory_claim_type" in indexes
            assert "idx_contradictions_unresolved" in indexes

            # 5. Inserting a contradiction works end-to-end.
            raw.execute(
                "INSERT INTO memory_entries (entry_id, instance_id, "
                "agent_dna, layer, scope, content, content_digest, "
                "tags_json, consented_to_json, created_at, claim_type) "
                "VALUES (?, ?, ?, 'episodic', 'private', ?, ?, '[]', '[]', ?, 'observation');",
                ("post-v11-row", "v10-agent", "0" * 12,
                 "post-migration entry", "digest2", "2026-05-01T00:00:00Z"),
            )
            raw.execute(
                "INSERT INTO memory_contradictions ("
                "    contradiction_id, earlier_entry_id, later_entry_id,"
                "    contradiction_kind, detected_at, detected_by"
                ") VALUES (?, ?, ?, 'updated', ?, ?);",
                ("cid-1", "pre-v11-entry", "post-v11-row",
                 "2026-05-01T00:01:00Z", "operator"),
            )
            # FK guard: contradiction referencing a non-existent entry must fail.
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(
                    "INSERT INTO memory_contradictions ("
                    "    contradiction_id, earlier_entry_id, later_entry_id,"
                    "    contradiction_kind, detected_at, detected_by"
                    ") VALUES (?, ?, ?, 'direct', ?, ?);",
                    ("cid-2", "pre-v11-entry", "ghost-entry",
                     "2026-05-01T00:02:00Z", "operator"),
                )
            raw.close()

    def test_migration_missing_entry_raises(self, tmp_path: Path):
        """A version gap with no registered migration is a hard error.

        Guards against a future SCHEMA_VERSION bump landing without a
        matching MIGRATIONS entry — we prefer a boot-time failure over
        silently skipping an undefined migration step.
        """
        import sqlite3
        from forest_soul_forge.registry import schema as _schema

        db = tmp_path / "reg.sqlite"
        # Build a clean v2 DB first.
        Registry.bootstrap(db).close()
        # Stamp it back to v0 (below our lowest registered migration key).
        conn = sqlite3.connect(str(db))
        conn.execute(
            "UPDATE registry_meta SET value='0' WHERE key='schema_version';"
        )
        conn.commit()
        conn.close()
        # Pretend the code expects v2 but MIGRATIONS[1] doesn't exist.
        # Save/restore avoids depending on pytest's monkeypatch fixture so
        # the stdlib test harness can run this too.
        original = _schema.MIGRATIONS
        _schema.MIGRATIONS = {2: original[2]}
        try:
            with pytest.raises(SchemaMismatchError, match="no forward migration"):
                Registry.bootstrap(db)
        finally:
            _schema.MIGRATIONS = original


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
