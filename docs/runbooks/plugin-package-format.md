# `.fsf` plugin package format — operator runbook

**ADR:** [ADR-0019](../decisions/ADR-0019-tool-execution-runtime.md) §T5
**Status:** Accepted (T5 plugin loader shipped)

The `.fsf` package format is how operator-installed tools (and future
tranches: skills, custom roles) live alongside the daemon's built-in
catalog without modifying the daemon's source.

## Why a plugin format

Built-in tools live under `src/forest_soul_forge/tools/builtin/` and
get registered at lifespan via `register_builtins()`. Adding a new
tool there is a code change — runs against the daemon's release
cadence.

Plugin tools live under `~/.fsf/plugins/` (or wherever
`DaemonSettings.plugins_dir` points). They:

- Register at lifespan (or via `POST /tools/reload`)
- Have the same `name + version` namespace as built-ins (collisions
  rejected)
- Persist across daemon restarts
- Can be hot-loaded without restarting the daemon
- Are listed in the `Tools` tab with `source: plugin`

## Directory layout

A plugin is one directory per tool, named `<tool_name>.v<version>/`:

```
~/.fsf/plugins/
└── my_custom_tool.v1/
    ├── spec.yaml          # ToolSpec — name, version, side_effects, args, etc.
    └── tool.py            # Python implementation — class with .name, .version, .side_effects, .validate, .execute
```

`spec.yaml` is required. `tool.py` is required. Other files in the
directory are ignored (so a plugin author can leave per-plugin docs or
test fixtures in the dir without confusing the loader).

## `spec.yaml`

```yaml
name: my_custom_tool
version: '1'
description: |
  What this tool does in 1-2 sentences.
side_effects: read_only          # one of read_only / network / filesystem / external
input_schema:
  type: object
  required: [target]
  properties:
    target:
      type: string
      description: "Where to point the tool"
archetype_tags: [investigator]   # optional — declares which archetypes' kits include this by default
```

## `tool.py`

The class must satisfy the Tool Protocol contract — duck-typed:

```python
"""my_custom_tool.v1 — what this does."""
from __future__ import annotations
from typing import Any
from forest_soul_forge.tools.base import (
    ToolContext, ToolResult, ToolValidationError,
)


class MyCustomTool:
    name = "my_custom_tool"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        target = args.get("target")
        if not isinstance(target, str) or not target.strip():
            raise ToolValidationError("target is required and must be a non-empty string")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        # Do the actual work, honoring ctx.constraints (allowlist, etc.)
        result = f"processed {args['target']}"
        return ToolResult(
            output={"result": result},
            metadata={},
            tokens_used=None,
            cost_usd=None,
            side_effect_summary=f"my_custom_tool: processed {args['target']!r}",
        )
```

Class name doesn't matter (the loader instantiates whatever class
satisfies the protocol shape). Keep it Pythonic-CamelCase.

## Loader contract

`load_plugins(plugins_dir, registry, catalog)` walks the dir and:

1. Skips dotfiles + non-directories
2. Reads each `<sub>/spec.yaml` — refuses with REJECTED status on
   missing or malformed YAML
3. Imports `<sub>/tool.py` as
   `forest_soul_forge.plugins.<name>_v<version>` (note: the
   namespace is reserved for plugins, doesn't clash with built-ins)
4. Verifies the loaded module has a class satisfying the Tool
   Protocol shape
5. Registers it into the supplied registry
6. Returns one `PluginLoadResult` per plugin (success or error +
   reason)

## Hot-reload

`POST /tools/reload` triggers `unload_plugins` then `load_plugins`:

```bash
curl -X POST $DAEMON/tools/reload \
  -H "X-FSF-Token: $TOKEN"
```

The unload path:

1. Drops every `forest_soul_forge.plugins.*` module from `sys.modules`
2. Walks `plugins_dir` again to determine which catalog entries to
   drop from the registry
3. Built-in tools are NOT affected (the namespace separation is the
   protection)

## Forge → install → use workflow

The Tool Forge (ADR-0030) emits exactly the plugin format:

```bash
fsf forge tool "scan a directory for files older than N days"
# → emits to data/forge/staged/<name>.v1/

fsf install tool data/forge/staged/<name>.v1/
# → copies into ~/.fsf/plugins/<name>.v1/

curl -X POST $DAEMON/tools/reload
# → tool is live in the registry

# Tool is now dispatchable from any agent whose constitution lists
# the tool key (operators add via tools_add at /birth or /spawn).
```

See `docs/runbooks/forge-tool-skill.md` for the full forge workflow.

## Operator constraints

- **Plugins inherit the same gating as built-ins.** Genre kit-tier
  ceiling, `requires_human_approval`, audit emission — all the same.
- **Plugin static-analysis at install.** The forge runs static
  analysis at codegen; install verifies again so an operator can't
  manually-edit a plugin into a state that bypasses the analysis.
- **Plugin namespace is reserved.** `forest_soul_forge.plugins.*` is
  off-limits to anything else; built-in tools must never live there.

## Where to dig deeper

- **ADR-0019 §T5**: plugin loader spec
- **Loader**: `tools/plugin_loader.py`
- **CLI**: `cli/install.py` (`fsf install tool`)
- **Tests**: `tests/unit/test_plugin_loader.py`,
  `test_tool_install_plugin.py`
- **Forge bridge**: `cli/forge_tool.py` produces this format
