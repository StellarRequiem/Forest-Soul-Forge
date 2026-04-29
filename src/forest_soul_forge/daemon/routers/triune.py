"""``/triune`` — bond three peer-root agents into a sealed triune.

ADR-003X K4. The triune pattern (Heartwood / Branch / Leaf, by
default) is three peer-root agents bound by their constitutions. The
bond is enforced inside ``delegate.v1`` via the
``triune.restrict_delegations`` flag — once set, a sister can only
delegate to her two partners, regardless of lineage or
``allow_out_of_lineage``.

This router exposes the bond-creation primitive:

* ``POST /triune/bond`` — given three already-birthed instance_ids
  and a bond_name, patch each agent's constitution YAML to add a
  ``triune`` block, then emit one ``ceremony`` event recording the
  bond. Atomic under the daemon's ``write_lock``.

What this endpoint does NOT do:

* It does not call ``/birth``. The operator (or the ``fsf triune``
  CLI wrapper) handles birthing first; this endpoint only seals the
  bond between agents that already exist.
* It does not recompute ``constitution_hash``. The triune block sits
  OUTSIDE ``Constitution.canonical_body()`` so existing hash
  verification stays stable. The bond is post-build metadata that
  ``delegate.v1`` reads directly from the YAML file at dispatch
  time (see ``tools/delegator.py::_load_caller_triune``).
* It does not unbond. Operator-initiated dissolution is a separate
  ceremony (filed for K4 follow-up — not required for the demo).
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
    TriuneBondRequest,
    TriuneBondResponse,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError

router = APIRouter(prefix="/triune", tags=["triune"])


@router.post(
    "/bond",
    response_model=TriuneBondResponse,
    dependencies=[Depends(require_writes_enabled)],
)
def triune_bond(
    body: TriuneBondRequest,
    registry: Registry = Depends(get_registry),
    audit: AuditChain = Depends(get_audit_chain),
    write_lock: threading.Lock = Depends(get_write_lock),
) -> TriuneBondResponse:
    bond_name = body.bond_name.strip()
    operator_id = body.operator_id.strip()
    if not bond_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bond_name must be a non-empty string",
        )
    if not operator_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="operator_id must be a non-empty string",
        )
    instance_ids = list(body.instance_ids)
    if len(set(instance_ids)) != 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instance_ids must contain three distinct agent ids",
        )

    # ---- 1. Validate all three exist + collect constitution paths -----
    agents: list[Any] = []
    for inst_id in instance_ids:
        try:
            agent = registry.get_agent(inst_id)
        except UnknownAgentError:
            agent = None
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"registry lookup failed for {inst_id}: {e}",
            ) from e
        if agent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"agent {inst_id!r} not found in registry",
            )
        agents.append(agent)

    # ---- 2. Verify constitution files are readable BEFORE any write ---
    # We refuse-early if any file is missing. The bond is atomic: either
    # all three constitutions get patched, or none do.
    parsed_constitutions: list[tuple[Path, dict]] = []
    for agent in agents:
        path = Path(agent.constitution_path)
        if not path.exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"constitution file missing for {agent.instance_id}: {path}",
            )
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"constitution YAML invalid for {agent.instance_id}: {e}",
            ) from e
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"constitution root must be a mapping for {agent.instance_id}",
            )
        parsed_constitutions.append((path, data))

    # ---- 3. Atomic patch under the write lock -------------------------
    # The lock guards against concurrent bond / unbond / archive on the
    # same agents. Within the lock we (a) write all three patched
    # constitutions, (b) emit the ceremony event. If any write fails
    # mid-batch, we roll back the writes that already landed by
    # restoring the original bytes.
    originals: list[tuple[Path, str]] = []
    try:
        with write_lock:
            for path, data in parsed_constitutions:
                originals.append((path, path.read_text(encoding="utf-8")))

            for (path, data), inst_id in zip(parsed_constitutions, instance_ids):
                partners = [other for other in instance_ids if other != inst_id]
                # Patch — replace any existing triune block. If the
                # operator re-bonds with a different bond_name, the new
                # values overwrite the old; we don't merge.
                data["triune"] = {
                    "bond_name": bond_name,
                    "partners": partners,
                    "restrict_delegations": bool(body.restrict_delegations),
                }
                # Atomic write via tmp + rename so a crash during write
                # doesn't leave a half-written constitution. yaml.safe_dump
                # with sort_keys=False preserves the operator-readable
                # ordering of the source file (best-effort — PyYAML's
                # round-trip is not byte-perfect, but the SEMANTIC
                # equivalence is what matters here, not formatting).
                tmp_path = path.with_suffix(path.suffix + ".tmp")
                tmp_path.write_text(
                    yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True),
                    encoding="utf-8",
                )
                tmp_path.replace(path)

            # ---- 4. Emit the bond ceremony event -----------------------
            event_data = {
                "ceremony_name": "triune.bonded",
                "summary": f"triune {bond_name!r} bonded: {instance_ids}",
                "operator_id": operator_id,
                "bond_name": bond_name,
                "instance_ids": instance_ids,
                "restrict_delegations": bool(body.restrict_delegations),
            }
            entry = audit.append(
                event_type="ceremony",
                event_data=event_data,
                agent_dna=None,
            )
    except HTTPException:
        # roll back any writes that landed before the failure
        _restore_originals(originals)
        raise
    except Exception as e:
        _restore_originals(originals)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"triune.bond failed mid-batch (rolled back): {e}",
        ) from e

    return TriuneBondResponse(
        bond_name=bond_name,
        instance_ids=instance_ids,
        restrict_delegations=bool(body.restrict_delegations),
        ceremony_seq=entry.seq,
        ceremony_timestamp=entry.timestamp,
    )


def _restore_originals(originals: list[tuple[Path, str]]) -> None:
    """Best-effort rollback. Restores each (path, text) pair we recorded
    before the failed batch began. Silently ignores per-file restore
    errors — the alternative (raising on rollback failure) buries the
    original error and leaves the operator with two failures to debug
    instead of one."""
    for path, original in originals:
        try:
            path.write_text(original, encoding="utf-8")
        except Exception:
            pass
