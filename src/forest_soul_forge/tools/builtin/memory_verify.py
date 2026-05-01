"""``memory_verify.v1`` — promote a memory entry to verified status.

ADR-003X Phase K1. The Iron Gate equivalent. Forest's existing memory
subsystem stores entries with provenance + scope + consent grants; this
tool adds the missing verification layer by reusing the consent-grant
table with a sentinel recipient.

Why a sentinel recipient (no schema change):
    The verification status is one bit per entry — either an external
    human verifier has signed off, or they haven't. A new column would
    have done the job, but the consent_grants table already encodes
    "external party X has standing on entry Y at time T" — exactly the
    semantic of verification. Treating the operator/verifier as a
    special "recipient" means existing consent-aware tools see
    verification as just another grant, and the v8 schema migration
    landed for the secrets store stays the only schema bump from
    ADR-003X.

Semantics:
    - The verifier_id is recorded in granted_by — typically a human
      identifier (operator handle, public key fingerprint, signing key).
    - Re-verification updates the timestamp + clears any prior
      revocation (idempotent, matches consent grant semantics).
    - Verification can be revoked via memory_unverify.v1 (planned).
    - Verification status is queryable via Memory.is_verified(entry_id)
      and Memory.get_verifier(entry_id).
    - The seal_note arg is operator-supplied free text that lands in
      the audit chain alongside the verification event — provides
      context for *why* this entry was verified.

side_effects: filesystem
    The tool writes to the daemon's SQLite (the consent grant row).
    Treated as filesystem rather than read_only to be honest about
    the state mutation; no external network or process side effects.
"""
from __future__ import annotations

from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


class MemoryVerifyError(Exception):
    """Tool-level error — distinct from validation failures."""


class MemoryVerifyTool:
    """Promote a memory entry to verified status via external verifier.

    Args:
      entry_id (str): the memory entry being verified. Must exist
        in the agent's accessible scope; the runtime will refuse
        verification of entries that don't exist.
      verifier_id (str): identifier of the human verifier — operator
        handle, public-key fingerprint, signing key short ID.
        Recorded in granted_by on the consent row.
      seal_note (str, optional): free text describing why this entry
        is being verified. Lands in the audit chain alongside the
        verification event; max 500 chars.

    Output:
      {
        "entry_id":      str,
        "verified":      bool,
        "verifier_id":   str,
        "verified_at":   str,    # iso timestamp
      }
    """

    name = "memory_verify"
    version = "1"
    side_effects = "filesystem"
    # ADR-0021-amendment §5 — promoting a memory entry to verified
    # is operator-driven by convention but technically dispatchable
    # by any agent in whose kit it appears. Required L3 — reactive
    # Companion (L1) cannot autonomously promote inferences to
    # verified ground truth, even with a verifier_id arg (the gate
    # is structural, not just argument-shape). Operator-driven calls
    # land in the agent's session context as L3+ via the operator's
    # birthing posture.
    required_initiative_level = "L3"

    def validate(self, args: dict[str, Any]) -> None:
        entry_id = args.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise ToolValidationError("entry_id is required and must be a non-empty string")

        verifier_id = args.get("verifier_id")
        if not isinstance(verifier_id, str) or not verifier_id.strip():
            raise ToolValidationError("verifier_id is required and must be a non-empty string")

        seal_note = args.get("seal_note")
        if seal_note is not None:
            if not isinstance(seal_note, str):
                raise ToolValidationError("seal_note must be a string when provided")
            if len(seal_note) > 500:
                raise ToolValidationError(
                    f"seal_note too long ({len(seal_note)} chars); max 500"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        entry_id = args["entry_id"]
        verifier_id = args["verifier_id"]
        seal_note = args.get("seal_note")

        memory = ctx.memory
        if memory is None:
            raise MemoryVerifyError(
                "memory subsystem not wired into this tool call — the "
                "daemon should populate ctx.memory; check lifespan"
            )

        # Confirm the entry exists and the calling agent can see it.
        # Verification on an entry the agent can't reach would be a
        # leaky permission grant.
        entry = memory.get(entry_id)
        if entry is None:
            raise MemoryVerifyError(
                f"memory entry {entry_id!r} not found"
            )
        # If the entry has scope=private + isn't owned by the calling
        # agent, refuse — verification is an action against an entry
        # the verifier-proposing agent has standing on.
        if entry.scope == "private" and entry.instance_id != ctx.instance_id:
            raise MemoryVerifyError(
                f"entry {entry_id!r} is private to a different agent; "
                f"cannot propose verification"
            )

        # Mark it verified (idempotent). seal_note persists on the row.
        memory.mark_verified(
            entry_id=entry_id, verifier_id=verifier_id, seal_note=seal_note,
        )
        # Read-back confirmation: the verifier we wrote should be what
        # get_verifier returns. Catches storage-layer regressions.
        if memory.get_verifier(entry_id=entry_id) != verifier_id:
            raise MemoryVerifyError(
                "verification write succeeded but read-back returned a "
                "different verifier; registry consistency error"
            )

        return ToolResult(
            output={
                "entry_id": entry_id,
                "verified": True,
                "verifier_id": verifier_id,
                # The actual timestamp is in the consent row's
                # granted_at; the audit chain's timestamp field
                # captures it for chain readers.
                "seal_note": seal_note,
            },
            metadata={
                "entry_id": entry_id,
                "verifier_id": verifier_id,
                "seal_note_present": seal_note is not None,
                "audit_event_type": "memory_verified",
            },
            side_effect_summary=(
                f"verified entry {entry_id} by {verifier_id}"
                + (f" ({seal_note[:60]}...)" if seal_note and len(seal_note) > 60
                   else f" ({seal_note})" if seal_note else "")
            ),
        )
