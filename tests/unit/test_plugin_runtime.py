"""Unit tests for the daemon's PluginRuntime — ADR-0043 T3."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.daemon.plugins_runtime import (
    PluginRuntime,
    ReloadResult,
    build_plugin_runtime,
)
from forest_soul_forge.plugins import (
    PluginNotFound,
    PluginRepository,
    PluginState,
)


def _write_plugin_dir(
    base: Path,
    *,
    name: str = "github-mcp",
    version: str = "0.1.0",
    plugin_type: str = "mcp_server",
    capabilities: list[str] | None = None,
    binary_bytes: bytes = b"#!/bin/sh\necho hi\n",
    requires_human_approval: dict | None = None,
) -> Path:
    src = base / f"src-{name}"
    src.mkdir(parents=True)
    binary = src / "server"
    binary.write_bytes(binary_bytes)
    sha = hashlib.sha256(binary_bytes).hexdigest()
    if capabilities is None:
        capabilities = [f"mcp.{name}.do_thing"]
    body = {
        "schema_version": 1,
        "name": name,
        "version": version,
        "type": plugin_type,
        "side_effects": "external",
        "entry_point": {
            "type": "stdio",
            "command": "./server",
            "sha256": sha,
        },
        "capabilities": capabilities,
    }
    if requires_human_approval is not None:
        body["requires_human_approval"] = requires_human_approval
    (src / "plugin.yaml").write_text(yaml.safe_dump(body))
    return src


def _runtime(tmp_path: Path) -> PluginRuntime:
    repo = PluginRepository(root=tmp_path / "plugins")
    return PluginRuntime(repo)


# ---- Initial reload ------------------------------------------------------

def test_runtime_starts_empty(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.reload()
    assert rt.active() == []
    assert rt.disabled() == []
    assert rt.all() == []


def test_build_plugin_runtime_initial_reload(tmp_path: Path):
    repo = PluginRepository(root=tmp_path / "plugins")
    repo.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt = build_plugin_runtime(plugin_root=tmp_path / "plugins")
    assert {p.name for p in rt.active()} == {"alpha"}


# ---- Diff semantics ------------------------------------------------------

def test_reload_diff_added_removed_updated(tmp_path: Path):
    rt = _runtime(tmp_path)
    repo = rt.repository

    # Initial state: alpha installed.
    repo.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.reload()
    assert {p.name for p in rt.active()} == {"alpha"}

    # Add beta. Reload should report added=[beta], removed=[], updated=[].
    repo.install_from_dir(_write_plugin_dir(tmp_path / "b", name="beta"))
    diff = rt.reload()
    assert diff.added == ["beta"]
    assert diff.removed == []
    assert diff.updated == []
    assert not diff.is_clean

    # Remove alpha. Reload should report added=[], removed=[alpha].
    repo.uninstall("alpha")
    diff = rt.reload()
    assert diff.added == []
    assert diff.removed == ["alpha"]
    assert diff.updated == []

    # Same set. Clean reload.
    diff = rt.reload()
    assert diff.is_clean


def test_reload_detects_version_change(tmp_path: Path):
    rt = _runtime(tmp_path)
    repo = rt.repository
    repo.install_from_dir(_write_plugin_dir(tmp_path, version="0.1.0"))
    rt.reload()

    # Reinstall with a new version + new bytes (so sha256 changes too).
    repo.install_from_dir(
        _write_plugin_dir(tmp_path / "v2", version="0.2.0", binary_bytes=b"v2"),
        force=True,
    )
    diff = rt.reload()
    assert "github-mcp" in diff.updated


def test_reload_detects_sha_change_only(tmp_path: Path):
    """Same version, different binary bytes — still flagged as updated."""
    rt = _runtime(tmp_path)
    repo = rt.repository
    repo.install_from_dir(_write_plugin_dir(tmp_path, binary_bytes=b"old"))
    rt.reload()
    repo.install_from_dir(
        _write_plugin_dir(tmp_path / "v2", binary_bytes=b"new"),
        force=True,
    )
    diff = rt.reload()
    assert "github-mcp" in diff.updated


def test_reload_disabled_plugin_not_in_active(tmp_path: Path):
    rt = _runtime(tmp_path)
    repo = rt.repository
    repo.install_from_dir(_write_plugin_dir(tmp_path))
    rt.reload()
    repo.disable("github-mcp")
    diff = rt.reload()
    assert diff.removed == ["github-mcp"]
    assert {p.name for p in rt.active()} == set()
    assert {p.name for p in rt.disabled()} == {"github-mcp"}


# ---- get / all -----------------------------------------------------------

def test_get_returns_active(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path))
    rt.reload()
    info = rt.get("github-mcp")
    assert info.name == "github-mcp"
    assert info.state == PluginState.INSTALLED


def test_get_returns_disabled(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path))
    rt.reload()
    rt.disable("github-mcp")
    info = rt.get("github-mcp")
    assert info.state == PluginState.DISABLED


def test_get_unknown_raises(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.reload()
    with pytest.raises(PluginNotFound):
        rt.get("ghost")


def test_all_returns_both_states(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path / "b", name="beta"))
    rt.reload()
    rt.disable("beta")
    names = {p.name: p.state for p in rt.all()}
    assert names == {
        "alpha": PluginState.INSTALLED,
        "beta": PluginState.DISABLED,
    }


# ---- enable / disable / verify ------------------------------------------

def test_enable_disable_round_trip(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path))
    rt.reload()
    rt.disable("github-mcp")
    assert rt.get("github-mcp").state == PluginState.DISABLED
    rt.enable("github-mcp")
    assert rt.get("github-mcp").state == PluginState.INSTALLED


def test_verify_match(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path))
    rt.reload()
    ok, info = rt.verify("github-mcp")
    assert ok is True
    assert info.name == "github-mcp"


def test_verify_mismatch(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path))
    rt.reload()
    info = rt.get("github-mcp")
    (info.directory / "server").write_bytes(b"tampered")
    ok, _ = rt.verify("github-mcp")
    assert ok is False


# ---- mcp_servers_view ---------------------------------------------------

def test_mcp_servers_view_emits_active_mcp_servers(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(
        tmp_path,
        name="github-mcp",
        capabilities=[
            "mcp.github-mcp.list_issues",
            "mcp.github-mcp.create_issue",
        ],
        requires_human_approval={"create_issue": True},
    ))
    rt.reload()
    view = rt.mcp_servers_view()
    assert "github-mcp" in view
    entry = view["github-mcp"]
    # Stripped allowlist tools (no namespace prefix)
    assert sorted(entry["allowlisted_tools"]) == ["create_issue", "list_issues"]
    # Approval-gate flips when ANY tool requires approval
    assert entry["requires_human_approval"] is True
    # URL prefix added when not URL-shaped
    assert entry["url"] == "stdio:./server"
    # sha256 + side_effects propagate
    assert len(entry["sha256"]) == 64
    assert entry["side_effects"] == "external"
    assert "v0.1.0" in entry["description"]


def test_mcp_servers_view_omits_disabled(tmp_path: Path):
    rt = _runtime(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path))
    rt.reload()
    rt.disable("github-mcp")
    assert rt.mcp_servers_view() == {}


def test_mcp_servers_view_skips_non_mcp_types(tmp_path: Path):
    """A plugin with type=tool (or skill, or genre) shouldn't appear in
    the mcp_servers_view — different runtime path."""
    # Write a plugin with type='tool' (still parses; only mcp_server is
    # bridged to mcp_call.v1 today).
    rt = _runtime(tmp_path)
    src = _write_plugin_dir(tmp_path, name="my-tool", plugin_type="tool")
    rt.repository.install_from_dir(src)
    rt.reload()
    # Active surface includes it...
    assert {p.name for p in rt.active()} == {"my-tool"}
    # ... but it's not in the MCP bridge view.
    assert rt.mcp_servers_view() == {}


def test_mcp_servers_view_passes_through_unconventional_capabilities(tmp_path: Path):
    """Capabilities that don't follow the mcp.<name>. namespace
    convention pass through verbatim."""
    rt = _runtime(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(
        tmp_path,
        capabilities=["unconventional_tool_name"],
    ))
    rt.reload()
    view = rt.mcp_servers_view()
    assert view["github-mcp"]["allowlisted_tools"] == ["unconventional_tool_name"]


# ---- Audit emit (Burst 106 / T4) ---------------------------------------

class _FakeChain:
    """Captures audit.append calls so tests can assert event sequence."""

    def __init__(self, raise_on_append: bool = False):
        self.events: list[tuple[str, dict]] = []
        self._raise = raise_on_append

    def append(self, event_type, payload, *, agent_dna=None):
        if self._raise:
            raise RuntimeError("simulated chain failure")
        self.events.append((event_type, dict(payload)))


def _runtime_with_chain(tmp_path: Path):
    """Helper: PluginRuntime + a _FakeChain wired in."""
    chain = _FakeChain()
    repo = PluginRepository(root=tmp_path / "plugins")
    rt = PluginRuntime(repo, audit_chain=chain)
    return rt, chain


def test_reload_emits_plugin_installed_for_added(tmp_path: Path):
    rt, chain = _runtime_with_chain(tmp_path)
    rt.reload()  # baseline empty — no events
    assert chain.events == []

    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.reload()
    types = [e[0] for e in chain.events]
    assert types == ["plugin_installed"]
    payload = chain.events[0][1]
    assert payload["plugin_name"] == "alpha"
    assert payload["version"] == "0.1.0"
    assert payload["type"] == "mcp_server"
    assert payload["side_effects"] == "external"
    assert payload["capabilities_count"] == 1


def test_reload_emits_plugin_uninstalled_for_removed(tmp_path: Path):
    rt, chain = _runtime_with_chain(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.reload()  # plugin_installed
    rt.repository.uninstall("alpha")
    rt.reload()
    types = [e[0] for e in chain.events]
    assert types == ["plugin_installed", "plugin_uninstalled"]
    assert chain.events[1][1]["plugin_name"] == "alpha"


def test_reload_does_not_emit_uninstalled_when_only_disabled(tmp_path: Path):
    """A plugin moving from active to disabled is NOT uninstalled —
    it stays known to the runtime."""
    rt, chain = _runtime_with_chain(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.reload()  # plugin_installed
    chain.events.clear()
    rt.repository.disable("alpha")
    rt.reload()
    # No plugin_uninstalled — alpha is still on disk in disabled/.
    types = [e[0] for e in chain.events]
    assert "plugin_uninstalled" not in types


def test_enable_emits_plugin_enabled(tmp_path: Path):
    rt, chain = _runtime_with_chain(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.reload()
    rt.disable("alpha")
    chain.events.clear()
    rt.enable("alpha")
    types = [e[0] for e in chain.events]
    # Reload-from-disabled-to-active emits plugin_installed (alpha
    # appears in active set again), then plugin_enabled records the
    # operator action explicitly.
    assert "plugin_installed" in types
    assert "plugin_enabled" in types
    enabled_payload = next(p for t, p in chain.events if t == "plugin_enabled")
    assert enabled_payload["plugin_name"] == "alpha"
    assert enabled_payload["version"] == "0.1.0"


def test_disable_emits_plugin_disabled(tmp_path: Path):
    rt, chain = _runtime_with_chain(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.reload()
    chain.events.clear()
    rt.disable("alpha")
    types = [e[0] for e in chain.events]
    assert "plugin_disabled" in types
    payload = next(p for t, p in chain.events if t == "plugin_disabled")
    assert payload["plugin_name"] == "alpha"


def test_verify_mismatch_emits_plugin_verification_failed(tmp_path: Path):
    rt, chain = _runtime_with_chain(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.reload()
    chain.events.clear()
    info = rt.get("alpha")
    (info.directory / "server").write_bytes(b"tampered")
    ok, _ = rt.verify("alpha")
    assert ok is False
    types = [e[0] for e in chain.events]
    assert types == ["plugin_verification_failed"]
    payload = chain.events[0][1]
    assert payload["plugin_name"] == "alpha"
    assert payload["expected_sha256"] == info.manifest.entry_point.sha256


def test_verify_match_does_not_emit(tmp_path: Path):
    """Successful verifies skip the chain — periodic polls would
    otherwise flood it with no-ops."""
    rt, chain = _runtime_with_chain(tmp_path)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.reload()
    chain.events.clear()
    ok, _ = rt.verify("alpha")
    assert ok is True
    assert chain.events == []


def test_audit_emit_failure_does_not_break_runtime(tmp_path: Path):
    """Same posture as the scheduler — chain failures must not block
    runtime operations."""
    chain = _FakeChain(raise_on_append=True)
    repo = PluginRepository(root=tmp_path / "plugins")
    rt = PluginRuntime(repo, audit_chain=chain)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    # Should not raise even though every chain.append throws.
    rt.reload()
    assert {p.name for p in rt.active()} == {"alpha"}


def test_runtime_without_chain_is_silent_no_op(tmp_path: Path):
    """Runtime constructed without a chain handle still operates;
    audit emits silently no-op."""
    repo = PluginRepository(root=tmp_path / "plugins")
    rt = PluginRuntime(repo, audit_chain=None)
    rt.repository.install_from_dir(_write_plugin_dir(tmp_path, name="alpha"))
    rt.reload()
    assert {p.name for p in rt.active()} == {"alpha"}
