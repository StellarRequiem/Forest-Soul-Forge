"""Unit tests for the Tool Forge engine — ADR-0030 T1.

Coverage:
  TestParseSpecYaml — happy path + every parse failure mode.
  TestParsePython   — fence stripping.
  TestForgeTool     — full pipeline with a fake provider, including
                      propose-only and full propose+codegen runs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from forest_soul_forge.forge.tool_forge import (
    ToolSpec,
    SpecParseError,
    forge_tool,
    parse_python_codegen,
    parse_spec_yaml,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fake provider — implements the ModelProvider Protocol surface that
# forge_tool needs (.name + .complete). One scripted reply per call.
# ---------------------------------------------------------------------------
@dataclass
class _FakeProvider:
    name: str = "local"
    replies: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.replies is None:
            self.replies = []
        self._idx = 0

    async def complete(self, prompt: str, **kwargs) -> str:
        if self._idx >= len(self.replies):
            raise RuntimeError(
                f"_FakeProvider out of replies "
                f"(asked {self._idx + 1}, have {len(self.replies)})"
            )
        out = self.replies[self._idx]
        self._idx += 1
        return out


# ---------------------------------------------------------------------------
# parse_spec_yaml
# ---------------------------------------------------------------------------
class TestParseSpecYaml:
    _GOOD = """
name: parse_csv
version: '1'
description: |
  Parse a CSV file and return its rows as a list of dicts.
side_effects: read_only
archetype_tags: [data_analyst]
input_schema:
  type: object
  required: [path]
  properties:
    path: {type: string}
output_schema:
  type: object
  properties:
    rows: {type: array}
"""

    def test_happy_path(self):
        spec = parse_spec_yaml(
            self._GOOD,
            forged_by="alex",
            forge_provider="local",
            forge_prompt_digest="sha256:abc",
        )
        assert spec.name == "parse_csv"
        assert spec.version == "1"
        assert spec.side_effects == "read_only"
        assert "data_analyst" in spec.archetype_tags
        assert spec.input_schema["properties"]["path"]["type"] == "string"

    def test_strips_markdown_fence(self):
        wrapped = "```yaml\n" + self._GOOD.strip() + "\n```"
        spec = parse_spec_yaml(
            wrapped,
            forged_by="x", forge_provider="x",
            forge_prompt_digest="x",
        )
        assert spec.name == "parse_csv"

    def test_invalid_name_rejected(self):
        bad = self._GOOD.replace("parse_csv", "ParseCSV")
        with pytest.raises(SpecParseError, match="snake_case"):
            parse_spec_yaml(
                bad, forged_by="x", forge_provider="x",
                forge_prompt_digest="x",
            )

    def test_unknown_side_effects_rejected(self):
        bad = self._GOOD.replace("read_only", "telekinesis")
        with pytest.raises(SpecParseError, match="side_effects must be"):
            parse_spec_yaml(
                bad, forged_by="x", forge_provider="x",
                forge_prompt_digest="x",
            )

    def test_missing_description_rejected(self):
        bad = "\n".join(
            line for line in self._GOOD.splitlines()
            if not line.startswith("description") and not line.startswith("  Parse")
        )
        with pytest.raises(SpecParseError, match="description"):
            parse_spec_yaml(
                bad, forged_by="x", forge_provider="x",
                forge_prompt_digest="x",
            )

    def test_non_mapping_rejected(self):
        with pytest.raises(SpecParseError, match="mapping"):
            parse_spec_yaml(
                "- just\n- a list\n",
                forged_by="x", forge_provider="x",
                forge_prompt_digest="x",
            )

    def test_invalid_yaml_rejected(self):
        with pytest.raises(SpecParseError, match="YAML parse"):
            parse_spec_yaml(
                "name: \"unterminated string\nversion: 1\n",
                forged_by="x", forge_provider="x",
                forge_prompt_digest="x",
            )


# ---------------------------------------------------------------------------
# parse_python_codegen
# ---------------------------------------------------------------------------
class TestParsePython:
    def test_strips_python_fence(self):
        wrapped = '```python\nclass T: pass\n```'
        out = parse_python_codegen(wrapped)
        assert out == "class T: pass"

    def test_strips_bare_fence(self):
        wrapped = '```\ndef foo():\n    return 1\n```'
        out = parse_python_codegen(wrapped)
        assert "def foo()" in out
        assert "```" not in out

    def test_passthrough_when_no_fence(self):
        src = "class T:\n    pass\n"
        out = parse_python_codegen(src)
        assert out.startswith("class T:")


# ---------------------------------------------------------------------------
# forge_tool — full pipeline
# ---------------------------------------------------------------------------
class TestForgeTool:
    _PROPOSE_REPLY = """
name: greet
version: '1'
description: Return a friendly greeting.
side_effects: read_only
archetype_tags: [companion]
input_schema:
  type: object
  required: [name]
  properties:
    name: {type: string}
output_schema:
  type: object
  properties:
    greeting: {type: string}
