"""Frontier provider — opt-in hosted inference.

Frontier is **off** by default. The user must explicitly set
``FSF_FRONTIER_ENABLED=1`` *and* supply credentials before this provider
will accept a call. See ADR-0008 for why: medical/therapeutic agents
handle user data that must not leak to a third-party API by accident.

The wire format is deliberately OpenAI-compatible (``/v1/chat/completions``
with ``messages``). That covers OpenAI, Anthropic via gateway, xAI, and
any of the open-compat hosted services. Users pointing at Anthropic's
native API can either use a gateway (LiteLLM, llm-proxy, etc.) or swap
in their own provider subclass — we intentionally don't bundle every
SDK.
"""
from __future__ import annotations

from typing import Any

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover — daemon extra not installed
    httpx = None  # type: ignore

from forest_soul_forge.daemon.providers.base import (
    ProviderDisabled,
    ProviderError,
    ProviderHealth,
    ProviderStatus,
    ProviderUnavailable,
    TaskKind,
)


class FrontierProvider:
    """Hosted-inference provider, OpenAI-compatible chat completions.

    The instance carries an ``enabled`` flag separate from having an API
    key so we can present "disabled by config" and "missing credentials"
    as two different failure modes in the UI.
    """

    name = "frontier"

    def __init__(
        self,
        *,
        enabled: bool,
        base_url: str,
        api_key: str | None,
        models: dict[TaskKind, str],
        timeout_s: float = 60.0,
    ) -> None:
        if httpx is None and enabled:
            raise ProviderError(
                "httpx is not installed; install the [daemon] extra to use FrontierProvider"
            )
        self._enabled = enabled
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._models = models
        self._timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def models(self) -> dict[TaskKind, str]:
        return dict(self._models)

    def _auth_headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    async def complete(
        self,
        prompt: str,
        *,
        task_kind: TaskKind = TaskKind.CONVERSATION,
        system: str | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        if not self._enabled:
            raise ProviderDisabled(
                "frontier provider is disabled (set FSF_FRONTIER_ENABLED=1 to enable)"
            )
        if not self._api_key:
            raise ProviderDisabled(
                "frontier provider has no API key configured (set FSF_FRONTIER_API_KEY)"
            )

        model = self._models.get(task_kind) or self._models[TaskKind.CONVERSATION]
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {"model": model, "messages": messages}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        for k in ("temperature", "top_p", "seed"):
            if k in kwargs:
                payload[k] = kwargs[k]

        url = f"{self._base_url}/v1/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(url, json=payload, headers=self._auth_headers())
                resp.raise_for_status()
                data = resp.json()
        except httpx.RequestError as e:
            raise ProviderUnavailable(
                f"frontier unreachable at {self._base_url}: {e}"
            ) from e
        except httpx.HTTPStatusError as e:
            raise ProviderError(
                f"frontier returned {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e

        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(f"frontier returned no choices: {data!r}")
        msg = choices[0].get("message") or {}
        text = msg.get("content")
        if not isinstance(text, str):
            raise ProviderError(f"unexpected frontier response shape: {data!r}")
        return text

    async def healthcheck(self) -> ProviderHealth:
        if not self._enabled:
            return ProviderHealth(
                name=self.name,
                status=ProviderStatus.DISABLED,
                base_url=self._base_url,
                models=self.models,
                details={"enabled": False},
                error=None,
            )
        if not self._api_key:
            return ProviderHealth(
                name=self.name,
                status=ProviderStatus.DISABLED,
                base_url=self._base_url,
                models=self.models,
                details={"enabled": True, "has_api_key": False},
                error="no API key configured",
            )
        # We deliberately do NOT ping the hosted API just to report
        # health — that would burn credits on every /healthz. Trust the
        # credentials exist and report OK. Actual failure surfaces on
        # first real call.
        return ProviderHealth(
            name=self.name,
            status=ProviderStatus.OK,
            base_url=self._base_url,
            models=self.models,
            details={"enabled": True, "has_api_key": True},
            error=None,
        )
