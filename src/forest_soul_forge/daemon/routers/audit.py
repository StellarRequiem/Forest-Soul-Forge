"""``/audit`` — read-only audit chain mirror.

Source of truth is the canonical JSONL at the configured
``audit_chain_path`` (default ``examples/audit_chain.jsonl`` per
``daemon/config.py``; override via ``FSF_AUDIT_CHAIN_PATH``). Per
ADR-0006 the registry is a derived index over that file. This router
reflects the split:

* ``/audit/tail`` reads the canonical JSONL directly via :meth:`AuditChain.tail`.
  Runtime events (tool dispatch, agent delegation, skill steps) appear
  immediately — the registry's ``audit_events`` table only mirrors what's
  ingested at lifespan, so a registry-only tail would silently drop every
  live event between process boots.
* ``/audit/agent/{instance_id}`` and ``/audit/by-dna/{dna}`` keep using
  the registry — they need indexed lookup, which is exactly what the
  derived index is for.

For tamper verification, use ``scripts/verify_audit_chain.py`` or the
upcoming ``/audit/verify`` endpoint (Phase 3 write tier).
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from forest_soul_forge.core.audit_chain import AuditChain, ChainEntry
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    AuditEventOut,
    AuditListOut,
    CeremonyEmitRequest,
    CeremonyEmitResponse,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError

router = APIRouter(prefix="/audit", tags=["audit"])


def _to_out(row) -> AuditEventOut:  # noqa: ANN001 — AuditRow is a frozen dataclass
    return AuditEventOut(**asdict(row))


def _chain_entry_to_out(entry: ChainEntry) -> AuditEventOut:
    """Map a canonical ChainEntry to the API's AuditEventOut shape.

    ``instance_id`` is conventionally embedded inside ``event_data`` by
    runtime emitters (dispatcher, delegator, skill runtime) — pulling it
    up here gives the same field shape callers see from the registry
    mirror, so the response schema doesn't fork between the two paths.

    ``event_json`` matches the registry's canonical-ish dump (sorted keys,
    no whitespace) so two events compared across the two endpoints
    serialize identically.
    """
    instance_id = entry.event_data.get("instance_id")
    return AuditEventOut(
        seq=entry.seq,
        timestamp=entry.timestamp,
        agent_dna=entry.agent_dna,
        instance_id=str(instance_id) if instance_id is not None else None,
        event_type=entry.event_type,
        event_json=json.dumps(entry.event_data, sort_keys=True, separators=(",", ":")),
        entry_hash=entry.entry_hash,
    )


@router.get("/tail", response_model=AuditListOut)
async def audit_tail(
    n: int = Query(default=100, ge=1, le=10_000),
    chain: AuditChain = Depends(get_audit_chain),
) -> AuditListOut:
    """Return the most recent ``n`` audit events, newest first.

    Reads the canonical JSONL directly so runtime events are visible
    without waiting for a lifespan restart to re-ingest them.
    """
    entries = chain.tail(n)
    return AuditListOut(
        count=len(entries),
        events=[_chain_entry_to_out(e) for e in entries],
    )


@router.get("/agent/{instance_id}", response_model=AuditListOut)
async def audit_for_agent(
    instance_id: str,
    registry: Registry = Depends(get_registry),
) -> AuditListOut:
    # Confirm the agent exists so a typo produces a clean 404 rather than
    # an empty list that could be misread as "nothing happened".
    try:
        registry.get_agent(instance_id)
    except UnknownAgentError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e)
        ) from e
    rows = registry.audit_for_agent(instance_id=instance_id)
    return AuditListOut(count=len(rows), events=[_to_out(r) for r in rows])


@router.get("/by-dna/{dna}", response_model=AuditListOut)
async def audit_by_dna(
    dna: str,
    registry: Registry = Depends(get_registry),
) -> AuditListOut:
    rows = registry.audit_for_agent(dna=dna)
    return AuditListOut(count=len(rows), events=[_to_out(r) for r in rows])


# ADR-003X K2 — operator-emitted ceremony events. POST writes a
# first-class `ceremony` event to the canonical chain. Distinct
# from tool-emitted events because the EMITTER is a human, not an
# agent — used to mark milestones, identity events, governance
# decisions that don't fit any tool call. Examples: an Iron Gate
# memory-promotion ceremony (Nexus pattern), an agent retirement,
# a tier promotion, an operator-acknowledged transition.
#
# The ceremony_name is operator-chosen (free string); summary +
# operator_id are required; metadata is an optional structured
# payload that lands in event_data alongside the rest. Emission
# is gated by writes_enabled — read-only daemons reject with 403.
@router.post(
    "/ceremony",
    response_model=CeremonyEmitResponse,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def audit_ceremony(
    body: CeremonyEmitRequest,
    chain: AuditChain = Depends(get_audit_chain),
    write_lock=Depends(get_write_lock),
) -> CeremonyEmitResponse:
    if not body.ceremony_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ceremony_name must be a non-empty string",
        )
    if not body.summary.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="summary must be a non-empty string",
        )
    if not body.operator_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="operator_id must be a non-empty string",
        )

    # Build event_data — operator's structured payload + the headline
    # fields. Caller's metadata can shadow the headline fields if they
    # set the same keys; that's their problem, we audit-trail what
    # they sent.
    event_data: dict = {
        "ceremony_name": body.ceremony_name,
        "summary": body.summary,
        "operator_id": body.operator_id,
    }
    if body.metadata:
        # Preserve operator's keys; if they collide with the headline
        # fields, theirs win (we still log the merged shape).
        event_data.update(body.metadata)

    with write_lock:
        entry = chain.append(
            event_type="ceremony",
            event_data=event_data,
            agent_dna=None,  # operator events have no agent identity
        )

    return CeremonyEmitResponse(
        seq=entry.seq,
        timestamp=entry.timestamp,
        entry_hash=entry.entry_hash,
        event_type=entry.event_type,
        ceremony_name=body.ceremony_name,
    )


# ADR-003X K3 — server-sent-events stream of new audit chain entries.
# Replaces the F6 status-bar polling pattern with push semantics:
# clients subscribe once, get every new chain entry as a server-sent
# event, and reconnect with a Last-Event-ID header to resume from
# where they left off.
#
# Implementation: tail the canonical JSONL by polling its size at a
# small interval. When the file grows, read the new bytes, parse them
# line-by-line, emit each as an SSE event with id=<seq>. Keeps the
# AuditChain.append signature unchanged (no async event bus) and
# tolerates a daemon restart cleanly (Last-Event-ID resumes the tail
# at the right position).
#
# Heartbeat every 30s prevents intermediate proxies from closing an
# idle connection. Disconnect detection via Request.is_disconnected()
# stops the polling loop within one tick of the client going away.
SSE_POLL_INTERVAL_S = 0.5
SSE_HEARTBEAT_INTERVAL_S = 30.0


@router.get("/stream")
async def audit_stream(
    request: Request,
    chain: AuditChain = Depends(get_audit_chain),
):
    """SSE: stream new audit chain entries as they land.

    Replays from ``Last-Event-ID`` header (the seq of the last event
    the client saw) so reconnects resume cleanly. When the header is
    absent, the stream starts from the current tail position — the
    client gets future events, not a backfill (that's what
    /audit/tail is for).
    """
    # Initial cursor: client-provided Last-Event-ID, else current EOF.
    # We store it as a byte offset into the file because seq alone
    # would require a full chain scan to translate. The trade-off:
    # if the file is rotated or compacted we'd miss entries, but
    # the current chain is append-only with no rotation.
    chain_path = chain.path
    last_id = request.headers.get("last-event-id")
    if last_id is not None:
        # Map seq → byte offset. Cheap because the chain is append-only
        # and tail-friendly: scan forward from start collecting offsets
        # until we find seq > last_id.
        try:
            target_seq = int(last_id)
        except ValueError:
            target_seq = -1
        offset = _seq_to_offset(chain_path, target_seq)
    else:
        offset = chain_path.stat().st_size if chain_path.exists() else 0

    async def event_stream():
        nonlocal offset
        last_heartbeat = asyncio.get_event_loop().time()
        while True:
            if await request.is_disconnected():
                break
            try:
                size = chain_path.stat().st_size if chain_path.exists() else 0
            except OSError:
                size = 0
            if size > offset:
                # New bytes — read them, parse JSONL line by line.
                with chain_path.open("rb") as f:
                    f.seek(offset)
                    new_bytes = f.read(size - offset)
                offset = size
                buf = new_bytes.decode("utf-8", errors="replace")
                # Yield one SSE message per complete line. Partial last
                # line (if write was mid-flush) waits for the next tick.
                lines = buf.split("\n")
                # If the buffer didn't end with newline, the last
                # element is partial — push the offset back so we re-read
                # it on the next tick.
                if buf and not buf.endswith("\n"):
                    partial = lines.pop()
                    offset -= len(partial.encode("utf-8"))
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        # Malformed line — skip; verify() reports the break.
                        continue
                    seq = obj.get("seq")
                    payload = json.dumps(obj, separators=(",", ":"))
                    yield f"id: {seq}\nevent: chain_entry\ndata: {payload}\n\n"
                last_heartbeat = asyncio.get_event_loop().time()
            else:
                # No new entries this tick — heartbeat if it's been a while.
                now = asyncio.get_event_loop().time()
                if now - last_heartbeat >= SSE_HEARTBEAT_INTERVAL_S:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
            await asyncio.sleep(SSE_POLL_INTERVAL_S)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx — disable response buffering
        },
    )


def _seq_to_offset(path, target_seq: int) -> int:
    """Return the byte offset of the line just AFTER ``target_seq``.

    Used to resume an SSE stream from a Last-Event-ID. Linear scan;
    chains stay tail-friendly because we're looking for a specific
    seq from the end. For very large chains this is O(N) bytes —
    acceptable for v1, can be replaced with an offset index later.
    """
    if not path.exists() or target_seq < 0:
        return 0
    offset = 0
    with path.open("rb") as f:
        for raw in f:
            try:
                obj = json.loads(raw)
                if obj.get("seq", -1) > target_seq:
                    return offset
            except json.JSONDecodeError:
                pass
            offset += len(raw)
    # Asked for a seq beyond the current EOF — start at EOF.
    return offset
