"""B225 / ADR-0055 universality — HTTP transport in mcp_call.v1.

Verifies the new HTTP/HTTPS branch routes JSON-RPC over HTTP POST,
preserves all the response-handling paths (success, JSON-RPC error,
malformed JSON, transport error, auth header substitution), and
keeps stdio behavior untouched.

Tests use monkeypatched httpx.AsyncClient so they don't bind real
sockets. The HTTP branch's only external dependency is httpx; we
inject a stub at the right place.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest import mock

import pytest

from forest_soul_forge.tools.builtin.mcp_call import McpCallError


class _FakeResponse:
    def __init__(self, status_code: int, body: Any, text: str | None = None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)
    def json(self):
        if isinstance(self._body, dict):
            return self._body
        raise json.JSONDecodeError("not json", self.text, 0)


class _FakeAsyncClient:
    """Stub for httpx.AsyncClient. Captures the post() call and returns
    the response the test configured."""

    captured: dict = {}

    def __init__(self, response, raise_exception=None, timeout=None):
        self._response = response
        self._raise = raise_exception
        self.timeout = timeout

    @classmethod
    def factory(cls, response=None, raise_exception=None):
        """Returns a callable shaped like httpx.AsyncClient(timeout=...).
        The returned object is an async context manager."""
        def _ctor(timeout=None, **kw):
            return cls(response, raise_exception, timeout=timeout)
        return _ctor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeAsyncClient.captured = {
            "url": url, "json": json, "headers": headers or {},
        }
        if self._raise is not None:
            raise self._raise
        return self._response


def _call_execute_http(monkeypatch, server_cfg: dict, response=None, raise_exception=None):
    """Drive a minimal McpCallTool.execute on a synthetic HTTP server
    config. Returns either the ToolResult or raises whatever execute
    raises."""
    from forest_soul_forge.tools.builtin import mcp_call as mc

    # Patch httpx.AsyncClient inside mcp_call's import site.
    import httpx
    fake_factory = _FakeAsyncClient.factory(response=response, raise_exception=raise_exception)
    monkeypatch.setattr(httpx, "AsyncClient", fake_factory)

    # Build a registry that resolves the server name to our HTTP config.
    server_name = "test_http_server"
    registry = {server_name: server_cfg}
    monkeypatch.setattr(
        mc, "_load_registry", lambda *a, **kw: registry,
    )

    tool = mc.McpCallTool()

    # Minimal ctx shape — McpCallTool reads only a handful of fields.
    ctx = mock.MagicMock()
    ctx.constraints = {"allowed_mcp_servers": (server_name,)}
    ctx.secrets = None  # auth_secret_name path skipped
    ctx.instance_id = "test_agent"

    args = {"server_name": server_name, "tool_name": "echo", "args": {"msg": "hi"}}

    return asyncio.run(tool.execute(args, ctx))


# ===========================================================================
# Happy path + error variants
# ===========================================================================

class TestHttpHappyPath:
    def test_200_ok_returns_result(self, monkeypatch):
        cfg = {
            "url": "https://example.com/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
        }
        resp = _FakeResponse(200, {
            "jsonrpc": "2.0", "id": 1,
            "result": {"echoed": "hi", "isError": False},
        })
        out = _call_execute_http(monkeypatch, cfg, response=resp)
        assert out.output["result"] == {"echoed": "hi", "isError": False}
        assert out.output["isError"] is False
        assert out.output["server"] == "test_http_server"

    def test_request_payload_shape(self, monkeypatch):
        cfg = {
            "url": "http://localhost:9999/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
        }
        resp = _FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": {}})
        _call_execute_http(monkeypatch, cfg, response=resp)
        captured = _FakeAsyncClient.captured
        assert captured["url"] == "http://localhost:9999/mcp"
        # JSON-RPC envelope shape per MCP spec
        assert captured["json"]["jsonrpc"] == "2.0"
        assert captured["json"]["method"] == "tools/call"
        assert captured["json"]["params"]["name"] == "echo"
        assert captured["json"]["params"]["arguments"] == {"msg": "hi"}
        # Default headers present
        assert captured["headers"]["Content-Type"] == "application/json"
        assert captured["headers"]["Accept"] == "application/json"


class TestHttpJsonRpcError:
    def test_jsonrpc_error_returns_isError(self, monkeypatch):
        """JSON-RPC error in the response body — HTTP 200 with an
        ``error`` field. ToolResult should carry isError=True without
        raising."""
        cfg = {
            "url": "https://example.com/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
        }
        resp = _FakeResponse(200, {
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32601, "message": "method not found"},
        })
        out = _call_execute_http(monkeypatch, cfg, response=resp)
        assert out.output["isError"] is True
        assert out.output["result"]["code"] == -32601


class TestHttpTransportErrors:
    def test_http_5xx_raises(self, monkeypatch):
        cfg = {
            "url": "https://example.com/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
        }
        resp = _FakeResponse(503, {}, text="upstream unavailable")
        with pytest.raises(McpCallError, match="HTTP 503"):
            _call_execute_http(monkeypatch, cfg, response=resp)

    def test_http_4xx_raises(self, monkeypatch):
        cfg = {
            "url": "https://example.com/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
        }
        resp = _FakeResponse(401, {}, text="unauthorized")
        with pytest.raises(McpCallError, match="refused"):
            _call_execute_http(monkeypatch, cfg, response=resp)

    def test_malformed_json_raises(self, monkeypatch):
        cfg = {
            "url": "https://example.com/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
        }
        resp = _FakeResponse(200, "not-a-dict", text="not json")
        with pytest.raises(McpCallError, match="malformed JSON"):
            _call_execute_http(monkeypatch, cfg, response=resp)

    def test_httpx_timeout_raises(self, monkeypatch):
        import httpx
        cfg = {
            "url": "https://example.com/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
        }
        with pytest.raises(McpCallError, match="timed out"):
            _call_execute_http(
                monkeypatch, cfg,
                raise_exception=httpx.TimeoutException("slow"),
            )


class TestAuthHeaderTemplate:
    def test_substitutes_secret_into_header(self, monkeypatch):
        """auth_header_template uses Python str.format with auth_env;
        the resulting Authorization header should land in the request."""
        from forest_soul_forge.tools.builtin import mcp_call as mc
        cfg = {
            "url": "https://api.example.com/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
            "auth_header_template": "Bearer {GITHUB_TOKEN}",
            "required_secrets": [
                {"name": "GITHUB_TOKEN", "env_var": "GITHUB_TOKEN"},
            ],
        }
        # Stub _resolve_required_secrets to inject GITHUB_TOKEN.
        def fake_resolve(server_name, required_secrets, auth_env):
            auth_env["GITHUB_TOKEN"] = "test_secret_42"
            return [{"name": "GITHUB_TOKEN", "env_var": "GITHUB_TOKEN", "resolved": True}]
        monkeypatch.setattr(mc, "_resolve_required_secrets", fake_resolve)
        resp = _FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": {}})
        _call_execute_http(monkeypatch, cfg, response=resp)
        assert _FakeAsyncClient.captured["headers"]["Authorization"] == "Bearer test_secret_42"

    def test_extra_headers_passed_through(self, monkeypatch):
        cfg = {
            "url": "https://api.example.com/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
            "extra_headers": {"X-Api-Version": "2026-05-11"},
        }
        resp = _FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": {}})
        _call_execute_http(monkeypatch, cfg, response=resp)
        assert _FakeAsyncClient.captured["headers"]["X-Api-Version"] == "2026-05-11"


class TestUnknownTransport:
    def test_ws_url_refuses(self, monkeypatch):
        cfg = {
            "url": "ws://example.com/mcp",
            "side_effects": "network",
            "allowlisted_tools": ["echo"],
        }
        with pytest.raises(McpCallError, match="unsupported transport"):
            _call_execute_http(monkeypatch, cfg)
