"""Trait engine — loads, validates, and queries the hierarchical trait tree.

The engine is the single source of truth for trait metadata. Every other component
(soul generator, grading engine, agent factory) goes through this engine rather
than reading the YAML directly. That way a schema change lands in one place.

Design reference: docs/architecture/trait-tree-design.md
Schema:            config/trait_tree.yaml
Decision record:   docs/decisions/ADR-0001-hierarchical-trait-tree.md
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

Tier = Literal["primary", "secondary", "tertiary"]

TIER_WEIGHTS: dict[Tier, float] = {
    "primary": 1.0,
    "secondary": 0.6,
    "tertiary": 0.3,
}

DEFAULT_TREE_PATH = Path("config/trait_tree.yaml")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class TraitTreeError(Exception):
    """Base class for trait-engine failures."""


class SchemaError(TraitTreeError):
    """The loaded YAML doesn't match the expected schema."""


class UnknownTraitError(TraitTreeError):
    """Referenced a trait name that doesn't exist in the tree."""


class UnknownRoleError(TraitTreeError):
    """Referenced a role name that doesn't exist in the tree."""


class InvalidTraitValueError(TraitTreeError):
    """A trait value is outside [0, 100]."""


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Trait:
    name: str
    domain: str
    subdomain: str
    tier: Tier
    default: int
    desc: str
    scale_low: str
    scale_mid: str
    scale_high: str

    @property
    def tier_weight(self) -> float:
        return TIER_WEIGHTS[self.tier]


@dataclass(frozen=True)
class Subdomain:
    name: str
    domain: str
    description: str
    traits: dict[str, Trait]


@dataclass(frozen=True)
class Domain:
    name: str
    description: str
    subdomains: dict[str, Subdomain]


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    domain_weights: dict[str, float]


@dataclass(frozen=True)
class FlaggedCombination:
    name: str
    conditions: dict[str, tuple[str, int]]  # trait -> (operator, threshold)
    warning: str


@dataclass
class TraitProfile:
    """A concrete agent's trait values, plus the role that sets domain weights."""

    role: str
    trait_values: dict[str, int]
    domain_weight_overrides: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
_COND_RE = re.compile(r"^(>=|<=|>|<|==)\s*(\d{1,3})$")


