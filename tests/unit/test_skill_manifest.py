"""Unit tests for the skill manifest parser — ADR-0031 T1.

Coverage:
  TestParseManifest    — happy path, every required-field error,
                         expression-validation propagation.
  TestStepReferences   — step args / when / unless reject undefined
                         references.
  TestForEach          — nested steps + ``each`` binding scoping.
  TestSkillHash        — hash stability + exclusion of forge metadata.
"""
from __future__ import annotations

import textwrap

import pytest

from forest_soul_forge.forge.skill_manifest import (
    ForEachStep,
    ManifestError,
    SkillDef,
    ToolStep,
    parse_manifest,
)


_GOOD = textwrap.dedent("""
schema_version: 1
name: scan_for_anomalies
version: '1'
description: |
  Walk a packet capture and find unusual flows.
requires:
  - timestamp_window.v1
  - flow_summary.v1
inputs:
  type: object
  required: [pcap_path]
  properties:
    pcap_path: {type: string}
steps:
  - id: window
    tool: timestamp_window.v1
    args:
      expression: 'last 1 hours'
  - id: flows
    tool: flow_summary.v1
    args:
      pcap: ${inputs.pcap_path}
      start: ${window.start}
      end: ${window.end}
output:
  flows: ${flows}
  count: ${count(flows.results)}
""").strip()


class TestParseManifest:
    def test_happy_path(self):
        sd = parse_manifest(_GOOD)
        assert isinstance(sd, SkillDef)
        assert sd.name == "scan_for_anomalies"
        assert sd.version == "1"
        assert sd.requires == ("timestamp_window.v1", "flow_summary.v1")
        assert len(sd.steps) == 2
        assert isinstance(sd.steps[0], ToolStep)
        assert sd.steps[0].id == "window"
        assert sd.skill_hash.startswith("sha256:")

    def test_strips_markdown_fence(self):
        wrapped = "```yaml\n" + _GOOD + "\n```"
        sd = parse_manifest(wrapped)
        assert sd.name == "scan_for_anomalies"

    def test_missing_name_rejected(self):
        bad = _GOOD.replace("name: scan_for_anomalies\n", "")
        with pytest.raises(ManifestError, match="name"):
            parse_manifest(bad)

    def test_invalid_name_rejected(self):
        bad = _GOOD.replace("name: scan_for_anomalies", "name: ScanCamelCase")
        with pytest.raises(ManifestError, match="snake_case"):
            parse_manifest(bad)

    def test_missing_description_rejected(self):
        # Drop the description and its continuation line.
        lines = _GOOD.splitlines()
        cleaned = [
            line for line in lines
            if not line.startswith("description") and
               not line.startswith("  Walk a packet")
        ]
        bad = "\n".join(cleaned)
        with pytest.raises(ManifestError, match="description"):
            parse_manifest(bad)

    def test_unsupported_schema_version_rejected(self):
        bad = _GOOD.replace("schema_version: 1", "schema_version: 99")
        with pytest.raises(ManifestError, match="schema_version"):
            parse_manifest(bad)

    def test_empty_steps_rejected(self):
        bad = _GOOD.replace("steps:\n  - id: window", "steps: []\n  - id: window")
        # Just blank the steps block.
        bad = textwrap.dedent("""
        schema_version: 1
        name: empty
        description: empty
        requires: []
        steps: []
        """).strip()
        with pytest.raises(ManifestError, match="non-empty list"):
            parse_manifest(bad)

    def test_invalid_tool_ref_rejected(self):
        bad = _GOOD.replace("- timestamp_window.v1", "- not-a-valid-ref")
        with pytest.raises(ManifestError, match="tool refs"):
            parse_manifest(bad)

    def test_duplicate_step_id_rejected(self):
        bad = _GOOD.replace("id: flows", "id: window")
        with pytest.raises(ManifestError, match="duplicate"):
            parse_manifest(bad)


