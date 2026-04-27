"""Approval queue endpoints — ADR-0019 T3.

Four routes:

- ``GET /agents/{instance_id}/pending_calls`` — list pending tickets
  for one agent. Default filter is ``status=pending``; query
  ``?status=all`` to include decided tickets too.
- ``GET /pending_calls/{ticket_id}`` — full detail on one ticket,
  including parsed args. 404 when unknown.
- ``POST /pending_calls/{ticket_id}/approve`` — operator approves;
  the dispatcher resumes the gated tool. Body: ``{operator_id}``.
- ``POST /pending_calls/{ticket_id}/reject`` — operator rejects;
  emits ``tool_call_rejected`` and marks the row decided. Body:
  ``{operator_id, reason}``.

Approve and reject are mutating endpoints — they take the daemon's
write lock for the same reasons /tools/call does (counter,
audit-chain head, registry row mutation must be atomic).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_tool_dispatcher,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    ApproveRequest,
    PendingApprovalListOut,
    PendingApprovalOut,
    RejectRequest,
    ToolCallResponse,
    ToolCallResultOut,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError
from forest_soul_forge.tools.dispatcher import (
    DispatchFailed,
    DispatchRefused,
    DispatchSucceeded,
    ToolDispatcher,
)


router = APIRouter(tags=["pending_calls"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_out(row: dict[str, Any]) -> PendingApprovalOut:
    """Convert a registry row to the response model.

    args_json is parsed back into a dict — clients shouldn't have to
    re-decode a quoted JSON string.
    """
    args_json = row.get("args_json") or "{}"
    try:
        args = json.loads(args_json)
        if not isinstance(args, dict):
            args = {}
    except (TypeError, ValueError):
        args = {}
    return PendingApprovalOut(
        ticket_id=row["ticket_id"],
        instance_id=row["instance_id"],
        session_id=row["session_id"],
        tool_key=row["tool_key"],
        args=args,
        side_effects=row["side_effects"],
        status=row["status"],
        pending_audit_seq=int(row["pending_audit_seq"]),
        decided_audit_seq=(
            int(row["decided_audit_seq"])
            if row.get("decided_audit_seq") is not None else None
        ),
        decided_by=row.get("decided_by"),
        decision_reason=row.get("decision_reason"),
        created_at=row["created_at"],
        decided_at=row.get("decided_at"),
    )


@router.get(
    "/agents/{instance_id}/pending_calls",
    response_model=PendingApprovalListOut,
)
async def list_pending_calls(
    instance_id: str,
    status_filter: str = Query(
        "pending",
        alias="status",
        description="Filter — 'pending' (default) or 'all'.",
    ),
    registry: Registry = Depends(get_registry),
) -> PendingApprovalListOut:
    """List queued approvals for an agent.

    Read-only — no write lock needed. 404 when the agent itself
    doesn't exist (distinguishes 'agent has zero pending' from
    'agent doesn't exist').
    """
    try:
        registry.get_agent(instance_id)
    except UnknownAgentError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown agent {instance_id!r}",
        )
    filter_ = None if status_filter == "all" else "pending"
    rows = registry.list_pending_approvals(instance_id, status=filter_)
    out = [_row_to_out(r) for r in rows]
    return PendingApprovalListOut(count=len(out), pending_calls=out)


@router.get(
    "/pending_calls/{ticket_id}",
    response_model=PendingApprovalOut,
)
async def get_pending_call(
    ticket_id: str,
    registry: Registry = Depends(get_registry),
) -> PendingApprovalOut:
    row = registry.get_pending_approval(ticket_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown ticket {ticket_id!r}",
        )
    return _row_to_out(row)


@router.post(
    "/pending_calls/{ticket_id}/approve",
    response_model=ToolCallResponse,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def approve_pending_call(
    ticket_id: str,
    payload: ApproveRequest,
    request: Request,
    registry: Registry = Depends(get_registry),
    dispatcher: ToolDispatcher = Depends(get_tool_dispatcher),
    write_lock: Lock = Depends(get_write_lock),
) -> ToolCallResponse:
    """Approve a queued tool call and resume execution.

    Sequence inside the lock:
      1. Re-read the row to confirm still pending (no double-approve).
      2. Look up the agent so the dispatcher has dna/role.
      3. Emit ``tool_call_approved`` audit event.
      4. Mark the registry row decided=approved.
      5. Resume the dispatch (counter increments, tool runs, succeeded
         or failed entry written).
      6. Return the dispatch outcome to the caller.

    Any failure in 1-2 returns 4xx. Failures in 3-5 propagate as 5xx
    or as DispatchRefused — the resume path can refuse for max_calls
    even after operator approval (the budget rules don't bend).
    """
    with write_lock:
        row = registry.get_pending_approval(ticket_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown ticket {ticket_id!r}",
            )
        if row["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"ticket {ticket_id!r} already {row['status']} "
                    f"(decided_by={row.get('decided_by')!r} at "
                    f"{row.get('decided_at')!r})"
                ),
            )

        try:
            agent = registry.get_agent(row["instance_id"])
        except UnknownAgentError:
            # Agent was archived between queue + approve. The ticket is
            # orphaned; mark it as such and let the operator know.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"agent {row['instance_id']!r} no longer exists; "
                    f"ticket {ticket_id!r} cannot be resumed"
                ),
            )

        # Parse args from canonical JSON storage.
        try:
            args = json.loads(row["args_json"]) if row["args_json"] else {}
            if not isinstance(args, dict):
                args = {}
        except (TypeError, ValueError):
            args = {}

        # Tool key parses to (name, version).
        tool_name, _, tool_version = row["tool_key"].rpartition(".v")

        # 3. Emit approved event before resuming so the chain order is
        # pending → approved → dispatched → succeeded/failed.
        approved_seq = dispatcher.emit_approved_event(
            ticket_id=ticket_id,
            instance_id=row["instance_id"],
            agent_dna=agent.dna,
            session_id=row["session_id"],
            tool_key=row["tool_key"],
            operator_id=payload.operator_id,
        )

        # 4. Mark decided. False return = race against another approver
        # (shouldn't happen under the write lock but defensive).
        ok = registry.mark_approval_decided(
            ticket_id,
            status="approved",
            decided_audit_seq=approved_seq,
            decided_by=payload.operator_id,
            decision_reason=None,
            decided_at=_now_iso(),
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"ticket {ticket_id!r} could not be marked approved",
            )

        # 5. Resume.
        outcome = await dispatcher.resume_approved(
            ticket_id=ticket_id,
            operator_id=payload.operator_id,
            instance_id=row["instance_id"],
            agent_dna=agent.dna,
            role=agent.role,
            genre=None,  # T6 hook
            session_id=row["session_id"],
            constitution_path=Path(agent.constitution_path),
            tool_name=tool_name,
            tool_version=tool_version,
            args=args,
            provider=_resolve_active_provider(request),
        )

    if isinstance(outcome, DispatchSucceeded):
        return ToolCallResponse(
            status="succeeded",
            tool_key=outcome.tool_key,
            audit_seq=outcome.audit_seq,
            call_count_after=outcome.call_count_after,
            result=ToolCallResultOut(
                output=outcome.result.output,
                metadata=dict(outcome.result.metadata),
                tokens_used=outcome.result.tokens_used,
                cost_usd=outcome.result.cost_usd,
                side_effect_summary=outcome.result.side_effect_summary,
                result_digest=outcome.result.result_digest(),
            ),
        )
    if isinstance(outcome, DispatchFailed):
        return ToolCallResponse(
            status="failed",
            tool_key=outcome.tool_key,
            audit_seq=outcome.audit_seq,
            failure_exception_type=outcome.exception_type,
        )
    if isinstance(outcome, DispatchRefused):
        # Resume-time refusal (tool unregistered, args revalidate fail,
        # max_calls). Surface as 4xx with the standard refusal shape.
        from forest_soul_forge.daemon.routers.tool_dispatch import _REFUSAL_STATUS
        http_status = _REFUSAL_STATUS.get(outcome.reason, status.HTTP_400_BAD_REQUEST)
        raise HTTPException(
            status_code=http_status,
            detail={
                "reason": outcome.reason,
                "detail": outcome.detail,
                "tool_key": outcome.tool_key,
                "audit_seq": outcome.audit_seq,
            },
        )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"unexpected resume outcome: {type(outcome).__name__}",
    )


@router.post(
    "/pending_calls/{ticket_id}/reject",
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def reject_pending_call(
    ticket_id: str,
    payload: RejectRequest,
    registry: Registry = Depends(get_registry),
    dispatcher: ToolDispatcher = Depends(get_tool_dispatcher),
    write_lock: Lock = Depends(get_write_lock),
) -> dict[str, Any]:
    """Reject a queued tool call. Tool never runs; chain records the
    operator's reasoning."""
    with write_lock:
        row = registry.get_pending_approval(ticket_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown ticket {ticket_id!r}",
            )
        if row["status"] != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"ticket {ticket_id!r} already {row['status']} "
                    f"(decided_by={row.get('decided_by')!r})"
                ),
            )
        try:
            agent = registry.get_agent(row["instance_id"])
            agent_dna = agent.dna
        except UnknownAgentError:
            # Agent gone — still record the rejection in the chain,
            # using a None dna so the entry is at least self-consistent.
            agent_dna = None  # type: ignore[assignment]
        rejected_seq = dispatcher.emit_rejected_event(
            ticket_id=ticket_id,
            instance_id=row["instance_id"],
            agent_dna=agent_dna or "0" * 12,
            session_id=row["session_id"],
            tool_key=row["tool_key"],
            operator_id=payload.operator_id,
            reason=payload.reason,
        )
        ok = registry.mark_approval_decided(
            ticket_id,
            status="rejected",
            decided_audit_seq=rejected_seq,
            decided_by=payload.operator_id,
            decision_reason=payload.reason,
            decided_at=_now_iso(),
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"ticket {ticket_id!r} could not be marked rejected",
            )
    return {
        "ticket_id": ticket_id,
        "status": "rejected",
        "audit_seq": rejected_seq,
        "decided_by": payload.operator_id,
        "decision_reason": payload.reason,
    }


def _resolve_active_provider(request: Request):
    """Mirror of tool_dispatch._resolve_active_provider so the resume
    path gets the same provider plumbing as a fresh dispatch."""
    pr = getattr(request.app.state, "providers", None)
    if pr is None:
        return None
    try:
        return pr.active()
    except Exception:
        return None