class TraitEngine:
    """Loads and exposes the trait tree."""

    def __init__(self, tree_path: Path | str | None = None) -> None:
        self.tree_path = Path(tree_path) if tree_path else DEFAULT_TREE_PATH
        self._raw: dict = self._load()
        self.version: str = str(self._raw.get("version", "?"))
        self.min_domain_weight: float
        self.max_domain_weight: float
        self.domains: dict[str, Domain]
        self.roles: dict[str, Role]
        self.flagged_combinations: list[FlaggedCombination]
        self._traits_by_name: dict[str, Trait]
        self._parse_and_validate()

    # ---- loading & validation -------------------------------------------
    def _load(self) -> dict:
        if not self.tree_path.exists():
            raise SchemaError(f"Trait tree not found: {self.tree_path}")
        with self.tree_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise SchemaError(f"Trait tree root must be a mapping, got {type(data).__name__}")
        return data

    def _parse_and_validate(self) -> None:
        constraints = self._raw.get("constraints", {}) or {}
        self.min_domain_weight = float(constraints.get("min_domain_weight", 0.4))
        self.max_domain_weight = float(constraints.get("max_domain_weight", 3.0))

        tier_weights = constraints.get("tier_weights", {}) or {}
        for tier, expected in TIER_WEIGHTS.items():
            got = tier_weights.get(tier)
            if got is not None and float(got) != expected:
                raise SchemaError(
                    f"Tier weight for '{tier}' in YAML ({got}) disagrees with engine ({expected})"
                )

        domains_raw = self._raw.get("domains")
        if not isinstance(domains_raw, dict) or not domains_raw:
            raise SchemaError("Tree must contain a non-empty 'domains' mapping")

        self.domains = {}
        self._traits_by_name = {}
        for d_name, d_body in domains_raw.items():
            self.domains[d_name] = self._parse_domain(d_name, d_body)

        roles_raw = self._raw.get("roles") or {}
        if not isinstance(roles_raw, dict):
            raise SchemaError("'roles' must be a mapping if present")
        self.roles = {r_name: self._parse_role(r_name, r_body) for r_name, r_body in roles_raw.items()}

        self.flagged_combinations = self._parse_flagged(constraints.get("flagged_combinations") or [])

    def _parse_domain(self, name: str, body: dict) -> Domain:
        if not isinstance(body, dict):
            raise SchemaError(f"Domain '{name}' must be a mapping")
        subdomains_raw = body.get("subdomains") or {}
        if not isinstance(subdomains_raw, dict) or not subdomains_raw:
            raise SchemaError(f"Domain '{name}' needs at least one subdomain")
        subdomains: dict[str, Subdomain] = {}
        for s_name, s_body in subdomains_raw.items():
            subdomains[s_name] = self._parse_subdomain(name, s_name, s_body)
        return Domain(
            name=name,
            description=str(body.get("description", "")),
            subdomains=subdomains,
        )

    def _parse_subdomain(self, domain: str, name: str, body: dict) -> Subdomain:
        if not isinstance(body, dict):
            raise SchemaError(f"Subdomain '{domain}.{name}' must be a mapping")
        traits_raw = body.get("traits") or {}
        if not isinstance(traits_raw, dict) or not traits_raw:
            raise SchemaError(f"Subdomain '{domain}.{name}' needs at least one trait")
        traits: dict[str, Trait] = {}
        for t_name, t_body in traits_raw.items():
            trait = self._parse_trait(domain, name, t_name, t_body)
            if t_name in self._traits_by_name:
                raise SchemaError(f"Trait '{t_name}' defined more than once in the tree")
            traits[t_name] = trait
            self._traits_by_name[t_name] = trait
        return Subdomain(
            name=name,
            domain=domain,
            description=str(body.get("description", "")),
            traits=traits,
        )

    def _parse_trait(self, domain: str, subdomain: str, name: str, body: dict) -> Trait:
        if not isinstance(body, dict):
            raise SchemaError(f"Trait '{name}' must be a mapping")
        tier = body.get("tier")
        if tier not in TIER_WEIGHTS:
            raise SchemaError(
                f"Trait '{name}' has invalid tier '{tier}'. Must be one of {list(TIER_WEIGHTS)}"
            )
        default = body.get("default")
        if not isinstance(default, int) or not 0 <= default <= 100:
            raise SchemaError(f"Trait '{name}' default must be int in [0, 100], got {default!r}")
        scale = body.get("scale") or {}
        return Trait(
            name=name,
            domain=domain,
            subdomain=subdomain,
            tier=tier,  # type: ignore[arg-type]
            default=default,
            desc=str(body.get("desc", "")),
            scale_low=str(scale.get("low", "")),
            scale_mid=str(scale.get("mid", "")),
            scale_high=str(scale.get("high", "")),
        )

    def _parse_role(self, name: str, body: dict) -> Role:
        if not isinstance(body, dict):
            raise SchemaError(f"Role '{name}' must be a mapping")
        weights_raw = body.get("domain_weights") or {}
        if not isinstance(weights_raw, dict) or not weights_raw:
            raise SchemaError(f"Role '{name}' needs domain_weights")
        weights: dict[str, float] = {}
        for d_name, w in weights_raw.items():
            if d_name not in self.domains:
                raise SchemaError(f"Role '{name}' references unknown domain '{d_name}'")
            w_f = float(w)
            if not self.min_domain_weight <= w_f <= self.max_domain_weight:
                raise SchemaError(
                    f"Role '{name}' weight for '{d_name}' ({w_f}) outside "
                    f"[{self.min_domain_weight}, {self.max_domain_weight}]"
                )
            weights[d_name] = w_f
        return Role(name=name, description=str(body.get("description", "")), domain_weights=weights)

    def _parse_flagged(self, raw: list) -> list[FlaggedCombination]:
        out: list[FlaggedCombination] = []
        for item in raw:
            name = str(item.get("name", ""))
            conditions: dict[str, tuple[str, int]] = {}
            for t_name, cond in (item.get("traits") or {}).items():
                if t_name not in self._traits_by_name:
                    raise SchemaError(
                        f"Flagged combination '{name}' references unknown trait '{t_name}'"
                    )
                m = _COND_RE.match(str(cond).strip())
                if not m:
                    raise SchemaError(
                        f"Flagged combination '{name}' has invalid condition for '{t_name}': {cond!r}"
                    )
                op, thresh = m.group(1), int(m.group(2))
                if not 0 <= thresh <= 100:
                    raise SchemaError(f"Threshold {thresh} out of [0, 100]")
                conditions[t_name] = (op, thresh)
            out.append(FlaggedCombination(name=name, conditions=conditions, warning=str(item.get("warning", ""))))
        return out

    # ---- lookup API ------------------------------------------------------
    def get_trait(self, name: str) -> Trait:
        if name not in self._traits_by_name:
            raise UnknownTraitError(name)
        return self._traits_by_name[name]

    def get_role(self, name: str) -> Role:
        if name not in self.roles:
            raise UnknownRoleError(name)
        return self.roles[name]

    def list_traits(self, domain: str | None = None) -> list[Trait]:
        if domain is None:
            return list(self._traits_by_name.values())
        if domain not in self.domains:
            raise SchemaError(f"Unknown domain: {domain}")
        return [
            t
            for sd in self.domains[domain].subdomains.values()
            for t in sd.traits.values()
        ]

    # ---- profile construction & analysis ---------------------------------
    def build_profile(
        self,
        role: str,
        overrides: dict[str, int] | None = None,
        domain_weight_overrides: dict[str, float] | None = None,
    ) -> TraitProfile:
        """Create a TraitProfile for a role; trait values default, overridden as specified."""
        role_obj = self.get_role(role)  # validates role name
        values: dict[str, int] = {t.name: t.default for t in self._traits_by_name.values()}
        for name, v in (overrides or {}).items():
            if name not in self._traits_by_name:
                raise UnknownTraitError(name)
            if not isinstance(v, int) or not 0 <= v <= 100:
                raise InvalidTraitValueError(f"{name}={v}")
            values[name] = v
        dw_over: dict[str, float] = {}
        for d, w in (domain_weight_overrides or {}).items():
            if d not in self.domains:
                raise SchemaError(f"Unknown domain in override: {d}")
            w_f = float(w)
            if not self.min_domain_weight <= w_f <= self.max_domain_weight:
                raise SchemaError(
                    f"Domain weight override {w_f} outside [{self.min_domain_weight}, {self.max_domain_weight}]"
                )
            dw_over[d] = w_f
        return TraitProfile(role=role_obj.name, trait_values=values, domain_weight_overrides=dw_over)

    def effective_domain_weight(self, profile: TraitProfile, domain: str) -> float:
        if domain in profile.domain_weight_overrides:
            return profile.domain_weight_overrides[domain]
        role_obj = self.get_role(profile.role)
        return role_obj.domain_weights.get(domain, 1.0)

    def effective_trait_weight(self, profile: TraitProfile, trait_name: str) -> float:
        """Role domain weight × tier weight. Does not include the trait value."""
        trait = self.get_trait(trait_name)
        return self.effective_domain_weight(profile, trait.domain) * trait.tier_weight

    def scan_flagged(self, profile: TraitProfile) -> list[FlaggedCombination]:
        """Return any flagged combinations that apply to this profile."""
        hits: list[FlaggedCombination] = []
        for fc in self.flagged_combinations:
            if all(
                _compare(profile.trait_values[tname], op, thresh)
                for tname, (op, thresh) in fc.conditions.items()
            ):
                hits.append(fc)
        return hits


def _compare(value: int, op: str, thresh: int) -> bool:
    if op == ">=":
        return value >= thresh
    if op == "<=":
        return value <= thresh
    if op == ">":
        return value > thresh
    if op == "<":
        return value < thresh
    if op == "==":
        return value == thresh
    raise ValueError(f"Unsupported operator: {op}")
