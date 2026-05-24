"""Tests for ADR-0092 Phase C — investment_compare.v1 builtin tool."""
from __future__ import annotations

import asyncio

import pytest

from forest_soul_forge.tools.base import ToolContext, ToolValidationError
from forest_soul_forge.tools.builtin.investment_compare import (
    InvestmentCompareTool,
)


def _ctx():
    return ToolContext(
        instance_id="investment_researcher_test",
        agent_dna="a" * 12,
        role="investment_researcher",
        genre="researcher",
        session_id=None,
    )


def _run(args):
    return asyncio.run(InvestmentCompareTool().execute(args, _ctx()))


def _opt(slug, **fields):
    return {"option_slug": slug, "label": slug.upper(), "fields": fields}


def _dim(field, cls, label=None):
    out = {"field": field, "class": cls}
    if label:
        out["label"] = label
    return out


class TestValidation:
    def test_comparison_slug_required(self):
        with pytest.raises(ToolValidationError, match="comparison_slug"):
            InvestmentCompareTool().validate({
                "options": [_opt("a", er=0.05)],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_comparison_slug_must_be_string(self):
        with pytest.raises(ToolValidationError, match="comparison_slug"):
            InvestmentCompareTool().validate({
                "comparison_slug": 1,
                "options": [_opt("a", er=0.05)],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_comparison_slug_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="comparison_slug"):
            InvestmentCompareTool().validate({
                "comparison_slug": "   ",
                "options": [_opt("a", er=0.05)],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_comparison_slug_length_capped(self):
        with pytest.raises(ToolValidationError, match="<= 200"):
            InvestmentCompareTool().validate({
                "comparison_slug": "x" * 201,
                "options": [_opt("a", er=0.05)],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_options_required(self):
        with pytest.raises(ToolValidationError, match="options"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_options_must_be_list(self):
        with pytest.raises(ToolValidationError, match="options"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": "x",
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_options_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="at least one"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_options_capped(self):
        big = [_opt(f"o{i}", er=0.01) for i in range(51)]
        with pytest.raises(ToolValidationError, match="50"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": big,
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_option_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="must be a dict"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": ["nope"],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_option_slug_required(self):
        with pytest.raises(ToolValidationError, match="option_slug"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [{"fields": {"er": 0.05}}],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_option_slug_must_be_unique(self):
        with pytest.raises(ToolValidationError, match="duplicates"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [_opt("a", er=0.05), _opt("a", er=0.10)],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_option_fields_required(self):
        with pytest.raises(ToolValidationError, match="fields"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [{"option_slug": "a"}],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_option_fields_must_be_dict(self):
        with pytest.raises(ToolValidationError, match="fields"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [{"option_slug": "a", "fields": "no"}],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_option_label_must_be_string(self):
        with pytest.raises(ToolValidationError, match="label"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [{"option_slug": "a", "label": 5, "fields": {"er": 0.05}}],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_option_label_length_capped(self):
        with pytest.raises(ToolValidationError, match="<= 500"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [{"option_slug": "a", "label": "x" * 501, "fields": {"er": 0.05}}],
                "dimensions": [_dim("er", "lower_is_better")],
            })

    def test_dimensions_required(self):
        with pytest.raises(ToolValidationError, match="dimensions"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [_opt("a", er=0.05)],
            })

    def test_dimensions_must_be_list(self):
        with pytest.raises(ToolValidationError, match="dimensions must be a list"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [_opt("a", er=0.05)],
                "dimensions": "x",
            })

    def test_dimensions_must_be_non_empty(self):
        with pytest.raises(ToolValidationError, match="at least one"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [_opt("a", er=0.05)],
                "dimensions": [],
            })

    def test_dimensions_capped(self):
        big = [_dim(f"f{i}", "info_only") for i in range(31)]
        with pytest.raises(ToolValidationError, match="30"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [_opt("a", er=0.05)],
                "dimensions": big,
            })

    def test_dimension_field_required(self):
        with pytest.raises(ToolValidationError, match="field"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [_opt("a", er=0.05)],
                "dimensions": [{"class": "lower_is_better"}],
            })

    def test_dimension_class_must_be_valid(self):
        with pytest.raises(ToolValidationError, match="lower_is_better"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [_opt("a", er=0.05)],
                "dimensions": [{"field": "er", "class": "magic"}],
            })

    def test_dimension_field_must_be_unique(self):
        with pytest.raises(ToolValidationError, match="duplicates"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [_opt("a", er=0.05)],
                "dimensions": [
                    _dim("er", "lower_is_better"),
                    _dim("er", "higher_is_better"),
                ],
            })

    def test_dimension_label_must_be_string(self):
        with pytest.raises(ToolValidationError, match="label"):
            InvestmentCompareTool().validate({
                "comparison_slug": "c1",
                "options": [_opt("a", er=0.05)],
                "dimensions": [{"field": "er", "class": "lower_is_better", "label": 99}],
            })


class TestComparison:
    def test_lower_is_better_winner(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("vti", er=0.03), _opt("voo", er=0.05)],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        dim = res.output["dimensions"][0]
        assert dim["winner_slug"] == "vti"
        assert dim["winner_value"] == 0.03

    def test_higher_is_better_winner(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("vti", ret=10.5), _opt("voo", ret=11.2)],
            "dimensions": [_dim("ret", "higher_is_better")],
        })
        dim = res.output["dimensions"][0]
        assert dim["winner_slug"] == "voo"
        assert dim["winner_value"] == 11.2

    def test_info_only_no_winner(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("vti", ticker="VTI"), _opt("voo", ticker="VOO")],
            "dimensions": [_dim("ticker", "info_only")],
        })
        dim = res.output["dimensions"][0]
        assert dim["winner_slug"] is None
        assert "info_only" in dim["rationale"]

    def test_delta_to_winner_lower_is_better(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("vti", er=0.03), _opt("voo", er=0.05)],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        voo = next(o for o in res.output["options"] if o["option_slug"] == "voo")
        assert voo["per_dimension"][0]["delta_to_winner"] == 0.02

    def test_delta_to_winner_higher_is_better(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("vti", ret=10.5), _opt("voo", ret=11.2)],
            "dimensions": [_dim("ret", "higher_is_better")],
        })
        vti = next(o for o in res.output["options"] if o["option_slug"] == "vti")
        # 11.2 - 10.5 = 0.7
        assert vti["per_dimension"][0]["delta_to_winner"] == 0.7

    def test_is_winner_flag(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("vti", er=0.03), _opt("voo", er=0.05)],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        vti = next(o for o in res.output["options"] if o["option_slug"] == "vti")
        voo = next(o for o in res.output["options"] if o["option_slug"] == "voo")
        assert vti["per_dimension"][0]["is_winner"] is True
        assert voo["per_dimension"][0]["is_winner"] is False

    def test_missing_data_per_option(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("vti", er=0.03), _opt("voo")],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        voo = next(o for o in res.output["options"] if o["option_slug"] == "voo")
        assert voo["per_dimension"][0]["missing_data"] is True
        assert voo["missing_data_count"] == 1

    def test_all_missing_no_winner(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("a"), _opt("b")],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        dim = res.output["dimensions"][0]
        assert dim["winner_slug"] is None

    def test_multiple_dimensions(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [
                _opt("vti", er=0.03, ret=10.5, ticker="VTI"),
                _opt("voo", er=0.05, ret=11.2, ticker="VOO"),
            ],
            "dimensions": [
                _dim("er", "lower_is_better"),
                _dim("ret", "higher_is_better"),
                _dim("ticker", "info_only"),
            ],
        })
        assert res.output["dimensions"][0]["winner_slug"] == "vti"
        assert res.output["dimensions"][1]["winner_slug"] == "voo"
        assert res.output["dimensions"][2]["winner_slug"] is None

    def test_no_recommendation_in_side_effect_summary(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("vti", er=0.03), _opt("voo", er=0.05)],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        # The tool MUST signal "no recommendation" — info-only contract.
        assert "NO recommendation" in res.side_effect_summary
        assert "operator decides" in res.side_effect_summary

    def test_summary_dimension_counts(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("a", er=0.03, ticker="A")],
            "dimensions": [
                _dim("er", "lower_is_better"),
                _dim("ticker", "info_only"),
            ],
        })
        s = res.output["summary"]
        assert s["compared_dimension_count"] == 1
        assert s["info_only_dimension_count"] == 1

    def test_total_missing_cells(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [
                _opt("a", er=0.03, ret=10),
                _opt("b", er=0.05),  # missing ret
                _opt("c"),  # missing both
            ],
            "dimensions": [
                _dim("er", "lower_is_better"),
                _dim("ret", "higher_is_better"),
            ],
        })
        # b missing ret (1) + c missing er + ret (2) = 3
        assert res.output["summary"]["total_missing_data_cells"] == 3

    def test_dimension_count_in_output(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("a", er=0.03)],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        assert res.output["dimension_count"] == 1
        assert res.output["option_count"] == 1

    def test_label_propagates(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [{"option_slug": "vti", "label": "Vanguard TSM", "fields": {"er": 0.03}}],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        assert res.output["options"][0]["label"] == "Vanguard TSM"

    def test_label_defaults_to_slug(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [{"option_slug": "vti", "fields": {"er": 0.03}}],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        assert res.output["options"][0]["label"] == "vti"

    def test_dimension_label_in_output(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("a", er=0.03)],
            "dimensions": [_dim("er", "lower_is_better", label="Expense Ratio %")],
        })
        assert res.output["dimensions"][0]["label"] == "Expense Ratio %"

    def test_dimension_label_defaults_to_field(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("a", er=0.03)],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        assert res.output["dimensions"][0]["label"] == "er"

    def test_metadata_present(self):
        res = _run({
            "comparison_slug": "index-funds-2026",
            "options": [_opt("a", er=0.03), _opt("b", er=0.05)],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        assert res.metadata["comparison_slug"] == "index-funds-2026"
        assert res.metadata["compared_dimension_count"] == 1

    def test_bool_value_treated_as_missing(self):
        # Booleans must not be coerced into numeric comparison.
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("a", er=True), _opt("b", er=0.05)],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        a = next(o for o in res.output["options"] if o["option_slug"] == "a")
        assert a["per_dimension"][0]["missing_data"] is True

    def test_higher_is_better_tie_alpha_first(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("z", ret=10), _opt("a", ret=10)],
            "dimensions": [_dim("ret", "higher_is_better")],
        })
        assert res.output["dimensions"][0]["winner_slug"] == "a"

    def test_string_field_info_only(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("a", asset="equity")],
            "dimensions": [_dim("asset", "info_only")],
        })
        opt = res.output["options"][0]
        assert opt["per_dimension"][0]["value"] == "equity"
        assert opt["per_dimension"][0]["missing_data"] is False

    def test_single_option_compared(self):
        res = _run({
            "comparison_slug": "c1",
            "options": [_opt("solo", er=0.05)],
            "dimensions": [_dim("er", "lower_is_better")],
        })
        # Single option is the trivial winner.
        assert res.output["dimensions"][0]["winner_slug"] == "solo"
        assert res.output["options"][0]["per_dimension"][0]["delta_to_winner"] == 0.0
