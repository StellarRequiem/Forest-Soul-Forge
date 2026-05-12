"""AgentKeyStore — per-agent ed25519 private key storage (ADR-0049).

Thin wrapper over the ADR-0052 :class:`SecretStoreProtocol`. Holds
bytes (private keys) by base64-encoding them as strings before
delegating to the underlying secret store. Read paths decode back
to bytes. Names are prefixed with ``forest_agent_key:`` so the
agent-key namespace is visible in backends like macOS Keychain that
list secrets by name.

## Surface

  - ``store(instance_id, private_key_bytes)`` — put. Idempotent.
  - ``fetch(instance_id) -> bytes | None`` — get. ``None`` means
    "no key stored for this agent."
  - ``delete(instance_id) -> bool`` — remove. Returns False if no
    key existed (idempotent on the wire); True if a key was
    actually deleted.
  - ``list_agent_ids() -> list[str]`` — every agent that has a
    key in the store. Useful for the verifier to enumerate which
    chain events it can check vs. which agents lost their key.

## Error model

Two distinct exceptions:

  - :class:`AgentKeyNotFoundError` — raised by fetch when the
    caller used ``fetch_strict`` (the must-have-a-key path).
    Callers that tolerate missing keys use plain ``fetch`` which
    returns None.
  - :class:`AgentKeyStoreError` — wraps any underlying
    :class:`SecretStoreError` so callers don't have to import
    from the secrets package to catch backend-level failures.

## Caveat: base64 is encoding, not encryption

The confidentiality of the private key is the backend's job. On
macOS Keychain (default), the OS enforces access control + hardware-
backed encryption. On the file backend, ``chmod 600`` and the
operator-visible insecurity warning are the protections in place.
A future encrypted_file backend could be added to ADR-0052 to give
the file path confidentiality without OS Keychain integration.
"""
from __future__ import annotations

import base64
import threading
from typing import ClassVar

from forest_soul_forge.security.secrets import (
    SecretStoreError,
    SecretStoreProtocol,
    resolve_secret_store,
)


# ---- secret-name prefix ---------------------------------------------------

#: Prefix every agent-key secret name carries. Makes the agent-key
#: namespace visible in any backend that lists secrets by name
#: (macOS Keychain Access, ``fsf secret list``, etc.). External
#: consumers querying the secret store by name use this prefix as a
#: stable contract; the prefix is part of the on-disk format and
#: must not change without a migration.
SECRET_NAME_PREFIX: str = "forest_agent_key:"


# ---- exceptions -----------------------------------------------------------


class AgentKeyStoreError(Exception):
    """Wraps backend-level failures (network, permission, vault
    locked) so callers don't have to import from the secrets
    package to catch them. The original ``SecretStoreError`` is
    chained via ``__cause__``."""


class AgentKeyNotFoundError(AgentKeyStoreError):
    """The caller asked for an agent's key via ``fetch_strict`` and
    no key was found. Use plain ``fetch`` to get ``None`` instead.
    """


# ---- store ----------------------------------------------------------------


