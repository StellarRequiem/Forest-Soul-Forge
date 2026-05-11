"""Prompt-tool forge engine — ADR-0058 / B202.

Sister of ``forge.skill_forge``. One-stage propose pipeline:

    PROPOSE — call the active provider with a propose-prompt; parse
              the YAML reply into a ForgedToolSpec; stage as
              spec.yaml + forge.log under
              ``data/forge/tools/staged/<name>.v<version>/``.

There is no separate codegen stage. Forged prompt-template tools are
data, not code — the implementation is the generic
``PromptTemplateTool`` class registered MULTIPLE times, once per
forged spec. Install path: see
``daemon/routers/tools_forge.py::install_tool_endpoint``.

The ``forge_tool`` engine in ``forge.tool_forge`` is the OTHER tool
forge path (ADR-0030) — it generates Python module bodies for
tools that need real implementations. This engine is the simpler,
data-only path for prompt-template tools.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


SPEC_SCHEMA_VERSION = 1
PROMPT_VERSION = "3"  # B210 — runtime-model explanation for prompt_template


def _strip_fences(raw: str) -> str:
    """Same shape as forge.skill_forge._strip_fences. LLMs wrap their
    YAML output in ```yaml ... ``` markdown fences even when explicitly
    told not to; strip a single outer fence if present so parse_spec
    sees clean YAML.
    """
    s = raw.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
    if s.endswith("```"):
        s = s[: s.rfind("```")].rstrip()
    return s.strip()


@dataclass(frozen=True)
class ForgedToolSpec:
    """Validated spec.yaml shape for a forged prompt-template tool."""

    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    prompt_template: str
    archetype_tags: tuple[str, ...]
    forged_at: str
    forged_by: str
    forge_provider: str
    spec_hash: str

    @property
    def implementation(self) -> str:
        return "prompt_template_tool.v1"

    @property
    def side_effects(self) -> str:
        return "read_only"

    def to_yaml(self) -> str:
        body: dict[str, Any] = {
            "schema_version": SPEC_SCHEMA_VERSION,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "implementation": self.implementation,
            "side_effects": self.side_effects,
            "archetype_tags": list(self.archetype_tags),
            "input_schema": self.input_schema,
            "prompt_template": self.prompt_template,
            "forged_at": self.forged_at,
            "forged_by": self.forged_by,
            "forge_provider": self.forge_provider,
            "spec_hash": self.spec_hash,
        }
        return yaml.safe_dump(body, sort_keys=False, default_flow_style=False)


class ToolSpecError(Exception):
    """Raised when the LLM output doesn't validate as a ForgedToolSpec."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"{path}: {detail}")
        self.path = path
        self.detail = detail


