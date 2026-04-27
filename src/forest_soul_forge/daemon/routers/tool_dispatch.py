"""``POST /agents/{instance_id}/tools/call`` — fast-path tool dispatch.

ADR-0019 T2. Wraps :class:`ToolDispatcher` with the daemon's standard
write-path discipline: write lock, idempotency cache (keyed on the
client's session_id + args), agent lookup, audit-chain emission.

HTTP status mapping:

* ``200 OK`` — succeeded or failed (the dispatch completed; the tool
  may have raised but the API call worked). Body distinguishes via
  ``status`` field.
* ``202 Accepted`` — pending_approval. Body carries ``ticket_id`` for
  the operator's approval queue (T3 makes this real; T2 returns a stub).
* ``400 Bad Request`` — bad_args refusal.
* ``403 Forbidden`` — max_calls_exceeded refusal.
* ``404 Not Found`` — unknown_tool, unknown_agent, constitution_missing,
  tool_not_in_constitution.
"""
from __future__ import annotations

from pathlib import Path
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forest_soul_forge.daemon.deps import (
    get_registry,
    get_tool_dispatcher,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    ToolCallRequest,
    ToolCallResponse,
    ToolCallResultOut,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError
from forest_soul_forge.tools.dispatcher import (
    DispatchFailed,
    DispatchPendingApproval,
    DispatchRefused,
    DispatchSucceeded,
    ToolDispatcher,
)


router = APIRouter(tags=["tools"])


def _resolve_active_provider(request: Request):
    """Best-effort fetch of the active model provider.

    Returns ``None`` when the provider registry isn't on app.state OR
    no default is set. Tools that need a provider raise a ToolError
    (handled by the dispatcher) when one isn't available; pure-function
    tools ignore the parameter entirely.
    """
    pr = getattr(request.app.state, "providers", None)
    if pr is None:
        return None
    try:
        return pr.active()
    except Exception:
        return None


# Refusal reason → HTTP status. 4xx surface lets clients react cleanly
# without parsing the body. The mapping is single-source-of-truth here
# so future tranches add new reasons in one place.
_REFUSAL_STATUS = {
    "unknown_tool": status.HTTP_404_NOT_FOUND,
    "unknown_agent": status.HTTP_404_NOT_FOUND,
    "constitution_missing": status.HTTP_404_NOT_FOUND,
    "tool_not_in_constitution": status.HTTP_404_NOT_FOUND,
    "bad_args": status.HTTP_400_BAD_REQUEST,
    "max_calls_exceeded": status.HTTP_403_FORBIDDEN,
    "side_effects_exceed_budget": status.HTTP_403_FORBIDDEN,
    # ADR-0019 T6 — genre risk floor violated at dispatch time
    # (Companion + frontier provider, Observer + non-read_only tool,
    # etc.). 403 because the request is well-formed; the policy says no.
    "genre_floor_violated": status.HTTP_403_FORBIDDEN,
}


@router.post(
    "/agents/{instance_id}/tools/call",
    response_model=ToolCallResponse,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def call_tool(
    instance_id: str,
    payload: ToolCallRequest,
    request: Request,
    registry: Registry = Depends(get_registry),
    dispatcher: ToolDispatcher = Depends(get_tool_dispatcher),
    write_lock: Lock = Depends(get_write_lock),
) -> ToolCallResponse:
    """Dispatch one tool call against the agent's runtime.

    The write lock wraps the entire dispatch so the read-then-write of
    the per-session counter is atomic against concurrent invocations.
    Inside the lock we also emit audit entries — same lock keeps the
    chain's head pointer consistent without a second mutex.
    """
    # Look up agent FIRST so a 404 here doesn't burn lock time. Outside
    # the lock because get_agent is read-only.
    try:
        agent = registry.get_agent(instance_id)
    except UnknownAgentError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown agent {instance_id!r}",
        )

    # The dispatcher reads/writes both the SQLite counter and the audit
    # chain. Hold the lock across the whole dispatch so the two stay in
    # sync (counter increment + dispatched event live in the same atomic
    # window). The write lock is a threading.Lock; FastAPI runs sync
    # endpoints on a threadpool but this is an async endpoint, so we
    # acquire the lock manually rather than via Depends.
    with write_lock:
        outcome = await dispatcher.dispatch(
            instance_id=instance_id,
            agent_dna=agent.dna,
            role=agent.role,
            genre=None,  # T6 hook — derived from constitution by dispatcher in v3
            session_id=payload.session_id,
            constitution_path=Path(agent.constitution_path),
            tool_name=payload.tool_name,
            tool_version=payload.tool_version,
            args=payload.args,
            # Active provider — None when not bootstrapped. Tools that
            # wrap LLM calls reach for this; pure-function tools ignore
            # it. Done lazily so a missing provider registry doesn't
            # crash dispatches that don't need one.
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

    if isinstance(outcome, DispatchPendingApproval):
        # 202 Accepted: the request has been recorded but the action
        # hasn't taken place. Caller polls /agents/{id}/pending_calls
        # (T3) for the operator's decision.
        from fastapi.responses import JSONResponse
        body = ToolCallResponse(
            status="pending_approval",
            tool_key=outcome.tool_key,
            audit_seq=outcome.audit_seq,
            ticket_id=outcome.ticket_id,
        )
        return JSONResponse(
            content=body.model_dump(),
            status_code=status.HTTP_202_ACCEPTED,
        )

    if isinstance(outcome, DispatchFailed):
        return ToolCallResponse(
            status="failed",
            tool_key=outcome.tool_key,
            audit_seq=outcome.audit_seq,
            failure_exception_type=outcome.exception_type,
        )

    if isinstance(outcome, DispatchRefused):
        http_status = _REFUSAL_STATUS.get(
            outcome.reason, status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=http_status,
            detail={
                "reason": outcome.reason,
                "detail": outcome.detail,
                "tool_key": outcome.tool_key,
                "audit_seq": outcome.audit_seq,
            },
        )

    # Should be unreachable — every dispatcher path returns one of the
    # four classes above. If we land here it's a programming error in
    # the dispatcher or this router. 500 is the right code.
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"unexpected dispatch outcome: {type(outcome).__name__}",
    )
