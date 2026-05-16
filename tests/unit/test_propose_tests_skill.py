"""ADR-0077 T4a (B337) — propose_tests.v1 skill manifest tests.

Verifies the canonical examples/skills/propose_tests.v1.yaml parses
through the manifest loader cleanly + has the structure ADR-0077 §1
calls for. Static-only (no daemon needed).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.forge.skill_manifest import parse_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / "examples" / "skills" / "propose_tests.v1.yaml"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skill_dict(skill_text) -> dict:
    return yaml.safe_load(skill_text)


@pytest.fixture(scope="module")
def parsed_skill(skill_text):
    """Run the production manifest parser against the canonical
    YAML. Catches any expression-validation, step-reference, or
    schema-version drift."""
    return parse_manifest(skill_text)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_skill_loads_without_errors(parsed_skill):
    assert parsed_skill.name == "propose_tests"
    assert parsed_skill.version == "1"
    assert parsed_skill.schema_version == 1


def test_required_tools_declared(skill_dict):
    """The `requires:` block must enumerate every tool the steps
    reference. Manifest parser already validates step→requires
    consistency, but this guard pins the operator-readable list."""
    required = set(skill_dict.get("requires", []))
    expected = {
        "memory_recall.v1",
        "llm_think.v1",
        "code_edit.v1",
        "shell_exec.v1",
    }
    assert expected == required


def test_inputs_have_required_fields(skill_dict):
    inputs = skill_dict["inputs"]
    required = set(inputs.get("required", []))
    # ADR-0077 §propose_tests contract: spec_summary +
    # target_test_path + production_module_path.
    assert {
        "spec_summary",
        "target_test_path",
        "production_module_path",
    }.issubset(required)


# ---------------------------------------------------------------------------
# Step structure
# ---------------------------------------------------------------------------


def test_step_ids_in_expected_order(parsed_skill):
    """The four-step pipeline: recall → draft → write → run-twice
    (collect + confirm_failure). Order matters because each step
    consumes outputs from prior steps via ${id.out.*} references."""
    step_ids = [s.id for s in parsed_skill.steps]
    assert step_ids == [
        "prior_context",
        "draft",
        "write_test",
        "collect_and_run",
        "confirm_failure",
    ]


def test_draft_step_is_llm_think(parsed_skill):
    draft = next(s for s in parsed_skill.steps if s.id == "draft")
    assert draft.tool == "llm_think.v1"


def test_write_test_targets_input_path(skill_dict):
    """code_edit.path MUST come from inputs.target_test_path so
    the operator (or upstream caller) controls where the test
    lands. Hardcoding the path would defeat the constraint."""
    steps = {s["id"]: s for s in skill_dict["steps"]}
    write_args = steps["write_test"]["args"]
    assert write_args["path"] == "${inputs.target_test_path}"
    assert write_args["content"] == "${draft.out.response}"
    assert write_args.get("mode") == "write"


def test_confirm_failure_uses_pytest(skill_dict):
    """The contract: pytest_returncode SHOULD be non-zero. The
    skill drives pytest -x -q against the freshly-written file
    and the caller reads the output to learn what assertions
    need satisfying."""
    steps = {s["id"]: s for s in skill_dict["steps"]}
    confirm = steps["confirm_failure"]
    assert confirm["tool"] == "shell_exec.v1"
    argv = confirm["args"]["argv"]
    assert argv[0] == "pytest"
    assert "${inputs.target_test_path}" in argv


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_output_surfaces_pytest_return_code(skill_dict):
    """The caller needs the return code to confirm tests failed
    for the right reason, plus the stdout to read what failed."""
    out = skill_dict["output"]
    assert "pytest_returncode" in out
    assert "pytest_stdout" in out
    assert "written_path" in out
    assert "production_module" in out
