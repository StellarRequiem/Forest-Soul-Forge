"""Embedding adapter for the procedural-shortcut path (ADR-0054 T2).

The dispatcher's ProceduralShortcutStep (T3) calls ``embed_situation``
on every eligible operator turn to produce the query vector that
``ProceduralShortcutsTable.search_by_cosine`` matches against
stored shortcut embeddings. T1 supplied the search math; T2 supplies
the input.

Design choices documented in ADR-0054:
- Local-only by default (LocalProvider.embed → Ollama
  /api/embeddings against ``nomic-embed-text:latest``). 768-dim
  float32 vectors. Frontier providers don't get an embedding path
  in v0.1 — the provider Protocol exposes ``complete`` only;
  embedding is LocalProvider-specific.
- All errors collapse to ``EmbeddingError`` (provider-failure
  superset). The caller (T3) catches it and falls through to the
  full ``llm_think`` path — a temporary Ollama outage degrades the
  shortcut surface to "no shortcuts today" rather than breaking
  the conversation.
- Returns a normalized float32 vector. Normalization at the
  embedding seam (rather than at search time) means:
    - the table's stored vectors and the query vector are both
      pre-normalized, so cosine reduces to a single dot product
    - operators inspecting the BLOB on disk see unit vectors,
      which matches the math the search step does
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from forest_soul_forge.daemon.providers.base import ModelProvider


class EmbeddingError(Exception):
    """Embedding failed — Ollama unreachable, model missing, malformed
    response. Caller falls through to the full ``llm_think`` path."""


async def embed_situation(
    provider: "ModelProvider",
    text: str,
    *,
    model: str | None = None,
) -> np.ndarray:
    """Produce a normalized float32 embedding for ``text``.

    Args:
        provider: a ModelProvider instance. Must expose an async
            ``embed(text, model=...)`` method returning ``list[float]``.
            LocalProvider has this; FrontierProvider currently does
            not — passing a frontier provider raises EmbeddingError
            immediately rather than silently falling back.
        text: the operator-turn text to embed. Whitespace-only or
            empty input raises EmbeddingError (matching to nothing
            is a misconfiguration, not a runtime fallback).
        model: override Ollama model tag. Default
            ``nomic-embed-text:latest`` (set in LocalProvider.embed).

    Returns:
        A unit-norm 1-D numpy array (dtype=float32). Shape depends on
        the model — typically (768,) for nomic-embed-text.

    Raises:
        EmbeddingError on any provider failure or malformed response.
    """
    if not isinstance(text, str) or not text.strip():
        raise EmbeddingError(
            "embed_situation requires non-empty text; got "
            f"{type(text).__name__!r} value {text!r}"
        )
    if not hasattr(provider, "embed"):
        raise EmbeddingError(
            f"provider {provider.name!r} has no embed() method. "
            f"Procedural shortcuts require a local embedding model "
            f"(currently only LocalProvider implements embed). "
            f"Frontier providers fall through to llm_think."
        )

    try:
        raw = await provider.embed(text, model=model)
    except Exception as e:  # noqa: BLE001 — provider-specific exception variety
        raise EmbeddingError(
            f"provider {provider.name!r} embed() raised "
            f"{type(e).__name__}: {e}"
        ) from e

    if not isinstance(raw, list) or not raw:
        raise EmbeddingError(
            f"provider {provider.name!r} embed() returned unexpected "
            f"shape: {type(raw).__name__} len={len(raw) if hasattr(raw, '__len__') else 'n/a'}"
        )

    arr = np.asarray(raw, dtype=np.float32)
    if arr.ndim != 1:
        raise EmbeddingError(
            f"provider {provider.name!r} embed() returned non-1D "
            f"array shape {arr.shape}"
        )
    norm = np.linalg.norm(arr)
    if norm == 0:
        # All-zero vector. Either the model output something pathological
        # (rare) or the input collapsed to nothing meaningful. Refuse —
        # the cosine math against any other unit vector would be 0,
        # never matching anything; better to fail loudly so the caller
        # falls through to llm_think with a clear error in the audit
        # chain.
        raise EmbeddingError(
            "embedding is the zero vector — model output is degenerate "
            "for this input; falling through to llm_think"
        )
    return (arr / norm).astype(np.float32, copy=False)
