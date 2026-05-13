"""Tests for ADR-0063 T2 — verify_claim.v1 builtin tool.

Coverage:
- Argument validation
- Empty claim → not_in_scope
- Claim outside any fact's domain → not_in_scope
- Claim with canonical term → confirmed
- Claim with forbidden term and no canonical → contradicted
- Claim in domain with neither → unknown
- Multiple facts in scope: contradicted wins, then confirmed,
  then unknown, then not_in_scope
- highest_severity reports the worst contradicting fact's severity
- fact_ids filter restricts evaluation
- Per-agent constitution additions layered + verified
- Real catalog smoke: license / schema_version / audit_chain_path
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.verify_claim import (
    VerifyClaimTool,
    VERDICT_CONFIRMED,
    VERDICT_CONTRADICTED,
    VERDICT_UNKNOWN,
    VERDICT_NOT_IN_SCOPE,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CATALOG = REPO_ROOT / "config" / "ground_truth.yaml"


def _ctx():
    return ToolContext(
        instance_id="t", agent_dna="a" * 12,
        role="verifier", genre="verifier",
        session_id=None,
    )


def _run(args):
    return asyncio.run(VerifyClaimTool().execute(args, _ctx()))


def _write_catalog(path: Path, facts: list[dict]) -> None:
    path.write_text(
        yaml.safe_dump({"catalog_version": 1, "facts": facts}),
        encoding="utf-8",
    )


class TestValidation:
    def test_claim_required(self):
        with pytest.raises(ToolValidationError, match="claim"):
            VerifyClaimTool().validate({})

    def test_claim_must_be_string(self):
        with pytest.raises(ToolValidationError, match="claim"):
            VerifyClaimTool().validate({"claim": 42})

    def test_fact_ids_must_be_list_of_strings(self):
        with pytest.raises(ToolValidationError, match="fact_ids"):
            VerifyClaimTool().validate({"claim": "x", "fact_ids": "single"})
        with pytest.raises(ToolValidationError, match="fact_ids"):
            VerifyClaimTool().validate({"claim": "x", "fact_ids": ["ok", ""]})

    def test_agent_constitution_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="agent_constitution"):
            VerifyClaimTool().validate({
                "claim": "x", "agent_constitution": "string-not-dict",
            })


class TestVerdicts:
    @pytest.fixture
    def fixture_catalog(self, tmp_path):
        cat = tmp_path / "facts.yaml"
        _write_catalog(cat, [
            {"id": "license",
             "statement": "License is ELv2.",
             "domain_keywords": ["license", "licensed"],
             "canonical_terms": ["elv2", "elastic license"],
             "forbidden_terms": ["mit", "gpl", "apache 2"],
             "severity": "HIGH"},
            {"id": "db",
             "statement": "DB is Postgres.",
             "domain_keywords": ["database", "db"],
             "canonical_terms": ["postgres", "postgresql"],
             "forbidden_terms": ["mysql", "sqlite", "mongo"],
             "severity": "MEDIUM"},
            {"id": "crit",
             "statement": "DNA is content-addressed.",
             "domain_keywords": ["dna"],
             "canonical_terms": ["content-addressed"],
             "forbidden_terms": ["random", "uuid"],
             "severity": "CRITICAL"},
        ])
        return cat

    def test_empty_claim_is_not_in_scope(self, fixture_catalog):
        result = _run({
            "claim": "",
            "catalog_path": str(fixture_catalog),
        })
        assert result.output["verdict"] == VERDICT_NOT_IN_SCOPE

    def test_claim_outside_all_domains_is_not_in_scope(
        self, fixture_catalog,
    ):
        result = _run({
            "claim": "the weather is nice today",
            "catalog_path": str(fixture_catalog),
        })
        assert result.output["verdict"] == VERDICT_NOT_IN_SCOPE
        assert result.output["by_fact"] == []

    def test_canonical_term_confirms(self, fixture_catalog):
        result = _run({
            "claim": "this repo is licensed under ELv2",
            "catalog_path": str(fixture_catalog),
        })
        assert result.output["verdict"] == VERDICT_CONFIRMED
        license_row = [
            r for r in result.output["by_fact"] if r["fact_id"] == "license"
        ][0]
        assert license_row["verdict"] == VERDICT_CONFIRMED
        assert "elv2" in license_row["matched_terms"]

    def test_forbidden_without_canonical_contradicts(self, fixture_catalog):
        result = _run({
            "claim": "this repo is licensed under MIT",
            "catalog_path": str(fixture_catalog),
        })
        assert result.output["verdict"] == VERDICT_CONTRADICTED
        license_row = [
            r for r in result.output["by_fact"] if r["fact_id"] == "license"
        ][0]
        assert license_row["verdict"] == VERDICT_CONTRADICTED
        assert "mit" in license_row["matched_terms"]
        assert result.output["highest_severity"] == "HIGH"

    def test_in_domain_but_neither_term_is_unknown(self, fixture_catalog):
        # "license" is a domain hit but the claim doesn't say
        # which license — neither canonical nor forbidden.
        result = _run({
            "claim": "we updated the license file last week",
            "catalog_path": str(fixture_catalog),
        })
        # 'license' domain matches but no canonical/forbidden →
        # the fact is 'unknown' for this claim. Aggregate verdict
        # is 'unknown' (no contradictions, no confirms).
        assert result.output["verdict"] == VERDICT_UNKNOWN
        assert result.output["highest_severity"] is None

    def test_critical_contradiction_wins_severity(self, fixture_catalog):
        # Multiple facts contradicted; CRITICAL outranks HIGH.
        result = _run({
            "claim": "DNA is random and the license is MIT",
            "catalog_path": str(fixture_catalog),
        })
        assert result.output["verdict"] == VERDICT_CONTRADICTED
        assert result.output["highest_severity"] == "CRITICAL"

    def test_canonical_present_overrides_forbidden(self, fixture_catalog):
        # Claim contains BOTH "elv2" (canonical) and "apache 2"
        # (forbidden). Canonical present → confirmed.
        result = _run({
            "claim": "License is ELv2; previously Apache 2",
            "catalog_path": str(fixture_catalog),
        })
        assert result.output["verdict"] == VERDICT_CONFIRMED
        license_row = [
            r for r in result.output["by_fact"] if r["fact_id"] == "license"
        ][0]
        assert license_row["verdict"] == VERDICT_CONFIRMED


class TestFactIdFilter:
    def test_filter_restricts_evaluation(self, tmp_path):
        cat = tmp_path / "f.yaml"
        _write_catalog(cat, [
            {"id": "a", "statement": "x",
             "domain_keywords": ["foo"], "canonical_terms": ["bar"],
             "severity": "LOW"},
            {"id": "b", "statement": "y",
             "domain_keywords": ["foo"], "canonical_terms": ["baz"],
             "severity": "HIGH"},
        ])
        result = _run({
            "claim": "the foo is bar",
            "catalog_path": str(cat),
            "fact_ids": ["a"],
        })
        # Only fact 'a' evaluated → confirmed.
        assert result.output["facts_evaluated"] == 1
        assert result.output["verdict"] == VERDICT_CONFIRMED
        assert all(r["fact_id"] == "a" for r in result.output["by_fact"])


class TestAgentAdditions:
    def test_agent_addition_layered_into_verification(self, tmp_path):
        cat = tmp_path / "op.yaml"
        _write_catalog(cat, [
            {"id": "license",
             "statement": "ELv2.",
             "domain_keywords": ["license"],
             "canonical_terms": ["elv2"],
             "forbidden_terms": ["mit"],
             "severity": "HIGH"},
        ])
        result = _run({
            "claim": "the schema version is v99",
            "catalog_path": str(cat),
            "agent_constitution": {
                "ground_truth_additions": [
                    {"id": "agent_schema",
                     "statement": "Agent's schema is v2 only.",
                     "domain_keywords": ["schema version"],
                     "canonical_terms": ["v2"],
                     "forbidden_terms": ["v99", "v100"],
                     "severity": "HIGH"},
                ],
            },
        })
        assert result.output["verdict"] == VERDICT_CONTRADICTED
        ids_in_output = {r["fact_id"] for r in result.output["by_fact"]}
        assert "agent_schema" in ids_in_output

    def test_agent_collision_logged_not_applied(self, tmp_path):
        cat = tmp_path / "op.yaml"
        _write_catalog(cat, [
            {"id": "license",
             "statement": "ELv2.",
             "domain_keywords": ["license"],
             "canonical_terms": ["elv2"],
             "forbidden_terms": ["mit"],
             "severity": "HIGH"},
        ])
        result = _run({
            "claim": "license is MIT",
            "catalog_path": str(cat),
            "agent_constitution": {
                "ground_truth_additions": [
                    {"id": "license",  # collision
                     "statement": "license override attempt",
                     "domain_keywords": ["license"],
                     "canonical_terms": ["mit"],  # would FLIP the verdict
                     "severity": "LOW"},
                ],
            },
        })
        # Collision rejected → operator-global wins → MIT
        # is still forbidden → contradicted.
        assert result.output["verdict"] == VERDICT_CONTRADICTED
        assert any(
            "collides" in e for e in result.output["catalog_errors"]
        )


class TestRealCatalogSmoke:
    """End-to-end checks against the real ground_truth.yaml so
    we know the bootstrap set behaves correctly."""

    def _real_or_skip(self):
        if not REAL_CATALOG.exists():
            pytest.skip("real catalog missing")

    def test_license_confirmed_for_elv2_claim(self):
        self._real_or_skip()
        result = _run({
            "claim": "Forest Soul Forge is now licensed under ELv2",
            "catalog_path": str(REAL_CATALOG),
        })
        assert result.output["verdict"] == VERDICT_CONFIRMED

    def test_license_contradicted_for_mit_claim(self):
        self._real_or_skip()
        result = _run({
            "claim": "Forest is MIT licensed",
            "catalog_path": str(REAL_CATALOG),
        })
        assert result.output["verdict"] == VERDICT_CONTRADICTED

    def test_schema_version_contradicted_for_old_version(self):
        self._real_or_skip()
        result = _run({
            "claim": "the schema version is v17",
            "catalog_path": str(REAL_CATALOG),
        })
        assert result.output["verdict"] == VERDICT_CONTRADICTED

    def test_audit_chain_path_critical_about_data_path(self):
        self._real_or_skip()
        result = _run({
            "claim": "the audit chain path is examples/audit_chain.jsonl",
            "catalog_path": str(REAL_CATALOG),
        })
        assert result.output["verdict"] == VERDICT_CONFIRMED

    def test_unrelated_claim_not_in_scope(self):
        self._real_or_skip()
        result = _run({
            "claim": "I had eggs for breakfast",
            "catalog_path": str(REAL_CATALOG),
        })
        assert result.output["verdict"] == VERDICT_NOT_IN_SCOPE
