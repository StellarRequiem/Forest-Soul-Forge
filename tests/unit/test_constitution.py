"""Unit tests for the constitution builder.

Design reference: docs/decisions/ADR-0004-constitution-builder.md
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.core.constitution import (
    CONSTITUTION_SCHEMA_VERSION,
    Constitution,
    ConstitutionError,
    DriftMonitoring,
    Policy,
    RiskThresholds,
    STRICTNESS_ORDER,
    TemplateSchemaError,
    build,
)
from forest_soul_forge.core.dna import dna_full, dna_short
from forest_soul_forge.core.trait_engine import TraitEngine, UnknownRoleError

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "config" / "trait_tree.yaml"
TEMPLATES_PATH = REPO_ROOT / "config" / "constitution_templates.yaml"


@pytest.fixture(scope="module")
def engine() -> TraitEngine:
    return TraitEngine(YAML_PATH)


# ---------------------------------------------------------------------------
# Shape & defaults
# ---------------------------------------------------------------------------
class TestShape:
    def test_default_profile_builds(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        c = build(profile, engine, agent_name="Watcher", templates_path=TEMPLATES_PATH)
        assert isinstance(c, Constitution)
        assert c.schema_version == CONSTITUTION_SCHEMA_VERSION
        assert c.role == "network_watcher"
        assert c.agent_name == "Watcher"
        assert c.agent_dna == dna_short(profile)
        assert c.agent_dna_full == dna_full(profile)

    def test_policies_sorted_by_id(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        ids = [p.id for p in c.policies]
        assert ids == sorted(ids)

    def test_triggers_sorted(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        for p in c.policies:
            assert list(p.triggers) == sorted(p.triggers)

    def test_out_of_scope_sorted_and_unique(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        assert list(c.out_of_scope) == sorted(set(c.out_of_scope))

    def test_all_five_roles_have_templates(self, engine: TraitEngine) -> None:
        for role in engine.roles:
            profile = engine.build_profile(role)
            c = build(profile, engine, agent_name=f"{role}-test", templates_path=TEMPLATES_PATH)
            assert c.role == role
            assert c.policies  # at least one baseline policy per role
            assert c.risk_thresholds.auto_halt_risk > 0.0
            assert c.drift_monitoring.max_profile_deviation == 0


# ---------------------------------------------------------------------------
# Determinism & hash behavior
# ---------------------------------------------------------------------------
class TestHashAndDeterminism:
    def test_hash_is_stable_across_runs(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("log_analyst")
        c1 = build(profile, engine, agent_name="X", templates_path=TEMPLATES_PATH)
        c2 = build(profile, engine, agent_name="X", templates_path=TEMPLATES_PATH)
        assert c1.constitution_hash == c2.constitution_hash

    def test_hash_excludes_agent_name(self, engine: TraitEngine) -> None:
        """Two agents with different display names but identical rulebooks
        should have the same constitution_hash — identity is bound in soul.md,
        not here.
        """
        profile = engine.build_profile("log_analyst")
        a = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        b = build(profile, engine, agent_name="B", templates_path=TEMPLATES_PATH)
        assert a.constitution_hash == b.constitution_hash
        assert a.agent_name != b.agent_name

    def test_hash_changes_when_rulebook_changes(self, engine: TraitEngine) -> None:
        """Bumping caution above 80 activates caution_high_approval, which
        changes the policy list and therefore the hash.
        """
        base_profile = engine.build_profile("network_watcher")
        high_profile = engine.build_profile("network_watcher", overrides={"caution": 85})

        base = build(base_profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        high = build(high_profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        # If the default network_watcher already hits caution>=80, pick a
        # profile that definitely changes policy count to keep the test
        # meaningful.
        if base.constitution_hash == high.constitution_hash:
            low_profile = engine.build_profile("network_watcher", overrides={"caution": 10})
            low = build(low_profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
            assert low.constitution_hash != high.constitution_hash
        else:
            assert base.constitution_hash != high.constitution_hash

    def test_hash_is_64_hex_chars(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        h = c.constitution_hash
        assert len(h) == 64
        assert all(ch in "0123456789abcdef" for ch in h)


# ---------------------------------------------------------------------------
# Trait modifiers
# ---------------------------------------------------------------------------
class TestTraitModifiers:
    def test_high_caution_adds_approval_policy(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher", overrides={"caution": 95})
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        ids = {p.id for p in c.policies}
        assert "caution_high_approval" in ids

    def test_below_threshold_does_not_add_policy(self, engine: TraitEngine) -> None:
        # Caution at 10 is below the >=80 trigger; make sure that modifier is
        # silent.
        profile = engine.build_profile("network_watcher", overrides={"caution": 10})
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        ids = {p.id for p in c.policies}
        assert "caution_high_approval" not in ids

    def test_low_hedging_triggers_reviewer_flag(self, engine: TraitEngine) -> None:
        profile = engine.build_profile(
            "incident_communicator", overrides={"hedging": 5}
        )
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        ids = {p.id for p in c.policies}
        assert "low_hedging_reviewer_flag" in ids

    def test_trait_source_tag_recorded(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher", overrides={"caution": 95})
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        trigger_policy = next(p for p in c.policies if p.id == "caution_high_approval")
        assert trigger_policy.source.startswith("trait:caution:>=80")


# ---------------------------------------------------------------------------
# Flagged-combination mapping
# ---------------------------------------------------------------------------
class TestFlaggedMapping:
    def test_flagged_combo_becomes_forbid_policy(self, engine: TraitEngine) -> None:
        if not engine.flagged_combinations:
            pytest.skip("No flagged combinations configured")
        fc = engine.flagged_combinations[0]
        overrides = {}
        for name, (op, thresh) in fc.conditions.items():
            if op == ">=": v = min(100, thresh)
            elif op == ">": v = min(100, thresh + 1)
            elif op == "<=": v = max(0, thresh)
            elif op == "<": v = max(0, thresh - 1)
            else: v = thresh
            overrides[name] = v
        profile = engine.build_profile("network_watcher", overrides=overrides)
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        matching = [p for p in c.policies if p.source == f"flagged:{fc.name}"]
        assert len(matching) == 1
        assert matching[0].rule == "forbid"
        # Rationale carries the combo warning text.
        assert matching[0].rationale == fc.warning


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------
class TestConflictResolution:
    def test_stricter_rule_supersedes_weaker(self, engine: TraitEngine) -> None:
        """``network_watcher`` has ``approval_for_host_modifying_action``
        (require_human_approval) on ``modify_host``. Triggering a flagged
        combo emits a ``forbid`` on ``any_state_change`` — different trigger,
        no conflict. To synthesize a conflict we need a modifier and a role
        policy on the *same* trigger. Use caution-high which adds approval
        on ``any_state_change``. Because a flagged combo also adds ``forbid``
        on ``any_state_change``, the approval policy should be superseded.
        """
        if not engine.flagged_combinations:
            pytest.skip("No flagged combinations configured")
        fc = engine.flagged_combinations[0]
        overrides = {"caution": 95}
        for name, (op, thresh) in fc.conditions.items():
            if op == ">=": v = min(100, thresh)
            elif op == ">": v = min(100, thresh + 1)
            elif op == "<=": v = max(0, thresh)
            elif op == "<": v = max(0, thresh - 1)
            else: v = thresh
            overrides[name] = v
        profile = engine.build_profile("network_watcher", overrides=overrides)
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)

        caution_pol = next(p for p in c.policies if p.id == "caution_high_approval")
        forbid_policies = [p for p in c.policies if p.rule == "forbid" and "any_state_change" in p.triggers]
        assert forbid_policies, "expected at least one forbid on any_state_change"
        assert caution_pol.superseded_by is not None
        assert caution_pol.superseded_by == forbid_policies[0].id

    def test_non_ordered_rule_never_supersedes(self, engine: TraitEngine) -> None:
        """A ``require_explicit_uncertainty`` rule shouldn't displace an
        allow/approval/forbid, and shouldn't be marked as superseded itself
        by a rule on a different trigger.
        """
        profile = engine.build_profile("incident_communicator")
        c = build(profile, engine, agent_name="A", templates_path=TEMPLATES_PATH)
        modifiers = [p for p in c.policies if p.rule not in STRICTNESS_ORDER]
        for m in modifiers:
            assert m.superseded_by is None


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------
class TestErrors:
    def test_missing_templates_file_raises(self, engine: TraitEngine, tmp_path: Path) -> None:
        profile = engine.build_profile("network_watcher")
        with pytest.raises(TemplateSchemaError):
            build(profile, engine, agent_name="A", templates_path=tmp_path / "nope.yaml")

    def test_unknown_role_raises(self, engine: TraitEngine, tmp_path: Path) -> None:
        # Minimal valid-shape template missing the requested role.
        fake = tmp_path / "templates.yaml"
        fake.write_text(
            "schema_version: 1\n"
            "role_base:\n"
            "  some_other_role:\n"
            "    policies: []\n"
            "    risk_thresholds:\n"
            "      auto_halt_risk: 0.5\n"
            "      escalate_risk: 0.3\n"
            "      min_confidence_to_act: 0.5\n"
            "    drift_monitoring:\n"
            "      profile_hash_check: per_turn\n"
            "      max_profile_deviation: 0\n"
            "      on_drift: halt\n",
            encoding="utf-8",
        )
        profile = engine.build_profile("network_watcher")
        with pytest.raises(UnknownRoleError):
            build(profile, engine, agent_name="A", templates_path=fake)

    def test_bad_schema_version_raises(self, engine: TraitEngine, tmp_path: Path) -> None:
        fake = tmp_path / "templates.yaml"
        fake.write_text("schema_version: 999\nrole_base: {}\n", encoding="utf-8")
        profile = engine.build_profile("network_watcher")
        with pytest.raises(TemplateSchemaError, match="schema_version mismatch"):
            build(profile, engine, agent_name="A", templates_path=fake)


# ---------------------------------------------------------------------------
# YAML rendering
# ---------------------------------------------------------------------------
class TestRender:
    def test_yaml_contains_all_sections(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        c = build(profile, engine, agent_name="Watcher", templates_path=TEMPLATES_PATH)
        text = c.to_yaml()
        for key in (
            "schema_version",
            "constitution_hash",
            "agent",
            "policies",
            "risk_thresholds",
            "out_of_scope",
            "operator_duties",
            "drift_monitoring",
        ):
            assert key in text

    def test_yaml_omits_generated_at_by_default(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        c = build(profile, engine, agent_name="Watcher", templates_path=TEMPLATES_PATH)
        text = c.to_yaml()
        assert "generated_at" not in text

    def test_yaml_deterministic_without_timestamp(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        c1 = build(profile, engine, agent_name="Watcher", templates_path=TEMPLATES_PATH)
        c2 = build(profile, engine, agent_name="Watcher", templates_path=TEMPLATES_PATH)
        assert c1.to_yaml() == c2.to_yaml()

    def test_yaml_parseable_round_trip(self, engine: TraitEngine) -> None:
        import yaml

        profile = engine.build_profile("anomaly_investigator")
        c = build(profile, engine, agent_name="Zed", templates_path=TEMPLATES_PATH)
        text = c.to_yaml()
        parsed = yaml.safe_load(text)
        assert parsed["schema_version"] == 1
        assert parsed["constitution_hash"] == c.constitution_hash
        assert parsed["agent"]["role"] == "anomaly_investigator"
        assert len(parsed["policies"]) == len(c.policies)


# ---------------------------------------------------------------------------
# Soul frontmatter binding
# ---------------------------------------------------------------------------
class TestSoulBinding:
    def test_soul_emits_constitution_fields_when_provided(self, engine: TraitEngine) -> None:
        from forest_soul_forge.soul.generator import SoulGenerator

        profile = engine.build_profile("network_watcher")
        c = build(profile, engine, agent_name="Watcher", templates_path=TEMPLATES_PATH)
        gen = SoulGenerator(engine)
        soul = gen.generate(
            profile,
            agent_name="Watcher",
            constitution_hash=c.constitution_hash,
            constitution_file="watcher.constitution.yaml",
        )
        assert f'constitution_hash: "{c.constitution_hash}"' in soul.markdown
        assert 'constitution_file: "watcher.constitution.yaml"' in soul.markdown

    def test_soul_omits_constitution_fields_by_default(self, engine: TraitEngine) -> None:
        from forest_soul_forge.soul.generator import SoulGenerator

        profile = engine.build_profile("network_watcher")
        gen = SoulGenerator(engine)
        soul = gen.generate(profile, agent_name="Watcher")
        assert "constitution_hash:" not in soul.markdown
        assert "constitution_file:" not in soul.markdown

    def test_soul_requires_both_or_neither(self, engine: TraitEngine) -> None:
        from forest_soul_forge.soul.generator import SoulGenerator

        profile = engine.build_profile("network_watcher")
        gen = SoulGenerator(engine)
        with pytest.raises(ValueError):
            gen.generate(profile, agent_name="Watcher", constitution_hash="abcd")
        with pytest.raises(ValueError):
            gen.generate(profile, agent_name="Watcher", constitution_file="x.yaml")


# ============================================================================
# ADR-0021 T3 — genre in canonical body and YAML output.
# ============================================================================
class TestGenre:
    """Genre is part of the rulebook (hashed); description is not."""

    def test_no_genre_default(self, engine: TraitEngine) -> None:
        c = build(engine.build_profile("network_watcher"),
                  engine, agent_name="A", templates_path=TEMPLATES_PATH)
        assert c.genre is None
        assert c.genre_description is None

    def test_canonical_body_has_genre_field_with_empty_sentinel(
        self, engine: TraitEngine
    ) -> None:
        # Even when genre is None, canonical_body must include the field
        # (with "") so the hash shape is stable across legacy and modern
        # constitutions. This is the explicit ADR-0021 T3 contract.
        c = build(engine.build_profile("network_watcher"),
                  engine, agent_name="A", templates_path=TEMPLATES_PATH)
        body = c.canonical_body()
        assert "genre" in body
        assert body["genre"] == ""

    def test_hash_changes_when_genre_changes(self, engine: TraitEngine) -> None:
        # Two agents with identical profile + tools but different genre
        # must hash differently. Genre is policy.
        profile = engine.build_profile("network_watcher")
        c_obs = build(profile, engine, agent_name="A",
                      templates_path=TEMPLATES_PATH, genre="observer")
        c_inv = build(profile, engine, agent_name="A",
                      templates_path=TEMPLATES_PATH, genre="investigator")
        c_none = build(profile, engine, agent_name="A",
                       templates_path=TEMPLATES_PATH)
        assert c_obs.constitution_hash != c_inv.constitution_hash
        assert c_obs.constitution_hash != c_none.constitution_hash
        assert c_inv.constitution_hash != c_none.constitution_hash

    def test_hash_excludes_genre_description(self, engine: TraitEngine) -> None:
        # Description is documentation, not policy. Two agents in the
        # same genre but with different description text (won't happen
        # in practice but enforced by the data model) must hash equally.
        profile = engine.build_profile("network_watcher")
        a = build(profile, engine, agent_name="A",
                  templates_path=TEMPLATES_PATH,
                  genre="observer", genre_description="version 1 prose")
        b = build(profile, engine, agent_name="A",
                  templates_path=TEMPLATES_PATH,
                  genre="observer", genre_description="version 2 prose, edited")
        assert a.constitution_hash == b.constitution_hash

    def test_to_yaml_emits_genre_block_when_present(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        c = build(profile, engine, agent_name="A",
                  templates_path=TEMPLATES_PATH,
                  genre="observer",
                  genre_description="Passive watching, read-only orientation.")
        y = c.to_yaml(generated_at="2026-04-26 00:00:00Z")
        assert "genre: observer" in y
        assert "Passive watching" in y

    def test_to_yaml_omits_genre_when_none(self, engine: TraitEngine) -> None:
        # Back-compat: pre-T3 constitutions had no genre line. Same shape
        # post-T3 when genre is None.
        c = build(engine.build_profile("network_watcher"),
                  engine, agent_name="A", templates_path=TEMPLATES_PATH)
        y = c.to_yaml(generated_at="2026-04-26 00:00:00Z")
        assert "genre:" not in y
        assert "genre_description:" not in y


# ============================================================================
# ADR-0021-amendment §2 — initiative_level + initiative_ceiling on
# constitution. Hashed; surface in YAML when non-default.
# ============================================================================
class TestInitiativeLadder:
    """Initiative posture is part of the rulebook (hashed). YAML emission
    is conditional: defaults (L5/L5) stay omitted for back-compat with
    pre-amendment artifacts; non-defaults always surface as a pair."""

    def test_default_l5_l5_when_no_genre(self, engine: TraitEngine) -> None:
        # No genre supplied → defaults to L5/L5 (pre-amendment behavior).
        c = build(engine.build_profile("network_watcher"),
                  engine, agent_name="A", templates_path=TEMPLATES_PATH)
        assert c.initiative_level == "L5"
        assert c.initiative_ceiling == "L5"

    def test_explicit_initiative_round_trips(
        self, engine: TraitEngine
    ) -> None:
        c = build(engine.build_profile("network_watcher"),
                  engine, agent_name="A", templates_path=TEMPLATES_PATH,
                  genre="companion",
                  initiative_level="L1", initiative_ceiling="L2")
        assert c.initiative_level == "L1"
        assert c.initiative_ceiling == "L2"

    def test_canonical_body_includes_both_fields(
        self, engine: TraitEngine
    ) -> None:
        # Hash shape is stable: both fields always present in body.
        c = build(engine.build_profile("network_watcher"),
                  engine, agent_name="A", templates_path=TEMPLATES_PATH,
                  initiative_level="L3", initiative_ceiling="L4")
        body = c.canonical_body()
        assert body["initiative_level"] == "L3"
        assert body["initiative_ceiling"] == "L4"

    def test_hash_changes_when_initiative_level_changes(
        self, engine: TraitEngine
    ) -> None:
        profile = engine.build_profile("network_watcher")
        c_l1 = build(profile, engine, agent_name="A",
                     templates_path=TEMPLATES_PATH,
                     initiative_level="L1", initiative_ceiling="L2")
        c_l2 = build(profile, engine, agent_name="A",
                     templates_path=TEMPLATES_PATH,
                     initiative_level="L2", initiative_ceiling="L2")
        assert c_l1.constitution_hash != c_l2.constitution_hash

    def test_hash_changes_when_ceiling_changes(
        self, engine: TraitEngine
    ) -> None:
        profile = engine.build_profile("network_watcher")
        c_l3 = build(profile, engine, agent_name="A",
                     templates_path=TEMPLATES_PATH,
                     initiative_level="L1", initiative_ceiling="L3")
        c_l4 = build(profile, engine, agent_name="A",
                     templates_path=TEMPLATES_PATH,
                     initiative_level="L1", initiative_ceiling="L4")
        assert c_l3.constitution_hash != c_l4.constitution_hash

    def test_to_yaml_emits_initiative_pair_when_non_default(
        self, engine: TraitEngine
    ) -> None:
        c = build(engine.build_profile("network_watcher"),
                  engine, agent_name="A", templates_path=TEMPLATES_PATH,
                  genre="companion",
                  initiative_level="L1", initiative_ceiling="L2")
        y = c.to_yaml(generated_at="2026-05-01 00:00:00Z")
        assert "initiative_level: L1" in y
        assert "initiative_ceiling: L2" in y

    def test_to_yaml_omits_when_l5_l5_back_compat(
        self, engine: TraitEngine
    ) -> None:
        # Default L5/L5 keeps pre-amendment YAML byte-identical for
        # callers that don't engage the new mechanism.
        c = build(engine.build_profile("network_watcher"),
                  engine, agent_name="A", templates_path=TEMPLATES_PATH)
        y = c.to_yaml(generated_at="2026-05-01 00:00:00Z")
        assert "initiative_level:" not in y
        assert "initiative_ceiling:" not in y

    def test_to_yaml_emits_pair_when_only_one_non_default(
        self, engine: TraitEngine
    ) -> None:
        # If level OR ceiling is non-default, both surface — operators
        # always see a complete posture, never a half-pair.
        c = build(engine.build_profile("network_watcher"),
                  engine, agent_name="A", templates_path=TEMPLATES_PATH,
                  initiative_level="L3", initiative_ceiling="L5")
        y = c.to_yaml(generated_at="2026-05-01 00:00:00Z")
        assert "initiative_level: L3" in y
        # Ceiling defaults to L5 here but still emitted because level
        # is non-default — pair-or-nothing rule.
        assert "initiative_ceiling: L5" in y

