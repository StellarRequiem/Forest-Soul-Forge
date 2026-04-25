"""Tool constraint policy — derives per-(agent, tool) constraints from
the agent's trait profile + the tool's catalog metadata.

ADR-0018 T2.5. Resolves what's allowed and what requires human approval
for each tool an agent has access to. The trait profile is the input;
the constraints are the output. The constitution.yaml renders these
into the agent's permanent rulebook so the constraint set is part of
constitution_hash and survives rebuild-from-artifacts.

The policy is a hardcoded list of rules in v1 — operators today are the
project's developers, not external users editing YAML. The rule shape
is deliberately YAML-compatible so we can promote this to
``config/tool_constraint_policy.yaml`` when the second operator shows
up. Until then, simpler is better.

Resolution algorithm:
  1. Start every tool at the policy defaults.
  2. Walk rules in declaration order.
  3. For each rule, if `when` matches the agent's trait profile and the
     tool falls in `target`, apply `set` to that tool's constraints.
  4. Record the rule's name in the tool's `applied_rules` list for
     audit transparency.

Later rules can refine earlier ones (e.g., a high-thoroughness agent
might already have approval-required from a caution rule, plus a
call-cap from a thoroughness rule). They don't conflict; they layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forest_soul_forge.core.tool_catalog import ToolDef
    from forest_soul_forge.core.trait_engine import TraitProfile


# ---------------------------------------------------------------------------
# Default constraint set — applied to every tool unless a rule overrides
# ---------------------------------------------------------------------------
DEFAULT_CONSTRAINTS: dict[str, Any] = {
    # Soft cap on calls per session. Tunable later via per-tool overrides;
    # 1000 is generous for read_only tools and gets clamped down by rules
    # for higher-side-effect tools.
    "max_calls_per_session": 1000,
    # When true, every invocation requires explicit human go-ahead. When
    # false, the runtime is permitted to call the tool autonomously
    # (still bounded by max_calls_per_session and audit_every_call).
    "requires_human_approval": False,
    # When true, every invocation appends a tool_invoked event to the
    # audit chain (per ADR-0018 T5 once the runtime ships). True by
    # default — observability is cheap, opacity is expensive.
    "audit_every_call": True,
}


@dataclass(frozen=True)
class ResolvedConstraints:
    """The output of policy resolution for one (agent, tool) pair."""

    tool_name: str
    tool_version: str
    side_effects: str
    constraints: dict[str, Any]
    applied_rules: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """For canonical_body() / to_yaml() emission. Sorted-key dict so
        two equivalent resolutions produce byte-identical output."""
        return {
            "name": self.tool_name,
            "version": self.tool_version,
            "side_effects": self.side_effects,
            "constraints": dict(sorted(self.constraints.items())),
            "applied_rules": list(self.applied_rules),
        }


# ---------------------------------------------------------------------------
# Rule shape
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Rule:
    """One policy rule. Frozen — the policy is immutable at runtime.

    `when_*` and `target_*` fields are the matching conditions. Any
    field set to None is "don't constrain on this dimension." `set_`
    is the constraint overrides applied when the rule matches.
    """

    name: str
    description: str
    # Trait condition — None means "always matches on the trait side."
    when_trait: str | None
    when_op: str | None       # ">=" | ">" | "<=" | "<" | "==" | "!="
    when_value: int | None
    # Target condition — None means "all tools."
    target_side_effects_in: tuple[str, ...] | None
    # Constraint overrides to apply.
    set_: dict[str, Any] = field(default_factory=dict)

    def matches_profile(self, profile: "TraitProfile") -> bool:
        """True if the trait condition is satisfied (or absent)."""
        if self.when_trait is None:
            return True
        if self.when_trait not in profile.trait_values:
            # Unknown trait — rule cannot match; report it as a
            # non-match rather than raising. The catalog of valid
            # trait names lives in trait_engine; if a rule references
            # a missing trait, fix the rule, don't crash birth.
            return False
        v = profile.trait_values[self.when_trait]
        return _compare(v, self.when_op or "==", self.when_value or 0)

    def matches_tool(self, tool: "ToolDef") -> bool:
        """True if the tool falls in this rule's target set."""
        if self.target_side_effects_in is None:
            return True
        return tool.side_effects in self.target_side_effects_in


