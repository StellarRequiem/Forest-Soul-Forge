"""Behavior provenance substrate — ADR-0072 T1 (B290).

Loads + validates the two operator-mutable rule files:
  - data/operator/preferences.yaml  (operator-edited)
  - data/learned_rules.yaml          (agent-edited, RA-gated)

Plus the precedence-ordering helper that says "given a conflict
between layers, which one wins."

## Surface

  - :class:`Preference` — one operator preference entry
  - :class:`LearnedRule` — one auto-edited learned rule entry
  - :class:`PreferencesConfig` — full preferences.yaml
  - :class:`LearnedRulesConfig` — full learned_rules.yaml
  - :func:`load_preferences(path=None)` — read + validate
  - :func:`load_learned_rules(path=None)` — read + validate
  - :func:`save_preferences(...)` + :func:`save_learned_rules(...)`
    — atomic writes with audit-event-ready delta JSON
  - :func:`compute_behavior_change_delta(before, after)` — diff
    two configs for the behavior_change audit event_data payload

## Why pure-function

T1 ships the data layer + delta computation only. The cron that
runs Reality Anchor against pending rules (T3), the CLI surface
(T2), and the orchestrator integration (T4) all build on this.
Keeping T1 pure-function-shaped makes each downstream consumer
testable in isolation against fake configs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml


PREFERENCES_DEFAULT_PATH = Path("data/operator/preferences.yaml")
LEARNED_RULES_DEFAULT_PATH = Path("data/learned_rules.yaml")

PREFERENCES_ENV = "FSF_PREFERENCES_PATH"
LEARNED_RULES_ENV = "FSF_LEARNED_RULES_PATH"

SCHEMA_VERSION_PREFERENCES = 1
SCHEMA_VERSION_LEARNED_RULES = 1


# ADR-0072 D1 — strict precedence ordering. Higher number wins.
# Helper consumers use this when resolving a conflict across
# layers (e.g., orchestrator routing has BOTH a preference saying
# "route X to D2" AND a learned rule saying "route X to D7").
PRECEDENCE: dict[str, int] = {
    "hardcoded_handoff":   1000,
    "constitutional":       800,
    "preference":           400,
    "learned":              100,
}


class BehaviorProvenanceError(RuntimeError):
    """Raised on hard-fatal loader problems (top-level not a mapping,
    schema_version mismatch). Per-entry problems surface as soft
    errors in the load tuple."""


@dataclass(frozen=True)
class Preference:
    """One operator-edited preference.

    A preference is an operator-asserted bias that influences
    decisions but DOES NOT override constitutional policy. Example:
    ``id="orchestrator.route.draft_to_d7"`` saying "when ambiguous
    between D7 Content Studio and D10 Research Lab, prefer D7."
    """
    id: str
    statement: str
    weight: float  # in [0.0, 1.0]; how strongly to bias
    domain: str    # which subsystem reads this (e.g. "orchestrator")
    created_at: str  # RFC 3339 UTC
    updated_at: str


@dataclass(frozen=True)
class LearnedRule:
    """One auto-edited learned rule.

    Rules land in ``pending_activation`` until Reality Anchor (the
    nightly cron from T3) verifies the rule's text doesn't
    contradict operator-asserted ground truth.
    """
    id: str
    statement: str
    weight: float
    domain: str
    proposer_agent_dna: str  # which agent emitted this rule
    created_at: str
    # 'pending_activation' until RA verifies; 'active' once
    # verified; 'refused' if RA returned a contradiction.
    status: str
    verification_verdict: Optional[str] = None  # RA verdict text
    verification_reason: Optional[str] = None   # RA reason text


@dataclass(frozen=True)
class PreferencesConfig:
    """The full preferences.yaml contents."""
    schema_version: int
    preferences: tuple[Preference, ...]


@dataclass(frozen=True)
class LearnedRulesConfig:
    """The full learned_rules.yaml contents. Split into two buckets
    per ADR-0072 D2: pending_activation (awaiting RA verification)
    and active (verified + dispatchable)."""
    schema_version: int
    pending_activation: tuple[LearnedRule, ...]
    active: tuple[LearnedRule, ...]


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_preferences(
    path: Optional[Path] = None,
) -> tuple[PreferencesConfig, list[str]]:
    """Read + validate preferences.yaml.

    Returns ``(config, errors)``. Missing file is benign: returns
    an empty config + a single info note. Per-entry problems
    surface as soft errors. Structural failures raise
    :class:`BehaviorProvenanceError`.
    """
    import os as _os
    resolved = (
        path if path is not None
        else Path(_os.environ.get(PREFERENCES_ENV, str(PREFERENCES_DEFAULT_PATH)))
    )

    errors: list[str] = []
    if not resolved.exists():
        errors.append(
            f"preferences file not found at {resolved}; "
            f"orchestrator will resolve without operator preferences"
        )
        return PreferencesConfig(
            schema_version=SCHEMA_VERSION_PREFERENCES,
            preferences=(),
        ), errors

    raw = _safe_load_yaml(resolved)
    sv = raw.get("schema_version")
    if sv != SCHEMA_VERSION_PREFERENCES:
        raise BehaviorProvenanceError(
            f"{resolved}: schema_version {sv!r} not supported "
            f"(expected {SCHEMA_VERSION_PREFERENCES})"
        )

    entries = []
    raw_prefs = raw.get("preferences") or []
    if not isinstance(raw_prefs, list):
        errors.append("preferences must be a list; ignoring")
        raw_prefs = []
    seen_ids: set[str] = set()
    for idx, raw_pref in enumerate(raw_prefs):
        pref, item_errors = _parse_preference(raw_pref, idx)
        errors.extend(item_errors)
        if pref is None:
            continue
        if pref.id in seen_ids:
            errors.append(
                f"duplicate preference id {pref.id!r} at index {idx}; "
                f"first kept"
            )
            continue
        seen_ids.add(pref.id)
        entries.append(pref)

    return PreferencesConfig(
        schema_version=int(sv),
        preferences=tuple(entries),
    ), errors


def load_learned_rules(
    path: Optional[Path] = None,
) -> tuple[LearnedRulesConfig, list[str]]:
    """Read + validate learned_rules.yaml."""
    import os as _os
    resolved = (
        path if path is not None
        else Path(_os.environ.get(
            LEARNED_RULES_ENV, str(LEARNED_RULES_DEFAULT_PATH),
        ))
    )

    errors: list[str] = []
    if not resolved.exists():
        errors.append(
            f"learned rules file not found at {resolved}; "
            f"orchestrator will resolve without learned-rule bias"
        )
        return LearnedRulesConfig(
            schema_version=SCHEMA_VERSION_LEARNED_RULES,
            pending_activation=(),
            active=(),
        ), errors

    raw = _safe_load_yaml(resolved)
    sv = raw.get("schema_version")
    if sv != SCHEMA_VERSION_LEARNED_RULES:
        raise BehaviorProvenanceError(
            f"{resolved}: schema_version {sv!r} not supported "
            f"(expected {SCHEMA_VERSION_LEARNED_RULES})"
        )

    pending = _parse_rules_section(
        raw.get("pending_activation") or [], errors, "pending_activation",
    )
    active = _parse_rules_section(
        raw.get("active") or [], errors, "active",
    )

    return LearnedRulesConfig(
        schema_version=int(sv),
        pending_activation=tuple(pending),
        active=tuple(active),
    ), errors


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def save_preferences(
    config: PreferencesConfig, path: Optional[Path] = None,
) -> Path:
    """Atomic write of preferences.yaml."""
    p = path if path is not None else PREFERENCES_DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": config.schema_version,
        "preferences": [
            {
                "id":         pr.id,
                "statement":  pr.statement,
                "weight":     pr.weight,
                "domain":     pr.domain,
                "created_at": pr.created_at,
                "updated_at": pr.updated_at,
            }
            for pr in config.preferences
        ],
    }
    return _atomic_write_yaml(p, payload)


def save_learned_rules(
    config: LearnedRulesConfig, path: Optional[Path] = None,
) -> Path:
    """Atomic write of learned_rules.yaml."""
    p = path if path is not None else LEARNED_RULES_DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": config.schema_version,
        "pending_activation": [_rule_to_dict(r) for r in config.pending_activation],
        "active":             [_rule_to_dict(r) for r in config.active],
    }
    return _atomic_write_yaml(p, payload)


# ---------------------------------------------------------------------------
# Precedence + delta
# ---------------------------------------------------------------------------


def resolve_precedence(layer_a: str, layer_b: str) -> str:
    """Given two layers, return the winning one.

    Raises ValueError if either layer is unknown. Uses the
    PRECEDENCE table from ADR-0072 D1.
    """
    if layer_a not in PRECEDENCE:
        raise ValueError(
            f"unknown layer {layer_a!r}; valid: {sorted(PRECEDENCE)}"
        )
    if layer_b not in PRECEDENCE:
        raise ValueError(
            f"unknown layer {layer_b!r}; valid: {sorted(PRECEDENCE)}"
        )
    if PRECEDENCE[layer_a] >= PRECEDENCE[layer_b]:
        return layer_a
    return layer_b


def compute_behavior_change_delta(
    before: Any, after: Any,
) -> dict[str, Any]:
    """Diff two preference / learned-rule configs for the
    behavior_change audit event_data.

    Returns a structured payload showing what was added, modified,
    or removed between before + after. Used by save_* helpers to
    emit audit chain entries with a complete provenance trail.

    Output shape:
      {
        "added":    [{id, fields...}, ...],
        "modified": [{id, before: {...}, after: {...}}, ...],
        "removed":  [{id, fields...}, ...],
      }
    """
    before_items = _items_by_id(before)
    after_items = _items_by_id(after)
    before_ids = set(before_items)
    after_ids = set(after_items)

    added = [after_items[i] for i in (after_ids - before_ids)]
    removed = [before_items[i] for i in (before_ids - after_ids)]

    modified: list[dict[str, Any]] = []
    for i in before_ids & after_ids:
        if before_items[i] != after_items[i]:
            modified.append({
                "id":     i,
                "before": before_items[i],
                "after":  after_items[i],
            })

    return {
        "added":    sorted(added, key=lambda d: d.get("id", "")),
        "modified": sorted(modified, key=lambda d: d.get("id", "")),
        "removed":  sorted(removed, key=lambda d: d.get("id", "")),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _safe_load_yaml(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise BehaviorProvenanceError(f"{path}: read failed: {e}") from e
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise BehaviorProvenanceError(
            f"{path}: malformed YAML: {e}"
        ) from e
    if not isinstance(raw, dict):
        raise BehaviorProvenanceError(
            f"{path}: top-level must be a YAML mapping"
        )
    return raw


def _parse_preference(raw: Any, idx: int) -> tuple[Optional[Preference], list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, [f"preferences[{idx}]: must be a mapping"]
    required = {"id", "statement", "weight", "domain"}
    missing = required - set(raw.keys())
    if missing:
        return None, [
            f"preferences[{idx}]: missing fields: {sorted(missing)}"
        ]
    weight = raw["weight"]
    if not isinstance(weight, (int, float)) or weight < 0 or weight > 1:
        return None, [
            f"preferences[{idx}]: weight must be in [0, 1]; "
            f"got {weight!r}"
        ]
    now_iso = _now_iso()
    return Preference(
        id=str(raw["id"]),
        statement=str(raw["statement"]),
        weight=float(weight),
        domain=str(raw["domain"]),
        created_at=str(raw.get("created_at", now_iso)),
        updated_at=str(raw.get("updated_at", now_iso)),
    ), errors


def _parse_learned_rule(
    raw: Any, idx: int, section: str,
) -> tuple[Optional[LearnedRule], list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, [f"{section}[{idx}]: must be a mapping"]
    required = {"id", "statement", "weight", "domain",
                "proposer_agent_dna", "status"}
    missing = required - set(raw.keys())
    if missing:
        return None, [
            f"{section}[{idx}]: missing fields: {sorted(missing)}"
        ]
    weight = raw["weight"]
    if not isinstance(weight, (int, float)) or weight < 0 or weight > 1:
        return None, [
            f"{section}[{idx}]: weight must be in [0, 1]; got {weight!r}"
        ]
    status = str(raw["status"])
    if status not in ("pending_activation", "active", "refused"):
        return None, [
            f"{section}[{idx}]: status {status!r} not in "
            f"{{pending_activation, active, refused}}"
        ]
    return LearnedRule(
        id=str(raw["id"]),
        statement=str(raw["statement"]),
        weight=float(weight),
        domain=str(raw["domain"]),
        proposer_agent_dna=str(raw["proposer_agent_dna"]),
        created_at=str(raw.get("created_at", _now_iso())),
        status=status,
        verification_verdict=raw.get("verification_verdict"),
        verification_reason=raw.get("verification_reason"),
    ), errors


def _parse_rules_section(
    raw_list: list, errors: list[str], section: str,
) -> list[LearnedRule]:
    if not isinstance(raw_list, list):
        errors.append(f"{section} must be a list; ignoring")
        return []
    out: list[LearnedRule] = []
    seen_ids: set[str] = set()
    for idx, raw_rule in enumerate(raw_list):
        rule, item_errors = _parse_learned_rule(raw_rule, idx, section)
        errors.extend(item_errors)
        if rule is None:
            continue
        if rule.id in seen_ids:
            errors.append(
                f"{section} has duplicate rule id {rule.id!r}; first kept"
            )
            continue
        seen_ids.add(rule.id)
        out.append(rule)
    return out


def _rule_to_dict(r: LearnedRule) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id":                 r.id,
        "statement":          r.statement,
        "weight":             r.weight,
        "domain":             r.domain,
        "proposer_agent_dna": r.proposer_agent_dna,
        "created_at":         r.created_at,
        "status":             r.status,
    }
    if r.verification_verdict is not None:
        out["verification_verdict"] = r.verification_verdict
    if r.verification_reason is not None:
        out["verification_reason"] = r.verification_reason
    return out


def _items_by_id(config: Any) -> dict[str, dict[str, Any]]:
    """Flatten a config (Preferences or LearnedRules) into
    {id: fields_dict} for delta comparison."""
    out: dict[str, dict[str, Any]] = {}
    if hasattr(config, "preferences"):
        for p in config.preferences:
            out[p.id] = {
                "id":        p.id,
                "statement": p.statement,
                "weight":    p.weight,
                "domain":    p.domain,
            }
    if hasattr(config, "active"):
        for r in config.active:
            out[r.id] = _rule_to_dict(r)
        for r in config.pending_activation:
            out[r.id] = _rule_to_dict(r)
    return out


def _atomic_write_yaml(path: Path, payload: dict) -> Path:
    """Write to <path>.tmp then rename — crash-safe."""
    tmp = path.with_name(path.name + ".tmp")
    text = yaml.safe_dump(
        payload, sort_keys=False, default_flow_style=False,
        allow_unicode=True,
    )
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
