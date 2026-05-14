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


def _render_tool_module(tool: str, tier: str) -> str:
    """Render tools/<tool>.py — Tool Protocol stub."""
    class_name = "".join(p.capitalize() for p in tool.split("_")) + "Tool"
    return f'''"""{tool}.v1 — scaffolded by `fsf plugin new`.

TODO: Replace this docstring with what this tool does + when an
operator should grant access.
"""
from __future__ import annotations

from typing import Any

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
        # TODO: validate args here. Example:
        # query = args.get("query")
        # if not isinstance(query, str) or not query.strip():
        #     raise ToolValidationError("query must be a non-empty string")
        pass

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        """Run the tool. Return a ToolResult.

        ctx carries the agent identity, audit handle, registry
        references, etc. Use ctx.audit to emit additional events
        if your tool produces noteworthy side observations.
        """
        # TODO: implement.
        return ToolResult(
            success=True,
            output={{"echo": args}},
            audit_payload={{"args_keys": sorted(args.keys())}},
        )


# Module-level instance — plugin loader instantiates and registers
# this with the host's tool registry at install time.
{tool}_tool = {class_name}()
'''


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
