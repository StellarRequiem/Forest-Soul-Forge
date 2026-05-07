"""Tool dispatch (ADR-0019 T2 — POST /agents/{id}/tools/call) + approval queue.

Split out of the original 1139-line monolithic ``schemas.py`` by R1
of the post-marathon roadmap. Re-exported via ``schemas/__init__.py``
so existing ``from forest_soul_forge.daemon.schemas import X`` imports
keep working — this is purely an organizational refactor.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from forest_soul_forge.daemon.providers import ProviderStatus, TaskKind


# ADR-0056 E2 — Experimenter mode tagging. Selects which subset of
# the agent's constitutional kit is dispatchable for a given call.
# 'none' = no clamp (every other agent's default; non-experimenter
# agents ignore the field entirely). The experimenter agent uses
# 'explore' / 'work' / 'display' to swap kits for read-only
# discovery, full-kit implementation cycles, and review-only
# approval surfaces respectively. ModeKitClampStep enforces.
ExperimenterMode = Literal["none", "explore", "work", "display"]


# T2.2b — per-task caps the operator decides at execution time. These
# layer on top of the constitution's provider_posture_overrides (T2.2a)
# which set per-model defaults. task_caps are ad-hoc per request:
# operator says "for THIS task, no more than N tokens / context limited
# to M tokens." Logged to audit on every dispatch that has them set.
class TaskCaps(BaseModel):
    """Operator-supplied caps for a single tool call or skill run.

    Both fields are optional; a request with no task_caps proceeds with
    no per-task limit (constitution + genre floor still apply).

    ``context_cap_tokens`` — soft cap on the input context the tool
    can assemble for an LLM call. Tool-side enforcement: tools that
    wrap an LLM check this and refuse if their assembled prompt would
    exceed it. Pure-function tools ignore the field.

    ``usage_cap_tokens`` — hard cap on cumulative tokens consumed for
    this task across all dispatches in the same session. The dispatcher
    sums tokens_used from prior dispatches in the session and refuses
    when the next call would exceed the cap. Refusal lands in the chain
    as a tool_call_refused event with reason='task_usage_cap_exceeded'.
    """
    context_cap_tokens: int | None = Field(
        default=None, ge=1, le=2_000_000,
        description="Soft cap on input context tokens; tool-side enforcement.",
    )
    usage_cap_tokens: int | None = Field(
        default=None, ge=1, le=10_000_000,
        description="Hard cap on cumulative tokens for this task; dispatcher-enforced.",
    )
    # ADR-0056 E2 — Experimenter mode selector. 'none' (the
    # default) is a no-op for all agents; the experimenter agent's
    # ModeKitClampStep also passes through. Other values clamp the
    # eligible tools per ADR-0056 D2:
    #   explore — read-only kit (discovery, no mutations)
    #   work    — full kit (implementation cycles)
    #   display — review-only allowlist (cycle inspection)
    # Non-experimenter agents inherit no behavior change — the
    # clamp step is no-op for any agent whose role isn't the
    # configured experimenter_role (default 'experimenter').
    mode: ExperimenterMode = Field(
        default="none",
        description=(
            "ADR-0056 E2 mode selector. Affects only the experimenter "
            "agent's dispatches; other agents see this as a no-op."
        ),
    )

class ToolCallRequest(BaseModel):
    """Request body for ``POST /agents/{instance_id}/tools/call``.

    ``session_id`` is operator-supplied. Two reasons:
    * The runtime per-session counter keys on it.
    * Operators batching multiple calls under one logical session want
      stable counter semantics — a single UUID per session, not a
      per-request UUID.

    ``args`` is the tool's input. Validation is the tool's job; the
    daemon refuses to inspect it beyond JSON-decoding.

    ``task_caps`` (T2.2b) are operator-supplied per-task limits. None
    means no per-task cap (constitution + genre floor still apply).
    """

    tool_name: str = Field(..., min_length=1, max_length=80)
    tool_version: str = Field(..., min_length=1, max_length=16)
    session_id: str = Field(..., min_length=1, max_length=80)
    args: dict[str, Any] = Field(default_factory=dict)
    task_caps: TaskCaps | None = None

class ToolCallResultOut(BaseModel):
    """The agent-facing result of a successful dispatch."""

    output: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    tokens_used: int | None = None
    cost_usd: float | None = None
    side_effect_summary: str | None = None
    result_digest: str = Field(
        ...,
        description=(
            "SHA-256 over canonical (output, metadata). Mirrors the "
            "audit chain entry's result_digest so callers can verify "
            "result integrity without re-fetching the chain."
        ),
    )

class PendingApprovalOut(BaseModel):
    """One row from ``GET /agents/{id}/pending_calls``.

    ``args`` is parsed back from the registry's canonical JSON so the
    frontend gets a JSON object rather than a quoted string. ``status``
    is exposed even though the default endpoint filters to pending —
    the full-history variant uses the same shape.
    """

    ticket_id: str
    instance_id: str
    session_id: str
    tool_key: str
    args: dict[str, Any] = Field(default_factory=dict)
    side_effects: str
    status: str
    pending_audit_seq: int
    decided_audit_seq: int | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    created_at: str
    decided_at: str | None = None

class PendingApprovalListOut(BaseModel):
    count: int
    pending_calls: list[PendingApprovalOut]

class ApproveRequest(BaseModel):
    """Body for ``POST /pending_calls/{ticket_id}/approve``."""

    operator_id: str = Field(..., min_length=1, max_length=80)

class RejectRequest(BaseModel):
    """Body for ``POST /pending_calls/{ticket_id}/reject``.

    ``reason`` is required so the rejected event in the audit chain
    carries the operator's stated rationale. Empty rejections obscure
    intent in the long run.
    """

    operator_id: str = Field(..., min_length=1, max_length=80)
    reason: str = Field(..., min_length=1, max_length=500)

class ToolCallResponse(BaseModel):
    """Response shape for ``POST /agents/{id}/tools/call``.

    Exactly one of ``result`` / ``ticket_id`` / ``failure`` is set,
    discriminated by ``status``:
    * ``succeeded`` — ``result`` populated, HTTP 200.
    * ``pending_approval`` — ``ticket_id`` set, HTTP 202.
    * ``failed`` — tool started but raised; ``failure`` set, HTTP 200
      (the API call succeeded — the tool didn't). Distinguishes from
      a ``refused`` outcome which uses HTTP 4xx.

    Refusals don't reach this schema; they're returned as HTTP 400/403/404
    via FastAPI's HTTPException machinery so clients get the standard
    error shape.
    """

    status: str = Field(
        ..., description="One of: succeeded, pending_approval, failed."
    )
    tool_key: str = Field(
        ..., description="The dispatched tool's name.vversion."
    )
    audit_seq: int = Field(
        ..., description="Audit-chain seq of the terminating event."
    )
    call_count_after: int | None = Field(
        default=None,
        description=(
            "Per-session call count after this dispatch (succeeded only). "
            "None for pending_approval and failed since the counter is "
            "incremented BEFORE execute and the failure path still "
            "returns it via the audit chain."
        ),
    )
    result: ToolCallResultOut | None = None
    ticket_id: str | None = None
    failure_exception_type: str | None = None
