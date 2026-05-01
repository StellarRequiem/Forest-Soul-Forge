"""Constitution builder — derives a machine-readable rulebook from a profile.

`soul.md` is prose the LLM reads. The constitution is structured data the
runtime branches on: what's allowed, what's forbidden, what needs approval,
what the risk thresholds and drift rules are. It's a pure function of
``(role, profile, engine, templates)`` — same inputs → byte-identical hash.

The document is composed in three layers, ADR-0004 style:

    1. role_base        — per-role baseline from constitution_templates.yaml
    2. trait_modifiers  — rules triggered by trait values
    3. flagged combos   — every engine-flagged combination becomes a forbid

Conflict resolution is **strictness-wins** across
{``allow``, ``require_human_approval``, ``forbid``}. Policies that use other
rule words (``require_explicit_uncertainty``, etc.) stack independently —
they're modifiers, not gates. When a stricter policy displaces a weaker one,
the weaker one survives with a ``superseded_by`` pointer so reviewers never
silently lose a rule.

Hash design: the body (policies + thresholds + scope + duties + drift) is
canonicalized to JSON with sorted keys and hashed with SHA-256. The hash
does **not** cover ``generated_at`` or agent identity — two agents with the
same derived rulebook produce the same ``constitution_hash``. Agent identity
(DNA, role, name) is bound in ``soul.md`` frontmatter, not here.

Design reference: docs/decisions/ADR-0004-constitution-builder.md
Related: ADR-0001 (trait tree), ADR-0002 (DNA), ADR-0003 (grading).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

import yaml

from forest_soul_forge.core.dna import dna_full, dna_short
from forest_soul_forge.core.trait_engine import (
    SchemaError,
    TraitEngine,
    TraitProfile,
    UnknownRoleError,
    UnknownTraitError,
)

CONSTITUTION_SCHEMA_VERSION: int = 1

DEFAULT_TEMPLATES_PATH = Path("config/constitution_templates.yaml")

# Rules that participate in strictness-wins conflict resolution. A rule not in
# this list is a modifier (e.g. ``require_explicit_uncertainty``) that stacks
# alongside gates rather than replacing them.
STRICTNESS_ORDER: tuple[str, ...] = (
    "allow",
    "require_human_approval",
    "forbid",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ConstitutionError(Exception):
    """Base class for constitution-builder failures."""


class TemplateSchemaError(ConstitutionError):
    """constitution_templates.yaml is malformed or missing required fields."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Policy:
    """One rule in the constitution.

    ``source`` is provenance — which layer produced this policy. Useful for
    audit and for the strictness-wins logic (so we can point ``superseded_by``
    at the policy id that displaced us).
    """

    id: str
    source: str
    rule: str
    triggers: tuple[str, ...]
    rationale: str
    superseded_by: str | None = None


@dataclass(frozen=True)
class RiskThresholds:
    auto_halt_risk: float
    escalate_risk: float
    min_confidence_to_act: float


@dataclass(frozen=True)
class DriftMonitoring:
    profile_hash_check: str
    max_profile_deviation: int
    on_drift: str


