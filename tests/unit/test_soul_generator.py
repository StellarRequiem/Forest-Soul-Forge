"""Unit tests for soul_generator."""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.core.trait_engine import TraitEngine
from forest_soul_forge.core.dna import Lineage, dna_full, dna_short, verify
from forest_soul_forge.soul.generator import (
    BANDS,
    FRONTMATTER_SCHEMA_VERSION,
    SoulGenerator,
    band_for,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "config" / "trait_tree.yaml"


@pytest.fixture(scope="module")
def engine() -> TraitEngine:
    return TraitEngine(YAML_PATH)


@pytest.fixture(scope="module")
def generator(engine: TraitEngine) -> SoulGenerator:
    return SoulGenerator(engine)


# ----- bands --------------------------------------------------------------
class TestBands:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, "very low"),
            (19, "very low"),
            (20, "low"),
            (39, "low"),
            (40, "moderate"),
            (59, "moderate"),
            (60, "fairly high"),
            (79, "fairly high"),
            (80, "very high"),
            (100, "very high"),
        ],
    )
    def test_band_boundaries(self, value: int, expected: str) -> None:
        assert band_for(value) == expected

    def test_all_bands_covered(self) -> None:
        for v in range(101):
            assert band_for(v) in {"very low", "low", "moderate", "fairly high", "very high"}


