"""Tests for ``forest_soul_forge.core.genre_engine`` (ADR-0021 T1).

Coverage:
- TestRealCatalog        — config/genres.yaml loads + has all 7 genres + claims 3 known roles
- TestLoadErrors         — every malformed-input path raises GenreEngineError
- TestPublicAPI          — genre_for / roles_for / all_genres / can_spawn
- TestEmptyEngine        — fallback path returns sensible empty defaults
- TestValidateAgainstTraitEngine — separate ADR-0021 invariant function
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.core.genre_engine import (
    GenreDef,
    GenreEngine,
    GenreEngineError,
    RiskProfile,
    empty_engine,
    load_genres,
    validate_against_trait_engine,
)


# Project root → config/genres.yaml. Computed relative to this test file so
# pytest can run from any cwd inside the dockerized harness.
REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_GENRES = REPO_ROOT / "config" / "genres.yaml"


# ---------------------------------------------------------------------------
# A helper to build a minimal valid YAML body for the malformed-input tests.
# Every TestLoadErrors case mutates ONE field and asserts the raise.
# ---------------------------------------------------------------------------
def _good_yaml() -> str:
    return yaml.safe_dump({
        "version": "0.1",
        "genres": {
            "watcher": {
                "description": "passive watching",
                "risk_profile": {"max_side_effects": "read_only"},
                "default_kit_pattern": ["passive_observation"],
                "trait_emphasis": ["vigilance"],
                "memory_pattern": "short_retention",
                "spawn_compatibility": ["watcher"],
                "roles": ["network_watcher"],
            },
        },
    })


def _write(tmp_path, content):
    p = tmp_path / "genres.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# ===========================================================================
# Real catalog
# ===========================================================================
class TestRealCatalog:
    """The shipped config/genres.yaml is the canonical source of truth.
    These tests pin its public shape so accidental regressions surface."""

    def test_loads_without_error(self):
        engine = load_genres(REAL_GENRES)
        assert engine.version == "0.1"
        assert engine.source_path == REAL_GENRES

    def test_has_seven_genres(self):
        engine = load_genres(REAL_GENRES)
        assert set(engine.genres.keys()) == {
            "observer", "investigator", "communicator", "actuator",
            "guardian", "researcher", "companion",
        }

    def test_existing_trait_engine_roles_claimed(self):
        # The three roles the trait engine ships with today must each be
        # claimed by exactly one genre. ADR-0021 invariant.
        engine = load_genres(REAL_GENRES)
        for role in ("network_watcher", "log_analyst", "anomaly_investigator"):
            assert role in engine.role_to_genre, (
                f"role {role!r} not claimed by any genre — ADR-0021 invariant violated"
            )

    def test_companion_carries_local_only_provider_constraint(self):
        # ADR-0008 Phase 5 floor — Companion must carry the local-only
        # provider constraint as a structural property, not a procedural one.
        engine = load_genres(REAL_GENRES)
        companion = engine.genres["companion"]
        assert companion.risk_profile.provider_constraint == "local_only"

    def test_actuator_is_external_tier(self):
        engine = load_genres(REAL_GENRES)
        actuator = engine.genres["actuator"]
        assert actuator.risk_profile.max_side_effects == "external"

    def test_observer_kit_is_read_only(self):
        engine = load_genres(REAL_GENRES)
        observer = engine.genres["observer"]
        assert observer.risk_profile.max_side_effects == "read_only"

    def test_observer_cannot_spawn_actuator(self):
        # ADR-0021 explicit: observation should not be its own action-taker.
        # An observer that needs to act routes through a communicator.
        engine = load_genres(REAL_GENRES)
        assert engine.can_spawn("observer", "actuator") is False
        assert engine.can_spawn("observer", "investigator") is True

    def test_companion_cannot_spawn_outside_genre(self):
        # ADR-0021: Companion's strict isolation. Privacy floor + memory
        # contract make cross-genre spawn unsafe by default.
        engine = load_genres(REAL_GENRES)
        for other in ("observer", "investigator", "communicator", "actuator",
                       "guardian", "researcher"):
            assert engine.can_spawn("companion", other) is False, (
                f"companion → {other} should be forbidden by default"
            )
        assert engine.can_spawn("companion", "companion") is True

    def test_every_role_appears_in_exactly_one_genre(self):
        engine = load_genres(REAL_GENRES)
        seen: set[str] = set()
        for genre in engine.genres.values():
            for role in genre.roles:
                assert role not in seen, (
                    f"role {role!r} duplicated across genres — load should have rejected"
                )
                seen.add(role)


# ===========================================================================
# Load errors
# ===========================================================================
class TestLoadErrors:
    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(GenreEngineError, match="not found"):
            load_genres(tmp_path / "does_not_exist.yaml")

    def test_yaml_parse_error_raises(self, tmp_path):
        p = _write(tmp_path, "version: 0.1\ngenres: { :::: not valid yaml")
        with pytest.raises(GenreEngineError, match="YAML parse error"):
            load_genres(p)

    def test_root_not_mapping_raises(self, tmp_path):
        p = _write(tmp_path, "- just\n- a\n- list\n")
        with pytest.raises(GenreEngineError, match="root must be a mapping"):
            load_genres(p)

    def test_missing_version_raises(self, tmp_path):
        p = _write(tmp_path, yaml.safe_dump({"genres": {}}))
        with pytest.raises(GenreEngineError, match="'version' is required"):
            load_genres(p)

    def test_empty_genres_raises(self, tmp_path):
        p = _write(tmp_path, yaml.safe_dump({"version": "0.1", "genres": {}}))
        with pytest.raises(GenreEngineError, match="at least one genre"):
            load_genres(p)

    def test_unknown_side_effects_tier_raises(self, tmp_path):
        body = yaml.safe_load(_good_yaml())
        body["genres"]["watcher"]["risk_profile"]["max_side_effects"] = "telekinesis"
        p = _write(tmp_path, yaml.safe_dump(body))
        with pytest.raises(GenreEngineError, match="max_side_effects"):
            load_genres(p)

    def test_unknown_provider_constraint_raises(self, tmp_path):
        body = yaml.safe_load(_good_yaml())
        body["genres"]["watcher"]["risk_profile"]["provider_constraint"] = "frontier_only"
        p = _write(tmp_path, yaml.safe_dump(body))
        with pytest.raises(GenreEngineError, match="provider_constraint"):
            load_genres(p)

    def test_role_in_two_genres_raises(self, tmp_path):
        body = yaml.safe_load(_good_yaml())
        body["genres"]["double_claimer"] = {
            "description": "claims same role",
            "risk_profile": {"max_side_effects": "read_only"},
            "default_kit_pattern": ["x"],
            "trait_emphasis": ["y"],
            "memory_pattern": "short_retention",
            "spawn_compatibility": ["double_claimer"],
            "roles": ["network_watcher"],   # already claimed by watcher
        }
        p = _write(tmp_path, yaml.safe_dump(body))
        with pytest.raises(GenreEngineError, match="claimed by both"):
            load_genres(p)

    def test_spawn_compat_unknown_genre_raises(self, tmp_path):
        body = yaml.safe_load(_good_yaml())
        body["genres"]["watcher"]["spawn_compatibility"] = ["watcher", "phantom_genre"]
        p = _write(tmp_path, yaml.safe_dump(body))
        with pytest.raises(GenreEngineError, match="unknown genre"):
            load_genres(p)

    def test_empty_roles_list_raises(self, tmp_path):
        body = yaml.safe_load(_good_yaml())
        body["genres"]["watcher"]["roles"] = []
        p = _write(tmp_path, yaml.safe_dump(body))
        with pytest.raises(GenreEngineError, match="at least one role"):
            load_genres(p)

    def test_empty_spawn_compat_raises(self, tmp_path):
        body = yaml.safe_load(_good_yaml())
        body["genres"]["watcher"]["spawn_compatibility"] = []
        p = _write(tmp_path, yaml.safe_dump(body))
        with pytest.raises(GenreEngineError, match="spawn_compatibility"):
            load_genres(p)

    def test_missing_required_field_raises(self, tmp_path):
        body = yaml.safe_load(_good_yaml())
        del body["genres"]["watcher"]["memory_pattern"]
        p = _write(tmp_path, yaml.safe_dump(body))
        with pytest.raises(GenreEngineError, match="memory_pattern"):
            load_genres(p)


# ===========================================================================
# Public API behavior on the real catalog
# ===========================================================================
class TestPublicAPI:
    @pytest.fixture(scope="class")
    def engine(self):
        return load_genres(REAL_GENRES)

    def test_genre_for_known_role(self, engine):
        assert engine.genre_for("network_watcher").name == "observer"
        assert engine.genre_for("anomaly_investigator").name == "investigator"

    def test_genre_for_unknown_role_raises(self, engine):
        with pytest.raises(GenreEngineError, match="not claimed"):
            engine.genre_for("definitely_not_a_real_role")

    def test_roles_for_known_genre(self, engine):
        roles = engine.roles_for("observer")
        assert "network_watcher" in roles
        assert "log_analyst" in roles

    def test_roles_for_unknown_genre_raises(self, engine):
        with pytest.raises(GenreEngineError, match="unknown genre"):
            engine.roles_for("phantom")

    def test_all_genres_returns_seven(self, engine):
        assert len(engine.all_genres()) == 7

    def test_can_spawn_unknown_parent_raises(self, engine):
        with pytest.raises(GenreEngineError, match="unknown parent genre"):
            engine.can_spawn("phantom", "observer")

    def test_can_spawn_unknown_child_returns_false(self, engine):
        # Defensive: an unrecognized child is never compatible. Doesn't
        # raise — caller may be probing.
        assert engine.can_spawn("observer", "definitely_not_a_real_genre") is False


# ===========================================================================
# Empty fallback
# ===========================================================================
class TestEmptyEngine:
    def test_empty_engine_has_no_genres(self):
        e = empty_engine()
        assert e.genres == {}
        assert e.role_to_genre == {}
        assert e.source_path is None
        assert e.all_genres() == ()

    def test_empty_engine_genre_for_raises_clearly(self):
        e = empty_engine()
        with pytest.raises(GenreEngineError, match="not claimed"):
            e.genre_for("network_watcher")


# ===========================================================================
# Trait-engine cross-validation (ADR-0021 invariant)
# ===========================================================================
class TestValidateAgainstTraitEngine:
    def test_compliant_engine_returns_empty(self):
        engine = load_genres(REAL_GENRES)
        unclaimed = validate_against_trait_engine(
            engine, ["network_watcher", "log_analyst", "anomaly_investigator"]
        )
        assert unclaimed == []

    def test_unclaimed_role_surfaces(self):
        engine = load_genres(REAL_GENRES)
        unclaimed = validate_against_trait_engine(
            engine, ["network_watcher", "freshly_invented_role_v1"]
        )
        assert unclaimed == ["freshly_invented_role_v1"]

    def test_empty_engine_reports_all_as_unclaimed(self):
        e = empty_engine()
        unclaimed = validate_against_trait_engine(e, ["a", "b", "c"])
        assert unclaimed == ["a", "b", "c"]
