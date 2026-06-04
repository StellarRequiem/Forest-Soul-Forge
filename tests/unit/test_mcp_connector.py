"""Tests for the FSF MCP connector tools (the httpx client). No daemon + no `mcp`
lib needed — monkeypatches httpx to assert each tool builds the right request.
(server.py is NOT imported here, so CI never needs the FastMCP dependency.)"""
import httpx

from mcp_connector import tools as T


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._p


def test_get_tools_build_correct_requests(monkeypatch):
    calls = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["url"], calls["params"] = url, params
        return _FakeResp({"ok": True})

    monkeypatch.setattr(httpx, "get", fake_get)

    T.bounties(min_n=1.0, top=5)
    assert calls["url"].endswith("/synapse/bounties")
    assert calls["params"] == {"min_n": 1.0, "top": 5}

    T.route("llm_think.v1", candidates="a,b", seed=7)
    assert calls["url"].endswith("/synapse/route")
    assert calls["params"] == {"problem_class": "llm_think.v1", "candidates": "a,b", "seed": 7}

    T.why("agent", "x.v1")
    assert calls["params"] == {"node": "agent", "problem_class": "x.v1"}


def test_token_header_only_when_set(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _FakeResp({})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.delenv("FSF_API_TOKEN", raising=False)
    T.health()
    assert captured["headers"] == {}                       # no token → no header
    monkeypatch.setenv("FSF_API_TOKEN", "secret")
    T.health()
    assert captured["headers"] == {"X-FSF-Token": "secret"}


def test_base_url_override_strips_trailing_slash(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResp({})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setenv("FSF_DAEMON_URL", "http://example:9999/")
    T.nodes()
    assert captured["url"] == "http://example:9999/synapse/nodes"


def test_run_training_posts(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResp({"passed": 6, "total": 6})

    monkeypatch.setattr(httpx, "post", fake_post)
    out = T.run_training()
    assert captured["url"].endswith("/training/run") and out["passed"] == 6
