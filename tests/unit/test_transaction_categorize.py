"""Tests for ADR-0092 Phase B — transaction_categorize.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.transaction_categorize import (
    TransactionCategorizeTool,
)


def _ctx():
    return ToolContext(
        instance_id="transaction_tracker_test",
        agent_dna="a" * 12,
        role="transaction_tracker",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(TransactionCategorizeTool().execute(args, _ctx()))


def _txn(tid, merchant="Trader Joe's", amount=42.0, description=None, currency="USD"):
    out = {"txn_id": tid, "merchant": merchant, "amount": amount, "currency": currency}
    if description is not None:
        out["description"] = description
    return out


def _rule_merchant(cat, *needles):
    return {"category": cat, "merchant_contains": list(needles)}


def _rule_amount(cat, *, mn=None, mx=None):
    out = {"category": cat}
    if mn is not None:
        out["amount_min"] = mn
    if mx is not None:
        out["amount_max"] = mx
    return out


class TestValidation:
    def test_batch_slug_required(self):
        with pytest.raises(ToolValidationError, match="batch_slug"):
            TransactionCategorizeTool().validate(
                {"transactions": [_txn("t1")], "rules": [_rule_merchant("g", "trader")]}
            )

    def test_batch_slug_must_be_string(self):
        with pytest.raises(ToolValidationError, match="batch_slug"):
            TransactionCategorizeTool().validate(
                {"batch_slug": 1, "transactions": [_txn("t1")], "rules": []}
            )

    def test_batch_slug_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="batch_slug"):
            TransactionCategorizeTool().validate(
                {"batch_slug": "   ", "transactions": [_txn("t1")], "rules": []}
            )

    def test_batch_slug_length_capped(self):
        with pytest.raises(ToolValidationError, match="<= 200"):
            TransactionCategorizeTool().validate(
                {"batch_slug": "x" * 201, "transactions": [_txn("t1")], "rules": []}
            )

    def test_transactions_required(self):
        with pytest.raises(ToolValidationError, match="transactions"):
            TransactionCategorizeTool().validate({"batch_slug": "b1", "rules": []})

    def test_transactions_must_be_list(self):
        with pytest.raises(ToolValidationError, match="transactions"):
            TransactionCategorizeTool().validate(
                {"batch_slug": "b1", "transactions": "no", "rules": []}
            )

    def test_transactions_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="at least one"):
            TransactionCategorizeTool().validate(
                {"batch_slug": "b1", "transactions": [], "rules": []}
            )

    def test_transactions_capped(self):
        big = [_txn(f"t{i}") for i in range(501)]
        with pytest.raises(ToolValidationError, match="500"):
            TransactionCategorizeTool().validate(
                {"batch_slug": "b1", "transactions": big, "rules": []}
            )

    def test_txn_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="must be a dict"):
            TransactionCategorizeTool().validate(
                {"batch_slug": "b1", "transactions": ["nope"], "rules": []}
            )

    def test_txn_id_required(self):
        with pytest.raises(ToolValidationError, match="txn_id"):
            TransactionCategorizeTool().validate(
                {"batch_slug": "b1", "transactions": [{"amount": 1.0}], "rules": []}
            )

    def test_txn_id_must_be_unique(self):
        with pytest.raises(ToolValidationError, match="duplicates"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1"), _txn("t1")],
                "rules": [],
            })

    def test_txn_amount_required(self):
        with pytest.raises(ToolValidationError, match="amount"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [{"txn_id": "t1"}],
                "rules": [],
            })

    def test_txn_amount_must_be_number(self):
        with pytest.raises(ToolValidationError, match="amount"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [{"txn_id": "t1", "amount": "lots"}],
                "rules": [],
            })

    def test_txn_amount_rejects_bool(self):
        with pytest.raises(ToolValidationError, match="amount"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [{"txn_id": "t1", "amount": True}],
                "rules": [],
            })

    def test_txn_merchant_must_be_string(self):
        with pytest.raises(ToolValidationError, match="merchant"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [{"txn_id": "t1", "amount": 1.0, "merchant": 5}],
                "rules": [],
            })

    def test_txn_field_length_capped(self):
        with pytest.raises(ToolValidationError, match="<= 500"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [{"txn_id": "t1", "amount": 1.0, "merchant": "x" * 501}],
                "rules": [],
            })

    def test_rules_must_be_list(self):
        with pytest.raises(ToolValidationError, match="rules must be a list"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1")],
                "rules": "x",
            })

    def test_rules_capped(self):
        big = [_rule_merchant(f"c{i}", "x") for i in range(201)]
        with pytest.raises(ToolValidationError, match="200"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1")],
                "rules": big,
            })

    def test_rule_category_required(self):
        with pytest.raises(ToolValidationError, match="category"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1")],
                "rules": [{"merchant_contains": ["trader"]}],
            })

    def test_rule_requires_predicate(self):
        with pytest.raises(ToolValidationError, match="predicate"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1")],
                "rules": [{"category": "groceries"}],
            })

    def test_rule_merchant_contains_must_be_list(self):
        with pytest.raises(ToolValidationError, match="merchant_contains"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1")],
                "rules": [{"category": "g", "merchant_contains": "trader"}],
            })

    def test_rule_merchant_contains_items_must_be_string(self):
        with pytest.raises(ToolValidationError, match="non-empty string"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1")],
                "rules": [{"category": "g", "merchant_contains": [""]}],
            })

    def test_rule_amount_min_must_be_number(self):
        with pytest.raises(ToolValidationError, match="amount_min"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1")],
                "rules": [{"category": "g", "amount_min": "lots"}],
            })

    def test_rule_amount_min_max_order(self):
        with pytest.raises(ToolValidationError, match="amount_min"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1")],
                "rules": [{"category": "g", "amount_min": 100, "amount_max": 50}],
            })

    def test_rule_predicate_items_capped(self):
        big = [f"m{i}" for i in range(51)]
        with pytest.raises(ToolValidationError, match="50"):
            TransactionCategorizeTool().validate({
                "batch_slug": "b1",
                "transactions": [_txn("t1")],
                "rules": [{"category": "g", "merchant_contains": big}],
            })


class TestCategorization:
    def test_single_merchant_match(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant="Trader Joe's", amount=64.32)],
            "rules": [_rule_merchant("groceries", "trader joe")],
        })
        v = res.output["verdicts"][0]
        assert v["category"] == "groceries"
        assert v["matched_rule_idx"] == 0
        assert "trader joe" in v["rationale"]

    def test_merchant_case_insensitive(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant="TRADER JOE'S", amount=10)],
            "rules": [_rule_merchant("groceries", "trader joe")],
        })
        assert res.output["verdicts"][0]["category"] == "groceries"

    def test_first_rule_wins(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant="Trader Joe's", amount=350)],
            "rules": [
                # High-value rule first wins over generic merchant rule
                {"category": "big-shop", "merchant_contains": ["trader"], "amount_min": 300},
                _rule_merchant("groceries", "trader"),
            ],
        })
        v = res.output["verdicts"][0]
        assert v["category"] == "big-shop"
        assert v["matched_rule_idx"] == 0

    def test_unmatched_falls_to_uncategorized(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant="Strange Vendor")],
            "rules": [_rule_merchant("groceries", "trader joe")],
        })
        v = res.output["verdicts"][0]
        assert v["category"] == "uncategorized"
        assert v["matched_rule_idx"] is None
        assert "no_match" in v["rationale"]

    def test_amount_window_match(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant="Unknown", amount=150)],
            "rules": [_rule_amount("mid-spend", mn=100, mx=200)],
        })
        assert res.output["verdicts"][0]["category"] == "mid-spend"

    def test_amount_below_min_no_match(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant="Unknown", amount=50)],
            "rules": [_rule_amount("mid-spend", mn=100)],
        })
        assert res.output["verdicts"][0]["category"] == "uncategorized"

    def test_amount_above_max_no_match(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant="Unknown", amount=500)],
            "rules": [_rule_amount("low-spend", mx=100)],
        })
        assert res.output["verdicts"][0]["category"] == "uncategorized"

    def test_description_contains_match(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant=None, amount=12, description="MONTHLY GYM MEMBERSHIP")],
            "rules": [{"category": "fitness", "description_contains": ["gym"]}],
        })
        assert res.output["verdicts"][0]["category"] == "fitness"

    def test_missing_merchant_fails_merchant_predicate(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [{"txn_id": "t1", "amount": 1.0}],
            "rules": [_rule_merchant("groceries", "trader")],
        })
        assert res.output["verdicts"][0]["category"] == "uncategorized"

    def test_combined_predicates_must_all_match(self):
        # Merchant matches but amount is outside the window — no match.
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant="Trader Joe's", amount=50)],
            "rules": [{
                "category": "big-shop",
                "merchant_contains": ["trader"],
                "amount_min": 200,
            }],
        })
        assert res.output["verdicts"][0]["category"] == "uncategorized"

    def test_summary_per_category_counts(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [
                _txn("t1", merchant="Trader Joe's", amount=40),
                _txn("t2", merchant="Whole Foods", amount=60),
                _txn("t3", merchant="Random Shop", amount=20),
            ],
            "rules": [
                _rule_merchant("groceries", "trader", "whole foods"),
            ],
        })
        s = res.output["summary"]
        assert s["transaction_count"] == 3
        assert s["categorized_count"] == 2
        assert s["uncategorized_count"] == 1
        assert s["per_category"]["groceries"] == 2
        assert s["per_category"]["uncategorized"] == 1

    def test_empty_rules_marks_all_uncategorized(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1"), _txn("t2", merchant="X", amount=99)],
            "rules": [],
        })
        s = res.output["summary"]
        assert s["categorized_count"] == 0
        assert s["uncategorized_count"] == 2
        assert s["per_category"] == {"uncategorized": 2}

    def test_metadata_present(self):
        res = _run({
            "batch_slug": "fiscal-2026-05",
            "transactions": [_txn("t1")],
            "rules": [_rule_merchant("g", "trader")],
        })
        assert res.metadata["batch_slug"] == "fiscal-2026-05"
        assert res.metadata["transaction_count"] == 1
        assert res.metadata["categorized_count"] == 1
        assert res.metadata["uncategorized_count"] == 0

    def test_side_effect_summary_present(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1")],
            "rules": [_rule_merchant("g", "trader")],
        })
        assert "categorized 1/1" in res.side_effect_summary

    def test_amount_only_rule(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant=None, amount=1500)],
            "rules": [_rule_amount("big-ticket", mn=1000)],
        })
        assert res.output["verdicts"][0]["category"] == "big-ticket"

    def test_amount_min_inclusive(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant=None, amount=100.0)],
            "rules": [_rule_amount("c", mn=100)],
        })
        assert res.output["verdicts"][0]["category"] == "c"

    def test_amount_max_inclusive(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant=None, amount=100.0)],
            "rules": [_rule_amount("c", mx=100)],
        })
        assert res.output["verdicts"][0]["category"] == "c"

    def test_rule_count_in_output(self):
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1")],
            "rules": [_rule_merchant("a", "x"), _rule_merchant("b", "y")],
        })
        assert res.output["rule_count"] == 2

    def test_zero_amount_transaction(self):
        # Amount = 0 (refund placeholder) — rules with amount_min=0 should match.
        res = _run({
            "batch_slug": "b1",
            "transactions": [_txn("t1", merchant=None, amount=0)],
            "rules": [_rule_amount("nonneg", mn=0)],
        })
        assert res.output["verdicts"][0]["category"] == "nonneg"
