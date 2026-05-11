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


PROMPT_VERSION = "3"  # B208 — ref-scope rules + step shape strictness

_PROPOSE_SYSTEM = (
    "You are a skill-manifest generator for the Forest Soul Forge runtime "
    "(ADR-0031). Given a plain-English description of a workflow, you emit "
    "a YAML manifest that describes the workflow as a DAG of tool calls.\n\n"
    "You MUST emit valid YAML and nothing else (no prose, no markdown "
    "fences, no preamble).\n\n"
    # B207 — the critical YAML caveat. The ${...} expression syntax uses
    # curly braces, which clash with YAML flow-mapping delimiters. A line
    # like `output: { summary: ${step.out.text} }` blows up the parser
    # because the parser sees a nested `{` it doesn't expect. Always use
    # block style for any container that holds ${...} values.
    "CRITICAL YAML STYLE RULE:\n"
    "  - Use BLOCK-STYLE YAML for `output:`, `inputs:`, `args:`,\n"
    "    `properties:`, and `steps:`. NEVER use flow-style `{...}`\n"
    "    or `[...]` for those fields.\n"
    "  - The expression syntax `${name.field}` contains curly braces.\n"
    "    Flow-style YAML uses the same curly braces as mapping\n"
    "    delimiters. They CONFLICT. The parser will fail.\n"
    "  - Correct (block style):\n"
    "        output:\n"
    "          summary: ${step.out.text}\n"
    "  - Wrong (flow style — DO NOT EMIT):\n"
    "        output: { summary: ${step.out.text} }\n\n"
    # B208 — explicit ref-scope rules. The parser uses scope-checked
    # references: only `inputs`, `each` (inside for_each), and prior
    # step ids are in scope as bare names. Bare references to an
    # input field name (e.g. `${audit_chain_path}` when the schema
    # has `inputs.audit_chain_path`) raise ManifestError because the
    # bare token isn't in scope — you must say `${inputs.audit_chain_path}`.
    # This rule existed in the parser since T1 but the prompt described
    # it ambiguously as "bare name + .field chain", which the LLM
    # interpreted as license to drop the `inputs.` prefix.
    "CRITICAL REFERENCE SYNTAX RULE:\n"
    "  - To reference a SKILL INPUT, you MUST write `${inputs.<name>}`.\n"
    "    Bare `${<name>}` for an input field will FAIL — the parser\n"
    "    only puts `inputs` (the whole object) in scope, not its\n"
    "    individual fields.\n"
    "  - To reference a previous STEP's output, write\n"
    "    `${<step_id>.out.<field>}` — or `${<step_id>.out}` for the\n"
    "    whole output. The step id is the value of `id:`.\n"
    "  - Inside a for_each body, the current element is `${each}` or\n"
    "    `${each.<field>}`.\n"
    "  - Examples (assuming inputs.user_query and steps `search`,\n"
    "    `summarize`):\n"
    "        Correct: file_path: ${inputs.audit_chain_path}\n"
    "        Wrong:   file_path: ${audit_chain_path}\n"
    "        Correct: prompt: ${summarize.out.text}\n"
    "        Wrong:   prompt: ${summarize}\n\n"
    "Top-level fields the manifest MUST have:\n"
    "  - schema_version: 1\n"
    "  - name:           snake_case identifier\n"
    "  - version:        '1'\n"
    "  - description:    one or two sentences\n"
    "  - requires:       list of tool refs (name.vversion — ALWAYS\n"
    "                    versioned, e.g. `llm_think.v1`, never bare\n"
    "                    `llm_think`)\n"
    "  - inputs:         JSON Schema object describing skill inputs\n"
    "  - steps:          non-empty list of step mappings\n"
    "  - output:         mapping of name → ${expression}\n\n"
    "Each step has EXACTLY these fields:\n"
    "  - id:    unique snake_case name (required)\n"
    "  - tool:  name.vversion of the tool to dispatch (required;\n"
    "           versioned, e.g. `llm_think.v1`)\n"
    "  - args:  mapping of arg name → ${expression} or literal\n"
    "           (this is the ONLY way to pass data into a tool —\n"
    "           there is no `inputs:` field on a step)\n"
    "  - when:  optional ${...} predicate; step skipped if false\n"
    "  - unless:optional inverse of when\n\n"
    # B208 — strict step shape callout. The B207 smoke produced a step
    # with `inputs: ${prior.out}` (string at the wrong field). The
    # parser silently ignored it (args defaulted to {}), then the
    # ${audit_chain_path} ref blew up — but the underlying confusion
    # was the LLM treating step-level `inputs:` as a thing.
    "Do NOT put `inputs:` on a step. `inputs` is a TOP-LEVEL field that\n"
    "declares the skill's input schema. Step data flows in via `args:`.\n\n"
    "For iteration, replace `tool:` with `for_each: ${list_expr}` plus a\n"
    "nested `steps:` list. Inside the inner steps, the variable\n"
    "${each.field} refers to the current iteration element.\n\n"
    "The expression language is small. You may use:\n"
    "  - Variables:    `inputs.<field>`, `<step_id>.out` (and\n"
    "                  `<step_id>.out.<field>`), `each` (in for_each).\n"
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
    tool_catalog: Any = None,
) -> SkillForgeResult:
    """Run the propose stage end-to-end.

    On success, the staged folder contains ``manifest.yaml`` (the LLM's
    output, parsed and re-serialized via the SkillDef path) and
    ``forge.log`` (the prompt, the raw reply, parser results).

    Raises :class:`ManifestError` propagated from the parser if the LLM
    output didn't validate.

    ``tool_catalog`` (added B204): if provided, the engine injects a
    compact summary of every tool in the catalog into the propose
    user-prompt. Without it the LLM has to guess tool names from
    common-sense knowledge — that produced the hallucinated
    ``text_summarizer.v1`` reference observed in B203 smoke
    (forge_skill_proposed at chain seq #6321 referenced a tool that
    doesn't exist). Pass ``app.state.tool_catalog`` from the HTTP
    handler; CLI usage can pass the loaded catalog from
    ``daemon.lifespan`` or pass None and accept the hallucination
    risk.
    """
    log: list[str] = []
    log.append(f"# skill forge.log — {_now_iso()}")
    log.append(f"forged_by: {forged_by}")
    log.append(f"provider: {getattr(provider, 'name', '?')}")
    log.append(f"prompt_version: {PROMPT_VERSION}")
    log.append(f"description:\n  {description}\n")

    catalog_summary = _format_catalog_for_propose(tool_catalog)
    if catalog_summary:
        log.append(f"catalog_injected: {catalog_summary.count(chr(10))} tools")

    propose_prompt = _build_propose_prompt(description, catalog_summary)
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

    # B207: stage the raw output + forge.log BEFORE attempting to parse.
    # Pre-B207 this whole block was AFTER parse_manifest, so when the
    # LLM produced invalid YAML (e.g., the flow-mapping vs ${} clash
    # in the B204-shipped propose), the staged dir was never created
    # and the operator lost all diagnostic data. Now we always have a
    # quarantine dir with the raw reply + log, even when parse fails —
    # operator can read forge.log to see what went wrong, edit
    # manifest_raw.yaml by hand, and re-attempt install.
    #
    # If the LLM emitted a parseable name we use that for the staged
    # dir; otherwise fall back to a timestamp-keyed quarantine name so
    # multiple failed forges don't collide on disk.
    fallback_name = (name_override or f"unparseable_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}")
    raw_staged_dir = (out_dir / f"{fallback_name}.v{version}").resolve()
    raw_staged_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_staged_dir / "manifest_raw.yaml"
    raw_path.write_text(cleaned, encoding="utf-8")
    raw_log_path = raw_staged_dir / "forge.log"
    raw_log_path.write_text("\n".join(log) + "\n", encoding="utf-8")

    try:
        skill = parse_manifest(cleaned)
    except Exception as exc:
        # Parse failed. The raw output + log are already on disk for
        # diagnostics; surface the failure with the path so the
        # caller can point the operator at the quarantine dir.
        # B208: capture the exception type + str into forge.log so the
        # operator can see WHY parse failed without grepping the
        # parser source. ManifestError already carries a useful
        # path-prefixed detail; generic exceptions still get their
        # repr captured for completeness.
        log.append("\n=== PROPOSE FAIL: parse_manifest raised ===")
        log.append(f"exception_type: {type(exc).__name__}")
        if isinstance(exc, ManifestError):
            log.append(f"manifest_path:  {getattr(exc, 'path', '?')}")
            log.append(f"manifest_msg:   {getattr(exc, 'detail', str(exc))}")
        else:
            log.append(f"exception_str:  {str(exc)}")
        log.append(f"raw_staged_dir: {raw_staged_dir}")
        raw_log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
        raise

    # Parse succeeded — relocate the raw quarantine into the canonical
    # name.vversion staged dir (in case fallback_name was used) and
    # rewrite the manifest in the canonical serialized form.
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

    canonical_staged_dir = (out_dir / f"{skill.name}.v{skill.version}").resolve()
    if canonical_staged_dir != raw_staged_dir:
        # Move the quarantine contents into the canonical dir. Keep
        # the raw file for diagnostics even on success.
        canonical_staged_dir.mkdir(parents=True, exist_ok=True)
        try:
            (canonical_staged_dir / "manifest_raw.yaml").write_text(cleaned, encoding="utf-8")
            # Remove the quarantine dir last (best-effort).
            for f in raw_staged_dir.iterdir():
                f.unlink(missing_ok=True)
            raw_staged_dir.rmdir()
        except OSError:
            pass

    manifest_path = canonical_staged_dir / "manifest.yaml"
    manifest_path.write_text(_serialize(skill, raw_yaml=cleaned), encoding="utf-8")
    log_path = canonical_staged_dir / "forge.log"
    log_path.write_text("\n".join(log) + "\n", encoding="utf-8")

    return SkillForgeResult(
        skill=skill, manifest_path=manifest_path,
        log_path=log_path, staged_dir=canonical_staged_dir,
        log_lines=log,
    )


