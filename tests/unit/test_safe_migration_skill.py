"""ADR-0077 T4b (B338) — safe_migration.v1 skill manifest tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.forge.skill_manifest import parse_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / "examples" / "skills" / "safe_migration.v1.yaml"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skill_dict(skill_text) -> dict:
    return yaml.safe_load(skill_text)


@pytest.fixture(scope="module")
def parsed_skill(skill_text):
    return parse_manifest(skill_text)


def test_skill_loads_without_errors(parsed_skill):
    assert parsed_skill.name == "safe_migration"
    assert parsed_skill.version == "1"


def test_required_tools_subset(skill_dict):
    """The kit deliberately omits code_edit (migration_pilot
    drafts SQL via llm_think output, doesn't write Python files
    in this skill) and audit_chain_verify (the analysis pipeline
    doesn't need it; the apply path will)."""
    required = set(skill_dict.get("requires", []))
    assert {"memory_recall.v1", "llm_think.v1", "shell_exec.v1"} == required


def test_skill_uses_scratch_db_not_production(skill_dict):
    """The dry_run step MUST execute against the scratch copy,
    not data/registry.sqlite. Hardcoded check on the argv
    template — if a future edit accidentally points at the real
    DB, this test fails loud."""
    steps = {s["id"]: s for s in skill_dict["steps"]}
    dry_run_argv = " ".join(steps["dry_run"]["args"]["argv"])
    assert "/tmp/registry.dryrun" in dry_run_argv
    assert "data/registry.sqlite" not in dry_run_argv


def test_clone_step_writes_scratch_only(skill_dict):
    """clone_registry copies production → scratch. The cp source
    is the production DB but the destination MUST be /tmp."""
    steps = {s["id"]: s for s in skill_dict["steps"]}
    clone_argv = " ".join(steps["clone_registry"]["args"]["argv"])
    assert "data/registry.sqlite" in clone_argv
    assert "/tmp/registry.dryrun" in clone_argv


def test_pipeline_has_recommend_step(parsed_skill):
    """Every safe_migration run terminates with an llm_think
    `recommend` step that produces the GO / NO-GO / NEEDS-REVIEW
    verdict. Operator parses on those literals."""
    last = parsed_skill.steps[-1]
    assert last.id == "recommend"
    assert last.tool == "llm_think.v1"


def test_no_apply_step(skill_dict):
    """ADR-0077 D2 says the apply step is operator-driven,
    NEVER part of safe_migration.v1 itself. Verify no step
    references the production data/registry.sqlite as a write
    target."""
    for step in skill_dict["steps"]:
        argv_str = " ".join(step.get("args", {}).get("argv", [])) if isinstance(
            step.get("args", {}).get("argv", []), list,
        ) else ""
        # data/registry.sqlite may appear ONLY as the cp source;
        # it must NEVER appear as a redirect target or sqlite3 arg
        # outside the clone_registry step.
        if step["id"] == "clone_registry":
            continue
        assert "data/registry.sqlite" not in argv_str, (
            f"step {step['id']} references production DB; only "
            f"clone_registry may"
        )


def test_output_includes_recommendation_and_scratch_path(skill_dict):
    out = skill_dict["output"]
    assert "recommendation" in out
    assert "scratch_db_path" in out
    assert "fk_and_rollback_analysis" in out
