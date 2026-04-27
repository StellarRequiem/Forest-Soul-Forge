"""Cross-agent skill delegation — ADR-0033 A3.

Builds the closure that ``delegate.v1`` reaches via
``ToolContext.delegate``. The closure invokes another agent's skill
in-process through :class:`SkillRuntime`, threading the target
agent's identity through the dispatcher so any tool calls inside
the skill execute against the target's session, constraints, and
audit trail (not the caller's).

Design notes:

* **Lineage gating**: a caller can only delegate to an agent in its
  lineage chain (ancestors ∪ descendants ∪ self) by default. This
  mirrors the swarm escalation pattern (security_low → security_mid
  → security_high) without inventing a new compatibility rule.
  Operators who need to override pass ``allow_out_of_lineage=True``
  on the delegate call; the override is recorded in the audit
  ``agent_delegated`` event so the violation is visible.

* **Audit chain**: every successful delegation appends one
  ``agent_delegated`` entry BEFORE the target's skill runs. The
  entry carries caller, target, skill ref, reason, and an
  ``allow_out_of_lineage`` flag. The skill itself emits its own
  ``skill_invoked`` / ``skill_completed`` events; the chain reader
  can correlate them by sequence + timestamps.

* **Failure isolation**: a refusal (target missing, skill not
  installed, lineage violation, etc.) raises
  :class:`DelegateError` BEFORE the audit event is appended, so
  refused delegations don't pollute the chain. Once the audit event
  has landed, the skill runs to completion (success or failure)
  and the outcome is returned — failures during skill execution
  appear as ``SkillFailed`` outcomes, not as missing audit events.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable


class DelegateError(Exception):
    """Raised when a delegation request cannot be honored. The tool
    layer maps this to ``ToolValidationError`` so the dispatcher
    returns a refusal rather than a runtime crash."""


def _compute_lineage_chain(conn, instance_id: str) -> set[str]:
    """Return the set of agent ids in the reader's lineage chain
    (self ∪ ancestors ∪ descendants). Mirrors the helper in
    ``memory_recall.py`` but returns a set since we use it for
    membership tests, not iteration."""
    try:
        rows_up = conn.execute(
            "SELECT ancestor_id FROM agent_ancestry WHERE instance_id = ?;",
            (instance_id,),
        ).fetchall()
        rows_down = conn.execute(
            "SELECT instance_id FROM agent_ancestry WHERE ancestor_id = ?;",
            (instance_id,),
        ).fetchall()
    except Exception:
        return {instance_id}
    chain: set[str] = {instance_id}
    for r in rows_up:
        chain.add(r[0] if not hasattr(r, "keys") else r["ancestor_id"])
    for r in rows_down:
        chain.add(r[0] if not hasattr(r, "keys") else r["instance_id"])
    return chain


def build_delegator_factory(
    *,
    registry,
    audit_chain,
    dispatcher,
    skill_install_dir: Path,
    write_lock,
    provider_resolver: Callable[[], Any] | None = None,
) -> Callable[[str, str], Callable[..., Awaitable[Any]]]:
    """Build the factory that the dispatcher invokes per call.

    The returned factory takes the caller's identity and returns an
    async closure that executes a delegation. Captures everything
    the closure needs:

      * ``registry`` — for target agent lookup
      * ``audit_chain`` — for the ``agent_delegated`` event
      * ``dispatcher`` — to thread tool calls inside the target's
                          skill through the same dispatcher (so the
                          target's session, counters, and approval
                          queue stay coherent)
      * ``skill_install_dir`` — to load the target's skill manifest
      * ``write_lock`` — held while the SkillRuntime mutates state
      * ``provider_resolver`` — optional callable returning the
                                 active provider; passed through to
                                 SkillRuntime. None ⇒ no provider
                                 (some skills don't need an LLM).
    """
    from forest_soul_forge.forge.skill_manifest import (
        ManifestError,
        parse_manifest,
    )
    from forest_soul_forge.forge.skill_runtime import SkillRuntime

    def factory(caller_instance_id: str, caller_dna: str):
        async def delegate(
            *,
            target_instance_id: str,
            skill_name: str,
            skill_version: str,
            inputs: dict | None = None,
            reason: str,
            session_id: str | None = None,
            allow_out_of_lineage: bool = False,
        ):
            # 1. Validate target exists.
            try:
                target = registry.get_agent(target_instance_id)
            except Exception:
                target = None
            if target is None:
                raise DelegateError(
                    f"target instance {target_instance_id!r} not found"
                )
            if target_instance_id == caller_instance_id:
                raise DelegateError(
                    "target_instance_id must differ from the caller; "
                    "self-delegation is meaningless"
                )

            # 2. Lineage gating. The caller's lineage chain must include
            #    the target unless allow_out_of_lineage is set.
            if not allow_out_of_lineage:
                conn = registry._conn  # noqa: SLF001 — internal access by design
                chain = _compute_lineage_chain(conn, caller_instance_id)
                if target_instance_id not in chain:
                    raise DelegateError(
                        f"target {target_instance_id!r} is not in caller's "
                        "lineage chain. Pass allow_out_of_lineage=True to "
                        "override (the override is recorded in the audit "
                        "chain)."
                    )

            # 3. Resolve target's skill manifest from disk.
            manifest_path = (
                Path(skill_install_dir)
                / f"{skill_name}.v{skill_version}"
                / "skill.yaml"
            )
            if not manifest_path.exists():
                raise DelegateError(
                    f"skill {skill_name}.v{skill_version} not installed at "
                    f"{manifest_path}"
                )
            try:
                skill = parse_manifest(manifest_path.read_text(encoding="utf-8"))
            except ManifestError as e:
                raise DelegateError(
                    f"installed manifest invalid: {e.path}: {e.detail}"
                )

            # 4. Audit BEFORE the skill runs so a crash mid-skill
            #    still leaves the delegation visible. We tag the
            #    event with the CALLER's dna because the swarm reader
            #    wants to find delegations under the originating
            #    agent's lineage.
            audit_chain.append(
                "agent_delegated",
                {
                    "caller_instance":     caller_instance_id,
                    "target_instance":     target_instance_id,
                    "skill_name":          skill_name,
                    "skill_version":       skill_version,
                    "reason":              reason,
                    "allow_out_of_lineage": bool(allow_out_of_lineage),
                    "session_id":          session_id,
                },
                agent_dna=caller_dna,
            )

            # 5. Build a dispatch_tool closure for the TARGET — so
            #    nested tool calls inside the skill run against the
            #    target's identity (not the caller's), with the
            #    target's constitution path for constraint resolution.
            target_const_path = Path(target.constitution_path)

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
                return await dispatcher.dispatch(
                    instance_id=instance_id,
                    agent_dna=agent_dna,
                    role=role,
                    genre=genre,
                    session_id=session_id,
                    constitution_path=target_const_path,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    args=args,
                    provider=provider,
                )

            # 6. Run the skill. Locked under the write lock for the
            #    same reason the skills_run endpoint locks: the
            #    SkillRuntime mutates registry state through nested
            #    tool calls, and concurrent /skills/run vs.
            #    /tools/call must observe a coherent view.
            runtime = SkillRuntime(audit=audit_chain, dispatch_tool=dispatch_tool)
            provider = provider_resolver() if provider_resolver else None
            target_session = session_id or f"delegate-{caller_instance_id[:8]}"
            with write_lock:
                outcome = await runtime.run(
                    skill=skill,
                    instance_id=target_instance_id,
                    agent_dna=target.dna,
                    role=target.role,
                    genre=None,  # T6 hook — same as fast-path dispatch
                    session_id=target_session,
                    inputs=dict(inputs or {}),
                    provider=provider,
                    dry_run=False,
                )
            return outcome

        return delegate

    return factory
