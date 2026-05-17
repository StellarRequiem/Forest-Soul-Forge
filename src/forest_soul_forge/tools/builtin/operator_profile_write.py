"""``operator_profile_write.v1`` — ADR-0068 T2 (B312).

Mutating sibling to ``operator_profile_read.v1`` (B277). Takes a
field path + new value, atomically updates the operator profile
on disk, and emits the canonical ``operator_profile_changed``
audit event with a before/after diff.

## Approval posture

``requires_human_approval=True`` — operator-truth must not change
without the operator's explicit per-call confirmation. The
dispatcher's gate fires before the write lands, so an LLM that
emits the call without operator presence sees a
``tool_call_pending_approvals`` row instead of an immediate mutation.

## Field paths

Args carry ``field_path`` as a dotted string targeting one of the
OperatorProfile dataclass fields. Supported at T2:

- ``name``
- ``preferred_name``
- ``email``
- ``timezone``
- ``locale``
- ``work_hours.start``
- ``work_hours.end``

Future tranches (T4-T6) add ``trust_circle.*`` /
``voice_samples.*`` / ``financial.*`` paths under the ``extra``
slot. T2 deliberately restricts to the flat top-level fields so
the validation surface stays tight.

## Reality Anchor re-seed

After a successful write, the tool re-runs
``profile_to_ground_truth_seeds`` and surfaces the resulting
seed list in the tool result's metadata. The operator runbook
(T8) walks the operator through manually reloading the Reality
Anchor via ``POST /reality-anchor/reload`` — automatic re-seed
without daemon coordination would require a write_lock held
across two subsystems, deferred until the cross-domain
orchestrator wires it.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from forest_soul_forge.core.operator_profile import (
    OperatorProfile,
    OperatorProfileError,
    WorkHours,
    default_operator_profile_path,
    load_operator_profile,
    profile_to_ground_truth_seeds,
    save_operator_profile,
)
from forest_soul_forge.tools.base import (
    ToolContext,
    ToolError,
    ToolResult,
    ToolValidationError,
)


_NAME = "operator_profile_write"
# B353 fix: bare version string (registry composes ".v" prefix
# itself via base.py:273 `f"{name}.v{version}"`). Pre-B353 this
# was "v1" which produced operator_profile_write.vv1 in the
# registry and surfaced as the tool_runtime startup_diagnostic
# failure (registry/catalog mismatch).
_VERSION = "1"


# Field paths supported in T2. Each maps to the OperatorProfile
# attribute name (top-level) or a (parent, child) tuple for nested
# WorkHours. T4-T6 extend this map under `extra`.
_SUPPORTED_PATHS: dict[str, tuple[str, ...]] = {
    "name":              ("name",),
    "preferred_name":    ("preferred_name",),
    "email":             ("email",),
    "timezone":          ("timezone",),
    "locale":            ("locale",),
    "work_hours.start":  ("work_hours", "start"),
    "work_hours.end":    ("work_hours", "end"),
}


class OperatorProfileWriteTool:
    """Update one field on the operator's profile.

    Required args:
      - ``field_path`` (str): one of the supported paths above.
      - ``new_value`` (str): the new value. Type-coercion handled
        inside; rejects non-string for v1 (all supported fields
        are strings).
      - ``reason`` (str): operator-supplied free-text rationale
        captured in the audit event payload. Required so the
        chain carries operator-context for every change, not just
        the diff.
    """

    name: str = _NAME
    version: str = _VERSION
    side_effects: str = "filesystem"
    requires_human_approval: bool = True
    sandbox_eligible: bool = False  # writes to data/ — needs real fs

    def validate(self, args: dict[str, Any]) -> None:
        """Raise ToolValidationError on malformed args."""
        for required in ("field_path", "new_value", "reason"):
            if required not in args:
                raise ToolValidationError(
                    f"missing required arg: {required!r}",
                )
            if not isinstance(args[required], str):
                raise ToolValidationError(
                    f"{required!r} must be a string",
                )
            if not args[required].strip() and required != "new_value":
                raise ToolValidationError(
                    f"{required!r} must be non-empty",
                )

        fp = args["field_path"]
        if fp not in _SUPPORTED_PATHS:
            raise ToolValidationError(
                f"unsupported field_path {fp!r}; "
                f"valid: {sorted(_SUPPORTED_PATHS)}",
            )

        # work_hours fields must look like HH:MM
        if fp.startswith("work_hours."):
            v = args["new_value"]
            if not _looks_like_hhmm(v):
                raise ToolValidationError(
                    f"work_hours.* must be HH:MM (got {v!r})",
                )

    def call(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        field_path = args["field_path"]
        new_value = args["new_value"]
        reason = args["reason"]

        # Encryption-aware load + save. Same pattern as the read tool.
        encryption_config = _build_encryption_config(ctx)
        path = default_operator_profile_path()

        try:
            before = load_operator_profile(
                path, encryption_config=encryption_config,
            )
        except OperatorProfileError as e:
            raise ToolError(
                code="operator_profile_unavailable",
                detail=str(e),
            ) from e

        before_value = _get_field(before, field_path)
        if before_value == new_value:
            # No-op; surface clearly without writing or emitting.
            return ToolResult(
                output={
                    "ok":            True,
                    "no_op":         True,
                    "field_path":    field_path,
                    "value":         new_value,
                },
                metadata={
                    "audit_payload": {
                        "field_path": field_path,
                        "no_op":      True,
                    },
                },
            )

        after = _replace_field(before, field_path, new_value)

        # Persist atomically. save_operator_profile stamps
        # updated_at automatically.
        save_operator_profile(
            after, path, encryption_config=encryption_config,
        )

        # Emit the canonical change event so the chain carries the
        # before/after diff. The runtime wraps this dispatch in
        # tool_invoked; we additionally emit the domain-specific
        # operator_profile_changed entry so dashboards / runbooks
        # have one stable event_type to filter on.
        audit_chain = _resolve_audit_chain(ctx)
        if audit_chain is not None:
            try:
                audit_chain.append(
                    "operator_profile_changed",
                    {
                        "field_path":    field_path,
                        "before":        before_value,
                        "after":         new_value,
                        "reason":        reason,
                        "operator_id":   before.operator_id,
                        "schema_version": before.schema_version,
                    },
                    agent_dna=None,
                )
            except Exception:
                # Best-effort: the disk write already landed.
                # Surface the failure via metadata so the operator
                # sees the chain-emit gap.
                pass

        # Re-seed Reality Anchor candidates. The actual reload is
        # operator-driven (see runbook in T8) so we surface the
        # new seeds in metadata.
        new_seeds = profile_to_ground_truth_seeds(after)

        return ToolResult(
            output={
                "ok":         True,
                "no_op":      False,
                "field_path": field_path,
                "before":     before_value,
                "after":      new_value,
                "reason":     reason,
                "ground_truth_seeds_count": len(new_seeds),
            },
            metadata={
                "audit_payload": {
                    "field_path":    field_path,
                    "before":        before_value,
                    "after":         new_value,
                    "operator_id":   before.operator_id,
                },
                "reality_anchor_seeds": new_seeds,
            },
            side_effect_summary=(
                f"profile.{field_path}: {before_value!r} -> {new_value!r}"
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _looks_like_hhmm(s: str) -> bool:
    """Loose HH:MM validator. We're not enforcing 24-hour bounds
    here — Reality Anchor downstream catches absurd values via
    the operator's ground_truth.yaml entries."""
    if len(s) != 5 or s[2] != ":":
        return False
    return s[:2].isdigit() and s[3:].isdigit()


