"""Daemon-side plugin runtime — ADR-0043 T3 (Burst 105).

Bridges :class:`forest_soul_forge.plugins.PluginRepository` (the
on-disk truth) into the running daemon's process state.

What this module owns:

* :class:`PluginRuntime` — the long-lived in-process view of
  installed/disabled plugins. Lifespan instantiates it; HTTP
  routes read + mutate it; hot-reload diffs it against the
  filesystem.
* Conversion of ``type=mcp_server`` manifests into the
  ``mcp_servers.yaml``-shaped dict the existing
  :mod:`mcp_call` tool consumes. T3 builds the conversion;
  T4 (Burst 106) wires the result into the live dispatcher
  via constraint injection. For now the conversion is exposed
  as an introspection helper so tests can verify the bridge is
  shaped correctly before the dispatcher path lands.

What this module does NOT own (yet):

* Audit-chain emit. The 6 ``plugin_*`` events listed in
  ADR-0043 §"Audit events" emit from :class:`PluginRuntime`
  callsites in T4.
* Constraint injection into :class:`ToolDispatcher`. The
  dispatcher reads MCP server config from
  ``ctx.constraints["mcp_registry"]``; T4 will populate that
  from :meth:`PluginRuntime.mcp_servers_view` when constructing
  the dispatch context.

Design — single-writer SQLite discipline (ADR-0001) extends to
the plugin runtime: every mutation grabs ``app.state.write_lock``
before touching the filesystem. Reads don't need the lock —
:class:`PluginRepository.list` is a directory scan; momentary
inconsistency between ``installed/`` and ``disabled/`` mid-rename
is acceptable for read endpoints (worst case: a plugin briefly
shows up in both states; the next refresh corrects it).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from forest_soul_forge.plugins import (
    PluginInfo,
    PluginRepository,
    PluginState,
)
from forest_soul_forge.plugins.errors import PluginError, PluginNotFound
from forest_soul_forge.plugins.manifest import PluginType, SideEffects

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reload diff record — returned from PluginRuntime.reload() so /plugins/reload
# can show the operator what changed.
# ---------------------------------------------------------------------------
class ReloadResult:
    """Outcome of a hot-reload pass.

    Reported by :meth:`PluginRuntime.reload` so the
    ``POST /plugins/reload`` endpoint can return a structured
    summary the operator (or a script) can act on.
    """

    __slots__ = ("added", "removed", "updated", "errors")

    def __init__(self) -> None:
        self.added: list[str] = []
        self.removed: list[str] = []
        self.updated: list[str] = []
        # Plugin name → reason. Plugins that failed verification or
        # had a malformed manifest at reload time are reported here
        # rather than silently dropped.
        self.errors: dict[str, str] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": list(self.added),
            "removed": list(self.removed),
            "updated": list(self.updated),
            "errors": dict(self.errors),
        }

    @property
    def is_clean(self) -> bool:
        """True iff the reload made no changes AND surfaced no errors."""
        return (
            not self.added
            and not self.removed
            and not self.updated
            and not self.errors
        )


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
class PluginRuntime:
    """Long-lived in-process view of installed plugins.

    One instance per daemon lifespan. The HTTP routes look it up
    from ``app.state.plugin_runtime``.

    Mutating operations (install / uninstall / enable / disable /
    reload) acquire ``app.state.write_lock`` at the route level
    before calling through. Read operations are lock-free and may
    observe a snapshot that's a few microseconds out of date if a
    write is concurrent.
    """

    def __init__(
        self,
        repository: PluginRepository,
        audit_chain: Any = None,
    ) -> None:
        """Construct the runtime.

        ``audit_chain`` is optional. When present (lifespan wires the
        daemon's chain), every lifecycle transition emits one of the
        ADR-0043 §"Audit events" entries — see :meth:`_emit_audit`.
        When None (tests / chain-load failure), the runtime still
        functions; chain coverage just goes silent. Mirrors the
        scheduler's audit-emit policy from Burst 89.
        """
        self._repo = repository
        self._audit = audit_chain
        # name → PluginInfo for currently-INSTALLED (active) plugins
        self._active: dict[str, PluginInfo] = {}
        # name → PluginInfo for DISABLED plugins. Surfaced via /plugins
        # so operators can see what's paused.
        self._disabled: dict[str, PluginInfo] = {}
        # Snapshot lock — short-held in mutating methods so the
        # internal dicts never get observed mid-update by a concurrent
        # reader. Distinct from app.state.write_lock (which serializes
        # against the wider daemon, including the audit chain).
        self._snapshot_lock = threading.Lock()

    @property
    def repository(self) -> PluginRepository:
        return self._repo

    # ---- read ----------------------------------------------------------

    def active(self) -> list[PluginInfo]:
        """All plugins currently in the installed/ state. The order
        is stable (sorted by name) so callers — especially the
        frontend — can rely on consistent presentation."""
        with self._snapshot_lock:
            return sorted(self._active.values(), key=lambda p: p.name)

    def disabled(self) -> list[PluginInfo]:
        """All plugins currently in the disabled/ state."""
        with self._snapshot_lock:
            return sorted(self._disabled.values(), key=lambda p: p.name)

    def all(self) -> list[PluginInfo]:
        """Active + disabled, sorted by name."""
        with self._snapshot_lock:
            combined: list[PluginInfo] = []
            combined.extend(self._active.values())
            combined.extend(self._disabled.values())
        return sorted(combined, key=lambda p: p.name)

    def get(self, name: str) -> PluginInfo:
        """Look up one plugin by name. Raises PluginNotFound."""
        with self._snapshot_lock:
            if name in self._active:
                return self._active[name]
            if name in self._disabled:
                return self._disabled[name]
        raise PluginNotFound(f"no plugin named {name!r}")

    # ---- bridge to mcp_call.v1 ----------------------------------------

    def mcp_servers_view(self) -> dict[str, dict[str, Any]]:
        """Convert active mcp_server-type plugins to the dict shape
        ``mcp_call.v1`` already consumes (the same shape
        ``config/mcp_servers.yaml`` produces).

        T3 ships the conversion + introspection. T4 wires the result
        into the dispatch context so live calls actually use
        plugin-registered servers.

        Conversion mapping (per ADR-0043 §plugin.yaml schema):

        * ``entry_point.command`` → ``url`` field (with ``stdio:`` prefix
          if not already URL-shaped). MCP's existing convention is
          ``stdio:./path`` for subprocess servers.
        * ``entry_point.sha256`` → ``sha256`` (pin verification stays
          identical to the YAML path)
        * ``side_effects`` → ``side_effects``
        * ``requires_human_approval`` map: two fields are emitted
          for back-compat AND per-tool fidelity (Burst 111):

            * ``requires_human_approval`` — per-server bool (any
              True in the map flips it). Kept so older callers and
              the YAML registry shape continue to work.
            * ``requires_human_approval_per_tool`` — the full dict
              ``{tool_name: bool, ...}`` straight from the manifest.
              The dispatcher's ``McpPerToolApprovalStep`` consults
              this map at dispatch time so a single
              ``mcp.foo.write`` can gate while ``mcp.foo.read``
              slips through, even when the YAML-style server-level
              bool would have flipped on for both. Lookup key is the
              bare tool name as it appears in the manifest's
              ``requires_human_approval`` map (e.g. ``write_file``,
              not ``mcp.fs.write_file``) — that's what an agent
              passes as ``tool_name`` to ``mcp_call.v1``.
        * ``capabilities`` → ``allowlisted_tools`` (stripping the
          ``mcp.<server>.`` prefix)
        """
        view: dict[str, dict[str, Any]] = {}
        for info in self._active.values():
            m = info.manifest
            if m.type != PluginType.MCP_SERVER:
                continue
            entry = m.entry_point
            # Default to stdio: prefix if the command doesn't already
            # have a URL scheme. mcp_call.v1's _resolve_server logic
            # handles 'stdio:' and 'http://' / 'https://' uniformly.
            url = entry.command
            if "://" not in url and not url.startswith("stdio:"):
                url = f"stdio:{url}"
            # Strip mcp.<server>. prefix from capabilities to recover
            # the tool names the MCP server itself exposes.
            tools_prefix = f"mcp.{m.name}."
            allowlisted_tools: list[str] = []
            for cap in m.capabilities:
                if cap.startswith(tools_prefix):
                    allowlisted_tools.append(cap[len(tools_prefix):])
                else:
                    # Capability that doesn't match the namespace
                    # convention — pass through as-is so plugin authors
                    # using non-conventional naming still work.
                    allowlisted_tools.append(cap)
            requires_approval = any(m.requires_human_approval.values())
            # Burst 111: pass the full per-tool map straight through
            # so McpPerToolApprovalStep can gate write_file while
            # leaving read_file ungated for the same plugin. dict()
            # makes a shallow copy — defensive, since the manifest
            # object is shared across calls.
            per_tool = dict(m.requires_human_approval)
            view[m.name] = {
                "url": url,
                "sha256": entry.sha256,
                "side_effects": m.side_effects.value,
                "requires_human_approval": requires_approval,
                "requires_human_approval_per_tool": per_tool,
                "allowlisted_tools": allowlisted_tools,
                "description": (
                    f"{m.display_label()} v{m.version} (plugin)"
                ),
            }
        return view

    # ---- mutate --------------------------------------------------------

    def reload(self) -> ReloadResult:
        """Re-walk the plugin directory and update the in-process
        view. Returns a :class:`ReloadResult` describing the diff.

        Caller MUST hold ``app.state.write_lock`` before calling
        — single-writer discipline applies to plugin runtime
        mutations the same way it applies to the registry.

        ADR-0043 audit emit (T4 / Burst 106):
        * ``plugin_installed`` for each name in ``added``
        * ``plugin_uninstalled`` for each name in ``removed`` —
          these are plugins that were active and are now gone from
          the filesystem entirely (operator ran ``fsf plugin
          uninstall`` or moved the directory). ``plugin_disabled``
          is a separate event emitted by :meth:`disable`.
        Updates (manifest version or sha256 changed) are NOT
        currently emitted as events; T5 may add a
        ``plugin_updated`` event if operators need to see binary
        upgrades on the chain.
        """
        result = ReloadResult()

        # Fresh disk read.
        try:
            disk_infos = self._repo.list()
        except PluginError as e:
            result.errors["__repository__"] = str(e)
            return result

        new_active: dict[str, PluginInfo] = {}
        new_disabled: dict[str, PluginInfo] = {}
        for info in disk_infos:
            if info.state == PluginState.INSTALLED:
                new_active[info.name] = info
            else:
                new_disabled[info.name] = info

        with self._snapshot_lock:
            # Diff against BOTH active and disabled — a plugin moved
            # from active to disabled isn't "uninstalled", just paused;
            # only the move-out-of-the-runtime case fires
            # plugin_uninstalled.
            old_known = set(self._active.keys()) | set(self._disabled.keys())
            new_known = set(new_active.keys()) | set(new_disabled.keys())

            old_active_names = set(self._active.keys())
            new_active_names = set(new_active.keys())

            result.added = sorted(new_active_names - old_active_names)
            result.removed = sorted(old_active_names - new_active_names)
            for name in new_active_names & old_active_names:
                old_v = self._active[name].manifest.version
                new_v = new_active[name].manifest.version
                old_sha = self._active[name].manifest.entry_point.sha256
                new_sha = new_active[name].manifest.entry_point.sha256
                if old_v != new_v or old_sha != new_sha:
                    result.updated.append(name)

            # Capture the staged-on-disk snapshot needed for emits
            # before we swap the in-memory state.
            new_active_snapshot = dict(new_active)
            uninstalled_names = old_known - new_known

            self._active = new_active
            self._disabled = new_disabled

        # Emits happen OUTSIDE the snapshot lock — the chain.append
        # call may take longer than a microsecond; we don't want
        # readers blocked on it.
        for name in result.added:
            info = new_active_snapshot.get(name)
            if info is not None:
                self._emit_audit("plugin_installed", {
                    "plugin_name": name,
                    "version": info.manifest.version,
                    "type": info.manifest.type.value,
                    "side_effects": info.manifest.side_effects.value,
                    "capabilities_count": len(info.manifest.capabilities),
                })
        for name in sorted(uninstalled_names):
            self._emit_audit("plugin_uninstalled", {
                "plugin_name": name,
            })

        logger.info(
            "plugin runtime reload: +%d / -%d / ~%d (errors=%d)",
            len(result.added), len(result.removed),
            len(result.updated), len(result.errors),
        )
        return result

    def enable(self, name: str) -> PluginInfo:
        """Move ``name`` from disabled/ to installed/ on disk; refresh
        the in-process snapshot. Caller holds the write lock.

        Emits ``plugin_enabled`` to the audit chain after the move
        succeeds. The post-move :meth:`reload` may also emit
        ``plugin_installed`` if this is the first time the plugin
        appears in the active set; that's expected — they capture
        different facts (the operator action vs. the runtime
        registration).
        """
        info = self._repo.enable(name)
        self.reload()
        self._emit_audit("plugin_enabled", {
            "plugin_name": info.name,
            "version": info.manifest.version,
            "type": info.manifest.type.value,
        })
        return info

    def disable(self, name: str) -> PluginInfo:
        """Inverse of :meth:`enable`. Emits ``plugin_disabled``."""
        info = self._repo.disable(name)
        self.reload()
        self._emit_audit("plugin_disabled", {
            "plugin_name": info.name,
            "version": info.manifest.version,
            "type": info.manifest.type.value,
        })
        return info

    def verify(self, name: str) -> tuple[bool, PluginInfo]:
        """Re-check the entry-point binary's sha256. Returns
        (matches, info). Operator action on mismatch is to either
        update the manifest's pinned sha256 (trust the new binary)
        or restore the original binary.

        On mismatch, emits ``plugin_verification_failed`` so the
        forensic question "when did the operator first notice the
        binary diverged?" is answerable from the chain. Successful
        verifications do NOT emit — the chain would be flooded with
        no-op events from periodic verify polls.
        """
        info = self._repo.load(name)
        ok = self._repo.verify_binary(name)
        if not ok:
            self._emit_audit("plugin_verification_failed", {
                "plugin_name": info.name,
                "expected_sha256": info.manifest.entry_point.sha256,
            })
        return ok, info

    # ---- audit emit ---------------------------------------------------

    def _emit_audit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Best-effort audit emit. Never raises out of the runtime.

        The audit chain is the evidence layer; if it's down, the
        runtime keeps working and the operator sees the gap on chain
        inspection. Same posture as the scheduler's audit-emit
        policy from Burst 89.
        """
        if self._audit is None:
            return
        try:
            self._audit.append(event_type, payload, agent_dna=None)
        except Exception:
            logger.exception(
                "plugin runtime audit emit failed for %s", event_type,
            )


def build_plugin_runtime(
    plugin_root: Path | None = None,
    audit_chain: Any = None,
) -> PluginRuntime:
    """Construct a :class:`PluginRuntime` over the standard plugin
    root (or an override). Performs an initial reload so the runtime
    starts populated. Lifespan calls this once.

    ``audit_chain`` is forwarded to the runtime; the initial reload
    DOES emit ``plugin_installed`` events for every plugin found
    on disk, which gives the chain a clean post-restart baseline of
    what the daemon thinks is active.
    """
    repo = PluginRepository(root=plugin_root)
    runtime = PluginRuntime(repo, audit_chain=audit_chain)
    # Initial population — equivalent to a startup reload but without
    # the write_lock since lifespan owns the only handle and there are
    # no concurrent writers yet.
    runtime.reload()
    return runtime
