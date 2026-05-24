"""Tests for ADR-0092 Phase B — bill_recurrence_check.v1 builtin tool."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.bill_recurrence_check import (
    BillRecurrenceCheckTool,
)


def _ctx():
    return ToolContext(
        instance_id="bill_steward_test",
        agent_dna="a" * 12,
        role="bill_steward",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(BillRecurrenceCheckTool().execute(args, _ctx()))


def _bills_monthly(bill_slug, anchor="2026-01-15", count=6, label=None):
    base = date.fromisoformat(anchor)
    out = []
    for i in range(count):
        d = base + timedelta(days=30 * i)
        entry = {"bill_slug": bill_slug, "bill_date": d.isoformat()}
        if label:
            entry["label"] = label
        out.append(entry)
    return out


def _bills_with_interval(bill_slug, days, count=3, anchor="2026-01-15"):
    base = date.fromisoformat(anchor)
    return [
        {"bill_slug": bill_slug, "bill_date": (base + timedelta(days=days * i)).isoformat()}
        for i in range(count)
    ]


class TestValidation:
    def test_ledger_slug_required(self):
        with pytest.raises(ToolValidationError, match="ledger_slug"):
            BillRecurrenceCheckTool().validate({
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
            })

    def test_ledger_slug_must_be_string(self):
        with pytest.raises(ToolValidationError, match="ledger_slug"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": 1,
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
            })

    def test_ledger_slug_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="ledger_slug"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "   ",
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
            })

    def test_ledger_slug_length_capped(self):
        with pytest.raises(ToolValidationError, match="<= 200"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "x" * 201,
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
            })

    def test_today_iso_required(self):
        with pytest.raises(ToolValidationError, match="today_iso"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": _bills_monthly("rent"),
            })

    def test_today_iso_must_be_string(self):
        with pytest.raises(ToolValidationError, match="today_iso"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": _bills_monthly("rent"),
                "today_iso": 20260524,
            })

    def test_today_iso_must_parse(self):
        with pytest.raises(ToolValidationError, match="YYYY-MM-DD"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": _bills_monthly("rent"),
                "today_iso": "not-a-date",
            })

    def test_bills_required(self):
        with pytest.raises(ToolValidationError, match="bills"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "today_iso": "2026-05-24",
            })

    def test_bills_must_be_list(self):
        with pytest.raises(ToolValidationError, match="bills"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": "x",
                "today_iso": "2026-05-24",
            })

    def test_bills_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="at least one"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": [],
                "today_iso": "2026-05-24",
            })

    def test_bills_capped(self):
        big = [
            {"bill_slug": f"b{i}", "bill_date": "2026-01-01"}
            for i in range(1001)
        ]
        with pytest.raises(ToolValidationError, match="1000"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": big,
                "today_iso": "2026-05-24",
            })

    def test_bill_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="must be a dict"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": ["nope"],
                "today_iso": "2026-05-24",
            })

    def test_bill_slug_required(self):
        with pytest.raises(ToolValidationError, match="bill_slug"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": [{"bill_date": "2026-01-01"}],
                "today_iso": "2026-05-24",
            })

    def test_bill_slug_must_be_string(self):
        with pytest.raises(ToolValidationError, match="bill_slug"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": [{"bill_slug": 1, "bill_date": "2026-01-01"}],
                "today_iso": "2026-05-24",
            })

    def test_bill_date_required(self):
        with pytest.raises(ToolValidationError, match="bill_date"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": [{"bill_slug": "rent"}],
                "today_iso": "2026-05-24",
            })

    def test_bill_date_must_parse(self):
        with pytest.raises(ToolValidationError, match="YYYY-MM-DD"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": [{"bill_slug": "rent", "bill_date": "not-a-date"}],
                "today_iso": "2026-05-24",
            })

    def test_bill_amount_must_be_number(self):
        with pytest.raises(ToolValidationError, match="amount"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": [{"bill_slug": "rent", "bill_date": "2026-01-01", "amount": "lots"}],
                "today_iso": "2026-05-24",
            })

    def test_bill_amount_rejects_bool(self):
        with pytest.raises(ToolValidationError, match="amount"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": [{"bill_slug": "rent", "bill_date": "2026-01-01", "amount": True}],
                "today_iso": "2026-05-24",
            })

    def test_bill_label_must_be_string(self):
        with pytest.raises(ToolValidationError, match="label"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": [{"bill_slug": "rent", "bill_date": "2026-01-01", "label": 99}],
                "today_iso": "2026-05-24",
            })

    def test_bill_label_length_capped(self):
        with pytest.raises(ToolValidationError, match="<= 500"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": [{"bill_slug": "rent", "bill_date": "2026-01-01", "label": "x" * 501}],
                "today_iso": "2026-05-24",
            })

    def test_min_history_floor(self):
        with pytest.raises(ToolValidationError, match="min_history"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
                "min_history": 1,
            })

    def test_min_history_ceiling(self):
        with pytest.raises(ToolValidationError, match="min_history"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
                "min_history": 200,
            })

    def test_min_history_must_be_int(self):
        with pytest.raises(ToolValidationError, match="min_history"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
                "min_history": 3.5,
            })

    def test_day_drift_overrides_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="day_drift_overrides"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
                "day_drift_overrides": "monthly=3",
            })

    def test_day_drift_overrides_keys_restricted(self):
        with pytest.raises(ToolValidationError, match="monthly"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
                "day_drift_overrides": {"weekly": 1},
            })

    def test_day_drift_overrides_value_range(self):
        with pytest.raises(ToolValidationError, match="day_drift_overrides"):
            BillRecurrenceCheckTool().validate({
                "ledger_slug": "l1",
                "bills": _bills_monthly("rent"),
                "today_iso": "2026-05-24",
                "day_drift_overrides": {"monthly": 100},
            })


class TestDetection:
    def test_monthly_detected(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_monthly("rent", anchor="2026-01-15", count=6),
            "today_iso": "2026-06-20",
        })
        s = res.output["series"][0]
        assert s["verdict"] == "monthly"
        assert s["next_due_iso"] is not None

    def test_quarterly_detected(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("car-insurance", 91, count=4, anchor="2025-08-01"),
            "today_iso": "2026-06-20",
        })
        s = res.output["series"][0]
        assert s["verdict"] == "quarterly"

    def test_annual_detected(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("domain-renewal", 365, count=3, anchor="2024-05-01"),
            "today_iso": "2026-05-15",
        })
        s = res.output["series"][0]
        assert s["verdict"] == "annual"

    def test_insufficient_history(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": [{"bill_slug": "rent", "bill_date": "2026-04-15"}],
            "today_iso": "2026-05-24",
        })
        s = res.output["series"][0]
        assert s["verdict"] == "insufficient_history"
        assert s["next_due_iso"] is None

    def test_irregular_intervals(self):
        # 7-day intervals — does not match monthly/quarterly/annual.
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("weekly-x", 7, count=4),
            "today_iso": "2026-05-24",
        })
        s = res.output["series"][0]
        assert s["verdict"] == "irregular"

    def test_missing_cycle_flagged(self):
        # Monthly bills with last on 2026-01-15, today is 2026-05-24 — way overdue.
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_monthly("rent", anchor="2025-08-15", count=6),
            "today_iso": "2026-06-30",
        })
        s = res.output["series"][0]
        assert s["verdict"] == "monthly"
        # The last bill is 2026-01-15 (5 months later); today=2026-06-30 → overdue.
        assert s["missing_cycle_flag"] is True

    def test_no_missing_when_current(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_monthly("rent", anchor="2026-01-15", count=5),
            "today_iso": "2026-05-25",
        })
        s = res.output["series"][0]
        assert s["missing_cycle_flag"] is False

    def test_summary_counts(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": (
                _bills_monthly("rent", count=4) +
                _bills_with_interval("weekly-x", 7, count=3) +
                [{"bill_slug": "once", "bill_date": "2026-02-01"}]
            ),
            "today_iso": "2026-06-30",
        })
        s = res.output["summary"]
        assert s["series_count"] == 3
        assert s["matched_count"] == 1
        assert s["irregular_count"] == 1
        assert s["insufficient_count"] == 1

    def test_label_propagates(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_monthly("rent", count=3, label="Monthly Rent"),
            "today_iso": "2026-05-24",
        })
        assert res.output["series"][0]["label"] == "Monthly Rent"

    def test_label_defaults_to_slug(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_monthly("rent", count=3),
            "today_iso": "2026-05-24",
        })
        assert res.output["series"][0]["label"] == "rent"

    def test_series_sorted_by_slug(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": (
                _bills_monthly("zeta", count=2) +
                _bills_monthly("alpha", count=2)
            ),
            "today_iso": "2026-05-24",
        })
        slugs = [s["bill_slug"] for s in res.output["series"]]
        assert slugs == ["alpha", "zeta"]

    def test_drift_overrides_apply(self):
        # Bills 33d apart — outside default monthly drift (3) but ok with override 5.
        res_default = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("x", 33, count=4),
            "today_iso": "2026-05-24",
        })
        # 33d median; monthly = 30 ± 3 = [27,33] inclusive. Actually 33 = 30+3 exactly.
        # Use 34d to force the failure.
        res_default = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("x", 34, count=4),
            "today_iso": "2026-05-24",
        })
        assert res_default.output["series"][0]["verdict"] == "irregular"
        res_override = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("x", 34, count=4),
            "today_iso": "2026-05-24",
            "day_drift_overrides": {"monthly": 5},
        })
        assert res_override.output["series"][0]["verdict"] == "monthly"

    def test_metadata_present(self):
        res = _run({
            "ledger_slug": "bills-2026-05",
            "bills": _bills_monthly("rent"),
            "today_iso": "2026-05-24",
        })
        assert res.metadata["ledger_slug"] == "bills-2026-05"
        assert res.metadata["series_count"] == 1
        assert res.metadata["matched_count"] == 1

    def test_side_effect_summary_present(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_monthly("rent", count=3),
            "today_iso": "2026-05-24",
        })
        assert "checked 1 bill series" in res.side_effect_summary

    def test_today_iso_in_output(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_monthly("rent", count=3),
            "today_iso": "2026-05-24",
        })
        assert res.output["today_iso"] == "2026-05-24"

    def test_next_due_is_last_plus_interval(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("rent", 30, count=4, anchor="2026-01-15"),
            "today_iso": "2026-05-24",
        })
        s = res.output["series"][0]
        last = date.fromisoformat(s["last_bill_date"])
        nxt = date.fromisoformat(s["next_due_iso"])
        assert (nxt - last).days == 30

    def test_bills_out_of_chronological_order(self):
        # Tool should sort internally.
        res = _run({
            "ledger_slug": "l1",
            "bills": [
                {"bill_slug": "rent", "bill_date": "2026-05-01"},
                {"bill_slug": "rent", "bill_date": "2026-01-01"},
                {"bill_slug": "rent", "bill_date": "2026-03-02"},
                {"bill_slug": "rent", "bill_date": "2026-04-01"},
                {"bill_slug": "rent", "bill_date": "2026-02-01"},
            ],
            "today_iso": "2026-05-24",
        })
        s = res.output["series"][0]
        assert s["last_bill_date"] == "2026-05-01"

    def test_median_interval_in_output(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("rent", 30, count=3),
            "today_iso": "2026-05-24",
        })
        s = res.output["series"][0]
        assert s["median_interval_days"] == 30.0

    def test_min_history_override(self):
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("rent", 30, count=2),
            "today_iso": "2026-05-24",
            "min_history": 3,
        })
        s = res.output["series"][0]
        assert s["verdict"] == "insufficient_history"

    def test_first_match_wins_cycle_priority(self):
        # If an interval matches multiple cycles (unlikely but possible at boundaries),
        # the tool tries monthly first.
        res = _run({
            "ledger_slug": "l1",
            "bills": _bills_with_interval("rent", 30, count=4),
            "today_iso": "2026-05-24",
        })
        assert res.output["series"][0]["verdict"] == "monthly"
