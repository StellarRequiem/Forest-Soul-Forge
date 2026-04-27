"""Tool Forge engine — ADR-0030 T1.

Pipeline as scoped for T1 (the smallest useful slice):

    1. PROPOSE — call the active provider with the propose-prompt; parse
       the YAML reply into a ToolSpec.
    2. CODEGEN — call the provider with the codegen-prompt + the spec;
       receive a Python module body.
    3. STAGE — write spec.yaml + tool.py + forge.log under
       ``data/forge/staged/<name>.v<version>/``.

Static analysis, sandboxed test runs, and install-to-plugin land in
T2/T3/T4. T1's only goal is "operator gets a folder with three files
they can review."

The forge talks to providers through the same ``ModelProvider`` Protocol
the daemon uses (ADR-0008). For CLI usage we build the provider locally
from the daemon settings — no daemon process required to forge.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SIDE_EFFECTS_VALUES = ("read_only", "network", "filesystem", "external")


# ---------------------------------------------------------------------------
# ToolSpec — the manifest the LLM emits at stage 1
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolSpec:
    """Parsed propose-stage output.

    Frozen so a spec is a *value*, not a mutable record. The CLI lets
    the operator edit between propose and codegen by replacing the
    spec — not by mutating in place.
    """

    name: str
    version: str
    description: str
    side_effects: str
    archetype_tags: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    forged_at: str
    forged_by: str
    forge_provider: str
    forge_prompt_digest: str
    risk_flags: tuple[str, ...] = ()

    def to_yaml(self) -> str:
        body: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "side_effects": self.side_effects,
            "archetype_tags": list(self.archetype_tags),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "risk_flags": list(self.risk_flags),
            "forged_at": self.forged_at,
            "forged_by": self.forged_by,
            "forge_provider": self.forge_provider,
            "forge_prompt_digest": self.forge_prompt_digest,
        }
        return yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


class ForgeError(Exception):
    """Anything that goes wrong inside the forge pipeline."""


class SpecParseError(ForgeError):
    """LLM emitted something that didn't parse as a ToolSpec."""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
# Kept as module constants so they're testable and so the propose ↔
# codegen boundary is explicit. The {placeholders} are filled at call
# time. Prompt content is the contract — changes here are the same kind
# of break as a function-signature change. Versioned via PROMPT_VERSION.
PROMPT_VERSION = "1"

_PROPOSE_SYSTEM = (
    "You are a tool-spec generator for the Forest Soul Forge runtime.\n"
    "Given a plain-English description of what a tool should do, you emit "
    "a YAML ToolSpec describing the tool's contract.\n\n"
    "You MUST emit valid YAML and nothing else (no prose, no markdown "
    "fences, no preamble). The YAML must have these top-level fields:\n"
    "  - name: snake_case identifier, < 60 chars\n"
    "  - version: string, defaults to '1'\n"
    "  - description: one or two sentences\n"
    "  - side_effects: one of read_only, network, filesystem, external\n"
    "  - archetype_tags: list of role names this tool is relevant to\n"
    "  - input_schema: a JSON Schema object describing the args\n"
    "  - output_schema: a JSON Schema object describing the result\n\n"
    "Side-effects classification rules:\n"
    "  read_only   — pure functions, in-memory only, OR reads from local "
    "files the tool already has.\n"
    "  network     — makes outbound network calls (HTTP, DNS, etc.).\n"
    "  filesystem  — writes or modifies files on the host.\n"
    "  external    — sends email, executes commands, posts to APIs that "
    "have durable side effects on the world.\n"
    "When unsure, pick the higher tier. Operators can lower it during "
    "review; they can't easily catch a too-low classification.\n"
)

