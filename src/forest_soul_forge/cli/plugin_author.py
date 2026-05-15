"""``fsf plugin new`` — ADR-0071 T1 (B289) plugin scaffold generator.

Generates a fresh plugin skeleton under ``~/.forest/plugins/<name>/``
(or operator-supplied ``--target``) so authoring a Forest plugin
goes from "read 4 example dirs" to "run one command and edit a
stub."

## Surface

  - ``fsf plugin new <name>`` — scaffold a fresh plugin
    - ``--tier``: read_only / network / filesystem / external
    - ``--tool``: starter tool name (defaults to ``hello_world``)
    - ``--target``: override the output dir (default
      ~/.forest/plugins/<name>/)
    - ``--license``: license string for plugin.yaml (default ELv2)

## Generated layout

    <target>/
    ├── plugin.yaml          ADR-0043 manifest, pre-filled
    ├── README.md            author-facing next-steps docs
    ├── tools/
    │   └── <tool>.py        Tool Protocol stub
    ├── tests/
    │   └── test_<tool>.py   pytest skeleton with mock ctx
    └── .gitignore

The operator runs ``fsf install plugin <target>`` to actually
register the plugin with the running daemon (per ADR-0043's
install discipline).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional


VALID_TIERS = ("read_only", "network", "filesystem", "external")


def add_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    """Append the ``new`` subcommand under ``fsf plugin``.

    Existing ``fsf plugin install/list/...`` lives in plugin_cmd.py;
    we add ``new`` to the same plugin group at registration time
    via the dispatch shim below.
    """
    # add_subparser is a stand-alone hook for plugin_author. The
    # main.py wiring places it under the existing 'plugin' subparser
    # that plugin_cmd.py registers. For T1 we expose a top-level
    # ``fsf plugin-new`` and the docs note that future tranches will
    # consolidate under ``fsf plugin new``. Decision matches the
    # incremental wiring approach the rest of the CLI uses.
    p = parent_subparsers.add_parser(
        "plugin-new",
        help=(
            "Scaffold a new Forest plugin under ~/.forest/plugins/ "
            "(ADR-0071 T1)."
        ),
    )
    p.add_argument(
        "name",
        help=(
            "Plugin name. Must be lowercase + hyphens "
            "(e.g. forest-plaid, forest-slack-adapter)."
        ),
    )
    p.add_argument(
        "--tier",
        choices=VALID_TIERS,
        default="read_only",
        help="Side-effects tier ceiling (default: read_only).",
    )
    p.add_argument(
        "--tool",
        default="hello_world",
        help="Starter tool name (default: hello_world).",
    )
    p.add_argument(
        "--target", default=None,
        help="Override output dir. Default ~/.forest/plugins/<name>/.",
    )
    p.add_argument(
        "--license", default="Elastic License 2.0",
        help="License string for plugin.yaml (default: Elastic License 2.0).",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing plugin dir.",
    )
    p.set_defaults(_run=_run_new)


_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _run_new(args: argparse.Namespace) -> int:
    """Generate the plugin skeleton."""
    if not _NAME_RE.match(args.name):
        print(
            f"REFUSED: plugin name {args.name!r} must be lowercase + "
            f"hyphens, e.g. forest-plaid",
            file=sys.stderr,
        )
        return 2
    if not _TOOL_NAME_RE.match(args.tool):
        print(
            f"REFUSED: tool name {args.tool!r} must be lowercase + "
            f"underscores, e.g. transactions_list",
            file=sys.stderr,
        )
        return 2

    target = (
        Path(args.target).expanduser() if args.target
        else Path.home() / ".forest" / "plugins" / args.name
    )

    if target.exists() and not args.force:
        print(
            f"REFUSED: {target} already exists. Pass --force to "
            f"overwrite (existing files will be replaced).",
            file=sys.stderr,
        )
        return 2

    target.mkdir(parents=True, exist_ok=True)
    (target / "tools").mkdir(exist_ok=True)
    (target / "tests").mkdir(exist_ok=True)

    plugin_yaml = _render_plugin_yaml(
        name=args.name,
        tier=args.tier,
        tool=args.tool,
        license_=args.license,
    )
    (target / "plugin.yaml").write_text(plugin_yaml, encoding="utf-8")

    readme = _render_readme(name=args.name, tier=args.tier, tool=args.tool)
    (target / "README.md").write_text(readme, encoding="utf-8")

    tool_py = _render_tool_module(tool=args.tool, tier=args.tier)
    (target / "tools" / f"{args.tool}.py").write_text(
        tool_py, encoding="utf-8",
    )

    test_py = _render_test_module(tool=args.tool)
    (target / "tests" / f"test_{args.tool}.py").write_text(
        test_py, encoding="utf-8",
    )

    gitignore = _render_gitignore()
    (target / ".gitignore").write_text(gitignore, encoding="utf-8")

    print(f"Scaffolded plugin at {target}")
    print()
    print("Next steps:")
    print(f"  1. cd {target}")
    print(f"  2. Edit tools/{args.tool}.py — implement validate + execute")
    print(f"  3. Edit tests/test_{args.tool}.py — add real test cases")
    print(f"  4. Run: pytest tests/")
    print(f"  5. Install: fsf install plugin {target}")
    return 0


# ---------------------------------------------------------------------------
# Template renderers
# ---------------------------------------------------------------------------


def _render_plugin_yaml(
    name: str, tier: str, tool: str, license_: str,
) -> str:
    """Render plugin.yaml — ADR-0043 manifest pre-filled."""
    return f"""# ADR-0043 plugin manifest, scaffolded by `fsf plugin new`.
