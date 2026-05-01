"""Unit tests for daemon/providers/local.py and frontier.py.

Coverage was 0 unit tests at Phase A audit (2026-04-30, finding T-9).
The providers were exercised only end-to-end through the daemon's
integration tests, which masks failure modes (e.g. malformed upstream
response, network disconnect mid-call).

Strategy: stub httpx.AsyncClient.post / get with mocked responses so
we exercise the provider's full error-handling matrix:
  - happy path (2xx + valid body)
  - HTTP status error (5xx etc.)
  - request error (network unreachable)
  - malformed response shape
  - frontier-specific gates (disabled / no API key)
"""
from __future__ import annotations

import asyncio
from unittest import mock

import pytest

httpx = pytest.importorskip("httpx")

from forest_soul_forge.daemon.providers import (
    ProviderDisabled,
    ProviderError,
    ProviderStatus,
    ProviderUnavailable,
    TaskKind,
)
from forest_soul_forge.daemon.providers.frontier import FrontierProvider
from forest_soul_forge.daemon.providers.local import LocalProvider


def _run(coro):
    return asyncio.run(coro)


def _local() -> LocalProvider:
    return LocalProvider(
        base_url="http://localhost:11434",
        models={
            TaskKind.CONVERSATION: "llama3:8b",
            TaskKind.GENERATE: "qwen2.5-coder:7b",
        },
    )


def _frontier(*, enabled=True, api_key="test-key") -> FrontierProvider:
    return FrontierProvider(
        enabled=enabled,
        base_url="https://api.example.com",
        api_key=api_key,
        models={
            TaskKind.CONVERSATION: "claude-sonnet-4",
            TaskKind.GENERATE: "claude-opus-4",
        },
    )


class _MockResponse:
    """httpx-compatible Response stub for our tests."""
    def __init__(self, *, status_code=200, json_body=None, text="", raise_status=False):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text
        self._raise = raise_status

    def raise_for_status(self):
        if self._raise or self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=mock.Mock(),
                response=self,
            )

    def json(self):
        return self._json


class _MockAsyncClient:
    """Context-managed httpx client stub."""
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        if self._raises:
            raise self._raises
        return self._response

    async def get(self, url, **kwargs):
        if self._raises:
            raise self._raises
        return self._response


