# ADR-0003 — Grading Engine (Config-Grade)

- **Status:** Accepted
- **Date:** 2026-04-21
- **Supersedes:** —
- **Related:** ADR-0001 (hierarchical trait tree), ADR-0002 (DNA and lineage)

## Context

ADR-0001 wrote down the behavior-score formula:

```
behavior_score = Σ_domains [ role_weight × Σ_subdomains [ Σ_traits [ tier_weight × value × relevance ] ] ]
```

but never said what it was for, and the codebase hasn't implemented it. Two distinct things could use a formula like that:

1. **Config grading** — given a `TraitProfile`, produce a characterization of the agent itself: per-domain scores and an overall score. A stable, deterministic, machine-computable summary of "how security-dominant, how emotionally-weighted, how overall-tuned is this agent?" Useful for:
   - UI comparisons ("Agent A is 73 on security, Agent B is 42")
   - Golden-file testing (grade change implies behavior change, independent of DNA which is about identity, not intensity)
   - Sorting and filtering an agent library
   - Giving the operator a one-glance read on what they've built

2. **Output grading** — given an agent *finding* (a piece of output the agent produced at runtime), score how aligned, accurate, or risky it is. Needs either an LLM judge or rich heuristics over content. Depends on actual agents existing and producing findings.

This ADR scopes in **config grading only**. Output grading is explicitly Phase 3 work, blocked on the agent factory landing. The `relevance` factor in the ADR-0001 formula is defined but not computed in v0.1 — it defaults to `1.0` and becomes meaningful when output grading arrives (relevance of a given trait to a given decision).

Rationale for scoping: config grading is a ~100-line deterministic function over data we already have. Output grading is a research-flavored problem with design space (LLM judge? keyword rubric? hybrid?) that should not be rushed into a v0.1 ADR.

## Decision

Build `src/forest_soul_forge/core/grading.py` exposing a pure function and a small result dataclass.

### Public API

```python
@dataclass(frozen=True)
class DomainGrade:
    domain: str
    intrinsic_score: float       # 0..100, role-independent
    role_weight: float            # from the role preset
    weighted_score: float         # intrinsic_score * role_weight
    subdomain_scores: dict[str, float]  # 0..100 per subdomain
    included_traits: int          # count of traits that contributed
    skipped_traits: int           # count skipped (value below tertiary_min_value)


@dataclass(frozen=True)
class GradeReport:
    profile_dna: str              # short DNA of the profile graded
    role: str
    per_domain: dict[str, DomainGrade]
    overall_score: float          # weighted avg across domains, 0..100
    dominant_domain: str          # domain with highest weighted_score
    warnings: tuple[str, ...]     # flagged-combination names hit

    def render(self) -> str:
        """Human-readable multi-line summary."""
```

A module-level `grade(profile: TraitProfile, engine: TraitEngine) -> GradeReport` is the single entry point.

### Math

All math is deterministic, float64, and uses the existing `Trait.tier_weight` / `Role.domain_weights` values from `TraitEngine`.

**Trait contribution (same as in soul ordering):**
```
contribution(trait) = tier_weight(trait) * trait_value(trait)
```
Units: 0..100 (since value is 0..100 and tier_weight is ≤1.0).

**Subdomain score (tier-weighted average over traits in that subdomain):**
```
subdomain_score = Σ contribution(trait) / Σ tier_weight(trait)
                = Σ (tier_weight * value) / Σ tier_weight
```
This is the weighted average trait value within a subdomain. Range: 0..100.

**Intrinsic domain score (simple average across subdomains):**
```
intrinsic_domain_score = mean(subdomain_score for subdomain in domain)
```
Simple mean, not weighted — subdomains are equal citizens within a domain. Range: 0..100.

**Weighted domain score (role emphasis applied):**
```
weighted_domain_score = intrinsic_domain_score * role_weight(domain)
```
Range: 0..300 (role weights are 0.4..3.0).

**Overall score (normalized back to 0..100):**
```
overall_score = Σ weighted_domain_score(domain) / Σ role_weight(domain)
             = Σ (intrinsic * role_weight) / Σ role_weight
```
This is a role-weighted average of intrinsic domain scores. Range: 0..100.