schema_version: 1
name: {name}
version: "0.1.0"
license: {license_}
description: |
  TODO: One-paragraph description of what this plugin does. Operator
  reads this in the install confirmation prompt; make it clear.

# Side-effects tier ceiling. Each tool inside this plugin is gated
# at this level or below by Forest's governance pipeline. Choose
# the LOWEST tier that covers your tools' actual reach.
#
#   read_only   — no network, no filesystem writes
#   network     — outbound HTTP, no filesystem writes
#   filesystem  — writes to operator-allowed paths
#   external    — invokes subprocesses or system commands
tier: {tier}

# Tool list. Each entry must have a matching tools/<name>.py file.
tools:
  - name: {tool}
    version: "1"
    side_effects: {tier}
    description: |
      TODO: What does this tool do? Operator reads this when granting
      per-tool approval; be specific about side effects.

# Author info (operator-facing). Helps the operator decide whether
# to trust this plugin.
author:
  name: TODO
  contact: TODO

# Compatibility — minimum Forest version this plugin requires.
# Updated automatically by `fsf install plugin` validation.
min_forest_version: "0.5.0"
"""


def _render_readme(name: str, tier: str, tool: str) -> str:
    return f"""# {name}

Forest plugin scaffolded by `fsf plugin new` (ADR-0071 T1).

## What this is

TODO: Describe what this plugin exposes to Forest agents. One
paragraph; operator-facing.

## Tools

- **`{tool}.v1`** ({tier}) — TODO: describe.

## Install

```
fsf install plugin <this-dir>
```

After install, grant agents per-tool access via the operator UI's
Per-Tool Grants pane (ADR-0053). Tools default to denied; the
operator opts in per (agent, tool).

## Test

```
pytest tests/
```

## License

