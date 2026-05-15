"""Reality-Anchor pass over pending learned rules — ADR-0072 T3 (B325).

The substrate piece that walks ``learned_rules.yaml``'s
``pending_activation`` bucket and decides for each pending rule
whether to promote it to ``active``, refuse it as
``refused``, or leave it pending (low-confidence verdict that
needs operator review).

## Verdict policy

For each pending rule, run ``verify_claim.v1``-style pattern
matching against the operator's ground-truth catalog. The
aggregate verdict drives the action:

  - **confirmed**     → promote to ``active`` (RA endorses)
  - **not_in_scope**  → promote to ``active`` (no conflict)
  - **contradicted**  → refuse with the RA verdict + reason
  - **unknown**       → leave pending (domain matched but the
                        rule didn't carry the canonical OR
                        forbidden terms; operator should review)

## Why split runner from scheduler task type

This module is pure-function: takes a LearnedRulesConfig + a
verifier callable, returns a new LearnedRulesConfig + the
per-rule outcome list. The scheduler task type wraps that with
yaml load/save + audit emit + context resolution. Splitting
keeps the policy logic testable without spinning up a daemon
or the full scheduler.

## Failure posture

Verifier raises → that rule is treated as ``unknown`` and stays
pending. The pass continues with the next rule. A pure-function
pass that crashes on one bad rule defeats the whole purpose.
The exception is captured in the outcome so the scheduler can
emit a diagnostic.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable

from forest_soul_forge.core.behavior_provenance import (
    LearnedRule,
    LearnedRulesConfig,
)


# Callable shape the runner expects. The real verifier is
# ``verify_claim.v1``; tests pass a stub.
#
# Returns a dict with at least ``verdict`` and optionally
# ``highest_severity`` + ``by_fact``. The runner only reads
# ``verdict`` + a one-line reason.
RAVerifier = Callable[[str], dict]


VERDICT_CONFIRMED = "confirmed"
VERDICT_CONTRADICTED = "contradicted"
VERDICT_UNKNOWN = "unknown"
VERDICT_NOT_IN_SCOPE = "not_in_scope"

# Decision matrix per ADR-0072 D2 / Decision 3 — keys are RA
# verdict strings; values are the resulting LearnedRule.status.
_PROMOTION_MATRIX: dict[str, str] = {
    VERDICT_CONFIRMED:     "active",
    VERDICT_NOT_IN_SCOPE:  "active",
    VERDICT_CONTRADICTED:  "refused",
    # Unknown stays pending — explicit absence from this map
    # surfaces in code as the "default branch".
}


@dataclass(frozen=True)
class RuleOutcome:
    """One per-rule verdict + the action taken."""
    rule_id: str
    verdict: str               # the RA verdict string
    action: str                # "promoted" | "refused" | "still_pending" | "verifier_error"
    reason: str                # human-readable one-liner
    severity: str | None = None


@dataclass(frozen=True)
class RAPassResult:
    """Aggregate output of one full pass."""
    new_config: LearnedRulesConfig
    outcomes: tuple[RuleOutcome, ...]
    started_at: str
    finished_at: str

    @property
    def promoted_count(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "promoted")

    @property
    def refused_count(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "refused")

    @property
    def still_pending_count(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "still_pending")

    @property
    def verifier_error_count(self) -> int:
        return sum(1 for o in self.outcomes if o.action == "verifier_error")


def run_ra_pass(
    config: LearnedRulesConfig,
    verifier: RAVerifier,
) -> RAPassResult:
    """Walk ``config.pending_activation``, decide for each rule.

    Returns a new LearnedRulesConfig with promoted rules moved
    from pending → active and refused rules updated in place
    with status='refused' + verdict/reason attached. Rules
    that remain pending are left in pending_activation.

    The original ``config`` is not mutated (LearnedRulesConfig
    + LearnedRule are frozen dataclasses; we build a new one).
    """
    started_at = _now_iso()
    outcomes: list[RuleOutcome] = []

    new_active: list[LearnedRule] = list(config.active)
    new_pending: list[LearnedRule] = []

    for rule in config.pending_activation:
        try:
            verdict_result = verifier(rule.statement)
        except Exception as e:  # noqa: BLE001 — verifier is user code
            outcomes.append(RuleOutcome(
                rule_id=rule.id,
                verdict="error",
                action="verifier_error",
                reason=f"{type(e).__name__}: {e}",
            ))
            # Verifier crashed for this rule. Keep it pending so a
            # later pass (after the operator fixes whatever broke
            # the verifier) can re-evaluate.
            new_pending.append(rule)
            continue

        verdict = verdict_result.get("verdict", VERDICT_UNKNOWN)
        severity = verdict_result.get("highest_severity")

        if verdict == VERDICT_UNKNOWN:
            # Operator-review territory. Keep pending; don't touch
            # verdict fields on the rule so a re-pass sees the
            # same "needs review" state.
            outcomes.append(RuleOutcome(
                rule_id=rule.id,
                verdict=VERDICT_UNKNOWN,
                action="still_pending",
                reason="RA returned 'unknown' — operator review required",
            ))
            new_pending.append(rule)
            continue

        target_status = _PROMOTION_MATRIX.get(verdict)
        if target_status == "active":
            new_active.append(replace(
                rule,
                status="active",
                verification_verdict=verdict,
                verification_reason=_reason_text(verdict_result),
            ))
            outcomes.append(RuleOutcome(
                rule_id=rule.id,
                verdict=verdict,
                action="promoted",
                reason=_reason_text(verdict_result),
                severity=severity,
            ))
        elif target_status == "refused":
            # Stays in pending_activation with status updated so
            # the operator can see what happened on the next CLI
            # / UI inspection. T3 deliberately doesn't drop
            # refused rules from disk — the operator may want to
            # see the audit trail of what got rejected and why.
            new_pending.append(replace(
                rule,
                status="refused",
                verification_verdict=verdict,
                verification_reason=_reason_text(verdict_result),
            ))
            outcomes.append(RuleOutcome(
                rule_id=rule.id,
                verdict=verdict,
                action="refused",
                reason=_reason_text(verdict_result),
                severity=severity,
            ))
        else:
            # Unknown verdict shape — should not happen with the
            # canonical verifier but defensive against future
            # verdict additions.
            outcomes.append(RuleOutcome(
                rule_id=rule.id,
                verdict=verdict,
                action="still_pending",
                reason=f"unrecognized verdict {verdict!r}; keeping pending",
            ))
            new_pending.append(rule)

    finished_at = _now_iso()
    new_config = LearnedRulesConfig(
        schema_version=config.schema_version,
        pending_activation=tuple(new_pending),
        active=tuple(new_active),
    )
    return RAPassResult(
        new_config=new_config,
        outcomes=tuple(outcomes),
        started_at=started_at,
        finished_at=finished_at,
    )


def _reason_text(verdict_result: dict) -> str:
    """Compact one-liner reason for the RuleOutcome + saved
    LearnedRule. Pulls the first by_fact entry's statement when
    one is present; falls back to the bare verdict otherwise."""
    by_fact = verdict_result.get("by_fact") or []
    if by_fact:
        f0 = by_fact[0]
        return (
            f"RA verdict={verdict_result.get('verdict')} "
            f"via fact={f0.get('fact_id')!r}: "
            f"{f0.get('statement', '')[:140]}"
        )
    return f"RA verdict={verdict_result.get('verdict')}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
