"""Unit tests for the grading engine.

Design reference: docs/decisions/ADR-0003-grading-engine.md
"""
from __future__ import annotations

from pathlib import Path

import pytest

from forest_soul_forge.core.grading import (
    CANONICAL_DOMAIN_ORDER,
    DomainGrade,
    GRADING_SCHEMA_VERSION,
    GradeReport,
    TERTIARY_MIN_VALUE,
    grade,
)
from forest_soul_forge.core.trait_engine import TraitEngine
from forest_soul_forge.core.dna import dna_short

REPO_ROOT = Path(__file__).resolve().parents[2]
YAML_PATH = REPO_ROOT / "config" / "trait_tree.yaml"


@pytest.fixture(scope="module")
def engine() -> TraitEngine:
    return TraitEngine(YAML_PATH)


# ---------------------------------------------------------------------------
# Shape & basic invariants
# ---------------------------------------------------------------------------
class TestReportShape:
    def test_default_profile_produces_report(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        report = grade(profile, engine)
        assert isinstance(report, GradeReport)
        assert report.role == "network_watcher"
        assert report.schema_version == GRADING_SCHEMA_VERSION
        assert report.profile_dna == dna_short(profile)

    def test_every_canonical_domain_graded(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("log_analyst")
        report = grade(profile, engine)
        for d in CANONICAL_DOMAIN_ORDER:
            assert d in report.per_domain
            assert isinstance(report.per_domain[d], DomainGrade)

    def test_scores_within_expected_ranges(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("anomaly_investigator")
        report = grade(profile, engine)
        assert 0.0 <= report.overall_score <= 100.0
        for g in report.per_domain.values():
            assert 0.0 <= g.intrinsic_score <= 100.0
            assert g.role_weight > 0.0
            # weighted = intrinsic * role_weight; role weights are in [0.4, 3.0]
            # so weighted ∈ [0, 300].
            assert g.weighted_score == pytest.approx(
                g.intrinsic_score * g.role_weight
            )
            for sd_score in g.subdomain_scores.values():
                assert 0.0 <= sd_score <= 100.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_same_inputs_same_output(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("incident_communicator")
        a = grade(profile, engine)
        b = grade(profile, engine)
        assert a == b

    def test_different_profile_different_dna(self, engine: TraitEngine) -> None:
        p1 = engine.build_profile("network_watcher")
        p2 = engine.build_profile("network_watcher", overrides={"caution": 99})
        r1 = grade(p1, engine)
        r2 = grade(p2, engine)
        assert r1.profile_dna != r2.profile_dna


# ---------------------------------------------------------------------------
# Math at boundaries
# ---------------------------------------------------------------------------
class TestMathBoundaries:
    def test_all_traits_max_gives_100s(self, engine: TraitEngine) -> None:
        overrides = {t.name: 100 for t in engine.list_traits()}
        profile = engine.build_profile("network_watcher", overrides=overrides)
        report = grade(profile, engine)
        # Every subdomain, every intrinsic, and the overall all collapse to 100
        # since value is constant. Role weights don't matter once everything
        # in the numerator is 100.
        assert report.overall_score == pytest.approx(100.0)
        for g in report.per_domain.values():
            assert g.intrinsic_score == pytest.approx(100.0)
            for sd in g.subdomain_scores.values():
                assert sd == pytest.approx(100.0)

    def test_all_traits_zero_gives_zeros(self, engine: TraitEngine) -> None:
        overrides = {t.name: 0 for t in engine.list_traits()}
        profile = engine.build_profile("network_watcher", overrides=overrides)
        report = grade(profile, engine)
        assert report.overall_score == pytest.approx(0.0)
        for g in report.per_domain.values():
            assert g.intrinsic_score == pytest.approx(0.0)

    def test_uniform_fifty_gives_fifty_overall(self, engine: TraitEngine) -> None:
        # If every trait is 50, every tier-weighted subdomain average is 50,
        # every intrinsic is 50, and the role-weighted mean is 50 regardless
        # of the role weights. This isolates the normalization step.
        overrides = {t.name: 50 for t in engine.list_traits()}
        profile = engine.build_profile("operator_companion", overrides=overrides)
        report = grade(profile, engine)
        assert report.overall_score == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Subdomain math details
# ---------------------------------------------------------------------------
class TestSubdomainMath:
    def test_tier_weighted_average_is_correct(self, engine: TraitEngine) -> None:
        """Hand-verify one subdomain against the tier-weighted formula.

        Pick a subdomain, give each trait a distinct value, compute by hand,
        then compare to the grader's output.
        """
        # Use security.defensive_posture — pick the first subdomain of security.
        sd = next(iter(engine.domains["security"].subdomains.values()))
        overrides: dict[str, int] = {}
        # Give values that exercise the formula: 80, 60, 40, etc.
        values_to_assign = [80, 60, 40, 20, 95, 10]
        for i, trait in enumerate(sd.traits.values()):
            overrides[trait.name] = values_to_assign[i % len(values_to_assign)]

        profile = engine.build_profile("network_watcher", overrides=overrides)
        report = grade(profile, engine)

        # Recompute by hand.
        numerator = 0.0
        denominator = 0.0
        for trait in sd.traits.values():
            tw = trait.tier_weight
            v = overrides[trait.name]
            numerator += tw * v
            denominator += tw
        expected = numerator / denominator

        assert report.per_domain["security"].subdomain_scores[sd.name] == pytest.approx(expected)

    def test_primary_outweighs_tertiary(self, engine: TraitEngine) -> None:
        """A primary trait at 100 should pull a subdomain higher than an
        equivalent secondary or tertiary trait would. This makes sure tier
        weights actually gate contribution strength.
        """
        # Find a subdomain that has at least one primary and one tertiary.
        target_sd = None
        for dom in engine.domains.values():
            for sd in dom.subdomains.values():
                tiers = {t.tier for t in sd.traits.values()}
                if "primary" in tiers and "tertiary" in tiers:
                    target_sd = sd
                    break
            if target_sd:
                break
        assert target_sd is not None, "test requires a mixed-tier subdomain"

        # Case A: primary high, tertiary low.
        a_overrides: dict[str, int] = {}
        for t in target_sd.traits.values():
            a_overrides[t.name] = 100 if t.tier == "primary" else 0
        pa = engine.build_profile("network_watcher", overrides=a_overrides)
        ra = grade(pa, engine)

        # Case B: primary low, tertiary high.
        b_overrides: dict[str, int] = {}
        for t in target_sd.traits.values():
            b_overrides[t.name] = 100 if t.tier == "tertiary" else 0
        pb = engine.build_profile("network_watcher", overrides=b_overrides)
        rb = grade(pb, engine)

        dom_name = target_sd.domain
        sd_name = target_sd.name
        a_score = ra.per_domain[dom_name].subdomain_scores[sd_name]
        b_score = rb.per_domain[dom_name].subdomain_scores[sd_name]
        assert a_score > b_score


# ---------------------------------------------------------------------------
# Role weighting
# ---------------------------------------------------------------------------
class TestRoleWeighting:
    def test_role_weight_applied_to_weighted_score(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        report = grade(profile, engine)
        for d, g in report.per_domain.items():
            expected_rw = engine.effective_domain_weight(profile, d)
            assert g.role_weight == pytest.approx(expected_rw)

    def test_domain_weight_override_used(self, engine: TraitEngine) -> None:
        # Override security to a specific value; weighted_score should follow.
        overrides = {"security": 2.5}
        profile = engine.build_profile(
            "network_watcher", domain_weight_overrides=overrides
        )
        report = grade(profile, engine)
        assert report.per_domain["security"].role_weight == pytest.approx(2.5)

    def test_overall_is_role_weighted_mean(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("anomaly_investigator")
        report = grade(profile, engine)
        num = sum(g.intrinsic_score * g.role_weight for g in report.per_domain.values())
        den = sum(g.role_weight for g in report.per_domain.values())
        assert report.overall_score == pytest.approx(num / den)


# ---------------------------------------------------------------------------
# Dominant domain selection
# ---------------------------------------------------------------------------
class TestDominantDomain:
    def test_picks_highest_weighted_score(self, engine: TraitEngine) -> None:
        # Crank emotional via trait values + a domain weight override.
        overrides = {t.name: 100 for t in engine.list_traits(domain="emotional")}
        # Zero everything else, so only emotional has non-zero weighted_score.
        for t in engine.list_traits():
            if t.domain != "emotional":
                overrides[t.name] = 0
        profile = engine.build_profile("network_watcher", overrides=overrides)
        report = grade(profile, engine)
        assert report.dominant_domain == "emotional"

    def test_ties_broken_by_canonical_order(self, engine: TraitEngine) -> None:
        # If every domain is uniformly 50 and role weights are equal, all
        # weighted_scores match and the canonical winner is 'security' (first
        # in CANONICAL_DOMAIN_ORDER).
        overrides_traits = {t.name: 50 for t in engine.list_traits()}
        # Force every domain weight to 1.0 to create a perfect tie.
        overrides_dw = {d: 1.0 for d in engine.domains}
        profile = engine.build_profile(
            "network_watcher",
            overrides=overrides_traits,
            domain_weight_overrides=overrides_dw,
        )
        report = grade(profile, engine)
        # Sanity: everyone should tie.
        scores = {d: g.weighted_score for d, g in report.per_domain.items()}
        assert len(set(round(s, 6) for s in scores.values())) == 1
        assert report.dominant_domain == CANONICAL_DOMAIN_ORDER[0]


# ---------------------------------------------------------------------------
# Trait inclusion / skipped counts
# ---------------------------------------------------------------------------
class TestInclusion:
    def test_low_tertiary_still_counts(self, engine: TraitEngine) -> None:
        """A tertiary trait below TERTIARY_MIN_VALUE must affect the score.

        Pick the first tertiary trait in the tree, grade with it at 0, then at
        99, and confirm the domain's intrinsic score changed.
        """
        tertiary = next(
            (t for t in engine.list_traits() if t.tier == "tertiary"), None
        )
        assert tertiary is not None, "trait tree must have at least one tertiary"

        base = engine.build_profile("network_watcher")
        low_overrides = dict(base.trait_values)
        low_overrides[tertiary.name] = 0
        low_profile = engine.build_profile("network_watcher", overrides=low_overrides)

        high_overrides = dict(base.trait_values)
        high_overrides[tertiary.name] = 99
        high_profile = engine.build_profile("network_watcher", overrides=high_overrides)

        low_report = grade(low_profile, engine)
        high_report = grade(high_profile, engine)

        low_intrinsic = low_report.per_domain[tertiary.domain].intrinsic_score
        high_intrinsic = high_report.per_domain[tertiary.domain].intrinsic_score
        assert high_intrinsic > low_intrinsic

    def test_skipped_traits_counts_low_tertiary(self, engine: TraitEngine) -> None:
        # Set every tertiary trait to TERTIARY_MIN_VALUE - 1. Every tertiary
        # should count as skipped.
        overrides = {}
        expected_skipped_per_domain: dict[str, int] = {}
        for t in engine.list_traits():
            if t.tier == "tertiary":
                overrides[t.name] = TERTIARY_MIN_VALUE - 1
                expected_skipped_per_domain[t.domain] = (
                    expected_skipped_per_domain.get(t.domain, 0) + 1
                )
        profile = engine.build_profile("network_watcher", overrides=overrides)
        report = grade(profile, engine)
        for d, expected_count in expected_skipped_per_domain.items():
            assert report.per_domain[d].skipped_traits == expected_count

    def test_skipped_zero_when_all_high(self, engine: TraitEngine) -> None:
        overrides = {t.name: 95 for t in engine.list_traits()}
        profile = engine.build_profile("network_watcher", overrides=overrides)
        report = grade(profile, engine)
        for g in report.per_domain.values():
            assert g.skipped_traits == 0

    def test_included_traits_matches_domain_trait_count(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        report = grade(profile, engine)
        for domain_name, domain in engine.domains.items():
            expected = sum(len(sd.traits) for sd in domain.subdomains.values())
            assert report.per_domain[domain_name].included_traits == expected


# ---------------------------------------------------------------------------
# Flagged combinations
# ---------------------------------------------------------------------------
class TestWarnings:
    def test_no_warnings_on_defaults(self, engine: TraitEngine) -> None:
        # Default profiles shouldn't hit any flagged combinations — if they
        # do, that's a trait-tree bug worth catching here.
        for role in engine.roles:
            profile = engine.build_profile(role)
            report = grade(profile, engine)
            assert report.warnings == (), (
                f"Role '{role}' triggered warnings on defaults: {report.warnings}"
            )

    def test_flagged_combination_surfaces(self, engine: TraitEngine) -> None:
        # Only assert behavior if the trait tree defines at least one flagged
        # combination. Skip otherwise — the mechanism is covered once flagged
        # combinations exist.
        if not engine.flagged_combinations:
            pytest.skip("No flagged combinations configured")
        fc = engine.flagged_combinations[0]
        # Construct a profile that satisfies every condition in fc.
        overrides: dict[str, int] = {}
        for trait_name, (op, thresh) in fc.conditions.items():
            overrides[trait_name] = _value_satisfying(op, thresh)
        profile = engine.build_profile("network_watcher", overrides=overrides)
        report = grade(profile, engine)
        assert fc.name in report.warnings


def _value_satisfying(op: str, thresh: int) -> int:
    """Pick a trait value that satisfies `op thresh` and stays in [0, 100]."""
    if op == ">=":
        return min(100, thresh)
    if op == ">":
        return min(100, thresh + 1)
    if op == "<=":
        return max(0, thresh)
    if op == "<":
        return max(0, thresh - 1)
    if op == "==":
        return thresh
    raise ValueError(op)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
class TestRender:
    def test_render_contains_role_and_dna(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("network_watcher")
        report = grade(profile, engine)
        rendered = report.render()
        assert report.role in rendered
        assert report.profile_dna in rendered
        assert "Overall" in rendered
        assert "dominant" in rendered

    def test_render_lists_every_canonical_domain(self, engine: TraitEngine) -> None:
        profile = engine.build_profile("log_analyst")
        report = grade(profile, engine)
        rendered = report.render()
        for d in CANONICAL_DOMAIN_ORDER:
            assert d in rendered