_CODEGEN_SYSTEM = (
    "You are a Python tool-implementation generator for the Forest Soul "
    "Forge runtime.\n\n"
    "You will be given:\n"
    "  1. A ToolSpec (YAML) describing the tool's contract.\n"
    "  2. The Tool Protocol contract (verbatim Python).\n"
    "  3. A reference example.\n\n"
    "Emit a single Python module that satisfies the Tool Protocol. "
    "Output ONLY Python source — no markdown fences, no prose, no "
    "preamble.\n\n"
    "Style rules:\n"
    "  - Pure functions where possible; minimize global state.\n"
    "  - ``execute`` is async even when it doesn't await.\n"
    "  - Validate args in ``validate``; the runtime calls it BEFORE counter\n"
    "    increment so a typo doesn't burn budget.\n"
    "  - No dynamic imports, no eval/exec, no os.system, no subprocess\n"
    "    unless the side_effects tier is filesystem or external.\n"
    "  - All docstrings present. Module docstring matches the spec\n"
    "    description.\n"
    "  - For pure-function tools, return ``ToolResult(output=...,\n"
    "    tokens_used=None, cost_usd=None, side_effect_summary=None)``.\n"
    "  - Use type hints. Python 3.11+ targets; ``from __future__ import\n"
    "    annotations`` at the top.\n"
)

_REFERENCE_TOOL_SOURCE = '''
"""Reference: timestamp_window.v1 — a pure-function tool.

Use this as a stylistic template. Your generated tool should look like
this in shape: a class with name/version/side_effects, a validate that
raises ToolValidationError, an async execute that returns ToolResult.
"""
from __future__ import annotations
from typing import Any
from forest_soul_forge.tools.base import ToolContext, ToolResult, ToolValidationError


class TimestampWindowTool:
    name = "timestamp_window"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        if "expression" not in args:
            raise ToolValidationError("missing required arg 'expression'")

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        # ... compute the result ...
        return ToolResult(
            output={"start": "...", "end": "...", "span_seconds": 0},
            tokens_used=None, cost_usd=None,
        )
'''.strip()


def build_propose_prompt(description: str) -> str:
    """Concrete user-side prompt for stage 1."""
    return (
        f"Description of the tool to forge:\n\n{description.strip()}\n\n"
        "Emit the YAML ToolSpec now."
    )


def build_codegen_prompt(spec: ToolSpec) -> str:
    """Concrete user-side prompt for stage 2.

    Embeds the spec + Tool Protocol contract + reference example so the
    model has everything it needs in one shot.
    """
    return (
        "Spec:\n"
        f"```yaml\n{spec.to_yaml()}```\n\n"
        "Tool Protocol contract (verbatim):\n"
        f"```python\n{_TOOL_PROTOCOL_SNIPPET}\n```\n\n"
        "Reference implementation:\n"
        f"```python\n{_REFERENCE_TOOL_SOURCE}\n```\n\n"
        "Emit the Python module now. Output ONLY Python — no fences."
    )