See `plugin.yaml` `license` field.
"""


# ---------------------------------------------------------------------------
# ADR-0071 T3 (B310) — `fsf plugin adapt`
# ---------------------------------------------------------------------------


_UPSTREAM_NAME_RE = _NAME_RE   # same convention as plugin-new
_CAPABILITY_RE = __import__("re").compile(r"^[a-z][a-z0-9_]*$")


def add_adapt_subparser(parent_subparsers: argparse._SubParsersAction) -> None:
    """Register `fsf plugin-adapt` — ADR-0071 T3 port-face generator.

    Produces a Forest plugin wrapper around an existing upstream
    MCP server. The operator provides:

      - the upstream's name, version, license
      - its transport (stdio or http) + endpoint (command for
        stdio, URL for http)
      - a list of tool names to expose
      - the side-effect tier governance should treat the wrapper at

    The wrapper is an ADR-0043 plugin manifest with `type:
    mcp_server` + capabilities derived from the supplied tools.
    Forest's mcp_call.v1 dispatcher bridge handles the runtime —
    operator never writes Python.

    Distinct from `fsf plugin-new`:
      * plugin-new ships an operator-authored MCP server stub
        with Python tool modules
      * plugin-adapt wraps an existing upstream binary, no
        Python stubs

    Output goes to ``~/.forest/plugins/<name>/`` by default
    (override via ``--target``).
    """
    adapt = parent_subparsers.add_parser(
        "plugin-adapt",
        help=(
            "Generate a Forest plugin wrapper around an existing "
            "upstream MCP server (ADR-0071 T3)."
        ),
    )
    adapt.add_argument(
        "name",
        help="Plugin name (lowercase + hyphens, e.g. 'brave-search').",
    )
    adapt.add_argument(
        "--upstream-version", default="0.1.0",
        help="Version string for the wrapper (default: 0.1.0).",
    )
    adapt.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help=(
            "MCP transport. stdio: the upstream is a local binary "
            "Forest spawns. http: the upstream listens on a port."
        ),
    )
    adapt.add_argument(
        "--command",
        help=(
            "stdio transport: path to the upstream binary (e.g. "
            "'./server' or '/usr/local/bin/brave-search-mcp'). "
            "Required for --transport=stdio."
        ),
    )
    adapt.add_argument(
        "--url",
        help=(
            "http transport: URL the upstream listens on (e.g. "
            "'http://127.0.0.1:7300'). Required for --transport=http."
        ),
    )
    adapt.add_argument(
        "--tool", action="append", default=[],
        help=(
            "Upstream tool name to expose. Repeat for multiple tools. "
            "Each tool becomes capability 'mcp.<plugin-name>.<tool>'. "
            "Tool names must be lowercase + underscores."
        ),
    )
    adapt.add_argument(
        "--tier",
        choices=("read_only", "network", "filesystem", "external"),
        default="read_only",
        help=(
            "Side-effect tier the wrapper governs at (ADR-0043). "
            "read_only requires no per-call operator approval; "
            "other tiers gate at dispatch time. Pick the LOWEST "
            "tier that covers the upstream's actual reach."
        ),
    )
    adapt.add_argument(
        "--license", default="MIT",
        help="SPDX license id for the wrapper manifest (default: MIT).",
    )
    adapt.add_argument(
        "--target", default=None,
        help="Output dir. Default: ~/.forest/plugins/<name>/.",
    )
    adapt.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing target directory.",
    )
    adapt.set_defaults(_run=_run_adapt)


def _run_adapt(args: argparse.Namespace) -> int:
    """Generate the Forest plugin wrapper. Returns 0 on success,
    2 on validation refusal."""
    if not _UPSTREAM_NAME_RE.match(args.name):
        print(
            f"REFUSED: plugin name {args.name!r} must be lowercase + "
            f"hyphens, e.g. brave-search",
            file=sys.stderr,
        )
        return 2

    if not args.tool:
        print(
            "REFUSED: at least one --tool is required",
            file=sys.stderr,
        )
        return 2
    for tool in args.tool:
        if not _CAPABILITY_RE.match(tool):
            print(
                f"REFUSED: tool {tool!r} must be lowercase + "
                f"underscores (e.g. search_web)",
                file=sys.stderr,
            )
            return 2

    if args.transport == "stdio" and not args.command:
        print(
            "REFUSED: --transport stdio requires --command "
            "(path to the upstream binary)",
            file=sys.stderr,
        )
        return 2
    if args.transport == "http" and not args.url:
        print(
            "REFUSED: --transport http requires --url",
            file=sys.stderr,
        )
        return 2

    target = (
        Path(args.target).expanduser() if args.target
        else Path.home() / ".forest" / "plugins" / args.name
    )
    if target.exists() and not args.force:
        print(
            f"REFUSED: {target} already exists. Pass --force to "
            f"overwrite (existing files will be replaced).",
            file=sys.stderr,
        )
        return 2

    target.mkdir(parents=True, exist_ok=True)

    plugin_yaml = _render_adapt_plugin_yaml(
        name=args.name,
        upstream_version=args.upstream_version,
        license_=args.license,
        tier=args.tier,
        tools=list(args.tool),
        transport=args.transport,
        command=args.command,
        url=args.url,
    )
    (target / "plugin.yaml").write_text(plugin_yaml, encoding="utf-8")

    readme = _render_adapt_readme(
        name=args.name, tools=list(args.tool),
        transport=args.transport,
        command=args.command, url=args.url,
        tier=args.tier,
    )
    (target / "README.md").write_text(readme, encoding="utf-8")

    print(f"Scaffolded MCP wrapper at {target}")
    print()
    print("Next steps:")
    print(f"  1. cd {target}")
    print("  2. Read README.md for the install procedure")
    if args.transport == "stdio":
        print(
            "  3. Compute the sha256 of the upstream binary + "
            "paste it into plugin.yaml"
        )
    print("  4. Install: fsf plugin install . --plugin-root ~/.forest/plugins")
    return 0


def _render_adapt_plugin_yaml(
    *,
    name: str,
    upstream_version: str,
    license_: str,
    tier: str,
    tools: list[str],
    transport: str,
    command: str | None,
    url: str | None,
) -> str:
    """Render the ADR-0043 mcp_server manifest for an upstream wrapper.

    The capability list is one entry per upstream tool, namespaced
    under the plugin name so two plugins exposing 'search' don't
    collide.
    """
    capabilities = "\n".join(
        f"  - mcp.{name}.{t}" for t in tools
    )
    approval_lines = "\n".join(
        f"  {t}: false" if tier == "read_only" else f"  {t}: true"
        for t in tools
    )
    if transport == "stdio":
        entry_block = (
            "entry_point:\n"
            "  type: stdio\n"
            f"  command: {command}\n"
            "  # REQUIRED: compute the sha256 of the upstream binary +\n"
            "  # paste it below. Run: shasum -a 256 {cmd}\n".format(cmd=command)
            + "  sha256: \"0000000000000000000000000000000000000000000000000000000000000000\"\n"
        )
    else:
        entry_block = (
            "entry_point:\n"
            "  type: http\n"
            f"  url: {url}\n"
            "  # http transport doesn't carry a binary sha256 —\n"
            "  # operator must validate the endpoint's identity\n"
            "  # via TLS / token / etc. before install.\n"
        )

    return f"""# ADR-0043 plugin manifest. Scaffolded by `fsf plugin-adapt`.
