"""Deterministic acceptance checks for training steps (ADR-0096).

Trust must never be built on unverified success (OPERATOR_PROTOCOL: a
theoretical/fictional source carries zero epistemic weight). Training acceptance
is therefore deterministic + auditable ONLY: a step passes iff the dispatch
reached the expected status AND the optional output assertion holds. No LLM
grading — that is gated + down-weighted (ADR-0096 §3) and lives outside this path.
"""
from __future__ import annotations

from typing import Any


def _dig(output: Any, path: str) -> Any:
    """Walk a dotted path into a nested dict output. Returns None if any segment
    is missing or a non-dict is encountered."""
    cur = output
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def check_step(expect: dict[str, Any], status: str, output: Any) -> tuple[bool, str]:
    """Return ``(passed, reason)``.

    ``expect`` keys (all optional; all present ones must hold):
      - ``status`` — required dispatch status (default ``"succeeded"``).
      - ``path``   — dotted path into ``output`` to inspect.
      - ``equals`` — ``output@path`` must ``==`` this (use with ``path``).
      - ``truthy`` — bool: ``output@path`` must be truthy / falsy (use with ``path``).
    """
    want_status = expect.get("status", "succeeded")
    if status != want_status:
        return (False, f"status {status!r} != expected {want_status!r}")
    if "path" in expect:
        val = _dig(output, expect["path"])
        if "equals" in expect and val != expect["equals"]:
            return (False, f"{expect['path']}={val!r} != expected {expect['equals']!r}")
        if "truthy" in expect and bool(val) != bool(expect["truthy"]):
            return (False, f"{expect['path']}={val!r} (truthy={bool(val)}) "
                           f"!= expected truthy={bool(expect['truthy'])}")
    return (True, "ok")
