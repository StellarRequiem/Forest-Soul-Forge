"""``delegate.v1`` — invoke another agent's skill.

ADR-0033 A3 — the swarm escalation primitive. A tier-N agent calls
``delegate.v1`` with the target agent + skill + args; the dispatcher
routes the request through the pre-bound ``ctx.delegate`` callable
which loads the target's skill manifest, runs it under the target's
identity, and returns the outcome.

Refusals (raise :class:`ToolValidationError`):

* delegate not wired on this dispatcher (test/degraded daemon)
* target agent not found
* target == caller (self-delegation)
* skill not installed at the daemon's skill_install_dir
* lineage violation (target not in caller's lineage chain) without
  ``allow_out_of_lineage=True`` override

Each successful delegation appends one ``agent_delegated`` audit
event BEFORE the target's skill begins executing, so a crash mid-
skill still leaves the delegation visible. The skill itself emits
its own ``skill_invoked`` / ``skill_completed`` events; correlate
by sequence + timestamps.

Side-effects classification: ``read_only`` for the dispatch itself.
The target's skill may include privileged steps that go through the
approval queue under the target's own constraints. The delegation
event is the audit trail link between the two contexts.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)
from forest_soul_forge.tools.delegator import DelegateError


_MAX_REASON = 512


class DelegateTool:
    """Args:
      target_instance_id (str, required): the agent whose skill runs.
      skill_name (str, required): the skill's name (no version).
      skill_version (str, required): the skill's version.
      inputs (object, optional): inputs passed to the skill's
        manifest. Defaults to {}.
      reason (str, required): one-line explanation recorded in the
        audit chain. ≤ 512 chars. The chain is the source of truth
        for "why did A invoke B?"; the reason makes that readable
        without re-reading the calling agent's logs.
      session_id (str, optional): session id for the target's run.
        Defaults to ``delegate-<caller_prefix>``. Useful when an
        operator wants to correlate a delegation chain across
        multiple dispatches.
      allow_out_of_lineage (bool, optional): override the lineage
        gate. False by default; True records the override in the
        audit event so the violation is visible.

    Output:
      { status, target_instance_id, skill_name, skill_version,
        invoked_seq, completed_seq, output (when succeeded),
        failed_step_id (when failed), failure_reason (when failed) }
    """

    name = "delegate"
    version = "1"
    side_effects = "read_only"
    # ADR-0021-amendment §5 — delegate dispatches another agent's
    # skill but doesn't itself mutate state; the dispatched skill's
    # tools carry their own initiative requirements. Required L3 here
    # so reactive Companion (L1) and suggestion-class (L2) agents
    # can't autonomously delegate work. Observer / Investigator /
    # Researcher / SW-track all reach by genre default. The chained
    # downstream tools each enforce their own floor independently —
    # delegation is not a back-door around per-tool requirements.
    required_initiative_level = "L3"

    def validate(self, args: dict[str, Any]) -> None:
        for field in ("target_instance_id", "skill_name", "skill_version", "reason"):
            v = args.get(field)
            if not isinstance(v, str) or not v.strip():
                raise ToolValidationError(
                    f"{field} must be a non-empty string"
                )
        if len(args["reason"]) > _MAX_REASON:
            raise ToolValidationError(
                f"reason exceeds max {_MAX_REASON} chars; got {len(args['reason'])}"
            )
        inputs = args.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            raise ToolValidationError(
                "inputs must be an object when provided"
            )
        allow = args.get("allow_out_of_lineage")
        if allow is not None and not isinstance(allow, bool):
            raise ToolValidationError(
                "allow_out_of_lineage must be a boolean when provided"
            )
        sid = args.get("session_id")
        if sid is not None and not isinstance(sid, str):
            raise ToolValidationError(
                "session_id must be a string when provided"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        if ctx.delegate is None:
            raise ToolValidationError(
                "delegate.v1: no delegator wired on dispatcher (this "
                "daemon was started without the delegator factory). "
                "Cross-agent invocation is unavailable."
            )

        try:
            outcome = await ctx.delegate(
                target_instance_id=args["target_instance_id"],
                skill_name=args["skill_name"],
                skill_version=args["skill_version"],
                inputs=args.get("inputs") or {},
                reason=args["reason"],
                session_id=args.get("session_id"),
                allow_out_of_lineage=bool(args.get("allow_out_of_lineage") or False),
            )
        except DelegateError as e:
            # Refusals from the delegator (target missing, lineage
            # violation, manifest invalid) become tool-level
            # ToolValidationErrors so the dispatcher returns 4xx
            # rather than crashing.
            raise ToolValidationError(f"delegate refused: {e}") from e

        # Marshal the SkillRuntime outcome into a flat dict. We
        # deliberately don't import the SkillSucceeded/SkillFailed
        # types here — using duck typing keeps this file decoupled
        # from the skill runtime's class hierarchy and lets the
        # delegator return either real objects or test stubs.
        out: dict[str, Any] = {
            "target_instance_id": args["target_instance_id"],
            "skill_name":         args["skill_name"],
            "skill_version":      args["skill_version"],
        }
        if hasattr(outcome, "skill_hash"):
            out["skill_hash"] = outcome.skill_hash
        if hasattr(outcome, "invoked_seq"):
            out["invoked_seq"] = outcome.invoked_seq
        if hasattr(outcome, "completed_seq"):
            out["completed_seq"] = outcome.completed_seq

        # Discriminate succeeded vs failed via the presence of
        # `output` (succeeded) or `failed_step_id` (failed). Both
        # outcome dataclasses have `skill_name`/`skill_version` so
        # those alone don't disambiguate.
        if hasattr(outcome, "output") and getattr(outcome, "output", None) is not None:
            out["status"] = "succeeded"
            out["output"] = outcome.output
            out["steps_executed"] = getattr(outcome, "steps_executed", 0)
            out["steps_skipped"]  = getattr(outcome, "steps_skipped", 0)
        elif hasattr(outcome, "failed_step_id"):
            out["status"] = "failed"
            out["failed_step_id"]  = outcome.failed_step_id
            out["failure_reason"]  = getattr(outcome, "failure_reason", None)
            out["failure_detail"]  = getattr(outcome, "failure_detail", None)
        else:
            # Unknown outcome shape — surface raw repr so the operator
            # can investigate. Should be unreachable in production.
            out["status"] = "unknown"
            out["raw"] = repr(outcome)

        return ToolResult(
            output=out,
            metadata={
                "caller_instance":   ctx.instance_id,
                "target_instance":   args["target_instance_id"],
                "skill_ref":         f"{args['skill_name']}.v{args['skill_version']}",
                "allow_out_of_lineage": bool(args.get("allow_out_of_lineage") or False),
                "outcome_status":    out.get("status"),
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"delegated {args['skill_name']}.v{args['skill_version']} "
                f"→ {args['target_instance_id'][:8]}…"
            ),
        )
