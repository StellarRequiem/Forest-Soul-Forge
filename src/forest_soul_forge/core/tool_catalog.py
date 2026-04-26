"""Tool catalog — declarative descriptors for the MCP-style tool surface
each agent can be birthed with. See ADR-0018 for the design rationale.

The catalog YAML at ``config/tool_catalog.yaml`` is the canonical source.
Each tool is keyed by ``{name}.{version}`` (e.g. ``packet_query.v1``).
soul.md frontmatter pins ``name`` + ``version`` references rather than
the full schema, so editing v1 in this file would silently change the
contract for every committed agent that references it. Catalog
integrity is enforced at load time:

* Every tool entry has a unique ``{name}.{version}`` composite key.
* Every entry has the required descriptor fields (name, version,
  description, input_schema, side_effects, archetype_tags).
* ``side_effects`` is one of the recognized enum values — the
  constitution constraint policy keys off this so an unrecognized
  value would silently bypass safety checks.
* Every name in any archetype's ``standard_tools`` list resolves to a
  real ``{name}.{version}`` entry in ``tools``.

The catalog does NOT validate ``input_schema`` JSONSchema bodies in v1
— that's a step we add when (a) we have a JSONSchema validator
dependency baked in, or (b) malformed schemas start causing real
problems. For now, structural shape + enum membership are enough to
catch the common typos, and tool authors can paste their schema into
any online validator before committing.

Loaded once per daemon process during lifespan startup; held on
``app.state.tool_catalog``. Reads only — never mutated at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


SIDE_EFFECT_VALUES = frozenset({"read_only", "network", "filesystem", "external"})


class ToolCatalogError(Exception):
    """Raised when the catalog YAML is malformed or violates an integrity rule."""


@dataclass(frozen=True)
class ToolDef:
    """One versioned tool entry. Frozen because the catalog is immutable
    once loaded — runtime mutation would break the audit trail (an
    agent's referenced version must always resolve to the same bytes).
    """

    name: str
    version: str
    description: str
    input_schema: dict
    side_effects: str
    archetype_tags: tuple[str, ...]

    @property
    def key(self) -> str:
        """Composite key used in the catalog's ``tools`` mapping."""
        return f"{self.name}.v{self.version}"


@dataclass(frozen=True)
class ToolRef:
    """A pointer to a specific tool version. Used in soul.md frontmatter,
    in BirthRequest.tools_add, and as the resolver's output type."""

    name: str
    version: str

    @property
    def key(self) -> str:
        return f"{self.name}.v{self.version}"

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> "ToolRef":
        return cls(name=str(d["name"]), version=str(d["version"]))

    @classmethod
    def from_key(cls, key: str) -> "ToolRef":
        """Parse a 'name.vN' composite key. Lenient: accepts 'name.v1' or
        'name.1'."""
        if "." not in key:
            raise ToolCatalogError(
                f"tool ref key missing version separator: {key!r}"
            )
        name, _, version_part = key.rpartition(".")
        version = version_part[1:] if version_part.startswith("v") else version_part
        if not name or not version:
            raise ToolCatalogError(f"malformed tool ref key: {key!r}")
        return cls(name=name, version=version)


@dataclass(frozen=True)
class ArchetypeBundle:
    """A role's standard tool kit. ``standard_tools`` are ToolRefs that
    must all resolve to entries in the catalog's ``tools`` mapping."""

    role: str
    standard_tools: tuple[ToolRef, ...]


@dataclass(frozen=True)
class ToolCatalog:
    """The whole catalog after load + integrity check. Held on app.state.

    ``genre_default_tools`` (ADR-0021 T4) is the per-genre fallback kit:
    a mapping from genre name to a tuple of ToolRefs. Resolved as the
    LAST layer in :meth:`resolve_kit` when a role has no archetype
    standard_tools entry. Empty when the catalog has no genre fallback
    section — preserves pre-T4 behavior bit-for-bit.
    """

    version: str
    tools: dict[str, ToolDef]   # keyed by ToolDef.key
    archetypes: dict[str, ArchetypeBundle]   # keyed by role name
    genre_default_tools: dict[str, tuple[ToolRef, ...]] = field(default_factory=dict)
    source_path: Path | None = None

    def get_tool(self, ref: ToolRef) -> ToolDef:
        """Resolve a ToolRef to its ToolDef. Raises if missing."""
        td = self.tools.get(ref.key)
        if td is None:
            raise ToolCatalogError(
                f"tool not in catalog: {ref.key} "
                f"(known: {sorted(self.tools.keys())})"
            )
        return td

    def has_tool_name(self, name: str) -> bool:
        """True if ANY version of `name` exists in the catalog."""
        return any(td.name == name for td in self.tools.values())

    def resolve_kit(
        self,
        role: str,
        *,
        tools_add: list[ToolRef] | None = None,
        tools_remove: list[str] | None = None,
        genre: str | None = None,
    ) -> tuple[ToolRef, ...]:
        """Compose the final tool kit for an agent of `role` at birth.

        Algorithm (per ADR-0018 + ADR-0021 T4):

        1. Start with the archetype's standard_tools (or, if the role
           has no archetype entry, fall back to the genre's
           ``genre_default_tools`` when the optional ``genre`` arg is
           supplied — T4 fallback).
        2. Drop any whose name is in ``tools_remove`` (any version match).
        3. Append the ``tools_add`` entries.
        4. Validate every entry resolves to a real catalog tool.
        5. Deduplicate by name (later wins) — lets a caller override
           ``packet_query.v1`` from the standard kit with ``packet_query.v2``
           via tools_add without first removing v1.

        Returns a tuple (frozen) of ToolRefs in deterministic order:
        kept-standard-tools first, then added tools in the order
        supplied.

        ``genre`` is OPTIONAL and defaults to None for back-compat — the
        existing /birth and /spawn paths that pass tools_add /
        tools_remove without a genre still work the same way they did
        pre-T4.  When supplied, the genre's fallback kit is ONLY used
        when the role has no archetype entry. Roles with an archetype
        kit are unchanged.
        """
        tools_add = tools_add or []
        tools_remove = tools_remove or []
        remove_names = set(tools_remove)

        # Validate every add entry exists in the catalog before doing
        # anything else — fail fast on a typo in the request.
        for ta in tools_add:
            if ta.key not in self.tools:
                raise ToolCatalogError(
                    f"tools_add references unknown tool: {ta.key}"
                )

        # Validate every remove name corresponds to SOMETHING — silently
        # ignoring an unknown remove name lets typos slide through.
        for rn in tools_remove:
            if not self.has_tool_name(rn):
                raise ToolCatalogError(
                    f"tools_remove references unknown tool name: {rn}"
                )

        bundle = self.archetypes.get(role)
        kept: list[ToolRef] = []
        if bundle is not None:
            for ref in bundle.standard_tools:
                if ref.name in remove_names:
                    continue
                kept.append(ref)
        elif genre is not None:
            # Per-genre fallback (ADR-0021 T4). Only fires when the role
            # has no archetype entry — preserves byte-for-byte behavior
            # for agents whose role IS in `archetypes`.
            for ref in self.genre_default_tools.get(genre, ()):
                if ref.name in remove_names:
                    continue
                kept.append(ref)

        # Append adds, deduplicating by name (last wins).
        by_name: dict[str, ToolRef] = {}
        for ref in kept:
            by_name[ref.name] = ref
        for ref in tools_add:
            by_name[ref.name] = ref

        # Preserve stable order: kept first (in archetype order minus
        # removed, but possibly upgraded by tools_add overrides), then
        # purely-new adds in the order they were specified.
        ordered: list[ToolRef] = []
        seen: set[str] = set()
        # First pass — keep archetype order, but use the (possibly
        # upgraded) version from by_name.
        for ref in kept:
            if ref.name in seen:
                continue
            ordered.append(by_name[ref.name])
            seen.add(ref.name)
        # Second pass — purely-new adds (not in kept).
        for ref in tools_add:
            if ref.name in seen:
                continue
            ordered.append(by_name[ref.name])
            seen.add(ref.name)

        return tuple(ordered)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_catalog(path: Path | str) -> ToolCatalog:
    """Read + validate the catalog YAML. Raises ToolCatalogError on any
    integrity violation; the daemon's lifespan logs and falls back to
    an empty catalog so /birth with no tool overrides still works.
    """
    p = Path(path)
    if not p.exists():
        raise ToolCatalogError(f"tool catalog not found at {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ToolCatalogError(
            f"tool catalog root must be a mapping, got {type(raw).__name__}"
        )

    version_raw = raw.get("version")
    if version_raw is None:
        raise ToolCatalogError("tool catalog 'version' is required")
    version = str(version_raw).strip()
    if not version:
        raise ToolCatalogError("tool catalog 'version' must not be empty")

    tools_raw = raw.get("tools") or {}
    if not isinstance(tools_raw, dict):
        raise ToolCatalogError("'tools' must be a mapping of {name}.{version} → entry")

    tools: dict[str, ToolDef] = {}
    for key, entry in tools_raw.items():
        td = _parse_tool_entry(key, entry)
        if td.key != key:
            raise ToolCatalogError(
                f"tool entry key {key!r} disagrees with name+version "
                f"({td.name!r}, v{td.version!r}); expected key {td.key!r}"
            )
        if td.key in tools:
            raise ToolCatalogError(f"duplicate tool entry: {td.key}")
        tools[td.key] = td

    archetypes_raw = raw.get("archetypes") or {}
    if not isinstance(archetypes_raw, dict):
        raise ToolCatalogError("'archetypes' must be a mapping of role → bundle")

    archetypes: dict[str, ArchetypeBundle] = {}
    for role, body in archetypes_raw.items():
        if not isinstance(body, dict):
            raise ToolCatalogError(
                f"archetype entry for {role!r} must be a mapping"
            )
        std = body.get("standard_tools") or []
        if not isinstance(std, list):
            raise ToolCatalogError(
                f"archetype {role!r}.standard_tools must be a list"
            )
        refs: list[ToolRef] = []
        for raw_ref in std:
            ref = ToolRef.from_key(str(raw_ref))
            if ref.key not in tools:
                raise ToolCatalogError(
                    f"archetype {role!r} references unknown tool: {ref.key}"
                )
            refs.append(ref)
        archetypes[str(role)] = ArchetypeBundle(
            role=str(role),
            standard_tools=tuple(refs),
        )

    # Per-genre fallback kits (ADR-0021 T4). Optional block — pre-T4
    # catalogs lack it entirely, in which case the empty dict preserves
    # the legacy "no archetype kit → empty kit" behavior. When present,
    # every tool ref must resolve to a real catalog entry.
    genre_default_tools_raw = raw.get("genre_default_tools") or {}
    if not isinstance(genre_default_tools_raw, dict):
        raise ToolCatalogError(
            "'genre_default_tools' must be a mapping of genre name -> list"
        )
    genre_default_tools: dict[str, tuple[ToolRef, ...]] = {}
    for genre_name, items in genre_default_tools_raw.items():
        if not isinstance(items, list):
            raise ToolCatalogError(
                f"genre_default_tools[{genre_name!r}] must be a list of tool refs"
            )
        refs: list[ToolRef] = []
        for raw_ref in items:
            ref = ToolRef.from_key(str(raw_ref))
            if ref.key not in tools:
                raise ToolCatalogError(
                    f"genre_default_tools[{genre_name!r}] references "
                    f"unknown tool: {ref.key}"
                )
            refs.append(ref)
        genre_default_tools[str(genre_name)] = tuple(refs)

    return ToolCatalog(
        version=version,
        tools=tools,
        archetypes=archetypes,
        genre_default_tools=genre_default_tools,
        source_path=p,
    )


def _parse_tool_entry(key: str, entry: Any) -> ToolDef:
    """Parse + validate a single tool entry. Raises on missing required
    fields or invalid enum values."""
    if not isinstance(entry, dict):
        raise ToolCatalogError(f"tool entry {key!r} must be a mapping")

    def require(field_name: str) -> Any:
        if field_name not in entry:
            raise ToolCatalogError(
                f"tool entry {key!r} missing required field {field_name!r}"
            )
        return entry[field_name]

    name = str(require("name")).strip()
    if not name:
        raise ToolCatalogError(f"tool entry {key!r} has empty name")
    version = str(require("version")).strip()
    if not version:
        raise ToolCatalogError(f"tool entry {key!r} has empty version")
    description = str(require("description")).strip()
    if not description:
        raise ToolCatalogError(f"tool entry {key!r} has empty description")

    input_schema = require("input_schema")
    if not isinstance(input_schema, dict):
        raise ToolCatalogError(
            f"tool entry {key!r}.input_schema must be a mapping"
        )

    side_effects = str(require("side_effects")).strip()
    if side_effects not in SIDE_EFFECT_VALUES:
        raise ToolCatalogError(
            f"tool entry {key!r}.side_effects must be one of "
            f"{sorted(SIDE_EFFECT_VALUES)}; got {side_effects!r}"
        )

    archetype_tags_raw = entry.get("archetype_tags") or []
    if not isinstance(archetype_tags_raw, list):
        raise ToolCatalogError(
            f"tool entry {key!r}.archetype_tags must be a list"
        )
    archetype_tags = tuple(str(t) for t in archetype_tags_raw)

    return ToolDef(
        name=name,
        version=version,
        description=description,
        input_schema=dict(input_schema),
        side_effects=side_effects,
        archetype_tags=archetype_tags,
    )


def empty_catalog() -> ToolCatalog:
    """Catalog with no tools and no archetypes. Used as the lifespan
    fallback when the catalog file is absent or malformed — keeps /birth
    working for callers who don't need a kit."""
    return ToolCatalog(version="0", tools={}, archetypes={}, source_path=None)