"""

    _CODEGEN_REPLY = '''
"""greet.v1 — return a friendly greeting."""
from __future__ import annotations
from typing import Any
from forest_soul_forge.tools.base import ToolContext, ToolResult, ToolValidationError


class GreetTool:
    name = "greet"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        if "name" not in args:
            raise ToolValidationError("missing 'name'")

    async def execute(self, args, ctx) -> ToolResult:
        return ToolResult(
            output={"greeting": f"hello {args['name']}"},
            tokens_used=None, cost_usd=None,
        )
'''

    def test_propose_only_writes_spec(self, tmp_path):
        provider = _FakeProvider(replies=[self._PROPOSE_REPLY])
        result = _run(forge_tool(
            description="say hello to a name",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
            proposed_only=True,
        ))
        assert result.proposed_only is True
        assert result.spec.name == "greet"
        assert result.spec_path.exists()
        assert result.tool_path is None
        # Log file is written.
        assert result.log_path.exists()
        # Catalog diff is NOT written for proposed-only runs.
        assert result.catalog_diff_path is None

    def test_full_run_writes_spec_python_and_diff(self, tmp_path):
        provider = _FakeProvider(replies=[
            self._PROPOSE_REPLY, self._CODEGEN_REPLY,
        ])
        result = _run(forge_tool(
            description="say hello to a name",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
        ))
        assert result.proposed_only is False
        assert result.spec.name == "greet"
        assert result.spec_path.exists()
        assert result.tool_path is not None
        assert result.tool_path.exists()
        assert "class GreetTool" in result.tool_path.read_text()
        assert result.catalog_diff_path is not None
        assert "greet" in result.catalog_diff_path.read_text()

    def test_name_override(self, tmp_path):
        provider = _FakeProvider(replies=[
            self._PROPOSE_REPLY, self._CODEGEN_REPLY,
        ])
        result = _run(forge_tool(
            description="hi",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
            name_override="custom_name",
        ))
        assert result.spec.name == "custom_name"
        assert result.staged_dir.name.startswith("custom_name")

    def test_version_override(self, tmp_path):
        provider = _FakeProvider(replies=[
            self._PROPOSE_REPLY, self._CODEGEN_REPLY,
        ])
        result = _run(forge_tool(
            description="hi",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
            version="2",
        ))
        assert result.spec.version == "2"
        assert result.staged_dir.name.endswith(".v2")

    def test_forge_log_includes_propose_and_codegen(self, tmp_path):
        provider = _FakeProvider(replies=[
            self._PROPOSE_REPLY, self._CODEGEN_REPLY,
        ])
        result = _run(forge_tool(
            description="hi",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
        ))
        log = result.log_path.read_text()
        assert "PROPOSE" in log
        assert "CODEGEN" in log
        assert "greet" in log

    def test_propose_yaml_failure_propagates(self, tmp_path):
        # Provider returns garbage at propose stage.
        provider = _FakeProvider(replies=["this is not yaml at all\n: bad"])
        with pytest.raises(SpecParseError):
            _run(forge_tool(
                description="hi",
                provider=provider,
                out_dir=tmp_path,
                forged_by="alex",
            ))


# ---------------------------------------------------------------------------
# T2 integration — analysis result threaded through the forge pipeline
# ---------------------------------------------------------------------------
class TestForgeStaticAnalysisIntegration:
    _PROPOSE = TestForgeTool._PROPOSE_REPLY
    _GOOD_CODEGEN = TestForgeTool._CODEGEN_REPLY

    _BAD_CODEGEN = '''
"""greet — but with eval."""
from forest_soul_forge.tools.base import ToolResult, ToolValidationError


class GreetTool:
    name = "greet"
    version = "1"
    side_effects = "read_only"

    def validate(self, args):
        if "name" not in args:
            raise ToolValidationError("missing 'name'")

    async def execute(self, args, ctx):
        return ToolResult(output={"greeting": eval("'hi ' + args['name']")})
'''.strip()

    def test_clean_codegen_yields_no_flags(self, tmp_path):
        provider = _FakeProvider(replies=[self._PROPOSE, self._GOOD_CODEGEN])
        result = _run(forge_tool(
            description="hi",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
        ))
        assert result.analysis is not None
        assert result.analysis.flags == ()
        assert result.staging_blocked is False

    def test_eval_codegen_blocks_staging(self, tmp_path):
        provider = _FakeProvider(replies=[self._PROPOSE, self._BAD_CODEGEN])
        result = _run(forge_tool(
            description="hi",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
        ))
        assert result.analysis is not None
        rules = [f.rule for f in result.analysis.flags]
        assert "forbidden_builtin" in rules
        assert result.staging_blocked is True
        # REJECTED.md was written alongside the staged folder.
        assert (result.staged_dir / "REJECTED.md").exists()
        # Tool is still on disk for operator inspection.
        assert result.tool_path is not None
        assert result.tool_path.exists()

    def test_analysis_recorded_in_forge_log(self, tmp_path):
        provider = _FakeProvider(replies=[self._PROPOSE, self._BAD_CODEGEN])
        result = _run(forge_tool(
            description="hi",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
        ))
        log = result.log_path.read_text()
        assert "STATIC ANALYSIS" in log
        assert "forbidden_builtin" in log
        assert "HARD FLAGS — install blocked" in log
