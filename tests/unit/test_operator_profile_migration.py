"""ADR-0068 T8 (B319) — profile migration substrate tests.

Coverage:
  - PROFILE_MIGRATIONS empty by default
  - register_profile_migration / double-register refused
  - migrate_raw_profile: no-op same-version, future-version refused,
    missing-step refused, registered migration runs + bumps version
  - load_operator_profile auto-migrates on disk + writes backup
  - Non-integer schema_version refused
  - fsf operator migrate CLI: dry-run, real-run, idempotent,
    restore-from-backup, missing-backup refused
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core import operator_profile as opm
from forest_soul_forge.core.audit_chain import KNOWN_EVENT_TYPES
from forest_soul_forge.core.operator_profile import (
    OperatorProfileError,
    PROFILE_MIGRATIONS,
    _backup_path,
    load_operator_profile,
    migrate_raw_profile,
    register_profile_migration,
)


def _v1_yaml() -> dict:
    return {
        "schema_version": 1,
        "operator": {
            "operator_id": "op_v1", "name": "Old", "preferred_name": "Old",
            "email": "old@x.com", "timezone": "UTC", "locale": "en-US",
            "work_hours": {"start": "09:00", "end": "17:00"},
        },
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-05-01T00:00:00+00:00",
    }


@pytest.fixture(autouse=True)
def _clean_migrations():
    """Each test gets an empty PROFILE_MIGRATIONS + restored
    SCHEMA_VERSION; preserve the original state across tests."""
    saved_migrations = dict(PROFILE_MIGRATIONS)
    saved_version = opm.SCHEMA_VERSION
    PROFILE_MIGRATIONS.clear()
    try:
        yield
    finally:
        PROFILE_MIGRATIONS.clear()
        PROFILE_MIGRATIONS.update(saved_migrations)
        opm.SCHEMA_VERSION = saved_version


# ---------------------------------------------------------------------------
# Framework primitives
# ---------------------------------------------------------------------------

def test_profile_migrations_empty_by_default():
    assert PROFILE_MIGRATIONS == {}


def test_register_decorator_adds_to_registry():
    @register_profile_migration(from_version=1)
    def _m(d):
        return d
    assert PROFILE_MIGRATIONS[1] is _m


def test_double_register_refused():
    @register_profile_migration(from_version=1)
    def _m(d):
        return d
    with pytest.raises(RuntimeError, match="already registered"):
        @register_profile_migration(from_version=1)
        def _m2(d):
            return d


def test_migrate_same_version_is_noop():
    raw = {"schema_version": 1}
    out, applied = migrate_raw_profile(raw, from_version=1, to_version=1)
    assert out is raw or out == raw
    assert applied == []


def test_migrate_future_version_refused():
    """When the file claims a version newer than the daemon —
    operator's daemon is out of date."""
    with pytest.raises(OperatorProfileError, match="newer than"):
        migrate_raw_profile({}, from_version=5, to_version=1)


def test_migrate_missing_step_refused():
    """A v1 file in a v2 daemon with no registered migration
    refuses cleanly rather than silently producing a v1 profile."""
    with pytest.raises(
        OperatorProfileError, match="no profile migration registered",
    ):
        migrate_raw_profile({}, from_version=1, to_version=2)


def test_migrate_walks_chain_and_bumps_version():
    """The registered migration runs and the migrated dict carries
    the new schema_version."""
    @register_profile_migration(from_version=1)
    def _m(d):
        d.setdefault("operator", {})["was_migrated"] = True
        return d

    out, applied = migrate_raw_profile(
        {"schema_version": 1}, from_version=1, to_version=2,
    )
    assert applied == [1]
    assert out["schema_version"] == 2
    assert out["operator"]["was_migrated"] is True


def test_migrate_wraps_inner_exception():
    """A bug in a migration function surfaces as an
    OperatorProfileError with the wrapped exception type."""
    @register_profile_migration(from_version=1)
    def _broken(d):
        raise ValueError("nope")

    with pytest.raises(OperatorProfileError, match="raised"):
        migrate_raw_profile({}, from_version=1, to_version=2)


# ---------------------------------------------------------------------------
# Loader integration
# ---------------------------------------------------------------------------

def test_load_auto_migrates_and_writes_backup(tmp_path):
    """v1 file in v2 daemon: loader runs migration + saves
    migrated form + writes profile.yaml.bak.v1 with original
    content."""
    opm.SCHEMA_VERSION = 2

    @register_profile_migration(from_version=1)
    def _m(d):
        return d  # no-op migration; we just want the version bump

    prof_path = tmp_path / "profile.yaml"
    v1_text = yaml.safe_dump(_v1_yaml())
    prof_path.write_text(v1_text)

    profile = load_operator_profile(prof_path)
    assert profile.schema_version == 2

    bp = _backup_path(prof_path, 1)
    assert bp.exists()
    assert bp.read_text() == v1_text

    # On-disk file is now v2
    reloaded = yaml.safe_load(prof_path.read_text())
    assert reloaded["schema_version"] == 2


