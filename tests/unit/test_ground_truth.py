"""Tests for ADR-0063 ground-truth loader (core/ground_truth.py).

Coverage:
- Loader: happy path with the real in-repo catalog
- Loader: missing file → empty list + descriptive error
- Loader: malformed YAML → empty + error
- Loader: missing required fields per fact → skipped + error
- Loader: invalid severity → skipped + error
- Loader: duplicate id → second skipped + error
- merge_agent_additions: per-agent ADD layered on top
- merge_agent_additions: collision REJECTED + error surfaced
- Lowercasing applied to domain/canonical/forbidden tuples
"""
from __future__ import annotations

import yaml
from pathlib import Path

import pytest

from forest_soul_forge.core.ground_truth import (
    Fact,
    load_ground_truth,
    merge_agent_additions,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CATALOG = REPO_ROOT / "config" / "ground_truth.yaml"


def _write(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


class TestLoadGroundTruth:
    def test_real_catalog_loads_without_errors(self):
        if not REAL_CATALOG.exists():
            pytest.skip(f"real catalog missing at {REAL_CATALOG}")
        facts, errors = load_ground_truth(REAL_CATALOG)
        assert errors == []
        assert len(facts) >= 10  # bootstrap set has ~14 entries
        # license, schema_version, audit_chain_path are
        # foundational — must be present.
        ids = {f.id for f in facts}
        assert "license" in ids
        assert "schema_version" in ids
        assert "audit_chain_path" in ids

    def test_missing_file_returns_empty_and_descriptive_error(self, tmp_path):
        facts, errors = load_ground_truth(tmp_path / "nope.yaml")
        assert facts == []
        assert any("not found" in e for e in errors)

    def test_malformed_yaml_surfaces_parse_error(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{not: valid: yaml", encoding="utf-8")
        facts, errors = load_ground_truth(bad)
        assert facts == []
        assert any("YAML parse failed" in e for e in errors)

    def test_root_must_be_mapping(self, tmp_path):
        not_mapping = tmp_path / "list.yaml"
        not_mapping.write_text("- just\n- a\n- list\n", encoding="utf-8")
        facts, errors = load_ground_truth(not_mapping)
        assert facts == []
        assert any("YAML mapping" in e for e in errors)

    def test_fact_missing_id_skipped(self, tmp_path):
        cat = tmp_path / "cat.yaml"
        _write(cat, {"facts": [
            {"statement": "x", "domain_keywords": ["a"],
             "canonical_terms": ["b"], "severity": "LOW"},
            {"id": "good", "statement": "x",
             "domain_keywords": ["a"], "canonical_terms": ["b"],
             "severity": "LOW"},
        ]})
        facts, errors = load_ground_truth(cat)
        assert len(facts) == 1
        assert facts[0].id == "good"
        assert any("no/invalid id" in e for e in errors)

    def test_fact_missing_statement_skipped(self, tmp_path):
        cat = tmp_path / "cat.yaml"
        _write(cat, {"facts": [
            {"id": "no_statement", "domain_keywords": ["a"],
             "canonical_terms": ["b"], "severity": "LOW"},
        ]})
        facts, errors = load_ground_truth(cat)
        assert facts == []
        assert any("no statement" in e for e in errors)

    def test_fact_missing_domain_keywords_skipped(self, tmp_path):
        cat = tmp_path / "cat.yaml"
        _write(cat, {"facts": [
            {"id": "x", "statement": "s",
             "domain_keywords": [], "canonical_terms": ["b"],
             "severity": "LOW"},
        ]})
        facts, errors = load_ground_truth(cat)
        assert facts == []
        assert any("no domain_keywords" in e for e in errors)

    def test_fact_missing_canonical_terms_skipped(self, tmp_path):
        cat = tmp_path / "cat.yaml"
        _write(cat, {"facts": [
            {"id": "x", "statement": "s",
             "domain_keywords": ["a"], "canonical_terms": [],
             "severity": "LOW"},
        ]})
        facts, errors = load_ground_truth(cat)
        assert facts == []
        assert any("no canonical_terms" in e for e in errors)

    def test_invalid_severity_skipped(self, tmp_path):
        cat = tmp_path / "cat.yaml"
        _write(cat, {"facts": [
            {"id": "x", "statement": "s",
             "domain_keywords": ["a"], "canonical_terms": ["b"],
             "severity": "RED_ALERT"},
        ]})
        facts, errors = load_ground_truth(cat)
        assert facts == []
        assert any("invalid severity" in e for e in errors)

    def test_duplicate_id_second_skipped(self, tmp_path):
        cat = tmp_path / "cat.yaml"
        _write(cat, {"facts": [
            {"id": "dup", "statement": "first",
             "domain_keywords": ["a"], "canonical_terms": ["b"],
             "severity": "LOW"},
            {"id": "dup", "statement": "second",
             "domain_keywords": ["x"], "canonical_terms": ["y"],
             "severity": "HIGH"},
        ]})
        facts, errors = load_ground_truth(cat)
        assert len(facts) == 1
        assert facts[0].statement == "first"
        assert any("duplicate fact id" in e for e in errors)

    def test_keywords_lowercased_on_load(self, tmp_path):
        cat = tmp_path / "cat.yaml"
        _write(cat, {"facts": [
            {"id": "case", "statement": "s",
             "domain_keywords": ["License", "LICENSED"],
             "canonical_terms": ["ELv2", "Elastic License"],
             "forbidden_terms": ["MIT", "GPL"],
             "severity": "HIGH"},
        ]})
        facts, errors = load_ground_truth(cat)
        assert errors == []
        f = facts[0]
        assert f.domain_keywords == ("license", "licensed")
        assert f.canonical_terms == ("elv2", "elastic license")
        assert f.forbidden_terms == ("mit", "gpl")


class TestMergeAgentAdditions:
    def _operator_only(self, tmp_path: Path) -> list[Fact]:
        cat = tmp_path / "op.yaml"
        _write(cat, {"facts": [
            {"id": "op_fact", "statement": "operator says",
             "domain_keywords": ["op"], "canonical_terms": ["yes"],
             "severity": "LOW"},
        ]})
        facts, errors = load_ground_truth(cat)
        assert errors == []
        return facts

    def test_no_constitution_returns_unchanged(self, tmp_path):
        operator = self._operator_only(tmp_path)
        merged, errors = merge_agent_additions(operator, None)
        assert merged == operator
        assert errors == []

    def test_addition_layered_on_top(self, tmp_path):
        operator = self._operator_only(tmp_path)
        merged, errors = merge_agent_additions(
            operator,
            {"ground_truth_additions": [
                {"id": "agent_fact", "statement": "agent says",
                 "domain_keywords": ["agent"],
                 "canonical_terms": ["v2"], "severity": "MEDIUM"},
            ]},
            agent_instance_id="ag123",
        )
        assert errors == []
        assert len(merged) == len(operator) + 1
        added = [f for f in merged if f.id == "agent_fact"][0]
        assert added.source == "agent:ag123"

    def test_collision_with_operator_rejected(self, tmp_path):
        operator = self._operator_only(tmp_path)
        merged, errors = merge_agent_additions(
            operator,
            {"ground_truth_additions": [
                {"id": "op_fact",  # collides
                 "statement": "agent override attempt",
                 "domain_keywords": ["op"],
                 "canonical_terms": ["no"], "severity": "HIGH"},
            ]},
            agent_instance_id="malicious",
        )
        assert len(merged) == len(operator)
        assert any(
            "collides" in e and "op_fact" in e for e in errors
        )

    def test_invalid_addition_skipped(self, tmp_path):
        operator = self._operator_only(tmp_path)
        merged, errors = merge_agent_additions(
            operator,
            {"ground_truth_additions": [
                {"statement": "no id"},  # missing id
            ]},
            agent_instance_id="ag",
        )
        assert len(merged) == len(operator)
        assert any("no/invalid id" in e for e in errors)

    def test_non_list_additions_logged_and_ignored(self, tmp_path):
        operator = self._operator_only(tmp_path)
        merged, errors = merge_agent_additions(
            operator,
            {"ground_truth_additions": "not a list"},
            agent_instance_id="ag",
        )
        assert merged == operator
        assert any("must be a list" in e for e in errors)
