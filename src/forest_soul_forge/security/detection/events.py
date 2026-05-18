"""DetectionRule + DetectionMatch — the dataclass surface.

ADR-0065 T1 (B389). The parser (parser.py) produces DetectionRule
instances; the engine (T2) calls DetectionRule.evaluate(event) per
event in each batch; matches become DetectionMatch instances that
the engine emits as `detection_fired` audit chain entries.

The evaluate() method is intentionally narrow — pure dict-vs-event
field-equality over the parser-validated selections + condition.
Aggregation, time windows, and cross-event correlation are NOT
in the v1 subset (ADR-0065 D1). If a rule needs more, the parser
rejects it with a clear error and the operator authors the rule
out of band.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


class DetectionRuleError(ValueError):
    """Raised by parser when a rule violates the Sigma-subset
    contract or by evaluate() when an evaluated event has the
    wrong shape for the rule's logsource."""


# Operator-supplied severity values. Sigma defines low/medium/
# high/critical; we mirror.
_VALID_LEVELS = frozenset({"informational", "low", "medium", "high", "critical"})


@dataclass(frozen=True)
class DetectionMatch:
    """One match — emitted by evaluate() when a rule fires.

    The engine (T2) collects matches across a batch and emits
    one `detection_fired` audit event per (rule, batch) pair
    carrying the matched_event_ids list, not per individual
    match. This dataclass is the per-event signal the engine
    aggregates.
    """

    rule_id: str
    rule_version: str          # sha256(rule body, hex) — pins the rule
    event_id: str              # TelemetryEvent.event_id
    technique: str             # ATT&CK technique (or 'attack.unknown')
    level: str                 # one of _VALID_LEVELS
    matched_selections: tuple[str, ...]  # which selections all passed


@dataclass(frozen=True)
class DetectionRule:
    """A parsed Sigma-subset rule.

    Immutable; the parser builds and the engine reads. Subclasses
    are forbidden — the rule semantic is the field set here. New
    semantic gates land via parser support + new fields, never via
    behavioral subclassing.

    Selections and condition are normalized at parse time so
    evaluate() runs in O(events × selections) without re-parsing.
    """

    rule_id: str
    title: str
    description: str
    rule_version: str              # sha256(rule body, hex)
    level: str
    tags: tuple[str, ...]          # ATT&CK technique list; >= 1 mandatory
    logsource_source: str | None   # matches TelemetryEvent.source if set
    logsource_event_type: str | None  # matches TelemetryEvent.event_type if set
    selections: dict[str, dict[str, Any]]  # name -> {field: expected_value}
    condition: str                  # boolean expression over selection names

    def __post_init__(self) -> None:
        if self.level not in _VALID_LEVELS:
            raise DetectionRuleError(
                f"rule {self.rule_id!r}: level must be in {sorted(_VALID_LEVELS)}; "
                f"got {self.level!r}"
            )
        if not self.tags:
            raise DetectionRuleError(
                f"rule {self.rule_id!r}: at least one tag is mandatory per "
                f"ADR-0065 D3 (use 'attack.unknown' if the technique is not "
                f"identified)"
            )
        if not self.selections:
            raise DetectionRuleError(
                f"rule {self.rule_id!r}: at least one selection is required"
            )
        if not self.condition:
            raise DetectionRuleError(
                f"rule {self.rule_id!r}: condition cannot be empty"
            )

    # ----- evaluation ------------------------------------------------------

    def applies_to(self, event_source: str, event_type: str) -> bool:
        """Does this rule's logsource match the event's source/type?

        Either field is optional in the rule. Unset means
        "match any". A rule with no logsource at all matches every
        event (rare; useful for catch-all rules).
        """
        if self.logsource_source is not None and self.logsource_source != event_source:
            return False
        if self.logsource_event_type is not None and self.logsource_event_type != event_type:
            return False
        return True

    def evaluate(
        self,
        event_id: str,
        event_source: str,
        event_type: str,
        event_payload: dict[str, Any],
    ) -> DetectionMatch | None:
        """Test a single event against this rule. Returns a
        DetectionMatch when the rule fires, else None.

        Per ADR-0065 D1, the subset shipped is:
          selection.<field>: <expected_value>  — equality match
            (field may use dotted-path lookup into event_payload
            for nested keys, e.g. process.image)
          condition: <boolean expr over selection names>
            Supported operators: 'and', 'or', 'not', parentheses.
            Identifiers must be exact selection names from the
            rule's detection block.

        If the rule's logsource does not match the event, returns
        None without evaluating selections (cheap rejection).
        """
        if not self.applies_to(event_source, event_type):
            return None

        # Evaluate each selection against the event payload.
        passed: dict[str, bool] = {}
        for sel_name, sel_match in self.selections.items():
            passed[sel_name] = _selection_matches(sel_match, event_payload)

        # Evaluate the condition expression over the passed table.
        if _eval_condition(self.condition, passed):
            matched = tuple(name for name, ok in passed.items() if ok)
            # Default tag for the match is the first ATT&CK technique
            # in tags; engines that want all tags can read from
            # rule.tags directly.
            technique = self.tags[0]
            return DetectionMatch(
                rule_id=self.rule_id,
                rule_version=self.rule_version,
                event_id=event_id,
                technique=technique,
                level=self.level,
                matched_selections=matched,
            )
        return None