@dataclass(frozen=True)
class Constitution:
    """A derived, byte-stable rulebook for a single agent.

    The ``constitution_hash`` is a function of ``policies`` +
    ``risk_thresholds`` + ``out_of_scope`` + ``operator_duties`` +
    ``drift_monitoring`` + ``tools`` (per ADR-0018 T2.5) + ``genre``
    (per ADR-0021 T3). It intentionally excludes ``generated_at`` and
    agent identity — two agents with identical rulebooks share a hash.

    ``tools`` is the per-tool resolved policy (kit + per-tool
    constraints). Two agents with the same trait profile but different
    tool overrides will have different constitution hashes, which is
    correct: their effective surface differs.

    ``genre`` is the role's claimed genre per ADR-0021. It's a property
    of the agent's policy floor (Companion → local-only provider,
    Observer → no non-read_only side effects, etc.), so it's part of
    the rulebook and therefore the hash. ``None`` is the legacy /
    unclaimed-role path: agents whose role isn't claimed by any genre
    in the loaded ``genres.yaml`` get ``None`` and the canonical body
    serializes the field as the empty string ``""`` so old artifacts
    (which never carried a genre) re-derive to a stable hash.

    ``genre_description`` is the operator-facing prose. NOT in the
    hash — descriptions are documentation, not policy. Stored on the
    constitution for ``to_yaml()`` round-trip but excluded from
    ``canonical_body()`` deliberately.
    """

    schema_version: int
    agent_dna: str
    agent_dna_full: str
    role: str
    agent_name: str
    policies: tuple[Policy, ...]
    risk_thresholds: RiskThresholds
    out_of_scope: tuple[str, ...]
    operator_duties: tuple[str, ...]
    drift_monitoring: DriftMonitoring
    # Per-tool resolved constraints (ADR-0018 T2.5). Empty tuple when the
    # agent has no tool surface (legacy birth, role with no archetype
    # kit). Each entry is a frozen dict so two equivalent constitutions
    # produce byte-identical hashes.
    tools: tuple[dict[str, Any], ...] = ()
    # Role's genre (ADR-0021 T3). None when the role is unclaimed by any
    # loaded genre OR the genre engine wasn't available at build time.
    # Hashed; description is not.
    genre: str | None = None
    genre_description: str | None = None
    # Initiative ladder (ADR-0021-amendment §2). Both default to "L5"
    # for back-compat: a constitution built without these fields keeps
    # the v1 behavior of "no initiative ceiling." When the genre engine
    # supplies values, they reflect the genre's max_initiative_level
    # (ceiling) and default_initiative_level (the genre default that
    # the operator can narrow but not widen). Both fields land in the
    # canonical body and therefore the hash — two agents with the same
    # role + traits + tools but different initiative postures get
    # different constitution hashes.
    initiative_level: str = "L5"
    initiative_ceiling: str = "L5"

    # ---- hashing --------------------------------------------------------
    def canonical_body(self) -> dict[str, Any]:
        """Return the rulebook as a sort-stable dict, ready to JSON-canonicalize.

        ``genre`` is included unconditionally with ``""`` as the
        unclaimed-role sentinel so old constitutions (which never
        carried genre) re-derive to a stable hash. ``genre_description``
        is intentionally excluded — it's documentation, not policy.

        ``initiative_level`` and ``initiative_ceiling`` (ADR-0021-amendment
        §2) are included unconditionally with the L5/L5 back-compat
        defaults — same shape as the genre field's introduction. A
        post-amendment constitution carries the genre-derived values;
        a pre-amendment constitution re-derived without them gets the
        defaults. Two agents with identical role + traits + tools but
        different initiative postures get different hashes, which is
        correct: their effective autonomy posture differs.
        """
        return {
            "policies": [_policy_to_dict(p) for p in self.policies],
            "risk_thresholds": {
                "auto_halt_risk": self.risk_thresholds.auto_halt_risk,
                "escalate_risk": self.risk_thresholds.escalate_risk,
                "min_confidence_to_act": self.risk_thresholds.min_confidence_to_act,
            },
            "out_of_scope": list(self.out_of_scope),
            "operator_duties": list(self.operator_duties),
            "drift_monitoring": {
                "profile_hash_check": self.drift_monitoring.profile_hash_check,
                "max_profile_deviation": self.drift_monitoring.max_profile_deviation,
                "on_drift": self.drift_monitoring.on_drift,
            },
            "tools": [dict(sorted(t.items())) for t in self.tools],
            "genre": self.genre or "",
            "initiative_level": self.initiative_level,
            "initiative_ceiling": self.initiative_ceiling,
        }

    @property
    def constitution_hash(self) -> str:
        body = self.canonical_body()
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def constitution_hash_short(self) -> str:
        return self.constitution_hash[:12]

    # ---- serialization --------------------------------------------------
    def to_yaml(self, *, generated_at: str | None = None) -> str:
        """Render the constitution as a deterministic YAML document.

        ``generated_at`` is a caller-supplied timestamp (informational only —
        not in the hash). Pass ``None`` to omit the field entirely, which is
        what you want in tests that need byte-identical output.
        """
        doc: dict[str, Any] = {
            "schema_version": self.schema_version,
            "constitution_hash": self.constitution_hash,
        }
        if generated_at is not None:
            doc["generated_at"] = generated_at
        agent_block: dict[str, Any] = {
            "dna": self.agent_dna,
            "dna_full": self.agent_dna_full,
            "role": self.role,
            "agent_name": self.agent_name,
        }
        # Genre is part of agent identity at the YAML level, even though
        # it's policy at the hash level. Operators reading the file see
        # role and genre side-by-side. Omit when None for back-compat
        # with constitutions written before ADR-0021 T3 — those parse
        # cleanly because consumers tolerate missing genre.
        if self.genre is not None:
            agent_block["genre"] = self.genre
        if self.genre_description is not None:
            agent_block["genre_description"] = self.genre_description
        # Initiative posture (ADR-0021-amendment §2) lands in the agent
        # block alongside genre — both shape "what kind of agent is
        # this" at the operator-readable level. Omit when both are at
        # the L5/L5 back-compat default to keep pre-amendment YAML
        # byte-identical for callers that don't engage the new mechanism.
        # When at least one is non-default, both surface so an inspector
        # always sees a complete posture (level + ceiling pair).
        if self.initiative_level != "L5" or self.initiative_ceiling != "L5":
            agent_block["initiative_level"] = self.initiative_level
            agent_block["initiative_ceiling"] = self.initiative_ceiling
        doc["agent"] = agent_block
        # Body fields come after identity so readers see the rules front-and-
        # center rather than buried beneath metadata.
        doc["policies"] = [_policy_to_dict(p) for p in self.policies]
        doc["risk_thresholds"] = self.canonical_body()["risk_thresholds"]
        doc["out_of_scope"] = list(self.out_of_scope)
        doc["operator_duties"] = list(self.operator_duties)
        doc["drift_monitoring"] = self.canonical_body()["drift_monitoring"]
        # Per-tool resolved constraints (ADR-0018 T2.5). Always emit the
        # key — readers can rely on its presence. Empty list when the
        # agent has no tool surface.
        doc["tools"] = [dict(sorted(t.items())) for t in self.tools]

        # sort_keys=False keeps our intentional top-level ordering. The body
        # itself is already sorted by construction.
        return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def build(
    profile: TraitProfile,
    engine: TraitEngine,
    *,
    agent_name: str,
    templates_path: Path | str | None = None,
    tools: tuple[dict[str, Any], ...] = (),
    genre: str | None = None,
    genre_description: str | None = None,
    initiative_level: str = "L5",
    initiative_ceiling: str = "L5",
) -> Constitution:
    """Derive a :class:`Constitution` from a profile.

    Pure function modulo the YAML file read. Same inputs → same output.

    ``genre`` and ``genre_description`` are populated from the genre
    engine at the daemon edge (ADR-0021 T3). Pass ``None`` for both
    when there's no genre engine loaded, or when the role isn't
    claimed by any genre — the constitution still builds, just without
    a genre policy floor. The hash uses ``""`` as the no-genre
    sentinel so old artifacts re-derive to a stable hash.

    ``initiative_level`` + ``initiative_ceiling`` (ADR-0021-amendment §2)
    populate from the genre engine in the same way: the daemon edge
    reads the role's claimed genre's ``max_initiative_level`` /
    ``default_initiative_level`` and passes them through. The
    operator can narrow ``initiative_level`` (set it BELOW the
    ceiling); raising it above the ceiling refuses at the
    enforcement layer (writes.py). Both default to ``"L5"`` for
    back-compat — a constitution built without these args keeps
    the v1 behavior of "no initiative ceiling."
    """
    tpath = Path(templates_path) if templates_path else DEFAULT_TEMPLATES_PATH
    templates = _load_templates(tpath)

    role_base = _require_role(templates, profile.role)

    # ---- layer 1: role base ------------------------------------------------
    policies: list[Policy] = [
        _policy_from_template(raw, source=f"role:{profile.role}")
        for raw in (role_base.get("policies") or [])
    ]
    out_of_scope: list[str] = list(role_base.get("out_of_scope") or [])
    operator_duties: list[str] = list(role_base.get("operator_duties") or [])
    risk_thresholds = _risk_thresholds(role_base)
    drift = _drift_monitoring(role_base)

    # ---- layer 2: trait modifiers -----------------------------------------
    for mod in templates.get("trait_modifiers") or []:
        if not _condition_matches(profile, engine, mod.get("if") or {}):
            continue
        effect = mod.get("effect") or {}
        if "add_policy" in effect:
            p_raw = effect["add_policy"]
            source = _trait_source_tag(mod["if"])
            policies.append(_policy_from_template(p_raw, source=source))
        if "add_out_of_scope" in effect:
            item = str(effect["add_out_of_scope"])
            if item not in out_of_scope:
                out_of_scope.append(item)

    # ---- layer 3: flagged-combination policies ----------------------------
    flagged_template = templates.get("flagged_combo_policy_template") or {}
    flag_rule = str(flagged_template.get("rule", "forbid"))
    flag_triggers = tuple(sorted(flagged_template.get("triggers") or ["any_state_change"]))
    for fc in engine.scan_flagged(profile):
        policies.append(
            Policy(
                id=f"flagged_{fc.name}",
                source=f"flagged:{fc.name}",
                rule=flag_rule,
                triggers=flag_triggers,
                rationale=fc.warning,
            )
        )

    # ---- conflict resolution + canonicalization --------------------------
    policies = _resolve_conflicts(policies)
    policies = _canonicalize_policies(policies)
    out_of_scope = sorted(set(out_of_scope))
    operator_duties = list(operator_duties)  # preserve authorship order

    return Constitution(
        schema_version=CONSTITUTION_SCHEMA_VERSION,
        agent_dna=dna_short(profile),
        agent_dna_full=dna_full(profile),
        role=profile.role,
        agent_name=agent_name,
        policies=tuple(policies),
        risk_thresholds=risk_thresholds,
        out_of_scope=tuple(out_of_scope),
        operator_duties=tuple(operator_duties),
        drift_monitoring=drift,
        tools=tuple(tools),
        genre=genre,
        genre_description=genre_description,
        initiative_level=initiative_level,
        initiative_ceiling=initiative_ceiling,
    )


