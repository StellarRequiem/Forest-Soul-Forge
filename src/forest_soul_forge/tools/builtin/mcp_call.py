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
    # ADR-0021-amendment §5 — MCP servers can perform arbitrary external
    # mutations (file writes on the host, API calls to third parties,
    # any side-effect the server author chose). Required initiative
    # L5 mirrors browser_action: only web_actuator (default L5) reaches.
    # Per-server side_effects override exists in mcp_call's resolve
    # path, but the initiative floor is uniform — any MCP call is
    # treated as destructive-class for autonomy purposes.
    required_initiative_level = "L5"

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
        # This is the per-CALL dynamic auth path (operator pre-stored
        # the secret in the agent's encrypted store + the constitution
        # listed it as allowed). Distinct from required_secrets below.
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

        # ADR-0052 T4 (B170): resolve the plugin manifest's
        # required_secrets via the operator-configured backend
        # (FSF_SECRET_STORE → file / keychain / vaultwarden / BYO) and
        # set the env_var fields on the subprocess. See
        # _resolve_required_secrets() docstring for the full
        # design rationale. Mutates auth_env in place; returns
        # an audit-trail descriptor list (name + backend only,
        # never values) that we pass to ToolResult.metadata so
        # the existing tool_call_succeeded event hashes it into
        # the audit chain (B171).
        secrets_resolved = _resolve_required_secrets(
            server_name=server_name,
            required_secrets=server_cfg.get("required_secrets") or [],
            auth_env=auth_env,
        )

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
                    # B171: include resolution descriptors even in the
                    # error path so an auditor reading
                    # tool_call_succeeded-but-MCP-server-said-error can
                    # still see which secrets were consumed before the
                    # server-side failure.
                    "required_secrets_resolved": secrets_resolved,
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
                # B171 (ADR-0052 T4 follow-up): audit-trail for the
                # operator-managed secrets resolved at server-launch
                # time. Each entry is {secret_name, env_var, backend}
                # — never the value. The dispatcher hashes this into
                # the existing tool_call_succeeded chain entry, so an
                # auditor querying the chain can see WHICH plugin ran
                # against WHICH secrets via WHICH backend without ever
                # seeing the credentials themselves.
                "required_secrets_resolved": secrets_resolved,
            },
            side_effect_summary=(
                f"mcp_call {server_name}/{tool_name} → ok"
            ),
        )


def _resolve_required_secrets(
    *,
    server_name: str,
    required_secrets: list,
    auth_env: dict[str, str],
) -> list[dict]:
    """ADR-0052 T4 (B170) — resolve the plugin manifest's
    required_secrets via the operator-configured backend
    (FSF_SECRET_STORE → file / keychain / vaultwarden / BYO) and
    set each env_var on the subprocess. Mutates auth_env in place.

    Returns a list of resolution descriptors — ONE per
    successfully-resolved secret. Each descriptor is a dict:

        {
            "secret_name": <str>,    # the name asked for (matches
                                     # plugin manifest entry.name)
            "env_var":     <str>,    # the env var that received it
            "backend":     <str>,    # store.name ("file", "keychain",
                                     # "vaultwarden", BYO module name)
        }

    Critically, the descriptor NEVER includes the secret value —
    only the name + backend identifier. McpCallTool.execute()
    surfaces this list via ``ToolResult.metadata['required_secrets_
    resolved']`` so the existing ``tool_call_succeeded`` audit
    event hashes it into the chain (B171). Operators querying the
    audit chain for "what secrets did this plugin call use?" find
    the full lineage there without ever logging the values.

    Distinct from the per-call ``auth_secret_name`` path:

      auth_secret_name  — per-CALL agent-allowlisted secret pulled
                          from the registry's encrypted store; sets
                          FSF_MCP_AUTH. Used when the agent has
                          dynamic per-session credentials (rare).
      required_secrets  — OPERATOR-LEVEL plugin auth (e.g., a
                          GitHub PAT); pre-stored via `fsf secret
                          put` + served by the active
                          SecretStoreProtocol backend; sets each
                          manifest-declared env_var. The common
                          case for plugins that need stable
                          credentials.

    Both paths coexist: per-call FSF_MCP_AUTH and per-plugin
    FSF_SECRET_* vars are separate keys in the env dict.

    Failure modes:
      - Backend unreachable: raises McpCallError pointing at
        FSF_SECRET_STORE configuration. The existing
        ``tool_call_failed`` event captures the error_class +
        message, so 'secret_store_unreachable' visibility from
        ADR-0052 §Decision 6 is preserved without a dedicated
        new event type.
      - Missing secret: raises McpCallError pointing at
        `fsf secret put <name>` for the operator to fix
      - Backend .get() raises: propagates as McpCallError tied
        to the specific name + backend identifier
      - Malformed entry shapes (missing name/env_var): skipped
        silently — the manifest schema validates these at load
        time; this is just defense in depth

    Empty list → returns empty list, no resolver call, no env
    mutation.
    """
    if not required_secrets:
        return []

    # Lazy import — keeps mcp_call importable in environments where
    # the secrets module hasn't been touched yet (test harnesses,
    # tools-suite-only configurations).
    from forest_soul_forge.security.secrets import (
        SecretStoreError as _SecretStoreError,
        resolve_secret_store as _resolve_secret_store,
    )

    try:
        store = _resolve_secret_store()
    except _SecretStoreError as e:
        raise McpCallError(
            f"plugin {server_name!r} requires {len(required_secrets)} "
            f"operator-managed secret(s) but the secret-store backend "
            f"is unavailable: {e}. Check FSF_SECRET_STORE and the "
            f"active backend's configuration."
        ) from e

    resolved: list[dict] = []
    for entry in required_secrets:
        if not isinstance(entry, dict):
            continue
        rs_name = entry.get("name")
        rs_env = entry.get("env_var")
        if not rs_name or not rs_env:
            # Manifest schema validates these at load time; defensive
            # skip rather than dereference None.
            continue
        try:
            value = store.get(rs_name)
        except _SecretStoreError as e:
            raise McpCallError(
                f"plugin {server_name!r}: backend {store.name!r} "
                f"failed reading required secret {rs_name!r}: {e}"
            ) from e
        if value is None:
            raise McpCallError(
                f"plugin {server_name!r} requires secret "
                f"{rs_name!r} but the {store.name!r} backend "
                f"doesn't have it. Operator must store it via "
                f"`fsf secret put {rs_name}` first, then retry."
            )
        auth_env[rs_env] = value
        # B171 audit-trail descriptor. Name + backend only — never
        # the value. McpCallTool.execute() surfaces this list via
        # ToolResult.metadata for chain inclusion.
        resolved.append({
            "secret_name": rs_name,
            "env_var": rs_env,
            "backend": store.name,
        })
    return resolved


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
