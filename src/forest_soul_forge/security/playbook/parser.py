"""SOAR playbook YAML parser.

ADR-0066 T1 (B454). Takes a YAML body (or a dict) and produces a
PlaybookDef. Rejects any feature outside the v1 DSL subset with a
clear error so the operator knows exactly what needs to change.

Per ADR-0066 D7, Phase D closure requires the playbook library to
parse 100% clean. parse_playbooks_from_dir returns (parsed, failed)
rather than raising on the first bad file so the caller — the
engine's loader and section-01 of the diagnostic harness — can
surface the full punch list. This mirrors detection/parser.py's
parse_rules_from_dir exactly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from forest_soul_forge.security.playbook.events import (
    APPROVAL_DEFAULT_REQUIRED,
    PlaybookDef,
    PlaybookError,
    PlaybookStep,
    PlaybookTrigger,
    playbook_version_hash,
    severity_rank,
)


# Keys a step may carry. `requires_human_approval` is the per-step
# approval override; `args` is the invocation payload. Anything else
# — `if`, `condition`, `loop`, `foreach`, `on_failure` — is a
# control-flow feature the v1 DSL deliberately omits (ADR-0066 D1:
# composition over nesting). The parser rejects them so the operator
# sees the gap rather than relying on undefined behavior.
_STEP_ALLOWED_KEYS = frozenset({
    "id", "action", "args", "requires_human_approval",
})


def parse_playbook(
    playbook_body: str, *, source_path: str | None = None
) -> PlaybookDef:
    """Parse a YAML playbook body into a PlaybookDef.

    source_path is included in error messages for operator
    diagnostics (which file failed) — purely informational; the
    parser doesn't read from disk on its own.
    """
    if not isinstance(playbook_body, str) or not playbook_body.strip():
        raise PlaybookError(
            f"playbook body is empty"
            f"{' at ' + source_path if source_path else ''}"
        )

    try:
        doc = yaml.safe_load(playbook_body)
    except yaml.YAMLError as e:
        raise PlaybookError(
            f"YAML parse error"
            f"{' at ' + source_path if source_path else ''}: {e}"
        ) from e

    if not isinstance(doc, dict):
        raise PlaybookError(
            f"playbook body must parse to a mapping; got "
            f"{type(doc).__name__}"
            f"{' at ' + source_path if source_path else ''}"
        )

    return _parse_playbook_dict(doc, source_path)


def _parse_playbook_dict(
    doc: dict[str, Any], source_path: str | None
) -> PlaybookDef:
    where = f" ({source_path})" if source_path else ""

    playbook_id = doc.get("playbook_id") or doc.get("id")
    if not playbook_id or not isinstance(playbook_id, str):
        raise PlaybookError(f"playbook missing 'playbook_id'{where}")

    version = doc.get("version")
    # YAML happily parses an unquoted `version: 1` as an int — coerce
    # so the operator isn't forced to remember the quotes, but keep
    # the stored value a string (it pins the operator-authored
    # revision, not a number to do arithmetic on).
    if isinstance(version, (int, float)):
        version = str(version)
    if not version or not isinstance(version, str):
        raise PlaybookError(
            f"{playbook_id!r}: 'version' is required (string){where}"
        )

    trigger = _parse_trigger(playbook_id, doc.get("trigger"), where)
    approval_default, steps_auto_approved = _parse_approval(
        playbook_id, doc.get("approval"), where
    )
    steps = _parse_steps(
        playbook_id, doc.get("steps"), steps_auto_approved, where
    )

    # postconditions is optional; the only key v1 reads is
    # audit_event_type, which defaults to playbook_executed.
    postconditions = doc.get("postconditions") or {}
    if postconditions and not isinstance(postconditions, dict):
        raise PlaybookError(
            f"{playbook_id!r}: postconditions must be a mapping{where}"
        )
    audit_event_type = postconditions.get("audit_event_type") or "playbook_executed"
    if not isinstance(audit_event_type, str):
        raise PlaybookError(
            f"{playbook_id!r}: postconditions.audit_event_type must be "
            f"a string{where}"
        )

    # Every step in the auto-approve allowlist must name a real
    # step — a typo'd allowlist entry that silently does nothing is
    # a security footgun (operator believes a step is gated when it
    # is not, or vice versa).
    step_ids = {s.id for s in steps}
    for sid in steps_auto_approved:
        if sid not in step_ids:
            raise PlaybookError(
                f"{playbook_id!r}: approval.steps_auto_approved names "
                f"{sid!r}, which is not a declared step id "
                f"(declared: {sorted(step_ids)}){where}"
            )

    # Canonicalize the body for the playbook_version hash so
    # whitespace / key-order changes don't flip the version.
    canonical = yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)

    return PlaybookDef(
        playbook_id=playbook_id,
        version=version,
        playbook_version=playbook_version_hash(canonical),
        trigger=trigger,
        approval_default=approval_default,
        steps_auto_approved=steps_auto_approved,
        steps=steps,
        postcondition_audit_event_type=audit_event_type,
    )


def _parse_trigger(
    playbook_id: str, raw: Any, where: str
) -> PlaybookTrigger:
    if not isinstance(raw, dict):
        raise PlaybookError(
            f"{playbook_id!r}: 'trigger' must be a mapping{where}"
        )

    rule_ids = raw.get("detection_rule_ids")
    if not isinstance(rule_ids, list) or not rule_ids:
        raise PlaybookError(
            f"{playbook_id!r}: trigger.detection_rule_ids must be a "
            f"non-empty list{where}"
        )
    for rid in rule_ids:
        if not isinstance(rid, str) or not rid.strip():
            raise PlaybookError(
                f"{playbook_id!r}: trigger.detection_rule_ids entries "
                f"must be non-empty strings; got {rid!r}{where}"
            )

    min_severity = raw.get("min_severity")
    if not isinstance(min_severity, str):
        raise PlaybookError(
            f"{playbook_id!r}: trigger.min_severity is required "
            f"(string){where}"
        )
    # severity_rank raises PlaybookError on an unknown severity —
    # let it propagate; the message already names the allowed set.
    severity_rank(min_severity)

    cooldown = raw.get("cooldown_seconds")
    if cooldown is None:
        raise PlaybookError(
            f"{playbook_id!r}: trigger.cooldown_seconds is required "
            f"(ADR-0066 D4 — re-fire suppression is not optional){where}"
        )
    if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 0:
        raise PlaybookError(
            f"{playbook_id!r}: trigger.cooldown_seconds must be a "
            f"non-negative integer; got {cooldown!r}{where}"
        )

    return PlaybookTrigger(
        detection_rule_ids=tuple(rule_ids),
        min_severity=min_severity,
        cooldown_seconds=cooldown,
    )


def _parse_approval(
    playbook_id: str, raw: Any, where: str
) -> tuple[str, tuple[str, ...]]:
    """Returns (approval_default, steps_auto_approved)."""
    if raw is None:
        # No approval block → pure default-deny: every step gated.
        return (APPROVAL_DEFAULT_REQUIRED, ())
    if not isinstance(raw, dict):
        raise PlaybookError(
            f"{playbook_id!r}: 'approval' must be a mapping{where}"
        )

    default = raw.get("default") or APPROVAL_DEFAULT_REQUIRED
    if not isinstance(default, str):
        raise PlaybookError(
            f"{playbook_id!r}: approval.default must be a string{where}"
        )

    auto = raw.get("steps_auto_approved") or []
    if not isinstance(auto, list):
        raise PlaybookError(
            f"{playbook_id!r}: approval.steps_auto_approved must be a "
            f"list{where}"
        )
    for sid in auto:
        if not isinstance(sid, str) or not sid.strip():
            raise PlaybookError(
                f"{playbook_id!r}: approval.steps_auto_approved entries "
                f"must be non-empty strings; got {sid!r}{where}"
            )

    return (default, tuple(auto))


def _parse_steps(
    playbook_id: str,
    raw: Any,
    steps_auto_approved: tuple[str, ...],
    where: str,
) -> tuple[PlaybookStep, ...]:
    if not isinstance(raw, list) or not raw:
        raise PlaybookError(
            f"{playbook_id!r}: 'steps' must be a non-empty list{where}"
        )

    auto_set = set(steps_auto_approved)
    steps: list[PlaybookStep] = []
    for idx, raw_step in enumerate(raw):
        if not isinstance(raw_step, dict):
            raise PlaybookError(
                f"{playbook_id!r}: step #{idx} must be a mapping; got "
                f"{type(raw_step).__name__}{where}"
            )

        # Reject control-flow keys explicitly. ADR-0066 D1: no
        # branches, no loops in v1 — the operator composes multiple
        # playbooks instead. A silent ignore would let an operator
        # believe a guard is in place when it is not.
        unknown = set(raw_step) - _STEP_ALLOWED_KEYS
        if unknown:
            raise PlaybookError(
                f"{playbook_id!r}: step #{idx} has unsupported key(s) "
                f"{sorted(unknown)}. The v1 DSL is loop-free and "
                f"branch-free (ADR-0066 D1); allowed step keys: "
                f"{sorted(_STEP_ALLOWED_KEYS)}{where}"
            )

        step_id = raw_step.get("id")
        if not step_id or not isinstance(step_id, str):
            raise PlaybookError(
                f"{playbook_id!r}: step #{idx} missing 'id'{where}"
            )

        action = raw_step.get("action")
        if not action or not isinstance(action, str):
            raise PlaybookError(
                f"{playbook_id!r}: step {step_id!r} missing 'action' "
                f"(a catalog tool or skill name){where}"
            )

        args = raw_step.get("args") or {}
        if not isinstance(args, dict):
            raise PlaybookError(
                f"{playbook_id!r}: step {step_id!r}: args must be a "
                f"mapping; got {type(args).__name__}{where}"
            )

        requires_approval = _resolve_step_approval(
            playbook_id, step_id, raw_step, auto_set, where
        )

        steps.append(PlaybookStep(
            id=step_id,
            action=action,
            args=dict(args),
            requires_approval=requires_approval,
        ))

    return tuple(steps)


def _resolve_step_approval(
    playbook_id: str,
    step_id: str,
    raw_step: dict[str, Any],
    auto_set: set[str],
    where: str,
) -> bool:
    """Resolve a step's approval disposition at parse time.

    ADR-0066 D2 — default-deny: a step requires approval UNLESS the
    operator explicitly opts it out. There are exactly two opt-out
    channels, both explicit:
      1. listing the step id in approval.steps_auto_approved, or
      2. setting `requires_human_approval: false` on the step.

    A per-step `requires_human_approval: true` always wins (it can
    escalate but never the reverse). A step that is BOTH in the
    allowlist AND carries `requires_human_approval: true` is a
    contradiction — the operator's two declarations disagree — so
    the parser rejects it rather than silently picking one.
    """
    has_explicit = "requires_human_approval" in raw_step
    in_allowlist = step_id in auto_set

    if has_explicit:
        explicit = raw_step["requires_human_approval"]
        if not isinstance(explicit, bool):
            raise PlaybookError(
                f"{playbook_id!r}: step {step_id!r}: "
                f"requires_human_approval must be a boolean; got "
                f"{explicit!r}{where}"
            )
        if explicit and in_allowlist:
            raise PlaybookError(
                f"{playbook_id!r}: step {step_id!r} is in "
                f"approval.steps_auto_approved but also sets "
                f"requires_human_approval: true — the two "
                f"declarations contradict. Pick one{where}"
            )
        return explicit

    if in_allowlist:
        return False

    # Default-deny.
    return True


def parse_playbooks_from_dir(
    directory: Path,
) -> tuple[list[PlaybookDef], list[tuple[Path, PlaybookError]]]:
    """Walk a directory of *.yml files and parse each. Returns
    (parsed, failed) — failed entries carry the path + error so the
    caller can surface them as a punch list rather than crashing on
    the first bad playbook.

    Mirrors detection/parser.py:parse_rules_from_dir. The engine's
    loader and section-01 of the diagnostic harness both call this;
    Phase D closure (ADR-0066 D7) requires zero failures.
    """
    parsed: list[PlaybookDef] = []
    failed: list[tuple[Path, PlaybookError]] = []

    if not directory.exists():
        return (parsed, failed)

    for path in sorted(directory.glob("*.yml")):
        body = path.read_text(encoding="utf-8")
        try:
            parsed.append(parse_playbook(body, source_path=str(path)))
        except PlaybookError as e:
            failed.append((path, e))

    # Duplicate playbook_id detection — an operator quality footgun.
    # Two playbooks with the same id would both fire and the
    # cooldown fingerprint would collide.
    seen: dict[str, Path] = {}
    for pb in parsed:
        if pb.playbook_id in seen:
            failed.append((
                Path(""),  # synthetic path for the duplicate signal
                PlaybookError(
                    f"duplicate playbook_id {pb.playbook_id!r}: appears "
                    f"in multiple files"
                ),
            ))
        seen[pb.playbook_id] = seen.get(pb.playbook_id) or Path("(unknown)")
    return (parsed, failed)
