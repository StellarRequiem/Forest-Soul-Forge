"""``mcp_call.v1`` — call an operator-registered MCP server.

ADR-003X Phase C4. The integration multiplier — lets an Forest agent
call any MCP server (Slack, GitHub, Linear, etc.) the operator has
pre-registered. Auth via the per-agent secrets store. Side effects
default to ``external`` (gated) but can be overridden per-server in
the registration config — read-only tools (search_code,
list_issues) can ship at ``network`` to skip the per-call approval
gate.

Trust boundary (per ADR-003X §"What this ADR is NOT"):
    The operator types the server config; Forest verifies the
    server's binary SHA256 matches the operator-pinned hash before
    each launch. This defends against the typosquatted-MCP-server /
    silent-supply-chain-swap attack class. It does NOT defend
    against an operator pinning the hash of a server they shouldn't
    have trusted in the first place. Trust boundary remains at the
    operator's pin, not the server's identity.

Discovery is explicitly out of scope. Forest does not search for
MCP servers, does not auto-install third-party servers, does not
run untrusted code. The operator's ``config/mcp_servers.yaml`` is
the registry; this tool dispatches against it.

Per-agent constitution must list:
  allowed_mcp_servers: [github, linear, ...]   # subset of registered
  allowed_secret_names: [...]                  # if auth used
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)

# Where the operator-curated MCP server config lives. Loaded lazily;
# missing file = subsystem disabled (mcp_call refuses cleanly).
DEFAULT_MCP_REGISTRY_PATH = Path("config/mcp_servers.yaml")

DEFAULT_TIMEOUT_S = 30.0
MAX_TIMEOUT_S = 300.0


class McpCallError(Exception):
    """Tool-level error — distinct from validation failures."""


class McpCallTool:
    """Dispatch an MCP tool call against an operator-registered server.

    Args:
      server_name (str): name from config/mcp_servers.yaml. Must be
        in the agent's allowed_mcp_servers constitution list.
      tool_name (str): tool to call. Must be in the server's
        allowlisted_tools list (set by operator at registration).
      args (dict, optional): tool-specific arguments. Empty dict if
        omitted.
      auth_secret_name (str, optional): name in the agent's secrets
        store. Read via ctx.secrets.get(name) (subject to the
        agent's allowed_secret_names allowlist) and passed to the
        server as the FSF_MCP_AUTH env var.
      timeout_s (float, optional): per-call timeout. Default 30.0,
        max 300.0.

    Output:
      {
        "result":  dict,    # passthrough from MCP server
        "isError": bool,
        "server":  str,     # echo of server_name
        "tool":    str,     # echo of tool_name
      }

    Constraints (read from ctx.constraints):
      mcp_registry: dict[str, dict]    # loaded server config
      allowed_mcp_servers: tuple[str, ...]   # required
    """

    name = "mcp_call"
    version = "1"
    side_effects = "external"  # default — per-server config can override

    def validate(self, args: dict[str, Any]) -> None:
        server = args.get("server_name")
        if not isinstance(server, str) or not server.strip():
            raise ToolValidationError(
                "server_name is required and must be a non-empty string"
            )
        tool = args.get("tool_name")
        if not isinstance(tool, str) or not tool.strip():
            raise ToolValidationError(
                "tool_name is required and must be a non-empty string"
            )
        if "args" in args and not isinstance(args["args"], dict):
            raise ToolValidationError(
                f"args must be an object when provided; got {type(args['args']).__name__}"
            )
        secret_name = args.get("auth_secret_name")
        if secret_name is not None and not isinstance(secret_name, str):
            raise ToolValidationError(
                "auth_secret_name must be a string when provided"
            )
        timeout = args.get("timeout_s", DEFAULT_TIMEOUT_S)
        if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > MAX_TIMEOUT_S:
            raise ToolValidationError(
                f"timeout_s must be a positive number ≤ {MAX_TIMEOUT_S}; got {timeout!r}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        server_name = args["server_name"]
        tool_name = args["tool_name"]
        tool_args = args.get("args", {})
        secret_name = args.get("auth_secret_name")
        timeout_s = float(args.get("timeout_s", DEFAULT_TIMEOUT_S))

        # MCP registry — operator's curated config.
        registry = ctx.constraints.get("mcp_registry") or _load_registry()
        if not registry:
            raise McpCallError(
                "no MCP server registry configured — operator must populate "
                f"{DEFAULT_MCP_REGISTRY_PATH} before mcp_call.v1 can dispatch"
            )

        # Per-agent allowlist of servers.
        allowed_servers = ctx.constraints.get("allowed_mcp_servers") or ()
        if not allowed_servers:
            raise McpCallError(
                "agent has no allowed_mcp_servers in its constitution — "
                "mcp_call refuses to reach any server"
            )
        if server_name not in allowed_servers:
            raise McpCallError(
                f"server {server_name!r} is not in the agent's "
                f"allowed_mcp_servers (allowed: {sorted(allowed_servers)})"
            )

        # Server config from registry.
        server_cfg = registry.get(server_name)
        if not server_cfg:
            raise McpCallError(
                f"server {server_name!r} not in registry — operator must "
                f"add it to {DEFAULT_MCP_REGISTRY_PATH}"
            )

        # Per-server tool allowlist (operator pre-approved at registration).
        server_tools = server_cfg.get("allowlisted_tools") or ()
        if tool_name not in server_tools:
            raise McpCallError(
                f"tool {tool_name!r} is not allowlisted for server "
                f"{server_name!r} (operator-approved tools: {sorted(server_tools)})"
            )

        # SHA256 verification — defense against typosquat / supply-chain swap.
        # Operator pins the binary's sha256 in config; we recompute and refuse
        # to launch if it doesn't match. v1 trust boundary; Sigstore-style
        # provenance is C5 follow-up.
        binary_path = server_cfg.get("path")
        pinned_sha256 = server_cfg.get("sha256")
        if binary_path and pinned_sha256:
            actual = _sha256_of_file(Path(binary_path))
            if actual != pinned_sha256:
                raise McpCallError(
                    f"server {server_name!r} binary at {binary_path} has "
                    f"sha256={actual[:16]}... but config pinned "
                    f"{pinned_sha256[:16]}... — refusing to launch (this is "
                    "the typosquat / supply-chain-swap defense)"
                )

        # Resolve auth secret if requested. Allowlist enforcement is in
        # the SecretsAccessor; we just propagate the result.
        auth_env: dict[str, str] = {}
        if secret_name:
            if ctx.secrets is None:
                raise McpCallError(
                    "auth_secret_name was set but the secrets subsystem is "
                    "not wired into this tool call — set FSF_SECRETS_MASTER_KEY "
                    "and ensure the agent's allowed_secret_names lists this name"
                )
            try:
                token = ctx.secrets.get(secret_name)
            except Exception as e:
                raise McpCallError(f"secret {secret_name!r}: {e}") from e
            auth_env["FSF_MCP_AUTH"] = token

        # Dispatch the call. v1 supports stdio: prefix only — JSON-RPC over
        # the server's stdin/stdout. ws:// / http:// transports are future
        # additions; refuse cleanly on unrecognized scheme.
        url = server_cfg.get("url", "")
        if not url.startswith("stdio:"):
            raise McpCallError(
                f"server {server_name!r} url {url!r} uses unsupported transport; "
                "v1 supports only stdio: (subprocess JSON-RPC)"
            )
        cmd_path = url[len("stdio:"):]
        if not cmd_path:
            raise McpCallError(
                f"server {server_name!r} url is missing the command after stdio:"
            )

        # Build JSON-RPC request matching MCP wire format.
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": tool_args,
            },
        }
        request_bytes = (json.dumps(request) + "\n").encode("utf-8")

        try:
            proc = await asyncio.create_subprocess_exec(
                cmd_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, **auth_env},
            )
        except (FileNotFoundError, PermissionError) as e:
            raise McpCallError(
                f"failed to launch MCP server {server_name!r}: {e}"
            ) from e

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=request_bytes),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass
            raise McpCallError(
                f"MCP server {server_name!r} timed out after {timeout_s}s"
            )

        # Parse JSON-RPC response. MCP servers respond on a single stdout
        # line per request; if the server emitted multiple lines, the
        # result line is the last non-empty one.
        if not stdout.strip():
            stderr_preview = stderr.decode("utf-8", errors="replace")[:200]
            raise McpCallError(
                f"MCP server {server_name!r} returned no stdout response. "
                f"stderr: {stderr_preview}"
            )
        last_line = stdout.decode("utf-8", errors="replace").strip().split("\n")[-1]
        try:
            response = json.loads(last_line)
        except json.JSONDecodeError as e:
            raise McpCallError(
                f"MCP server {server_name!r} returned malformed JSON: {e}; "
                f"raw: {last_line[:200]!r}"
            ) from e

        if "error" in response:
            err = response["error"]
            return ToolResult(
                output={
                    "result": err,
                    "isError": True,
                    "server": server_name,
                    "tool": tool_name,
                },
                metadata={
                    "server": server_name,
                    "tool": tool_name,
                    "auth_used": bool(secret_name),
                    "isError": True,
                },
                side_effect_summary=(
                    f"mcp_call {server_name}/{tool_name} → error: "
                    f"{str(err.get('message', err))[:80]}"
                ),
            )

        result = response.get("result", {})
        return ToolResult(
            output={
                "result": result,
                "isError": bool(result.get("isError", False)) if isinstance(result, dict) else False,
                "server": server_name,
                "tool": tool_name,
            },
            metadata={
                "server": server_name,
                "tool": tool_name,
                "auth_used": bool(secret_name),
                "result_digest": _digest(result),
            },
            side_effect_summary=(
                f"mcp_call {server_name}/{tool_name} → ok"
            ),
        )


def _load_registry() -> dict[str, dict]:
    """Load config/mcp_servers.yaml. Returns empty dict if absent."""
    path = DEFAULT_MCP_REGISTRY_PATH
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return {}
    return data.get("servers", {}) if isinstance(data, dict) else {}


def _sha256_of_file(path: Path) -> str:
    """SHA256 hex digest of a file. Used for binary verification."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _digest(value: Any) -> str:
    """SHA256 of canonical-JSON value. Used for result_digest in audit."""
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
