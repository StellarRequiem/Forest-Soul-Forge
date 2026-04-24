"""Local provider — Ollama-compatible HTTP backend.

Ollama is the default target because it's the most common local stack
and its ``/api/generate`` endpoint is stable. Same wire format works for
LM Studio in server mode and llama.cpp's ``server`` binary, so pointing
``FSF_LOCAL_BASE_URL`` at any of them works without code changes.

This provider **does not** attempt to start or manage the local server.
If the user's Ollama isn't running, we surface ``UNREACHABLE`` in the
health check and raise :class:`ProviderUnavailable` on ``complete``.
That's strictly better than silently reaching for a frontier provider.
"""
from __future__ import annotations

from typing import Any

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover — daemon extra not installed
    httpx = None  # type: ignore

from forest_soul_forge.daemon.providers.base import (
    ModelProvider,
    ProviderError,
    ProviderHealth,
    ProviderStatus,
    ProviderUnavailable,
    TaskKind,
)


class LocalProvider:
    """Ollama-compatible local-HTTP model provider.

    ``models`` maps :class:`TaskKind` to Ollama model tags. Callers can
    override any tag without touching code via env vars — see
    :mod:`forest_soul_forge.daemon.config`.
    """

    name = "local"

    def __init__(
        self,
        *,
        base_url: str,
        models: dict[TaskKind, str],
        timeout_s: float = 60.0,
    ) -> None:
        if httpx is None:
            raise ProviderError(
                "httpx is not installed; install the [daemon] extra to use LocalProvider"
            )
        self._base_url = base_url.rstrip("/")
        self._models = models
        self._timeout_s = timeout_s

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def models(self) -> dict[TaskKind, str]:
        # Return a copy so callers can't mutate internal state.
        return dict(self._models)

    async def complete(
        self,
        prompt: str,
        *,
        task_kind: TaskKind = TaskKind.CONVERSATION,
        system: str | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        model = self._models.get(task_kind) or self._models[TaskKind.CONVERSATION]
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system is not None:
            payload["system"] = system
        if max_tokens is not None:
            payload.setdefault("options", {})["num_predict"] = max_tokens
        # Allow passthrough of Ollama-specific options (temperature, etc).
        # These live under "options" per Ollama's API contract.
        for k in ("temperature", "top_p", "top_k", "seed"):
            if k in kwargs:
                payload.setdefault("options", {})[k] = kwargs[k]

        url = f"{self._base_url}/api/generate"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.RequestError as e:
            raise ProviderUnavailable(
                f"local model unreachable at {self._base_url}: {e}"
            ) from e
        except httpx.HTTPStatusError as e:
            raise ProviderError(
                f"local model returned {e.response.status_code}: "
                f"{e.response.text[:200]}"
            ) from e

        # Ollama returns {"response": "..."} for non-streamed generate.
        text = data.get("response")
        if not isinstance(text, str):
            raise ProviderError(f"unexpected local response shape: {data!r}")
        return text

    async def healthcheck(self) -> ProviderHealth:
        url = f"{self._base_url}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except httpx.RequestError as e:
            return ProviderHealth(
                name=self.name,
                status=ProviderStatus.UNREACHABLE,
                base_url=self._base_url,
                models=self.models,
                details={},
                error=str(e),
            )
        except httpx.HTTPStatusError as e:
            return ProviderHealth(
                name=self.name,
                status=ProviderStatus.DEGRADED,
                base_url=self._base_url,
                models=self.models,
                details={},
                error=f"HTTP {e.response.status_code}",
            )

        # ``data["models"]`` is the list of currently-pulled models.
        loaded = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
        wanted = set(self._models.values())
        missing = sorted(wanted - set(loaded))
        status = ProviderStatus.OK if not missing else ProviderStatus.DEGRADED
        return ProviderHealth(
            name=self.name,
            status=status,
            base_url=self._base_url,
            models=self.models,
            details={"loaded": loaded, "missing": missing},
            error=None if not missing else f"models not pulled locally: {missing}",
        )


# Ensure the Protocol check passes at import-time without importing httpx.
_ = ModelProvider  # re-export for callers that want the type
