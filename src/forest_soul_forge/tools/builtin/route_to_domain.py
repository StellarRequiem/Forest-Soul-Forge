"""``route_to_domain.v1`` — ADR-0067 T3 (B281).

The actuator side of cross-domain orchestration. Consumes the output
of decompose_intent.v1 (T2), gates on status='routable', emits a
``domain_routed`` audit event that captures the routing decision,
then fires delegate.v1 to invoke the target agent's skill.

## Why this is its own tool, not a parameter on delegate.v1

Cross-domain routing is a distinct audit concern from agent
delegation. The chain entry shape is different (intent_hash +
target_domain + capability + confidence vs. caller + target +
skill + reason). Splitting at the tool boundary keeps audit-chain
queries clean:

  - "show me every cross-domain routing decision" → filter on
    event_type=domain_routed
  - "show me every cross-agent invocation" → filter on
    event_type=agent_delegated

The dispatcher emits BOTH events for each successful route_to_domain
call (one domain_routed before, one agent_delegated after) so the
forensic chain captures the orchestrator's intent + the runtime's
follow-through.

## Why this tool doesn't resolve agent instance id

T3 ships the audited-routing primitive. The CALLER tells route_to_domain
which target_instance_id to dispatch to. The agent-resolution
heuristic ("given a domain + capability, which alive agent
instance handles this?") lives in T4 (full routing engine) where it
can be operator-configurable via handoffs.yaml + learned routes.

For T3, the orchestrator agent (queued T5) will resolve agents
internally before calling route_to_domain. Operators who want to
script direct cross-domain calls pass target_instance_id explicitly.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)
from forest_soul_forge.tools.builtin.delegate import DelegateError


MAX_INTENT_LEN = 4000
MAX_REASON_LEN = 512


class RouteToDomainTool:
    """Audited cross-domain routing primitive.

    Args:
      target_domain (str, required): domain_id from the registry.
        Must point at a domain with status in {partial, live}.
      target_capability (str, required): capability tag declared by
        the target domain. Used in the audit event for forensic
        replay.
      target_instance_id (str, required): the agent who will receive
        the delegate. T3 doesn't resolve this — T4 (full routing
        engine) ships the resolver.
      skill_name (str, required): skill on the target agent to run.
      skill_version (str, required): skill version (e.g., '1').
      intent (str, required): operator-utterance fragment that
        triggered this routing decision. Hashed for the audit
        event; not stored verbatim.
      reason (str, required): one-line operator-readable rationale.
        Recorded in both the domain_routed AND the downstream
        agent_delegated audit events.
      inputs (object, optional): inputs forwarded to the delegated
        skill's manifest. Defaults to {intent: <intent_text>}.
      confidence (number, optional, default 1.0): confidence score
        from decompose_intent. Recorded in domain_routed.
      decomposition_seq (int, optional): seq of the decompose_intent
        tool_call_succeeded event whose output this routing came
        from. Lets auditors join the decomposition + the routing.
      allow_planned (bool, optional, default False): override the
        'planned domain refuses' gate. True records the override
        in the audit event.

    Output:
      {
        target_domain, target_capability, target_instance_id,
        decomposition_seq, status, delegate_output (when succeeded)
      }
    """

    name = "route_to_domain"
    version = "1"
    side_effects = "read_only"
    # ADR-0021-am §5: route_to_domain dispatches another agent's
    # skill (via delegate underneath). Required L3 mirrors delegate.v1's
    # floor — reactive Companion (L1) can't autonomously route across
    # domains.
    required_initiative_level = "L3"

    def validate(self, args: dict[str, Any]) -> None:
        for field in (
            "target_domain", "target_capability", "target_instance_id",
            "skill_name", "skill_version", "intent", "reason",
        ):
            v = args.get(field)
            if not isinstance(v, str) or not v.strip():
                raise ToolValidationError(
                    f"{field} must be a non-empty string"
                )
        intent = args["intent"]
        if len(intent) > MAX_INTENT_LEN:
            raise ToolValidationError(
                f"intent exceeds max {MAX_INTENT_LEN} chars; got {len(intent)}"
            )
        reason = args["reason"]
        if len(reason) > MAX_REASON_LEN:
            raise ToolValidationError(
                f"reason exceeds max {MAX_REASON_LEN} chars; got {len(reason)}"
            )
        confidence = args.get("confidence", 1.0)
        if not isinstance(confidence, (int, float)) or confidence < 0 or confidence > 1:
            raise ToolValidationError(
                f"confidence must be in [0, 1]; got {confidence!r}"
            )
        inputs = args.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            raise ToolValidationError(
                "inputs must be an object when provided"
            )
        decomposition_seq = args.get("decomposition_seq")
        if decomposition_seq is not None and not isinstance(decomposition_seq, int):
            raise ToolValidationError(
                "decomposition_seq must be an int when provided"
            )
        allow_planned = args.get("allow_planned")
        if allow_planned is not None and not isinstance(allow_planned, bool):
            raise ToolValidationError(
                "allow_planned must be a boolean when provided"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        # Load the domain registry to validate the target_domain.
        from forest_soul_forge.core.domain_registry import (
            DomainRegistryError,
            load_domain_registry,
        )
        try:
            registry, _registry_errors = load_domain_registry()
        except DomainRegistryError as e:
            raise ToolValidationError(
                f"route_to_domain.v1 requires loadable domain registry: {e}"
            ) from e

        target_domain_id = args["target_domain"]
        domain = registry.by_id(target_domain_id)
        if domain is None:
            raise ToolValidationError(
                f"target_domain {target_domain_id!r} not in registry. "
                f"Valid domain_ids: {sorted(registry.domain_ids())}"
            )

        allow_planned = bool(args.get("allow_planned") or False)
        if not domain.is_dispatchable and not allow_planned:
            raise ToolValidationError(
                f"target_domain {target_domain_id!r} has status="
                f"{domain.status!r}; routing refused. Birth the entry "
                f"agents first or pass allow_planned=true to override "
                f"(override is recorded in the audit chain)."
            )

        # Validate that the capability is declared somewhere in this
        # domain (either top-level or via an entry agent). Loose
        # check — we don't refuse on capability mismatch (operator
        # may have a custom capability), but the audit event records
        # whether the capability was registry-known.
        target_capability = args["target_capability"]
        known_capabilities = set(domain.capabilities)
        for ea in domain.entry_agents:
            known_capabilities.add(ea.capability)
        capability_known = target_capability in known_capabilities

        intent_text: str = args["intent"]
        intent_hash = _hash_intent(intent_text)
        confidence = float(args.get("confidence", 1.0))
        decomposition_seq = args.get("decomposition_seq")
        reason = args["reason"]

        # 1. Emit domain_routed BEFORE the delegate fires.
        # Captures the orchestrator's intent independent of the
        # downstream delegate outcome.
        domain_routed_payload = {
            "target_domain": target_domain_id,
            "target_capability": target_capability,
            "target_instance_id": args["target_instance_id"],
            "intent_hash": intent_hash,
            "confidence": confidence,
            "decomposition_seq": decomposition_seq,
            "reason": reason,
            "capability_known_in_registry": capability_known,
            "domain_status_at_route": domain.status,
            "allow_planned_override": allow_planned,
        }
        _emit_domain_routed_event(ctx, domain_routed_payload)

        # 2. Fire delegate.v1. If the delegator isn't wired (test
        # context or degraded daemon), surface a clean error rather
        # than crashing.
        if ctx.delegate is None:
            raise ToolValidationError(
                "route_to_domain.v1: no delegator wired on the "
                "dispatcher. Cross-domain routing requires "
                "ctx.delegate to be set."
            )

        # Default inputs: just forward the intent text so the target
        # skill has something to consume. Callers can override.
        skill_inputs = args.get("inputs") or {"intent": intent_text}

        t0 = time.perf_counter()
        try:
            outcome = await ctx.delegate(
                target_instance_id=args["target_instance_id"],
                skill_name=args["skill_name"],
                skill_version=args["skill_version"],
                inputs=skill_inputs,
                reason=reason,
                session_id=args.get("session_id"),
                allow_out_of_lineage=bool(
                    args.get("allow_out_of_lineage") or False,
                ),
            )
        except DelegateError as e:
            raise ToolValidationError(
                f"delegate refused mid-route: {e}"
            ) from e
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # Marshal outcome — same shape contract as DelegateTool.
        # Keeps the audit chain symmetric with direct delegate calls.
        outcome_dict = _outcome_to_dict(outcome)

        result_output = {
            "target_domain": target_domain_id,
            "target_capability": target_capability,
            "target_instance_id": args["target_instance_id"],
            "decomposition_seq": decomposition_seq,
            "status": outcome_dict.get("status", "unknown"),
            "delegate_output": outcome_dict,
            "elapsed_ms": elapsed_ms,
        }

        return ToolResult(
            success=outcome_dict.get("status") == "succeeded",
            output=result_output,
            audit_payload={
                "target_domain": target_domain_id,
                "target_capability": target_capability,
                "target_instance_id": args["target_instance_id"],
                "intent_hash": intent_hash,
                "confidence": confidence,
                "delegate_status": outcome_dict.get("status"),
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_intent(intent: str) -> str:
    """16-char sha256 prefix. PII-safe audit-chain identifier for
    the intent text — auditors can confirm "was this MY intent"
    via cross-reference without the chain storing raw operator
    words."""
    return hashlib.sha256(intent.encode("utf-8")).hexdigest()[:16]


def _emit_domain_routed_event(ctx: ToolContext, payload: dict) -> None:
    """Append a domain_routed entry to the audit chain via the
    dispatcher's audit handle.

    Defensive: if ctx doesn't carry an audit handle (test context)
    we skip the event silently — the downstream delegate.v1 will
    still emit agent_delegated, so the routing isn't invisible. In
    production daemons, ctx.audit is always wired."""
    audit = getattr(ctx, "audit", None)
    if audit is None:
        return
    try:
        audit.append(
            "domain_routed",
            payload,
            agent_dna=getattr(ctx, "caller_dna", None) or "orchestrator",
        )
    except Exception:
        # Audit failures are non-fatal here — the delegate call
        # still emits its own audit event. We don't want a quirk
        # in the audit append to take down the routing call.
        pass


def _outcome_to_dict(outcome: Any) -> dict:
    """Duck-typed marshal of the delegator's outcome.

    Mirrors DelegateTool.execute's pattern: don't import the
    skill-runtime class hierarchy here; use attribute presence to
    classify the outcome shape (succeeded / failed / refused).
    """
    if hasattr(outcome, "status"):
        d = {"status": getattr(outcome, "status")}
        for attr in (
            "target_instance_id", "skill_name", "skill_version",
            "invoked_seq", "completed_seq", "output",
            "failed_step_id", "failure_reason",
        ):
            v = getattr(outcome, attr, None)
            if v is not None:
                d[attr] = v
        return d
    if isinstance(outcome, dict):
        return dict(outcome)
    return {"status": "unknown", "raw": str(outcome)}
