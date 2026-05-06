"""FileStore — plaintext secrets at ``~/.forest/secrets/secrets.yaml``.

Per ADR-0052 Decision 3: the FILE backend is INSECURE by design;
it exists for CI environments + sandboxed daemons + operators
spinning up Forest before configuring their preferred vault. A
loud warning fires on first read in production-shape contexts;
the daemon's startup banner recommends the operator switch to
KeychainStore (macOS) or VaultWardenStore.

Hard-edge defenses:

  - chmod-600 enforced at write time. Read refuses if perms are
    looser (catches ``cp`` losing the bit, careless ``chmod``,
    rsync pulling the file with 644).
  - File path resolved via ``Path.expanduser()`` — supports the
    operator overriding ``~/.forest/secrets/`` via
    ``FSF_FILE_SECRETS_PATH`` env var (e.g., for read-only mounts
    in containerized deployments).
  - Plain YAML format: ``{secret_name: secret_value}``. Forest
    treats values as opaque strings; YAML loaders never auto-
    expand references in this file (we use ``yaml.safe_load``).
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import ClassVar

import yaml

from .protocol import SecretStoreError


class FileStore:
    """Plaintext-YAML secrets backend. INSECURE; documented as such.

    Implements :class:`SecretStoreProtocol` structurally (Forest's
    runtime check uses the @runtime_checkable Protocol).
    """

    name: ClassVar[str] = "file"

    #: Default path. Operator overrides via ``FSF_FILE_SECRETS_PATH``
    #: pointing at a different YAML file (e.g., a read-only Docker
    #: mount). The directory is created on first write if absent.
    DEFAULT_PATH = Path("~/.forest/secrets/secrets.yaml")

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            override = os.environ.get("FSF_FILE_SECRETS_PATH")
            path = Path(override) if override else self.DEFAULT_PATH
        self._path: Path = Path(path).expanduser()
        # Warn loudly on first-touch — easy to miss the daemon
        # startup banner. Stderr because logging may not be wired
        # at the call site (the resolver runs early).
        if not getattr(FileStore, "_warned", False):
            sys.stderr.write(
                "[forest_soul_forge.security.secrets] FileStore is "
                "INSECURE: secrets stored as plaintext YAML. Use "
                "KeychainStore (macOS) or VaultWardenStore for "
                "production-grade storage.\n"
            )
            FileStore._warned = True  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # SecretStoreProtocol surface
    # ------------------------------------------------------------------

    def get(self, secret_name: str) -> str | None:
        data = self._load()
        if not isinstance(data, dict):
            return None
        value = data.get(secret_name)
        # Refuse anything that isn't a string. A YAML-loaded number,
        # bool, or list is malformed for our purposes — better to
        # surface an error than silently coerce.
        if value is None:
            return None
        if not isinstance(value, str):
            raise SecretStoreError(
                f"file backend: secret {secret_name!r} is type "
                f"{type(value).__name__}, expected string. Edit "
                f"{self._path} to fix."
            )
        return value

    def put(self, secret_name: str, secret_value: str) -> None:
        if not isinstance(secret_name, str) or not secret_name:
            raise SecretStoreError("file backend: secret_name must be a non-empty string")
        if not isinstance(secret_value, str):
            raise SecretStoreError("file backend: secret_value must be a string")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Tighten the directory perms too — defense in depth in case
        # the operator's umask is permissive.
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass

        data = self._load() if self._path.exists() else {}
        if not isinstance(data, dict):
            # File exists but isn't a YAML mapping — refuse to
            # overwrite blindly. Operator must clean up.
            raise SecretStoreError(
                f"file backend: existing {self._path} is not a YAML "
                f"mapping. Refusing to clobber. Move it aside and "
                f"retry."
            )
        data[secret_name] = secret_value
        self._write(data)

    def delete(self, secret_name: str) -> None:
        if not self._path.exists():
            return
        data = self._load()
        if not isinstance(data, dict):
            return
        if secret_name in data:
            del data[secret_name]
            self._write(data)

    def list_names(self) -> list[str]:
        data = self._load()
        if not isinstance(data, dict):
            return []
        return list(data.keys())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load(self) -> dict | None:
        if not self._path.exists():
            return {}
        # chmod-check BEFORE reading: a 644 secrets file is a
        # diagnostic problem, not a "soft" one — refuse to read so
        # the operator notices. Operator can re-tighten with
        # chmod 600.
        st = self._path.stat()
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            raise SecretStoreError(
                f"file backend: {self._path} has mode {mode:o}; "
                f"expected 600 (no group/other access). Fix with "
                f"`chmod 600 {self._path}`. Refusing to read until "
                f"perms are tight."
            )
        try:
            with self._path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise SecretStoreError(
                f"file backend: {self._path} is malformed YAML: {e}"
            ) from e

    def _write(self, data: dict) -> None:
        # Atomic-ish write: temp file + rename. Avoids a half-written
        # secrets.yaml if the process is killed mid-write.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        finally:
            # Clean up tmp if rename failed.
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