**Dominant domain:** `argmax(weighted_domain_score)`. Ties broken by ADR-0001's canonical domain order (security, audit, emotional, cognitive, communication).

### Inclusion rules

- Traits with `value < tertiary_min_value` (default 40, same threshold already used by `SoulGenerator` for prose inclusion) still contribute to subdomain scores. Grading sees the full profile even when prose doesn't.
  - Rationale: the prose skip is a readability choice; the grade should reflect the full configured profile, including low-valued tertiary traits. A tertiary trait at value 10 still lowers its subdomain's average, which is the correct signal.
- `skipped_traits` in `DomainGrade` counts traits below the prose threshold for the operator's awareness, but they're *included* in the score.
- Flagged combinations (`engine.scan_flagged(profile)`) are surfaced as `GradeReport.warnings` — they don't penalize the score, they just appear on the report. Penalization would require deciding "how much" which is an open design question, and ADR-0003 declines to answer it.

### Determinism and stability

`grade()` is a pure function: same profile + same engine YAML → same report, always. This is mandatory for:
- Golden-file tests that assert grade stability across refactors.
- Reproducible UI.
- Any future cache/index.

The report is not persisted by default; callers serialize it if they need it. No hidden clock, no hidden random.

## Consequences

### Positive

- Operator gets a one-glance characterization of any agent they build.
- Comparison and sorting across a library of agents becomes trivial.
- Golden-file tests can lock grades alongside DNA — catches regressions where the math changes but the hash doesn't.
- Later output-grading can reuse `GradeReport` as the "config baseline" to compare runtime behavior against.

### Negative

- The formula has tuning knobs (equal subdomain weighting; role weight as simple multiplier) that are stated now and hard to change later without re-grading every agent. Mitigated by: the choice is documented here, and any change gets its own ADR + `grading_schema_version` bump on the report.
- A `GradeReport` is not self-describing enough to be an end-user's only view of an agent — it's a numeric summary, not an explanation. That's fine; soul.md prose is the explanation.

### Neutral

- `relevance` stays as a future parameter, defaulting to 1.0. This ADR doesn't commit to how it will be computed when output grading arrives.
- Grade is not part of DNA. Two agents with the same DNA (same profile) always have the same grade. Two agents with different DNAs could have the same overall grade — DNA is identity, grade is intensity.

## Alternatives considered

**Single scalar behavior_score only (no per-domain breakdown).** Simpler, matches ADR-0001's formula exactly. Rejected because it erases the thematic structure that's the whole point of ADR-0001 — operators would have to recompute per-domain in their heads.

**LLM-based grading.** Feed the soul.md to a local LLM and ask for a rubric score. Rejected for v0.1: non-deterministic, model-dependent, and unavailable without the agent runtime. Revisit as an *output-grading* tool in Phase 3, not a config-grading one.

**Z-scores against a catalog baseline.** "This agent is 1.3σ above the library mean on security." Rejected as premature — no catalog to z-score against yet, and the concept only becomes useful once there's a library. Note for Phase 4.

**Penalize flagged combinations directly in the score.** E.g., subtract 10 per hit. Rejected: the penalty amount is arbitrary and masks the underlying config. Surface warnings separately — operator decides.

**Weight subdomains by how many traits they contain.** Rejected: rewards inflating trait counts in favored subdomains. Equal-weight subdomains keep the math honest.

## Open questions

- **`tertiary_min_value` coupling.** Currently `SoulGenerator` uses 40 as the prose-inclusion threshold. If that ever moves, `GradeReport.skipped_traits` moves with it. Consider extracting the constant to the trait tree YAML as a schema property.
- **Subdomain count imbalance.** Security has more subdomains than Communication in v0.1. Simple mean across subdomains means each subdomain in a small-subdomain-count domain has more leverage. Noted, not fixed — ADR-0001 accepted the v0.1 schema and rebalancing subdomains is its own migration.
- **Grading the lineage chain.** Should a spawned agent's grade carry a "drift from parent" number? Useful but specific to swarms. Defer to whichever ADR introduces the agent factory.

## Scope explicitly out

- Output grading (Phase 3).
- LLM judgment.
- User-tunable tier or domain weights at runtime.
- Persistence of grade reports to disk (caller's responsibility if needed).
- `relevance` computation.
