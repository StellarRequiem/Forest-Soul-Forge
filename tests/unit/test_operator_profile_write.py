"""ADR-0068 T2 (B312) — operator_profile_write.v1 tests.

Mutating sibling to operator_profile_read. Tests cover:
  - validate refuses missing/malformed args
  - happy path: writes the field, file updates, audit event fires
  - no-op when new_value matches current
  - nested work_hours.start/end paths
  - audit event payload shape (before/after/reason/operator_id)
  - Reality Anchor seeds re-computed + surfaced in metadata
"""
from __future__ import annotations

import pytest
import yaml

from forest_soul_forge.core.operator_profile import (
    OperatorProfile,
    WorkHours,
    load_operator_profile,
    save_operator_profile,
)
from forest_soul_forge.tools.base import ToolValidationError
from forest_soul_forge.tools.builtin.operator_profile_write import (
    OperatorProfileWriteTool,
    operator_profile_write_tool,
)
import forest_soul_forge.tools.builtin.operator_profile_write as op_write_mod


class _MockChain:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type, payload, *, agent_dna=None):
        self.events.append((event_type, dict(payload)))


class _FakeCtx:
    """Minimal ToolContext shape — operator_profile_write reads
    only audit_chain + master_key + constraints from ctx."""

    instance_id = "a1"
    agent_dna = "dna"
    role = "operator_steward"
    genre = "core"
    session_id = "s1"
    constraints: dict = {}
    master_key = None

    def __init__(self, chain=None):
        self.audit_chain = chain


@pytest.fixture
def seed_profile(tmp_path, monkeypatch):
    """Write a baseline profile to a tmp path + monkeypatch the
    write tool's default_operator_profile_path so the test runs
    don't touch the real data/operator/profile.yaml."""
    prof_path = tmp_path / "operator" / "profile.yaml"
    prof_path.parent.mkdir(parents=True)
    profile = OperatorProfile(
        schema_version=1,
        operator_id="op_1",
        name="Alex Price",
        preferred_name="Alex",
        email="alex@example.com",
        timezone="America/Los_Angeles",
        locale="en-US",
        work_hours=WorkHours(start="09:00", end="17:00"),
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:00+00:00",
    )
    save_operator_profile(profile, prof_path)
    monkeypatch.setattr(
        op_write_mod, "default_operator_profile_path",
        lambda data_dir=None: prof_path,
    )
    return prof_path


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_refuses_missing_field_path():
    tool = OperatorProfileWriteTool()
    with pytest.raises(ToolValidationError, match="field_path"):
        tool.validate({})


def test_validate_refuses_missing_new_value():
    tool = OperatorProfileWriteTool()
    with pytest.raises(ToolValidationError, match="new_value"):
        tool.validate({"field_path": "name"})


def test_validate_refuses_missing_reason():
    tool = OperatorProfileWriteTool()
    with pytest.raises(ToolValidationError, match="reason"):
        tool.validate({"field_path": "name", "new_value": "X"})


def test_validate_refuses_unsupported_field_path():
    tool = OperatorProfileWriteTool()
    with pytest.raises(
        ToolValidationError, match="unsupported field_path",
    ):
        tool.validate({
            "field_path": "extra.foo",
            "new_value": "y",
            "reason": "z",
        })


def test_validate_refuses_bad_work_hours_format():
    tool = OperatorProfileWriteTool()
    with pytest.raises(ToolValidationError, match="HH:MM"):
        tool.validate({
            "field_path": "work_hours.start",
            "new_value": "noon",
            "reason": "z",
        })


