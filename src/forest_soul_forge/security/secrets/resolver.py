"""resolve_secret_store() — the single entry point that callers use.

Reads ``FSF_SECRET_STORE`` env var. Recognized values:

  - ``file`` (default in T1) — plaintext YAML at
    ``~/.forest/secrets/secrets.yaml``. INSECURE; for CI/sandbox.
  - ``keychain`` (T2 — coming, raises NotImplementedError today)
  - ``vaultwarden`` (T3 — coming)
  - ``module:my_pkg.my_store.MyStore`` — BYO. Resolver imports the
    dotted path, calls the no-arg constructor, asserts the result
    implements SecretStoreProtocol, returns it.

The function caches resolved instances per-process so subsequent
calls share the same backend handle. Callers don't have to thread
the instance through their call chain.
"""
from __future__ import annotations

import importlib
import os
import threading
from typing import Any

from .file_store import FileStore
from .protocol import SecretStoreError, SecretStoreProtocol


_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


def resolve_secret_store(*, force_reload: bool = False) -> SecretStoreProtocol:
    """Return the active SecretStoreProtocol implementation.

    Reads ``FSF_SECRET_STORE`` env var; defaults to ``file``.
    Caches the resolved instance per-process; pass
    ``force_reload=True`` to bypass the cache (test seam).
    """
    backend_id = (os.environ.get("FSF_SECRET_STORE") or "file").strip()

    with _CACHE_LOCK:
        if not force_reload and backend_id in _CACHE:
            return _CACHE[backend_id]

        instance = _build(backend_id)
        # Structural protocol check — catches BYO modules that
        # forgot get/put/delete/list_names without waiting for a
        # late KeyError.
        if not isinstance(instance, SecretStoreProtocol):
            raise SecretStoreError(
                f"FSF_SECRET_STORE={backend_id!r} resolved to "
                f"{type(instance).__name__}, which does not satisfy "
                f"SecretStoreProtocol. Implement get/put/delete/"
                f"list_names + the `name` class attribute."
            )
        _CACHE[backend_id] = instance
        return instance


def _build(backend_id: str) -> SecretStoreProtocol:
    if backend_id == "file":
        return FileStore()
    if backend_id == "keychain":
        # T2 will replace this stub with a real KeychainStore.
        raise SecretStoreError(
            "FSF_SECRET_STORE=keychain not implemented yet "
            "(ADR-0052 T2). Use 'file' for now or switch to "
            "'vaultwarden' once T3 lands."
        )
    if backend_id == "vaultwarden":
        # T3 will replace this stub.
        raise SecretStoreError(
            "FSF_SECRET_STORE=vaultwarden not implemented yet "
            "(ADR-0052 T3)."
        )
    if backend_id.startswith("module:"):
        return _build_byo(backend_id[len("module:"):])

    raise SecretStoreError(
        f"FSF_SECRET_STORE={backend_id!r} not recognized. Valid: "
        f"file, keychain, vaultwarden, module:<dotted.path.Class>"
    )


def _build_byo(dotted_path: str) -> SecretStoreProtocol:
    """Import a BYO backend by dotted path. Constructor takes no args.

    Operators integrating HashiCorp Vault / AWS Secrets Manager /
    1Password Connect provide a small adapter implementing
    SecretStoreProtocol and point FSF_SECRET_STORE at it. Forest
    never ships the vendor-specific code; the operator owns the
    integration. This keeps Forest's surface narrow + lets each
    operator's threat model dictate which backend they trust.
    """
    if "." not in dotted_path:
        raise SecretStoreError(
            f"BYO backend path {dotted_path!r} must be "
            f"<module.path>.<ClassName>"
        )
    module_name, _, class_name = dotted_path.rpartition(".")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise SecretStoreError(
            f"BYO backend module {module_name!r} not importable: {e}. "
            f"Install the module + ensure it's on PYTHONPATH."
        ) from e
    try:
        cls = getattr(module, class_name)
    except AttributeError as e:
        raise SecretStoreError(
            f"BYO backend class {class_name!r} not found in "
            f"module {module_name!r}: {e}"
        ) from e
    try:
        return cls()
    except Exception as e:                     # noqa: BLE001
        raise SecretStoreError(
            f"BYO backend {dotted_path!r} constructor raised: "
            f"{type(e).__name__}: {e}"
        ) from e


def _reset_cache_for_tests() -> None:
    """Test seam — clears the per-process cache so a test can
    re-resolve after mutating FSF_SECRET_STORE. Not part of the
    public API."""
    with _CACHE_LOCK:
        _CACHE.clear()