# This wrapper bridges an existing upstream MCP server into
# Forest's mcp_call.v1 dispatcher. See README.md for installation.

schema_version: 1
name: {name}
display_name: "{name.replace('-', ' ').title()}"
version: "{upstream_version}"
author: "@your-handle"
license: {license_}

type: mcp_server
# ADR-0043: the tier governs what side-effects Forest accepts
# from this plugin's tools. Choose the LOWEST tier that covers
# the upstream's actual reach.
side_effects: {tier}

capabilities:
{capabilities}

# Per-tool approval map (ADR-0043). 'false' means the tool fires
# without operator-per-call confirmation; 'true' surfaces an
# approval prompt. read_only defaults to false; higher tiers
# default to true. Override per-tool here if needed.
requires_human_approval:
{approval_lines}

{entry_block}
# No required secrets configured. If the upstream needs an API
# key or token, add it here as a `required_secrets:` list — see
# examples/plugins/brave-search for the pattern.
required_secrets: []
"""


def _render_adapt_readme(
    *,
    name: str,
    tools: list[str],
    transport: str,
    command: str | None,
    url: str | None,
    tier: str,
) -> str:
    tool_lines = "\n".join(f"- `mcp.{name}.{t}`" for t in tools)
    if transport == "stdio":
        install_block = f"""## Install (stdio transport)