# ---------- selection / condition primitives ------------------------------

def _selection_matches(sel_match: dict[str, Any], payload: dict[str, Any]) -> bool:
    """A selection's match dict is interpreted as: ALL fields must
    equal their expected values. Field names may be dotted-paths
    into nested dicts (e.g. 'process.image').

    Sigma's full grammar supports modifiers (contains|startswith|
    regex|...) — the subset shipped here is equality only. If a
    rule needs a modifier, the parser must reject the rule with a
    clear "unsupported modifier" error; users author the equality
    rule out of band.
    """
    if not sel_match:
        # An empty selection matches nothing (vacuous-true is a
        # rule-quality footgun: don't fire on an empty rule).
        return False
    for field_path, expected in sel_match.items():
        actual = _dotted_lookup(payload, field_path)
        if actual != expected:
            return False
    return True


def _dotted_lookup(d: dict[str, Any], path: str) -> Any:
    """Resolve 'a.b.c' against nested dicts. Returns None if any
    intermediate is missing or not a dict."""
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


# Boolean condition evaluator — handles 'and', 'or', 'not',
# parentheses, and identifiers (selection names). NOT a general
# expression evaluator: refuses anything else with a clear error.

_TOKEN_RE = re.compile(r"\(|\)|\band\b|\bor\b|\bnot\b|[A-Za-z_][A-Za-z0-9_]*")


def _tokenize_condition(condition: str) -> list[str]:
    tokens = _TOKEN_RE.findall(condition.strip())
    # Reconstruct + verify the tokens cover the source (no stray
    # characters). Anything not matched by the regex is unsupported.
    rebuilt = " ".join(tokens)
    stripped = re.sub(r"\s+", " ", condition.strip())
    # Build a comparable normalized form of the original by
    # injecting spaces around parens.
    normalized = re.sub(r"\s+", " ", re.sub(r"([()])", r" \1 ", stripped)).strip()
    if rebuilt != normalized:
        raise DetectionRuleError(
            f"condition contains unsupported syntax. "
            f"Subset supports: and / or / not / parentheses / "
            f"selection names. Got: {condition!r}"
        )
    return tokens


def _eval_condition(condition: str, passed: dict[str, bool]) -> bool:
    """Recursive-descent evaluator: expr := term (or term)*
    term := factor (and factor)*  factor := 'not' factor | '(' expr ')' | identifier."""
    tokens = _tokenize_condition(condition)
    pos = [0]

    def peek() -> str | None:
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def consume() -> str:
        tok = tokens[pos[0]]
        pos[0] += 1
        return tok

    def parse_factor() -> bool:
        t = peek()
        if t is None:
            raise DetectionRuleError(f"unexpected end of condition")
        if t == "not":
            consume()
            return not parse_factor()
        if t == "(":
            consume()
            val = parse_expr()
            if peek() != ")":
                raise DetectionRuleError(f"missing ')' in condition")
            consume()
            return val
        # identifier
        name = consume()
        if name not in passed:
            raise DetectionRuleError(
                f"condition references unknown selection {name!r}; "
                f"declared selections: {sorted(passed)}"
            )
        return passed[name]

    def parse_term() -> bool:
        left = parse_factor()
        while peek() == "and":
            consume()
            right = parse_factor()
            left = left and right
        return left

    def parse_expr() -> bool:
        left = parse_term()
        while peek() == "or":
            consume()
            right = parse_term()
            left = left or right
        return left

    result = parse_expr()
    if pos[0] != len(tokens):
        raise DetectionRuleError(
            f"unexpected trailing tokens in condition: "
            f"{tokens[pos[0]:]!r}"
        )
    return result


def rule_version_hash(rule_body: str) -> str:
    """sha256 of the rule's YAML body (hex). Used as
    rule_version on every DetectionMatch + on the
    detection_fired audit chain event so chain history pins the
    exact rule that fired (ADR-0065 D5).

    The body should be the canonicalized YAML — the parser
    re-emits via yaml.safe_dump(sort_keys=True) before hashing
    so whitespace/key-order changes don't flip the version."""
    return hashlib.sha256(rule_body.encode("utf-8")).hexdigest()