def _build_propose_prompt(description: str, catalog_summary: str = "") -> str:
    parts = [
        "Workflow description:\n",
        description.strip(),
        "",
    ]
    if catalog_summary:
        parts.extend([
            "AVAILABLE TOOLS — these are the ONLY tools you may reference in",
            "`requires` and in step `tool:` fields. Do NOT invent or rename",
            "tools. If no listed tool fits, the closest match is",
            "llm_think.v1 — which can do arbitrary reasoning given a prompt.",
            "",
            catalog_summary,
            "",
        ])
    parts.extend([
        "Emit the YAML skill manifest now. Output ONLY the manifest — "
        "no fences, no preamble.",
    ])
    return "\n".join(parts)


def _format_catalog_for_propose(catalog: Any) -> str:
    """Compact one-line-per-tool summary the LLM can reason against.

    Returns empty string when ``catalog`` is None (CLI fallback path).
    Format matches what ADR-0058 / B204 settled on:

        - <name>.v<version> [<side_effects>]: <first sentence of description>

    Side-effects bracket lets the LLM filter for read-only tools when
    the operator's description suggests a read-only workflow. Keeping
    the description to one sentence avoids blowing past prompt-length
    caps on big catalogs (54 tools at ~80 chars each = ~4.3KB, well
    under the 32KB MAX_PROMPT_LEN).
    """
    if catalog is None:
        return ""
    tools = getattr(catalog, "tools", None)
    if not tools:
        return ""
    lines: list[str] = []
    for key in sorted(tools):
        td = tools[key]
        # First sentence of description, capped.
        desc = (getattr(td, "description", "") or "").strip()
        first_sentence = desc.split(".", 1)[0].split("\n", 1)[0].strip()[:120]
        side_effects = getattr(td, "side_effects", "?")
        lines.append(f"  - {key} [{side_effects}]: {first_sentence}")
    return "\n".join(lines)


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