# Embedded instead of read-from-disk to keep the engine self-contained
# (CLI can run from the wheel without a source tree). Update if
# tools.base.py's Protocol changes.
_TOOL_PROTOCOL_SNIPPET = '''
@runtime_checkable
class Tool(Protocol):
    name: str
    version: str
    side_effects: str

    def validate(self, args: dict[str, Any]) -> None: ...
    async def execute(
        self, args: dict[str, Any], ctx: ToolContext
    ) -> ToolResult: ...
'''.strip()


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def parse_spec_yaml(
    raw: str,
    *,
    forged_by: str,
    forge_provider: str,
    forge_prompt_digest: str,
) -> ToolSpec:
    """Parse the LLM's YAML reply into a ToolSpec.

    Tolerates a leading/trailing markdown fence even though the prompt
    says no fences — model behavior is empirically not always what the
    prompt asks. Anything beyond minor cleanup is a parse failure.
    """
    cleaned = _strip_fences(raw)
    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError as e:
        raise SpecParseError(f"YAML parse failed: {e}") from e
    if not isinstance(data, dict):
        raise SpecParseError(
            f"expected a YAML mapping at top level, got {type(data).__name__}"
        )

    name = str(data.get("name") or "").strip()
    if not _IDENT.match(name):
        raise SpecParseError(
            f"name must be snake_case identifier, got {name!r}"
        )
    if len(name) > 60:
        raise SpecParseError(f"name too long ({len(name)} > 60)")

    version = str(data.get("version") or "1").strip()
    if not version:
        raise SpecParseError("version must be non-empty")

    description = str(data.get("description") or "").strip()
    if not description:
        raise SpecParseError("description must be non-empty")

    side_effects = str(data.get("side_effects") or "").strip()
    if side_effects not in SIDE_EFFECTS_VALUES:
        raise SpecParseError(
            f"side_effects must be one of {list(SIDE_EFFECTS_VALUES)}; "
            f"got {side_effects!r}"
        )

    archetype_tags = tuple(str(t) for t in (data.get("archetype_tags") or []))

    input_schema = data.get("input_schema") or {"type": "object"}
    output_schema = data.get("output_schema") or {"type": "object"}
    if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
        raise SpecParseError("input_schema / output_schema must be mappings")

    return ToolSpec(
        name=name,
        version=version,
        description=description,
        side_effects=side_effects,
        archetype_tags=archetype_tags,
        input_schema=input_schema,
        output_schema=output_schema,
        forged_at=_now_iso(),
        forged_by=forged_by,
        forge_provider=forge_provider,
        forge_prompt_digest=forge_prompt_digest,
    )


_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


def _strip_fences(raw: str) -> str:
    """Remove a single leading/trailing ``` fence if present."""
    s = raw.strip()
    if s.startswith("```"):
        # Drop the first line entirely (handles ```yaml or just ```)
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1:]
    if s.endswith("```"):
        s = s[: s.rfind("```")].rstrip()
    return s.strip()


def parse_python_codegen(raw: str) -> str:
    """Strip fences and return the Python source.

    No AST validation here — that's stage 4 (static analysis) in the
    full ADR-0030 pipeline. T1 only stages the file; T2 adds the
    static checker.
    """
    return _strip_fences(raw)


# ---------------------------------------------------------------------------
# Forge engine
# ---------------------------------------------------------------------------
@dataclass
class ForgeResult:
    """Outcome of a forge_tool() run.

    All paths are absolute. ``proposed_only`` indicates a dry-run
    invocation that stopped after stage 1.
    """

    spec: ToolSpec
    spec_path: Path
    tool_path: Path | None
    log_path: Path
    staged_dir: Path
    proposed_only: bool = False
    catalog_diff_path: Path | None = None
    log_lines: list[str] = field(default_factory=list)