def _compare(value: int, op: str, threshold: int) -> bool:
    if op == ">=": return value >= threshold
    if op == ">":  return value > threshold
    if op == "<=": return value <= threshold
    if op == "<":  return value < threshold
    if op == "==": return value == threshold
    if op == "!=": return value != threshold
    raise ValueError(f"unknown comparison op: {op!r}")


# ---------------------------------------------------------------------------
# The actual rules
# ---------------------------------------------------------------------------
# Order matters: rules are applied in this sequence and later rules
# layer onto earlier ones. The two "always" rules go LAST so that even
# a low-caution / low-thoroughness agent still gets approval-required
# on filesystem and external tools — the safety floor is independent
# of the trait profile.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        name="high_caution_approval_on_side_effects",
        description=(
            "Agents with caution >= 80 require human approval on any "
            "tool whose side_effects is not 'read_only'. Codifies the "
            "tool-risk-guide recommendation that high-caution agents "
            "lean on the operator for any externally-visible action."
        ),
        when_trait="caution",
        when_op=">=",
        when_value=80,
        target_side_effects_in=("network", "filesystem", "external"),
        set_={"requires_human_approval": True},
    ),
    _Rule(
        name="high_thoroughness_caps_external_calls",
        description=(
            "Agents with thoroughness >= 80 cap network and external "
            "tool calls at 50 per session. The intent: thorough agents "
            "should re-derive from existing reads rather than re-query "
            "the world."
        ),
        when_trait="thoroughness",
        when_op=">=",
        when_value=80,
        target_side_effects_in=("network", "external"),
        set_={"max_calls_per_session": 50},
    ),
    _Rule(
        name="filesystem_always_human_approval",
        description=(
            "Filesystem tools always require human approval. Path "
            "scoping is in the tool implementation; agent-side trust "
            "is not a substitute for that, and a filesystem write "
            "under agent-controlled input is a path-traversal risk no "
            "matter the agent's caution level."
        ),
        when_trait=None,  # always
        when_op=None,
        when_value=None,
        target_side_effects_in=("filesystem",),
        set_={"requires_human_approval": True},
    ),
    _Rule(
        name="external_always_human_approval",
        description=(
            "External (mutating) tools always require human approval. "
            "Durable side effects (sending email, creating tickets, "
            "executing commands) reach the world before the operator "
            "can intervene; the daemon refuses to bypass approval "
            "regardless of the agent's trait values."
        ),
        when_trait=None,
        when_op=None,
        when_value=None,
        target_side_effects_in=("external",),
        set_={"requires_human_approval": True},
    ),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def resolve_constraints(
    profile: "TraitProfile",
    tool: "ToolDef",
) -> ResolvedConstraints:
    """Resolve per-tool constraints for one agent.

    Returns a ResolvedConstraints with the merged constraint set and
    the names of every rule that matched (in declaration order). The
    list of matched rules is what the constitution.yaml exposes for
    auditors — answers "why is this constraint set what it is" without
    requiring re-derivation.
    """
    out_constraints = dict(DEFAULT_CONSTRAINTS)
    matched: list[str] = []
    for rule in _RULES:
        if not rule.matches_profile(profile):
            continue
        if not rule.matches_tool(tool):
            continue
        matched.append(rule.name)
        out_constraints.update(rule.set_)

    return ResolvedConstraints(
        tool_name=tool.name,
        tool_version=tool.version,
        side_effects=tool.side_effects,
        constraints=out_constraints,
        applied_rules=tuple(matched),
    )


def resolve_kit_constraints(
    profile: "TraitProfile",
    tools: list["ToolDef"],
) -> list[ResolvedConstraints]:
    """Resolve constraints for a whole tool kit at once. Order is
    preserved — the resolved list is in the same order as the input
    kit, which matches how SoulGenerator emits tools in the soul.md
    frontmatter."""
    return [resolve_constraints(profile, t) for t in tools]


def rule_names() -> tuple[str, ...]:
    """Diagnostic helper — returns the names of every rule the policy
    defines, in declaration order. Used by tests and by future tooling
    that wants to render the rule list to a human."""
    return tuple(r.name for r in _RULES)
