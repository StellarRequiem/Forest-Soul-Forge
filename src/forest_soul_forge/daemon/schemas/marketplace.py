"""ADR-0055 M1 (Burst 184) — marketplace endpoint Pydantic schemas.

Mirror of the registry-entry shape documented in
``forest-marketplace/docs/manifest-schema.md``. The kernel doesn't
own the schema (the marketplace repo does); we duplicate the
field set here as a defensive read model so a future schema bump
in the marketplace repo doesn't crash the kernel — unknown extra
fields are silently dropped at deserialization time.

Future schema bumps (the marketplace adds new optional fields)
land here as additive optional Pydantic fields. Field renames or
required-field additions in the marketplace are breaking and
require a paired kernel release; the marketplace's
``schema_version`` field tracks this.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# Per ADR-0055 Decision 3, the schema's enum for side_effects
# matches the kernel's tool-side enum (read_only / network /
# filesystem / external). The marketplace UI sorts on this field
# so the operator can filter by capability tier.
SideEffectTier = Literal["read_only", "network", "filesystem", "external"]


class MarketplaceContributesTool(BaseModel):
    """One tool contributed by a plugin entry. Mirrors the
    Forest-Soul-Forge tool_catalog.yaml entry shape so the
    marketplace UI can render a per-tool subtable identical to
    what the operator sees in the Tools tab once installed."""

    name: str
    version: str
    side_effects: SideEffectTier


class MarketplaceContributes(BaseModel):
    """Capabilities a plugin contributes. Each list defaults to
    empty so an entry that contributes only tools (no skills, no
    mcp servers) doesn't have to write three fields."""

    tools: list[MarketplaceContributesTool] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)


class MarketplaceReview(BaseModel):
    """One per-entry review record. Multiple reviews per entry are
    allowed (different reviewers / re-review after version bump)."""

    reviewer: str
    date: str   # ISO date — kept as string so the kernel doesn't
                # fight YAML's date parsing on entries with
                # variable date precision.
    verdict: Literal["approved", "rejected", "conditional"]
    audit_url: str | None = None
    notes: str | None = None


class MarketplaceEntryOut(BaseModel):
    """One plugin entry as the kernel returns it to the frontend
    Browse pane. Matches the canonical schema from
    forest-marketplace/registry/entries/<id>.yaml + an extra
    ``source_registry`` field added by the kernel during
    aggregation so the UI can show the operator which configured
    registry contributed this entry."""

    # Identity
    id: str
    name: str
    version: str
    author: str

    # Distribution
    source_url: str
    download_url: str
    download_sha256: str
    manifest_signature: str | None = None  # null until M6 lands

    # Description
    description: str
    permissions_summary: str

    # Capabilities
    contributes: MarketplaceContributes
    archetype_tags: list[str] = Field(default_factory=list)
    highest_side_effect_tier: SideEffectTier
    required_secrets: list[str] = Field(default_factory=list)
    minimum_kernel_version: str | None = None

    # Provenance
    reviewed_by: list[MarketplaceReview] = Field(default_factory=list)

    # Aggregation metadata — added by the kernel at fetch time.
    # NOT present in the source YAML.
    source_registry: str
    trusted: bool = False  # M6 will compute this from
                           # manifest_signature + trusted-keys
                           # check; M1-M5 reports False for all
                           # (signing not yet shipped).


class MarketplaceIndexOut(BaseModel):
    """The full index returned by ``GET /marketplace/index``.

    ``stale`` is set when one or more configured registries failed
    to refresh and the kernel served the last-known-good. The
    frontend should surface this as a soft warning ('marketplace
    last refreshed N minutes ago') rather than blocking the
    Browse pane.

    ``failed_registries`` lists URLs that couldn't be reached on
    this fetch attempt — empty when everything succeeded.
    Operators inspecting registry health (or scripts driving the
    daemon) can act on this without parsing free-text errors.
    """

    schema_version: int = 1
    entries: list[MarketplaceEntryOut]
    fetched_at: str           # ISO timestamp of the most recent
                              # successful fetch across registries.
    cache_ttl_s: int          # the cache TTL in effect — UI can
                              # show "next refresh in N seconds."
    stale: bool = False
    failed_registries: list[str] = Field(default_factory=list)
    configured_registries: list[str] = Field(default_factory=list)
