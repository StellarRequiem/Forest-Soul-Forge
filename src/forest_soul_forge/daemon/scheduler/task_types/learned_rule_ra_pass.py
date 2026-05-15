"""``learned_rule_ra_pass`` task type — ADR-0072 T3 (B325).

Scheduler runner that loads ``learned_rules.yaml``, runs the
Reality-Anchor pass over pending entries, and writes the result
back. The pure-function policy lives in
``core/learned_rule_ra_pass.py``; this module is the daemon-side
wrapper.

Config shape (one entry from ``scheduled_tasks.yaml``)::

    - id: learned_rules_ra_pass_nightly
      description: "Reality-Anchor pass over pending learned rules"
      schedule: "every 24h"
      enabled: true
      type: learned_rule_ra_pass
      config:
        learned_rules_path: null   # optional override
        catalog_path:       null   # optional ground_truth path

The runner emits per-pass audit events:

  - ``learned_rule_activated`` for each promoted rule
  - ``learned_rule_refused``   for each contradicted rule
  - one diagnostic ``behavior_change`` for the whole-config diff

Verifier-error rules are kept pending without status change so
a later pass can re-evaluate when the underlying issue clears.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from forest_soul_forge.core.behavior_provenance import (
    LearnedRulesConfig,
    load_learned_rules,
    save_learned_rules,
)
from forest_soul_forge.core.ground_truth import load_ground_truth
from forest_soul_forge.core.learned_rule_ra_pass import (
    RAPassResult,
    run_ra_pass,
)


logger = logging.getLogger(__name__)


async def learned_rule_ra_pass_runner(
    config: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Scheduler entry point. See module docstring for config keys.

    Returns ``{"ok": True, ...counts}`` on success — even when
    individual rules failed verification (those are normal
    outcomes, not runner failures). Returns ``{"ok": False,
    "error": "..."}`` only on hard failure (yaml unreadable,
    audit chain unavailable).
    """
    learned_rules_path = config.get("learned_rules_path")
    catalog_path = config.get("catalog_path")
    audit_chain = context.get("audit_chain")

    rules_path = Path(learned_rules_path) if learned_rules_path else None
    cat_path = Path(catalog_path) if catalog_path else None

    try:
        rules_config, load_errors = load_learned_rules(rules_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"load_learned_rules: {e}"}
    if load_errors:
        logger.info(
            "learned_rule_ra_pass: load surfaced %d soft error(s)",
            len(load_errors),
        )

    # Build the verifier closure. We mirror what verify_claim.v1
    # does internally but skip the tool-runtime envelope so the
    # scheduler doesn't need a full dispatcher to do its job.
    # Using the same ground_truth catalog as the live tool keeps
    # the verdicts aligned.
    try:
        facts, catalog_errors = load_ground_truth(path=cat_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"load_ground_truth: {e}"}
    if catalog_errors:
        logger.info(
            "learned_rule_ra_pass: catalog surfaced %d error(s)",
            len(catalog_errors),
        )

    def _verifier(claim: str) -> dict:
        # Lazy import — _evaluate_fact lives inside verify_claim
        # to avoid a circular at module-load time.
        from forest_soul_forge.tools.builtin.verify_claim import (
            _evaluate_fact,
        )
        from forest_soul_forge.tools.builtin.verify_claim import (
            VERDICT_CONFIRMED, VERDICT_CONTRADICTED, VERDICT_UNKNOWN,
            VERDICT_NOT_IN_SCOPE, _SEVERITY_RANK,
        )
        claim_norm = claim.strip().lower()
        per_fact = []
        for fact in facts:
            verdict, matched, domain_hits = _evaluate_fact(claim_norm, fact)
            if verdict is None:
                continue
            per_fact.append({
                "fact_id":   fact.id,
                "verdict":   verdict,
                "severity":  fact.severity,
                "statement": fact.statement,
                "matched_terms": matched,
                "domain_match":  domain_hits,
            })
        # Aggregate.
        if not per_fact:
            return {
                "claim": claim, "verdict": VERDICT_NOT_IN_SCOPE,
                "highest_severity": None, "by_fact": [],
            }
        contradictions = [f for f in per_fact if f["verdict"] == VERDICT_CONTRADICTED]
        if contradictions:
            severity = max(
                contradictions,
                key=lambda f: _SEVERITY_RANK.get(f["severity"], 0),
            )["severity"]
            return {
                "claim": claim, "verdict": VERDICT_CONTRADICTED,
                "highest_severity": severity, "by_fact": per_fact,
            }
        if any(f["verdict"] == VERDICT_CONFIRMED for f in per_fact):
            return {
                "claim": claim, "verdict": VERDICT_CONFIRMED,
                "highest_severity": None, "by_fact": per_fact,
            }
        return {
            "claim": claim, "verdict": VERDICT_UNKNOWN,
            "highest_severity": None, "by_fact": per_fact,
        }

    pass_result: RAPassResult = run_ra_pass(rules_config, _verifier)

    # Persist if anything changed. Avoid touching the file on a
    # no-op pass so the operator's filesystem mtime stays clean.
    if (
        pass_result.promoted_count
        or pass_result.refused_count
        or _has_status_drift(rules_config, pass_result.new_config)
    ):
        try:
            save_learned_rules(pass_result.new_config, rules_path)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"save_learned_rules: {e}"}

    # Emit per-rule audit events for the promotions + refusals.
    if audit_chain is not None:
        for outcome in pass_result.outcomes:
            if outcome.action == "promoted":
                try:
                    audit_chain.append("learned_rule_activated", {
                        "rule_id":  outcome.rule_id,
                        "verdict":  outcome.verdict,
                        "reason":   outcome.reason,
                    }, agent_dna=None)
                except Exception:
                    logger.exception(
                        "learned_rule_ra_pass: audit emit failed for "
                        "promoted rule_id=%s", outcome.rule_id,
                    )
            elif outcome.action == "refused":
                try:
                    audit_chain.append("learned_rule_refused", {
                        "rule_id":  outcome.rule_id,
                        "verdict":  outcome.verdict,
                        "severity": outcome.severity,
                        "reason":   outcome.reason,
                    }, agent_dna=None)
                except Exception:
                    logger.exception(
                        "learned_rule_ra_pass: audit emit failed for "
                        "refused rule_id=%s", outcome.rule_id,
                    )

    return {
        "ok":              True,
        "promoted":        pass_result.promoted_count,
        "refused":         pass_result.refused_count,
        "still_pending":   pass_result.still_pending_count,
        "verifier_errors": pass_result.verifier_error_count,
        "started_at":      pass_result.started_at,
        "finished_at":     pass_result.finished_at,
    }


def _has_status_drift(
    before: LearnedRulesConfig,
    after: LearnedRulesConfig,
) -> bool:
    """True if any rule changed status across the pass (covers
    the refused-rules-stay-in-pending case where the buckets
    don't shrink/grow but the rule's status field did flip)."""
    before_by_id = {r.id: r for r in before.pending_activation}
    after_by_id = {r.id: r for r in after.pending_activation}
    for rid, b in before_by_id.items():
        a = after_by_id.get(rid)
        if a is None:
            return True
        if b.status != a.status:
            return True
    return False
