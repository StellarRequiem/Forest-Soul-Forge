"""B350 — audit_chain_verify.v1 ctx wiring regression tests.

Pre-B350 the audit_chain_verify tool's _resolve_chain only looked
for the chain at ``ctx.constraints['audit_chain']``. The dispatcher
populated neither that key nor ``ctx.audit_chain``, so any HTTP-path
invocation raised ToolValidationError. The only place the tool
actually worked was the unit fixture in test_b1_tools.py which
constructed the constraints dict manually.

This file pins:

1. ToolContext exposes a typed ``audit_chain`` field (default None)
   so the dispatcher can populate it without going through
   constraints.

2. _resolve_chain prefers the typed field over the constraints
   fallback when both are set (so daemon-wired path wins over
   test-fixture path if a test ever sets both).

3. _resolve_chain still honors constraints["audit_chain"] when the
   typed field is None (back-compat with test_b1_tools.py and any
   other fixture that uses the dict form).

4. _resolve_chain still raises ToolValidationError with a clear
   message when neither path is populated.

Discovered live during D3 Phase A verification — the
archive_evidence.v1 skill's verify_chain_integrity step blew up
because the daemon's skill-runtime path never set either key. Fix:
add typed field to ToolContext + populate from dispatcher.
"""
from __future__ import annotations

from dataclasses import fields

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.audit_chain_verify import _resolve_chain


# ---------------------------------------------------------------------------
# Typed field exists
# ---------------------------------------------------------------------------


def test_tool_context_has_audit_chain_field():
    """The dataclass must expose audit_chain as a named field with
    default None — otherwise the dispatcher's typed assignment in
    dispatcher.py fails at class instantiation."""
    field_names = {f.name for f in fields(ToolContext)}
    assert "audit_chain" in field_names


def test_tool_context_audit_chain_defaults_to_none():
    """Default must be None so existing test code that constructs
    ToolContext without the field keeps working."""
    ctx = _minimal_ctx()
    assert ctx.audit_chain is None


# ---------------------------------------------------------------------------
# _resolve_chain resolution order
# ---------------------------------------------------------------------------


def test_resolve_chain_prefers_typed_field():
    """When both ctx.audit_chain AND constraints['audit_chain'] are
    set, typed field wins. Daemon-wired path is authoritative."""
    typed_chain = object()
    constraints_chain = object()
    ctx = _minimal_ctx(
        audit_chain=typed_chain,
        constraints={"audit_chain": constraints_chain},
    )
    resolved = _resolve_chain(ctx)
    assert resolved is typed_chain


def test_resolve_chain_falls_back_to_constraints():
    """When typed field is None, the constraints dict still works.
    Keeps the pre-B350 test fixture (test_b1_tools.py) green
    without touching it."""
    fallback_chain = object()
    ctx = _minimal_ctx(constraints={"audit_chain": fallback_chain})
    resolved = _resolve_chain(ctx)
    assert resolved is fallback_chain


def test_resolve_chain_returns_typed_field_when_only_one_set():
    """The common B350+ path: dispatcher sets the typed field;
    constraints stays empty. Typed field is returned."""
    typed_chain = object()
    ctx = _minimal_ctx(audit_chain=typed_chain)
    resolved = _resolve_chain(ctx)
    assert resolved is typed_chain


def test_resolve_chain_raises_when_neither_set():
    """Defensive: tool should refuse cleanly rather than crash on
    AttributeError when both wiring paths are empty. Error message
    references B350 so future debugging starts at the right spot."""
    ctx = _minimal_ctx()
    with pytest.raises(ToolValidationError) as exc:
        _resolve_chain(ctx)
    msg = str(exc.value)
    assert "audit_chain_verify.v1" in msg
    assert "B350" in msg


def test_resolve_chain_handles_constraints_none():
    """Defensive: some test contexts pass constraints=None instead
    of an empty dict. The .get() call would TypeError without the
    `(ctx.constraints or {})` guard."""
    ctx = _minimal_ctx(constraints=None)
    with pytest.raises(ToolValidationError):
        _resolve_chain(ctx)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_ctx(**overrides) -> ToolContext:
    """Build a ToolContext with the required positional/named args
    populated to defaults; tests override only what they need."""
    defaults = dict(
        instance_id="test_agent",
        agent_dna="dna_test",
        role="test_role",
        genre="guardian",
        session_id="sess_test",
        constraints={},
    )
    # ToolContext is a frozen dataclass; constraints=None must be
    # explicitly allowed because that's the case test_resolve_chain_
    # handles_constraints_none exercises.
    if "constraints" in overrides and overrides["constraints"] is None:
        defaults.pop("constraints")
    defaults.update(overrides)
    return ToolContext(**defaults)
