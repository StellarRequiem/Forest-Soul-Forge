"""Unit tests for web_fetch.v1 (ADR-003X Phase C2).

Covers: argument validation, host allowlist enforcement,
redirect re-checking, secrets accessor wire-up, response
shape, body truncation behavior. The HTTP layer is stubbed
via httpx's MockTransport — no real network in the test path.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.web_fetch import (
    BODY_TRUNCATE_BYTES,
    WebFetchError,
    WebFetchTool,
)


def _ctx(allowed_hosts=("api.example.com",), secrets=None) -> ToolContext:
    return ToolContext(
        instance_id="inst-test",
        agent_dna="abc123",
        role="web_observer",
        genre="web_observer",
        session_id="sess-1",
        constraints={"allowed_hosts": allowed_hosts},
        secrets=secrets,
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestValidate:
    def test_url_required(self) -> None:
        t = WebFetchTool()
        with pytest.raises(ToolValidationError):
            t.validate({})

    def test_url_must_be_http_or_https(self) -> None:
        t = WebFetchTool()
        with pytest.raises(ToolValidationError):
            t.validate({"url": "ftp://example.com"})

    def test_method_must_be_in_allowlist(self) -> None:
        t = WebFetchTool()
        with pytest.raises(ToolValidationError):
            t.validate({"url": "https://x", "method": "DELETE"})

    def test_get_with_body_rejected(self) -> None:
        t = WebFetchTool()
        with pytest.raises(ToolValidationError):
            t.validate({"url": "https://x", "method": "GET", "body": "{}"})

    def test_timeout_bounds(self) -> None:
        t = WebFetchTool()
        with pytest.raises(ToolValidationError):
            t.validate({"url": "https://x", "timeout_s": 0})
        with pytest.raises(ToolValidationError):
            t.validate({"url": "https://x", "timeout_s": 120})

    def test_valid_args_pass(self) -> None:
        t = WebFetchTool()
        # Should not raise.
        t.validate({"url": "https://api.github.com/users/x", "method": "GET"})


# ---------------------------------------------------------------------------
# Allowlist enforcement (the structural gate)
# ---------------------------------------------------------------------------
class TestAllowlist:
    def test_no_allowlist_refuses(self) -> None:
        t = WebFetchTool()
        ctx = _ctx(allowed_hosts=())
        with pytest.raises(WebFetchError, match="no allowed_hosts"):
            _run(t.execute({"url": "https://api.example.com/x"}, ctx))

    def test_off_allowlist_refuses(self) -> None:
        t = WebFetchTool()
        ctx = _ctx(allowed_hosts=("api.example.com",))
        with pytest.raises(WebFetchError, match="not in the agent's allowed_hosts"):
            _run(t.execute({"url": "https://evil.example.org/x"}, ctx))

    def test_redirect_to_off_allowlist_refuses(self, monkeypatch) -> None:
        # Stub httpx with a transport that 302s to an off-list host.
        import httpx

        def handler(request):
            if request.url.host == "api.example.com":
                return httpx.Response(
                    302, headers={"location": "https://evil.example.org/landing"},
                )
            return httpx.Response(200, text="should never reach here")

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

        t = WebFetchTool()
        ctx = _ctx(allowed_hosts=("api.example.com",))
        with pytest.raises(WebFetchError, match="redirect"):
            _run(t.execute({"url": "https://api.example.com/start"}, ctx))


# ---------------------------------------------------------------------------
# Happy path: fetch returns the response
# ---------------------------------------------------------------------------
class TestExecute:
    def _patched_client(self, monkeypatch, handler):
        import httpx
        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)
        monkeypatch.setattr(httpx, "AsyncClient", factory)

    def test_get_returns_status_body_content_type(self, monkeypatch) -> None:
        import httpx

        def handler(request):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                text='{"ok":true}',
            )

        self._patched_client(monkeypatch, handler)

        t = WebFetchTool()
        ctx = _ctx()
        res = _run(t.execute({"url": "https://api.example.com/v1/ping"}, ctx))
        assert res.output["status"] == 200
        assert res.output["body"] == '{"ok":true}'
        assert res.output["body_truncated"] is False
        assert "json" in res.output["content_type"]
        assert res.metadata["host"] == "api.example.com"
        assert res.metadata["method"] == "GET"
        assert res.metadata["status"] == 200
        assert res.metadata["auth_used"] is False

    def test_body_truncated_when_oversized(self, monkeypatch) -> None:
        import httpx
        big = "x" * (BODY_TRUNCATE_BYTES + 1024)

        def handler(request):
            return httpx.Response(200, text=big)

        self._patched_client(monkeypatch, handler)

        t = WebFetchTool()
        ctx = _ctx()
        res = _run(t.execute({"url": "https://api.example.com/big"}, ctx))
        assert res.output["body_truncated"] is True
        assert len(res.output["body"]) == BODY_TRUNCATE_BYTES

    def test_post_with_json_body_sets_content_type(self, monkeypatch) -> None:
        import httpx
        seen_headers = {}

        def handler(request):
            seen_headers.update(request.headers)
            return httpx.Response(201, text='{"created":true}')

        self._patched_client(monkeypatch, handler)

        t = WebFetchTool()
        ctx = _ctx()
        res = _run(t.execute(
            {"url": "https://api.example.com/items", "method": "POST",
             "body": '{"name":"x"}'},
            ctx,
        ))
        assert res.output["status"] == 201
        assert "application/json" in seen_headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Secrets wire-up
# ---------------------------------------------------------------------------
class TestSecretsIntegration:
    def test_auth_secret_name_attaches_bearer(self, monkeypatch) -> None:
        import httpx
        from forest_soul_forge.core.secrets import SecretsAccessor

        # Fake registry that returns a hardcoded "decrypted" value.
        class FakeRegistry:
            def get_secret(self, instance_id, name, *, master_key):
                return "sk-fake-token"

        from forest_soul_forge.core import secrets as sec
        accessor = SecretsAccessor(
            registry=FakeRegistry(),
            master_key=sec.MasterKey(raw=b"k" * 32),
            instance_id="inst-test",
            agent_dna="abc",
            allowed_names=("openai_key",),
        )

        seen_headers = {}

        def handler(request):
            seen_headers.update(request.headers)
            return httpx.Response(200, text="ok")

        transport = httpx.MockTransport(handler)
        original = httpx.AsyncClient
        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original(*args, **kwargs)
        monkeypatch.setattr(httpx, "AsyncClient", factory)

        t = WebFetchTool()
        ctx = _ctx(secrets=accessor)
        res = _run(t.execute(
            {"url": "https://api.example.com/me", "auth_secret_name": "openai_key"},
            ctx,
        ))
        assert res.output["status"] == 200
        assert seen_headers.get("authorization") == "Bearer sk-fake-token"
        assert res.metadata["auth_used"] is True

    def test_auth_secret_not_in_allowlist_refuses(self, monkeypatch) -> None:
        from forest_soul_forge.core.secrets import SecretsAccessor
        from forest_soul_forge.core import secrets as sec

        class FakeRegistry:
            def get_secret(self, instance_id, name, *, master_key):
                # Should never be reached — the accessor's allowlist
                # gate fires first.
                raise AssertionError("get_secret should not be called")

        accessor = SecretsAccessor(
            registry=FakeRegistry(),
            master_key=sec.MasterKey(raw=b"k" * 32),
            instance_id="inst-test",
            agent_dna="abc",
            allowed_names=("openai_key",),  # "github_token" NOT listed
        )

        t = WebFetchTool()
        ctx = _ctx(secrets=accessor)
        with pytest.raises(WebFetchError, match="github_token"):
            _run(t.execute(
                {"url": "https://api.example.com/x",
                 "auth_secret_name": "github_token"},
                ctx,
            ))

    def test_auth_secret_without_accessor_refuses(self) -> None:
        # Operator forgot FSF_SECRETS_MASTER_KEY → ctx.secrets is None.
        t = WebFetchTool()
        ctx = _ctx(secrets=None)
        with pytest.raises(WebFetchError, match="secrets subsystem is not wired"):
            _run(t.execute(
                {"url": "https://api.example.com/x",
                 "auth_secret_name": "openai_key"},
                ctx,
            ))
