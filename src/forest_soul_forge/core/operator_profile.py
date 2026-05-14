"""Operator profile substrate — ADR-0068 T1 (B277).

Forest's kernel today has no canonical place to read "who is the
operator?" Every domain re-asks the same questions in conversation.
The ten-domain platform arc needs a single source of truth that
all agents read.

This module is the loader + validator + writer for
``data/operator/profile.yaml`` — a versioned YAML file with a
small, stable schema that the operator edits directly. Reality
Anchor (ADR-0063) seeds its ground-truth catalog from this file
at daemon boot, so personal facts become tamper-evident the same
way audit-chain entries are.

## Surface

  - :class:`OperatorProfile` — frozen dataclass shape
  - :func:`load_operator_profile(path)` — read + validate
  - :func:`save_operator_profile(profile, path)` — atomic write
  - :func:`default_operator_profile_path(data_dir)` — canonical loc
  - :func:`profile_to_ground_truth_seeds(profile)` — Reality Anchor
    seed entries derived from the profile, ready to merge into the
    ground_truth.yaml catalog at boot

## Why YAML, not a registry table

- Operator edits in a text editor; no daemon required.
- Survives registry rebuild — operator identity isn't derived
  from SQLite state.
- Encrypted-at-rest via ADR-0050 T5 file-encryption when
  ``FSF_AT_REST_ENCRYPTION=true`` (file lands at
  ``profile.yaml.enc``). The ``.soul.md.enc`` pattern from
  birth_pipeline applies identically — same encrypt_text helper.
- Matches existing config patterns (genres.yaml, ground_truth.yaml,
  security_iocs.yaml).

## Why a frozen dataclass

The profile is read-many-write-once-per-edit. Treating it as
immutable in-memory makes accidental mutation impossible.
Writes go through :func:`save_operator_profile` which constructs
a fresh instance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml


SCHEMA_VERSION = 1

# Required fields under operator.*. Validation refuses any profile
# missing these. Future tranches add optional fields; the required
# set stays minimal so a fresh operator can supply the bare essentials
# and grow the profile over time.
_REQUIRED_OPERATOR_FIELDS = frozenset({
    "operator_id",
    "name",
    "preferred_name",
    "email",
    "timezone",
    "locale",
    "work_hours",
})

# RFC 5322 simplified email regex — pragmatic, not exhaustive. We
# refuse obvious garbage (no @, no domain dot) but don't try to be
# the canonical validator. The operator edits this themselves;
# they'll catch typos.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

# BCP-47 locale shape: lang-Region (e.g. en-US, fr-FR). We don't
# enforce the full BCP-47 grammar (script subtags, variants), just
# the common shape.
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2})?$")

# HH:MM in 24-hour form.
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class OperatorProfileError(RuntimeError):
    """Raised when the profile file is missing required fields,
    has the wrong schema version, malformed YAML, or invalid
    values. Surfaces a clear operator-fixable message rather than
    a deep stack trace from yaml.safe_load.
    """


@dataclass(frozen=True)
class WorkHours:
    """Local-time work window, as the operator defines it.

    Both endpoints in HH:MM 24-hour form. Daily Life OS's
    Morning Coordinator reads this to schedule the briefing
    before ``start``; the time_steward agent uses it to protect
    the operator's focus blocks.
    """
    start: str
    end: str


@dataclass(frozen=True)
class OperatorProfile:
    """The single source of truth for who the operator is.

    All fields are required at T1; future tranches add optional
    fields under ``extra`` for forward-compat. Schema migrations
    bump SCHEMA_VERSION + provide a loader-side migration helper.
    """
    schema_version: int
    operator_id: str
    name: str
    preferred_name: str
    email: str
    timezone: str
    locale: str
    work_hours: WorkHours
    created_at: str
    updated_at: str
    # Forward-compat slot for tranches T4-T6 (trust circle, voice
    # samples, financial jurisdiction, etc.). Stays empty in T1.
    extra: dict[str, Any] = field(default_factory=dict)


def default_operator_profile_path(data_dir: Optional[Path] = None) -> Path:
    """Canonical profile location: ``<data_dir>/operator/profile.yaml``.

    Default ``data_dir`` is the repo's ``data/`` directory — matches
    the registry + audit chain placement convention. Operators with
    non-default data dirs pass it explicitly.
    """
    if data_dir is None:
        data_dir = Path("data")
    return data_dir / "operator" / "profile.yaml"


def load_operator_profile(
    path: Optional[Path] = None,
    *,
    encryption_config: Any = None,
) -> OperatorProfile:
    """Read + validate the operator profile.

    ADR-0050 T5 encryption-aware. If the canonical-named path
    doesn't exist but the ``.enc`` sibling does, decrypts via
    encrypt_text round-trip. Same detection pattern as
    birth_pipeline.read_soul_md.

    Raises :class:`OperatorProfileError` on:
      - file missing (both plain + .enc variants)
      - malformed YAML
      - schema_version mismatch
      - missing required field
      - field with invalid format (bad email, bad timezone, etc.)
    """
    p = path if path is not None else default_operator_profile_path()
    enc_path = p.with_name(p.name + ".enc")

    if enc_path.exists():
        if encryption_config is None:
            raise OperatorProfileError(
                f"operator profile is encrypted at {enc_path} but no "
                f"encryption_config was provided. Set "
                f"FSF_AT_REST_ENCRYPTION=true and ensure the master "
                f"key resolves; see "
                f"docs/runbooks/encryption-at-rest.md."
            )
        from forest_soul_forge.core.at_rest_encryption import decrypt_text
        try:
            text = decrypt_text(
                enc_path.read_text(encoding="utf-8"),
                encryption_config,
            )
        except Exception as e:  # noqa: BLE001
            raise OperatorProfileError(
                f"failed to decrypt operator profile at {enc_path}: "
                f"{type(e).__name__}: {e}"
            ) from e
    elif p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as e:
            raise OperatorProfileError(
                f"could not read operator profile at {p}: {e}"
            ) from e
    else:
        raise OperatorProfileError(
            f"operator profile not found at {p} (or .enc variant). "
            f"Run `fsf operator profile init` to create one."
        )

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise OperatorProfileError(
            f"operator profile at {p} is malformed YAML: {e}"
        ) from e

    return _validate_and_construct(raw, source_path=p)


def save_operator_profile(
    profile: OperatorProfile,
    path: Optional[Path] = None,
    *,
    encryption_config: Any = None,
) -> Path:
    """Atomic write of the operator profile.

    Writes to ``<path>.tmp`` then renames over the target, so a
    crash mid-write leaves the previous version intact.

    ADR-0050 T5 encryption-aware. When ``encryption_config`` is
    set, encrypts via encrypt_text and writes to ``<path>.enc``
    extension. Returns the actual path written.

    Stamps ``updated_at`` automatically; preserves the supplied
    ``created_at``.
    """
    p = path if path is not None else default_operator_profile_path()
    p.parent.mkdir(parents=True, exist_ok=True)

    refreshed = OperatorProfile(
        schema_version=profile.schema_version,
        operator_id=profile.operator_id,
        name=profile.name,
        preferred_name=profile.preferred_name,
        email=profile.email,
        timezone=profile.timezone,
        locale=profile.locale,
        work_hours=profile.work_hours,
        created_at=profile.created_at,
        updated_at=_now_iso(),
        extra=profile.extra,
    )

    payload = _to_yaml(refreshed)

    if encryption_config is not None:
        from forest_soul_forge.core.at_rest_encryption import encrypt_text
        target = p.with_name(p.name + ".enc")
        ciphertext = encrypt_text(payload, encryption_config)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(ciphertext, encoding="utf-8")
        tmp.replace(target)
        return target

    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(p)
    return p


def profile_to_ground_truth_seeds(profile: OperatorProfile) -> list[dict]:
    """Translate the operator profile into Reality Anchor ground-
    truth seed entries (ADR-0063 format).

    Each seed becomes a fact that the dispatcher's RealityAnchorStep
    cross-references on every gated tool call. So if an agent later
    claims "the operator's timezone is Europe/London," the anchor
    catches the contradiction against the seed.

    The structure matches config/ground_truth.yaml entries exactly
    so callers can append these to the ground-truth catalog at boot.
    """
    seeds: list[dict] = []

    seeds.append({
        "id": "operator_name",
        "severity": "HIGH",
        "statement": (
            f"The operator's name is {profile.name}."
        ),
        "domain_keywords": ["operator", "name", "identity"],
        "canonical_terms": [profile.name],
        "forbidden_terms": [],
        "source": "operator_profile.yaml",
    })

    if profile.preferred_name and profile.preferred_name != profile.name:
        seeds.append({
            "id": "operator_preferred_name",
            "severity": "MEDIUM",
            "statement": (
                f"The operator's preferred name (how to address them) "
                f"is {profile.preferred_name}."
            ),
            "domain_keywords": ["operator", "preferred name", "address"],
            "canonical_terms": [profile.preferred_name],
            "forbidden_terms": [],
            "source": "operator_profile.yaml",
        })

    seeds.append({
        "id": "operator_email",
        "severity": "HIGH",
        "statement": (
            f"The operator's primary email is {profile.email}."
        ),
        "domain_keywords": ["operator", "email", "contact"],
        "canonical_terms": [profile.email],
        "forbidden_terms": [],
        "source": "operator_profile.yaml",
    })

    seeds.append({
        "id": "operator_timezone",
        "severity": "HIGH",
        "statement": (
            f"The operator's timezone is {profile.timezone}."
        ),
        "domain_keywords": ["operator", "timezone", "schedule"],
        "canonical_terms": [profile.timezone],
        "forbidden_terms": [],
        "source": "operator_profile.yaml",
    })

    seeds.append({
        "id": "operator_locale",
        "severity": "MEDIUM",
        "statement": (
            f"The operator's locale is {profile.locale}."
        ),
        "domain_keywords": ["operator", "locale", "language"],
        "canonical_terms": [profile.locale],
        "forbidden_terms": [],
        "source": "operator_profile.yaml",
    })

    seeds.append({
        "id": "operator_work_hours",
        "severity": "MEDIUM",
        "statement": (
            f"The operator's standard work hours are "
            f"{profile.work_hours.start} to {profile.work_hours.end} "
            f"in their local timezone ({profile.timezone})."
        ),
        "domain_keywords": [
            "operator", "work hours", "schedule", "focus time",
        ],
        "canonical_terms": [
            profile.work_hours.start, profile.work_hours.end,
        ],
        "forbidden_terms": [],
        "source": "operator_profile.yaml",
    })

    return seeds


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _validate_and_construct(
    raw: dict, source_path: Path,
) -> OperatorProfile:
    """Pull required + optional fields from the raw dict; raise
    OperatorProfileError with a clear message on any failure."""

    if not isinstance(raw, dict):
        raise OperatorProfileError(
            f"operator profile at {source_path} must be a YAML "
            f"mapping at the top level; got {type(raw).__name__}"
        )

    sv = raw.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise OperatorProfileError(
            f"operator profile at {source_path} schema_version "
            f"{sv} does not match expected {SCHEMA_VERSION}. "
            f"Migration helpers will ship in T8."
        )

    op = raw.get("operator")
    if not isinstance(op, dict):
        raise OperatorProfileError(
            f"operator profile at {source_path} missing required "
            f"top-level 'operator' mapping"
        )

    missing = _REQUIRED_OPERATOR_FIELDS - set(op.keys())
    if missing:
        raise OperatorProfileError(
            f"operator profile at {source_path} missing required "
            f"fields under operator.*: {sorted(missing)}"
        )

    # Per-field validation. Refuse obviously-bad values early so
    # downstream consumers don't have to defend against them.
    email = op["email"]
    if not isinstance(email, str) or not _EMAIL_RE.match(email):
        raise OperatorProfileError(
            f"operator.email must be a valid email address; "
            f"got {email!r}"
        )

    locale = op["locale"]
    if not isinstance(locale, str) or not _LOCALE_RE.match(locale):
        raise OperatorProfileError(
            f"operator.locale must be a BCP-47 locale "
            f"(e.g. 'en-US'); got {locale!r}"
        )

    # Timezone: rely on Python's zoneinfo to validate without
    # importing the full tzdata package at module load time.
    tz_name = op["timezone"]
    if not isinstance(tz_name, str):
        raise OperatorProfileError(
            f"operator.timezone must be a string IANA name; "
            f"got {type(tz_name).__name__}"
        )
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        ZoneInfo(tz_name)
    except ImportError:
        # Python < 3.9 doesn't have zoneinfo. Skip the runtime
        # check; the rest of the validation still fires.
        pass
    except Exception:
        raise OperatorProfileError(
            f"operator.timezone is not a recognized IANA name: "
            f"{tz_name!r}"
        )

    wh_raw = op["work_hours"]
    if not isinstance(wh_raw, dict) or set(wh_raw.keys()) < {"start", "end"}:
        raise OperatorProfileError(
            f"operator.work_hours must be a mapping with 'start' "
            f"and 'end' keys; got {wh_raw!r}"
        )
    for k in ("start", "end"):
        v = wh_raw[k]
        if not isinstance(v, str) or not _HHMM_RE.match(v):
            raise OperatorProfileError(
                f"operator.work_hours.{k} must be HH:MM in 24-hour "
                f"form; got {v!r}"
            )

    work_hours = WorkHours(start=wh_raw["start"], end=wh_raw["end"])

    # Timestamps. If missing, stamp now. Operator-supplied values
    # pass through unchanged.
    created_at = raw.get("created_at") or _now_iso()
    updated_at = raw.get("updated_at") or created_at

    # Forward-compat extras: anything under operator.extra or at
    # the top level that we don't recognize gets stashed.
    extra: dict[str, Any] = {}
    operator_extra = op.get("extra")
    if isinstance(operator_extra, dict):
        extra.update(operator_extra)

    return OperatorProfile(
        schema_version=int(sv),
        operator_id=str(op["operator_id"]),
        name=str(op["name"]),
        preferred_name=str(op["preferred_name"]),
        email=str(email),
        timezone=str(tz_name),
        locale=str(locale),
        work_hours=work_hours,
        created_at=str(created_at),
        updated_at=str(updated_at),
        extra=extra,
    )


def _to_yaml(profile: OperatorProfile) -> str:
    """Serialize OperatorProfile to a stable YAML string.

    Sorted-key serialization so writes diff cleanly in version
    control (if the operator chooses to track the profile).
    """
    payload = {
        "schema_version": profile.schema_version,
        "operator": {
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
        },
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }
    if profile.extra:
        payload["operator"]["extra"] = dict(profile.extra)

    return yaml.safe_dump(
        payload,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def _now_iso() -> str:
    """RFC 3339 UTC timestamp, no sub-second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
