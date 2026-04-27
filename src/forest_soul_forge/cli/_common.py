"""Shared helpers for the ``fsf`` CLI subcommands.

Per ADR-0032 — every CLI surface that needs an LLM provider builds one
locally from ``DaemonSettings`` rather than depending on a running
daemon. Keeping the construction in one place means future subcommands
(``fsf forge skill``, ``fsf benchmark run``, etc.) get the same
provider story for free.
"""
from __future__ import annotations

import os
from typing import Any


def build_provider(override: str | None = None) -> Any:
    """Construct a ModelProvider directly from ``DaemonSettings``.

    ``override='local'`` or ``'frontier'`` forces one explicitly; None
    uses ``settings.default_provider``. Raises ``SystemExit`` (which
    argparse-style CLIs treat as graceful exit code 1) on misconfig
    rather than propagating the underlying provider error.
    """
    from forest_soul_forge.daemon.config import build_settings
    from forest_soul_forge.daemon.providers.frontier import FrontierProvider
    from forest_soul_forge.daemon.providers.local import LocalProvider

    settings = build_settings()
    pick = (override or settings.default_provider).strip().lower()

    if pick == "local":
        return LocalProvider(
            base_url=settings.local_base_url,
            models=settings.local_model_map(),
            timeout_s=settings.local_timeout_s,
        )
    if pick == "frontier":
        if not settings.frontier_enabled:
            raise SystemExit(
                "Frontier provider is disabled by FSF_FRONTIER_ENABLED. "
                "Enable it or pass --provider local."
            )
        return FrontierProvider(
            enabled=True,
            base_url=settings.frontier_base_url,
            api_key=settings.frontier_api_key,
            models=settings.frontier_model_map(),
            timeout_s=settings.frontier_timeout_s,
        )
    raise SystemExit(
        f"unknown provider {pick!r}; expected 'local' or 'frontier'"
    )


def resolve_operator() -> str:
    """Best-effort operator id for forge.log + audit-chain entries.

    Falls back to ``operator`` so the value is always non-empty —
    every ``forged_by`` field needs *some* identifier even when the
    user environment is unusual (CI, container, etc.).
    """
    return os.environ.get("USER") or os.environ.get("USERNAME") or "operator"
