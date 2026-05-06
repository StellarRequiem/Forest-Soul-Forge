"""ADR-0054 T2 (B179) — embedding adapter tests.

Coverage:
- LocalProvider.embed() request shape (model + prompt) + response
  parsing (extracts ``embedding`` field, coerces to floats)
- LocalProvider.embed() unreachable / non-2xx / malformed-shape
  failure modes
- embed_situation():
  - happy path returns unit-norm float32 1-D array
  - rejects empty / whitespace text
  - rejects providers without embed()
  - wraps provider exceptions as EmbeddingError
  - rejects malformed response shapes
  - rejects all-zero vectors (degenerate output)

All tests stub httpx so they run in the sandbox without Ollama.
"""
from __future__ import annotations

from unittest import mock

import numpy as np
import pytest

from forest_soul_forge.core.memory.procedural_embedding import (
    EmbeddingError,
    embed_situation,
)
from forest_soul_forge.daemon.providers.base import (
    ProviderError,
    ProviderUnavailable,
    TaskKind,
)
from forest_soul_forge.daemon.providers.local import LocalProvider


# ---------------------------------------------------------------------------
# Async test helper — pytest-asyncio in the sandbox
# ---------------------------------------------------------------------------

import asyncio
import functools


def asynctest(fn):
    """Inline async-test decorator. pytest-asyncio is already a
    dev dep in this repo but we keep this lightweight to avoid the
    runner discovery cost."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------------
# Mocked-httpx response builder
# ---------------------------------------------------------------------------

def _mock_async_client(json_response=None, raise_for_status=None, request_error=None):
    """Build an httpx.AsyncClient mock whose .post returns the
    requested JSON or raises the requested error."""
    resp = mock.MagicMock()
    if raise_for_status is not None:
        # Set a real-shaped HTTPStatusError
        import httpx
        resp.status_code = raise_for_status
        resp.text = "mocked error"
        def _raise():
            raise httpx.HTTPStatusError(
                "mocked",
                request=mock.MagicMock(),
                response=resp,
            )
        resp.raise_for_status = _raise
    else:
        resp.raise_for_status = mock.MagicMock()
        resp.json = mock.MagicMock(return_value=json_response or {})

    client = mock.AsyncMock()
    if request_error is not None:
        async def _post(*a, **kw):
            raise request_error
        client.post = _post
    else:
        async def _post(*a, **kw):
            return resp
        client.post = _post
    client.__aenter__ = mock.AsyncMock(return_value=client)
    client.__aexit__ = mock.AsyncMock(return_value=None)
    return client


def _provider() -> LocalProvider:
    return LocalProvider(
        base_url="http://127.0.0.1:11434",
        models={
            TaskKind.CONVERSATION: "qwen2.5-coder:7b",
        },
    )


# ===========================================================================
# LocalProvider.embed() — request/response shape
# ===========================================================================

@asynctest
async def test_embed_default_model_and_payload_shape():
    """When model arg is omitted, LocalProvider falls back to
    nomic-embed-text:latest. POST goes to /api/embeddings with
    {"model": ..., "prompt": ...}."""
    captured: dict = {}

    import httpx

    class _Resp:
        def __init__(self):
            self.status_code = 200

        def raise_for_status(self): pass

        def json(self):
            return {"embedding": [0.1, 0.2, 0.3]}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    p = _provider()
    with mock.patch.object(httpx, "AsyncClient", _Client):
        out = await p.embed("hello world")

    assert captured["url"].endswith("/api/embeddings")
    assert captured["json"]["model"] == "nomic-embed-text:latest"
    assert captured["json"]["prompt"] == "hello world"
    assert out == [0.1, 0.2, 0.3]


@asynctest
async def test_embed_explicit_model_override():
    captured: dict = {}

    import httpx

    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"embedding": [0.5, 0.5]}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            captured["model"] = json["model"]
            return _Resp()

    p = _provider()
    with mock.patch.object(httpx, "AsyncClient", _Client):
        await p.embed("x", model="some-other-embed-model")
    assert captured["model"] == "some-other-embed-model"


@asynctest
async def test_embed_request_error_raises_unavailable():
    """httpx RequestError (Ollama not running) → ProviderUnavailable
    so callers can show 'is your local model up?' rather than 500."""
    import httpx

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            raise httpx.ConnectError("Connection refused")

    p = _provider()
    with mock.patch.object(httpx, "AsyncClient", _Client):
        with pytest.raises(ProviderUnavailable) as exc:
            await p.embed("hi")
    assert "unreachable" in str(exc.value).lower()


@asynctest
async def test_embed_http_error_raises_provider_error():
    """Non-2xx response (e.g., model not pulled) → ProviderError
    distinct from ProviderUnavailable so the operator sees a
    different message in the chat / audit surface."""
    import httpx

    class _Resp:
        def __init__(self):
            self.status_code = 404
            self.text = "model 'some-other-embed-model' not found"

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "404", request=mock.MagicMock(), response=self,
            )

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None): return _Resp()

    p = _provider()
    with mock.patch.object(httpx, "AsyncClient", _Client):
        with pytest.raises(ProviderError) as exc:
            await p.embed("hi", model="some-other-embed-model")
    assert "404" in str(exc.value)


@asynctest
async def test_embed_malformed_response_raises_provider_error():
    """Ollama returning anything that isn't {'embedding': [...]} →
    ProviderError, not silently empty result."""
    import httpx

    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"unexpected": "shape"}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None): return _Resp()

    p = _provider()
    with mock.patch.object(httpx, "AsyncClient", _Client):
        with pytest.raises(ProviderError):
            await p.embed("hi")


@asynctest
async def test_embed_non_float_entries_raises_provider_error():
    import httpx

    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"embedding": ["not", "a", "number"]}

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None): return _Resp()

    p = _provider()
    with mock.patch.object(httpx, "AsyncClient", _Client):
        with pytest.raises(ProviderError) as exc:
            await p.embed("hi")
    assert "non-float" in str(exc.value)


# ===========================================================================
# embed_situation() — wrapper helper
# ===========================================================================

class _FakeProvider:
    """Stand-in ModelProvider with a controllable embed() implementation."""

    name = "fake-local"

    def __init__(self, return_value=None, raises=None):
        self._ret = return_value
        self._raises = raises
        self.calls: list[tuple[str, str | None]] = []

    async def embed(self, text, *, model=None):
        self.calls.append((text, model))
        if self._raises is not None:
            raise self._raises
        return self._ret


@asynctest
async def test_embed_situation_returns_unit_norm_float32():
    """Happy path: provider.embed returns a list; helper returns a
    unit-norm 1-D float32 numpy array."""
    p = _FakeProvider(return_value=[3.0, 4.0])  # length 5
    out = await embed_situation(p, "anything")
    assert out.dtype == np.float32
    assert out.ndim == 1
    assert abs(np.linalg.norm(out) - 1.0) < 1e-6
    np.testing.assert_array_almost_equal(out, np.array([0.6, 0.8]))


@asynctest
async def test_embed_situation_passes_model_through():
    p = _FakeProvider(return_value=[1.0, 0.0])
    await embed_situation(p, "x", model="custom-embed:v2")
    assert p.calls == [("x", "custom-embed:v2")]


@asynctest
async def test_embed_situation_rejects_empty_text():
    p = _FakeProvider(return_value=[1.0])
    for bad in ["", "   ", "\n\t\n"]:
        with pytest.raises(EmbeddingError) as exc:
            await embed_situation(p, bad)
        assert "non-empty text" in str(exc.value)
    assert p.calls == []   # never reached the provider


@asynctest
async def test_embed_situation_rejects_non_string_text():
    p = _FakeProvider(return_value=[1.0])
    with pytest.raises(EmbeddingError):
        await embed_situation(p, None)            # type: ignore[arg-type]
    with pytest.raises(EmbeddingError):
        await embed_situation(p, 12345)           # type: ignore[arg-type]


@asynctest
async def test_embed_situation_rejects_providers_without_embed():
    """A frontier provider (or any future provider) that doesn't
    implement embed() must raise EmbeddingError immediately so the
    caller (T3) falls through to llm_think rather than crashing."""
    class _NoEmbedProvider:
        name = "frontier-fake"

    with pytest.raises(EmbeddingError) as exc:
        await embed_situation(_NoEmbedProvider(), "hi")
    assert "no embed()" in str(exc.value)


@asynctest
async def test_embed_situation_wraps_provider_exception():
    """ProviderUnavailable / ProviderError / any other provider
    exception → EmbeddingError. Caller catches one type, not many."""
    p = _FakeProvider(raises=ProviderUnavailable("Ollama down"))
    with pytest.raises(EmbeddingError) as exc:
        await embed_situation(p, "hi")
    assert "ProviderUnavailable" in str(exc.value)
    assert "Ollama down" in str(exc.value)


@asynctest
async def test_embed_situation_rejects_malformed_response():
    for bad in [None, [], "not a list", {"not": "list"}]:
        p = _FakeProvider(return_value=bad)
        with pytest.raises(EmbeddingError):
            await embed_situation(p, "hi")


@asynctest
async def test_embed_situation_rejects_zero_vector():
    """Degenerate embedding (all zeros) would never match anything
    via cosine. Refuse loudly so the caller falls through to
    llm_think rather than silently returning a vector that excludes
    every shortcut."""
    p = _FakeProvider(return_value=[0.0, 0.0, 0.0])
    with pytest.raises(EmbeddingError) as exc:
        await embed_situation(p, "hi")
    assert "zero vector" in str(exc.value)
