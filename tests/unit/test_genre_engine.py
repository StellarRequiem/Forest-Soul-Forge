"""Tests for ``forest_soul_forge.core.genre_engine`` (ADR-0021 T1, ADR-0033).

Coverage:
- TestRealCatalog        — config/genres.yaml loads + has all 10 genres + claims known roles
- TestLoadErrors         — every malformed-input path raises GenreEngineError
- TestPublicAPI          — genre_for / roles_for / all_genres / can_spawn
- TestEmptyEngine        — fallback path returns sensible empty defaults
- TestValidateAgainstTraitEngine — separate ADR-0021 invariant function
- TestMemoryCeiling      — ADR-0027 §1+§5 ceiling parsing + comparator (added with ADR-0033)
- TestSecuritySwarm      — ADR-0033 three-tier genre family + 9 canonical roles
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
    genre_requires_approval,
    load_genres,
    memory_scope_exceeds_ceiling,
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

    def test_has_thirteen_genres(self):
        # ADR-0021 shipped 7; ADR-0033 added the security_low/mid/high
        # family (10 total); ADR-003X added the web_observer/researcher/
        # actuator family (13 total).
        engine = load_genres(REAL_GENRES)
        assert set(engine.genres.keys()) == {
            "observer", "investigator", "communicator", "actuator",
            "guardian", "researcher", "companion",
            "security_low", "security_mid", "security_high",
            "web_observer", "web_researcher", "web_actuator",
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

    def test_all_genres_returns_thirteen(self, engine):
        # 7 from ADR-0021 + 3 from ADR-0033 + 3 from ADR-003X.
        assert len(engine.all_genres()) == 13

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


# ===========================================================================
# Memory ceiling — ADR-0027 §1+§5 (added with ADR-0033)
# ===========================================================================
class TestMemoryCeiling:
    """Per-genre memory write ceilings — Companion's `private` floor is the
    canonical example, but every genre carries a ceiling now."""

    def test_real_catalog_ceilings_match_adr_0027(self):
        # ADR-0027 §5 names the ceilings; pin them so a future yaml edit
        # that drifts this contract surfaces.
        engine = load_genres(REAL_GENRES)
        expected = {
            "companion":    "private",
            "guardian":     "private",
            "observer":     "lineage",
            "investigator": "lineage",
            "actuator":     "lineage",
            "researcher":   "consented",
            "communicator": "consented",
            # ADR-0033 additions:
            "security_low":  "lineage",
            "security_mid":  "lineage",
            "security_high": "private",
        }
        for gname, ceiling in expected.items():
            actual = engine.genres[gname].risk_profile.memory_ceiling
            assert actual == ceiling, (
                f"genre {gname!r}: ceiling drifted from ADR-0027/ADR-0033 — "
                f"expected {ceiling!r}, got {actual!r}"
            )

    def test_omitted_ceiling_defaults_to_private(self, tmp_path):
        # When risk_profile omits memory_ceiling the loader picks "private"
        # — the strictest scope, safest fallback for forgotten config.
        body = yaml.safe_load(_good_yaml())  # _good_yaml has no ceiling
        p = _write(tmp_path, yaml.safe_dump(body))
        engine = load_genres(p)
        assert engine.genres["watcher"].risk_profile.memory_ceiling == "private"

    def test_unknown_ceiling_raises(self, tmp_path):
        body = yaml.safe_load(_good_yaml())
        body["genres"]["watcher"]["risk_profile"]["memory_ceiling"] = "telepathy"
        p = _write(tmp_path, yaml.safe_dump(body))
        with pytest.raises(GenreEngineError, match="memory_ceiling"):
            load_genres(p)

    def test_scope_exceeds_ceiling_companion_case(self):
        # Companion's identity rule from ADR-0027 §5: cannot widen to lineage.
        assert memory_scope_exceeds_ceiling("lineage", "private") is True
        assert memory_scope_exceeds_ceiling("realm", "private") is True
        # Same scope is fine.
        assert memory_scope_exceeds_ceiling("private", "private") is False

    def test_scope_exceeds_ceiling_lineage_case(self):
        # Mid-tier security: lineage allowed; consented or wider is rejected.
        assert memory_scope_exceeds_ceiling("private", "lineage") is False
        assert memory_scope_exceeds_ceiling("lineage", "lineage") is False
        assert memory_scope_exceeds_ceiling("consented", "lineage") is True
        assert memory_scope_exceeds_ceiling("realm", "lineage") is True

    def test_scope_exceeds_ceiling_unknown_fails_closed(self):
        # Unknown scope index → 0 (treated as private). Better to falsely
        # ALLOW the write and surface in audit (private is fine everywhere)
        # than to falsely permit a wider write.
        assert memory_scope_exceeds_ceiling("brand_new_scope", "lineage") is False


# ===========================================================================
# Security Swarm — ADR-0033 three-tier genre family
# ===========================================================================
class TestSecuritySwarm:
    """The new genres + 9 canonical roles that ADR-0033 adds. Pinned so a
    future yaml edit that drifts the swarm shape surfaces."""

    @pytest.fixture(scope="class")
    def engine(self):
        return load_genres(REAL_GENRES)

    def test_security_low_claims_three_roles(self, engine):
        roles = engine.roles_for("security_low")
        assert set(roles) == {"patch_patrol", "gatekeeper", "log_lurker"}

    def test_security_mid_claims_three_roles(self, engine):
        roles = engine.roles_for("security_mid")
        assert set(roles) == {"anomaly_ace", "net_ninja", "response_rogue"}

    def test_security_high_claims_three_roles(self, engine):
        roles = engine.roles_for("security_high")
        assert set(roles) == {"zero_zero", "vault_warden", "deception_duke"}

    def test_low_can_escalate_to_mid_but_not_high(self, engine):
        # ADR-0033: tier-skipping is forbidden. low → mid only.
        assert engine.can_spawn("security_low", "security_mid") is True
        assert engine.can_spawn("security_low", "security_high") is False

    def test_mid_can_escalate_to_high(self, engine):
        assert engine.can_spawn("security_mid", "security_high") is True

    def test_mid_can_spawn_back_down_to_low(self, engine):
        # Mid investigators sometimes spawn observers for deeper passive
        # watching — same rationale as Investigator → Observer in ADR-0021.
        assert engine.can_spawn("security_mid", "security_low") is True

    def test_high_is_a_sink(self, engine):
        # ADR-0033: apex tier never spawns out. Only self-spawn.
        for other in (
            "security_low", "security_mid",
            "observer", "investigator", "communicator",
            "actuator", "guardian", "researcher", "companion",
        ):
            assert engine.can_spawn("security_high", other) is False, (
                f"security_high should not be allowed to spawn into {other!r}"
            )
        assert engine.can_spawn("security_high", "security_high") is True

    def test_high_carries_local_only_provider_constraint(self, engine):
        # ADR-0033: key material + posture data must not leave the machine.
        # Mirrors Companion's floor for the same reason.
        high = engine.genres["security_high"]
        assert high.risk_profile.provider_constraint == "local_only"

    def test_high_ceiling_is_private(self, engine):
        # ADR-0033: cross-tier disclosure must be explicit; private default.
        assert engine.genres["security_high"].risk_profile.memory_ceiling == "private"

    def test_low_max_side_effects_is_read_only(self, engine):
        # ADR-0033: low tier is observation/audit only, never actuating.
        assert engine.genres["security_low"].risk_profile.max_side_effects == "read_only"

    def test_mid_max_side_effects_is_external(self, engine):
        # ADR-0033 §4 puts isolate_process.v1 (external side-effect) in
        # the mid tier; runtime gating is per-tool requires_human_approval
        # (tool_policy's external_always rule), not the genre ceiling.
        # A network-only mid would orphan ResponseRogue's containment
        # path. See genres.yaml security_mid risk_profile comment.
        # (Earlier test asserted "network" — that was from a pre-final
        # draft of the design; YAML is the source of truth.)
        assert engine.genres["security_mid"].risk_profile.max_side_effects == "external"

    def test_high_max_side_effects_is_external(self, engine):
        # High tier has the broadest side-effect ceiling because it includes
        # privileged actuation (dynamic_policy, isolate_process). Constrained
        # by approval queue + provider_constraint, not by tier.
        assert engine.genres["security_high"].risk_profile.max_side_effects == "external"

    def test_no_swarm_role_is_double_claimed(self, engine):
        # Guard against a future edit that accidentally lists e.g.
        # log_lurker under both security_low and security_mid.
        nine_roles = (
            "patch_patrol", "gatekeeper", "log_lurker",
            "anomaly_ace", "net_ninja", "response_rogue",
            "zero_zero", "vault_warden", "deception_duke",
        )
        seen: dict[str, str] = {}
        for genre in engine.genres.values():
            for role in genre.roles:
                if role in nine_roles:
                    assert role not in seen, (
                        f"swarm role {role!r} double-claimed by "
                        f"{seen[role]!r} and {genre.name!r}"
                    )
                    seen[role] = genre.name
        assert set(seen.keys()) == set(nine_roles), (
            "not every swarm role was claimed by some security_* genre"
        )


# ===========================================================================
# ADR-0033 A4 — per-genre approval policy graduation
# ===========================================================================
class TestGenreApprovalPolicy:
    """genre_requires_approval is the dispatcher's gate-source #2 (the
    tool-level constitution constraint is gate #1). It only elevates
    for the three security tiers; every other genre passes through."""

    def test_security_high_elevates_everything_beyond_read_only(self):
        # The high tier assumes hostility; even network calls could
        # exfiltrate. Read-only is the only tier that auto-passes.
        assert genre_requires_approval("security_high", "read_only") is False
        assert genre_requires_approval("security_high", "network") is True
        assert genre_requires_approval("security_high", "filesystem") is True
        assert genre_requires_approval("security_high", "external") is True

    def test_security_mid_passes_network_but_gates_writes(self):
        # Mid tier needs DNS lookups + threat-intel queries to work
        # without a click on every call. filesystem/external still gate.
        assert genre_requires_approval("security_mid", "read_only") is False
        assert genre_requires_approval("security_mid", "network") is False
        assert genre_requires_approval("security_mid", "filesystem") is True
        assert genre_requires_approval("security_mid", "external") is True

    def test_security_low_never_elevates(self):
        # Low tier is bounded to read_only by its genre risk_profile;
        # the policy is a no-op so the existing tool-level config
        # remains the only path to approval.
        for se in ("read_only", "network", "filesystem", "external"):
            assert genre_requires_approval("security_low", se) is False

    def test_non_security_genres_pass_through(self):
        # ADR-0033 A4 must not change behavior for the seven existing
        # genres — they keep the ADR-0019 T3 "tool config wins" rule.
        for g in ("observer", "investigator", "communicator",
                  "actuator", "guardian", "researcher", "companion"):
            for se in ("read_only", "network", "filesystem", "external"):
                assert genre_requires_approval(g, se) is False, (
                    f"non-security genre {g!r}/{se!r} should not elevate"
                )

    def test_none_or_unknown_genre_passes_through(self):
        # An agent with no genre (unclaimed role) or an unknown genre
        # name behaves like the existing seven — no elevation.
        assert genre_requires_approval(None, "external") is False
        assert genre_requires_approval("definitely_not_a_real_genre", "external") is False

    def test_case_insensitive(self):
        # The genre name is lowercased before lookup so a soul.md or
        # constitution entry that drifted on case still matches.
        assert genre_requires_approval("Security_High", "network") is True
        assert genre_requires_approval("SECURITY_LOW", "external") is False
