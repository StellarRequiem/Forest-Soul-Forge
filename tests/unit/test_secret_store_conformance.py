"""Conformance test suite for SecretStoreProtocol implementations.

Per ADR-0052 §"Negative — Three-backends-plus-BYO surface...
Mitigation: a shared conformance test suite ... every backend must
pass."

T1 (B167) ships FileStore. The conformance class below exercises
FileStore directly. Future tranches (T2 KeychainStore, T3
VaultWardenStore) parameterize the same class against their backend
fixtures; any backend that fails the conformance contract surfaces
the regression at this seam.

The contract under test:

  - get() returns None for unknown names (NOT raises)
  - put() then get() round-trips a string verbatim
  - put() with the same key twice overwrites (idempotent)
  - delete() removes a name (subsequent get returns None)
  - delete() of an unknown name is a no-op (NOT raises)
  - list_names() reflects the current set after put/delete
  - put() with non-string value raises SecretStoreError
  - put() with empty name raises SecretStoreError
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from forest_soul_forge.security.secrets import (
    FileStore,
    SecretStoreError,
    SecretStoreProtocol,
)


# ---------------------------------------------------------------------------
# FileStore-specific harness — gives each test its own tmp file
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> SecretStoreProtocol:
    """A fresh FileStore at a tmp path. Other backends parameterize
    by replacing this fixture; the test methods below stay
    backend-agnostic."""
    return FileStore(tmp_path / "secrets.yaml")


# ---------------------------------------------------------------------------
# Conformance contract — every backend must pass this
# ---------------------------------------------------------------------------

class TestSecretStoreConformance:
    """Shared across all SecretStoreProtocol implementations.

    Each backend's tranche adds a parameterized version of this
    class with the backend's own fixture; the assertion shape stays
    identical so a regression in any backend trips the same
    assertion line and the diff to git-bisect through is minimal.
    """

    def test_implements_protocol(self, store):
        """Structural Protocol check — catches accidental rename of
        get/put/delete/list_names or removal of the name attr."""
        assert isinstance(store, SecretStoreProtocol)

    def test_has_backend_name_string(self, store):
        """Backends MUST identify themselves via a name class attr.
        The resolver + audit chain rely on it."""
        assert isinstance(store.name, str)
        assert len(store.name) > 0

    def test_get_unknown_returns_none(self, store):
        """Unknown name → None, not a raise. Lets the loader
        distinguish 'no such secret' from 'backend exploded'."""
        assert store.get("never_set") is None

    def test_put_then_get_roundtrip(self, store):
        store.put("api_token", "ghp_abc123_secret_value")
        assert store.get("api_token") == "ghp_abc123_secret_value"

    def test_put_overwrites(self, store):
        store.put("api_token", "first")
        store.put("api_token", "second")
        assert store.get("api_token") == "second"

    def test_delete_removes(self, store):
        store.put("api_token", "v1")
        store.delete("api_token")
        assert store.get("api_token") is None

    def test_delete_unknown_is_noop(self, store):
        """No raise — backends must be idempotent on delete."""
        store.delete("never_set")
        # Second delete also fine.
        store.delete("never_set")

    def test_list_names_reflects_current_set(self, store):
        store.put("a", "1")
        store.put("b", "2")
        names = sorted(store.list_names())
        assert names == ["a", "b"]
        store.delete("a")
        assert sorted(store.list_names()) == ["b"]

    def test_put_non_string_value_raises(self, store):
        with pytest.raises(SecretStoreError):
            store.put("foo", 12345)             # type: ignore[arg-type]

    def test_put_empty_name_raises(self, store):
        with pytest.raises(SecretStoreError):
            store.put("", "value")


# ---------------------------------------------------------------------------
# FileStore-specific surfaces (chmod enforcement, env-var path,
# malformed YAML, etc.) — not part of the cross-backend contract
# ---------------------------------------------------------------------------

class TestFileStoreSpecific:
    def test_get_refuses_loose_perms(self, tmp_path: Path):
        """A 644 secrets file is a diagnostic problem; FileStore
        refuses to read until perms are tight."""
        path = tmp_path / "secrets.yaml"
        path.write_text("api_token: hello\n")
        os.chmod(path, 0o644)
        store = FileStore(path)
        with pytest.raises(SecretStoreError) as exc:
            store.get("api_token")
        assert "mode" in str(exc.value).lower()
        assert "chmod 600" in str(exc.value)

    def test_get_accepts_tight_perms(self, tmp_path: Path):
        path = tmp_path / "secrets.yaml"
        path.write_text("api_token: hello\n")
        os.chmod(path, 0o600)
        store = FileStore(path)
        assert store.get("api_token") == "hello"

    def test_put_chmods_file_to_600(self, tmp_path: Path):
        path = tmp_path / "secrets.yaml"
        store = FileStore(path)
        store.put("api_token", "v1")
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_malformed_yaml_raises(self, tmp_path: Path):
        path = tmp_path / "secrets.yaml"
        path.write_text("not: valid: yaml: blob: : :\n")
        os.chmod(path, 0o600)
        store = FileStore(path)
        with pytest.raises(SecretStoreError) as exc:
            store.get("anything")
        assert "malformed" in str(exc.value).lower()

    def test_non_string_value_in_file_raises_on_get(self, tmp_path: Path):
        """If the YAML file has been hand-edited to put a non-string
        value, FileStore surfaces that loud rather than coercing."""
        path = tmp_path / "secrets.yaml"
        path.write_text("api_token: 12345\n")    # int, not string
        os.chmod(path, 0o600)
        store = FileStore(path)
        with pytest.raises(SecretStoreError) as exc:
            store.get("api_token")
        assert "expected string" in str(exc.value)

    def test_env_var_path_override(self, tmp_path: Path, monkeypatch):
        """FSF_FILE_SECRETS_PATH overrides the default ~/.forest/...
        Useful for read-only mounts in containerized deployments."""
        custom = tmp_path / "custom-secrets.yaml"
        monkeypatch.setenv("FSF_FILE_SECRETS_PATH", str(custom))
        store = FileStore()
        store.put("foo", "bar")
        assert custom.exists()
        assert store.get("foo") == "bar"

    def test_default_path_expands_tilde(self, monkeypatch):
        """Operator overrides via env var; default is in their HOME.
        Verifying expanduser fires (test doesn't actually write to
        $HOME — just checks the resolved path is absolute)."""
        monkeypatch.delenv("FSF_FILE_SECRETS_PATH", raising=False)
        store = FileStore()
        # Path is computed from the default, expanded via expanduser.
        # Must be absolute (no leading ~).
        assert "/" in str(store._path)
        assert "~" not in str(store._path)
