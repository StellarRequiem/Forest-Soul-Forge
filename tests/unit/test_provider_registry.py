"""Tests for the in-process provider registry.

Stdlib-only — no FastAPI required. Exercises the protocol, the
registry's active/default tracking, and error paths that must work
whether or not the [daemon] extra is installed.
"""
from __future__ import annotations

import asyncio

import pytest


# Import paths used by the rest of the test: if pydantic-settings or
# httpx aren't installed in the environment, the providers/__init__ and
# providers/base still import because they're stdlib-only. Only the
# LocalProvider + FrontierProvider concrete classes need httpx.
from forest_soul_forge.daemon.providers import (
    ProviderHealth,
    ProviderRegistry,
    ProviderStatus,
    TaskKind,
    UnknownProviderError,
)


class _StubProvider:
    """In-memory provider used to test the registry without network I/O."""

    def __init__(self, name: str, *, models: dict[TaskKind, str] | None = None) -> None:
        self.name = name
        self._models = models or {TaskKind.CONVERSATION: "stub:latest"}
        self.calls: list[tuple[str, TaskKind]] = []

    async def complete(self, prompt, *, task_kind=TaskKind.CONVERSATION, **_):
        self.calls.append((prompt, task_kind))
        return f"[{self.name}/{task_kind.value}] {prompt}"

    async def healthcheck(self):
        return ProviderHealth(
            name=self.name,
            status=ProviderStatus.OK,
            base_url=None,
            models=self._models,
            details={},
            error=None,
        )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


class TestProviderRegistry:
    def test_default_is_local(self):
        local = _StubProvider("local")
        frontier = _StubProvider("frontier")
        reg = ProviderRegistry(
            providers={"local": local, "frontier": frontier},
            default="local",
        )
        assert reg.active_name == "local"
        assert reg.default_name == "local"
        assert reg.known() == ["frontier", "local"]  # sorted
        assert reg.active() is local

    def test_set_active_flips(self):
        local = _StubProvider("local")
        frontier = _StubProvider("frontier")
        reg = ProviderRegistry(
            providers={"local": local, "frontier": frontier},
            default="local",
        )
        reg.set_active("frontier")
        assert reg.active_name == "frontier"
        assert reg.active() is frontier

    def test_set_active_unknown_raises(self):
        local = _StubProvider("local")
        reg = ProviderRegistry(providers={"local": local}, default="local")
        with pytest.raises(UnknownProviderError):
            reg.set_active("nonexistent")

    def test_reset_restores_default(self):
        local = _StubProvider("local")
        frontier = _StubProvider("frontier")
        reg = ProviderRegistry(
            providers={"local": local, "frontier": frontier},
            default="local",
        )
        reg.set_active("frontier")
        reg.reset()
        assert reg.active_name == "local"

    def test_unknown_default_rejected(self):
        with pytest.raises(UnknownProviderError):
            ProviderRegistry(
                providers={"local": _StubProvider("local")},
                default="frontier",
            )

    def test_active_provider_routes_complete(self):
        local = _StubProvider("local")
        reg = ProviderRegistry(providers={"local": local}, default="local")
        out = _run(reg.active().complete("hello", task_kind=TaskKind.CLASSIFY))
        assert out == "[local/classify] hello"
        assert local.calls == [("hello", TaskKind.CLASSIFY)]


class TestTaskKind:
    def test_all_task_kinds_have_stable_string_values(self):
        # If any of these values changes, env-var overrides like
        # FSF_LOCAL_MODEL_CLASSIFY stop working silently. Pin them.
        assert TaskKind.CLASSIFY.value == "classify"
        assert TaskKind.GENERATE.value == "generate"
        assert TaskKind.SAFETY_CHECK.value == "safety_check"
        assert TaskKind.CONVERSATION.value == "conversation"
        assert TaskKind.TOOL_USE.value == "tool_use"
