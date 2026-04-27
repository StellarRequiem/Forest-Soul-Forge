"""Unit tests for the skill catalog loader — ADR-0031 T5."""
from __future__ import annotations

import textwrap
from pathlib import Path

from forest_soul_forge.core.skill_catalog import (
    empty_catalog,
    load_catalog,
)


_GOOD = textwrap.dedent("""
schema_version: 1
name: hello_skill
version: '1'
description: Test skill.
requires:
  - greet.v1
inputs:
  type: object
  properties: {who: {type: string}}
steps:
  - id: hi
    tool: greet.v1
    args:
      to: ${inputs.who}
output:
  msg: ${hi.text}
""").strip()


_BAD = "name: NotSnakeCase\n"


class TestSkillCatalog:
    def test_empty_catalog(self):
        c = empty_catalog()
        assert c.count == 0
        assert c.source_dir is None

    def test_missing_dir_returns_empty(self, tmp_path):
        c, errors = load_catalog(tmp_path / "does_not_exist")
        assert c.count == 0
        assert errors == []

    def test_loads_one_manifest(self, tmp_path):
        d = tmp_path / "skills"
        d.mkdir()
        (d / "hello_skill.v1.yaml").write_text(_GOOD, encoding="utf-8")
        c, errors = load_catalog(d)
        assert c.count == 1
        assert "hello_skill.v1" in c.skills
        assert errors == []

    def test_skips_malformed_manifest(self, tmp_path):
        d = tmp_path / "skills"
        d.mkdir()
        (d / "broken.v1.yaml").write_text(_BAD, encoding="utf-8")
        (d / "hello_skill.v1.yaml").write_text(_GOOD, encoding="utf-8")
        c, errors = load_catalog(d)
        # Good one loads; broken is reported but doesn't kill the load.
        assert c.count == 1
        assert any("broken.v1.yaml" in e for e in errors)
