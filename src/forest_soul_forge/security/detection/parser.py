"""Sigma-subset YAML parser.

ADR-0065 T1 (B389). Takes a YAML body (or a dict) and produces a
DetectionRule. Rejects any feature outside the subset with a
clear error so the operator knows exactly what needs to change.

Per ADR-0065 D7: parser refuses to run if any rule fails. The
engine (T2) treats parser failure as a halt — silent skip would
hide drift.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from forest_soul_forge.security.detection.events import (
    DetectionRule,
    DetectionRuleError,
    rule_version_hash,
)


# Reserved keys in the detection block — anything else is a
# selection name. Sigma's `condition` is the boolean expression;
# `timeframe`/`fields`/`falsepositives` are documentation/Sigma
# extensions we don't yet support (parser rejects them so the
# operator sees the gap).
_DETECTION_RESERVED = frozenset({"condition", "timeframe"})

# Sigma supports field modifiers (`fieldname|contains: ...`).
# The T1 subset is equality-only; if any field name contains '|'
# the parser rejects with a clear error pointing at the modifier.
_MODIFIER_MARKER = "|"


def parse_rule(rule_body: str, *, source_path: str | None = None) -> DetectionRule:
    """Parse a YAML rule body into a DetectionRule.

    source_path is included in error messages for operator
    diagnostics (which file failed) — purely informational; the
    parser doesn't read from disk on its own.
    """
    if not isinstance(rule_body, str) or not rule_body.strip():
        raise DetectionRuleError(
            f"rule body is empty{' at ' + source_path if source_path else ''}"
        )

    try:
        doc = yaml.safe_load(rule_body)
    except yaml.YAMLError as e:
        raise DetectionRuleError(
            f"YAML parse error{' at ' + source_path if source_path else ''}: {e}"
        ) from e

    if not isinstance(doc, dict):
        raise DetectionRuleError(
            f"rule body must parse to a mapping; got {type(doc).__name__}"
            f"{' at ' + source_path if source_path else ''}"
        )

    return _parse_rule_dict(doc, rule_body, source_path)


def _parse_rule_dict(
    doc: dict[str, Any],
    rule_body: str,
    source_path: str | None,
) -> DetectionRule:
    where = f" ({source_path})" if source_path else ""

    # Required top-level keys.
    rule_id = doc.get("id") or doc.get("rule_id") or doc.get("name")
    if not rule_id or not isinstance(rule_id, str):
        raise DetectionRuleError(
            f"rule missing 'id' (or 'rule_id'/'name'){where}"
        )

    title = doc.get("title") or rule_id
    description = doc.get("description") or ""
    if not isinstance(title, str) or not isinstance(description, str):
        raise DetectionRuleError(
            f"{rule_id!r}: title + description must be strings{where}"
        )

    level = doc.get("level")
    if level is None:
        raise DetectionRuleError(f"{rule_id!r}: 'level' is required{where}")
    if not isinstance(level, str):
        raise DetectionRuleError(
            f"{rule_id!r}: level must be a string; got "
            f"{type(level).__name__}{where}"
        )

    # Tags — mandatory per ADR-0065 D3.
    tags_raw = doc.get("tags")
    if not tags_raw:
        raise DetectionRuleError(
            f"{rule_id!r}: 'tags' is required (ADR-0065 D3); use "
            f"['attack.unknown'] if the technique is not yet "
            f"identified{where}"
        )
    if not isinstance(tags_raw, list):
        raise DetectionRuleError(
            f"{rule_id!r}: tags must be a list; got "
            f"{type(tags_raw).__name__}{where}"
        )
    for t in tags_raw:
        if not isinstance(t, str) or not t.strip():
            raise DetectionRuleError(
                f"{rule_id!r}: tags must be non-empty strings; got {t!r}{where}"
            )
    tags = tuple(t.strip() for t in tags_raw)

    # logsource — optional; both fields optional.
    logsource = doc.get("logsource") or {}
    if logsource and not isinstance(logsource, dict):
        raise DetectionRuleError(
            f"{rule_id!r}: logsource must be a mapping{where}"
        )
    ls_source = logsource.get("source") or logsource.get("product")
    ls_event_type = logsource.get("event_type") or logsource.get("category")
    if ls_source is not None and not isinstance(ls_source, str):
        raise DetectionRuleError(
            f"{rule_id!r}: logsource.source must be a string{where}"
        )
    if ls_event_type is not None and not isinstance(ls_event_type, str):
        raise DetectionRuleError(
            f"{rule_id!r}: logsource.event_type must be a string{where}"
        )

    # Detection block — required.
    detection = doc.get("detection")
    if not isinstance(detection, dict):
        raise DetectionRuleError(
            f"{rule_id!r}: 'detection' must be a mapping{where}"
        )

    condition = detection.get("condition")
    if not isinstance(condition, str) or not condition.strip():
        raise DetectionRuleError(
            f"{rule_id!r}: detection.condition is required (string){where}"
        )

    # Reject Sigma extensions we don't support so the operator
    # sees the gap rather than relying on undefined behavior.
    if "timeframe" in detection:
        raise DetectionRuleError(
            f"{rule_id!r}: 'timeframe' is unsupported in the T1 subset "
            f"(time-windowed correlation lands in a future ADR){where}"
        )

    selections: dict[str, dict[str, Any]] = {}
    for key, value in detection.items():
        if key in _DETECTION_RESERVED:
            continue
        if not isinstance(value, dict):
            raise DetectionRuleError(
                f"{rule_id!r}: selection {key!r} must be a mapping "
                f"of field -> expected_value; got "
                f"{type(value).__name__}{where}"
            )
        for fname in value:
            if _MODIFIER_MARKER in fname:
                raise DetectionRuleError(
                    f"{rule_id!r}: field modifiers are unsupported in "
                    f"the T1 subset. Got {fname!r} in selection "
                    f"{key!r}. Equality-only; rewrite as a direct "
                    f"field match{where}"
                )
        selections[key] = dict(value)

    if not selections:
        raise DetectionRuleError(
            f"{rule_id!r}: at least one selection is required "
            f"(detection block has only 'condition' key){where}"
        )

    # Canonicalize the rule body for the rule_version hash so
    # whitespace/key-order changes don't flip the version.
    canonical = yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
    version = rule_version_hash(canonical)

    return DetectionRule(
        rule_id=rule_id,
        title=title,
        description=description,
        rule_version=version,
        level=level,
        tags=tags,
        logsource_source=ls_source,
        logsource_event_type=ls_event_type,
        selections=selections,
        condition=condition,
    )


def parse_rules_from_dir(directory: Path) -> tuple[list[DetectionRule], list[tuple[Path, DetectionRuleError]]]:
    """Walk a directory of *.yml files and parse each. Returns
    (parsed, failed) — failed entries carry the path + error so
    the caller can surface them as a punch list rather than
    crashing on the first bad rule.

    Per ADR-0065 D7, the daemon's engine refuses to run if any
    rule fails to parse. Returning failures rather than raising
    lets the lifespan caller report ALL failures + halt; section-01
    (harness) does the same scan at static-config time so the
    operator gets the full punch list before runtime.
    """
    parsed: list[DetectionRule] = []
    failed: list[tuple[Path, DetectionRuleError]] = []

    if not directory.exists():
        return (parsed, failed)

    for path in sorted(directory.glob("*.yml")):
        body = path.read_text(encoding="utf-8")
        try:
            parsed.append(parse_rule(body, source_path=str(path)))
        except DetectionRuleError as e:
            failed.append((path, e))

    # Detect duplicate rule_ids — operator quality footgun.
    seen: dict[str, Path] = {}
    for rule in parsed:
        prior = seen.get(rule.rule_id)
        if prior is not None:
            failed.append((
                Path(""),  # synthetic path for the duplicate signal
                DetectionRuleError(
                    f"duplicate rule_id {rule.rule_id!r}: appears in "
                    f"multiple files; second occurrence ignored"
                ),
            ))
        seen[rule.rule_id] = prior or Path("(unknown)")
    return (parsed, failed)
