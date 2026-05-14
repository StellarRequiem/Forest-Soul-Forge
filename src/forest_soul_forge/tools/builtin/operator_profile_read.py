"""``operator_profile_read.v1`` — ADR-0068 T1 (B277).

Read-only builtin tool. Returns the parsed OperatorProfile as a
dict so any agent with the constitution permission can ask
"who is the operator?" without re-asking the human every
conversation.

Side-effects: ``read_only`` — no network, no filesystem writes,
just one read of the profile YAML.

Sandbox-eligible: yes (ADR-0051 T3) — no live registry / write_lock
needs.

## Why this tool is the central operator-context primitive

Ten domains each need to know who the operator is. Instead of
each domain re-implementing profile loading + caching, every
agent calls this tool and gets the canonical answer. The cache
lives in the dispatcher (one read per session) so repeated calls
within a dispatch chain don't re-read the file.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from forest_soul_forge.core.operator_profile import (
    OperatorProfile,
    OperatorProfileError,
    default_operator_profile_path,
    load_operator_profile,
)
from forest_soul_forge.tools.base import (
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
)


_NAME = "operator_profile_read"
_VERSION = "v1"


class OperatorProfileReadTool:
    """Return the operator's profile as a dict.

    No arguments. The tool reads from the canonical path
    (``data/operator/profile.yaml`` or its .enc variant under
    encryption).
    """

    name: str = _NAME
    version: str = _VERSION
    side_effects: str = "read_only"
    requires_human_approval: bool = False
    sandbox_eligible: bool = True

    def call(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        del args  # no args at T1; future T2 may add a field selector

        # Encryption-aware. The dispatcher's master_key flows through
        # ctx.constraints when at-rest encryption is on; build an
        # EncryptionConfig and thread it to the loader.
        encryption_config = None
        master_key = getattr(ctx, "master_key", None)
        if master_key is None:
            # Older ToolContext shapes don't carry master_key
            # directly; fall back to plaintext loader. The loader
            # itself raises OperatorProfileError if the file is at
            # the .enc path with no config supplied.
            pass
        else:
            from forest_soul_forge.core.at_rest_encryption import (
                EncryptionConfig,
            )
            encryption_config = EncryptionConfig(master_key=master_key)

        try:
            profile = load_operator_profile(
                default_operator_profile_path(),
                encryption_config=encryption_config,
            )
        except OperatorProfileError as e:
            raise ToolError(
                code="operator_profile_unavailable",
                detail=str(e),
            ) from e

        result = _profile_to_dict(profile)
        return ToolResult(
            success=True,
            result=result,
            audit_payload={
                "operator_id": result["operator_id"],
                "schema_version": result["schema_version"],
            },
        )


def _profile_to_dict(profile: OperatorProfile) -> dict[str, Any]:
    """Flatten OperatorProfile to a plain dict the agent's LLM
    consumes. Nested work_hours becomes a sub-dict.

    We deliberately do NOT include created_at / updated_at in the
    agent-facing payload — those are operational metadata, not
    operator-identity facts. The audit chain captures access; the
    agent doesn't need to reason about when the profile was
    edited.
    """
    return {
        "schema_version": profile.schema_version,
        "operator_id": profile.operator_id,
        "name": profile.name,
        "preferred_name": profile.preferred_name,
        "email": profile.email,
        "timezone": profile.timezone,
        "locale": profile.locale,
        "work_hours": {
            "start": profile.work_hours.start,
            "end": profile.work_hours.end,
        },
        "extra": dict(profile.extra),
    }


# Module-level instance the registry imports.
operator_profile_read_tool = OperatorProfileReadTool()
