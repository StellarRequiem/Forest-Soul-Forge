"""Registry error classes — extracted in R4 so the table accessors can
import them without going through the Registry façade (which would
create a circular import: registry.registry → tables → registry.registry).

All five error types are re-exported by ``registry/registry.py`` for
back-compat with the existing ``from forest_soul_forge.registry.registry
import UnknownAgentError`` callers, so this module is internal to the
registry package and downstream code should not import directly from
here.
"""
from __future__ import annotations


class RegistryError(Exception):
    """Base class for registry failures."""


class SchemaMismatchError(RegistryError):
    """Raised when an existing DB file's schema_version doesn't match ours."""


class UnknownAgentError(RegistryError):
    pass


class DuplicateInstanceError(RegistryError):
    pass


class IdempotencyMismatchError(RegistryError):
    """Raised when a cached key is replayed with a different request body.

    Per ADR-0007: an idempotency key represents a specific request, not
    an endpoint. Reusing the key with a mutated body is almost always a
    client bug (two different requests sharing a generated UUID) and
    must not silently short-circuit to the cached response.
    """

    def __init__(self, key: str, endpoint: str) -> None:
        super().__init__(
            f"idempotency key {key!r} previously used on {endpoint} with a "
            f"different request body"
        )
        self.key = key
        self.endpoint = endpoint
