"""Multi-domain orchestration coordinator — ADR-0067 T6 (B284).

The piece that turns an operator utterance like "remind me to call
Mom AND draft the Q3 update AND tell me what SOC saw overnight"
into a sequenced dispatch across multiple domains, with a clean
operator-readable summary of what dispatched + what surfaced for
ambiguity.

## Why this is a pure function, not a tool

Tools are leaf operations in Forest's runtime — they shouldn't
recursively dispatch other tools. The coordinator chains
decompose → resolve_route → route per sub-intent, which would be
a discipline violation if expressed as a single tool.

Instead T6 ships this as a pure function that the orchestrator
agent (T5) invokes from inside a skill manifest. The skill
manifest's step DAG runs decompose_intent.v1, then for-each over
the resulting sub-intents to fire route_to_domain.v1, with this
coordinator providing the sequencing + aggregation logic.

## Surface

  - :class:`OrchestrationOutcome` — frozen aggregate result
  - :func:`coordinate_dispatch(...)` — the main entry point

The coordinator takes callable hooks for the decompose + route
steps so tests can inject mocks; production callers pass closures
that hit the real tools via ctx.delegate.

## What the coordinator does

  1. Optionally call ``decompose_fn(utterance)`` to get sub-intents.
     (If pre-decomposed, the caller passes ``subintents=...`` and
     skips decomposition.)
  2. For each sub-intent: call ``resolve_route(subintent, registry,
     handoffs, agent_inventory)`` → ResolvedRoute or UnroutableSubIntent
  3. For each ResolvedRoute: call ``route_fn(route)`` which is
     expected to fire route_to_domain.v1 and return the dispatch
     result.
  4. For each successful ResolvedRoute: apply cascade_rules and
     route the cascades too.
  5. Aggregate: list of dispatched routes (initial + cascades) +
     list of unroutable sub-intents.
  6. Return an :class:`OrchestrationOutcome` summary the
     operator-facing UI can render.

## What this does NOT do

- Does NOT dispatch in parallel. Cross-domain handoffs are
  serialized for predictable audit chain ordering. Parallelization
  is a future ADR — for the typical 2-4 sub-intent operator
  utterance, serial dispatch is fast enough and the audit chain
  stays linearly readable.
- Does NOT retry failed dispatches. The route_fn closure decides
  retry policy; the coordinator just records the outcome.
- Does NOT surface back to the operator via conversation runtime
  directly. The caller (orchestrator agent skill) takes the
  OrchestrationOutcome and produces the conversational response
  using its own llm_think + voice.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from forest_soul_forge.core.routing_engine import (
    HandoffsConfig,
    ResolvedRoute,
    UnroutableSubIntent,
    apply_cascade_rules,
    resolve_route,
)


@dataclass(frozen=True)
class DispatchedRouteResult:
    """One successful dispatch outcome.

    Carries both the routing intent (which domain + capability +
    agent + skill) AND the downstream delegate outcome (success /
    failure / output). Aggregation surface for OrchestrationOutcome.
    """
    route: ResolvedRoute
    delegate_status: str  # "succeeded" | "failed" | "refused" | "unknown"
    delegate_output: dict
    error: Optional[str] = None


@dataclass(frozen=True)
class OrchestrationOutcome:
    """Operator-facing summary of what happened.

    Three buckets:
      - dispatched: list of successful routes (initial + cascades)
        with their delegate outcomes
      - failed_dispatches: routes whose delegate.v1 returned
        status=failed (could be retried per operator policy)
      - unroutable: sub-intents that couldn't resolve to a route
        (ambiguous / planned_domain / no_skill_mapping / etc.)
        Operator must clarify or expand registry before retrying.
    """
    utterance: str
    dispatched: tuple[DispatchedRouteResult, ...]
    failed_dispatches: tuple[DispatchedRouteResult, ...]
    unroutable: tuple[UnroutableSubIntent, ...]

    @property
    def needs_operator_attention(self) -> bool:
        """True iff the operator should see this outcome.

        Aggregates: failed dispatches + unroutable sub-intents.
        Pure successes (every sub-intent dispatched cleanly + every
        delegate succeeded) don't need operator attention — the
        result just flows back via the assistant chat.
        """
        return bool(self.failed_dispatches) or bool(self.unroutable)


# Type aliases for the callable hooks.
RouteFn = Callable[[ResolvedRoute], dict]
DecomposeFn = Callable[[str], list[dict]]


def coordinate_dispatch(
    utterance: str,
    *,
    subintents: list[dict] | None = None,
    decompose_fn: DecomposeFn | None = None,
    registry: Any,  # DomainRegistry
    handoffs: HandoffsConfig,
    agent_inventory: list[dict],
    route_fn: RouteFn,
) -> OrchestrationOutcome:
    """Coordinate dispatch across however many sub-intents the
    operator's utterance contains.

    Args:
      utterance: the operator's natural-language request.
      subintents: optional pre-decomposed sub-intent list (same
        shape as decompose_intent.v1 output). When supplied,
        decompose_fn is skipped. Lets callers cache decomposition
        across retries.
      decompose_fn: optional callable that takes the utterance and
        returns a list of sub-intent dicts. Required when subintents
        is None.
      registry: DomainRegistry from domain_registry.load_domain_registry.
      handoffs: HandoffsConfig from routing_engine.load_handoffs.
      agent_inventory: list of {instance_id, role, status} dicts
        for resolve_route.
      route_fn: callable taking a ResolvedRoute, returning a dict
        with keys {status, output, error}. Production callers wrap
        the route_to_domain.v1 tool invocation here.

    Returns:
      :class:`OrchestrationOutcome` with three buckets:
      dispatched / failed_dispatches / unroutable.
    """
    # Step 1: decomposition (or pre-supplied subintents).
    if subintents is None:
        if decompose_fn is None:
            raise ValueError(
                "coordinate_dispatch requires either subintents or "
                "decompose_fn"
            )
        subintents = decompose_fn(utterance)

    dispatched: list[DispatchedRouteResult] = []
    failed_dispatches: list[DispatchedRouteResult] = []
    unroutable: list[UnroutableSubIntent] = []

    # Step 2: per-subintent resolution + dispatch.
    for si in subintents:
        decision = resolve_route(
            si, registry, handoffs, agent_inventory,
        )
        if isinstance(decision, UnroutableSubIntent):
            unroutable.append(decision)
            continue

        # ResolvedRoute path: fire route_fn for the initial route.
        result = _safe_route(decision, route_fn)
        if result.delegate_status == "succeeded":
            dispatched.append(result)
            # Step 3: cascades fire only for successful initial routes.
            cascade_decisions = apply_cascade_rules(
                decision, handoffs, registry, agent_inventory,
            )
            for cascade in cascade_decisions:
                if isinstance(cascade, UnroutableSubIntent):
                    unroutable.append(cascade)
                    continue
                cascade_result = _safe_route(cascade, route_fn)
                if cascade_result.delegate_status == "succeeded":
                    dispatched.append(cascade_result)
                else:
                    failed_dispatches.append(cascade_result)
        else:
            failed_dispatches.append(result)

    return OrchestrationOutcome(
        utterance=utterance,
        dispatched=tuple(dispatched),
        failed_dispatches=tuple(failed_dispatches),
        unroutable=tuple(unroutable),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_route(
    route: ResolvedRoute, route_fn: RouteFn,
) -> DispatchedRouteResult:
    """Fire route_fn under try/except. Wraps any exception as a
    failed dispatch so coordinator never crashes mid-sequence.

    Operator gets the full report of what dispatched + what failed,
    rather than seeing a partial dispatch with a stack trace and
    not knowing which sub-intents actually fired.
    """
    try:
        outcome = route_fn(route)
    except Exception as e:  # noqa: BLE001 — coordinator never crashes
        return DispatchedRouteResult(
            route=route,
            delegate_status="failed",
            delegate_output={},
            error=f"{type(e).__name__}: {e}",
        )
    if not isinstance(outcome, dict):
        return DispatchedRouteResult(
            route=route,
            delegate_status="unknown",
            delegate_output={},
            error=f"route_fn returned non-dict: {type(outcome).__name__}",
        )
    return DispatchedRouteResult(
        route=route,
        delegate_status=str(outcome.get("status", "unknown")),
        delegate_output=outcome.get("output", {}) or {},
        error=outcome.get("error"),
    )


def render_operator_summary(
    outcome: OrchestrationOutcome,
) -> str:
    """Produce a Markdown summary the assistant can read aloud or
    paste into the chat. Doesn't need to be pretty — the orchestrator
    agent's llm_think pass typically rephrases this into the
    operator's voice context.

    Caller is responsible for whether to surface this or to feed it
    to a follow-up llm_think for natural-language rephrasing.
    """
    lines: list[str] = []
    lines.append(f"Operator utterance: {outcome.utterance}")
    lines.append("")
    if outcome.dispatched:
        lines.append(f"Dispatched ({len(outcome.dispatched)}):")
        for d in outcome.dispatched:
            cascade_marker = (
                " (cascade)" if d.route.is_cascade else ""
            )
            lines.append(
                f"  - {d.route.target_domain}/"
                f"{d.route.target_capability} → "
                f"{d.route.target_instance_id}{cascade_marker} "
                f"[{d.delegate_status}]"
            )
        lines.append("")
    if outcome.failed_dispatches:
        lines.append(f"Failed ({len(outcome.failed_dispatches)}):")
        for f in outcome.failed_dispatches:
            lines.append(
                f"  - {f.route.target_domain}/"
                f"{f.route.target_capability}: "
                f"{f.error or f.delegate_status}"
            )
        lines.append("")
    if outcome.unroutable:
        lines.append(
            f"Needs operator attention ({len(outcome.unroutable)}):"
        )
        for u in outcome.unroutable:
            lines.append(
                f"  - {u.intent!r} → "
                f"{u.code}: {u.detail}"
            )
        lines.append("")
    if not outcome.needs_operator_attention and outcome.dispatched:
        lines.append("All sub-intents dispatched cleanly.")
    return "\n".join(lines)