class AgentKeyStore:
    """Per-agent ed25519 private-key storage.

    Thread-safe at the wrapper layer (internal lock around the
    backend operations). Backends themselves may serialize via
    their own locks; the wrapper's lock is short and bounds
    concurrent put/get against the same key.
    """

    name: ClassVar[str] = "agent_key_store"

    def __init__(self, secret_store: SecretStoreProtocol | None = None) -> None:
        """Build the store on top of an existing secret-store
        backend. ``None`` (default) resolves via
        :func:`resolve_secret_store` — the same env-driven backend
        the plugin loader uses for plugin secrets.
        """
        self._backend: SecretStoreProtocol = (
            secret_store if secret_store is not None
            else resolve_secret_store()
        )
        self._lock = threading.RLock()

    # -- secret-name helpers -----------------------------------------------

    @staticmethod
    def _secret_name(instance_id: str) -> str:
        if not isinstance(instance_id, str) or not instance_id:
            raise AgentKeyStoreError(
                f"instance_id must be a non-empty string, got {instance_id!r}"
            )
        # Guard against accidental delimiter collision. The prefix
        # ends in ':' — disallowing ':' in instance_id keeps the
        # name unambiguous when extracting the agent id back out.
        if ":" in instance_id:
            raise AgentKeyStoreError(
                f"instance_id must not contain ':' (would collide with the "
                f"namespace delimiter); got {instance_id!r}"
            )
        return SECRET_NAME_PREFIX + instance_id

    @staticmethod
    def _agent_id_from_name(secret_name: str) -> str | None:
        """Reverse of ``_secret_name`` — returns None for non-agent-
        key secret names so callers can filter list_names output."""
        if not secret_name.startswith(SECRET_NAME_PREFIX):
            return None
        return secret_name[len(SECRET_NAME_PREFIX):]

    # -- operations --------------------------------------------------------

    def store(self, instance_id: str, private_key_bytes: bytes) -> None:
        """Put (or overwrite) the agent's private key.

        Idempotent — re-storing the same key is a no-op on the
        wire. Overwriting with different bytes IS allowed at the
        wrapper layer (the backend's put is idempotent); the
        ADR-0049 birth pipeline calls this exactly once per agent
        and the keypair is identity-bound, so overwriting in
        practice means a bug. Callers should treat re-store as
        suspicious unless explicitly part of a key-rotation flow
        (which ADR-0049 defers).
        """
        if not isinstance(private_key_bytes, (bytes, bytearray)):
            raise AgentKeyStoreError(
                f"private_key_bytes must be bytes, got "
                f"{type(private_key_bytes).__name__}"
            )
        encoded = base64.b64encode(bytes(private_key_bytes)).decode("ascii")
        with self._lock:
            try:
                self._backend.put(self._secret_name(instance_id), encoded)
            except SecretStoreError as e:
                raise AgentKeyStoreError(
                    f"backend put failed for agent {instance_id!r}"
                ) from e

    def fetch(self, instance_id: str) -> bytes | None:
        """Return the agent's private key bytes, or ``None`` if not
        stored. Use ``fetch_strict`` to raise instead."""
        with self._lock:
            try:
                encoded = self._backend.get(self._secret_name(instance_id))
            except SecretStoreError as e:
                raise AgentKeyStoreError(
                    f"backend get failed for agent {instance_id!r}"
                ) from e
        if encoded is None:
            return None
        try:
            return base64.b64decode(encoded.encode("ascii"), validate=True)
        except (ValueError, TypeError) as e:
            # Stored value was non-base64 — backend tampering or
            # corruption. Surface as AgentKeyStoreError; do NOT
            # silently return None (that would hide tampering).
            raise AgentKeyStoreError(
                f"stored value for agent {instance_id!r} is not valid base64"
            ) from e

    def fetch_strict(self, instance_id: str) -> bytes:
        """Like ``fetch`` but raises ``AgentKeyNotFoundError`` when
        no key is stored. Sign-on-emit paths use this — a missing
        key on an active agent is a bug, not a benign None."""
        result = self.fetch(instance_id)
        if result is None:
            raise AgentKeyNotFoundError(
                f"no private key stored for agent {instance_id!r}"
            )
        return result

    def delete(self, instance_id: str) -> bool:
        """Remove the agent's key. Returns True if a key was
        actually deleted, False if no key existed (idempotent at
        the wire). Use for agent-archive cleanup if the operator
        wants to scrub the key surface; otherwise the key stays as
        the verifier needs it to verify the agent's historical
        chain entries."""
        with self._lock:
            # The Protocol's delete is idempotent on absence, so we
            # need a pre-check to return True-vs-False honestly.
            try:
                pre = self._backend.get(self._secret_name(instance_id))
                self._backend.delete(self._secret_name(instance_id))
            except SecretStoreError as e:
                raise AgentKeyStoreError(
                    f"backend delete failed for agent {instance_id!r}"
                ) from e
        return pre is not None

    def list_agent_ids(self) -> list[str]:
        """Every agent_id with a key in the backend. Useful for the
        verifier + the operator's "what keys does Forest have"
        view. Backend list_names ordering isn't guaranteed; this
        method sorts for stable display."""
        with self._lock:
            try:
                names = self._backend.list_names()
            except SecretStoreError as e:
                raise AgentKeyStoreError(
                    "backend list_names failed"
                ) from e
        agent_ids = []
        for n in names:
            agent_id = self._agent_id_from_name(n)
            if agent_id is not None:
                agent_ids.append(agent_id)
        agent_ids.sort()
        return agent_ids


# ---- factory --------------------------------------------------------------

_RESOLVED_CACHE: dict[str, AgentKeyStore] = {}
_RESOLVED_LOCK = threading.RLock()


def resolve_agent_key_store(
    secret_store: SecretStoreProtocol | None = None,
) -> AgentKeyStore:
    """Get a process-cached ``AgentKeyStore`` instance.

    Pass an explicit ``secret_store`` to bypass the cache (used by
    tests that want a tmpdir-backed FileStore). Default behavior
    leans on the ADR-0052 resolver to pick the right backend for
    the current platform + env.
    """
    if secret_store is not None:
        # Caller supplied an explicit backend — return a fresh
        # instance, don't cache (the test scenarios that pass
        # explicit backends expect isolation).
        return AgentKeyStore(secret_store=secret_store)
    with _RESOLVED_LOCK:
        if "default" not in _RESOLVED_CACHE:
            _RESOLVED_CACHE["default"] = AgentKeyStore()
        return _RESOLVED_CACHE["default"]