# ===========================================================================
# LocalProvider.complete
# ===========================================================================
class TestLocalProviderComplete:
    def test_happy_path_returns_response_text(self):
        client = _MockAsyncClient(
            response=_MockResponse(json_body={"response": "hello world"}),
        )
        with mock.patch("httpx.AsyncClient", return_value=client):
            text = _run(_local().complete("hi"))
        assert text == "hello world"

    def test_routes_to_correct_model_for_task_kind(self):
        captured: dict = {}
        captured_resp = _MockResponse(json_body={"response": "out"})

        class _Capturing(_MockAsyncClient):
            async def post(self, url, **kwargs):
                captured["payload"] = kwargs.get("json")
                return captured_resp

        with mock.patch("httpx.AsyncClient", return_value=_Capturing()):
            _run(_local().complete("x", task_kind=TaskKind.GENERATE))
        assert captured["payload"]["model"] == "qwen2.5-coder:7b"

    def test_falls_back_to_conversation_model_when_task_missing(self):
        """LocalProvider's models dict only has CONVERSATION + GENERATE.
        Asking for SAFETY_CHECK should fall back to the CONVERSATION model."""
        captured: dict = {}
        captured_resp = _MockResponse(json_body={"response": "out"})

        class _Capturing(_MockAsyncClient):
            async def post(self, url, **kwargs):
                captured["payload"] = kwargs.get("json")
                return captured_resp

        with mock.patch("httpx.AsyncClient", return_value=_Capturing()):
            _run(_local().complete("x", task_kind=TaskKind.SAFETY_CHECK))
        assert captured["payload"]["model"] == "llama3:8b"

    def test_system_prompt_threaded_into_payload(self):
        captured: dict = {}
        resp = _MockResponse(json_body={"response": "out"})

        class _Capturing(_MockAsyncClient):
            async def post(self, url, **kwargs):
                captured["payload"] = kwargs.get("json")
                return resp

        with mock.patch("httpx.AsyncClient", return_value=_Capturing()):
            _run(_local().complete("user prompt", system="be helpful"))
        assert captured["payload"]["system"] == "be helpful"

    def test_max_tokens_routed_to_options_num_predict(self):
        captured: dict = {}
        resp = _MockResponse(json_body={"response": "out"})

        class _Capturing(_MockAsyncClient):
            async def post(self, url, **kwargs):
                captured["payload"] = kwargs.get("json")
                return resp

        with mock.patch("httpx.AsyncClient", return_value=_Capturing()):
            _run(_local().complete("x", max_tokens=512))
        assert captured["payload"]["options"]["num_predict"] == 512

    def test_kwargs_options_passthrough(self):
        captured: dict = {}
        resp = _MockResponse(json_body={"response": "out"})

        class _Capturing(_MockAsyncClient):
            async def post(self, url, **kwargs):
                captured["payload"] = kwargs.get("json")
                return resp

        with mock.patch("httpx.AsyncClient", return_value=_Capturing()):
            _run(_local().complete("x", temperature=0.7, top_p=0.95, seed=42))
        opts = captured["payload"]["options"]
        assert opts["temperature"] == 0.7
        assert opts["top_p"] == 0.95
        assert opts["seed"] == 42

    def test_request_error_raises_provider_unavailable(self):
        client = _MockAsyncClient(raises=httpx.RequestError("connection refused"))
        with mock.patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ProviderUnavailable, match="unreachable"):
                _run(_local().complete("x"))

    def test_http_status_error_raises_provider_error(self):
        client = _MockAsyncClient(
            response=_MockResponse(status_code=500, text="boom"),
        )
        with mock.patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ProviderError, match="500"):
                _run(_local().complete("x"))

    def test_malformed_response_raises_provider_error(self):
        """No 'response' key, or non-string value."""
        client = _MockAsyncClient(
            response=_MockResponse(json_body={"unexpected": "shape"}),
        )
        with mock.patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ProviderError, match="unexpected"):
                _run(_local().complete("x"))


# ===========================================================================
# LocalProvider.healthcheck
# ===========================================================================
class TestLocalProviderHealthcheck:
    def test_ok_when_all_models_loaded(self):
        client = _MockAsyncClient(response=_MockResponse(json_body={
            "models": [
                {"name": "llama3:8b"},
                {"name": "qwen2.5-coder:7b"},
            ],
        }))
        with mock.patch("httpx.AsyncClient", return_value=client):
            h = _run(_local().healthcheck())
        assert h.status == ProviderStatus.OK
        assert h.error is None

    def test_degraded_when_models_missing(self):
        client = _MockAsyncClient(response=_MockResponse(json_body={
            "models": [{"name": "llama3:8b"}],  # qwen2.5-coder missing
        }))
        with mock.patch("httpx.AsyncClient", return_value=client):
            h = _run(_local().healthcheck())
        assert h.status == ProviderStatus.DEGRADED
        assert "qwen2.5-coder:7b" in (h.error or "")

    def test_unreachable_on_request_error(self):
        client = _MockAsyncClient(raises=httpx.RequestError("conn refused"))
        with mock.patch("httpx.AsyncClient", return_value=client):
            h = _run(_local().healthcheck())
        assert h.status == ProviderStatus.UNREACHABLE
        assert "conn refused" in h.error

    def test_degraded_on_http_status_error(self):
        client = _MockAsyncClient(
            response=_MockResponse(status_code=503),
        )
        with mock.patch("httpx.AsyncClient", return_value=client):
            h = _run(_local().healthcheck())
        assert h.status == ProviderStatus.DEGRADED
        assert "503" in (h.error or "")


# ===========================================================================
# LocalProvider — model-dict isolation
# ===========================================================================
class TestLocalProviderModels:
    def test_models_returns_copy(self):
        """Caller mutating the returned dict must not affect provider state."""
        p = _local()
        d1 = p.models
        d1[TaskKind.CONVERSATION] = "INJECTED"
        assert p.models[TaskKind.CONVERSATION] == "llama3:8b"

    def test_base_url_strips_trailing_slash(self):
        p = LocalProvider(
            base_url="http://localhost:11434/",
            models={TaskKind.CONVERSATION: "x"},
        )
        assert p.base_url == "http://localhost:11434"


