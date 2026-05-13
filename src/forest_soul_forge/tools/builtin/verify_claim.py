"""``verify_claim.v1`` — ADR-0063 Reality Anchor verifier.

Pattern-match a claim against the operator-asserted ground
truth catalog. Returns a structured verdict the caller
(governance pipeline step, agent delegation, frontend) uses
to refuse, warn, or pass.

## Verdict matrix (ADR-0063 D5)

Per fact whose ``domain_keywords`` overlap the claim:

  - Claim contains a ``canonical_term``        → ``confirmed``
  - Claim contains a ``forbidden_term``        → ``contradicted``
    AND NO canonical term
  - Claim is in domain but matches neither    → ``unknown``
  - Claim doesn't mention any domain keyword  → fact is SKIPPED

Aggregate verdict for the call:

  - Any fact returned ``contradicted``         → ``contradicted``
    (highest severity wins; carries the fact's severity)
  - Else any fact returned ``confirmed``       → ``confirmed``
  - Else any fact returned ``unknown``         → ``unknown``
    (a domain matched but neither confirm nor contradict)
  - Else                                       → ``not_in_scope``
    (no fact had a matching domain — the claim doesn't touch
    any ground truth; safe to pass)

## Why pattern matching, not LLM

v1 keeps the verifier lightweight (< 1ms per call against
~14 facts) so it can run in the governance pipeline on EVERY
gated dispatch without latency cost. ADR-0063 D5 reserves
LLM-grade verification for a v2 deep pass that fires only
when the operator opts in via strict mode.

side_effects=read_only — the tool reads the catalog +
matches; never writes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from forest_soul_forge.core.ground_truth import (
    Fact,
    load_ground_truth,
    merge_agent_additions,
)
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


# ---- verdict constants ---------------------------------------------------

VERDICT_CONFIRMED = "confirmed"
VERDICT_CONTRADICTED = "contradicted"
VERDICT_UNKNOWN = "unknown"
VERDICT_NOT_IN_SCOPE = "not_in_scope"

VALID_VERDICTS = (
    VERDICT_CONFIRMED, VERDICT_CONTRADICTED,
    VERDICT_UNKNOWN, VERDICT_NOT_IN_SCOPE,
)

#: Severity hierarchy for aggregating contradiction findings.
#: Higher index = more severe.
_SEVERITY_RANK = {
    "INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4,
}


# ---- the tool -----------------------------------------------------------


class VerifyClaimTool:
    """Pattern-match a claim against the operator's ground
    truth catalog (ADR-0063).

    Args:
      claim (str, required): the claim text to verify. Lowered
        + tokenized before matching. Empty / whitespace-only
        claim returns verdict='not_in_scope' (nothing to check).
      fact_ids (list[str], optional): restrict matching to a
        subset of fact ids. Useful when an agent wants to
        check ONE specific fact ("is the license still ELv2?")
        without running the full catalog.
      agent_constitution (dict, optional): the calling agent's
        constitution dict; if present, per-agent
        ground_truth_additions are layered on (ADR-0063 D3).
      catalog_path (str, optional): override the default
        catalog path. Tests use this; operators normally don't.

    Output:
      {
        "claim":            str,                # echoed
        "verdict":          str,                # aggregate verdict
        "highest_severity": str | null,         # of contradicting facts
        "by_fact":          [{
            "fact_id":          str,
            "verdict":          str,
            "severity":         str,
            "statement":        str,
            "matched_terms":    [str, ...],     # canonical OR forbidden
            "domain_match":     [str, ...],     # domain_keywords that hit
        }, ...],
        "catalog_errors":   [str, ...],
        "facts_evaluated":  int,
      }
    """

    name = "verify_claim"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        claim = args.get("claim")
        if not isinstance(claim, str):
            raise ToolValidationError("claim must be a string")
        fact_ids = args.get("fact_ids")
        if fact_ids is not None:
            if not isinstance(fact_ids, list):
                raise ToolValidationError(
                    "fact_ids must be a list of strings",
                )
            if not all(isinstance(f, str) and f for f in fact_ids):
                raise ToolValidationError(
                    "fact_ids entries must be non-empty strings",
                )
        ac = args.get("agent_constitution")
        if ac is not None and not isinstance(ac, dict):
            raise ToolValidationError(
                "agent_constitution must be a dict if present",
            )
        cp = args.get("catalog_path")
        if cp is not None and not isinstance(cp, str):
            raise ToolValidationError(
                "catalog_path must be a string if present",
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        claim_raw: str = args["claim"]
        fact_ids_filter = args.get("fact_ids")
        agent_constitution = args.get("agent_constitution")
        catalog_path = (
            Path(args["catalog_path"])
            if args.get("catalog_path") else None
        )

        # Load + (optionally) layer per-agent ADD-only additions.
        facts, catalog_errors = load_ground_truth(path=catalog_path)
        if agent_constitution:
            agent_id = ctx.instance_id or "unknown"
            facts, merge_errors = merge_agent_additions(
                facts, agent_constitution, agent_instance_id=agent_id,
            )
            catalog_errors = catalog_errors + merge_errors

        if fact_ids_filter:
            allowed = set(fact_ids_filter)
            facts = [f for f in facts if f.id in allowed]

        # Trim + lowercase the claim once. The catalog already
        # lowered its keyword tuples (see ground_truth._parse_fact).
        claim = claim_raw.strip().lower()

        per_fact: list[dict] = []
        if not claim or not facts:
            return ToolResult(
                output={
                    "claim":            claim_raw,
                    "verdict":          VERDICT_NOT_IN_SCOPE,
                    "highest_severity": None,
                    "by_fact":          [],
                    "catalog_errors":   catalog_errors,
                    "facts_evaluated":  0,
                },
                metadata={"reason": "empty claim or empty catalog"},
                tokens_used=None,
                cost_usd=None,
                side_effect_summary="verify_claim: not_in_scope (no input)",
            )

        for fact in facts:
            verdict, matched, domain_hits = _evaluate_fact(claim, fact)
            if verdict is None:  # not in domain — skip
                continue
            per_fact.append({
                "fact_id":       fact.id,
                "verdict":       verdict,
                "severity":      fact.severity,
                "statement":     fact.statement,
                "matched_terms": matched,
                "domain_match":  domain_hits,
            })

        # Aggregate verdict.
        contradictions = [r for r in per_fact if r["verdict"] == VERDICT_CONTRADICTED]
        confirms = [r for r in per_fact if r["verdict"] == VERDICT_CONFIRMED]
        unknowns = [r for r in per_fact if r["verdict"] == VERDICT_UNKNOWN]

        if contradictions:
            verdict = VERDICT_CONTRADICTED
            highest_sev = max(
                (r["severity"] for r in contradictions),
                key=lambda s: _SEVERITY_RANK.get(s, -1),
            )
        elif confirms:
            verdict = VERDICT_CONFIRMED
            highest_sev = None
        elif unknowns:
            verdict = VERDICT_UNKNOWN
            highest_sev = None
        else:
            verdict = VERDICT_NOT_IN_SCOPE
            highest_sev = None

        summary = (
            f"verify_claim: {verdict}; "
            f"evaluated {len(facts)} fact(s), "
            f"{len(per_fact)} in scope"
            + (f"; highest_severity={highest_sev}" if highest_sev else "")
        )

        return ToolResult(
            output={
                "claim":            claim_raw,
                "verdict":          verdict,
                "highest_severity": highest_sev,
                "by_fact":          per_fact,
                "catalog_errors":   catalog_errors,
                "facts_evaluated":  len(facts),
            },
            metadata={
                "catalog_path": str(catalog_path) if catalog_path else None,
                "contradiction_count": len(contradictions),
                "confirm_count":       len(confirms),
                "unknown_count":       len(unknowns),
            },
            tokens_used=None,
            cost_usd=None,
            side_effect_summary=summary,
        )


# ---- internals -----------------------------------------------------------


def _evaluate_fact(
    claim_lower: str, fact: Fact,
) -> tuple[str | None, list[str], list[str]]:
    """Score one fact against one claim.

    Returns ``(verdict, matched_terms, domain_hits)``:
      * ``verdict`` is None when the claim isn't in this fact's
        domain (caller skips the fact entirely)
      * ``matched_terms`` is the canonical or forbidden terms
        that hit (used for the per-fact citation in the
        output)
      * ``domain_hits`` is the domain keywords that matched
        (so an operator debugging "why did this fire?" sees
        the trigger)

    Matching is whole-substring lowercase. We deliberately use
    plain ``in`` rather than word-boundary regex: ground-truth
    keywords are operator-curated phrases like ``"audit chain
    path"`` or ``"sqlite version"`` that aren't always whole-
    word-bounded. Operators who need stricter matching add
    word-boundary characters to their canonical_terms.
    """
    domain_hits = [k for k in fact.domain_keywords if k in claim_lower]
    if not domain_hits:
        return None, [], []

    canonical_hits = [t for t in fact.canonical_terms if t in claim_lower]
    if canonical_hits:
        return VERDICT_CONFIRMED, canonical_hits, domain_hits

    forbidden_hits = [t for t in fact.forbidden_terms if t in claim_lower]
    if forbidden_hits:
        return VERDICT_CONTRADICTED, forbidden_hits, domain_hits

    return VERDICT_UNKNOWN, [], domain_hits
