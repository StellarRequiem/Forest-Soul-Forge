"""Plugin protocol — ADR-0043 MCP-first plugin system.

Burst 104 ships T2: directory layout, manifest schema, repository
operations (filesystem-only), and the ``fsf plugin`` CLI subcommands.
No daemon-side wiring yet — that's T3 (Burst 105).

Public surface:

* :class:`PluginManifest` — Pydantic model for ``plugin.yaml``
* :class:`PluginRepository` — filesystem operations (list, install,
  uninstall, enable, disable)
* :func:`default_plugin_root` — resolves ``~/.forest/plugins/``
  (or ``$FSF_PLUGIN_ROOT``) per the ADR's directory layout
* :class:`PluginError` and friends — typed exceptions that the CLI
  maps to non-zero exit codes
"""
from forest_soul_forge.plugins.errors import (
    PluginAlreadyInstalled,
    PluginError,
    PluginNotFound,
    PluginValidationError,
)
from forest_soul_forge.plugins.manifest import PluginManifest, PluginType
from forest_soul_forge.plugins.paths import (
    default_plugin_root,
    plugin_directories,
)
from forest_soul_forge.plugins.repository import (
    PluginInfo,
    PluginRepository,
    PluginState,
)

__all__ = [
    "PluginAlreadyInstalled",
    "PluginError",
    "PluginInfo",
    "PluginManifest",
    "PluginNotFound",
    "PluginRepository",
    "PluginState",
    "PluginType",
    "PluginValidationError",
    "default_plugin_root",
    "plugin_directories",
]
