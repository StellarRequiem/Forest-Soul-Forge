"""Unit tests for the Skill Forge interpolation language — ADR-0031 T1.

Coverage:
  TestParse        — parser produces correct AST shapes for the full
                     supported syntax + rejects unsupported syntax.
  TestEvaluate     — every operator + function evaluates correctly.
  TestReferences   — references() returns the variable names actually
                     used (manifest validator depends on this).
  TestTemplate     — parse_template + Template.evaluate / references.
"""
from __future__ import annotations

import pytest

from forest_soul_forge.forge.skill_expression import (
    BinaryOp,
    Expr,
    ExpressionError,
    FuncCall,
    Literal,
    Template,
    UnaryOp,
    Var,
    parse,
    parse_template,
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
class TestParse:
    def test_string_literal_double_quoted(self):
        e = parse('"hello"')
        assert isinstance(e, Literal) and e.value == "hello"

    def test_string_literal_single_quoted(self):
        e = parse("'hi'")
        assert isinstance(e, Literal) and e.value == "hi"

    def test_int_literal(self):
        e = parse("42")
        assert isinstance(e, Literal) and e.value == 42

    def test_float_literal(self):
        e = parse("3.14")
        assert isinstance(e, Literal) and e.value == 3.14

    def test_keyword_literals(self):
        for src, expected in [
            ("true", True), ("false", False),
            ("True", True), ("False", False),
            ("null", None), ("None", None),
        ]:
            e = parse(src)
            assert isinstance(e, Literal)
            assert e.value is expected

    def test_bare_var(self):
        e = parse("foo")
        assert isinstance(e, Var) and e.name == "foo" and e.chain == ()

    def test_dotted_var(self):
        e = parse("step.result.count")
        assert isinstance(e, Var)
        assert e.name == "step"
        assert e.chain == ("result", "count")

    def test_function_call_no_args(self):
        e = parse("count()")
        assert isinstance(e, FuncCall) and e.fn == "count"
        assert e.args == ()

    def test_function_call_with_args(self):
        e = parse("default(x.foo, 'fallback')")
        assert isinstance(e, FuncCall) and e.fn == "default"
        assert len(e.args) == 2

    def test_binary_compare(self):
        for op in ("==", "!=", "<", "<=", ">", ">="):
            e = parse(f"a {op} b")
            assert isinstance(e, BinaryOp) and e.op == op

    def test_in_operator(self):
        e = parse("'x' in items")
        assert isinstance(e, BinaryOp) and e.op == "in"

    def test_not_in_operator(self):
        e = parse("'x' not in items")
        assert isinstance(e, BinaryOp) and e.op == "not in"

    def test_and_or_short_circuit_shape(self):
        e = parse("a and b or c")
        # parsed as (a and b) or c per precedence
        assert isinstance(e, BinaryOp) and e.op == "or"
        assert isinstance(e.left, BinaryOp) and e.left.op == "and"

    def test_not_unary(self):
        e = parse("not flag")
        assert isinstance(e, UnaryOp) and e.op == "not"

    def test_parens_change_grouping(self):
        e = parse("a and (b or c)")
        assert isinstance(e, BinaryOp) and e.op == "and"
        assert isinstance(e.right, BinaryOp) and e.right.op == "or"

    def test_unsupported_string_concat_rejected(self):
        # Only comparisons are binary ops; '+' isn't tokenized.
        with pytest.raises(ExpressionError):
            parse('"a" + "b"')

    def test_arbitrary_python_rejected(self):
        # No subscripts.
        with pytest.raises(ExpressionError):
            parse("items[0]")

    def test_trailing_token_rejected(self):
        with pytest.raises(ExpressionError, match="trailing"):
            parse("foo bar")

    def test_unexpected_char_rejected(self):
        with pytest.raises(ExpressionError, match="unexpected character"):
            parse("foo @ bar")


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------
class TestEvaluate:
    def test_var_lookup_via_dict_chain(self):
        e = parse("step.foo.bar")
        ctx = {"step": {"foo": {"bar": 42}}}
        assert e.evaluate(ctx) == 42

    def test_var_lookup_via_attr(self):
        class S:
            field = "x"
        e = parse("obj.field")
        assert e.evaluate({"obj": S()}) == "x"

    def test_unbound_name_raises(self):
        with pytest.raises(ExpressionError, match="unbound name"):
            parse("missing").evaluate({})

    def test_chain_through_none_raises(self):
        e = parse("step.foo")
        with pytest.raises(ExpressionError, match="cannot index"):
            e.evaluate({"step": None})

    def test_compare_operators(self):
        for src, expected in [
            ("1 == 1", True), ("1 != 2", True),
            ("1 < 2", True), ("2 <= 2", True),
            ("3 > 2", True), ("3 >= 3", True),
            ("'a' in items", True), ("'z' not in items", True),
        ]:
            assert parse(src).evaluate({"items": ["a", "b"]}) is expected

    def test_count_function(self):
        assert parse("count(items)").evaluate({"items": [1, 2, 3]}) == 3
        assert parse("count(empty)").evaluate({"empty": []}) == 0
        assert parse("count(none)").evaluate({"none": None}) == 0

    def test_any_all_functions(self):
        ctx = {"flags": [False, True, False]}
        assert parse("any(flags)").evaluate(ctx) is True
        assert parse("all(flags)").evaluate(ctx) is False

    def test_default_function(self):
        assert parse("default(x, 'fallback')").evaluate({"x": "real"}) == "real"
        assert parse("default(x, 'fallback')").evaluate({"x": None}) == "fallback"

    def test_and_or_short_circuit(self):
        # If 'and' didn't short-circuit, evaluating right-side on False
        # would error because `boom` is unbound.
        assert parse("false and boom").evaluate({}) is False
        assert parse("true or boom").evaluate({}) is True

    def test_not_operator(self):
        assert parse("not a").evaluate({"a": False}) is True
        assert parse("not a").evaluate({"a": True}) is False


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------
class TestReferences:
    def test_simple_var_returns_name(self):
        assert parse("foo").references() == {"foo"}

    def test_dotted_chain_returns_root_only(self):
        assert parse("step.result.count").references() == {"step"}

    def test_func_call_collects_arg_refs(self):
        assert parse("default(x.a, y.b)").references() == {"x", "y"}

    def test_compare_collects_both_sides(self):
        assert parse("a == b").references() == {"a", "b"}

    def test_literal_has_no_references(self):
        assert parse("42").references() == set()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
class TestTemplate:
    def test_pure_expression_returns_native_type(self):
        tpl = parse_template("${count}")
        assert tpl.is_pure_expression
        assert tpl.evaluate({"count": 5}) == 5

    def test_text_only_returns_string_unchanged(self):
        tpl = parse_template("hello world")
        assert not tpl.is_pure_expression
        assert tpl.evaluate({}) == "hello world"

    def test_mixed_text_and_expr(self):
        tpl = parse_template("got ${count} matches")
        assert not tpl.is_pure_expression
        assert tpl.evaluate({"count": 3}) == "got 3 matches"

    def test_multiple_expressions(self):
        tpl = parse_template("${a}-${b}")
        assert tpl.evaluate({"a": "x", "b": "y"}) == "x-y"

    def test_references_collects_from_all_blocks(self):
        tpl = parse_template("${a.foo} -- ${b}")
        assert tpl.references() == {"a", "b"}

    def test_invalid_inner_expression_raises(self):
        from forest_soul_forge.forge.skill_expression import ExpressionError
        with pytest.raises(ExpressionError):
            parse_template("${ items[0] }")
