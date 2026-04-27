"""Skill Forge engine — ADR-0031 T1 propose-only.

Pipeline as scoped for T1:

    1. PROPOSE — call provider with the propose-prompt; parse the YAML
       reply into a SkillDef via skill_manifest.parse_manifest.
    2. STAGE   — write manifest.yaml + forge.log under
       ``data/forge/skills/staged/<name>.v<version>/``.

T2 (the skill runtime) and T4 (install path) will compose on top of
this. T1 produces an artifact the operator can review; nothing
executes the manifest yet.

For now the LLM doesn't get the list of available tools — that
requires hooking the daemon's tool catalog at CLI invocation time,
which the next CLI tranche will add. The propose prompt instructs the
LLM to use only commonly-named primitives and to mark any required
tools that don't yet exist. Operator review catches the rest.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forest_soul_forge.forge.skill_manifest import (
    ManifestError,
    SkillDef,
    parse_manifest,
)


PROMPT_VERSION = "1"

_PROPOSE_SYSTEM = (
    "You are a skill-manifest generator for the Forest Soul Forge runtime "
    "(ADR-0031). Given a plain-English description of a workflow, you emit "
    "a YAML manifest that describes the workflow as a DAG of tool calls.\n\n"
    "You MUST emit valid YAML and nothing else (no prose, no markdown "
    "fences, no preamble).\n\n"
    "Top-level fields the manifest MUST have:\n"
    "  - schema_version: 1\n"
    "  - name:           snake_case identifier\n"
    "  - version:        '1'\n"
    "  - description:    one or two sentences\n"
    "  - requires:       list of tool refs (name.vversion)\n"
    "  - inputs:         JSON Schema object describing skill inputs\n"
    "  - steps:          non-empty list of step mappings\n"
    "  - output:         mapping of name → ${expression}\n\n"
    "Each step has:\n"
    "  - id:    unique snake_case name\n"
    "  - tool:  name.vversion of the tool to dispatch\n"
    "  - args:  mapping of arg name → ${expression} or literal\n"
    "  - when:  optional ${...} predicate; step skipped if false\n"
    "  - unless:optional inverse of when\n\n"
    "For iteration, replace `tool:` with `for_each: ${list_expr}` plus a\n"
    "nested `steps:` list. Inside the inner steps, the variable\n"
    "${each.field} refers to the current iteration element.\n\n"
    "The expression language is small. You may use:\n"
    "  - Variables:    bare name + .field chain — refer to step ids,\n"
    "                  ``inputs``, or ``each`` (inside for_each).\n"
    "  - Literals:     strings, ints, floats, true/false, null.\n"
    "  - Functions:    count(list), any(list), all(list), len(value),\n"
    "                  default(value, fallback).\n"
    "  - Comparisons:  ==, !=, <, <=, >, >=, in, not in.\n"
    "  - Boolean:      and, or, not.\n"
    "  - Parentheses.\n\n"
    "Do NOT use string concatenation, arithmetic, subscripts, list\n"
    "comprehensions, or any other Python feature. If a step needs\n"
    "imperative logic, declare a tool that does it and call that.\n"
)


@dataclass
class SkillForgeResult:
    """Outcome of forge_skill()."""

    skill: SkillDef
    manifest_path: Path
    log_path: Path
    staged_dir: Path
    log_lines: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def _strip_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
    if s.endswith("```"):
        s = s[: s.rfind("```")].rstrip()
    return s.strip()


async def forge_skill(
    *,
    description: str,
    provider: Any,
    out_dir: Path,
    forged_by: str = "operator",
    name_override: str | None = None,
    version: str = "1",
) -> SkillForgeResult:
    """Run the propose stage end-to-end.

    On success, the staged folder contains ``manifest.yaml`` (the LLM's
    output, parsed and re-serialized via the SkillDef path) and
    ``forge.log`` (the prompt, the raw reply, parser results).

    Raises :class:`ManifestError` propagated from the parser if the LLM
    output didn't validate.
    """
    log: list[str] = []
    log.append(f"# skill forge.log — {_now_iso()}")
    log.append(f"forged_by: {forged_by}")
    log.append(f"provider: {getattr(provider, 'name', '?')}")
    log.append(f"prompt_version: {PROMPT_VERSION}")
    log.append(f"description:\n  {description}\n")

    propose_prompt = _build_propose_prompt(description)
    digest = _sha256(propose_prompt + "::" + PROMPT_VERSION)
    log.append(f"propose_prompt_digest: {digest}")
    log.append("=== PROPOSE: provider.complete ===")

    from forest_soul_forge.daemon.providers import TaskKind
    raw = await provider.complete(
        propose_prompt,
        task_kind=TaskKind.GENERATE,
        system=_PROPOSE_SYSTEM,
    )
    log.append("--- raw manifest yaml ---")
    log.append(raw)

    cleaned = _strip_fences(raw)
    skill = parse_manifest(cleaned)
    if name_override:
        skill = replace(skill, name=name_override)
    if version != skill.version:
        skill = replace(skill, version=version)
    skill = replace(
        skill,
        forged_at=_now_iso(),
        forged_by=forged_by,
        forge_provider=getattr(provider, "name", "unknown"),
        forge_prompt_digest=digest,
    )
    log.append(f"\n=== PROPOSE OK: {skill.name}.v{skill.version} ===")
    log.append(f"requires: {list(skill.requires)}")
    log.append(f"steps: {len(skill.steps)}")
    log.append(f"skill_hash: {skill.skill_hash}")

    staged_dir = (out_dir / f"{skill.name}.v{skill.version}").resolve()
    staged_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = staged_dir / "manifest.yaml"
    manifest_path.write_text(_serialize(skill, raw_yaml=cleaned), encoding="utf-8")
    log_path = staged_dir / "forge.log"
    log_path.write_text("\n".join(log) + "\n", encoding="utf-8")

    return SkillForgeResult(
        skill=skill, manifest_path=manifest_path,
        log_path=log_path, staged_dir=staged_dir,
        log_lines=log,
    )


def _build_propose_prompt(description: str) -> str:
    return (
        "Workflow description:\n\n"
        f"{description.strip()}\n\n"
        "Emit the YAML skill manifest now. Output ONLY the manifest — "
        "no fences, no preamble."
    )


def _serialize(skill: SkillDef, *, raw_yaml: str) -> str:
    """For T1 we trust the LLM's YAML structure (we already validated
    it) and re-emit with forge metadata appended. The runtime path
    (T2) will re-parse this file, so byte-stability isn't required —
    the skill_hash is what's content-addressed.

    We re-emit the original cleaned YAML and append the forge fields
    so the manifest stays close to what the LLM produced (operator
    review is easier when we don't re-shuffle keys).
    """
    metadata = (
        f"\n# forge metadata — not part of skill_hash.\n"
        f"forged_at: '{skill.forged_at}'\n"
        f"forged_by: '{skill.forged_by}'\n"
        f"forge_provider: '{skill.forge_provider}'\n"
        f"forge_prompt_digest: '{skill.forge_prompt_digest}'\n"
        f"skill_hash: '{skill.skill_hash}'\n"
    )
    return raw_yaml.rstrip() + "\n" + metadata


def forge_skill_sync(**kwargs) -> SkillForgeResult:
    """Sync wrapper for the CLI."""
    return asyncio.run(forge_skill(**kwargs))
