"""Grading engine — deterministic config-grade summary of a TraitProfile.

Given a profile and the trait engine it was built against, produce a
:class:`GradeReport`: per-domain intrinsic and role-weighted scores, an overall
0..100 number, the dominant domain, and any flagged-combination warnings.

All math is pure and deterministic. Same profile + same engine YAML → same
report, always. No clocks, no randomness, no disk I/O beyond what the engine
already does.

This module intentionally scopes *config grading* only (how the agent is
configured). Output grading — scoring a runtime finding an agent emitted — is
a different problem with its own future ADR and is explicitly out of scope
here. See docs/decisions/ADR-0003-grading-engine.md.

Threshold note: tertiary traits with value < ``TERTIARY_MIN_VALUE`` are the
same set that :mod:`forest_soul_forge.soul.generator` skips from prose. They
still *count* in the grade — prose-skip is a readability choice, the grade
should reflect the configured profile. ``DomainGrade.skipped_traits`` just
tells the operator how many traits fell below that prose bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from forest_soul_forge.core.trait_engine import TraitEngine, TraitProfile
from forest_soul_forge.core.dna import dna_short

# Kept in sync with soul.generator.TERTIARY_MIN_VALUE. If these ever drift,
# grading and prose will disagree about which traits are "low tertiary", which
# is confusing for operators. Future cleanup: hoist to YAML.
TERTIARY_MIN_VALUE: int = 40

# Canonical tie-break order for dominant-domain selection. Matches the order
# domains are listed in config/trait_tree.yaml (ADR-0001). Any new domain must
# be appended here and to the YAML in the same order.
CANONICAL_DOMAIN_ORDER: tuple[str, ...] = (
    "security",
    "audit",
    "emotional",
    "cognitive",
    "communication",
)

GRADING_SCHEMA_VERSION: int = 1


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DomainGrade:
    """One domain's contribution to the overall grade.

    ``intrinsic_score`` is role-independent (0..100) — it's what this domain
    looks like if you ignore the role emphasis. ``weighted_score`` applies
    the role weight, so roles that de-emphasize a domain produce lower
    weighted scores for the same intrinsic configuration.
    """

    domain: str
    intrinsic_score: float
    role_weight: float
    weighted_score: float
    subdomain_scores: dict[str, float]
    included_traits: int
    skipped_traits: int


@dataclass(frozen=True)
class GradeReport:
    """Config-grade summary of a :class:`TraitProfile`.

    Not persisted by default. Callers serialize this themselves if they want
    it on disk alongside soul.md.
    """

    profile_dna: str
    role: str
    per_domain: dict[str, DomainGrade]
    overall_score: float
    dominant_domain: str
    warnings: tuple[str, ...]
    schema_version: int = GRADING_SCHEMA_VERSION

    # ---- rendering ------------------------------------------------------
    def render(self) -> str:
        """Return a human-readable multi-line summary.

        Intended for CLI output and demo scripts, not for machine parsing.
        Machines should read the dataclass fields directly.
        """
        lines: list[str] = []
        lines.append(f"Grade report — {self.role} ({self.profile_dna})")
        lines.append(
            f"  Overall: {self.overall_score:6.2f} / 100    "
            f"dominant: {self.dominant_domain}"
        )
        lines.append("  Per domain (intrinsic × role_weight = weighted):")
        for d in CANONICAL_DOMAIN_ORDER:
            if d not in self.per_domain:
                continue
            g = self.per_domain[d]
            lines.append(
                f"    {d:<14} intrinsic={g.intrinsic_score:6.2f}  "
                f"role_weight={g.role_weight:4.2f}  "
                f"weighted={g.weighted_score:6.2f}  "
                f"traits={g.included_traits} (skipped: {g.skipped_traits})"
            )
            for sd_name in sorted(g.subdomain_scores):
                lines.append(
                    f"      - {sd_name:<22} {g.subdomain_scores[sd_name]:6.2f}"
                )
        if self.warnings:
            lines.append("  Warnings:")
            for w in self.warnings:
                lines.append(f"    - {w}")
        else:
            lines.append("  Warnings: none")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def grade(profile: TraitProfile, engine: TraitEngine) -> GradeReport:
    """Compute a :class:`GradeReport` for ``profile``.

    Pure function. Same inputs → same output. No logging, no persistence.
    """
    per_domain: dict[str, DomainGrade] = {}
    domain_order = _domain_order(engine)

    for domain_name in domain_order:
        per_domain[domain_name] = _grade_domain(profile, engine, domain_name)

    overall = _overall_score(per_domain)
    dominant = _dominant_domain(per_domain)
    warnings = tuple(fc.name for fc in engine.scan_flagged(profile))

    return GradeReport(
        profile_dna=dna_short(profile),
        role=profile.role,
        per_domain=per_domain,
        overall_score=overall,
        dominant_domain=dominant,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _domain_order(engine: TraitEngine) -> list[str]:
    """Emit domains in canonical order first, then any non-canonical domain
    present in the engine (in engine's insertion order).

    This keeps the report stable when ADR-0001's domain list is the current
    one, while still grading any custom domain someone added via YAML without
    crashing. A non-canonical domain would land after the canon in iteration
    but *still* participate in overall score and dominant-domain selection.
    """
    canon = [d for d in CANONICAL_DOMAIN_ORDER if d in engine.domains]
    extras = [d for d in engine.domains if d not in CANONICAL_DOMAIN_ORDER]
    return canon + extras


def _grade_domain(
    profile: TraitProfile, engine: TraitEngine, domain_name: str
) -> DomainGrade:
    domain = engine.domains[domain_name]
    subdomain_scores: dict[str, float] = {}
    included = 0
    skipped = 0

    for sd_name, sd in domain.subdomains.items():
        sd_score, inc, skp = _subdomain_score(profile, sd.traits.values())
        subdomain_scores[sd_name] = sd_score
        included += inc
        skipped += skp

    intrinsic = _mean(subdomain_scores.values())
    role_weight = engine.effective_domain_weight(profile, domain_name)
    weighted = intrinsic * role_weight

    return DomainGrade(
        domain=domain_name,
        intrinsic_score=intrinsic,
        role_weight=role_weight,
        weighted_score=weighted,
        subdomain_scores=subdomain_scores,
        included_traits=included,
        skipped_traits=skipped,
    )


def _subdomain_score(
    profile: TraitProfile, traits: Iterable
) -> tuple[float, int, int]:
    """Return (weighted_average_0_to_100, included_trait_count, skipped_below_min).

    ``included_trait_count`` is every trait that contributed — which is all of
    them, since even low tertiary traits are included in the math. The
    "skipped" number counts tertiary traits below the prose threshold, for
    operator awareness only.
    """
    numerator = 0.0
    denominator = 0.0
    included = 0
    skipped = 0

    for trait in traits:
        v = int(profile.trait_values[trait.name])
        tw = trait.tier_weight
        numerator += tw * v
        denominator += tw
        included += 1
        if trait.tier == "tertiary" and v < TERTIARY_MIN_VALUE:
            skipped += 1

    # Schema guarantees ≥1 trait per subdomain, so denominator > 0. Guard
    # anyway so a future schema change doesn't silently emit NaN.
    if denominator <= 0.0:
        return (0.0, included, skipped)

    return (numerator / denominator, included, skipped)


def _overall_score(per_domain: dict[str, DomainGrade]) -> float:
    """Role-weighted mean of intrinsic domain scores, in 0..100.

    Equivalent to Σ(intrinsic × role_weight) / Σ(role_weight). This is the
    formula from ADR-0001, normalized so the answer stays comparable to per-
    domain intrinsic numbers.
    """
    weight_sum = sum(g.role_weight for g in per_domain.values())
    if weight_sum <= 0.0:
        return 0.0
    weighted_sum = sum(g.weighted_score for g in per_domain.values())
    return weighted_sum / weight_sum


def _dominant_domain(per_domain: dict[str, DomainGrade]) -> str:
    """Domain with the highest weighted_score. Ties break by canonical order
    (ADR-0001): any canonical domain beats any non-canonical domain on a tie;
    within the canonical list, earlier wins. Non-canonical ties fall back to
    insertion order in ``per_domain``.
    """
    if not per_domain:
        raise ValueError("grade() produced no per-domain results")

    def tie_key(domain_name: str) -> tuple[int, int]:
        try:
            return (0, CANONICAL_DOMAIN_ORDER.index(domain_name))
        except ValueError:
            # Non-canonical domain — sort after all canonical ones.
            return (1, list(per_domain).index(domain_name))

    best_name = None
    best_score = float("-inf")
    best_tie = (2, 0)  # sentinel worse than any real tie_key
    for name, g in per_domain.items():
        s = g.weighted_score
        tk = tie_key(name)
        if s > best_score or (s == best_score and tk < best_tie):
            best_name = name
            best_score = s
            best_tie = tk
    assert best_name is not None  # per_domain non-empty, loop ran
    return best_name


def _mean(values: Iterable[float]) -> float:
    vs = list(values)
    if not vs:
        return 0.0
    return sum(vs) / len(vs)
