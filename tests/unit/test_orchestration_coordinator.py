"""ADR-0067 T6 (B284) — orchestration coordinator tests.

Covers:
  - happy path: 3 sub-intents, all routable, all dispatch cleanly
  - cascade firing: initial route succeeds → cascade fires
  - failed dispatch: route_fn returns status=failed → bucket'd in
    failed_dispatches, NOT unroutable
  - unroutable sub-intent: status=ambiguous from decompose → bucket'd
    in unroutable, never reaches route_fn
  - mixed outcome: 1 dispatched + 1 failed + 1 unroutable
  - coordinator crash safety: route_fn raising doesn't crash the
    coordinator; that route lands in failed_dispatches
  - render_operator_summary produces readable Markdown
  - needs_operator_attention property
"""
from __future__ import annotations

import pytest

from forest_soul_forge.core.domain_registry import (
    Domain,
    DomainRegistry,
    EntryAgent,
)
from forest_soul_forge.core.orchestration_coordinator import (
    DispatchedRouteResult,
    OrchestrationOutcome,
    coordinate_dispatch,
    render_operator_summary,
)
from forest_soul_forge.core.routing_engine import (
    Handoff,
    HandoffsConfig,
    ResolvedRoute,
    SkillRef,
    UnroutableSubIntent,
)


def _registry() -> DomainRegistry:
    return DomainRegistry(domains=(
        Domain(
            domain_id="d_a", name="A", status="live", description="",
            entry_agents=(EntryAgent("role_a", "cap_1"),),
            capabilities=("cap_1",), example_intents=(),
        ),
        Domain(
            domain_id="d_b", name="B", status="live", description="",
            entry_agents=(EntryAgent("role_b", "cap_2"),),
            capabilities=("cap_2",), example_intents=(),
        ),
        Domain(
            domain_id="d_c", name="C", status="live", description="",
            entry_agents=(EntryAgent("role_c", "cap_3"),),
            capabilities=("cap_3",), example_intents=(),
        ),
    ))


def _handoffs(
    cascade_a_to_b: bool = False,
) -> HandoffsConfig:
    cascades = ()
    if cascade_a_to_b:
        cascades = (Handoff(
            source_domain="d_a", source_capability="cap_1",
            target_domain="d_b", target_capability="cap_2",
            reason="test cascade",
        ),)
    return HandoffsConfig(
        default_skill_per_capability={
            ("d_a", "cap_1"): SkillRef("sk_a", "1"),
            ("d_b", "cap_2"): SkillRef("sk_b", "1"),
            ("d_c", "cap_3"): SkillRef("sk_c", "1"),
        },
        cascade_rules=cascades,
    )


def _inventory() -> list[dict]:
    return [
        {"instance_id": "a_1", "role": "role_a", "status": "active"},
        {"instance_id": "b_1", "role": "role_b", "status": "active"},
        {"instance_id": "c_1", "role": "role_c", "status": "active"},
    ]


def _ok_route_fn(route: ResolvedRoute) -> dict:
    return {"status": "succeeded", "output": {"result": f"ok-{route.target_domain}"}}


def _failed_route_fn(route: ResolvedRoute) -> dict:
    return {"status": "failed", "output": {}, "error": f"sim fail on {route.target_domain}"}


def _crashing_route_fn(route: ResolvedRoute) -> dict:
    raise RuntimeError("simulated crash mid-route")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_coordinate_dispatch_happy_path_three_subintents():
    subintents = [
        {"intent": "do a", "domain": "d_a", "capability": "cap_1",
         "confidence": 0.9, "status": "routable"},
        {"intent": "do b", "domain": "d_b", "capability": "cap_2",
         "confidence": 0.85, "status": "routable"},
        {"intent": "do c", "domain": "d_c", "capability": "cap_3",
         "confidence": 0.95, "status": "routable"},
    ]
    outcome = coordinate_dispatch(
        utterance="do a, b, and c",
        subintents=subintents,
        registry=_registry(),
        handoffs=_handoffs(),
        agent_inventory=_inventory(),
        route_fn=_ok_route_fn,
    )
    assert len(outcome.dispatched) == 3
    assert outcome.failed_dispatches == ()
    assert outcome.unroutable == ()
    assert outcome.needs_operator_attention is False


# ---------------------------------------------------------------------------
# Cascade firing
# ---------------------------------------------------------------------------
def test_cascade_fires_after_successful_initial():
    subintents = [
        {"intent": "do a", "domain": "d_a", "capability": "cap_1",
         "confidence": 0.9, "status": "routable"},
    ]
    outcome = coordinate_dispatch(
        utterance="do a",
        subintents=subintents,
        registry=_registry(),
        handoffs=_handoffs(cascade_a_to_b=True),
        agent_inventory=_inventory(),
        route_fn=_ok_route_fn,
    )
    # Initial d_a route + cascade d_b route = 2 dispatched
    assert len(outcome.dispatched) == 2
    cascades = [d for d in outcome.dispatched if d.route.is_cascade]
    assert len(cascades) == 1
    assert cascades[0].route.target_domain == "d_b"


# ---------------------------------------------------------------------------
# Failure bucketing
# ---------------------------------------------------------------------------
def test_failed_dispatch_lands_in_failed_bucket():
    subintents = [
        {"intent": "do a", "domain": "d_a", "capability": "cap_1",
         "confidence": 0.9, "status": "routable"},
    ]
    outcome = coordinate_dispatch(
        utterance="do a",
        subintents=subintents,
        registry=_registry(),
        handoffs=_handoffs(),
        agent_inventory=_inventory(),
        route_fn=_failed_route_fn,
    )
    assert outcome.dispatched == ()
    assert len(outcome.failed_dispatches) == 1
    assert outcome.failed_dispatches[0].delegate_status == "failed"