class TestStepReferences:
    def test_arg_references_unknown_step_rejected(self):
        bad = textwrap.dedent("""
        schema_version: 1
        name: bad
        description: bad ref
        requires: [t.v1]
        steps:
          - id: a
            tool: t.v1
            args:
              x: ${b.foo}
        """).strip()
        with pytest.raises(ManifestError, match="undefined name"):
            parse_manifest(bad)

    def test_arg_can_reference_inputs(self):
        good = textwrap.dedent("""
        schema_version: 1
        name: good
        description: good
        requires: [t.v1]
        inputs:
          type: object
          required: [k]
          properties:
            k: {type: string}
        steps:
          - id: a
            tool: t.v1
            args:
              x: ${inputs.k}
        """).strip()
        sd = parse_manifest(good)
        assert sd.steps[0].args["x"].is_pure_expression

    def test_arg_can_reference_earlier_step(self):
        good = textwrap.dedent("""
        schema_version: 1
        name: good
        description: good
        requires: [t.v1]
        steps:
          - id: a
            tool: t.v1
            args: {}
          - id: b
            tool: t.v1
            args:
              x: ${a.result}
        """).strip()
        sd = parse_manifest(good)
        assert sd.steps[1].args["x"].references() == {"a"}

    def test_when_predicate_validated(self):
        good = textwrap.dedent("""
        schema_version: 1
        name: good
        description: cond
        requires: [t.v1]
        steps:
          - id: a
            tool: t.v1
            args: {}
          - id: b
            tool: t.v1
            when: ${a.matched}
            args: {}
        """).strip()
        sd = parse_manifest(good)
        assert sd.steps[1].when is not None

    def test_when_with_unknown_ref_rejected(self):
        bad = textwrap.dedent("""
        schema_version: 1
        name: bad
        description: cond
        requires: [t.v1]
        steps:
          - id: b
            tool: t.v1
            when: ${nonexistent.flag}
            args: {}
        """).strip()
        with pytest.raises(ManifestError, match="undefined"):
            parse_manifest(bad)


class TestForEach:
    def test_for_each_binds_each_in_inner_steps(self):
        good = textwrap.dedent("""
        schema_version: 1
        name: loop
        description: per-item lookup
        requires: [t.v1, lookup.v1]
        inputs: {type: object}
        steps:
          - id: items
            tool: t.v1
            args: {}
          - id: per
            for_each: ${items.results}
            steps:
              - id: lookup
                tool: lookup.v1
                args:
                  ip: ${each.source_ip}
        """).strip()
        sd = parse_manifest(good)
        assert isinstance(sd.steps[1], ForEachStep)
        inner = sd.steps[1].steps[0]
        assert isinstance(inner, ToolStep)
        assert inner.args["ip"].references() == {"each"}

    def test_for_each_each_not_visible_outside(self):
        bad = textwrap.dedent("""
        schema_version: 1
        name: leak
        description: each leak
        requires: [t.v1]
        inputs: {type: object}
        steps:
          - id: items
            tool: t.v1
            args: {}
          - id: outside
            tool: t.v1
            args:
              x: ${each.foo}
        """).strip()
        with pytest.raises(ManifestError, match="each"):
            parse_manifest(bad)

    def test_for_each_requires_inner_steps(self):
        bad = textwrap.dedent("""
        schema_version: 1
        name: empty
        description: e
        requires: [t.v1]
        steps:
          - id: items
            tool: t.v1
            args: {}
          - id: per
            for_each: ${items.results}
            steps: []
        """).strip()
        with pytest.raises(ManifestError, match="non-empty"):
            parse_manifest(bad)


class TestSkillHash:
    def test_hash_stable_across_irrelevant_metadata(self):
        a = parse_manifest(_GOOD)
        # Add forge metadata — should NOT change skill_hash.
        with_meta = _GOOD + "\nforged_at: '2026-04-27T00:00:00Z'\nforged_by: alex"
        b = parse_manifest(with_meta)
        assert a.skill_hash == b.skill_hash
        assert b.forged_by == "alex"
        assert a.forged_by is None

    def test_hash_changes_on_logic_change(self):
        a = parse_manifest(_GOOD)
        modified = _GOOD.replace("'last 1 hours'", "'last 6 hours'")
        b = parse_manifest(modified)
        assert a.skill_hash != b.skill_hash
