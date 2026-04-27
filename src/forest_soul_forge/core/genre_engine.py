"""Role genres and agent taxonomy — see ADR-0021.

Reads ``config/genres.yaml`` once at daemon startup and exposes:

* ``genre_for(role)`` — the GenreDef that claims a role (raises if unclaimed).
* ``roles_for(genre)`` — the tuple of roles in a genre.
* ``all_genres()`` — every loaded GenreDef.
* ``can_spawn(parent_genre, child_genre)`` — spawn-compatibility check.

Same load-time discipline as ``tool_catalog.py``:

* Each role appears in EXACTLY ONE genre. Duplicate-claim is fatal.
* Every genre named in any ``spawn_compatibility`` list resolves to a real
  loaded genre. A typo here would silently allow / forbid the wrong pair.
* Returns ``empty_engine()`` when the YAML is missing or malformed —
  daemon lifespan logs the failure and degrades gracefully (genre-aware
  surfaces show "no genre" rather than 503'ing).

The "every TraitEngine role must be claimed by some genre" check (ADR-0021
constraint) lives in a SEPARATE function (``validate_against_trait_engine``)
because it requires the loaded TraitEngine — that's a daemon-lifespan
concern, not a YAML-only concern. Keeping that separation makes the loader
testable without spinning up the trait tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# Mirrors tool_catalog.SIDE_EFFECT_VALUES — keeping the same vocabulary so
# a genre's max_side_effects compares cleanly against a tool's side_effects.
_SIDE_EFFECT_TIERS = ("read_only", "network", "filesystem", "external")
# For a genre's risk_profile.provider_constraint. Only "local_only" today
# (Companion's Phase 5 floor). Extensible to "frontier_only" / "any" later.
_PROVIDER_CONSTRAINTS = frozenset({"local_only"})
# ADR-0027 §1 read scopes. A genre's `memory_ceiling` is the widest scope an
# agent in that genre may write a memory entry under — strictly stricter than
# (or equal to) `realm`. Enforced at the memory write path (ADR-0022 v0.1+).
# Order encodes strictness: private < lineage < consented < realm. A genre's
# ceiling rejects any write whose scope index exceeds this.
_MEMORY_CEILING_TIERS: tuple[str, ...] = ("private", "lineage", "consented", "realm")
_MEMORY_CEILINGS = frozenset(_MEMORY_CEILING_TIERS)


class GenreEngineError(Exception):
    """Raised when ``genres.yaml`` is malformed or violates an integrity rule."""


@dataclass(frozen=True)
class RiskProfile:
    """A genre's risk floor.

    ``max_side_effects`` is the strictest side_effects tier the genre's
    standard kit defaults to (and beyond which tools require explicit
    operator override at birth time per ADR-0021 T5).

    ``provider_constraint`` is None for most genres; ``"local_only"`` for
    Companion (ADR-0008 Phase 5 floor — therapy / accessibility agents
    must run on local providers, no frontier).

    ``memory_ceiling`` is the widest scope an agent in this genre may write
    a memory entry under (ADR-0027 §1 + §5). Defaults to ``"private"``
    when the YAML omits it — strictest sensible fallback. Enforced at the
    memory write path; widening past the ceiling requires an explicit
    ``memory_scope_override`` audit event with operator id + reason.
    """

    max_side_effects: str
    provider_constraint: str | None = None
    memory_ceiling: str = "private"


@dataclass(frozen=True)
class GenreDef:
    """One genre entry from ``genres.yaml``."""

    name: str
    description: str
    risk_profile: RiskProfile
    default_kit_pattern: tuple[str, ...]
    trait_emphasis: tuple[str, ...]
    memory_pattern: str
    spawn_compatibility: tuple[str, ...]
    roles: tuple[str, ...]


@dataclass(frozen=True)
class GenreEngine:
    """The loaded genre catalog. Held on ``app.state.genre_engine`` post-T2."""

    version: str
    genres: dict[str, GenreDef]   # keyed by genre name
    role_to_genre: dict[str, str]   # inverse index, role -> genre name
    source_path: Path | None = None

    # ----- public API -----

    def genre_for(self, role: str) -> GenreDef:
        """Look up the genre that claims ``role``.

        Raises :class:`GenreEngineError` if no genre claims this role —
        callers that want the "unclaimed → None" semantic should catch.
        """
        gname = self.role_to_genre.get(role)
        if gname is None:
            raise GenreEngineError(
                f"role {role!r} is not claimed by any genre "
                f"(known roles: {sorted(self.role_to_genre.keys())})"
            )
        return self.genres[gname]

    def roles_for(self, genre: str) -> tuple[str, ...]:
        """Return roles in a genre. Raises if genre is unknown."""
        gd = self.genres.get(genre)
        if gd is None:
            raise GenreEngineError(
                f"unknown genre: {genre!r} "
                f"(known: {sorted(self.genres.keys())})"
            )
        return gd.roles

    def all_genres(self) -> tuple[GenreDef, ...]:
        """Every loaded GenreDef in declaration order."""
        return tuple(self.genres.values())

    def can_spawn(self, parent_genre: str, child_genre: str) -> bool:
        """True iff a parent of ``parent_genre`` is allowed to spawn a child
        of ``child_genre`` per the parent's ``spawn_compatibility``.

        Unknown parent_genre raises (caller bug — pick a real genre). Unknown
        child_genre returns False (defensive — an unrecognized child is
        never compatible).
        """
        parent = self.genres.get(parent_genre)
        if parent is None:
            raise GenreEngineError(
                f"unknown parent genre: {parent_genre!r}"
            )
        if child_genre not in self.genres:
            return False
        return child_genre in parent.spawn_compatibility


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_genres(path: Path | str) -> GenreEngine:
    """Read + validate ``genres.yaml``. Raises :class:`GenreEngineError`
    on any integrity violation.

    Daemon lifespan should wrap this in try/except and call
    :func:`empty_engine` on failure so the rest of the system stays up.
    """
    p = Path(path)
    if not p.exists():
        raise GenreEngineError(f"genres file not found at {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise GenreEngineError(f"genres.yaml YAML parse error: {e}") from e

    if not isinstance(raw, dict):
        raise GenreEngineError(
            f"genres.yaml root must be a mapping, got {type(raw).__name__}"
        )

    version_raw = raw.get("version")
    if version_raw is None:
        raise GenreEngineError("genres.yaml 'version' is required")
    version = str(version_raw).strip()
    if not version:
        raise GenreEngineError("genres.yaml 'version' must not be empty")

    genres_raw = raw.get("genres") or {}
    if not isinstance(genres_raw, dict):
        raise GenreEngineError("'genres' must be a mapping of genre name -> entry")
    if not genres_raw:
        raise GenreEngineError("'genres' must contain at least one genre")

    genres: dict[str, GenreDef] = {}
    role_to_genre: dict[str, str] = {}

    for gname, body in genres_raw.items():
        gd = _parse_genre_entry(str(gname), body)
        if gd.name in genres:
            raise GenreEngineError(f"duplicate genre name: {gd.name!r}")
        genres[gd.name] = gd
        for role in gd.roles:
            if role in role_to_genre:
                raise GenreEngineError(
                    f"role {role!r} is claimed by both {role_to_genre[role]!r} "
                    f"and {gd.name!r}; each role must belong to exactly one genre"
                )
            role_to_genre[role] = gd.name

    # Validate spawn_compatibility entries against the loaded genre set.
    # Doing this AFTER all genres are parsed so forward references work
    # (a genre can list another genre that's declared later in the file).
    for gd in genres.values():
        for target in gd.spawn_compatibility:
            if target not in genres:
                raise GenreEngineError(
                    f"genre {gd.name!r}.spawn_compatibility references "
                    f"unknown genre {target!r} "
                    f"(known: {sorted(genres.keys())})"
                )

    return GenreEngine(
        version=version,
        genres=genres,
        role_to_genre=role_to_genre,
        source_path=p,
    )


def _parse_genre_entry(name: str, entry: Any) -> GenreDef:
    """Parse + validate one genre body."""
    if not isinstance(entry, dict):
        raise GenreEngineError(f"genre {name!r} must be a mapping")

    def _require(field_name: str) -> Any:
        if field_name not in entry:
            raise GenreEngineError(
                f"genre {name!r} missing required field {field_name!r}"
            )
        return entry[field_name]

    description = str(_require("description")).strip()
    if not description:
        raise GenreEngineError(f"genre {name!r}.description is empty")

    risk_raw = _require("risk_profile")
    if not isinstance(risk_raw, dict):
        raise GenreEngineError(f"genre {name!r}.risk_profile must be a mapping")
    max_side_effects = str(risk_raw.get("max_side_effects", "")).strip()
    if max_side_effects not in _SIDE_EFFECT_TIERS:
        raise GenreEngineError(
            f"genre {name!r}.risk_profile.max_side_effects must be one of "
            f"{list(_SIDE_EFFECT_TIERS)}; got {max_side_effects!r}"
        )
    provider_constraint_raw = risk_raw.get("provider_constraint")
    provider_constraint: str | None
    if provider_constraint_raw is None:
        provider_constraint = None
    else:
        provider_constraint = str(provider_constraint_raw).strip()
        if provider_constraint not in _PROVIDER_CONSTRAINTS:
            raise GenreEngineError(
                f"genre {name!r}.risk_profile.provider_constraint must be one "
                f"of {sorted(_PROVIDER_CONSTRAINTS)} or omitted; got "
                f"{provider_constraint!r}"
            )

    # ADR-0027 §1 + §5 — memory_ceiling. Optional; defaults to "private"
    # (the strictest scope, safest fallback when YAML omits the field).
    # Validated against the four canonical scopes; an unknown value is
    # always a typo, never a forward-compat extension.
    memory_ceiling_raw = risk_raw.get("memory_ceiling")
    if memory_ceiling_raw is None:
        memory_ceiling = "private"
    else:
        memory_ceiling = str(memory_ceiling_raw).strip()
        if memory_ceiling not in _MEMORY_CEILINGS:
            raise GenreEngineError(
                f"genre {name!r}.risk_profile.memory_ceiling must be one of "
                f"{sorted(_MEMORY_CEILINGS)} or omitted; got "
                f"{memory_ceiling!r}"
            )

    risk_profile = RiskProfile(
        max_side_effects=max_side_effects,
        provider_constraint=provider_constraint,
        memory_ceiling=memory_ceiling,
    )

    default_kit_pattern = _require_str_list(entry, name, "default_kit_pattern")
    trait_emphasis = _require_str_list(entry, name, "trait_emphasis")
    spawn_compatibility = _require_str_list(entry, name, "spawn_compatibility")
    roles = _require_str_list(entry, name, "roles")

    if not roles:
        raise GenreEngineError(
            f"genre {name!r}.roles must contain at least one role "
            "(empty genres are not useful and likely a typo)"
        )
    if not spawn_compatibility:
        raise GenreEngineError(
            f"genre {name!r}.spawn_compatibility must contain at least one "
            "genre (a genre that can't spawn anything — including itself — "
            "is almost certainly a typo; list the genre's own name to allow "
            "self-spawning)"
        )

    memory_pattern = str(_require("memory_pattern")).strip()
    if not memory_pattern:
        raise GenreEngineError(
            f"genre {name!r}.memory_pattern is empty (use a placeholder like "
            "'short_retention' if ADR-0022 hasn't refined the values yet)"
        )

    return GenreDef(
        name=name,
        description=description,
        risk_profile=risk_profile,
        default_kit_pattern=tuple(default_kit_pattern),
        trait_emphasis=tuple(trait_emphasis),
        memory_pattern=memory_pattern,
        spawn_compatibility=tuple(spawn_compatibility),
        roles=tuple(roles),
    )


def _require_str_list(entry: dict, genre_name: str, field_name: str) -> list[str]:
    """Read a required list-of-strings field from a genre body."""
    if field_name not in entry:
        raise GenreEngineError(
            f"genre {genre_name!r} missing required field {field_name!r}"
        )
    raw = entry[field_name]
    if not isinstance(raw, list):
        raise GenreEngineError(
            f"genre {genre_name!r}.{field_name} must be a list, got "
            f"{type(raw).__name__}"
        )
    out: list[str] = []
    for item in raw:
        s = str(item).strip()
        if not s:
            raise GenreEngineError(
                f"genre {genre_name!r}.{field_name} contains an empty string"
            )
        out.append(s)
    return out


def empty_engine() -> GenreEngine:
    """Genre engine with no genres. Used as the lifespan fallback when
    ``genres.yaml`` is absent or malformed — keeps daemon endpoints up
    (genre-aware surfaces just report "no genres loaded")."""
    return GenreEngine(version="0", genres={}, role_to_genre={}, source_path=None)


def validate_against_trait_engine(
    genres_engine: "GenreEngine",
    trait_engine_roles: list[str],
) -> list[str]:
    """ADR-0021 invariant: every TraitEngine role is claimed by some genre.

    Returns a list of unclaimed role names. Empty list = compliant.

    Separate from :func:`load_genres` because the trait engine is a
    daemon-lifespan concern; the loader can be tested standalone. Daemon
    lifespan calls this AFTER both engines are loaded and surfaces any
    findings on /healthz's startup_diagnostics.
    """
    return [r for r in trait_engine_roles if r not in genres_engine.role_to_genre]


# ---------------------------------------------------------------------------
# T5 — kit-tier compatibility check
# ---------------------------------------------------------------------------
# Tier order matches tool_catalog.SIDE_EFFECT_VALUES. Index = strictness;
# higher index = "more side-effect-y." A tool whose tier index exceeds the
# genre's max_side_effects index violates the genre's risk profile.
_SIDE_EFFECTS_TIER_ORDER: tuple[str, ...] = (
    "read_only",
    "network",
    "filesystem",
    "external",
)


def _tier_index(side_effects: str) -> int:
    """Return the strictness index of a side_effects tier. Unknown tiers
    fall back to the strictest (external) so an unrecognized value never
    sneaks past a tier comparison."""
    try:
        return _SIDE_EFFECTS_TIER_ORDER.index(side_effects)
    except ValueError:
        return len(_SIDE_EFFECTS_TIER_ORDER) - 1


def kit_violations_for_genre(
    genre_def: GenreDef,
    tool_side_effects: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """ADR-0021 T5: return (tool_name, side_effects) pairs that exceed
    the genre's ``risk_profile.max_side_effects`` ceiling.

    Caller passes ``tool_side_effects`` as a list of ``(name, side_effects)``
    pairs (one per resolved tool). Returns an empty list when the kit is
    compatible. /birth and /spawn raise 400 when this returns anything.

    The check is intentionally one-direction: a tool MORE permissive than
    the genre's max is a violation; a tool LESS permissive (read_only on
    a network-tier genre) is fine. The genre's max is a ceiling.
    """
    ceiling = _tier_index(genre_def.risk_profile.max_side_effects)
    violations: list[tuple[str, str]] = []
    for name, se in tool_side_effects:
        if _tier_index(se) > ceiling:
            violations.append((name, se))
    return violations


# ---------------------------------------------------------------------------
# ADR-0033 + ADR-0027 §5 — memory ceiling enforcement
# ---------------------------------------------------------------------------
def _memory_tier_index(scope: str) -> int:
    """Return strictness index of a memory scope. Unknown → strictest
    (private = 0). Same fail-closed shape as ``_tier_index`` for
    ``side_effects``."""
    try:
        return _MEMORY_CEILING_TIERS.index(scope)
    except ValueError:
        return 0


def memory_scope_exceeds_ceiling(scope: str, ceiling: str) -> bool:
    """True iff ``scope`` is wider than ``ceiling``.

    Used at the memory write path: a Companion-genre agent (ceiling=private)
    that tries to write at scope=lineage triggers a refusal with this
    function returning True. Operator override path raises a separate
    audit event (``memory_scope_override``).

    Unknown scope OR unknown ceiling fail closed (treated as widest /
    strictest respectively) so a typo on either side never quietly
    permits a wider write than intended.
    """
    return _memory_tier_index(scope) > _memory_tier_index(ceiling)


# ---------------------------------------------------------------------------
# ADR-0033 A4 — per-genre approval policy graduation
# ---------------------------------------------------------------------------
# Rules baked directly: security tiers each have a different bar for
# what side_effects automatically elevate to "human approval required".
# Non-security genres are a no-op: the tool's own constitution config
# decides (existing ADR-0019 T3 behavior).
#
#   security_high → any side_effects beyond read_only requires approval
#                    (high tier assumes hostility; even network calls
#                     could exfiltrate, so they're gated)
#   security_mid  → filesystem / external require approval; network is
#                    OK because mid-tier investigators need DNS lookups,
#                    threat-intel queries, and baseline comparisons to
#                    work without a click on every call
#   security_low  → no elevation; tool config wins (low tier is bounded
#                    to read_only by its own genre risk_profile, so
#                    every call is already safe)
#
# Non-security genres pass through unchanged so this graduation has
# zero effect on the existing seven genres.
_GENRE_APPROVAL_RULES: dict[str, frozenset[str]] = {
    "security_high": frozenset({"network", "filesystem", "external"}),
    "security_mid":  frozenset({"filesystem", "external"}),
    "security_low":  frozenset(),
}


def genre_requires_approval(genre: str | None, side_effects: str) -> bool:
    """True iff the genre's approval policy elevates ``side_effects`` to
    "requires human approval" regardless of the tool's own config.

    The dispatcher consults this at the approval gate and ORs the
    result with ``resolved.constraints['requires_human_approval']``.
    Caller's responsibility to audit the elevation reason — the
    pending-approval ticket should record which gate fired.

    Genres outside the security family always return False so this
    helper is a no-op for non-security agents.
    """
    if not genre:
        return False
    rule = _GENRE_APPROVAL_RULES.get(genre.lower())
    if rule is None:
        return False
    return side_effects in rule
