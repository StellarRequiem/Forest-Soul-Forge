"""Tool catalog + registered-tools + per-agent resolved kit read schemas.

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


class ToolDefOut(BaseModel):
    """One catalog entry as exposed to the frontend.

    Mirrors :class:`forest_soul_forge.core.tool_catalog.ToolDef` minus
    ``input_schema`` (heavy, only needed at execution time — the UI
    works from the description and side_effects).
    """

    name: str
    version: str
    description: str
    side_effects: str
    archetype_tags: list[str] = Field(default_factory=list)

class ArchetypeBundleOut(BaseModel):
    """A role's standard kit. ``standard_tools`` are bare {name, version}
    refs the frontend can compare against the resolved kit to identify
    which entries are archetype-defaults vs. user-added."""

    role: str
    standard_tools: list[ToolRefIn]

class ToolCatalogOut(BaseModel):
    """Full catalog snapshot served at GET /tools/catalog.

    Read-only; the catalog is loaded once at lifespan startup and held on
    ``app.state``. ``version`` is the catalog file's version (advanced
    when the YAML changes), distinct from each tool's own version.
    """

    version: str
    tools: list[ToolDefOut]
    archetypes: list[ArchetypeBundleOut]

class RegisteredToolOut(BaseModel):
    """One row from ``GET /tools/registered`` — the live runtime view.

    Distinct from ``ToolDefOut`` because the catalog YAML and the live
    registry can disagree (a forged plugin loaded post-edit, a built-in
    that hasn't been added to the YAML, etc.). The frontend Tools tab
    renders from this — what the dispatcher will actually see when it
    looks up a tool key.

    ``source`` is one of:
      ``builtin``  — registered by ``register_builtins()`` at lifespan
      ``plugin``   — loaded from ``settings.plugins_dir`` by
                     ``plugin_loader.load_plugins()``
      ``unknown``  — registered but not classifiable (shouldn't happen
                     in v1; defensive)

    ``in_catalog`` reflects whether the catalog YAML also lists the
    same name+version. False is benign for plugins (the catalog is a
    static file; plugins augment in memory) but worth surfacing so an
    operator can spot a typo'd plugin name.
    """

    name: str
    version: str
    side_effects: str
    source: str
    in_catalog: bool = True
    description: str | None = None
    archetype_tags: list[str] = Field(default_factory=list)

class RegisteredToolsOut(BaseModel):
    """Response for ``GET /tools/registered``."""

    count: int
    tools: list[RegisteredToolOut]

class ResolvedToolOut(BaseModel):
    """One tool in a role's resolved kit, with policy constraints applied.

    Served by GET /tools/kit/{role} and embedded in PreviewResponse.
    Mirrors the per-tool record that ends up in constitution.yaml's
    ``tools`` block — same name/version/side_effects, same constraint
    dict, same applied_rules — plus the description joined from the
    catalog so the UI doesn't need a second lookup.
    """

    name: str
    version: str
    description: str
    side_effects: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    applied_rules: list[str] = Field(default_factory=list)

class ResolvedKitOut(BaseModel):
    """Response for GET /tools/kit/{role}.

    Includes the role echo and the catalog version so the frontend can
    invalidate cached kits when the underlying catalog changes.
    """

    role: str
    catalog_version: str
    tools: list[ResolvedToolOut]
