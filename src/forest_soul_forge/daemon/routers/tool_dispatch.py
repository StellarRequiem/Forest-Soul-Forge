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


def _load_agent_default_provider(
    constitution_path: Path,
    encryption_config=None,
) -> str | None:
    """ADR-0056 D4 / B193 — read the per-agent provider override
    from the constitution YAML. Returns the provider name (e.g.
    'frontier' or 'local') or None when unset / file missing /
    parse-error.

    The field is a single top-level string in the constitution:

        default_provider: frontier

    Operators add it after birth to route an agent's llm_think
    dispatches to a different provider than the daemon's
    registry default. Smith (the experimenter) defaults to
    frontier per ADR-0056 D4 — every other agent stays on the
    daemon-wide default unless explicitly opted in.
    """
    # ADR-0050 T5b (B272) — encryption-aware constitution read.
    # Lazy import to keep this router module independent of the
    # cryptography lib at import time.
    from forest_soul_forge.tools.dispatcher import _read_constitution_text
    text = _read_constitution_text(constitution_path, encryption_config)
    if text is None:
        return None
    try:
        import yaml
        data = yaml.safe_load(text) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("default_provider")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def _resolve_active_provider(request: Request, *, constitution_path: Path | None = None):
    """Resolve which model provider to hand the dispatcher.

    Resolution order (B193):
      1. If ``constitution_path`` is given AND the constitution
         specifies ``default_provider: <name>``, use that named
         provider from the registry.
      2. Otherwise, the daemon-wide active provider via
         ``ProviderRegistry.active()``.
      3. ``None`` when no provider registry is on app.state.

    Returns ``None`` when:
      - No registry wired (test contexts).
      - Per-agent override names a provider that isn't in the
        registry (defensive — log and fall through to active()).
      - active() raises for any reason.
    """
    pr = getattr(request.app.state, "providers", None)
    if pr is None:
        return None
    # Per-agent override.
    if constitution_path is not None:
        # ADR-0050 T5b (B272): thread the daemon's master_key so the
        # provider-override read decrypts .enc constitutions transparently.
        _master_key = getattr(request.app.state, "master_key", None)
        _enc_cfg = None
        if _master_key is not None:
            from forest_soul_forge.core.at_rest_encryption import (
                EncryptionConfig as _EncryptionConfig,
            )
            _enc_cfg = _EncryptionConfig(master_key=_master_key)
        override_name = _load_agent_default_provider(
            constitution_path, encryption_config=_enc_cfg,
        )
        if override_name is not None:
            try:
                return pr.get(override_name)
            except Exception:
                # Unknown provider name in the constitution —
                # fall through to active(). Operator may have
                # mistyped; the dispatch still works against the
                # daemon default. The audit chain captures the
                # provider via the dispatched event's metadata
                # so the operator sees the actual route on
                # review.
                pass
    # Daemon-wide active provider.
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
    # T2.2b — extract operator-supplied per-task caps. None means no cap.
    task_caps_dict: dict | None = None
    if payload.task_caps is not None:
        task_caps_dict = {
            "context_cap_tokens": payload.task_caps.context_cap_tokens,
            "usage_cap_tokens":   payload.task_caps.usage_cap_tokens,
        }

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
            #
            # B193: now accepts a constitution_path to honor per-agent
            # default_provider overrides. Smith's constitution sets
            # default_provider: frontier so its llm_think dispatches
            # route to Anthropic (claude-sonnet-4-6); every other
            # agent stays on the daemon-wide default unless their
            # constitution opts in.
            provider=_resolve_active_provider(
                request,
                constitution_path=Path(agent.constitution_path),
            ),
            # T2.2b — operator-supplied per-task caps. None when the
            # operator didn't set any; dispatcher treats absence as
            # "no per-task limit" (constitution + genre floor still apply).
            task_caps=task_caps_dict,
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
