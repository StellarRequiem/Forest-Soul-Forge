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


def _load_caller_triune(registry, caller_instance_id: str) -> dict | None:
    """Return the caller's ``triune`` constitution block, or None.

    ADR-003X K4. The triune block is an optional constitution
    extension declaring the caller's bonded sisters:

        triune:
          bond_name: aurora
          partners: [<instance_id>, <instance_id>]
          restrict_delegations: true

    When ``restrict_delegations`` is true, ``delegate.v1`` refuses
    any target outside ``partners`` regardless of lineage or
    ``allow_out_of_lineage``. Returns None if the constitution has
    no triune block (caller is a normal agent), or if the file
    cannot be read (treated as "no enforcement" — the read failure
    surfaces elsewhere in the dispatcher's constitution-hash check).
    """
    try:
        agent = registry.get_agent(caller_instance_id)
    except Exception:
        return None
    if agent is None:
        return None
    try:
        import yaml  # local import — yaml is already a hard dep
        text = Path(agent.constitution_path).read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
    except Exception:
        return None
    block = data.get("triune") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return None
    return block


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

            # 1b. Triune enforcement (ADR-003X K4). When the caller's
            #     constitution has triune.restrict_delegations=true, the
            #     ONLY permitted targets are sisters in triune.partners.
            #     This refuses BEFORE lineage gating because triune
            #     restriction is the stronger constraint — it cannot be
            #     bypassed by allow_out_of_lineage.
            triune_block = _load_caller_triune(registry, caller_instance_id)
            triune_internal = False
            triune_bond_name: str | None = None
            if triune_block and triune_block.get("restrict_delegations"):
                partners = tuple(triune_block.get("partners") or ())
                triune_bond_name = triune_block.get("bond_name") or "<unnamed>"
                if target_instance_id not in partners:
                    # Audit the rejection so operators see attempted
                    # out-of-triune calls — silent refusals would mask
                    # a misconfigured agent or a hostile prompt steering
                    # the agent off-bond.
                    try:
                        audit_chain.append(
                            "out_of_triune_attempt",
                            {
                                "caller_instance":  caller_instance_id,
                                "target_instance":  target_instance_id,
                                "skill_name":       skill_name,
                                "skill_version":    skill_version,
                                "bond_name":        triune_bond_name,
                                "allowed_partners": list(partners),
                                "reason":           reason,
                            },
                            agent_dna=caller_dna,
                        )
                    except Exception:
                        # Don't let audit failure mask the refusal.
                        pass
                    raise DelegateError(
                        f"triune restriction: target {target_instance_id!r} "
                        f"is not in caller's triune {triune_bond_name!r} "
                        f"partners {list(partners)}. allow_out_of_lineage "
                        "does NOT bypass triune restriction — the bond is "
                        "sealed by the constitution."
                    )
                triune_internal = True

            # 2. Lineage gating. The caller's lineage chain must include
            #    the target unless allow_out_of_lineage is set, OR the
            #    target is a triune sister (sisters are peers by design;
            #    requiring the operator to pass allow_out_of_lineage on
            #    every triune call would be ceremony without value).
            #
            # T2.1: emit a governance_relaxed event when the override
            # actually MATTERED — i.e. target wasn't in the chain AND
            # the caller used allow_out_of_lineage to bypass. This makes
            # operator-bypass visible as its own filterable event type
            # (Discord cross-check item #1: "everyone ends up with YOLO
            # mode toggled on eventually" is the failure mode this
            # closes — silent relaxations were the previous gap).
            if not triune_internal:
                conn = registry._conn  # noqa: SLF001 — internal access by design
                chain = _compute_lineage_chain(conn, caller_instance_id)
                target_in_chain = target_instance_id in chain
                if not target_in_chain and not allow_out_of_lineage:
                    raise DelegateError(
                        f"target {target_instance_id!r} is not in caller's "
                        "lineage chain. Pass allow_out_of_lineage=True to "
                        "override (the override is recorded in the audit "
                        "chain)."
                    )
                if not target_in_chain and allow_out_of_lineage:
                    # The override was load-bearing — emit governance_relaxed
                    # so chronicles surface this as a warn-class event,
                    # not a routine call.
                    try:
                        audit_chain.append(
                            "governance_relaxed",
                            {
                                "relaxation_type":  "out_of_lineage_delegate",
                                "caller_instance":  caller_instance_id,
                                "target_instance":  target_instance_id,
                                "skill_name":       skill_name,
                                "skill_version":    skill_version,
                                "reason":           reason,
                                "session_id":       session_id,
                            },
                            agent_dna=caller_dna,
                        )
                    except Exception:
                        # Don't let audit failure mask the delegation —
                        # the agent_delegated event will still land below
                        # with allow_out_of_lineage=True visible.
                        pass

            # 3. Resolve target's skill manifest from disk. Two install
            #    patterns are supported, matching skills_run.py's flat
            #    pattern AND the older subdirectory pattern:
            #      flat:   <install_dir>/<name>.v<version>.yaml
            #      subdir: <install_dir>/<name>.v<version>/skill.yaml
            #    Try flat first (it's what `fsf install skill` and the
            #    swarm-install script produce). Fall through to subdir
            #    so legacy installations keep working.
            install_root = Path(skill_install_dir)
            flat_path = install_root / f"{skill_name}.v{skill_version}.yaml"
            subdir_path = (
                install_root
                / f"{skill_name}.v{skill_version}"
                / "skill.yaml"
            )
            if flat_path.exists():
                manifest_path = flat_path
            elif subdir_path.exists():
                manifest_path = subdir_path
            else:
                raise DelegateError(
                    f"skill {skill_name}.v{skill_version} not installed "
                    f"(checked {flat_path} and {subdir_path})"
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
            agent_delegated_payload: dict[str, Any] = {
                "caller_instance":     caller_instance_id,
                "target_instance":     target_instance_id,
                "skill_name":          skill_name,
                "skill_version":       skill_version,
                "reason":              reason,
                "allow_out_of_lineage": bool(allow_out_of_lineage),
                "session_id":          session_id,
            }
            if triune_internal:
                # Make triune-internal calls visible in the audit chain
                # without inventing a new event type. The bond_name is
                # stable across the triune's lifetime so an operator can
                # filter "show me everything Aurora's sisters did to
                # each other."
                agent_delegated_payload["triune_bond_name"] = triune_bond_name
                agent_delegated_payload["triune_internal"] = True
            audit_chain.append(
                "agent_delegated",
                agent_delegated_payload,
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