# ---------------------------------------------------------------------------
# Internals: template loading
# ---------------------------------------------------------------------------
def _load_templates(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise TemplateSchemaError(f"constitution_templates.yaml not found at {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise TemplateSchemaError("constitution_templates.yaml root must be a mapping")
    if data.get("schema_version") != CONSTITUTION_SCHEMA_VERSION:
        raise TemplateSchemaError(
            f"constitution_templates schema_version mismatch: got {data.get('schema_version')!r}, "
            f"expected {CONSTITUTION_SCHEMA_VERSION}"
        )
    if not isinstance(data.get("role_base"), dict):
        raise TemplateSchemaError("'role_base' must be a mapping")
    return data


def _require_role(templates: dict[str, Any], role: str) -> dict[str, Any]:
    rb = templates["role_base"]
    if role not in rb:
        raise UnknownRoleError(
            f"No constitution role_base defined for role '{role}'. "
            f"Available: {sorted(rb)}"
        )
    body = rb[role]
    if not isinstance(body, dict):
        raise TemplateSchemaError(f"role_base['{role}'] must be a mapping")
    return body


# ---------------------------------------------------------------------------
# Internals: policy construction
# ---------------------------------------------------------------------------
def _policy_from_template(raw: dict[str, Any], *, source: str) -> Policy:
    if not isinstance(raw, dict):
        raise TemplateSchemaError(f"policy must be a mapping, got {type(raw).__name__}")
    for req in ("id", "rule", "triggers", "rationale"):
        if req not in raw:
            raise TemplateSchemaError(f"policy missing required field '{req}': {raw!r}")
    triggers = raw["triggers"]
    if not isinstance(triggers, list) or not all(isinstance(t, str) for t in triggers):
        raise TemplateSchemaError(f"policy.triggers must be a list of strings: {raw!r}")
    return Policy(
        id=str(raw["id"]),
        source=source,
        rule=str(raw["rule"]),
        triggers=tuple(sorted(triggers)),
        rationale=str(raw["rationale"]),
    )


def _policy_to_dict(p: Policy) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": p.id,
        "rule": p.rule,
        "source": p.source,
        "triggers": list(p.triggers),
        "rationale": p.rationale,
    }
    if p.superseded_by is not None:
        d["superseded_by"] = p.superseded_by
    return d


def _risk_thresholds(role_base: dict[str, Any]) -> RiskThresholds:
    rt = role_base.get("risk_thresholds") or {}
    try:
        return RiskThresholds(
            auto_halt_risk=float(rt["auto_halt_risk"]),
            escalate_risk=float(rt["escalate_risk"]),
            min_confidence_to_act=float(rt["min_confidence_to_act"]),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise TemplateSchemaError(f"risk_thresholds invalid or missing: {err}") from err


def _drift_monitoring(role_base: dict[str, Any]) -> DriftMonitoring:
    dm = role_base.get("drift_monitoring") or {}
    try:
        return DriftMonitoring(
            profile_hash_check=str(dm["profile_hash_check"]),
            max_profile_deviation=int(dm["max_profile_deviation"]),
            on_drift=str(dm["on_drift"]),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise TemplateSchemaError(f"drift_monitoring invalid or missing: {err}") from err


# ---------------------------------------------------------------------------
# Internals: trait-modifier evaluation
# ---------------------------------------------------------------------------
def _condition_matches(
    profile: TraitProfile, engine: TraitEngine, cond: dict[str, Any]
) -> bool:
    if not cond:
        return False
    try:
        trait_name = str(cond["trait"])
        op = str(cond["op"])
        threshold = int(cond["value"])
    except (KeyError, TypeError, ValueError) as err:
        raise TemplateSchemaError(f"trait_modifier condition malformed: {cond!r} ({err})") from err

    # Validate trait exists (reuse engine's check for a consistent error path).
    try:
        engine.get_trait(trait_name)
    except UnknownTraitError:
        raise TemplateSchemaError(
            f"trait_modifier references unknown trait '{trait_name}'"
        )

    value = int(profile.trait_values[trait_name])
    return _compare(value, op, threshold)


def _compare(value: int, op: str, threshold: int) -> bool:
    if op == ">=": return value >= threshold
    if op == "<=": return value <= threshold
    if op == ">":  return value > threshold
    if op == "<":  return value < threshold
    if op == "==": return value == threshold
    raise TemplateSchemaError(f"unsupported condition operator: {op!r}")


def _trait_source_tag(cond: dict[str, Any]) -> str:
    return f"trait:{cond['trait']}:{cond['op']}{cond['value']}"


# ---------------------------------------------------------------------------
# Internals: conflict resolution
# ---------------------------------------------------------------------------
def _strictness_rank(rule: str) -> int:
    try:
        return STRICTNESS_ORDER.index(rule)
    except ValueError:
        return -1  # non-ordered rules don't participate


def _resolve_conflicts(policies: Iterable[Policy]) -> list[Policy]:
    """Strictness-wins across {allow, require_human_approval, forbid}.

    For each trigger, find the strictest ordered policy touching it. Any
    weaker ordered policy on the same trigger gets its ``superseded_by`` set
    to that winner's id. Non-ordered rules (modifiers) are untouched.
    """
    policies = list(policies)

    # For each trigger, find the strictest id. If two equally-strict ordered
    # policies touch the same trigger, neither supersedes the other — both
    # survive unchanged. The tie is honest: both trait modifiers fired.
    strictest_per_trigger: dict[str, tuple[int, str]] = {}
    for p in policies:
        rank = _strictness_rank(p.rule)
        if rank < 0:
            continue
        for t in p.triggers:
            cur = strictest_per_trigger.get(t)
            if cur is None or rank > cur[0]:
                strictest_per_trigger[t] = (rank, p.id)

    resolved: list[Policy] = []
    for p in policies:
        rank = _strictness_rank(p.rule)
        if rank < 0:
            resolved.append(p)
            continue
        # Am I superseded on any trigger by a strictly stricter policy?
        superseded_by: str | None = None
        for t in p.triggers:
            winner = strictest_per_trigger.get(t)
            if winner and winner[0] > rank and winner[1] != p.id:
                superseded_by = winner[1]
                break
        resolved.append(replace(p, superseded_by=superseded_by))
    return resolved


def _canonicalize_policies(policies: list[Policy]) -> list[Policy]:
    """Sort policies by id. Deterministic ordering guarantees byte-stable YAML."""
    return sorted(policies, key=lambda p: p.id)
