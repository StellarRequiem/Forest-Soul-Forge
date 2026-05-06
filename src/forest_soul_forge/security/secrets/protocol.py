"""SecretStoreProtocol — the contract every backend honors.

Per ADR-0052 Decision 2: backends are pure key-value-by-name.
Rotation, expiration, ACLs are properties of the underlying
backend (VaultWarden has its own rotation flow; macOS Keychain
has its own ACL surface). Forest treats the backend as a
black-box keyed-string store.
"""
from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable


class SecretStoreError(Exception):
    """Backend-level failure distinguishable from "secret not found.".

    Surface this for: network down, permission denied, vault
    locked, sha256 mismatch, etc. Callers (the plugin loader,
    the `fsf secret` CLI) decide whether to retry vs. fall back
    to a default.

    Returning ``None`` from get() means "the key isn't stored
    here, and that's not an error" — the plugin just doesn't have
    that secret yet, and the loader fails with a clear "operator
    must run `fsf secret put X`" message.
    """


@runtime_checkable
class SecretStoreProtocol(Protocol):
    """Read/write/delete named secrets.

    Implementations that pass the conformance test suite
    (tests/unit/test_secret_store_conformance.py) plug into
    Forest's plugin-loader path without any further glue.
    """

    name: ClassVar[str]
    """Backend identifier — surfaced in audit-chain
    secret_resolved events so an auditor can see WHICH backend
    served the secret without leaking the value. Examples: 'file',
    'keychain', 'vaultwarden'. BYO modules choose their own name
    string; recommended convention is the short snake_case form."""

    def get(self, secret_name: str) -> str | None:
        """Return the secret value, or ``None`` if not present.

        Raises ``SecretStoreError`` on backend failure (network
        down, permission denied, etc.) — distinct from None so
        the loader can decide whether to retry vs. fall back to
        a default.

        Implementations MUST NOT log secret values. Logging the
        secret_name + backend identifier is fine; logging the
        value would defeat the abstraction.
        """
        ...

    def put(self, secret_name: str, secret_value: str) -> None:
        """Write a secret. Idempotent — overwrites existing.

        Operator-driven (the ``fsf secret`` CLI is the surface);
        plugins themselves never write. Raises
        ``SecretStoreError`` on backend failure.
        """
        ...

    def delete(self, secret_name: str) -> None:
        """Remove a secret. Idempotent — deleting an absent name
        is a no-op (does NOT raise). Raises ``SecretStoreError``
        on backend failure.
        """
        ...

    def list_names(self) -> list[str]:
        """List all secret names this backend can serve.

        Used by the settings panel to surface "what does Forest
        have access to right now" without exposing values. Order
        is not guaranteed; callers sort if they need stable
        display.
        """
        ...
