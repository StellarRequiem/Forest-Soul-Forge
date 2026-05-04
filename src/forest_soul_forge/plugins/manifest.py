"""Pydantic schema for ``plugin.yaml`` per ADR-0043 §Architecture.

Schema version 1 — the v0.5 minimum. Future bumps go through an
ADR amendment so plugin authors aren't surprised by silent shape
changes.
"""
from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from forest_soul_forge.plugins.errors import PluginValidationError


class PluginType(str, Enum):
    """ADR-0043 plugin type taxonomy. v0.5 implements ``mcp_server``
    end-to-end; the others are reserved namespaces (the manifest
    parses, but the runtime won't register them until follow-up
    ADRs land)."""

    MCP_SERVER = "mcp_server"
    TOOL = "tool"
    SKILL = "skill"
    GENRE = "genre"


class EntryPointType(str, Enum):
    """Wire protocol the daemon uses to talk to the plugin process."""

    STDIO = "stdio"
    HTTP = "http"


class EntryPoint(BaseModel):
    """How to launch + verify the plugin's runtime."""

    model_config = ConfigDict(extra="forbid")

    type: EntryPointType = Field(
        ...,
        description="Wire protocol: stdio (subprocess pipes) or http",
    )
    command: str = Field(
        ...,
        min_length=1,
        description=(
            "Path to the executable / module. Relative paths resolve "
            "to the plugin's installed directory."
        ),
    )
    args: list[str] = Field(default_factory=list)
    sha256: str = Field(
        ...,
        description=(
            "Expected SHA-256 of the launched binary. Verified before "
            "every spawn — typosquat / supply-chain-swap defense per "
            "ADR-003X Phase C4 §threat-model addendum."
        ),
    )

    @field_validator("sha256")
    @classmethod
    def _hex_sha256(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError(
                "sha256 must be exactly 64 lowercase hex chars"
            )
        return v


class RequiredSecret(BaseModel):
    """One secret the operator gets prompted for on install."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    env_var: str = Field(
        ...,
        min_length=1,
        max_length=80,
        description=(
            "Environment variable name the daemon sets when launching "
            "the plugin. Must be SHOUTY_SNAKE_CASE."
        ),
    )

    @field_validator("env_var")
    @classmethod
    def _shouty_snake(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", v):
            raise ValueError("env_var must be UPPER_SNAKE_CASE")
        return v


class SideEffects(str, Enum):
    """Mirror of the ADR-0019 side_effects classification."""

    READ_ONLY = "read_only"
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    EXTERNAL = "external"


# Plugin name regex: lowercase letters, digits, hyphens; must start
# with a letter; 1-80 chars. Matches the directory-name constraint
# operators see in their filesystem.
_PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,79}$")


class PluginManifest(BaseModel):
    """``plugin.yaml`` v1.

    Pydantic enforces structural correctness; semantic correctness
    (e.g., the binary at ``entry_point.command`` actually matching
    the declared sha256) is :class:`PluginRepository`'s job at
    install / verify time.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(
        ...,
        ge=1,
        le=1,
        description="Plugin manifest schema version. v0.5 supports v1 only.",
    )

    # Identity
    name: str = Field(
        ...,
        description=(
            "Unique key. Must match the install-dir name. Lowercase "
            "letters + digits + hyphens; starts with a letter."
        ),
    )
    display_name: str = Field("", description="Human-readable name")
    version: str = Field(
        ...,
        min_length=1,
        max_length=40,
        description="Plugin's own version (semver-ish, not enforced)",
    )
    author: str = ""
    homepage: str = ""
    license: str = ""

    # Type
    type: PluginType

    # What it provides
    capabilities: list[str] = Field(
        default_factory=list,
        description=(
            "Forest tool keys this plugin contributes to the catalog. "
            "Each key gates governance independently."
        ),
    )

    # Governance
    side_effects: SideEffects
    requires_human_approval: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Per-tool approval-gate override map. Keys must be tool "
            "names from `capabilities` (without the namespace prefix). "
            "Tools not listed default to gating per the side_effects "
            "default in ADR-0019."
        ),
    )

    # Runtime
    entry_point: EntryPoint

    # Secrets
    required_secrets: list[RequiredSecret] = Field(default_factory=list)

    # Optional registry signature (T5)
    verified_at: str = ""
    verified_by_sha256: str = ""

    @field_validator("name")
    @classmethod
    def _plugin_name_shape(cls, v: str) -> str:
        if not _PLUGIN_NAME_RE.fullmatch(v):
            raise ValueError(
                "name must be lowercase letters/digits/hyphens, "
                "1-80 chars, starting with a letter"
            )
        return v

    @field_validator("verified_by_sha256")
    @classmethod
    def _hex_sha256_optional(cls, v: str) -> str:
        if not v:
            return ""
        v = v.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError(
                "verified_by_sha256 must be 64 lowercase hex chars or empty"
            )
        return v

    # ---- helpers ------------------------------------------------------

    def display_label(self) -> str:
        """Pretty label for CLI output. Falls back to name if
        display_name is empty."""
        return self.display_name or self.name


def load_manifest(path: Path) -> PluginManifest:
    """Read + validate a plugin.yaml from disk.

    Raises :class:`forest_soul_forge.plugins.errors.PluginValidationError`
    on any structural failure (file missing, malformed YAML,
    schema violation). Caller is expected to convert that into the
    appropriate user-facing error.
    """
    if not path.exists():
        raise PluginValidationError(f"plugin.yaml not found: {path}")
    try:
        import yaml as _yaml
        raw = _yaml.safe_load(path.read_text())
    except Exception as e:
        raise PluginValidationError(
            f"plugin.yaml YAML parse failed at {path}: {e}"
        ) from e
    if not isinstance(raw, dict):
        raise PluginValidationError(
            f"plugin.yaml must be a YAML mapping at the top level, got "
            f"{type(raw).__name__} at {path}"
        )
    try:
        return PluginManifest.model_validate(raw)
    except Exception as e:
        raise PluginValidationError(
            f"plugin.yaml schema validation failed at {path}:\n{e}"
        ) from e
