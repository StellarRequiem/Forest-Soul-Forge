"""Typed exceptions for the plugin protocol.

ADR-0043 T2 (Burst 104). The CLI maps each one to a different
exit code so scripted operators can branch on outcome:

  PluginNotFound          → exit 4
  PluginAlreadyInstalled  → exit 5
  PluginValidationError   → exit 6
  PluginError             → exit 7 (catch-all)

Daemon-side code (T3+) maps the same exceptions to HTTP status
codes — typically 404 / 409 / 422 / 500 respectively.
"""
from __future__ import annotations


class PluginError(Exception):
    """Base for all plugin-protocol errors."""


class PluginNotFound(PluginError):
    """A plugin name doesn't resolve to an installed/disabled plugin."""


class PluginAlreadyInstalled(PluginError):
    """An install was attempted but the plugin already exists at the
    target path. Operator can pass ``--force`` to overwrite."""


class PluginValidationError(PluginError):
    """The plugin's ``plugin.yaml`` failed schema validation. The
    message includes the Pydantic error report (typically multi-line);
    callers should print it verbatim."""
