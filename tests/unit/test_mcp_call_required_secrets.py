"""ADR-0052 T4 (B170) — _resolve_required_secrets() helper tests.

The integration of the manifest's ``required_secrets`` into the MCP
server launch path is encapsulated in
``mcp_call._resolve_required_secrets``. Testing the helper in
isolation gives the same coverage as driving the full async
McpCallTool.execute path but with a fraction of the setup.

What's covered:

  - Empty list → no resolver call, no env mutation, no resolver
    import (lazy import is part of the contract)
  - Populated list → resolver fires once per entry; each env_var
    lands in auth_env with the resolved value
  - Missing secret (store.get returns None) → McpCallError pointing
    at `fsf secret put <name>`
  - Backend unreachable (resolver raises) → McpCallError pointing
    at FSF_SECRET_STORE
  - Backend .get() raises → McpCallError tied to the specific name
    + backend identifier
  - Malformed entry shapes (missing name/env_var, non-dict) skipped
    silently; defense in depth — manifest validation is the primary
    guard
"""
from __future__ import annotations

from unittest import mock

import pytest

from forest_soul_forge.security.secrets import SecretStoreError
from forest_soul_forge.tools.builtin.mcp_call import (
    McpCallError,
    _resolve_required_secrets,
)


class _FakeStore:
    """Minimal SecretStoreProtocol-shaped fake."""
    name = "fake"

    def __init__(self, mapping: dict[str, str | None]):
        self._m = mapping
        self.get_calls: list[str] = []

    def get(self, name: str) -> str | None:
        self.get_calls.append(name)
        return self._m.get(name)


def _patch_resolver(store):
    """Patch resolve_secret_store at the import path the helper
    uses (lazy-imported inside the function)."""
    return mock.patch(
        "forest_soul_forge.security.secrets.resolve_secret_store",
        return_value=store,
    )


# ---------------------------------------------------------------------------
# Empty-list path
# ---------------------------------------------------------------------------

def test_empty_list_is_noop():
    """No required_secrets → no resolver call, no env mutation.
    Existing plugins (forest-echo, brave-search, soulux-computer-
    control) all have empty lists; their behavior must stay
    byte-identical."""
    auth_env: dict[str, str] = {}
    fake_store = _FakeStore({})
    with _patch_resolver(fake_store):
        _resolve_required_secrets(
            server_name="some-plugin",
            required_secrets=[],
            auth_env=auth_env,
        )
    assert auth_env == {}
    assert fake_store.get_calls == []


def test_none_list_is_noop():
    """None as required_secrets — defensive: behaves same as []."""
    auth_env: dict[str, str] = {}
    _resolve_required_secrets(
        server_name="x",
        required_secrets=[],
        auth_env=auth_env,
    )
    assert auth_env == {}


# ---------------------------------------------------------------------------
# Happy path — resolved values land in declared env_vars
# ---------------------------------------------------------------------------

def test_resolved_values_populate_env_vars():
    fake_store = _FakeStore({
        "github_pat": "ghp_secret_xyz",
        "github_webhook_secret": "whsec_blah",
    })
    auth_env: dict[str, str] = {}
    with _patch_resolver(fake_store):
        _resolve_required_secrets(
            server_name="github-mcp",
            required_secrets=[
                {"name": "github_pat", "env_var": "GITHUB_TOKEN", "description": ""},
                {"name": "github_webhook_secret", "env_var": "GITHUB_WEBHOOK_SECRET"},
            ],
            auth_env=auth_env,
        )
    assert auth_env["GITHUB_TOKEN"] == "ghp_secret_xyz"
    assert auth_env["GITHUB_WEBHOOK_SECRET"] == "whsec_blah"
    assert sorted(fake_store.get_calls) == ["github_pat", "github_webhook_secret"]


def test_existing_auth_env_preserved():
    """The helper mutates auth_env in place; pre-existing keys
    (like FSF_MCP_AUTH from the per-call path) survive."""
    fake_store = _FakeStore({"x": "y"})
    auth_env = {"FSF_MCP_AUTH": "preexisting_token"}
    with _patch_resolver(fake_store):
        _resolve_required_secrets(
            server_name="p",
            required_secrets=[{"name": "x", "env_var": "X", "description": ""}],
            auth_env=auth_env,
        )
    assert auth_env == {"FSF_MCP_AUTH": "preexisting_token", "X": "y"}


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

def test_missing_secret_raises_with_fsf_secret_put_pointer():
    fake_store = _FakeStore({"present_one": "x"})
    auth_env: dict[str, str] = {}
    with _patch_resolver(fake_store):
        with pytest.raises(McpCallError) as exc:
            _resolve_required_secrets(
                server_name="github-mcp",
                required_secrets=[
                    {"name": "absent_one", "env_var": "ABSENT", "description": ""},
                ],
                auth_env=auth_env,
            )
    msg = str(exc.value)
    assert "absent_one" in msg
    assert "fsf secret put" in msg
    assert "fake" in msg     # backend name surfaces


def test_backend_unreachable_raises():
    """SecretStoreError from resolve_secret_store() → McpCallError
    with FSF_SECRET_STORE pointer."""
    auth_env: dict[str, str] = {}
    with mock.patch(
        "forest_soul_forge.security.secrets.resolve_secret_store",
        side_effect=SecretStoreError("vault locked"),
    ):
        with pytest.raises(McpCallError) as exc:
            _resolve_required_secrets(
                server_name="p",
                required_secrets=[{"name": "x", "env_var": "X"}],
                auth_env=auth_env,
            )
    msg = str(exc.value)
    assert "secret-store backend is unavailable" in msg
    assert "vault locked" in msg
    assert "FSF_SECRET_STORE" in msg


def test_backend_get_raises_per_secret_message():
    """SecretStoreError from .get() (e.g., chmod-violation,
    malformed YAML) propagates as McpCallError tied to the
    specific name."""
    class _RaisingStore:
        name = "file"
        def get(self, name):
            raise SecretStoreError("chmod violation")

    auth_env: dict[str, str] = {}
    with _patch_resolver(_RaisingStore()):
        with pytest.raises(McpCallError) as exc:
            _resolve_required_secrets(
                server_name="p",
                required_secrets=[{"name": "trouble", "env_var": "T"}],
                auth_env=auth_env,
            )
    msg = str(exc.value)
    assert "trouble" in msg
    assert "chmod violation" in msg
    assert "file" in msg


# ---------------------------------------------------------------------------
# Defense in depth — malformed entry shapes
# ---------------------------------------------------------------------------

def test_malformed_entries_skipped_silently():
    """Manifest schema validates name + env_var at load time. If
    a malformed dict somehow reaches this path (e.g., hand-built
    test fixture), the helper skips silently rather than
    dereferencing None or KeyError-ing out."""
    fake_store = _FakeStore({"good_one": "ok"})
    auth_env: dict[str, str] = {}
    with _patch_resolver(fake_store):
        _resolve_required_secrets(
            server_name="p",
            required_secrets=[
                {"name": "good_one", "env_var": "GOOD"},
                {"name": None, "env_var": "BAD"},          # name None
                {"name": "no_env_var"},                    # missing env_var
                {"description": "no name no env"},         # missing both
                "not a dict at all",                       # type mismatch
            ],
            auth_env=auth_env,
        )
    assert auth_env == {"GOOD": "ok"}
    assert fake_store.get_calls == ["good_one"]