def test_unroutable_subintent_never_calls_route_fn():
    """status=ambiguous from decompose → resolve_route returns
    UnroutableSubIntent; coordinator never calls route_fn on it."""
    call_count = [0]
    def route_fn(route):
        call_count[0] += 1
        return _ok_route_fn(route)

    subintents = [
        {"intent": "fuzzy", "domain": "d_a", "capability": "cap_1",
         "confidence": 0.3, "status": "ambiguous"},
    ]
    outcome = coordinate_dispatch(
        utterance="fuzzy thing",
        subintents=subintents,
        registry=_registry(),
        handoffs=_handoffs(),
        agent_inventory=_inventory(),
        route_fn=route_fn,
    )
    assert outcome.dispatched == ()
    assert outcome.failed_dispatches == ()
    assert len(outcome.unroutable) == 1
    assert call_count[0] == 0  # route_fn never called


def test_mixed_outcome_bucketing():
    """One routable+succeeds + one routable+fails + one ambiguous =
    three buckets, one each."""
    subintents = [
        {"intent": "do a", "domain": "d_a", "capability": "cap_1",
         "confidence": 0.9, "status": "routable"},
        {"intent": "fuzzy", "domain": "d_b", "capability": "cap_2",
         "confidence": 0.2, "status": "ambiguous"},
        {"intent": "do c but fail", "domain": "d_c", "capability": "cap_3",
         "confidence": 0.9, "status": "routable"},
    ]
    def selective_route_fn(route):
        # Succeed for d_a, fail for d_c
        if route.target_domain == "d_a":
            return _ok_route_fn(route)
        return _failed_route_fn(route)

    outcome = coordinate_dispatch(
        utterance="mixed",
        subintents=subintents,
        registry=_registry(),
        handoffs=_handoffs(),
        agent_inventory=_inventory(),
        route_fn=selective_route_fn,
    )
    assert len(outcome.dispatched) == 1
    assert len(outcome.failed_dispatches) == 1
    assert len(outcome.unroutable) == 1
    assert outcome.needs_operator_attention is True


# ---------------------------------------------------------------------------
# Crash safety
# ---------------------------------------------------------------------------
def test_route_fn_crash_lands_in_failed_dispatches():
    """route_fn raising doesn't crash the coordinator. The crashed
    route lands in failed_dispatches with the error captured."""
    subintents = [
        {"intent": "do a", "domain": "d_a", "capability": "cap_1",
         "confidence": 0.9, "status": "routable"},
    ]
    outcome = coordinate_dispatch(
        utterance="will crash",
        subintents=subintents,
        registry=_registry(),
        handoffs=_handoffs(),
        agent_inventory=_inventory(),
        route_fn=_crashing_route_fn,
    )
    assert outcome.dispatched == ()
    assert len(outcome.failed_dispatches) == 1
    assert "simulated crash" in outcome.failed_dispatches[0].error


# ---------------------------------------------------------------------------
# Decompose fn vs pre-supplied subintents
# ---------------------------------------------------------------------------
def test_decompose_fn_called_when_subintents_none():
    """When subintents=None, decompose_fn must be supplied and gets
    called with the utterance."""
    captured = []
    def decompose_fn(utterance):
        captured.append(utterance)
        return [
            {"intent": "decomp result", "domain": "d_a",
             "capability": "cap_1", "confidence": 0.9,
             "status": "routable"},
        ]
    outcome = coordinate_dispatch(
        utterance="some utterance",
        decompose_fn=decompose_fn,
        registry=_registry(),
        handoffs=_handoffs(),
        agent_inventory=_inventory(),
        route_fn=_ok_route_fn,
    )
    assert captured == ["some utterance"]
    assert len(outcome.dispatched) == 1


def test_no_subintents_no_decompose_raises():
    with pytest.raises(ValueError, match="subintents or decompose_fn"):
        coordinate_dispatch(
            utterance="x",
            registry=_registry(),
            handoffs=_handoffs(),
            agent_inventory=_inventory(),
            route_fn=_ok_route_fn,
        )


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------
def test_render_operator_summary_dispatched_only():
    subintents = [
        {"intent": "do a", "domain": "d_a", "capability": "cap_1",
         "confidence": 0.9, "status": "routable"},
    ]
    outcome = coordinate_dispatch(
        utterance="do a",
        subintents=subintents,
        registry=_registry(),
        handoffs=_handoffs(),
        agent_inventory=_inventory(),
        route_fn=_ok_route_fn,
    )
    summary = render_operator_summary(outcome)
    assert "Dispatched (1)" in summary
    assert "All sub-intents dispatched cleanly" in summary


def test_render_operator_summary_with_unroutable():
    subintents = [
        {"intent": "fuzzy", "domain": "d_a", "capability": "cap_1",
         "confidence": 0.3, "status": "ambiguous"},
    ]
    outcome = coordinate_dispatch(
        utterance="fuzzy",
        subintents=subintents,
        registry=_registry(),
        handoffs=_handoffs(),
        agent_inventory=_inventory(),
        route_fn=_ok_route_fn,
    )
    summary = render_operator_summary(outcome)
    assert "Needs operator attention" in summary
    assert "low_confidence" in summary
