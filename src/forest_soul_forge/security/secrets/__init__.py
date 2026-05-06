"""Pluggable secrets storage for MCP plugins (ADR-0052).

Plugins declare ``required_secrets`` in plugin.yaml; the loader
resolves each secret at server-launch time before setting
``FSF_MCP_AUTH`` (or per-secret env vars) on the spawned subprocess.
This subpackage abstracts the backend choice so an operator can
pick where secrets live without forking Forest:

  - macOS Keychain (T2 — coming)
  - VaultWarden  (T3 — coming)
  - Plaintext file at ``~/.forest/secrets/secrets.yaml``
    (T1 — this module; INSECURE; for CI / sandbox use)
  - BYO via ``FSF_SECRET_STORE=module:my.pkg.MyStore``

T1 scope (B167):
  - SecretStoreProtocol ABC (the contract subsequent backends honor)
  - SecretStoreError exception
  - FileStore reference implementation
  - resolve_secret_store() reading FSF_SECRET_STORE env var
"""
from __future__ import annotations

from .file_store import FileStore
from .protocol import SecretStoreError, SecretStoreProtocol
from .resolver import resolve_secret_store

__all__ = [
    "FileStore",
    "SecretStoreError",
    "SecretStoreProtocol",
    "resolve_secret_store",
]