@dataclass
class PromptToolForgeResult:
    spec: ForgedToolSpec
    staged_dir: Path
    spec_path: Path
    log_path: Path


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
_PROPOSE_SYSTEM = (
    "You are a prompt-template tool generator for the Forest Soul Forge "
    "runtime (ADR-0058). Given a plain-English description of a workflow "
    "the operator wants as a callable tool, you emit a YAML spec that "
    "binds to the generic prompt_template_tool.v1 implementation.\n\n"
    # B210 — the critical conceptual fix. Live smoke surfaced the LLM
    # producing templates that were literal expected answers with
    # placeholders, e.g. `template: "- {paragraph}\n- {paragraph}\n
    # - {paragraph}"` for a "summarize to three bullets" tool. That
    # template, after substitution, becomes a useless prompt that just
    # repeats the input. The LLM didn't understand: the template's
    # substituted text becomes a NEW prompt sent to ANOTHER LLM at
    # execute() time, so the template must be an INSTRUCTION the
    # runtime LLM will follow, not the answer.
    "HOW THIS TOOL ACTUALLY RUNS — read this carefully:\n"
    "  1. At runtime, an agent calls the forged tool with `args` (e.g.\n"
    "     `args = {paragraph: 'long text...'}`).\n"
    "  2. The runtime substitutes those args into your prompt_template\n"
    "     via Python str.format(**args) — every `{var}` is replaced\n"
    "     with the agent's input value.\n"
    "  3. The SUBSTITUTED TEXT becomes a NEW prompt that is sent to\n"
    "     an LLM (the same provider that's calling you now).\n"
    "  4. That LLM's response is what the agent gets back as the\n"
    "     tool's output.\n\n"
    "So prompt_template is an INSTRUCTION written FOR a downstream LLM.\n"
    "It must tell that LLM what to do with the substituted args. It is\n"
    "NOT the literal expected answer.\n\n"
    "CONCRETE EXAMPLE:\n"
    "  Description: 'summarize a paragraph as three concise bullets'\n"
    "  WRONG (this is what a broken forge produced before B210):\n"
    "      prompt_template: |\n"
    "        - {paragraph}\n"
    "        - {paragraph}\n"
    "        - {paragraph}\n"
    "    After substitution the downstream LLM sees three identical\n"
    "    bullets of the same input text and produces garbage.\n"
    "  RIGHT:\n"
    "      prompt_template: |\n"
    "        Summarize the paragraph below as exactly three concise\n"
    "        bullet points. Each bullet captures one key idea in one\n"
    "        sentence.\n\n"
    "        Paragraph:\n"
    "        {paragraph}\n\n"
    "        Three bullet points:\n"
    "    After substitution the downstream LLM sees a clear instruction\n"
    "    plus the input and produces the requested summary.\n\n"
    # B209 — engine strips fences before parsing but we still tell the
    # model not to emit them. Belt-and-suspenders.
    "CRITICAL OUTPUT FORMAT:\n"
    "  - Your output MUST start with `schema_version:` or `name:` —\n"
    "    NEVER with a backtick or any markdown fence.\n"
    "  - Do NOT wrap the YAML in ```yaml ... ``` or any other fence.\n"
    "  - Do NOT include any prose before or after the YAML.\n"
    "  - The very first character of your reply is a YAML key name.\n\n"
    "OUTPUT REQUIREMENTS:\n"
    "  - Output MUST be valid YAML and nothing else.\n"
    "  - Required keys: name (snake_case), version (string), description, "
    "input_schema (JSONSchema-shaped object with properties + required), "
    "prompt_template (string with {var_name} placeholders matching "
    "input_schema.properties keys).\n"
    "  - Optional: archetype_tags (list of strings).\n"
    "  - DO NOT include implementation, side_effects, forged_at, "
    "forged_by, forge_provider, or spec_hash — the engine fills those.\n"
    "  - prompt_template uses Python str.format() style — only "
    "{var_name} placeholders, no {{ }} escapes, no conditionals.\n"
    "  - Every {var_name} in prompt_template MUST appear in "
    "input_schema.properties.\n"
    "  - Keep input_schema simple: top-level required + properties with "
    "type only (string / integer / number / boolean). minimum/maximum "
    "for numbers OK.\n"
    "  - The prompt_template should be an INSTRUCTION followed by the\n"
    "    `{var}` placeholders for context, NOT a pre-written answer\n"
    "    with placeholders. See the CONCRETE EXAMPLE above.\n"
)


def _propose_user_prompt(description: str, name_override: str | None,
                          version: str, archetype_hints: str = "") -> str:
    parts = [
        "Generate a prompt-template tool spec.yaml for this workflow:",
        "",
        description.strip(),
        "",
    ]
    if archetype_hints:
        parts.extend([
            "VALID archetype_tags values — use ONLY these strings (or omit "
            "the field). Do NOT invent new archetype names.",
            archetype_hints,
            "",
        ])
    if name_override:
        parts.extend([
            f"Use name: {name_override}",
            "",
        ])
    parts.extend([
        f"Use version: {version}",
        "",
        "Output the YAML spec only.",
    ])
    return "\n".join(parts)


def _format_archetype_hints(genre_engine: Any) -> str:
    """Compact list of valid archetype names the LLM may use in
    archetype_tags. Empty string when no genre_engine is provided
    (CLI fallback). B204."""
    if genre_engine is None:
        return ""
    genres = getattr(genre_engine, "genres", None) or {}
    if not genres:
        return ""
    return "  archetypes: " + ", ".join(sorted(genres.keys()))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
