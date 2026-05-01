# Forge tool / skill — operator runbook

**ADRs:** [ADR-0030](../decisions/ADR-0030-tool-forge.md) (Tool Forge),
[ADR-0031](../decisions/ADR-0031-skill-forge.md) (Skill Forge)
**Status:** Accepted (T1–T4 / T1–T8 shipped 2026-04-30)

The Forge is the path from English description → installed tool or
skill, with operator review at every gate. Two parallel pipelines:

- **Tool Forge** (ADR-0030) — generates a Python tool implementation
- **Skill Forge** (ADR-0031) — generates a YAML skill manifest

Both go through six stages with operator decision points; nothing
ships to the live catalog without explicit confirmation.

## The six stages

```
DESCRIBE → PROPOSE → CODEGEN → REVIEW → PROVE → INSTALL
   ↓        ↓          ↓         ↓        ↓        ↓
operator   LLM    LLM + static  human   sandboxed operator
 types     emits   analysis    reads    pytest    installs
                                diff
```

Each stage can refuse the next. Static analysis flags hard issues
(forbidden builtins, network in read_only tier, missing tokens
plumbing); sandbox-pytest fails the prove stage if generated tests
crash. The operator sees a REJECTED.md alongside any forge that
made it through codegen but failed prove.

## Forge a tool

```bash
fsf forge tool "scan a directory for files older than N days" \
  --version 1 \
  --provider local \
  --out-dir data/forge/staged
```

The forge emits to `data/forge/staged/<name>.v<version>/` with:

- `spec.yaml` — the proposed ToolSpec
- `tool.py` — the generated implementation
- `test_tool.py` — generated tests (run in the sandbox at the prove stage)
- `STATIC_ANALYSIS.md` — flag report from static analysis
- `PROVE.md` — sandbox pytest output
- `REJECTED.md` (only if any hard flag fired) — what blocked install

Useful flags:

- `--dry-run` — stop after PROPOSE; no files written
- `--no-prompt` — skip y/N gates (scripted use)
- `--force` — stage even with hard flags (operator owns the risk; the
  REJECTED.md persists alongside)
- `--no-prove` — skip sandbox pytest (useful in environments without
  pytest)

## Install a forged tool

```bash
fsf install tool data/forge/staged/<name>.v1/
```

Default mode is **plugin**: drops the tool into `~/.fsf/plugins/`
where `POST /tools/reload` picks it up without daemon restart. Use
`--system` for built-in mode (requires daemon restart).

Install verifies:

1. Spec parses
2. Tool.py imports cleanly
3. Static analysis re-runs (in case the staged dir was edited
   manually after forge)
4. The tool's name + version don't collide with an existing
   registration

After install, the tool is live in the registry and dispatchable
from any agent whose constitution lists it.

## Forge a skill

```bash
fsf forge skill "morning sweep: log_scan, baseline_compare,
                 alert on findings" \
  --version 1
```

Emits to `data/forge/staged/<name>.v<version>/skill.yaml` plus the
same per-stage docs (STATIC_ANALYSIS.md, PROVE.md, REJECTED.md). The
skill manifest's `requires:` field must list tools that exist in the
loaded catalog; the install path verifies this.

## Install a forged skill

```bash
fsf install skill data/forge/staged/<name>.v1/skill.yaml
```

Drops the manifest into `examples/skills/` (or wherever
`DaemonSettings.skill_install_dir` points). After install, fire
`POST /skills/reload` to pick it up.

## Skill manifest contract

Each skill is a YAML manifest with:

```yaml
schema_version: 1
name: my_skill
version: '1'
description: |
  What this skill does in 1-2 sentences.
requires:
  - log_scan.v1
  - memory_write.v1
inputs:
  type: object
  required: [target_path]
  properties:
    target_path: {type: string}
steps:
  - id: scan
    tool: log_scan.v1
    args:
      path: ${inputs.target_path}
      pattern: 'ERROR'
  - id: write
    tool: memory_write.v1
    args:
      content: "found ${count(scan.matches)} errors"
      layer: episodic
output:
  count: ${count(scan.matches)}
```

Interpolation language: `${step.field}`, with comparison operators
(`>=`, `==`, `in`, `not in`) and registered functions (`count`, `any`,
`all`, `len`, `default`).

## Static analysis flags worth knowing

The full list lives in `src/forest_soul_forge/forge/static_analysis.py`,
but the load-bearing categories:

| Flag kind | Meaning | Hard / Soft |
|---|---|:---:|
| `forbidden_builtin` | `eval`, `exec`, `compile` | hard |
| `os_system` | shell-invoking call | hard |
| `dynamic_import` | `__import__` | hard |
| `builtins_access` | `__builtins__` reach | hard |
| `network_import_in_read_only` | imports requests in read_only tier | soft |
| `filesystem_write_in_low_tier` | writes in network or below | soft |
| `subprocess_used` | imports subprocess | soft |
| `missing_tokens_plumbing` | LLM call without tokens_used in result | soft |
| `hedge_marker` | TODO / FIXME / hedge in body | soft |

Hard flags block install (use `--force` to override). Soft flags
write to STATIC_ANALYSIS.md but don't block.

## Audit events emitted by the forge

| Event | When |
|---|---|
| `forge_tool_proposed` | LLM emits a ToolSpec |
| `forge_tool_installed` | tool lands in the registry |
| `forge_skill_proposed` | LLM emits a manifest |
| `forge_skill_installed` | manifest lands in skill_install_dir |

## Where to dig deeper

- **ADR-0030**: Tool Forge spec
- **ADR-0031**: Skill Forge spec
- **Engines**: `forge/tool_forge.py`, `forge/skill_forge.py`
- **Static analysis**: `forge/static_analysis.py`
- **Sandbox**: `forge/sandbox.py` (sandboxed pytest runner)
- **CLI**: `cli/forge_tool.py`, `cli/forge_skill.py`,
  `cli/install.py`
