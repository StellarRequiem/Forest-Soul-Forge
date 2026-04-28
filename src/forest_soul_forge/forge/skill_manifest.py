"""Skill manifest schema + parser + validator — ADR-0031 T1.

A skill manifest is a YAML document describing a DAG of tool calls
with declarative data flow. This module:

* parses YAML → :class:`SkillDef`
* validates every ``${...}`` template inside the manifest references
  a binding that the runtime will provide (input, prior step id, or
  ``each`` inside a ``for_each`` block)
* hashes the canonical manifest body (excluding forge-time metadata)
  into ``skill_hash`` for content-addressed identity

The skill runtime (ADR-0031 T2) consumes :class:`SkillDef` directly.
This module deliberately does NOT execute anything.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from forest_soul_forge.forge.skill_expression import (
    ExpressionError,
    Template,
    compile_arg,
    parse_template,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ManifestError(Exception):
    """Anything wrong with a skill manifest. ``path`` describes the
    field; ``detail`` is operator-facing."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"{path}: {detail}")
        self.path = path
        self.detail = detail


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ToolStep:
    """A leaf step that dispatches one tool call."""

    id: str
    tool: str  # "name.vversion" — leaf step only ever calls a tool
    args: dict[str, Any]   # Template | _LiteralArg | _DictArg | _ListArg
    when: Template | None = None
    unless: Template | None = None


@dataclass(frozen=True)
class ForEachStep:
    """A step that iterates over a list, dispatching nested steps once
    per element. Inside the inner steps, the variable ``each`` is
    bound to the current iteration's element."""

    id: str
    items: Template  # expression that evaluates to a list
    steps: tuple["StepNode", ...]  # nested steps
    when: Template | None = None
    unless: Template | None = None


StepNode = ToolStep | ForEachStep


@dataclass(frozen=True)
class SkillDef:
    """Parsed, validated skill manifest. Frozen — the runtime treats
    a SkillDef as a value, not a record to mutate."""

    schema_version: int
    name: str
    version: str
    description: str
    requires: tuple[str, ...]
    inputs_schema: dict[str, Any]
    steps: tuple[StepNode, ...]
    output: dict[str, Template]
    skill_hash: str
    # Forge-time metadata. NOT part of skill_hash so a re-forge with
    # the same description from a different operator doesn't perturb
    # the identity.
    forged_at: str | None = None
    forged_by: str | None = None
    forge_provider: str | None = None
    forge_prompt_digest: str | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse_manifest(yaml_text: str) -> SkillDef:
    """Parse a manifest YAML string into a :class:`SkillDef`.

    Raises :class:`ManifestError` with a path-prefixed detail on any
    schema or expression problem. The manifest is rejected on the
    first issue — we don't accumulate errors. Operator review is
    quick when each error is concrete.
    """
    cleaned = _strip_fences(yaml_text)
    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError as e:
        raise ManifestError("(root)", f"YAML parse failed: {e}") from e
    if not isinstance(data, dict):
        raise ManifestError("(root)", "manifest must be a YAML mapping")

    schema_version = int(data.get("schema_version") or 1)
    if schema_version != 1:
        raise ManifestError(
            "schema_version",
            f"only schema_version=1 is supported (got {schema_version})",
        )

    name = _str_field(data, "name", required=True)
    if not _IDENT.match(name):
        raise ManifestError("name", f"must be snake_case identifier (got {name!r})")
    version = _str_field(data, "version", required=False, default="1")
    description = _str_field(data, "description", required=True)

    requires = data.get("requires") or []
    if not isinstance(requires, list):
        raise ManifestError("requires", "must be a list of tool refs")
    requires_tuple = tuple(str(r) for r in requires)
    for ref in requires_tuple:
        if "." not in ref or not ref.split(".v")[-1]:
            raise ManifestError(
                f"requires[{ref!r}]",
                "tool refs must be of the form name.vversion (e.g. timestamp_window.v1)",
            )

    inputs_schema = data.get("inputs") or {"type": "object"}
    if not isinstance(inputs_schema, dict):
        raise ManifestError("inputs", "must be a JSON Schema object")

    raw_steps = data.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ManifestError("steps", "must be a non-empty list")
    steps, declared_ids = _parse_steps(raw_steps, path="steps", ambient={"inputs"})

    raw_output = data.get("output") or {}
    if not isinstance(raw_output, dict):
        raise ManifestError("output", "must be a mapping of name → expression")
    output: dict[str, Template] = {}
    for k, v in raw_output.items():
        try:
            tpl = parse_template(str(v))
        except ExpressionError as e:
            raise ManifestError(f"output.{k}", f"{e}") from e
        # Top-level output references must be visible names: declared
        # step ids, ``inputs``, or constant text.
        _check_refs(tpl, declared=declared_ids | {"inputs"},
                    path=f"output.{k}")
        output[k] = tpl

    forged_at = data.get("forged_at")
    forged_by = data.get("forged_by")
    forge_provider = data.get("forge_provider")
    forge_prompt_digest = data.get("forge_prompt_digest")

    skill_hash = _compute_hash(
        name=name, version=version, description=description,
        requires=requires_tuple, inputs_schema=inputs_schema,
        raw_steps=raw_steps, raw_output=raw_output,
    )
    return SkillDef(
        schema_version=schema_version,
        name=name, version=version, description=description,
        requires=requires_tuple,
        inputs_schema=inputs_schema,
        steps=steps, output=output,
        skill_hash=skill_hash,
        forged_at=str(forged_at) if forged_at else None,
        forged_by=str(forged_by) if forged_by else None,
        forge_provider=str(forge_provider) if forge_provider else None,
        forge_prompt_digest=str(forge_prompt_digest) if forge_prompt_digest else None,
    )


