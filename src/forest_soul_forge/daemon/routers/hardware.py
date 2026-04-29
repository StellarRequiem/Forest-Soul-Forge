"""``/agents/{instance_id}/hardware/...`` — hardware-binding ops.

ADR-003X K6. The unbind endpoint is the deliberate operator action
that releases an agent's pin to a "home" machine. Stripping the
``hardware_binding`` block from the constitution YAML allows the
agent to load on any machine, including the next /birth-bound one
after migration.

Currently exposes only POST /unbind. A future GET /status could
report fingerprint + match-state for operator visibility, but the
chronicle export already shows hardware_bound + hardware_unbound
events so /status is low-priority for v1.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, status

from forest_soul_forge.core.audit_chain import AuditChain
from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_write_lock,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    HardwareUnbindRequest,
    HardwareUnbindResponse,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError

router = APIRouter(prefix="/agents", tags=["hardware"])


@router.post(
    "/{instance_id}/hardware/unbind",
    response_model=HardwareUnbindResponse,
    dependencies=[Depends(require_writes_enabled)],
)
def hardware_unbind(
    instance_id: str,
    body: HardwareUnbindRequest,
    registry: Registry = Depends(get_registry),
    audit: AuditChain = Depends(get_audit_chain),
    write_lock: threading.Lock = Depends(get_write_lock),
) -> HardwareUnbindResponse:
    operator_id = body.operator_id.strip()
    reason = body.reason.strip()
    if not operator_id:
        raise HTTPException(status_code=400, detail="operator_id required")
    if not reason:
        raise HTTPException(status_code=400, detail="reason required")

    # 1. Load agent + constitution.
    try:
        agent = registry.get_agent(instance_id)
    except UnknownAgentError:
        raise HTTPException(status_code=404, detail=f"agent {instance_id!r} not found")
    const_path = Path(agent.constitution_path)
    if not const_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"constitution file missing for {instance_id}: {const_path}",
        )
    try:
        text = const_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"constitution YAML invalid for {instance_id}: {e}",
        ) from e
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail=f"constitution root must be a mapping for {instance_id}",
        )

    previous_binding: str | None = None
    block = data.get("hardware_binding")
    if isinstance(block, dict):
        previous_binding = block.get("fingerprint")
    elif isinstance(block, str):
        previous_binding = block

    # 2. Strip the block + write back atomically.
    with write_lock:
        if "hardware_binding" in data:
            del data["hardware_binding"]
            tmp_path = const_path.with_suffix(const_path.suffix + ".tmp")
            tmp_path.write_text(
                yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )
            tmp_path.replace(const_path)

        # 3. Emit hardware_unbound — even when the agent had no binding
        #    we still record the operator's intent (auditable evidence
        #    that the operator intentionally removed any future binding).
        entry = audit.append(
            "hardware_unbound",
            {
                "instance_id": instance_id,
                "previous_fingerprint": previous_binding,
                "operator_id": operator_id,
                "reason": reason,
            },
            agent_dna=agent.dna,
        )

        # 4. Lift quarantine in app.state if this agent was there. The
        #    set lives on app.state.quarantined_agents (created by the
        #    lifespan check). Lazy import + getattr so unbind works even
        #    when the lifespan check didn't populate it.
        try:
            from fastapi import Request as _Req  # noqa: F401 (just to confirm fastapi avail)
            # We can't reach app.state from a Depends-only signature
            # cheaply; the quarantine check at next dispatch will
            # re-evaluate using the (now-updated) constitution file. So
            # nothing to do here — the next agent.dispatch() lookup re-
            # reads and finds the binding gone.
            pass
        except Exception:
            pass

    return HardwareUnbindResponse(
        instance_id=instance_id,
        previous_binding=previous_binding,
        seq=entry.seq,
        timestamp=entry.timestamp,
    )
