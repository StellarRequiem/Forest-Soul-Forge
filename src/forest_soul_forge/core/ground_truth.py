"""ADR-0063 Reality Anchor — ground-truth catalog loader.

Loads `config/ground_truth.yaml` (the operator-asserted truth)
and optionally merges per-agent ADD-only additions from the
agent's constitution YAML. Per ADR-0063 D3:

  1. Operator-global is canonical.
  2. Per-agent entries may ADD facts; an id collision with
     operator-global is logged as a config error + the
     per-agent entry is IGNORED.
  3. Recent conversation context is NOT in this loader — it's
     handled by the RealityAnchorStep (T3) at dispatch time.

## Surface

  - ``load_ground_truth(path=None) -> tuple[list[Fact], list[str]]``
    Read + validate the operator-global catalog.

  - ``merge_agent_additions(operator_facts, agent_constitution_dict)
    -> tuple[list[Fact], list[str]]``
    Merge per-agent ADD-only additions; collisions
    surfaced as errors.

  - ``Fact`` dataclass.

## Why a dataclass, not just dicts

The verify_claim.v1 tool calls into this loader and walks the
fact list per claim. A typed dataclass makes the matcher's
inner loop type-safe and lets future tooling (frontend editor,
JSON-schema export for the SoulUX pane) introspect the shape
without re-deriving it from a dict's keys.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---- constants ------------------------------------------------------------

#: Default catalog path resolved relative to repo root.
#: Override via ``FSF_GROUND_TRUTH_PATH`` env var or the
#: explicit ``path`` argument to :func:`load_ground_truth`.
DEFAULT_CATALOG_PATH = Path("config/ground_truth.yaml")

ENV_VAR = "FSF_GROUND_TRUTH_PATH"

VALID_SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")


# ---- data types -----------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    """One operator-asserted fact.

    Frozen so the matcher can't accidentally mutate it during
    a verification pass (a class of bug we've seen elsewhere
    when verifiers carry per-call state).
    """
    id: str
    statement: str
    domain_keywords: tuple[str, ...]
    canonical_terms: tuple[str, ...]
    forbidden_terms: tuple[str, ...]
    severity: str
    last_confirmed_at: str | None = None
    notes: str = ""
    #: Where this fact came from — "operator_global" or
    #: ``f"agent:{instance_id}"`` for per-agent additions.
    source: str = "operator_global"


# ---- loaders --------------------------------------------------------------


def load_ground_truth(
    path: Path | None = None,
) -> tuple[list[Fact], list[str]]:
    """Read the operator-global catalog. Returns (facts, errors).

    Errors are config-level problems that don't crash the load
    (one bad fact shouldn't kill the whole catalog), surfaced
    so the operator can see them in ``/reality-anchor/status``
    (T7) without inspecting daemon logs.

    Missing file is benign: returns ``([], ["catalog file not
    found: ..."])`` so the Reality Anchor degrades to "no
    facts to check against" rather than crashing the dispatcher.
    """
    errors: list[str] = []
    resolved = _resolve_default_path(path)
    try:
        text = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], [f"catalog file not found: {resolved}"]
    except Exception as e:
        return [], [f"catalog read failed: {e}"]
    try:
        data = yaml.safe_load(text) or {}
    except Exception as e:
        return [], [f"catalog YAML parse failed: {e}"]
    if not isinstance(data, dict):
        return [], ["catalog root must be a YAML mapping"]
    raw_facts = data.get("facts") or []
    if not isinstance(raw_facts, list):
        return [], ["catalog `facts:` must be a list"]

    facts: list[Fact] = []
    seen_ids: set[str] = set()
    for idx, raw in enumerate(raw_facts):
        fact, fact_errors = _parse_fact(raw, idx, source="operator_global")
        errors.extend(fact_errors)
        if fact is None:
            continue
        if fact.id in seen_ids:
            errors.append(
                f"duplicate fact id {fact.id!r}; skipping second occurrence"
            )
            continue
        seen_ids.add(fact.id)
        facts.append(fact)

    # ADR-0068 T1.1 (B278) — merge operator profile-derived facts.
    # The operator profile at data/operator/profile.yaml is the
    # operator's personal truth (name/email/timezone/work_hours).
    # Conceptually identical to the operator-global catalog above:
    # operator-asserted, tamper-evident, single source of truth. The
    # merge gives every Reality Anchor consumer (dispatcher gate,
    # conversation gate, /reality-anchor router, verify_claim.v1)
    # transparent access to personal facts.
    #
    # Silent skip on any failure (profile missing, malformed) — the
    # operator-global catalog still loads, and the merge is purely
    # additive. errors get the non-fatal note so /reality-anchor/status
    # (T7) surfaces the gap without crashing dispatch.
    try:
        from forest_soul_forge.core.operator_profile import (
            OperatorProfileError,
            load_operator_profile,
            profile_to_ground_truth_seeds,
        )
        try:
            profile = load_operator_profile()
        except OperatorProfileError as e:
            errors.append(
                f"operator profile not loaded for ground-truth merge: {e}"
            )
        else:
            seeds = profile_to_ground_truth_seeds(profile)
            for seed in seeds:
                if seed["id"] in seen_ids:
                    # operator-global catalog wins on id collision —
                    # same discipline as merge_agent_additions: the
                    # explicit catalog is more authoritative than the
                    # derived profile seed.
                    errors.append(
                        f"operator profile seed {seed['id']!r} collides "
                        f"with operator-global catalog id; keeping catalog"
                    )
                    continue
                seen_ids.add(seed["id"])
                facts.append(_fact_from_profile_seed(seed))
    except Exception as e:  # noqa: BLE001 — non-fatal merge
        errors.append(
            f"operator profile ground-truth merge failed: "
            f"{type(e).__name__}: {e}"
        )

    return facts, errors


def _fact_from_profile_seed(seed: dict) -> Fact:
    """Translate an operator-profile-derived seed dict to a frozen Fact.

    The seed dicts come from
    :func:`operator_profile.profile_to_ground_truth_seeds`. The
    conversion is mechanical — same field names, just tuple-coerce
    the list-valued ones to match Fact's frozen dataclass shape.
    """
    return Fact(
        id=str(seed["id"]),
        statement=str(seed["statement"]),
        domain_keywords=tuple(seed.get("domain_keywords") or []),
        canonical_terms=tuple(seed.get("canonical_terms") or []),
        forbidden_terms=tuple(seed.get("forbidden_terms") or []),
        severity=str(seed.get("severity", "MEDIUM")),
        last_confirmed_at=seed.get("last_confirmed_at"),
        notes=str(seed.get("notes", "derived from operator_profile.yaml")),
        source="operator_profile",
    )


def merge_agent_additions(
    operator_facts: list[Fact],
    agent_constitution: dict[str, Any] | None,
    *,
    agent_instance_id: str = "unknown",
) -> tuple[list[Fact], list[str]]:
    """Layer per-agent ADD-only ground-truth additions on top
    of the operator-global catalog.

    Per ADR-0063 D3, an agent's constitution can declare:

        ground_truth_additions:
          - id: my_specialized_fact
            statement: ...
            domain_keywords: [...]
            canonical_terms: [...]
            ...

    But an addition whose ``id`` collides with an existing
    operator-global fact is REJECTED (logged + dropped). The
    operator-global catalog is the canonical truth; per-agent
    additions may extend, never override. This stops a
    compromised agent from rewriting its own reality.

    Returns ``(merged_facts, errors)``. The merge is non-
    destructive — ``operator_facts`` is not mutated.
    """
    errors: list[str] = []
    if not agent_constitution or not isinstance(agent_constitution, dict):
        return list(operator_facts), errors
    additions = agent_constitution.get("ground_truth_additions") or []
    if not isinstance(additions, list):
        errors.append(
            f"agent {agent_instance_id!r}: ground_truth_additions must be a "
            "list; ignoring"
        )
        return list(operator_facts), errors

    operator_ids = {f.id for f in operator_facts}
    merged = list(operator_facts)
    for idx, raw in enumerate(additions):
        fact, fact_errors = _parse_fact(
            raw, idx,
            source=f"agent:{agent_instance_id}",
        )
        errors.extend(
            f"agent {agent_instance_id!r}: {e}" for e in fact_errors
        )
        if fact is None:
            continue
        if fact.id in operator_ids:
            errors.append(
                f"agent {agent_instance_id!r}: per-agent addition with id "
                f"{fact.id!r} collides with an operator-global fact — "
                "rejected (operator-global wins per ADR-0063 D3)"
            )
            continue
        merged.append(fact)
    return merged, errors


# ---- internals -----------------------------------------------------------


def _parse_fact(
    raw: Any, idx: int, *, source: str,
) -> tuple[Fact | None, list[str]]:
    """Validate a single fact entry. Returns (fact, errors).

    A None fact means the entry was malformed and should be
    skipped (errors surface what was wrong). Required fields:
    id, statement, domain_keywords, canonical_terms, severity.
    forbidden_terms defaults to [].
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, [f"fact #{idx} is not a mapping; skipped"]

    fid = raw.get("id")
    if not isinstance(fid, str) or not fid:
        return None, [f"fact #{idx} has no/invalid id; skipped"]

    statement = raw.get("statement")
    if not isinstance(statement, str) or not statement:
        return None, [f"fact {fid!r} has no statement; skipped"]

    domain = raw.get("domain_keywords") or []
    if not isinstance(domain, list) or not domain:
        return None, [
            f"fact {fid!r} has no domain_keywords; skipped "
            "(every fact must declare at least one domain keyword "
            "so the verifier knows when to evaluate it)"
        ]
    if not all(isinstance(k, str) and k for k in domain):
        return None, [
            f"fact {fid!r} domain_keywords entries must be non-empty strings"
        ]

    canonical = raw.get("canonical_terms") or []
    if not isinstance(canonical, list) or not canonical:
        return None, [
            f"fact {fid!r} has no canonical_terms; skipped "
            "(at least one canonical term is required to detect "
            "confirmation)"
        ]
    if not all(isinstance(c, str) and c for c in canonical):
        return None, [
            f"fact {fid!r} canonical_terms entries must be non-empty strings"
        ]

    forbidden = raw.get("forbidden_terms") or []
    if not isinstance(forbidden, list):
        errors.append(
            f"fact {fid!r} forbidden_terms must be a list; treating as empty"
        )
        forbidden = []
    forbidden = [t for t in forbidden if isinstance(t, str) and t]

    severity = raw.get("severity")
    if severity not in VALID_SEVERITIES:
        return None, [
            f"fact {fid!r} has invalid severity {severity!r}; must be one "
            f"of {VALID_SEVERITIES}"
        ]

    fact = Fact(
        id=fid,
        statement=statement,
        # Lower-case the keyword/term tuples up-front so the matcher
        # doesn't have to re-lower per call. Claims are also lowered.
        domain_keywords=tuple(k.lower() for k in domain),
        canonical_terms=tuple(c.lower() for c in canonical),
        forbidden_terms=tuple(f.lower() for f in forbidden),
        severity=severity,
        last_confirmed_at=(
            raw.get("last_confirmed_at")
            if isinstance(raw.get("last_confirmed_at"), str) else None
        ),
        notes=(
            raw.get("notes")
            if isinstance(raw.get("notes"), str) else ""
        ),
        source=source,
    )
    return fact, errors


def _resolve_default_path(path: Path | None) -> Path:
    """Pick the catalog path: explicit > env var > default."""
    if path is not None:
        return path
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_CATALOG_PATH
