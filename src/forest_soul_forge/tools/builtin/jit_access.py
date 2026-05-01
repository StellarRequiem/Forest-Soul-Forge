"""``jit_access.v1`` — issue a time-bounded access grant.

ADR-0033 Phase B3. SecOpsSentinel's authorization primitive: when
a downstream agent (or operator-named principal) needs scoped
access to a resource — read a sensitive memory record, run a
privileged probe, talk to an external system — the requester
calls this tool with {principal, scope, ttl_seconds, reason}
and gets back a grant ID + expires_at timestamp.

The grant IS the audit event. side_effects=external means the
runtime wraps the call in a ``tool_invoked`` chain entry with
the grant payload hashed into result_digest. Downstream skills
that need to check whether a grant is still valid query the
audit chain (or a memory snapshot of it) for the latest grant
matching (principal, scope) and compare expires_at to now.

The tool does NOT mutate any external system on its own — no
network calls, no filesystem writes. The "external" classification
reflects that the grant changes what other agents are *allowed*
to do, which is a durable side effect even though the bytes only
touch the audit chain. Per ADR-0033 A4, side_effects=external
auto-triggers requires_human_approval — every JIT grant goes
through the operator queue before issuance.

Caps: ttl_seconds ≤ 24h (86400). Reason ≤ 256 chars. Principal
+ scope ≤ 64 chars each. Hard-cap of 1 grant per call (no batch
issuance — operator approval per grant is the design intent).
"""
from __future__ import annotations

import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_TTL_SECONDS = 86400          # 24h hard cap
_MAX_PRINCIPAL_LEN = 64
_MAX_SCOPE_LEN = 64
_MAX_REASON_LEN = 256
_VALID_SCOPE_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-:"
)


class JitAccessTool:
    """Issue a time-bounded access grant + audit event.

    Args:
      principal     (str, required): who the grant is for. Typically
        an instance_id (downstream agent) or an operator handle.
        ≤ 64 chars.
      scope         (str, required): what the grant authorizes.
        Free-form string the downstream check parses. Convention:
        ``<resource>:<action>`` (e.g. "memory.lineage:read",
        "fsf-priv:kill-pid"). ≤ 64 chars; URL-safe-ish charset.
      ttl_seconds   (int, required): grant lifetime in seconds.
        1 ≤ ttl_seconds ≤ 86400 (24h).
      reason        (str, required): one-line operator-facing
        explanation. Recorded in audit metadata and shown in the
        approval queue. ≤ 256 chars.

    Output:
      {
        "grant_id":    str,    # uuid4 — durable identifier
        "principal":   str,
        "scope":       str,
        "granted_at":  str,    # ISO-8601 UTC
        "expires_at":  str,    # ISO-8601 UTC
        "ttl_seconds": int,
        "reason":      str,
        "fingerprint": str     # short hash of (principal, scope, granted_at)
      }
    """

    name = "jit_access"
    version = "1"
    side_effects = "external"
    # ADR-0021-amendment §5 — JIT credential grants are reversible
    # (revoke + expire), so reversible-with-policy class. Required
    # initiative L4. security_mid + security_high reach by birthing
    # at ceiling L4; default L3 cannot autonomously grant.
    required_initiative_level = "L4"

    def validate(self, args: dict[str, Any]) -> None:
        principal = args.get("principal")
        if not isinstance(principal, str) or not principal.strip():
            raise ToolValidationError(
                "principal must be a non-empty string"
            )
        if len(principal) > _MAX_PRINCIPAL_LEN:
            raise ToolValidationError(
                f"principal must be ≤ {_MAX_PRINCIPAL_LEN} chars; "
                f"got {len(principal)}"
            )
        scope = args.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            raise ToolValidationError(
                "scope must be a non-empty string"
            )
        if len(scope) > _MAX_SCOPE_LEN:
            raise ToolValidationError(
                f"scope must be ≤ {_MAX_SCOPE_LEN} chars; got {len(scope)}"
            )
        bad = set(scope) - _VALID_SCOPE_CHARS
        if bad:
            raise ToolValidationError(
                f"scope contains disallowed chars: {sorted(bad)!r}"
            )
        ttl = args.get("ttl_seconds")
        if not isinstance(ttl, int) or isinstance(ttl, bool):
            raise ToolValidationError(
                f"ttl_seconds must be a positive integer; got {ttl!r}"
            )
        if ttl < 1 or ttl > _MAX_TTL_SECONDS:
            raise ToolValidationError(
                f"ttl_seconds must be in [1, {_MAX_TTL_SECONDS}]; got {ttl}"
            )
        reason = args.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ToolValidationError(
                "reason must be a non-empty string"
            )
        if len(reason) > _MAX_REASON_LEN:
            raise ToolValidationError(
                f"reason must be ≤ {_MAX_REASON_LEN} chars; "
                f"got {len(reason)}"
            )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        principal = args["principal"].strip()
        scope     = args["scope"].strip()
        ttl       = args["ttl_seconds"]
        reason    = args["reason"].strip()

        granted_at = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(
            granted_at.timestamp() + ttl, tz=timezone.utc,
        )
        grant_id = str(uuid.uuid4())
        # Stable fingerprint for downstream lookup — lets a checker
        # query "is there an active grant for (principal, scope)?"
        # without scanning every grant_id. Hash includes granted_at
        # so two grants for the same (principal, scope) at different
        # times produce different fingerprints.
        fp_input = f"{principal}|{scope}|{granted_at.isoformat()}"
        fingerprint = hashlib.sha256(
            fp_input.encode("utf-8"),
        ).hexdigest()[:16]

        return ToolResult(
            output={
                "grant_id":    grant_id,
                "principal":   principal,
                "scope":       scope,
                "granted_at":  granted_at.isoformat(),
                "expires_at":  expires_at.isoformat(),
                "ttl_seconds": ttl,
                "reason":      reason,
                "fingerprint": fingerprint,
            },
            metadata={
                # Tagged so audit-chain consumers can filter for JIT
                # grants without parsing every tool_invoked entry.
                "jit_grant":          True,
                "principal":          principal,
                "scope":              scope,
                "expires_at_unix":    int(expires_at.timestamp()),
                "fingerprint":        fingerprint,
                "issuer_instance_id": ctx.instance_id,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"jit_grant {fingerprint} → {principal} / {scope} "
                f"(ttl={ttl}s)"
            ),
        )


# ---------------------------------------------------------------------------
# Helper for downstream skills/tools — pure-function, importable.
# ---------------------------------------------------------------------------
def is_grant_valid(
    grant_output: dict[str, Any],
    *,
    now_unix: float | None = None,
) -> bool:
    """Returns True if the grant's expires_at is still in the future.

    Used by downstream tools that gate behavior on an active JIT
    grant: they recall the latest grant for their (principal, scope)
    via memory_recall, then call this helper. Pure function — no
    I/O — so it doesn't need the Tool runtime infrastructure.
    """
    exp = grant_output.get("expires_at")
    if not isinstance(exp, str):
        return False
    try:
        exp_dt = datetime.fromisoformat(exp)
    except ValueError:
        return False
    cutoff = now_unix if now_unix is not None else time.time()
    return exp_dt.timestamp() > cutoff
