"""Unit tests for trait_engine."""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.core.trait_engine import (
    InvalidTraitValueError,
    SchemaError,
    TIER_WEIGHTS,
    TraitEngine,
    UnknownRoleError,
    UnknownTraitError,
    _compare,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "config" / "trait_tree.yaml"


@pytest.fixture(scope="module")
def engine() -> TraitEngine:
    return TraitEngine(YAML_PATH)


# ----- loading ------------------------------------------------------------
class TestLoading:
    def test_loads_real_yaml(self, engine: TraitEngine) -> None:
        assert engine.version == "0.2"

    def test_all_six_domains_present(self, engine: TraitEngine) -> None:
        # v0.2 added 'embodiment' domain.
        assert set(engine.domains) == {
            "security", "audit", "emotional", "cognitive", "communication", "embodiment"
        }

    def test_expected_trait_count(self, engine: TraitEngine) -> None:
        # v0.1 had 26 traits (ADR-0001). v0.2 added 3 to embodiment.presentation,
        # bringing the total to 29.
        assert len(engine.list_traits()) == 29

    def test_expected_role_count(self, engine: TraitEngine) -> None:
        # 43 roles total:
        #   5 originals + 9 swarm (ADR-0033) + 3 SW-track (ADR-0034)
        #   + 1 verifier (ADR-0036) + 24 Burst 124 role-expansion roles
        #   (ADR-0044 §"role inventory") + 1 ADR-0047 assistant (B156)
        # Asserting the absolute count rather than a >= floor catches
        # accidental role duplication / typos in trait_tree.yaml at the
        # same time.
        assert len(engine.roles) == 43
        # Spot-check one representative role from each tranche so a single
        # tranche-removal would surface here too.
        assert "network_watcher" in engine.roles       # original 5
        assert "operator_companion" in engine.roles    # original 5
        assert "log_lurker" in engine.roles            # ADR-0033 low swarm
        assert "vault_warden" in engine.roles          # ADR-0033 high swarm
        assert "system_architect" in engine.roles      # ADR-0034 SW-track
        assert "assistant" in engine.roles             # ADR-0047 (B156)
        assert "code_reviewer" in engine.roles         # ADR-0034 SW-track
        assert "verifier_loop" in engine.roles         # ADR-0036 verifier
        # Burst 124 — one per tranche (8 tranches × ~3 roles each)
        assert "dashboard_watcher" in engine.roles     # T1 observer ext
        assert "incident_correlator" in engine.roles   # T2 investigator ext
        assert "briefer" in engine.roles               # T3 communicator ext
        assert "alert_dispatcher" in engine.roles      # T4 actuator ext
        assert "refusal_arbiter" in engine.roles       # T5 guardian ext
        assert "knowledge_consolidator" in engine.roles  # T6 researcher ext
        assert "journaling_partner" in engine.roles    # T7 companion ext (ADR-0038)
        assert "web_actuator" in engine.roles          # T8 web (ADR-003X)

    def test_missing_yaml_raises(self, tmp_path: Path) -> None:
        with pytest.raises(SchemaError, match="not found"):
            TraitEngine(tmp_path / "nope.yaml")


# ----- tier weights & traits ---------------------------------------------
class TestTraits:
    def test_known_trait_has_expected_tier(self, engine: TraitEngine) -> None:
        caution = engine.get_trait("caution")
        assert caution.tier == "primary"
        assert caution.tier_weight == TIER_WEIGHTS["primary"]
        assert caution.domain == "security"
        assert caution.subdomain == "defensive_posture"

    def test_tertiary_trait(self, engine: TraitEngine) -> None:
        sarcasm = engine.get_trait("sarcasm")
        assert sarcasm.tier == "tertiary"
        assert sarcasm.tier_weight == pytest.approx(0.3)

    def test_unknown_trait_raises(self, engine: TraitEngine) -> None:
        with pytest.raises(UnknownTraitError):
            engine.get_trait("not_a_real_trait")

    def test_traits_unique_across_tree(self, engine: TraitEngine) -> None:
        names = [t.name for t in engine.list_traits()]
        assert len(names) == len(set(names))

    def test_list_traits_by_domain(self, engine: TraitEngine) -> None:
        sec = engine.list_traits("security")
        assert all(t.domain == "security" for t in sec)
        assert len(sec) >= 3

    def test_paranoia_was_renamed(self, engine: TraitEngine) -> None:
        # ADR-0001 renamed paranoia to threat_prior.
        with pytest.raises(UnknownTraitError):
            engine.get_trait("paranoia")
        assert engine.get_trait("threat_prior").domain == "security"

    def test_every_trait_has_three_scale_bands(self, engine: TraitEngine) -> None:
        # Post-ADR-0002 prose change: scale_mid must be populated for all traits
        # so moderate-band values produce clean output instead of low/high concat.
        missing = [t.name for t in engine.list_traits() if not (t.scale_low and t.scale_mid and t.scale_high)]
        assert missing == [], f"Traits missing at least one scale band: {missing}"


# ----- roles --------------------------------------------------------------
class TestRoles:
    def test_unknown_role_raises(self, engine: TraitEngine) -> None:
        with pytest.raises(UnknownRoleError):
            engine.get_role("nonsense_role")

    def test_network_watcher_security_dominant(self, engine: TraitEngine) -> None:
        role = engine.get_role("network_watcher")
        assert role.domain_weights["security"] == 2.0
        assert role.domain_weights["emotional"] < role.domain_weights["security"]

    def test_operator_companion_emotional_dominant(self, engine: TraitEngine) -> None:
        role = engine.get_role("operator_companion")
        assert role.domain_weights["emotional"] >= 1.5
        assert role.domain_weights["emotional"] >= role.domain_weights["security"]

    def test_no_role_weight_below_floor(self, engine: TraitEngine) -> None:
        for role in engine.roles.values():
            for w in role.domain_weights.values():
                assert w >= engine.min_domain_weight


# ----- profiles -----------------------------------------------------------
class TestProfile:
    def test_build_profile_uses_defaults(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        assert profile.role == "network_watcher"
        caution_default = engine.get_trait("caution").default
        assert profile.trait_values["caution"] == caution_default

    def test_overrides_apply(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher", overrides={"caution": 42})
        assert profile.trait_values["caution"] == 42

    def test_unknown_override_trait_raises(self, engine: TraitEngine) -> None:
        with pytest.raises(UnknownTraitError):
            engine.build_profile("network_watcher", overrides={"not_real": 50})

    def test_out_of_range_value_raises(self, engine: TraitEngine) -> None:
        with pytest.raises(InvalidTraitValueError):
            engine.build_profile("network_watcher", overrides={"caution": 150})

    def test_effective_trait_weight_combines_role_and_tier(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        # caution: primary (tier 1.0) in security (role weight 2.0) = 2.0
        assert engine.effective_trait_weight(profile, "caution") == pytest.approx(2.0)
        # sarcasm: tertiary (0.3) in communication (0.8) = 0.24
        assert engine.effective_trait_weight(profile, "sarcasm") == pytest.approx(0.24)


# ----- flagged combinations ----------------------------------------------
class TestFlaggedCombinations:
    def test_contradictory_certainty_fires(self, engine: TraitEngine) -> None:
        profile = engine.build_profile(
            "network_watcher",
            overrides={"hedging": 85, "confidence": 85},
        )
        hits = engine.scan_flagged(profile)
        assert any(fc.name == "contradictory_certainty" for fc in hits)

    def test_no_flags_at_defaults(self, engine: TraitEngine) -> None:
        # Default values shouldn't trigger all three flags simultaneously.
        profile = engine.build_profile("network_watcher")
        flags = {fc.name for fc in engine.scan_flagged(profile)}
        assert "contradictory_certainty" not in flags


# ----- helper ------------------------------------------------------------
class TestCompare:
    @pytest.mark.parametrize(
        "value,op,thresh,expected",
        [
            (80, ">=", 80, True),
            (79, ">=", 80, False),
            (20, "<=", 20, True),
            (21, "<=", 20, False),
            (50, ">", 50, False),
            (51, ">", 50, True),
            (5, "==", 5, True),
            (6, "==", 5, False),
        ],
    )
    def test_comparisons(self, value: int, op: str, thresh: int, expected: bool) -> None:
        assert _compare(value, op, thresh) is expected