# ----- generation (prose body) -------------------------------------------
class TestGeneration:
    def test_generates_non_empty_markdown(self, engine: TraitEngine, generator: SoulGenerator) -> None:
        profile = engine.build_profile("network_watcher")
        doc = generator.generate(profile, agent_name="NetworkWatcher")
        assert "# Soul Definition" in doc.markdown
        assert "network_watcher" in doc.markdown
        assert "## Core rules" in doc.markdown

    def test_highest_weight_domain_appears_first(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher")
        doc = generator.generate(profile, agent_name="NW")
        md = doc.markdown
        assert md.index("## Security") < md.index("## Emotional")

    def test_operator_companion_reorders_domains(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("operator_companion")
        doc = generator.generate(profile, agent_name="Buddy")
        md = doc.markdown
        assert md.index("## Emotional") < md.index("## Security")

    def test_low_tertiary_traits_skipped_in_prose(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        # sarcasm default=20 is below TERTIARY_MIN_VALUE=40, so it shouldn't appear
        # in the prose body. It WILL appear in frontmatter trait_values.
        profile = engine.build_profile("network_watcher")
        doc = generator.generate(profile, agent_name="NW")
        body = doc.markdown.split("---", 2)[2]
        assert "**sarcasm**" not in body

    def test_high_tertiary_traits_included_in_prose(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher", overrides={"sarcasm": 85})
        doc = generator.generate(profile, agent_name="NW")
        body = doc.markdown.split("---", 2)[2]
        assert "**sarcasm** — 85/100" in body

    def test_flagged_combinations_surface_in_output(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile(
            "network_watcher",
            overrides={"hedging": 85, "confidence": 85},
        )
        doc = generator.generate(profile, agent_name="NW")
        assert "Profile warnings" in doc.markdown
        assert "contradictory_certainty" in doc.markdown

    def test_no_warnings_section_when_clean(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher")
        doc = generator.generate(profile, agent_name="NW")
        assert "Profile warnings" not in doc.markdown

    def test_trait_value_rendered_in_prose(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher", overrides={"caution": 77})
        doc = generator.generate(profile, agent_name="NW")
        assert "**caution** — 77/100" in doc.markdown

    def test_moderate_band_uses_scale_mid(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher", overrides={"caution": 50})
        doc = generator.generate(profile, agent_name="NW")
        mid_text = engine.get_trait("caution").scale_mid
        assert mid_text, "scale_mid should be populated for caution"
        assert mid_text.rstrip(".") in doc.markdown

    def test_no_awkward_concat_in_moderate(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher", overrides={"caution": 50})
        doc = generator.generate(profile, agent_name="NW")
        low = engine.get_trait("caution").scale_low.rstrip(".")
        high = engine.get_trait("caution").scale_high.rstrip(".")
        assert f"{low} / {high}" not in doc.markdown


# ----- frontmatter -------------------------------------------------------
class TestFrontmatter:
    def test_starts_with_frontmatter_fence(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher")
        doc = generator.generate(profile, agent_name="NW")
        assert doc.markdown.startswith("---\n")

    def test_frontmatter_contains_dna_and_role(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher")
        doc = generator.generate(profile, agent_name="NW")
        frontmatter = doc.markdown.split("---", 2)[1]
        assert f"dna: {doc.dna}" in frontmatter
        assert f'dna_full: "{doc.dna_full}"' in frontmatter
        assert "role: network_watcher" in frontmatter
        assert f"schema_version: {FRONTMATTER_SCHEMA_VERSION}" in frontmatter

    def test_frontmatter_contains_all_trait_values(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher", overrides={"caution": 77})
        doc = generator.generate(profile, agent_name="NW")
        frontmatter = doc.markdown.split("---", 2)[1]
        assert "caution: 77" in frontmatter
        for trait_name in profile.trait_values:
            assert f"{trait_name}:" in frontmatter

    def test_root_agent_has_null_lineage(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("network_watcher")
        doc = generator.generate(profile, agent_name="NW")
        frontmatter = doc.markdown.split("---", 2)[1]
        assert "parent_dna: null" in frontmatter
        assert "spawned_by: null" in frontmatter
        assert "lineage: []" in frontmatter
        assert "lineage_depth: 0" in frontmatter


# ----- DNA ---------------------------------------------------------------
class TestDNA:
    def test_dna_is_deterministic(self, engine: TraitEngine) -> None:
        a = engine.build_profile("network_watcher")
        b = engine.build_profile("network_watcher")
        assert dna_full(a) == dna_full(b)
        assert dna_short(a) == dna_short(b)

    def test_dna_changes_when_any_trait_changes(self, engine: TraitEngine) -> None:
        a = engine.build_profile("network_watcher")
        b = engine.build_profile(
            "network_watcher", overrides={"caution": a.trait_values["caution"] + 1}
        )
        assert dna_full(a) != dna_full(b)

    def test_dna_changes_when_role_changes(self, engine: TraitEngine) -> None:
        # Even at default values, role is part of identity.
        a = engine.build_profile("network_watcher")
        b = engine.build_profile("log_analyst")
        assert dna_full(a) != dna_full(b)

    def test_dna_changes_with_domain_weight_override(self, engine: TraitEngine) -> None:
        a = engine.build_profile("network_watcher")
        b = engine.build_profile(
            "network_watcher", domain_weight_overrides={"security": 2.5}
        )
        assert dna_full(a) != dna_full(b)

    def test_dna_short_form_is_prefix_of_full(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        assert dna_full(profile).startswith(dna_short(profile))
        assert len(dna_short(profile)) == 12
        assert len(dna_full(profile)) == 64

    def test_verify_accepts_both_forms(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        assert verify(profile, dna_full(profile))
        assert verify(profile, dna_short(profile))
        assert not verify(profile, "0" * 12)

    def test_dna_matches_generated_doc(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        profile = engine.build_profile("anomaly_investigator")
        doc = generator.generate(profile, agent_name="Hunter")
        assert doc.dna == dna_short(profile)
        assert doc.dna_full == dna_full(profile)


# ----- Lineage -----------------------------------------------------------
class TestLineage:
    def test_root_lineage(self) -> None:
        lineage = Lineage.root()
        assert lineage.is_root()
        assert lineage.depth == 0
        assert lineage.ancestors == ()
        assert lineage.parent_dna is None
        assert lineage.spawned_by is None

    def test_from_parent_builds_chain(self) -> None:
        root = Lineage.root()
        child = Lineage.from_parent(
            parent_dna="aaaaaaaaaaaa",
            parent_lineage=root,
            parent_agent_name="Parent",
        )
        assert child.parent_dna == "aaaaaaaaaaaa"
        assert child.spawned_by == "Parent"
        assert child.ancestors == ("aaaaaaaaaaaa",)
        assert child.depth == 1
        assert not child.is_root()

    def test_grandchild_chain_preserves_order(self) -> None:
        root = Lineage.root()
        child = Lineage.from_parent("aaaa", root, "Parent")
        grandchild = Lineage.from_parent("bbbb", child, "Child")
        # root-first: ancestors[0] is the root ancestor.
        assert grandchild.ancestors == ("aaaa", "bbbb")
        assert grandchild.depth == 2
        assert grandchild.parent_dna == "bbbb"

    def test_spawned_agent_has_lineage_footer(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        parent_profile = engine.build_profile("anomaly_investigator")
        parent_doc = generator.generate(parent_profile, agent_name="HuntMaster")

        child_profile = engine.build_profile(
            "anomaly_investigator", overrides={"curiosity": 85}
        )
        child_lineage = Lineage.from_parent(
            parent_dna=parent_doc.dna,
            parent_lineage=parent_doc.lineage,
            parent_agent_name="HuntMaster",
        )
        child_doc = generator.generate(
            child_profile, agent_name="Scout", lineage=child_lineage
        )

        assert "## Lineage" in child_doc.markdown
        assert parent_doc.dna in child_doc.markdown
        # Header line uses markdown bold: `**Spawned by:** HuntMaster (...)`.
        assert "**Spawned by:**" in child_doc.markdown
        assert "HuntMaster" in child_doc.markdown

        frontmatter = child_doc.markdown.split("---", 2)[1]
        assert f"parent_dna: {parent_doc.dna}" in frontmatter
        assert 'spawned_by: "HuntMaster"' in frontmatter
        assert "lineage_depth: 1" in frontmatter

    def test_lineage_not_in_dna(
        self, engine: TraitEngine, generator: SoulGenerator
    ) -> None:
        """Lineage is metadata — must not affect DNA, or descendants can't stabilize."""
        parent_profile = engine.build_profile("anomaly_investigator")
        root_doc = generator.generate(parent_profile, agent_name="X")
        spawn_lineage = Lineage.from_parent(
            parent_dna="deadbeefcafe",
            parent_lineage=Lineage.root(),
            parent_agent_name="Ghost",
        )
        spawned_doc = generator.generate(
            parent_profile, agent_name="X", lineage=spawn_lineage
        )
        # Same profile → same DNA, regardless of lineage or agent_name.
        assert root_doc.dna == spawned_doc.dna