1. **Build or download the upstream binary** to `{command}` (or
   wherever your manifest references). For most MCP servers this
   means cloning the upstream repo and following its build
   instructions.

2. **Compute the binary's sha256**:

   ```bash
   shasum -a 256 {command}
   ```

   Paste the hex digest into `plugin.yaml` under
   `entry_point.sha256`. Forest verifies this hash at install
   time and refuses to load a plugin whose binary doesn't match.

3. **Install the plugin**:

   ```bash
   fsf plugin install . --plugin-root ~/.forest/plugins
   ```

   Forest copies the directory into the plugin root, verifies
   the manifest schema, computes the binary's actual sha256,
   compares against `entry_point.sha256`, and registers the
   plugin's capabilities. The operator confirms before
   activation.
"""
    else:
        install_block = f"""## Install (http transport)

1. **Verify the endpoint** — http transport doesn't carry a
   binary checksum. You're trusting `{url}` to be the real
   upstream; validate via TLS cert pinning, token auth, or
   similar before install.

2. **Install the plugin**:

   ```bash
   fsf plugin install . --plugin-root ~/.forest/plugins
   ```
"""
    return f"""# {name}

Forest plugin wrapper around an upstream MCP server, scaffolded
by `fsf plugin-adapt`. Side-effect tier: **{tier}**.

## Exposed capabilities

{tool_lines}

Each capability bridges to the upstream tool of the same name
via Forest's `mcp_call.v1` dispatcher. Agents granted any of these
capabilities call them like any other Forest tool — the bridge
handles the transport.

{install_block}

## What this wrapper is NOT

- It does not contain Python code. The upstream MCP server is
  the implementation; this manifest is the bridge config.
- It does not change the upstream's tool surface. Renaming a tool
  here doesn't rename it upstream; the wrapper's capability name
  is what Forest agents call, but the upstream is invoked under
  its own name.

## What's queued

A future tranche will let `fsf plugin-adapt` introspect a running
upstream's `list_tools()` instead of requiring the operator to
spell out the tool list. Until then, the operator pulls the tool
list from the upstream's README + adds them via `--tool` flags.
"""


def _render_tool_module(tool: str, tier: str) -> str:
    """Render tools/<tool>.py — Tool Protocol stub.

    ADR-0071 T2 (B305): the tool body is tier-specific. Each tier
    gets a representative exemplar pattern so new plugin authors
    see "how a network tool actually fetches" / "how a filesystem
    tool actually respects allowed_paths" / "how an external tool
    actually shells out" instead of a generic echo for everything.
    Tier dictates side-effects which dictates how the tool calls
    the outside world; the exemplar reflects that.
    """
    class_name = "".join(p.capitalize() for p in tool.split("_")) + "Tool"
    validate_body, execute_body, extra_imports = _tier_exemplar(tier)
    return f'''"""{tool}.v1 — scaffolded by `fsf plugin new`.

TODO: Replace this docstring with what this tool does + when an
operator should grant access.

