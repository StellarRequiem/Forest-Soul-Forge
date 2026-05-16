"""ADR-0077 T4c (B339) — release_check.v1 skill manifest tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.forge.skill_manifest import parse_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / "examples" / "skills" / "release_check.v1.yaml"


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
    assert parsed_skill.name == "release_check"
    assert parsed_skill.version == "1"


def test_required_tools(skill_dict):
    """release_gatekeeper kit minus code_edit/web_fetch/etc.
    Strictly read + shell + chain-verify + LLM. Notably NO git,
    NO twine, NO curl — forbid_release_action enforced."""
    required = set(skill_dict.get("requires", []))
    expected = {
        "memory_recall.v1",
        "shell_exec.v1",
        "audit_chain_verify.v1",
        "llm_think.v1",
    }
    assert expected == required


def test_no_release_acting_tools_in_requires(skill_dict):
    """Defense-in-depth: even if a future edit tries to call
    git/twine/curl, the requires block won't list them. The
    manifest parser will reject any step that references a tool
    not in requires."""
    required = set(skill_dict.get("requires", []))
    for forbidden in ("git", "twine", "curl", "code_edit", "web_fetch"):
        assert not any(t.startswith(forbidden) for t in required), (
            f"release_check requires {forbidden!r} — defeats the "
            f"forbid_release_action policy"
        )


def test_pipeline_decision_step_is_last(parsed_skill):
    """The decide step is terminal — every release_check run ends
    with a single PASS / FAIL / INSUFFICIENT-EVIDENCE verdict."""
    last = parsed_skill.steps[-1]
    assert last.id == "decide"
    assert last.tool == "llm_think.v1"


def test_decide_prompt_enumerates_verdict_literals(skill_text):
    """ADR-0077 release_gatekeeper requires the verdict line to
    be EXACTLY one of three literals so operator tooling can
    parse on them. Verify the prompt mentions all three."""
    assert "PASS" in skill_text
    assert "FAIL" in skill_text
    assert "INSUFFICIENT-EVIDENCE" in skill_text


def test_inputs_include_release_tag_and_parent_sha(skill_dict):
    required = set(skill_dict["inputs"]["required"])
    assert "release_tag" in required
    assert "parent_commit_sha" in required


def test_output_carries_decision_and_evidence(skill_dict):
    out = skill_dict["output"]
    assert "decision" in out
    assert "conformance_returncode" in out
    assert "drift_sentinel_returncode" in out
    assert "chain_verify_result" in out
    assert "release_tag" in out