# ===========================================================================
# FrontierProvider.complete — gate matrix
# ===========================================================================
class TestFrontierProviderComplete:
    def test_disabled_provider_raises(self):
        p = _frontier(enabled=False)
        with pytest.raises(ProviderDisabled, match="disabled"):
            _run(p.complete("x"))

    def test_enabled_no_api_key_raises(self):
        p = _frontier(enabled=True, api_key=None)
        with pytest.raises(ProviderDisabled, match="API key"):
            _run(p.complete("x"))

    def test_happy_path_returns_message_content(self):
        client = _MockAsyncClient(response=_MockResponse(json_body={
            "choices": [{"message": {"content": "frontier reply"}}],
        }))
        with mock.patch("httpx.AsyncClient", return_value=client):
            text = _run(_frontier().complete("hi"))
        assert text == "frontier reply"

    def test_request_error_raises_unavailable(self):
        client = _MockAsyncClient(raises=httpx.RequestError("dns failure"))
        with mock.patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ProviderUnavailable):
                _run(_frontier().complete("x"))

    def test_http_status_error_raises_provider_error(self):
        client = _MockAsyncClient(
            response=_MockResponse(status_code=429, text="rate limited"),
        )
        with mock.patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ProviderError, match="429"):
                _run(_frontier().complete("x"))

    def test_no_choices_raises_provider_error(self):
        client = _MockAsyncClient(
            response=_MockResponse(json_body={"choices": []}),
        )
        with mock.patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ProviderError, match="no choices"):
                _run(_frontier().complete("x"))

    def test_malformed_message_shape_raises(self):
        client = _MockAsyncClient(
            response=_MockResponse(json_body={
                "choices": [{"message": {"unrelated": "field"}}],
            }),
        )
        with mock.patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ProviderError, match="unexpected"):
                _run(_frontier().complete("x"))

    def test_authorization_header_set_when_key_present(self):
        captured = {}
        resp = _MockResponse(json_body={"choices": [{"message": {"content": "x"}}]})

        class _Capturing(_MockAsyncClient):
            async def post(self, url, **kwargs):
                captured["headers"] = kwargs.get("headers")
                return resp

        with mock.patch("httpx.AsyncClient", return_value=_Capturing()):
            _run(_frontier(api_key="secret").complete("x"))
        assert captured["headers"]["Authorization"] == "Bearer secret"


# ===========================================================================
# FrontierProvider.healthcheck
# ===========================================================================
class TestFrontierProviderHealthcheck:
    def test_disabled_returns_disabled_status_no_error(self):
        p = _frontier(enabled=False)
        h = _run(p.healthcheck())
        assert h.status == ProviderStatus.DISABLED
        assert h.error is None
        assert h.details["enabled"] is False

    def test_enabled_no_key_returns_disabled_with_error(self):
        p = _frontier(enabled=True, api_key=None)
        h = _run(p.healthcheck())
        assert h.status == ProviderStatus.DISABLED
        assert "no API key" in h.error
        assert h.details["has_api_key"] is False

    def test_enabled_with_key_reports_ok_without_pinging(self):
        """The provider deliberately does NOT ping the hosted API just
        to report health — would burn credits on every /healthz."""
        p = _frontier(enabled=True, api_key="secret")
        # Note: no httpx mock — if it tried to call out, this would fail.
        h = _run(p.healthcheck())
        assert h.status == ProviderStatus.OK
        assert h.details["has_api_key"] is True


# ===========================================================================
# FrontierProvider — auth header isolation when no key
# ===========================================================================
class TestFrontierProviderAuth:
    def test_no_api_key_no_auth_header(self):
        """The _auth_headers helper returns empty when no key — avoids
        sending a malformed 'Authorization: Bearer None' header."""
        p = _frontier(enabled=True, api_key=None)
        # _auth_headers is private but the contract is test-relevant.
        assert p._auth_headers() == {}

    def test_models_returns_copy(self):
        p = _frontier()
        d1 = p.models
        d1[TaskKind.CONVERSATION] = "INJECTED"
        assert p.models[TaskKind.CONVERSATION] == "claude-sonnet-4"