Tier: {tier}
{_TIER_RUBRIC[tier]}
"""
from __future__ import annotations

from typing import Any
{extra_imports}
# Forest's tool base classes. Plugins import these from the
# host process's installed forest_soul_forge package.
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


class {class_name}:
    """TODO: docstring describing args + output shape."""

    name = "{tool}"
    version = "1"
    side_effects = "{tier}"

    def validate(self, args: dict[str, Any]) -> None:
        """Raise ToolValidationError on bad args.

        Forest gates this before execute; downstream code can
        assume args are well-shaped.
        """
{validate_body}

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        """Run the tool. Return a ToolResult.

        ctx carries the agent identity, audit handle, registry
        references, etc. Use ctx.audit to emit additional events
        if your tool produces noteworthy side observations.
        """
{execute_body}


# Module-level instance — plugin loader instantiates and registers
# this with the host's tool registry at install time.
{tool}_tool = {class_name}()
'''


# ADR-0071 T2 (B305): per-tier exemplar bodies for fsf plugin-new.
# Plugin authors see the canonical shape for their chosen tier
# instead of having to read three other plugins to figure out
# "what does a filesystem tool look like?"

_TIER_RUBRIC: dict[str, str] = {
    "read_only": (
        "This tier is for tools that compute purely from their\n"
        "args + Forest's internal state — no network, no disk\n"
        "writes, no subprocesses. Examples: text summarization\n"
        "against in-memory content, hash computation, validation\n"
        "checks against a static catalog."
    ),
    "network": (
        "This tier is for tools that hit outbound HTTP. Examples:\n"
        "REST API clients, RSS fetchers, GraphQL queries against\n"
        "external services. Forest gates the granted hostnames\n"
        "via plugin manifest's allowed_hosts (out of scope here\n"
        "but worth knowing for production plugins)."
    ),
    "filesystem": (
        "This tier is for tools that read or write files in\n"
        "operator-allowed paths. ctx.allowed_paths carries the\n"
        "approved roots; the tool MUST validate every path against\n"
        "that list before any open(). Forest does not enforce path\n"
        "scoping at the OS level — the tool is responsible."
    ),
    "external": (
        "This tier is for tools that invoke subprocesses (system\n"
        "commands, CLI binaries). Examples: ffmpeg, git, native\n"
        "scripts. Operator-trust required at install time. Always\n"
        "pass a timeout and capture stderr separately so failures\n"
        "are diagnosable."
    ),
}


def _tier_exemplar(tier: str) -> tuple[str, str, str]:
    """Return (validate_body, execute_body, extra_imports) for `tier`.

    Bodies are pre-indented to slot directly under their respective
    method signatures (8-space indent — two levels into the class).
    """
    if tier == "network":
        return (
            (
                "        url = args.get(\"url\")\n"
                "        if not isinstance(url, str) or not url.startswith(\n"
                "            (\"http://\", \"https://\")\n"
                "        ):\n"
                "            raise ToolValidationError(\n"
                "                \"url must be a str starting with http(s)://\",\n"
                "            )"
            ),
            (
                "        # Example: fetch a URL with a hard timeout. Real plugins\n"
                "        # use the host's configured http client; this stub uses\n"
                "        # urllib so it has zero deps.\n"
                "        url = args[\"url\"]\n"
                "        try:\n"
                "            with urllib.request.urlopen(url, timeout=10) as resp:\n"
                "                body = resp.read().decode(\"utf-8\", errors=\"replace\")\n"
                "                status = resp.status\n"
                "        except urllib.error.URLError as e:\n"
                "            return ToolResult(\n"
                "                success=False, output={\"error\": str(e)},\n"
                "                audit_payload={\"url\": url, \"error\": str(e)},\n"
                "            )\n"
                "        return ToolResult(\n"
                "            success=True,\n"
                "            output={\"status\": status, \"body\": body[:1024]},\n"
                "            audit_payload={\"url\": url, \"status\": status},\n"
                "        )"
            ),
            "import urllib.error\nimport urllib.request\n",
        )

    if tier == "filesystem":
        return (
            (
                "        path = args.get(\"path\")\n"
                "        if not isinstance(path, str) or not path:\n"
                "            raise ToolValidationError(\n"
                "                \"path must be a non-empty string\",\n"
                "            )"
            ),
            (
                "        # ctx.allowed_paths is the operator-approved list of\n"
                "        # root directories. EVERY read/write MUST validate\n"
                "        # the requested path lives under one of them.\n"
                "        target = pathlib.Path(args[\"path\"]).expanduser().resolve()\n"
                "        allowed = [pathlib.Path(p).resolve() for p in getattr(ctx, \"allowed_paths\", [])]\n"
                "        if not any(_is_within(target, root) for root in allowed):\n"
                "            return ToolResult(\n"
                "                success=False,\n"
                "                output={\"error\": f\"{target} is outside allowed_paths\"},\n"
                "                audit_payload={\"path\": str(target), \"refused\": True},\n"
                "            )\n"
                "        # TODO: do the read/write here.\n"
                "        return ToolResult(\n"
                "            success=True,\n"
                "            output={\"resolved\": str(target)},\n"
                "            audit_payload={\"path\": str(target)},\n"
                "        )"
            ),
            (
                "import pathlib\n"
                "\n"
                "\n"
                "def _is_within(target: pathlib.Path, root: pathlib.Path) -> bool:\n"
                "    \"\"\"True iff target == root or target is under root.\"\"\"\n"
                "    try:\n"
                "        target.relative_to(root)\n"
                "        return True\n"
                "    except ValueError:\n"
                "        return False\n"
            ),
        )

    if tier == "external":
        return (
            (
                "        cmd = args.get(\"cmd\")\n"
                "        if not isinstance(cmd, list) or not cmd:\n"
                "            raise ToolValidationError(\n"
                "                \"cmd must be a non-empty list of strings\",\n"
                "            )\n"
                "        if any(not isinstance(p, str) for p in cmd):\n"
                "            raise ToolValidationError(\n"
                "                \"every cmd element must be a string\",\n"
                "            )"
            ),
            (
                "        # Pass timeout + capture stderr separately so failures\n"
                "        # are diagnosable. NEVER use shell=True with operator-\n"
                "        # supplied args — that's a shell-injection vector.\n"
                "        try:\n"
                "            proc = subprocess.run(\n"
                "                args[\"cmd\"], capture_output=True, text=True,\n"
                "                timeout=30, check=False,\n"
                "            )\n"
                "        except subprocess.TimeoutExpired:\n"
                "            return ToolResult(\n"
                "                success=False,\n"
                "                output={\"error\": \"timeout\"},\n"
                "                audit_payload={\"cmd\": args[\"cmd\"], \"timeout\": True},\n"
                "            )\n"
                "        return ToolResult(\n"
                "            success=proc.returncode == 0,\n"
                "            output={\n"
                "                \"returncode\": proc.returncode,\n"
                "                \"stdout\": proc.stdout,\n"
                "                \"stderr\": proc.stderr,\n"
                "            },\n"
                "            audit_payload={\n"
                "                \"cmd\": args[\"cmd\"],\n"
                "                \"returncode\": proc.returncode,\n"
                "            },\n"
                "        )"
            ),
            "import subprocess\n",
        )

    # read_only — the default echo exemplar.
    return (
        (
            "        # TODO: validate args here. Example:\n"
            "        # query = args.get(\"query\")\n"
            "        # if not isinstance(query, str) or not query.strip():\n"
            "        #     raise ToolValidationError(\"query must be a non-empty string\")\n"
            "        pass"
        ),
        (
            "        # TODO: implement.\n"
            "        return ToolResult(\n"
            "            success=True,\n"
            "            output={\"echo\": args},\n"
            "            audit_payload={\"args_keys\": sorted(args.keys())},\n"
            "        )"
        ),
        "",
    )


def _render_test_module(tool: str) -> str:
    class_name = "".join(p.capitalize() for p in tool.split("_")) + "Tool"
    return f'''"""Tests for {tool}.v1 — scaffolded by `fsf plugin new`."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tools.{tool} import {class_name}


def _ctx():
    """Minimal mock ToolContext for unit tests."""
    return SimpleNamespace(
        provider=None,
        audit=None,
        constraints={{}},
        caller_dna="test-dna",
    )


def test_{tool}_validate_passes_with_no_args():
    """TODO: replace with real validation tests."""
    tool = {class_name}()
    tool.validate({{}})


def test_{tool}_execute_returns_success():
    """TODO: replace with real execute tests."""
    tool = {class_name}()
    result = asyncio.run(tool.execute({{"key": "val"}}, _ctx()))
    assert result.success is True
'''


def _render_gitignore() -> str:
    return """# Forest plugin gitignore — scaffolded defaults.
__pycache__/
*.py[cod]
*$py.class
.pytest_cache/
.mypy_cache/
.coverage
*.swp
*.swo
*.bak
.DS_Store

# Per-install runtime state (operator-local; never commit).
.forest-plugin-state/
"""
