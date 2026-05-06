"""ADR-0052 T1 (B167) — resolve_secret_store() tests.

The resolver reads ``FSF_SECRET_STORE`` and dispatches to the
right backend. Test coverage:

  - Default (no env var) → FileStore
  - Explicit 'file' → FileStore
  - 'keychain' / 'vaultwarden' → SecretStoreError today (T2/T3
    will replace these stubs with real backends)
  - 'module:dotted.path.Class' → BYO import path
  - Bad / unrecognized values → SecretStoreError
  - Per-process cache: same instance across calls until force_reload
"""
from __future__ import annotations

import pytest

from forest_soul_forge.security.secrets import (
    FileStore,
    SecretStoreError,
    resolve_secret_store,
)
from forest_soul_forge.security.secrets.resolver import _reset_cache_for_tests


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    """Each test gets a clean resolver cache."""
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Default + explicit backend selection
# ---------------------------------------------------------------------------

def test_default_resolves_to_platform_backend(monkeypatch, tmp_path):
    """No FSF_SECRET_STORE env var → platform default. Darwin →
    KeychainStore; everything else → FileStore (T2 default flip,
    B168)."""
    monkeypatch.delenv("FSF_SECRET_STORE", raising=False)
    monkeypatch.setenv("FSF_FILE_SECRETS_PATH", str(tmp_path / "s.yaml"))
    import platform as _platform
    if _platform.system() == "Darwin":
        # On a Mac the resolver picks Keychain — but constructing it
        # is fine even in tests; we just don't exercise security CLI
        # calls here.
        store = resolve_secret_store(force_reload=True)
        assert store.name == "keychain"
    else:
        store = resolve_secret_store(force_reload=True)
        assert isinstance(store, FileStore)
        assert store.name == "file"


def test_explicit_file_resolves_to_file_store(monkeypatch, tmp_path):
    monkeypatch.setenv("FSF_SECRET_STORE", "file")
    monkeypatch.setenv("FSF_FILE_SECRETS_PATH", str(tmp_path / "s.yaml"))
    store = resolve_secret_store(force_reload=True)
    assert isinstance(store, FileStore)


def test_whitespace_in_env_var_is_stripped(monkeypatch, tmp_path):
    """Operator might have ' file ' or 'file\\n' in their .env;
    the resolver tolerates surrounding whitespace."""
    monkeypatch.setenv("FSF_SECRET_STORE", "  file  ")
    monkeypatch.setenv("FSF_FILE_SECRETS_PATH", str(tmp_path / "s.yaml"))
    store = resolve_secret_store(force_reload=True)
    assert isinstance(store, FileStore)


# ---------------------------------------------------------------------------
# Pending-tranche stubs (T2 / T3)
# ---------------------------------------------------------------------------

def test_keychain_on_darwin_resolves(monkeypatch):
    """T2 (B168) shipped KeychainStore. On Darwin the resolver
    constructs it; on non-Darwin the constructor raises with a
    platform-only message."""
    import platform as _platform
    monkeypatch.setenv("FSF_SECRET_STORE", "keychain")
    if _platform.system() == "Darwin":
        store = resolve_secret_store(force_reload=True)
        assert store.name == "keychain"
    else:
        with pytest.raises(SecretStoreError) as exc:
            resolve_secret_store(force_reload=True)
        assert "macOS-only" in str(exc.value)


def test_vaultwarden_raises_with_pointer_to_t3(monkeypatch):
    monkeypatch.setenv("FSF_SECRET_STORE", "vaultwarden")
    with pytest.raises(SecretStoreError) as exc:
        resolve_secret_store(force_reload=True)
    assert "T3" in str(exc.value)


# ---------------------------------------------------------------------------
# Unrecognized values
# ---------------------------------------------------------------------------

def test_unknown_backend_id_raises(monkeypatch):
    monkeypatch.setenv("FSF_SECRET_STORE", "totally_made_up")
    with pytest.raises(SecretStoreError) as exc:
        resolve_secret_store(force_reload=True)
    msg = str(exc.value)
    # The error must list the valid options so the operator can
    # self-correct without grepping the source.
    assert "file" in msg
    assert "keychain" in msg
    assert "vaultwarden" in msg
    assert "module:" in msg


# ---------------------------------------------------------------------------
# BYO module path
# ---------------------------------------------------------------------------

def test_byo_module_loads_valid_class(monkeypatch, tmp_path):
    """A BYO backend that satisfies SecretStoreProtocol structurally
    is accepted. Use FileStore itself as the BYO target — it
    structurally satisfies the protocol."""
    monkeypatch.setenv(
        "FSF_SECRET_STORE",
        "module:forest_soul_forge.security.secrets.file_store.FileStore",
    )
    monkeypatch.setenv("FSF_FILE_SECRETS_PATH", str(tmp_path / "byo.yaml"))
    store = resolve_secret_store(force_reload=True)
    assert isinstance(store, FileStore)


def test_byo_module_path_without_dot_raises(monkeypatch):
    monkeypatch.setenv("FSF_SECRET_STORE", "module:no_dot_here")
    with pytest.raises(SecretStoreError) as exc:
        resolve_secret_store(force_reload=True)
    assert "module.path" in str(exc.value).lower() or "ClassName" in str(exc.value)


def test_byo_module_not_importable_raises(monkeypatch):
    monkeypatch.setenv(
        "FSF_SECRET_STORE",
        "module:not_a_real_module.SomeClass",
    )
    with pytest.raises(SecretStoreError) as exc:
        resolve_secret_store(force_reload=True)
    assert "not importable" in str(exc.value)


def test_byo_class_not_in_module_raises(monkeypatch):
    monkeypatch.setenv(
        "FSF_SECRET_STORE",
        "module:forest_soul_forge.security.secrets.file_store.NotAClass",
    )
    with pytest.raises(SecretStoreError) as exc:
        resolve_secret_store(force_reload=True)
    assert "not found" in str(exc.value).lower()


def test_byo_class_not_protocol_raises(monkeypatch):
    """A BYO module-path that resolves to a class without
    get/put/delete/list_names is rejected at the protocol check."""
    monkeypatch.setenv(
        "FSF_SECRET_STORE",
        # str class — has no SecretStoreProtocol methods
        "module:builtins.str",
    )
    with pytest.raises(SecretStoreError) as exc:
        resolve_secret_store(force_reload=True)
    assert "SecretStoreProtocol" in str(exc.value)


# ---------------------------------------------------------------------------
# Per-process cache
# ---------------------------------------------------------------------------

def test_cached_within_process(monkeypatch, tmp_path):
    """Two calls with the same env var return the same instance —
    callers don't need to thread the handle through their call
    chain."""
    monkeypatch.setenv("FSF_SECRET_STORE", "file")
    monkeypatch.setenv("FSF_FILE_SECRETS_PATH", str(tmp_path / "s.yaml"))
    a = resolve_secret_store(force_reload=True)
    b = resolve_secret_store()
    assert a is b


def test_force_reload_bypasses_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("FSF_SECRET_STORE", "file")
    monkeypatch.setenv("FSF_FILE_SECRETS_PATH", str(tmp_path / "s.yaml"))
    a = resolve_secret_store(force_reload=True)
    b = resolve_secret_store(force_reload=True)
    assert a is not b