def test_load_at_current_version_no_backup(tmp_path):
    """When the file is already at SCHEMA_VERSION, no backup
    is written + no migration runs."""
    opm.SCHEMA_VERSION = 1

    prof_path = tmp_path / "profile.yaml"
    prof_path.write_text(yaml.safe_dump(_v1_yaml()))

    load_operator_profile(prof_path)
    backups = list(tmp_path.glob("profile.yaml.bak*"))
    assert backups == []


def test_load_refuses_non_integer_schema_version(tmp_path):
    prof_path = tmp_path / "profile.yaml"
    bad = _v1_yaml()
    bad["schema_version"] = "not-an-int"
    prof_path.write_text(yaml.safe_dump(bad))
    with pytest.raises(
        OperatorProfileError, match="must be an integer",
    ):
        load_operator_profile(prof_path)


def test_load_refuses_future_version_no_migration(tmp_path):
    """File at v5 but daemon is at v1 — refuse rather than try
    to walk a chain in the wrong direction."""
    opm.SCHEMA_VERSION = 1

    prof_path = tmp_path / "profile.yaml"
    bad = _v1_yaml()
    bad["schema_version"] = 5
    prof_path.write_text(yaml.safe_dump(bad))
    with pytest.raises(OperatorProfileError, match="newer than"):
        load_operator_profile(prof_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _make_parser():
    from forest_soul_forge.cli.operator_cmd import add_subparser
    root = argparse.ArgumentParser(prog="fsf")
    sub = root.add_subparsers(dest="cmd")
    add_subparser(sub)
    return root


def test_cli_dry_run_does_not_touch_disk(tmp_path, capsys):
    opm.SCHEMA_VERSION = 2

    @register_profile_migration(from_version=1)
    def _m(d):
        return d

    prof_path = tmp_path / "profile.yaml"
    original = yaml.safe_dump(_v1_yaml())
    prof_path.write_text(original)

    parser = _make_parser()
    args = parser.parse_args([
        "operator", "migrate",
        "--profile-path", str(prof_path), "--dry-run",
    ])
    rc = args._run(args)
    assert rc == 0
    # File untouched.
    assert prof_path.read_text() == original
    # Output includes the migration banner.
    captured = capsys.readouterr()
    assert "Migration from v1 -> v2" in captured.out


def test_cli_real_migrate_writes_backup_and_replaces(tmp_path):
    opm.SCHEMA_VERSION = 2

    @register_profile_migration(from_version=1)
    def _m(d):
        return d

    prof_path = tmp_path / "profile.yaml"
    prof_path.write_text(yaml.safe_dump(_v1_yaml()))

    parser = _make_parser()
    args = parser.parse_args([
        "operator", "migrate",
        "--profile-path", str(prof_path),
    ])
    rc = args._run(args)
    assert rc == 0
    new_raw = yaml.safe_load(prof_path.read_text())
    assert new_raw["schema_version"] == 2
    assert (tmp_path / "profile.yaml.bak.v1").exists()


def test_cli_already_current_is_noop(tmp_path):
    """File at SCHEMA_VERSION already: CLI exits 0 + prints
    'nothing to do' + leaves disk untouched."""
    opm.SCHEMA_VERSION = 1

    prof_path = tmp_path / "profile.yaml"
    original = yaml.safe_dump(_v1_yaml())
    prof_path.write_text(original)

    parser = _make_parser()
    args = parser.parse_args([
        "operator", "migrate", "--profile-path", str(prof_path),
    ])
    rc = args._run(args)
    assert rc == 0
    assert prof_path.read_text() == original


def test_cli_restore_from_backup(tmp_path):
    opm.SCHEMA_VERSION = 2

    @register_profile_migration(from_version=1)
    def _m(d):
        return d

    prof_path = tmp_path / "profile.yaml"
    prof_path.write_text(yaml.safe_dump(_v1_yaml()))

    parser = _make_parser()
    # First migrate so a backup exists.
    args = parser.parse_args([
        "operator", "migrate", "--profile-path", str(prof_path),
    ])
    args._run(args)
    # Then restore.
    args = parser.parse_args([
        "operator", "migrate", "--profile-path", str(prof_path),
        "--restore-from-backup",
    ])
    rc = args._run(args)
    assert rc == 0
    restored = yaml.safe_load(prof_path.read_text())
    assert restored["schema_version"] == 1


def test_cli_restore_refuses_when_no_backup(tmp_path):
    """No .bak.v* siblings → restore fails with rc=2."""
    prof_path = tmp_path / "profile.yaml"
    prof_path.write_text(yaml.safe_dump(_v1_yaml()))

    parser = _make_parser()
    args = parser.parse_args([
        "operator", "migrate", "--profile-path", str(prof_path),
        "--restore-from-backup",
    ])
    rc = args._run(args)
    assert rc == 2


def test_cli_refuses_missing_profile(tmp_path):
    parser = _make_parser()
    args = parser.parse_args([
        "operator", "migrate",
        "--profile-path", str(tmp_path / "nope.yaml"),
    ])
    rc = args._run(args)
    assert rc == 2


# ---------------------------------------------------------------------------
# Audit event registration
# ---------------------------------------------------------------------------

def test_migration_audit_event_registered():
    assert "operator_profile_migrated" in KNOWN_EVENT_TYPES
