"""``transaction_categorize.v1`` — ADR-0092 Phase B transaction categorizer.

Deterministic rule-based categorization over an operator-supplied
transaction batch. Each transaction matches at most one operator
rule (first match wins); unmatched transactions fall to
``uncategorized`` so the operator can refine the rule corpus over
time.

Read-only. The ``transaction_monitoring.v1`` skill wraps this tool
with memory_recall (recent transaction batches + prior rule
corpora) + memory_write (the categorization attestation);
LLM-driven anomaly narrative is layered separately.

## Rule model

A category rule is an operator-defined matcher with three
optional predicates (ALL active predicates must match):

- ``merchant_contains`` (list[str]): case-insensitive substring
  match against the transaction's merchant field. Any element in
  the list matches.
- ``description_contains`` (list[str]): case-insensitive substring
  match against the transaction's description field. Any element
  matches.
- ``amount_min`` / ``amount_max`` (number, optional): inclusive
  amount window. When only ``amount_min`` is set, the rule
  matches any transaction at or above the floor; similarly
  ``amount_max``.

Rules are evaluated in operator-supplied order; first match wins.
This is the load-bearing determinism contract — operators reorder
rules to disambiguate (e.g. "trader_joe's > $300 → 'big-shop'"
must precede "trader_joe's → 'groceries'").

When no rule matches, the verdict is ``uncategorized`` with a
``no_match`` rationale.

Same shape as D8's ``framework_check.v1`` — operator-supplied
rule corpus + deterministic apply.

side_effects=read_only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_TRANSACTIONS = 500
_MAX_RULES = 200
_MAX_SLUG_LEN = 200
_MAX_FIELD_LEN = 500
_MAX_PREDICATE_ITEMS = 50


class TransactionCategorizeTool:
    """Categorize transactions against an operator-supplied rule set.

    Args:
      batch_slug (str, required): identifier this categorization
        batch binds to (e.g. ``fiscal-month-2026-05``). Recorded
        in the output for the wrapping skill's attestation.
      transactions (list[dict], required): per-transaction record.
        Each entry:

          - ``txn_id`` (str, required): unique transaction id.
          - ``merchant`` (str, optional): merchant name.
          - ``description`` (str, optional): bank-supplied
            description string.
          - ``amount`` (number, required): transaction amount.
            Sign convention is operator-defined (positive =
            expense by default).
          - ``currency`` (str, optional): ISO-4217 code for
            audit clarity.
      rules (list[dict], required): operator-supplied category
        rules in apply-order. Each entry:

          - ``category`` (str, required): category slug to
            assign on match.
          - ``merchant_contains`` (list[str], optional)
          - ``description_contains`` (list[str], optional)
          - ``amount_min`` (number, optional)
          - ``amount_max`` (number, optional)

    Output:
      {
        "generated_at":     str (ISO),
        "batch_slug":       str,
        "rule_count":       int,
        "verdicts": [{
          "txn_id":            str,
          "merchant":          str | null,
          "description":       str | null,
          "amount":            float,
          "currency":          str | null,
          "category":          str,
          "matched_rule_idx":  int | null,
          "rationale":         str,
        }, ...],
        "summary": {
          "transaction_count":    int,
          "categorized_count":    int,
          "uncategorized_count":  int,
          "per_category": {category_slug: int, ...},
        },
      }
    """

    name = "transaction_categorize"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        slug = args.get("batch_slug")
        if not isinstance(slug, str) or not slug.strip():
            raise ToolValidationError(
                "batch_slug must be a non-empty string"
            )
        if len(slug) > _MAX_SLUG_LEN:
            raise ToolValidationError(
                f"batch_slug must be <= {_MAX_SLUG_LEN} chars"
            )

        txns = args.get("transactions")
        if not isinstance(txns, list):
            raise ToolValidationError("transactions must be a list")
        if not txns:
            raise ToolValidationError(
                "transactions must contain at least one entry"
            )
        if len(txns) > _MAX_TRANSACTIONS:
            raise ToolValidationError(
                f"transactions must have <= {_MAX_TRANSACTIONS} entries; "
                f"got {len(txns)}"
            )

        seen: set[str] = set()
        for i, entry in enumerate(txns):
            if not isinstance(entry, dict):
                raise ToolValidationError(
                    f"transactions[{i}] must be a dict"
                )
            tid = entry.get("txn_id")
            if not isinstance(tid, str) or not tid.strip():
                raise ToolValidationError(
                    f"transactions[{i}].txn_id must be a non-empty string"
                )
            if len(tid) > _MAX_SLUG_LEN:
                raise ToolValidationError(
                    f"transactions[{i}].txn_id must be <= {_MAX_SLUG_LEN} chars"
                )
            if tid in seen:
                raise ToolValidationError(
                    f"transactions[{i}].txn_id duplicates earlier entry: {tid!r}"
                )
            seen.add(tid)
            for k in ("merchant", "description", "currency"):
                v = entry.get(k)
                if v is not None:
                    if not isinstance(v, str):
                        raise ToolValidationError(
                            f"transactions[{i}].{k} must be a string"
                        )
                    if len(v) > _MAX_FIELD_LEN:
                        raise ToolValidationError(
                            f"transactions[{i}].{k} must be <= {_MAX_FIELD_LEN} chars"
                        )
            amt = entry.get("amount")
            if not isinstance(amt, (int, float)) or isinstance(amt, bool):
                raise ToolValidationError(
                    f"transactions[{i}].amount must be a number"
                )

        rules = args.get("rules")
        if not isinstance(rules, list):
            raise ToolValidationError("rules must be a list")
        if len(rules) > _MAX_RULES:
            raise ToolValidationError(
                f"rules must have <= {_MAX_RULES} entries; got {len(rules)}"
            )
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise ToolValidationError(f"rules[{i}] must be a dict")
            cat = rule.get("category")
            if not isinstance(cat, str) or not cat.strip():
                raise ToolValidationError(
                    f"rules[{i}].category must be a non-empty string"
                )
            if len(cat) > _MAX_SLUG_LEN:
                raise ToolValidationError(
                    f"rules[{i}].category must be <= {_MAX_SLUG_LEN} chars"
                )
            has_pred = False
            for k in ("merchant_contains", "description_contains"):
                v = rule.get(k)
                if v is not None:
                    if not isinstance(v, list):
                        raise ToolValidationError(
                            f"rules[{i}].{k} must be a list"
                        )
                    if len(v) > _MAX_PREDICATE_ITEMS:
                        raise ToolValidationError(
                            f"rules[{i}].{k} must have <= "
                            f"{_MAX_PREDICATE_ITEMS} entries"
                        )
                    for j, item in enumerate(v):
                        if not isinstance(item, str) or not item.strip():
                            raise ToolValidationError(
                                f"rules[{i}].{k}[{j}] must be a non-empty string"
                            )
                    if v:
                        has_pred = True
            mn = rule.get("amount_min")
            mx = rule.get("amount_max")
            for k, val in (("amount_min", mn), ("amount_max", mx)):
                if val is not None:
                    if (
                        not isinstance(val, (int, float))
                        or isinstance(val, bool)
                    ):
                        raise ToolValidationError(
                            f"rules[{i}].{k} must be a number"
                        )
                    has_pred = True
            if mn is not None and mx is not None and mn > mx:
                raise ToolValidationError(
                    f"rules[{i}].amount_min must be <= amount_max"
                )
            if not has_pred:
                raise ToolValidationError(
                    f"rules[{i}] must define at least one predicate "
                    "(merchant_contains / description_contains / amount_min / amount_max)"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        slug = args["batch_slug"]
        txns = args["transactions"]
        rules = args["rules"]

        verdicts: list[dict[str, Any]] = []
        per_category: dict[str, int] = {}
        categorized_count = 0
        uncategorized_count = 0

        for entry in txns:
            txn_id = entry["txn_id"]
            merchant = entry.get("merchant")
            description = entry.get("description")
            amount = float(entry["amount"])
            currency = entry.get("currency")

            matched_idx: int | None = None
            matched_category = "uncategorized"
            rationale = "no_match: no rule matched the transaction."

            for idx, rule in enumerate(rules):
                if _rule_matches(rule, merchant, description, amount):
                    matched_idx = idx
                    matched_category = rule["category"]
                    rationale = _rationale(rule, merchant, description, amount)
                    break

            if matched_idx is not None:
                categorized_count += 1
            else:
                uncategorized_count += 1

            per_category[matched_category] = (
                per_category.get(matched_category, 0) + 1
            )

            verdicts.append({
                "txn_id":            txn_id,
                "merchant":          merchant,
                "description":       description,
                "amount":            round(amount, 4),
                "currency":          currency,
                "category":          matched_category,
                "matched_rule_idx":  matched_idx,
                "rationale":         rationale,
            })

        summary = {
            "transaction_count":   len(verdicts),
            "categorized_count":   categorized_count,
            "uncategorized_count": uncategorized_count,
            "per_category":        dict(sorted(per_category.items())),
        }

        body = {
            "generated_at":  datetime.now(timezone.utc)
                                     .replace(tzinfo=None)
                                     .isoformat(timespec="seconds")
                                     + "Z",
            "batch_slug":    slug,
            "rule_count":    len(rules),
            "verdicts":      verdicts,
            "summary":       summary,
        }

        return ToolResult(
            output=body,
            metadata={
                "batch_slug":           slug,
                "transaction_count":    summary["transaction_count"],
                "categorized_count":    summary["categorized_count"],
                "uncategorized_count":  summary["uncategorized_count"],
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"categorized {summary['categorized_count']}/"
                f"{summary['transaction_count']} transaction"
                f"{'s' if summary['transaction_count'] != 1 else ''} "
                f"across {len(summary['per_category'])} categor"
                f"{'ies' if len(summary['per_category']) != 1 else 'y'} "
                f"(uncategorized={summary['uncategorized_count']})"
            ),
        )


def _rule_matches(
    rule: dict[str, Any],
    merchant: str | None,
    description: str | None,
    amount: float,
) -> bool:
    mc = rule.get("merchant_contains") or []
    if mc:
        if not isinstance(merchant, str):
            return False
        ml = merchant.lower()
        if not any(s.lower() in ml for s in mc):
            return False
    dc = rule.get("description_contains") or []
    if dc:
        if not isinstance(description, str):
            return False
        dl = description.lower()
        if not any(s.lower() in dl for s in dc):
            return False
    mn = rule.get("amount_min")
    if mn is not None and amount < float(mn):
        return False
    mx = rule.get("amount_max")
    if mx is not None and amount > float(mx):
        return False
    return True


def _rationale(
    rule: dict[str, Any],
    merchant: str | None,
    description: str | None,
    amount: float,
) -> str:
    bits: list[str] = []
    mc = rule.get("merchant_contains") or []
    if mc:
        matched = next(
            (s for s in mc if isinstance(merchant, str) and s.lower() in merchant.lower()),
            None,
        )
        bits.append(f"merchant matched {matched!r}")
    dc = rule.get("description_contains") or []
    if dc:
        matched = next(
            (s for s in dc if isinstance(description, str) and s.lower() in description.lower()),
            None,
        )
        bits.append(f"description matched {matched!r}")
    mn = rule.get("amount_min")
    mx = rule.get("amount_max")
    if mn is not None and mx is not None:
        bits.append(f"amount {amount:.2f} in [{float(mn):.2f},{float(mx):.2f}]")
    elif mn is not None:
        bits.append(f"amount {amount:.2f} >= {float(mn):.2f}")
    elif mx is not None:
        bits.append(f"amount {amount:.2f} <= {float(mx):.2f}")
    return "rule matched: " + "; ".join(bits) if bits else "rule matched"
