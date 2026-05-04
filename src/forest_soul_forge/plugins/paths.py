"""Standard plugin-directory locations per ADR-0043 §Architecture.

Layout (re-stated from the ADR for code-side reference):

    ~/.forest/                              (or $FSF_PLUGIN_ROOT)
    ├── plugins/
    │   ├── installed/                      # active plugins
    │   ├── disabled/                       # operator-paused (manifest only)
    │   ├── registry-cache.json             # last-fetched plugin catalog
    │   └── secrets/                        # per-plugin secret store

The root resolves via, in priority order:

1. ``$FSF_PLUGIN_ROOT`` if set (test isolation; advanced operators
   pinning a non-default location)
2. ``~/.forest/plugins`` (the documented v0.5 default)

Tests pass an explicit ``root`` to :class:`PluginRepository` and
never touch the env var so they don't conflict with each other or
with the operator's real plugin store.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PluginDirectories:
    """Resolved set of plugin-protocol paths."""

    root: Path
    installed: Path
    disabled: Path
    secrets: Path
    registry_cache: Path


def default_plugin_root() -> Path:
    """Return the plugin root path per the ADR-0043 priority order.

    Does NOT create the directory — :class:`PluginRepository` is
    responsible for materializing it on demand. Pure resolver.
    """
    override = os.environ.get("FSF_PLUGIN_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".forest" / "plugins").resolve()


def plugin_directories(root: Path | None = None) -> PluginDirectories:
    """Compute the standard subdir layout under a plugin root.

    Pass ``root`` for tests (a tmp_path); omit for the operator-
    facing default.
    """
    base = root if root is not None else default_plugin_root()
    return PluginDirectories(
        root=base,
        installed=base / "installed",
        disabled=base / "disabled",
        secrets=base / "secrets",
        registry_cache=base / "registry-cache.json",
    )