_VALID_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def parse_spec(raw_yaml: str, *, forged_by: str, forge_provider: str
               ) -> ForgedToolSpec:
    """Parse + validate the LLM's YAML output.

    Returns a ForgedToolSpec on success; raises ToolSpecError pointing
    at the offending field on failure. Engine fills in forged_at,
    forge_provider, and spec_hash so the LLM doesn't have to (and so
    operators can't pass forged_by spoofing through the manifest).
    """
    try:
        body = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        raise ToolSpecError("yaml", f"unparseable: {e}") from e
    if not isinstance(body, dict):
        raise ToolSpecError("root", "must be a mapping")

    name = body.get("name")
    if not isinstance(name, str) or not _VALID_NAME.match(name):
        raise ToolSpecError(
            "name",
            f"must be snake_case [a-z][a-z0-9_]{{1,63}}; got {name!r}",
        )

    version = body.get("version")
    if version is None:
        raise ToolSpecError("version", "required")
    if not isinstance(version, (str, int)):
        raise ToolSpecError("version", f"must be string or int; got {type(version).__name__}")
    version = str(version)
    if not version:
        raise ToolSpecError("version", "must be non-empty")

    description = body.get("description") or ""
    if not isinstance(description, str):
        raise ToolSpecError("description", f"must be a string; got {type(description).__name__}")

    input_schema = body.get("input_schema")
    if not isinstance(input_schema, dict):
        raise ToolSpecError("input_schema", "required and must be an object")
    if input_schema.get("type") != "object":
        # Tolerate missing top-level type by adding it; the operator
        # may also have it correct.
        input_schema = {"type": "object", **input_schema}
    properties = input_schema.get("properties") or {}
    if not isinstance(properties, dict):
        raise ToolSpecError("input_schema.properties", "must be an object")

    prompt_template = body.get("prompt_template")
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        raise ToolSpecError(
            "prompt_template",
            "required and must be a non-empty string",
        )

    # Cross-check: every {var} in the template should be in
    # input_schema.properties. We're lenient on the inverse (extra
    # properties not used in the template are OK — operator may want
    # to expand later).
    template_vars = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", prompt_template))
    missing = template_vars - set(properties.keys())
    if missing:
        raise ToolSpecError(
            "prompt_template",
            f"references variable(s) {sorted(missing)} not declared in "
            f"input_schema.properties (which has {sorted(properties.keys())})"
        )

    archetype_tags = body.get("archetype_tags") or []
    if not isinstance(archetype_tags, list):
        raise ToolSpecError(
            "archetype_tags",
            f"must be a list when provided; got {type(archetype_tags).__name__}",
        )
    archetype_tags_t = tuple(str(t) for t in archetype_tags)

    forged_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    spec_dict = {
        "name": name,
        "version": version,
        "description": description,
        "input_schema": input_schema,
        "prompt_template": prompt_template,
        "archetype_tags": list(archetype_tags_t),
    }
    spec_hash = hashlib.sha256(
        yaml.safe_dump(spec_dict, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return ForgedToolSpec(
        name=name,
        version=version,
        description=description,
        input_schema=input_schema,
        prompt_template=prompt_template,
        archetype_tags=archetype_tags_t,
        forged_at=forged_at,
        forged_by=forged_by,
        forge_provider=forge_provider,
        spec_hash=spec_hash,
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
async def forge_prompt_tool(
    *,
    description: str,
    provider: Any,
    out_dir: Path,
    forged_by: str = "operator",
    name_override: str | None = None,
    version: str = "1",
    genre_engine: Any = None,
) -> PromptToolForgeResult:
    """Run the propose stage end-to-end.

    Returns a PromptToolForgeResult whose ``staged_dir`` contains
    spec.yaml + forge.log. Raises ToolSpecError on parse / validation
    failure.

    ``genre_engine`` (added B204): if provided, valid archetype_tags
    values are surfaced to the LLM so it doesn't invent archetype
    names. Pass ``app.state.genre_engine`` from the HTTP handler.
    """
    archetype_hints = _format_archetype_hints(genre_engine)
    user_prompt = _propose_user_prompt(description, name_override, version,
                                        archetype_hints=archetype_hints)
    log_lines = [
        f"# prompt-tool forge.log — {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"forged_by: {forged_by}",
        f"forge_provider: {getattr(provider, 'name', 'unknown')}",
        "",
        "## propose-system",
        _PROPOSE_SYSTEM,
        "",
        "## propose-user",
        user_prompt,
        "",
    ]

    raw = await provider.complete(
        user_prompt,
        system=_PROPOSE_SYSTEM,
    )
    log_lines.extend(["## raw-reply", raw, ""])

    # B209: strip markdown fences before parse. LLMs wrap YAML output
    # in ```yaml ... ``` even after being told not to.
    cleaned = _strip_fences(raw)

    # B209: stage raw output + forge.log BEFORE attempting to parse,
    # mirroring B207's pattern for skill forge. On parse failure the
    # quarantine dir stays on disk so the operator can read what the
    # LLM produced. Pre-B209 a parse failure left no on-disk artifact —
    # operator only saw the 422 detail string. Use timestamp-keyed
    # quarantine name when the LLM didn't produce a parseable name
    # (or its name fails _VALID_NAME) so collisions can't clobber
    # earlier failed forges.
    fallback_name = name_override or f"unparseable_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    quarantine_dir = (out_dir / f"{fallback_name}.v{version}").resolve()
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    (quarantine_dir / "spec_raw.yaml").write_text(cleaned, encoding="utf-8")
    quarantine_log = quarantine_dir / "forge.log"
    quarantine_log.write_text("\n".join(log_lines), encoding="utf-8")

    try:
        spec = parse_spec(
            cleaned,
            forged_by=forged_by,
            forge_provider=getattr(provider, "name", "unknown"),
        )
    except Exception as exc:
        # B209: surface the actual exception into forge.log so the
        # operator can diagnose without reading parse_spec source.
        # Mirrors B208's pattern for skill forge.
        log_lines.append("")
        log_lines.append("## parse_spec FAILED")
        log_lines.append(f"exception_type: {type(exc).__name__}")
        if isinstance(exc, ToolSpecError):
            log_lines.append(f"spec_path:   {getattr(exc, 'path', '?')}")
            log_lines.append(f"spec_detail: {getattr(exc, 'detail', str(exc))}")
        else:
            log_lines.append(f"exception_str: {str(exc)}")
        log_lines.append(f"quarantine_dir: {quarantine_dir}")
        quarantine_log.write_text("\n".join(log_lines), encoding="utf-8")
        raise

    log_lines.extend([
        "## parsed",
        f"name: {spec.name}",
        f"version: {spec.version}",
        f"spec_hash: {spec.spec_hash}",
        "",
    ])

    # Parse succeeded — relocate the quarantine to the canonical
    # name.vversion directory and write the canonical spec.yaml. Keep
    # the raw alongside for diagnostics on success too.
    staged_dir = (out_dir / f"{spec.name}.v{spec.version}").resolve()
    if staged_dir != quarantine_dir:
        staged_dir.mkdir(parents=True, exist_ok=True)
        try:
            (staged_dir / "spec_raw.yaml").write_text(cleaned, encoding="utf-8")
            for f in quarantine_dir.iterdir():
                f.unlink(missing_ok=True)
            quarantine_dir.rmdir()
        except OSError:
            pass

    spec_path = staged_dir / "spec.yaml"
    spec_path.write_text(spec.to_yaml(), encoding="utf-8")
    log_path = staged_dir / "forge.log"
    log_path.write_text("\n".join(log_lines), encoding="utf-8")

    return PromptToolForgeResult(
        spec=spec,
        staged_dir=staged_dir,
        spec_path=spec_path,
        log_path=log_path,
    )


def forge_prompt_tool_sync(**kwargs) -> PromptToolForgeResult:
    """Sync wrapper. CLI-style usage."""
    return asyncio.run(forge_prompt_tool(**kwargs))