async def forge_tool(
    *,
    description: str,
    provider: Any,
    out_dir: Path,
    forged_by: str = "operator",
    name_override: str | None = None,
    version: str = "1",
    proposed_only: bool = False,
) -> ForgeResult:
    """Run the forge pipeline.

    ``provider`` is anything that satisfies the ``ModelProvider``
    Protocol (see daemon/providers/base.py). The engine is decoupled
    from the daemon — tests inject a fake provider with a canned
    ``complete`` response.
    """
    log: list[str] = []

    # Stage 1 — PROPOSE
    log.append(f"# forge.log — {_now_iso()}")
    log.append(f"forged_by: {forged_by}")
    log.append(f"provider: {getattr(provider, 'name', '?')}")
    log.append(f"prompt_version: {PROMPT_VERSION}")
    log.append(f"description:\n  {description}\n")

    propose_prompt = build_propose_prompt(description)
    digest = _sha256(propose_prompt + "::" + PROMPT_VERSION)
    log.append(f"propose_prompt_digest: {digest}")
    log.append("=== PROPOSE: provider.complete ===")

    from forest_soul_forge.daemon.providers import TaskKind
    raw_spec = await provider.complete(
        propose_prompt,
        task_kind=TaskKind.GENERATE,
        system=_PROPOSE_SYSTEM,
    )
    log.append("--- raw spec yaml ---")
    log.append(raw_spec)

    spec = parse_spec_yaml(
        raw_spec,
        forged_by=forged_by,
        forge_provider=getattr(provider, "name", "unknown"),
        forge_prompt_digest=digest,
    )
    if name_override:
        spec = _replace_spec(spec, name=name_override)
    if version != spec.version:
        spec = _replace_spec(spec, version=version)

    log.append(f"\n=== PROPOSE OK: {spec.name}.v{spec.version} ===")
    log.append(f"side_effects: {spec.side_effects}")
    log.append(f"archetype_tags: {list(spec.archetype_tags)}")

    # Stage early-exit for --dry-run / propose_only.
    staged_dir = (out_dir / f"{spec.name}.v{spec.version}").resolve()
    staged_dir.mkdir(parents=True, exist_ok=True)
    spec_path = staged_dir / "spec.yaml"
    log_path = staged_dir / "forge.log"
    spec_path.write_text(spec.to_yaml(), encoding="utf-8")

    if proposed_only:
        log.append("=== STOPPED at propose (proposed_only=True) ===")
        log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
        return ForgeResult(
            spec=spec, spec_path=spec_path, tool_path=None,
            log_path=log_path, staged_dir=staged_dir,
            proposed_only=True, log_lines=log,
        )

    # Stage 2 — CODEGEN
    codegen_prompt = build_codegen_prompt(spec)
    log.append("\n=== CODEGEN: provider.complete ===")
    raw_python = await provider.complete(
        codegen_prompt,
        task_kind=TaskKind.TOOL_USE,
        system=_CODEGEN_SYSTEM,
    )
    log.append("--- raw python source ---")
    log.append(raw_python)

    python_source = parse_python_codegen(raw_python)
    if "class " not in python_source:
        # Surface as a forge log warning rather than a hard error.
        # T2's static analyzer will reject if no Tool class is found.
        log.append("WARN: codegen output contains no `class ` keyword — "
                   "T2 static analysis will likely reject this.")

    tool_path = staged_dir / "tool.py"
    tool_path.write_text(python_source, encoding="utf-8")
    log.append(f"\n=== STAGED ===\n  {staged_dir}")

    # Catalog diff — what the operator should append to
    # config/tool_catalog.yaml. Generated once so the operator can
    # review it alongside spec.yaml. T4 will apply it programmatically.
    catalog_diff_path = staged_dir / "catalog-diff.yaml"
    catalog_diff_path.write_text(
        _build_catalog_diff(spec), encoding="utf-8",
    )

    log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
    return ForgeResult(
        spec=spec, spec_path=spec_path, tool_path=tool_path,
        log_path=log_path, staged_dir=staged_dir,
        catalog_diff_path=catalog_diff_path,
        log_lines=log,
    )


def _build_catalog_diff(spec: ToolSpec) -> str:
    """Return YAML the operator should append to config/tool_catalog.yaml's
    ``tools:`` block. T4 applies this programmatically; T1 just produces
    it for review."""
    entry = {
        "name": spec.name,
        "version": spec.version,
        "side_effects": spec.side_effects,
        "description": spec.description.strip().splitlines()[0],
        "archetype_tags": list(spec.archetype_tags),
        "input_schema": spec.input_schema,
    }
    return (
        "# Append this entry to config/tool_catalog.yaml under `tools:`.\n"
        "# Generated by Tool Forge (ADR-0030 T1).\n"
        + yaml.safe_dump([entry], sort_keys=False, default_flow_style=False)
    )


def _replace_spec(spec: ToolSpec, **changes) -> ToolSpec:
    """Frozen-dataclass replace helper. dataclasses.replace works but
    needs an explicit import; this is a small wrapper for clarity."""
    from dataclasses import replace
    return replace(spec, **changes)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Sync wrapper for CLI use
# ---------------------------------------------------------------------------
def forge_tool_sync(**kwargs) -> ForgeResult:
    """asyncio.run() wrapper. Convenient for the CLI which is sync."""
    return asyncio.run(forge_tool(**kwargs))
