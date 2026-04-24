"""Provider registry — single source of truth for "which model is active".

The registry is an in-process object held by the FastAPI app. Switching
providers via ``PUT /runtime/provider`` mutates this registry; nothing is
persisted to disk. That's deliberate — provider choice is a runtime
toggle, not a canonical artifact. A fresh daemon restart always comes up
with ``local`` active (per ADR-0008).
"""
from __future__ import annotations

from forest_soul_forge.daemon.providers.base import (
    ModelProvider,
    ProviderDisabled,
    ProviderError,
    ProviderHealth,
    ProviderStatus,
    ProviderUnavailable,
    TaskKind,
)


class UnknownProviderError(ProviderError):
    """Raised when code asks for a provider name we don't know about."""


class ProviderRegistry:
    """Holds the set of available providers and tracks the active one.

    Not thread-safe by design — the FastAPI app uses async single-writer
    discipline (one event loop). If that ever changes, add a lock here.
    """

    def __init__(
        self,
        *,
        providers: dict[str, ModelProvider],
        default: str,
    ) -> None:
        if default not in providers:
            raise UnknownProviderError(
                f"default provider {default!r} not in registry"
            )
        self._providers = dict(providers)
        self._active_name = default
        self._default_name = default

    @property
    def active_name(self) -> str:
        return self._active_name

    @property
    def default_name(self) -> str:
        return self._default_name

    def known(self) -> list[str]:
        return sorted(self._providers.keys())

    def get(self, name: str) -> ModelProvider:
        try:
            return self._providers[name]
        except KeyError as e:
            raise UnknownProviderError(
                f"unknown provider {name!r} (known: {self.known()})"
            ) from e

    def active(self) -> ModelProvider:
        return self._providers[self._active_name]

    def set_active(self, name: str) -> None:
        if name not in self._providers:
            raise UnknownProviderError(
                f"cannot activate unknown provider {name!r} (known: {self.known()})"
            )
        self._active_name = name

    def reset(self) -> None:
        """Restore the default. Useful in tests and on reload signals."""
        self._active_name = self._default_name


__all__ = [
    "ModelProvider",
    "ProviderDisabled",
    "ProviderError",
    "ProviderHealth",
    "ProviderRegistry",
    "ProviderStatus",
    "ProviderUnavailable",
    "TaskKind",
    "UnknownProviderError",
]
