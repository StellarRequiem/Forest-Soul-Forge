"""``POST /agents/{instance_id}/skills/run`` — ADR-0031 T2b.

Loads the skill manifest from disk (ad-hoc, pre ADR-0031 T5 catalog),
wires SkillRuntime to the daemon's ToolDispatcher, runs the DAG under
the write lock, returns the assembled output.

Skill runtime invariants preserved here:

- Same write lock as /tools/call. Skill execution emits N tool
  dispatch events + skill events; lock keeps the audit chain head
  consistent across all of them.
- Tool dispatches inside the skill go through the agent's normal
  constraint policy + genre floor + counter — the skill is just
  an orchestrator, not a privilege boundary.
- Pending-approval at any step pauses the skill (returns
  ``status=failed`` with reason ``tool_pending_approval``). T2.5 of
  ADR-0031 will wire skill-level resume.
"""
from __future__ import annotations

from pathlib import Path
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forest_soul_forge.daemon.deps import (
    get_audit_chain,
    get_registry,
    get_settings,
    get_tool_dispatcher,
    get_write_lock,
    require_api_token,
    require_writes_enabled,
)
from forest_soul_forge.daemon.schemas import (
    SkillRunRequest,
    SkillRunResponse,
)
from forest_soul_forge.registry import Registry
from forest_soul_forge.registry.registry import UnknownAgentError


router = APIRouter(tags=["skills"])


def _skill_manifest_path(settings, name: str, version: str) -> Path:
    """Where the ad-hoc loader looks for an installed manifest.

    ADR-0031 T5 will replace this with a registry-backed catalog;
    until then the operator drops manifests directly into the
    install dir. Defaults to ``data/forge/skills/installed/`` —
    overridable via ``settings.skill_install_dir`` once it's added
    to DaemonSettings (next tranche).
    """
    base = getattr(
        settings, "skill_install_dir",
        Path("data/forge/skills/installed"),
    )
    return Path(base) / f"{name}.v{version}.yaml"


@router.post(
    "/agents/{instance_id}/skills/run",
    response_model=SkillRunResponse,
    dependencies=[Depends(require_writes_enabled), Depends(require_api_token)],
)
async def run_skill(
    instance_id: str,
    payload: SkillRunRequest,
    request: Request,
    registry: Registry = Depends(get_registry),
    write_lock: Lock = Depends(get_write_lock),
    audit=Depends(get_audit_chain),
    settings=Depends(get_settings),
    tool_dispatcher=Depends(get_tool_dispatcher),
) -> SkillRunResponse:
    """Execute one skill against the named agent."""
    # Look up the agent OUTSIDE the lock — read-only.
    try:
        agent = registry.get_agent(instance_id)
    except UnknownAgentError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown agent {instance_id!r}",
        )

    # Load the manifest. 404 on missing — operator should run
    # `fsf forge skill` first, then drop the staged file into
    # data/forge/skills/installed/ (T7 will automate this move).
    manifest_path = _skill_manifest_path(
        settings, payload.skill_name, payload.skill_version,
    )
    if not manifest_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"skill {payload.skill_name}.v{payload.skill_version} not "
                f"installed at {manifest_path}. Stage via `fsf forge skill` "
                f"and copy the manifest into the install dir."
            ),
        )
    try:
        from forest_soul_forge.forge.skill_manifest import (
            ManifestError,
            parse_manifest,
        )
        skill = parse_manifest(manifest_path.read_text(encoding="utf-8"))
    except ManifestError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"installed manifest invalid: {e.path}: {e.detail}",
        )

    # Build the runtime — wire dispatch_tool to the daemon's
    # ToolDispatcher.dispatch with the agent's identity baked in.
    from forest_soul_forge.forge.skill_runtime import (
        SkillFailed,
        SkillRuntime,
        SkillSucceeded,
    )

    constitution_path = Path(agent.constitution_path)

    async def dispatch_tool(
        *,
        tool_name: str,
        tool_version: str,
        args: dict,
        instance_id: str,
        agent_dna: str,
        role: str,
        genre: str | None,
        session_id: str,
        provider,
    ):
        return await tool_dispatcher.dispatch(
            instance_id=instance_id,
            agent_dna=agent_dna,
            role=role,
            genre=genre,
            session_id=session_id,
            constitution_path=constitution_path,
            tool_name=tool_name,
            tool_version=tool_version,
            args=args,
            provider=provider,
        )

    runtime = SkillRuntime(audit=audit, dispatch_tool=dispatch_tool)

    # Resolve provider once for the whole skill — all steps share it.
    provider = _resolve_active_provider(request)

    with write_lock:
        outcome = await runtime.run(
            skill=skill,
            instance_id=instance_id,
            agent_dna=agent.dna,
            role=agent.role,
            genre=None,  # T6 hook — same as fast-path dispatch
            session_id=payload.session_id,
            inputs=payload.inputs,
            provider=provider,
            dry_run=payload.dry_run,
        )

    if isinstance(outcome, SkillSucceeded):
        return SkillRunResponse(
            status="succeeded",
            skill_name=outcome.skill_name,
            skill_version=outcome.skill_version,
            skill_hash=outcome.skill_hash,
            invoked_seq=outcome.invoked_seq,
            completed_seq=outcome.completed_seq,
            output=outcome.output,
            steps_executed=outcome.steps_executed,
            steps_skipped=outcome.steps_skipped,
        )
    if isinstance(outcome, SkillFailed):
        return SkillRunResponse(
            status="failed",
            skill_name=outcome.skill_name,
            skill_version=outcome.skill_version,
            skill_hash=outcome.skill_hash,
            invoked_seq=outcome.invoked_seq,
            completed_seq=outcome.completed_seq,
            failed_step_id=outcome.failed_step_id,
            failure_reason=outcome.failure_reason,
            failure_detail=outcome.detail,
            bindings_at_failure=outcome.bindings_at_failure,
        )
    # Unreachable.
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"unexpected skill outcome: {type(outcome).__name__}",
    )


def _resolve_active_provider(request: Request):
    """Mirror of tool_dispatch._resolve_active_provider so skills get
    the same provider plumbing. Best-effort; tools that don't need a
    provider tolerate None."""
    pr = getattr(request.app.state, "providers", None)
    if pr is None:
        return None
    try:
        return pr.active()
    except Exception:
        return None
