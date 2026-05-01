"""``web_fetch.v1`` — HTTP GET/POST against a per-agent host allowlist.

ADR-003X Phase C2. The simplest open-web primitive: an agent fetches
text/JSON from a URL whose host is on its constitutional allowlist.
Optional auth via the per-agent secrets store (G2). No browser, no
redirects to off-allowlist hosts (each redirect re-checks against the
allowlist). No streaming.

Side effects: ``network``. Distinct from ``read_only`` because the
remote server sees the request — that's a side effect on the wire,
even if the daemon doesn't write to disk. Distinct from ``external``
because no human-approval gate by default — the host allowlist IS
the gate. Operators who want per-call approval for fetches can wrap
the tool in a constraint policy that adds requires_human_approval.

The audit event captures host + method + status + url_final.
The response body is NOT logged — it's too noisy and may contain
sensitive content from the upstream. Tools downstream that summarize
fetched content for memory persistence handle the privacy contract
explicitly per ADR-003X §1.

Per-agent constitution must list:
  allowed_hosts: [api.github.com, status.cloud.example, ...]
  allowed_secret_names: [github_token, openai_key, ...]   # if auth used
"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)

# Truncation limit for the body returned to the agent. Larger payloads
# get truncated and the metadata flag tells the agent so it can ask for
# a follow-up range, summarize, etc. 32 KB is enough for ~5 pages of
# rendered text, JSON API responses for most endpoints, RFC fragments.
BODY_TRUNCATE_BYTES = 32 * 1024

ALLOWED_METHODS = ("GET", "POST", "HEAD")


class WebFetchError(Exception):
    """Tool-level error — distinct from validation failures."""


class WebFetchTool:
    """Fetch a URL via HTTP GET/POST with per-agent host allowlist.

    Args:
      url (str): full URL including scheme. http or https only.
      method (str, optional): GET (default) | POST | HEAD.
      body (str, optional): request body for POST. Tool sets
        Content-Type to application/json if body parses as JSON,
        else text/plain.
      auth_secret_name (str, optional): name of a secret in the
        agent's secrets store. Tool reads via ctx.secrets.get(name)
        (subject to the agent's allowed_secret_names allowlist) and
        attaches as Authorization: Bearer <value>.
      timeout_s (float, optional): per-request timeout. Default 15.0,
        max 60.0.

    Output:
      {
        "status":         int,    # HTTP status code
        "body":           str,    # response body, truncated
        "body_truncated": bool,
        "content_type":   str,
        "url_final":      str,    # after redirects
      }

    Constraints (read from ctx.constraints):
      allowed_hosts: tuple[str, ...]   # required — no wildcards in v1
    """

    name = "web_fetch"
    version = "1"
    side_effects = "network"
    # ADR-0021-amendment §5 — autonomous web reads need at least L3.
    # web_observer (default L3) and web_researcher (default L3 ceiling
    # L4) reach. Companion (L1) cannot autonomously fetch — operator-
    # initiated fetch path is a v0.3 escape hatch (per ADR-0021-am
    # §5 the operator-initiated marker isn't wired in v0.2; the
    # initiative gate applies to every dispatch uniformly until then).
    required_initiative_level = "L3"

    def validate(self, args: dict[str, Any]) -> None:
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ToolValidationError("url is required and must be a non-empty string")
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception as e:
            raise ToolValidationError(f"url is malformed: {e}") from e
        if parsed.scheme not in ("http", "https"):
            raise ToolValidationError(
                f"url scheme must be http or https; got {parsed.scheme!r}"
            )
        if not parsed.netloc:
            raise ToolValidationError("url must include a host")

        method = args.get("method", "GET")
        if method not in ALLOWED_METHODS:
            raise ToolValidationError(
                f"method must be one of {ALLOWED_METHODS}; got {method!r}"
            )

        body = args.get("body")
        if body is not None and not isinstance(body, str):
            raise ToolValidationError("body must be a string when provided")
        if body is not None and method == "GET":
            raise ToolValidationError("body is not allowed with method=GET")

        secret_name = args.get("auth_secret_name")
        if secret_name is not None and not isinstance(secret_name, str):
            raise ToolValidationError("auth_secret_name must be a string when provided")

        timeout = args.get("timeout_s", 15.0)
        if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 60:
            raise ToolValidationError(
                f"timeout_s must be a positive number ≤ 60; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        url = args["url"]
        method = args.get("method", "GET")
        body = args.get("body")
        secret_name = args.get("auth_secret_name")
        timeout = float(args.get("timeout_s", 15.0))

        # Allowlist check BEFORE any network activity. The agent's
        # constitution lists allowed_hosts; the dispatcher pulls them
        # into ctx.constraints. No allowlist = refuse — even read_only
        # tools shouldn't reach hosts that weren't sanctioned.
        allowed_hosts = ctx.constraints.get("allowed_hosts") or ()
        if not allowed_hosts:
            raise WebFetchError(
                "agent has no allowed_hosts in its constitution — "
                "web_fetch refuses to reach any host"
            )
        target_host = urllib.parse.urlparse(url).hostname or ""
        if target_host not in allowed_hosts:
            raise WebFetchError(
                f"host {target_host!r} is not in the agent's allowed_hosts "
                f"(allowed: {sorted(allowed_hosts)})"
            )

        # Resolve secret if requested. The accessor enforces the
        # constitutional allowlist; an unlisted name raises and we
        # surface that as a tool error.
        headers: dict[str, str] = {}
        if secret_name:
            if ctx.secrets is None:
                raise WebFetchError(
                    "auth_secret_name was set but the secrets subsystem is "
                    "not wired into this tool call — set FSF_SECRETS_MASTER_KEY "
                    "and ensure the agent's allowed_secret_names lists this name"
                )
            try:
                token = ctx.secrets.get(secret_name)
            except Exception as e:
                raise WebFetchError(f"secret {secret_name!r}: {e}") from e
            headers["Authorization"] = f"Bearer {token}"

        # Body framing. JSON-looking bodies get application/json, others plain.
        if body is not None:
            try:
                json.loads(body)
                headers.setdefault("Content-Type", "application/json")
            except (json.JSONDecodeError, ValueError):
                headers.setdefault("Content-Type", "text/plain; charset=utf-8")

        # Lazy httpx import — keeps the rest of forest_soul_forge import-light.
        try:
            import httpx
        except ImportError as e:
            raise WebFetchError(
                "httpx is not installed — install the daemon optional-deps "
                "(pip install forest-soul-forge[daemon])"
            ) from e

        # Per-call client so connection state doesn't bleed between
        # agents. Follow_redirects=True but EVERY redirect re-checks the
        # allowlist via an event hook — the agent's constitution governs
        # the final landing host too, not just the start.
        url_final = url

        async def _check_redirect(response: httpx.Response) -> None:
            nonlocal url_final
            url_final = str(response.url)
            redirect_host = response.url.host or ""
            if redirect_host not in allowed_hosts:
                raise WebFetchError(
                    f"redirect to {redirect_host!r} is not in allowed_hosts; "
                    f"original url was {url}"
                )

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                event_hooks={"response": [_check_redirect]},
            ) as client:
                resp = await client.request(method, url, content=body, headers=headers)
        except httpx.TimeoutException as e:
            raise WebFetchError(f"timeout after {timeout}s: {e}") from e
        except httpx.RequestError as e:
            raise WebFetchError(f"request failed: {e}") from e

        url_final = str(resp.url)
        body_bytes = resp.content
        truncated = len(body_bytes) > BODY_TRUNCATE_BYTES
        body_text = (body_bytes[:BODY_TRUNCATE_BYTES]).decode(
            "utf-8", errors="replace",
        )

        return ToolResult(
            output={
                "status": resp.status_code,
                "body": body_text,
                "body_truncated": truncated,
                "content_type": resp.headers.get("content-type", ""),
                "url_final": url_final,
            },
            metadata={
                # Audit-trail-visible fields. Host + method + status are
                # the operator's ledger of what reached the wire. No
                # body here — too noisy and may leak upstream content.
                "host": target_host,
                "method": method,
                "status": resp.status_code,
                "bytes_received": len(body_bytes),
                "auth_used": bool(secret_name),
            },
            side_effect_summary=(
                f"{method} {target_host} → {resp.status_code} "
                f"({len(body_bytes)} bytes{', truncated' if truncated else ''})"
            ),
        )
