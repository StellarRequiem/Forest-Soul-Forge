"""Unit tests for the Skill Forge engine — ADR-0031 T1.

The engine is propose-only in T1. These tests exercise:
  - Successful propose stage with a valid manifest from the LLM
  - Manifest-validation failures propagating
  - name/version override
  - forge.log content
"""
from __future__ import annotations

import asyncio
import textwrap
from dataclasses import dataclass

import pytest

from forest_soul_forge.forge.skill_forge import forge_skill
from forest_soul_forge.forge.skill_manifest import ManifestError


def _run(coro):
    return asyncio.run(coro)


@dataclass
class _FakeProvider:
    name: str = "local"
    replies: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.replies is None:
            self.replies = []
        self._idx = 0

    async def complete(self, prompt: str, **kwargs) -> str:
        out = self.replies[self._idx]
        self._idx += 1
        return out


_GOOD_MANIFEST = textwrap.dedent("""
schema_version: 1
name: lookup_pipeline
version: '1'
description: Run a lookup pipeline.
requires:
  - flow_summary.v1
  - lookup.v1
inputs:
  type: object
  required: [pcap]
  properties:
    pcap: {type: string}
steps:
  - id: flows
    tool: flow_summary.v1
    args:
      pcap: ${inputs.pcap}
output:
  flows: ${flows}
""").strip()


class TestForgeSkill:
    def test_happy_path(self, tmp_path):
        provider = _FakeProvider(replies=[_GOOD_MANIFEST])
        result = _run(forge_skill(
            description="run flow lookup",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
        ))
        assert result.skill.name == "lookup_pipeline"
        assert result.skill.skill_hash.startswith("sha256:")
        assert result.manifest_path.exists()
        assert result.log_path.exists()
        # Forge metadata stamped onto the SkillDef.
        assert result.skill.forged_by == "alex"
        assert result.skill.forge_provider == "local"

    def test_invalid_manifest_propagates(self, tmp_path):
        bad = _GOOD_MANIFEST.replace(
            "name: lookup_pipeline", "name: NotSnakeCase",
        )
        provider = _FakeProvider(replies=[bad])
        with pytest.raises(ManifestError, match="snake_case"):
            _run(forge_skill(
                description="x",
                provider=provider,
                out_dir=tmp_path,
                forged_by="alex",
            ))

    def test_name_override(self, tmp_path):
        provider = _FakeProvider(replies=[_GOOD_MANIFEST])
        result = _run(forge_skill(
            description="x",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
            name_override="custom_skill",
        ))
        assert result.skill.name == "custom_skill"
        assert result.staged_dir.name.startswith("custom_skill")

    def test_version_override(self, tmp_path):
        provider = _FakeProvider(replies=[_GOOD_MANIFEST])
        result = _run(forge_skill(
            description="x",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
            version="3",
        ))
        assert result.skill.version == "3"
        assert result.staged_dir.name.endswith(".v3")

    def test_forge_log_includes_propose_section(self, tmp_path):
        provider = _FakeProvider(replies=[_GOOD_MANIFEST])
        result = _run(forge_skill(
            description="hi",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
        ))
        log = result.log_path.read_text()
        assert "PROPOSE" in log
        assert "lookup_pipeline" in log

    def test_manifest_persists_with_forge_metadata_appended(self, tmp_path):
        provider = _FakeProvider(replies=[_GOOD_MANIFEST])
        result = _run(forge_skill(
            description="hi",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
        ))
        text = result.manifest_path.read_text()
        # Forge metadata block was appended to the original manifest.
        assert "forged_by:" in text
        assert "skill_hash:" in text
        # Original logic still present.
        assert "lookup_pipeline" in text

    def test_strips_markdown_fence(self, tmp_path):
        wrapped = "```yaml\n" + _GOOD_MANIFEST + "\n```"
        provider = _FakeProvider(replies=[wrapped])
        result = _run(forge_skill(
            description="x",
            provider=provider,
            out_dir=tmp_path,
            forged_by="alex",
        ))
        assert result.skill.name == "lookup_pipeline"
