"""ADR-0078 Phase A T4 (B346) — archive_evidence.v1 skill manifest tests.

Verifies the canonical examples/skills/archive_evidence.v1.yaml
parses through the manifest loader cleanly + has the structure
ADR-0078 Phase A calls for. Static-only (no daemon needed).

The skill is read-only end-to-end. These tests pin that invariant
in code: no code_edit, no shell_exec in `requires`, no step
references either tool. If a future burst tries to "extend" the
skill into mutating the artifact bytes, these assertions fail and
the operator gets a loud signal.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forest_soul_forge.forge.skill_manifest import parse_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_PATH = REPO_ROOT / "examples" / "skills" / "archive_evidence.v1.yaml"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def skill_dict(skill_text) -> dict:
    return yaml.safe_load(skill_text)


@pytest.fixture(scope="module")
def parsed_skill(skill_text):
    """Run the production manifest parser. Catches expression-
    validation, step-reference, schema-version drift."""
    return parse_manifest(skill_text)


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_skill_loads_without_errors(parsed_skill):
    assert parsed_skill.name == "archive_evidence"
    assert parsed_skill.version == "1"
    assert parsed_skill.schema_version == 1


def test_required_tools_declared(skill_dict):
    """The `requires:` block must enumerate every tool the steps
    reference + must NOT include any write-the-filesystem or
    shell-execute tool. Read-only invariant pinned here."""
    required = set(skill_dict.get("requires", []))
    expected = {
        "memory_recall.v1",
        "memory_write.v1",
        "file_integrity.v1",
        "audit_chain_verify.v1",
        "llm_think.v1",
    }
    assert expected == required


def test_no_mutation_tools_in_requires(skill_dict):
    """Hard guard: the chain-of-custody role's defining invariant
    is forbid_artifact_mutation. The skill MUST NOT pull in
    code_edit or shell_exec — those would let the archivist
    silently violate the policy via skill steps.

    If a future burst tries to add a `move artifact to archive
    directory` step that uses shell_exec or code_edit, this test
    catches it before the manifest ships. The right way to do
    that work is a SEPARATE operator-driven path (the operator
    moves bytes; the archivist re-attests after the move)."""
    required = set(skill_dict.get("requires", []))
    forbidden = {
        "code_edit.v1",
        "shell_exec.v1",
        "browser_action.v1",
        "mcp_call.v1",
        "external_messaging.v1",
    }
    intersection = required & forbidden
    assert intersection == set(), (
        f"archive_evidence skill must be read-only end-to-end; "
        f"found mutation tools in requires: {sorted(intersection)}"
    )


def test_inputs_have_required_fields(skill_dict):
    """ADR-0078 Phase A skill contract: artifact_id +
    artifact_path + transition_type + attestor_reason are
    REQUIRED. handoff_to is conditionally required (handoff
    transition); expected_prior_hash is operator-supplied
    defense-in-depth."""
    inputs = skill_dict["inputs"]
    required = set(inputs.get("required", []))
    assert {
        "artifact_id",
        "artifact_path",
        "transition_type",
        "attestor_reason",
    } == required


def test_transition_type_is_enum(skill_dict):
    """transition_type must be a hard enum {acquire, handoff,
    retire}. The evaluate_transition step's decision matrix is
    built around exactly those three values; any other string
    is a manifest bug, not an unknown future transition kind."""
    inputs = skill_dict["inputs"]
    transition_prop = inputs["properties"]["transition_type"]
    assert transition_prop.get("type") == "string"
    assert set(transition_prop.get("enum", [])) == {
        "acquire", "handoff", "retire",
    }


# ---------------------------------------------------------------------------
# Step structure
# ---------------------------------------------------------------------------


def test_step_ids_in_expected_order(parsed_skill):
    """Five-step pipeline:
       prior_context → verify_artifact_integrity →
       verify_chain_integrity → evaluate_transition →
       write_attestation
    Order matters: each step consumes outputs from prior steps
    via ${id.out.*} references, and write_attestation must come
    last (it records the verdict, including HALT verdicts so the
    chain captures WHY transitions were refused)."""
    step_ids = [s.id for s in parsed_skill.steps]
    assert step_ids == [
        "prior_context",
        "verify_artifact_integrity",
        "verify_chain_integrity",
        "evaluate_transition",
        "write_attestation",
    ]


def test_evaluate_transition_is_llm_think(parsed_skill):
    """The verdict reasoning lives in llm_think — the decision
    matrix is too nuanced for a deterministic chain (mismatch
    detection, orphan-transition logic, operator-disagreement
    handling). Pinning the tool here means a future burst can't
    accidentally swap to a pattern-match-only step that misses
    the HALT cases."""
    eval_step = next(
        s for s in parsed_skill.steps if s.id == "evaluate_transition"
    )
    assert eval_step.tool == "llm_think.v1"


def test_verify_chain_integrity_step_exists(parsed_skill):
    """The require_integrity_hash_verification constitutional
    policy demands chain verification BEFORE the attestation
    writes. The dedicated step is the load-bearing one — without
    it, the archivist could attest against a broken chain and
    the attestation would itself be untrustworthy."""
    verify_step = next(
        s for s in parsed_skill.steps if s.id == "verify_chain_integrity"
    )
    assert verify_step.tool == "audit_chain_verify.v1"


def test_write_attestation_uses_memory_write(skill_dict):
    """The custody log entry is a memory_write at scope=private
    (the archivist's own store). The chain-of-custody is queryable
    via memory_recall by artifact_id — that's the tag the operator
    uses to walk the per-artifact chain later."""
    steps = {s["id"]: s for s in skill_dict["steps"]}
    write = steps["write_attestation"]
    assert write["tool"] == "memory_write.v1"
    args = write["args"]
    assert args["scope"] == "private"
    # layer must be episodic (per-event recording, not durable knowledge)
    assert args["layer"] == "episodic"
    tags = args["tags"]
    assert "chain_of_custody" in tags
    # artifact_id tag is the lookup key — must come from inputs
    assert "${inputs.artifact_id}" in tags


def test_write_attestation_records_both_attest_and_halt(skill_dict):
    """Critical: write_attestation runs unconditionally and
    records the verdict_block from evaluate_transition. Both
    ATTEST and HALT verdicts hit the log. This is the
    forbid_silent_archive invariant — a HALT that wasn't recorded
    would be operationally equivalent to forgetting the
    transition happened."""
    steps = {s["id"]: s for s in skill_dict["steps"]}
    write = steps["write_attestation"]
    # The content is the verdict_block from evaluate_transition,
    # not a separate ATTEST-only summary. That's the load-bearing
    # bit: HALT verdicts get recorded too.
    assert write["args"]["content"] == "${evaluate_transition.out.response}"


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_output_surfaces_key_fields(skill_dict):
    """The caller (orchestrator or operator) needs at minimum:
       - artifact_id + transition_type (echo the inputs)
       - current_hash (the verification result)
       - chain_status (was the chain itself ok?)
       - verdict_block (ATTEST or HALT plus details)
       - attestation_entry_id (so the operator can reference
         the specific custody log entry later)"""
    out = skill_dict["output"]
    for field in (
        "artifact_id", "transition_type",
        "current_hash", "chain_status",
        "verdict_block", "attestation_entry_id",
    ):
        assert field in out, (
            f"output missing required field {field!r}"
        )
