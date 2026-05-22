"""PlaybookDef + PlaybookStep + PlaybookTrigger — the dataclass surface.

ADR-0066 T1 (B454). The parser (parser.py) produces PlaybookDef
instances; the engine (T2) matches each `detection_fired` event
against every playbook's trigger, applies cooldown, resolves the
per-step approval disposition, and emits one `playbook_executed`
audit chain entry per fired playbook.

The DSL is intentionally narrow (ADR-0066 D1): each step is one
catalog-defined tool or skill invocation. No conditional branches,
no loops. If the operator needs branching, the right shape is
multiple playbooks with different triggers (composition over
nesting) — the parser rejects loop/condition keys with a clear
error so the gap is visible rather than silently ignored.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


class PlaybookError(ValueError):
    """Raised by the parser when a playbook violates the ADR-0066
    DSL contract. Distinct from ValueError so callers can catch
    this class specifically without swallowing unrelated errors
    from yaml/hashlib/etc."""


# Severity vocabulary — mirrors the detection substrate's
# _VALID_LEVELS (security/detection/events.py). A playbook trigger's
# `min_severity` is compared against the firing detection's severity
# using this rank; a playbook with min_severity=high fires on high
# and critical detections, not on medium.
_SEVERITY_RANK: dict[str, int] = {
    "informational": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

# The only approval default v1 supports. ADR-0066 D2 inverts the
# posture: approval is required UNLESS the playbook explicitly
# auto-approves a step. A future ADR could add an opt-out default,
# but it would have to argue past the default-deny posture first.
APPROVAL_DEFAULT_REQUIRED = "required_human"


def severity_rank(level: str) -> int:
    """Map a severity string to its comparable rank. Unknown
    severities raise — a typo in `min_severity` must not silently
    rank as 0 (which would make the playbook fire on everything)."""
    try:
        return _SEVERITY_RANK[level]
    except KeyError:
        raise PlaybookError(
            f"unknown severity {level!r}; allowed: "
            f"{sorted(_SEVERITY_RANK, key=_SEVERITY_RANK.get)}"
        ) from None


def playbook_version_hash(playbook_body: str) -> str:
    """sha256 of the playbook's canonicalized YAML body (hex).

    Recorded as `playbook_version` on every `playbook_executed`
    audit chain event (ADR-0066 D5) so chain history pins the exact
    playbook that ran. The body should be the canonicalized YAML —
    the parser re-emits via yaml.safe_dump(sort_keys=True) before
    hashing so whitespace / key-order changes don't flip the
    version, exactly as detection's rule_version_hash does."""
    return hashlib.sha256(playbook_body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PlaybookStep:
    """One step — a single catalog tool or skill invocation.

    `requires_approval` is resolved at parse time from the playbook's
    `approval.default`, the `approval.steps_auto_approved` allowlist,
    and any per-step `requires_human_approval` override. The engine
    never re-derives it; the parsed value is authoritative.
    """

    id: str
    action: str               # catalog tool/skill name (version-free;
                              # resolved against the catalog at dispatch)
    args: dict[str, Any]      # invocation args; may carry ${...} refs
    requires_approval: bool   # resolved disposition (see above)


@dataclass(frozen=True)
class PlaybookTrigger:
    """When a playbook fires. ADR-0066: a detection matches the
    trigger when its rule_id is in `detection_rule_ids` AND its
    severity is at or above `min_severity`."""

    detection_rule_ids: tuple[str, ...]
    min_severity: str
    cooldown_seconds: int

    def matches(self, *, rule_id: str, severity: str) -> bool:
        """True iff a detection with this rule_id + severity should
        fire this playbook. Cooldown is NOT considered here — that
        is per-(playbook, rule, target) state the engine owns."""
        if rule_id not in self.detection_rule_ids:
            return False
        return severity_rank(severity) >= severity_rank(self.min_severity)


@dataclass(frozen=True)
class PlaybookDef:
    """A parsed SOAR playbook.

    Immutable; the parser builds and the engine reads. Subclassing
    is forbidden — the playbook semantic is the field set here. New
    semantics land via parser support + new fields, never via
    behavioral subclassing (same posture as DetectionRule).
    """

    playbook_id: str
    version: str                       # operator-authored version string
    playbook_version: str              # sha256 of canonical body (D5)
    trigger: PlaybookTrigger
    approval_default: str              # APPROVAL_DEFAULT_REQUIRED
    steps_auto_approved: tuple[str, ...]
    steps: tuple[PlaybookStep, ...]
    postcondition_audit_event_type: str

    def __post_init__(self) -> None:
        if not self.steps:
            raise PlaybookError(
                f"playbook {self.playbook_id!r}: at least one step is required"
            )
        if self.approval_default != APPROVAL_DEFAULT_REQUIRED:
            raise PlaybookError(
                f"playbook {self.playbook_id!r}: approval.default must be "
                f"{APPROVAL_DEFAULT_REQUIRED!r} (the v1 default-deny posture, "
                f"ADR-0066 D2); got {self.approval_default!r}"
            )
        # Step ids must be unique — the cooldown fingerprint, the
        # auto-approve allowlist, and the playbook_executed step
        # history all key on step id.
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise PlaybookError(
                    f"playbook {self.playbook_id!r}: duplicate step id "
                    f"{step.id!r}"
                )
            seen.add(step.id)

    @property
    def auto_approved_step_ids(self) -> tuple[str, ...]:
        """Step ids the engine will dispatch without operator
        approval. Derived from the resolved per-step disposition,
        not from the raw allowlist — a per-step
        `requires_human_approval: true` escalates a listed step
        back to approval-required."""
        return tuple(s.id for s in self.steps if not s.requires_approval)
