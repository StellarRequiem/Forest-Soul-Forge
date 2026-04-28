"""``dynamic_policy.v1`` — apply / revoke a transient firewall rule.

ADR-0033 Phase B3 — privileged. Wraps the PrivClient ``pf-add`` /
``pf-drop`` operations (macOS pf, Linux iptables/nftables fall in
under the same helper anchor model). Used by ResponseRogue and
SecOpsSentinel to install a temporary block — e.g. drop traffic
from a suspicious source while triage runs — and tear it down
when the incident closes.

Operations:
  * ``add``  — add a single rule to a named anchor. Caller
    supplies anchor name + rule body; PrivClient validates both
    against the helper's allowlist (anchor charset, length caps).
  * ``drop`` — flush the named anchor. No rule body needed.

side_effects=external — durable change to the host's traffic
posture. The runtime auto-gates on requires_human_approval per
``external_always_human_approval`` in tool_policy.py. The TTL
is operator-promised (the tool does NOT schedule a teardown);
ResponseRogue's playbook is to record the grant in audit metadata
and call ``add`` again with ``op=drop`` when the timer fires —
this keeps the side-effect contract (every change → one tool call
→ one audit entry) clean.

Refusals (raise ToolValidationError):
  * priv_client not wired (helper not installed / lifespan flag off)
  * op not in {add, drop}
  * add without rule
  * drop with rule (the helper ignores the rule arg, but the tool
    refuses early so the audit log doesn't capture confusing input)
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_VALID_OPS = ("add", "drop")
_MAX_REASON = 256


class DynamicPolicyTool:
    """Apply or revoke a firewall anchor rule via the sudo helper.

    Args:
      op       (str, required): one of 'add', 'drop'.
      anchor   (str, required): anchor name. Allowlist mirrors
        the helper: [A-Za-z0-9-_./]+, ≤ 64 chars.
      rule     (str, required for op=add; absent for op=drop):
        the pf rule body to install. ≤ 256 chars.
      reason   (str, required): one-line operator-facing
        explanation recorded in audit metadata.

    Output:
      {
        "op":       str,
        "anchor":   str,
        "rule":     str | null,
        "reason":   str,
        "ok":       bool,
        "exit_code": int,
        "stdout":   str,
        "stderr":   str,
      }
    """

    name = "dynamic_policy"
    version = "1"
    side_effects = "external"

    def validate(self, args: dict[str, Any]) -> None:
        op = args.get("op")
        if op not in _VALID_OPS:
            raise ToolValidationError(
                f"op must be one of {list(_VALID_OPS)}; got {op!r}"
            )
        anchor = args.get("anchor")
        if not isinstance(anchor, str) or not anchor.strip():
            raise ToolValidationError(
                "anchor must be a non-empty string"
            )
        # Charset + length validation also runs in PrivClient — the
        # tool re-checks here so a typo fails BEFORE the runtime
        # increments call counters or queues approval.
        if len(anchor) > 64:
            raise ToolValidationError(
                f"anchor must be ≤ 64 chars; got {len(anchor)}"
            )

        rule = args.get("rule")
        if op == "add":
            if not isinstance(rule, str) or not rule.strip():
                raise ToolValidationError(
                    "op=add requires a non-empty 'rule'"
                )
            if len(rule) > 256:
                raise ToolValidationError(
                    f"rule must be ≤ 256 chars; got {len(rule)}"
                )
        else:  # op == drop
            if rule is not None:
                raise ToolValidationError(
                    "op=drop must NOT carry a 'rule' arg "
                    "(the helper ignores it; refusing to keep audit clean)"
                )
        reason = args.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ToolValidationError(
                "reason must be a non-empty string"
            )
        if len(reason) > _MAX_REASON:
            raise ToolValidationError(
                f"reason must be ≤ {_MAX_REASON} chars; got {len(reason)}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        if ctx.priv_client is None:
            raise ToolValidationError(
                "dynamic_policy.v1: no PrivClient wired on ctx (sudo "
                "helper not installed or FSF_ENABLE_PRIV_CLIENT=false). "
                "Run docs/runbooks/sudo-helper-install.md and set "
                "FSF_ENABLE_PRIV_CLIENT=true to enable, then restart."
            )
        from forest_soul_forge.security.priv_client import (
            HelperMissing,
            PrivClientError,
        )
        op = args["op"]
        anchor = args["anchor"].strip()
        reason = args["reason"].strip()
        rule = args.get("rule")
        rule_clean = rule.strip() if isinstance(rule, str) else None

        try:
            if op == "add":
                result = ctx.priv_client.pf_add(anchor, rule_clean)
            else:
                result = ctx.priv_client.pf_drop(anchor)
        except HelperMissing as e:
            raise ToolValidationError(
                f"dynamic_policy.v1: helper missing — {e}"
            ) from e
        except PrivClientError as e:
            raise ToolValidationError(
                f"dynamic_policy.v1: client refused — {e}"
            ) from e

        return ToolResult(
            output={
                "op":        op,
                "anchor":    anchor,
                "rule":      rule_clean,
                "reason":    reason,
                "ok":        result.ok,
                "exit_code": result.exit_code,
                "stdout":    result.stdout,
                "stderr":    result.stderr,
            },
            metadata={
                "priv_op":   "pf-add" if op == "add" else "pf-drop",
                "priv_args": [anchor] + ([rule_clean] if op == "add" else []),
                "reason":    reason,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"{op} pf-anchor {anchor}: "
                f"{'OK' if result.ok else f'failed (exit {result.exit_code})'}"
            ),
        )