# ---------------------------------------------------------------------------
# Step parsing
# ---------------------------------------------------------------------------
def _parse_steps(
    raw: list[Any],
    *,
    path: str,
    ambient: set[str],
) -> tuple[tuple[StepNode, ...], set[str]]:
    """Parse a steps list. Returns (parsed_tuple, declared_ids).

    ``ambient`` is the set of names already in scope at this depth
    (top level: ``{"inputs"}``; inside a for_each: also ``{"each"}``).
    Each parsed step adds its id to the *next* step's ambient set so
    later steps can reference earlier ones, but the FIRST step can
    only reference ambient names.
    """
    steps: list[StepNode] = []
    declared: set[str] = set()
    seen_ids: set[str] = set()
    for i, raw_step in enumerate(raw):
        sub_path = f"{path}[{i}]"
        if not isinstance(raw_step, dict):
            raise ManifestError(sub_path, "step must be a mapping")
        sid = _str_field(raw_step, "id", required=True, path=sub_path)
        if not _IDENT.match(sid):
            raise ManifestError(
                f"{sub_path}.id",
                f"id must be snake_case identifier (got {sid!r})",
            )
        if sid in seen_ids:
            raise ManifestError(
                f"{sub_path}.id", f"duplicate step id {sid!r}",
            )
        seen_ids.add(sid)

        scope = ambient | declared

        when = _opt_template(raw_step.get("when"), f"{sub_path}.when", scope)
        unless = _opt_template(
            raw_step.get("unless"), f"{sub_path}.unless", scope,
        )

        if "for_each" in raw_step:
            items_raw = raw_step["for_each"]
            try:
                items_tpl = parse_template(str(items_raw))
            except ExpressionError as e:
                raise ManifestError(f"{sub_path}.for_each", f"{e}") from e
            _check_refs(items_tpl, declared=scope, path=f"{sub_path}.for_each")
            inner_raw = raw_step.get("steps")
            if not isinstance(inner_raw, list) or not inner_raw:
                raise ManifestError(
                    f"{sub_path}.steps",
                    "for_each requires a non-empty inner steps list",
                )
            inner_steps, _inner_ids = _parse_steps(
                inner_raw,
                path=f"{sub_path}.steps",
                ambient=scope | {"each"},
            )
            steps.append(ForEachStep(
                id=sid, items=items_tpl, steps=inner_steps,
                when=when, unless=unless,
            ))
            declared.add(sid)
            continue

        # Leaf tool step.
        tool = _str_field(raw_step, "tool", required=True, path=sub_path)
        if "." not in tool:
            raise ManifestError(
                f"{sub_path}.tool",
                "tool ref must be of the form name.vversion",
            )
        args_raw = raw_step.get("args") or {}
        if not isinstance(args_raw, dict):
            raise ManifestError(
                f"{sub_path}.args", "args must be a mapping",
            )
        # ``compile_arg`` preserves YAML structure (dict/list) end-to-end so
        # tools that strict-check args as ``dict`` or ``list[str]`` receive
        # the right shape at runtime. Pre-fix this was ``parse_template(str(v))``
        # which stringified everything → blocked delegate.v1 from manifests.
        args: dict[str, Any] = {}
        for k, v in args_raw.items():
            try:
                tpl = compile_arg(v)
            except ExpressionError as e:
                raise ManifestError(f"{sub_path}.args.{k}", f"{e}") from e
            _check_refs(tpl, declared=scope, path=f"{sub_path}.args.{k}")
            args[str(k)] = tpl
        steps.append(ToolStep(
            id=sid, tool=tool, args=args, when=when, unless=unless,
        ))
        declared.add(sid)
    return tuple(steps), declared


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


def _str_field(
    data: dict[str, Any], key: str,
    *, required: bool, default: str = "", path: str = "",
) -> str:
    val = data.get(key)
    if val is None:
        if required:
            raise ManifestError(
                f"{path + '.' if path else ''}{key}",
                "required field missing",
            )
        return default
    return str(val).strip()


def _opt_template(
    raw: Any, path: str, scope: set[str],
) -> Template | None:
    if raw is None:
        return None
    try:
        tpl = parse_template(str(raw))
    except ExpressionError as e:
        raise ManifestError(path, f"{e}") from e
    _check_refs(tpl, declared=scope, path=path)
    return tpl


def _check_refs(tpl: Template, *, declared: set[str], path: str) -> None:
    refs = tpl.references()
    bad = sorted(r for r in refs if r not in declared)
    if bad:
        raise ManifestError(
            path,
            f"references undefined name(s): {bad} "
            f"(in scope: {sorted(declared)})",
        )


def _strip_fences(raw: str) -> str:
    """Same shape as forge.tool_forge._strip_fences — kept private here
    to avoid cross-module leakage. LLMs add fences even when asked
    not to."""
    s = raw.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
    if s.endswith("```"):
        s = s[: s.rfind("```")].rstrip()
    return s.strip()


def _compute_hash(
    *,
    name: str, version: str, description: str,
    requires: tuple[str, ...],
    inputs_schema: dict[str, Any],
    raw_steps: list[Any],
    raw_output: dict[str, Any],
) -> str:
    """Content-addressed identity. Forge-time metadata (forged_at,
    forged_by, etc.) is excluded so a re-forge with the same logic
    from a different operator gets the same hash."""
    body = {
        "name": name,
        "version": version,
        "description": description,
        "requires": list(requires),
        "inputs": inputs_schema,
        "steps": raw_steps,
        "output": raw_output,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