def test_validate_accepts_supported_fields():
    """All seven supported paths validate cleanly with well-formed
    args."""
    tool = OperatorProfileWriteTool()
    for fp in [
        "name", "preferred_name", "email",
        "timezone", "locale",
        "work_hours.start", "work_hours.end",
    ]:
        v = "10:00" if fp.startswith("work_hours") else "value"
        tool.validate({
            "field_path": fp, "new_value": v, "reason": "test",
        })


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_write_updates_top_level_field(seed_profile):
    chain = _MockChain()
    ctx = _FakeCtx(chain=chain)
    result = operator_profile_write_tool.call(ctx, {
        "field_path": "preferred_name",
        "new_value":  "Lex",
        "reason":     "shorter form",
    })
    assert result.output["before"] == "Alex"
    assert result.output["after"] == "Lex"
    assert result.output["no_op"] is False

    reloaded = load_operator_profile(seed_profile)
    assert reloaded.preferred_name == "Lex"


def test_write_updates_nested_work_hours_start(seed_profile):
    """Nested write only touches the targeted child field — work_hours.end
    must stay intact."""
    ctx = _FakeCtx(chain=_MockChain())
    operator_profile_write_tool.call(ctx, {
        "field_path": "work_hours.start",
        "new_value":  "10:00",
        "reason":     "shifting morning",
    })
    reloaded = load_operator_profile(seed_profile)
    assert reloaded.work_hours.start == "10:00"
    assert reloaded.work_hours.end == "17:00"


def test_write_emits_operator_profile_changed_event(seed_profile):
    chain = _MockChain()
    ctx = _FakeCtx(chain=chain)
    operator_profile_write_tool.call(ctx, {
        "field_path": "email",
        "new_value":  "alex.new@example.com",
        "reason":     "new address",
    })
    assert len(chain.events) == 1
    event_type, payload = chain.events[0]
    assert event_type == "operator_profile_changed"
    assert payload["field_path"] == "email"
    assert payload["before"] == "alex@example.com"
    assert payload["after"] == "alex.new@example.com"
    assert payload["reason"] == "new address"
    assert payload["operator_id"] == "op_1"


def test_write_surfaces_reality_anchor_seeds_in_metadata(seed_profile):
    """The tool re-runs profile_to_ground_truth_seeds after a successful
    write and exposes the result for the operator's reload step."""
    ctx = _FakeCtx(chain=_MockChain())
    result = operator_profile_write_tool.call(ctx, {
        "field_path": "name",
        "new_value":  "Alexander Price",
        "reason":     "full name",
    })
    seeds = result.metadata.get("reality_anchor_seeds")
    assert seeds is not None
    assert len(seeds) > 0
    # The seeds reflect the updated profile.
    name_seed = next(
        (s for s in seeds if "Alexander Price" in str(s)), None,
    )
    assert name_seed is not None


# ---------------------------------------------------------------------------
# No-op semantics
# ---------------------------------------------------------------------------

def test_write_no_op_when_value_unchanged(seed_profile):
    chain = _MockChain()
    ctx = _FakeCtx(chain=chain)
    result = operator_profile_write_tool.call(ctx, {
        "field_path": "preferred_name",
        "new_value":  "Alex",  # already the current value
        "reason":     "double check",
    })
    assert result.output["no_op"] is True
    # No audit emit on no-op.
    assert chain.events == []
    # File untouched (we'd see updated_at change otherwise — but
    # the no-op path skips save_operator_profile entirely).
    raw = yaml.safe_load(seed_profile.read_text())
    assert raw["preferred_name"] == "Alex"


# ---------------------------------------------------------------------------
# Tool surface invariants
# ---------------------------------------------------------------------------

def test_tool_requires_human_approval():
    """ADR-0068 T2 spec: operator-truth mutations must gate per-call."""
    tool = OperatorProfileWriteTool()
    assert tool.requires_human_approval is True


def test_tool_side_effects_filesystem():
    """The tool writes data/operator/profile.yaml — filesystem tier."""
    tool = OperatorProfileWriteTool()
    assert tool.side_effects == "filesystem"


def test_tool_not_sandbox_eligible():
    """Filesystem writes can't run in the ADR-0051 sandbox."""
    tool = OperatorProfileWriteTool()
    assert tool.sandbox_eligible is False
