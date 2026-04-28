"""Skill Forge interpolation expression language — ADR-0031 T1.

A deliberately small expression language used inside ``${...}`` blocks
in skill manifests. Designed so an audit-chain reader can verify
"this skill could only ever call these tools with these arg shapes":

  - Variables:    bare ``name`` chained with ``.field.subfield``
  - Literals:     strings, ints, floats, booleans, null
  - Functions:    ``count(list)``, ``any(list)``, ``all(list)``,
                  ``len(value)``, ``default(value, fallback)``
  - Comparisons:  ``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``,
                  ``in``, ``not in``
  - Boolean:      ``and``, ``or``, ``not``
  - Parentheses

NOT supported (deliberately):
  - String concatenation (``"x" + "y"``) — emit literal templates
  - Arithmetic (other than what's needed for compare)
  - Lambdas, list comprehensions, attribute reflection
  - Loops — ``for_each`` is a manifest construct, not an expression
  - Arbitrary Python — there is no ``eval`` here

Two layers:

* :func:`parse` — string → :class:`Expr` AST. Pure syntactic check.
* :meth:`Expr.evaluate` — AST + context → value. Runtime use.
* :meth:`Expr.references` — AST → set of variable names. Used by the
  manifest validator to confirm every ``${step_x.foo}`` points at a
  known step.

Lossy roundtrip is fine — we never re-emit expressions, only evaluate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class ExpressionError(Exception):
    """Raised by parse() and evaluate(). Distinct from ManifestError so
    the validator can attach better context (which step / which arg)."""


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Expr:
    """Marker base. Use ``parse()`` rather than constructing nodes
    directly. ``evaluate(ctx)`` and ``references()`` are implemented on
    every concrete subclass."""

    def evaluate(self, ctx: dict[str, Any]) -> Any:  # pragma: no cover
        raise NotImplementedError

    def references(self) -> set[str]:
        """Top-level variable names this expression touches.
        ``${step_a.foo}`` returns ``{"step_a"}`` — attribute chains
        are descent steps, not separate references."""
        return set()


@dataclass(frozen=True)
class Literal(Expr):
    value: Any

    def evaluate(self, ctx):
        return self.value


@dataclass(frozen=True)
class Var(Expr):
    """Variable lookup with a dotted chain.

    ``Var(name="step_a", chain=("foo", "bar"))`` means ``ctx["step_a"]
    .foo.bar`` — attribute access falls back to dict-key lookup so
    JSON-shaped structures work the same way.
    """

    name: str
    chain: tuple[str, ...] = ()

    def evaluate(self, ctx):
        if self.name not in ctx:
            raise ExpressionError(f"unbound name {self.name!r}")
        cur = ctx[self.name]
        for seg in self.chain:
            cur = _drill(cur, seg)
        return cur

    def references(self) -> set[str]:
        return {self.name}


@dataclass(frozen=True)
class FuncCall(Expr):
    fn: str
    args: tuple[Expr, ...]

    def evaluate(self, ctx):
        impl = _FUNCTIONS.get(self.fn)
        if impl is None:
            raise ExpressionError(f"unknown function {self.fn!r}")
        return impl(*[a.evaluate(ctx) for a in self.args])

    def references(self) -> set[str]:
        out: set[str] = set()
        for a in self.args:
            out |= a.references()
        return out


@dataclass(frozen=True)
class BinaryOp(Expr):
    op: str  # "==", "!=", "<", "<=", ">", ">=", "in", "not in", "and", "or"
    left: Expr
    right: Expr

    def evaluate(self, ctx):
        # Short-circuit and/or before evaluating the right side.
        if self.op == "and":
            return bool(self.left.evaluate(ctx)) and bool(self.right.evaluate(ctx))
        if self.op == "or":
            return bool(self.left.evaluate(ctx)) or bool(self.right.evaluate(ctx))
        l = self.left.evaluate(ctx)
        r = self.right.evaluate(ctx)
        return _apply_binop(self.op, l, r)

    def references(self) -> set[str]:
        return self.left.references() | self.right.references()


@dataclass(frozen=True)
class UnaryOp(Expr):
    op: str  # "not"
    inner: Expr

    def evaluate(self, ctx):
        v = self.inner.evaluate(ctx)
        if self.op == "not":
            return not bool(v)
        raise ExpressionError(f"unknown unary op {self.op!r}")

    def references(self) -> set[str]:
        return self.inner.references()


# ---------------------------------------------------------------------------
# Function table
# ---------------------------------------------------------------------------
def _count(x):
    if x is None:
        return 0
    return len(x) if hasattr(x, "__len__") else sum(1 for _ in x)


def _any(x):
    return any(bool(v) for v in (x or []))


def _all(x):
    return all(bool(v) for v in (x or []))


def _len(x):
    if x is None:
        raise ExpressionError("len() of None")
    return len(x)


def _default(value, fallback):
    return value if value is not None else fallback


_FUNCTIONS: dict[str, Any] = {
    "count": _count,
    "any": _any,
    "all": _all,
    "len": _len,
    "default": _default,
}


# ---------------------------------------------------------------------------
# Drill helper — chain segment lookup
# ---------------------------------------------------------------------------
def _drill(cur: Any, seg: str) -> Any:
    if cur is None:
        raise ExpressionError(f"cannot index {seg!r} on None")
    if isinstance(cur, dict):
        if seg not in cur:
            raise ExpressionError(f"key {seg!r} missing on dict")
        return cur[seg]
    if hasattr(cur, seg):
        return getattr(cur, seg)
    raise ExpressionError(
        f"cannot resolve {seg!r} on {type(cur).__name__}"
    )


def _apply_binop(op: str, l: Any, r: Any) -> Any:
    if op == "==": return l == r
    if op == "!=": return l != r
    if op == "<":  return l < r
    if op == "<=": return l <= r
    if op == ">":  return l > r
    if op == ">=": return l >= r
    if op == "in":     return l in r
    if op == "not in": return l not in r
    raise ExpressionError(f"unknown binary op {op!r}")


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------
_TOKEN_SPEC = (
    ("WS",      r"\s+"),
    ("STRING",  r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''),
    ("FLOAT",   r"\d+\.\d+"),
    ("INT",     r"\d+"),
    # 'not in' must match before 'not' and 'in' separately. Special-case
    # via a dedicated token.
    ("NOT_IN",  r"not\s+in\b"),
    ("OP2",     r"==|!=|<=|>="),
    ("OP1",     r"[<>]"),
    ("LPAREN",  r"\("),
    ("RPAREN",  r"\)"),
    ("DOT",     r"\."),
    ("COMMA",   r","),
    ("NAME",    r"[A-Za-z_][A-Za-z0-9_]*"),
)
_TOKEN_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _TOKEN_SPEC))


@dataclass(frozen=True)
class _Tok:
    kind: str
    value: str


def _tokenize(s: str) -> list[_Tok]:
    out: list[_Tok] = []
    i = 0
    while i < len(s):
        m = _TOKEN_RE.match(s, i)
        if not m:
            raise ExpressionError(
                f"unexpected character {s[i]!r} at position {i} in {s!r}"
            )
        kind = m.lastgroup or "?"
        value = m.group()
        i = m.end()
        if kind == "WS":
            continue
        # Keyword-shaped names get promoted.
        if kind == "NAME" and value in ("and", "or", "not", "in",
                                         "true", "false", "True",
                                         "False", "null", "None"):
            kind = "KEYWORD"
        out.append(_Tok(kind=kind, value=value))
    return out


# ---------------------------------------------------------------------------
# Parser — recursive descent, Pratt-style for binary operators
# ---------------------------------------------------------------------------
# Precedence (low to high):
#   or
#   and
#   not (unary)
#   in / not in / == / != / < / <= / > / >=
#   primary  (literals, vars, calls, parenthesized)

class _Parser:
    def __init__(self, tokens: list[_Tok]) -> None:
        self.toks = tokens
        self.i = 0

    def peek(self) -> _Tok | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def eat(self, kind: str | None = None, value: str | None = None) -> _Tok:
        tok = self.peek()
        if tok is None:
            raise ExpressionError(
                f"unexpected end of expression "
                f"(expected {value or kind})"
            )
        if kind and tok.kind != kind:
            raise ExpressionError(
                f"expected {kind}, got {tok.kind} ({tok.value!r})"
            )
        if value and tok.value != value:
            raise ExpressionError(
                f"expected {value!r}, got {tok.value!r}"
            )
        self.i += 1
        return tok

    def parse(self) -> Expr:
        expr = self.parse_or()
        if self.peek() is not None:
            tok = self.peek()
            raise ExpressionError(
                f"unexpected trailing token {tok.value!r}"
            )
        return expr

    def parse_or(self) -> Expr:
        left = self.parse_and()
        while self._is_keyword("or"):
            self.eat()
            right = self.parse_and()
            left = BinaryOp("or", left, right)
        return left

    def parse_and(self) -> Expr:
        left = self.parse_not()
        while self._is_keyword("and"):
            self.eat()
            right = self.parse_not()
            left = BinaryOp("and", left, right)
        return left

    def parse_not(self) -> Expr:
        if self._is_keyword("not"):
            self.eat()
            inner = self.parse_not()
            return UnaryOp("not", inner)
        return self.parse_compare()

    def parse_compare(self) -> Expr:
        left = self.parse_primary()
        while True:
            tok = self.peek()
            if tok is None:
                break
            if tok.kind == "OP2":
                self.eat()
                right = self.parse_primary()
                left = BinaryOp(tok.value, left, right)
            elif tok.kind == "OP1":
                self.eat()
                right = self.parse_primary()
                left = BinaryOp(tok.value, left, right)
            elif tok.kind == "KEYWORD" and tok.value == "in":
                self.eat()
                right = self.parse_primary()
                left = BinaryOp("in", left, right)
            elif tok.kind == "NOT_IN":
                self.eat()
                right = self.parse_primary()
                left = BinaryOp("not in", left, right)
            else:
                break
        return left

    def parse_primary(self) -> Expr:
        tok = self.peek()
        if tok is None:
            raise ExpressionError("unexpected end of expression")
        if tok.kind == "STRING":
            self.eat()
            return Literal(_unquote(tok.value))
        if tok.kind == "INT":
            self.eat()
            return Literal(int(tok.value))
        if tok.kind == "FLOAT":
            self.eat()
            return Literal(float(tok.value))
        if tok.kind == "KEYWORD":
            if tok.value in ("true", "True"):
                self.eat()
                return Literal(True)
            if tok.value in ("false", "False"):
                self.eat()
                return Literal(False)
            if tok.value in ("null", "None"):
                self.eat()
                return Literal(None)
            raise ExpressionError(f"unexpected keyword {tok.value!r}")
        if tok.kind == "LPAREN":
            self.eat()
            inner = self.parse_or()
            self.eat("RPAREN")
            return inner
        if tok.kind == "NAME":
            self.eat()
            # Function call?
            nxt = self.peek()
            if nxt and nxt.kind == "LPAREN":
                self.eat("LPAREN")
                args: list[Expr] = []
                if self.peek() and self.peek().kind != "RPAREN":
                    args.append(self.parse_or())
                    while self.peek() and self.peek().kind == "COMMA":
                        self.eat("COMMA")
                        args.append(self.parse_or())
                self.eat("RPAREN")
                return FuncCall(fn=tok.value, args=tuple(args))
            # Var with optional dotted chain.
            chain: list[str] = []
            while self.peek() and self.peek().kind == "DOT":
                self.eat("DOT")
                seg = self.eat("NAME")
                chain.append(seg.value)
            return Var(name=tok.value, chain=tuple(chain))
        raise ExpressionError(f"unexpected token {tok.kind} ({tok.value!r})")

    def _is_keyword(self, kw: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "KEYWORD" and tok.value == kw


def _unquote(s: str) -> str:
    """Strip surrounding quotes + handle simple backslash escapes."""
    quote = s[0]
    body = s[1:-1]
    return body.replace("\\" + quote, quote).replace("\\\\", "\\")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def parse(expression: str) -> Expr:
    """Parse a single expression. Used both at manifest validate-time
    and at runtime evaluate-time. Caches are deliberately not added —
    skill manifests are small and compile-once at load."""
    toks = _tokenize(expression)
    return _Parser(toks).parse()


# ---------------------------------------------------------------------------
# Template expansion: a value with embedded ${...} blocks
# ---------------------------------------------------------------------------
_TEMPLATE_RE = re.compile(r"\$\{([^}]*)\}")


def parse_template(template: str) -> "Template":
    """Parse a string with zero or more ``${...}`` blocks into a
    :class:`Template`. Plain text segments stay as strings; expressions
    are parsed once.

    A template that's *entirely* a single ``${...}`` returns the
    expression's evaluated value verbatim (so ``args.count`` can
    return an int, not ``"5"``). Mixed text + expression renders to a
    string with each expression interpolated as ``str()``.
    """
    parts: list[str | Expr] = []
    last = 0
    for m in _TEMPLATE_RE.finditer(template):
        if m.start() > last:
            parts.append(template[last:m.start()])
        parts.append(parse(m.group(1)))
        last = m.end()
    if last < len(template):
        parts.append(template[last:])
    return Template(tuple(parts))


@dataclass(frozen=True)
class Template:
    """A parsed template — list of string segments and Expr nodes."""

    parts: tuple[Any, ...]

    @property
    def is_pure_expression(self) -> bool:
        """True iff this template is exactly one expression with no
        surrounding text — return value should keep its native type."""
        return len(self.parts) == 1 and isinstance(self.parts[0], Expr)

    def evaluate(self, ctx: dict[str, Any]) -> Any:
        if self.is_pure_expression:
            return self.parts[0].evaluate(ctx)
        chunks: list[str] = []
        for p in self.parts:
            if isinstance(p, str):
                chunks.append(p)
            else:
                chunks.append(str(p.evaluate(ctx)))
        return "".join(chunks)

    def references(self) -> set[str]:
        out: set[str] = set()
        for p in self.parts:
            if isinstance(p, Expr):
                out |= p.references()
        return out


# ---------------------------------------------------------------------------
# Compiled argument values — preserve YAML structure (dict / list) end-to-end
# ---------------------------------------------------------------------------
#
# Pre-2026-04-29 the manifest parser called ``parse_template(str(v))`` on every
# YAML arg value, which stringified dicts and lists into their Python repr
# before they reached the tool's validator. That broke:
#
#   args:
#     tags: ["morning_sweep", "log_lurker"]   # → string "['morning_sweep', ...]"
#     inputs: {match_count: ${scan.match_count}}  # → string "{'match_count': ...}"
#
# ``delegate.v1``'s ``inputs: dict`` arg in particular blocked the canonical
# Security Swarm cross-agent chain — every link uses delegate.v1.
#
# The fix below preserves YAML structure: ``compile_arg`` recursively walks
# dicts and lists, parsing string leaves into Templates (so ``${...}``
# interpolation still works inside nested structures) and wrapping native
# literals (int/float/bool/None) in ``_LiteralArg``. Each compiled-arg type
# implements the same ``evaluate(ctx) → Any`` + ``references() → set[str]``
# contract as Template, so the runtime's ``tpl.evaluate(bindings)`` call site
# in skill_runtime.py needed no changes.


@dataclass(frozen=True)
class _LiteralArg:
    """A non-template literal value — int, float, bool, None. Returned
    unchanged at runtime. Has the same evaluate/references interface as
    Template so the runtime can treat all arg-value shapes uniformly."""

    value: Any

    def evaluate(self, ctx: dict[str, Any]) -> Any:
        return self.value

    def references(self) -> set[str]:
        return set()


@dataclass(frozen=True)
class _DictArg:
    """A YAML mapping where each value is itself a compiled arg
    (Template / _DictArg / _ListArg / _LiteralArg). At runtime, evaluates
    each value against the bindings and returns a real ``dict``."""

    items: tuple[tuple[str, Any], ...]   # frozen sequence of (key, compiled)

    def evaluate(self, ctx: dict[str, Any]) -> dict[str, Any]:
        return {k: v.evaluate(ctx) for k, v in self.items}

    def references(self) -> set[str]:
        out: set[str] = set()
        for _, v in self.items:
            out |= v.references()
        return out


@dataclass(frozen=True)
class _ListArg:
    """A YAML sequence where each item is itself a compiled arg. At
    runtime, evaluates each item against the bindings and returns a real
    ``list``."""

    items: tuple[Any, ...]

    def evaluate(self, ctx: dict[str, Any]) -> list[Any]:
        return [v.evaluate(ctx) for v in self.items]

    def references(self) -> set[str]:
        out: set[str] = set()
        for v in self.items:
            out |= v.references()
        return out


def compile_arg(value: Any) -> "Template | _LiteralArg | _DictArg | _ListArg":
    """Compile a YAML arg value into a structure with .evaluate(ctx) and
    .references(). Preserves dict/list shape from the YAML so tools that
    expect structured args (``delegate.v1`` / ``log_correlate.v1`` /
    ``lateral_movement_detect.v1`` / ``memory_write.v1`` tags / ...)
    receive them unchanged.

    Type dispatch:
      * ``str``           → ``parse_template(value)``  — preserves ``${...}``
      * ``dict``          → ``_DictArg``  — recursively compile each value
      * ``list``          → ``_ListArg``  — recursively compile each item
      * ``int|float|bool``→ ``_LiteralArg``  — pass through unchanged
      * ``None``          → ``_LiteralArg(None)``
      * other             → fallback: ``parse_template(str(value))``
    """
    if isinstance(value, str):
        return parse_template(value)
    if isinstance(value, bool):
        # Order matters: bool is a subclass of int in Python, so this
        # branch must precede the int check.
        return _LiteralArg(value=value)
    if isinstance(value, (int, float)):
        return _LiteralArg(value=value)
    if value is None:
        return _LiteralArg(value=None)
    if isinstance(value, dict):
        items = tuple((str(k), compile_arg(v)) for k, v in value.items())
        return _DictArg(items=items)
    if isinstance(value, list):
        return _ListArg(items=tuple(compile_arg(v) for v in value))
    # Unknown type — fall back to stringification with a parse_template
    # wrap. Shouldn't happen for well-formed YAML; preserves the old
    # behavior as a safety net.
    return parse_template(str(value))