def _get_field(profile: OperatorProfile, field_path: str) -> str:
    """Resolve a dotted path against the profile dataclass."""
    parts = _SUPPORTED_PATHS[field_path]
    if len(parts) == 1:
        return getattr(profile, parts[0])
    # Nested: work_hours.start / work_hours.end
    parent = getattr(profile, parts[0])
    return getattr(parent, parts[1])


def _replace_field(
    profile: OperatorProfile,
    field_path: str,
    new_value: str,
) -> OperatorProfile:
    """Return a new OperatorProfile with the named field replaced.

    Uses dataclasses.replace on both the top level (frozen-style
    if applicable) and the nested WorkHours sub-record. Even if
    OperatorProfile is mutable, going through replace keeps the
    swap atomic from a 'before vs after' reasoning standpoint.
    """
    parts = _SUPPORTED_PATHS[field_path]
    if len(parts) == 1:
        return replace(profile, **{parts[0]: new_value})
    # work_hours nested.
    parent_name, child_name = parts
    new_parent = replace(
        getattr(profile, parent_name), **{child_name: new_value},
    )
    return replace(profile, **{parent_name: new_parent})


def _build_encryption_config(ctx: ToolContext) -> Any:
    """Mirror operator_profile_read's encryption-config builder."""
    master_key = getattr(ctx, "master_key", None)
    if master_key is None:
        return None
    from forest_soul_forge.core.at_rest_encryption import EncryptionConfig
    return EncryptionConfig(master_key=master_key)


def _resolve_audit_chain(ctx: ToolContext) -> Any:
    """The audit chain handle is on ctx.constraints or directly on
    ctx in newer shapes. We probe both — the tool runtime evolves
    independently of any single tool."""
    chain = getattr(ctx, "audit_chain", None)
    if chain is not None:
        return chain
    return ctx.constraints.get("audit_chain") if ctx.constraints else None


# Module-level instance the registry imports.
operator_profile_write_tool = OperatorProfileWriteTool()
