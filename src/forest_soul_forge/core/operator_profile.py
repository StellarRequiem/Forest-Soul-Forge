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

# ADR-0068 T6 (B316): loose validation for financial fields.
# Currency: ISO 4217 three-letter uppercase code (USD, EUR, JPY, …).
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
# Tax residence: ISO 3166-1 alpha-2, optionally with a subdivision
# (US, US-CA, GB-ENG). Permissive on subdivision length because
# countries vary (US is 2-letter, GB-ENG is 3, JP-13 is digits).
_TAX_RESIDENCE_RE = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,4})?$")
# Fiscal year start: MM-DD. Calendar-year operators get 01-01;
# UK personal tax year is 04-06; US federal is 10-01.
_MMDD_RE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


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
class FinancialContext:
    """Operator's financial + jurisdictional context
    (ADR-0068 T6, B316).

    Singleton sub-record: each operator has at most one. Feeds
    Finance Guardian and any cross-domain agent that needs to
    reason in operator-currency or operator-jurisdiction (e.g.
    a Daily Life OS reminder for "tax filing deadline" picks
    the right deadline by ``tax_residence``).

    Fields:
      - ``currency``: ISO 4217 three-letter code (USD, EUR, GBP, …).
      - ``tax_residence``: ISO 3166-1 alpha-2 country code, or
        country+subdivision (US-CA, GB-ENG). Free-form-ish — the
        loader does a loose regex check, not a full lookup.
      - ``fiscal_year_start``: MM-DD of the operator's fiscal-year
        start (01-01 for calendar-year; 04-01 for UK personal).
      - ``preferred_tooling``: list of strings naming the operator's
        preferred finance tooling (e.g. ['Quicken', 'YNAB']).
        Finance Guardian agents consult this to avoid recommending
        tools the operator doesn't use.

    Reality Anchor seeds emit currency + tax_residence at HIGH
    severity — an agent claiming the wrong currency to a
    transaction agent is a high-stakes mistake.
    """
    currency: str
    tax_residence: str
    fiscal_year_start: str
    preferred_tooling: tuple[str, ...] = ()


@dataclass(frozen=True)
class VoiceSample:
    """One pronunciation reference for TTS personalization (ADR-0068 T5, B315).

    The operator records short audio samples — a name, a domain term,
    an unusual word — so the Voice I/O TTS layer (ADR-0070) can match
    pronunciation. The sample is a file pointer + the word/phrase it
    demonstrates; the audio itself lives next to the profile under
    ``data/operator/voice_samples/``.

    No Reality Anchor seeds — these are operational pointers, not
    operator-assertion-grade facts. The TTS subsystem reads the
    profile and resolves the file paths at synthesize time.
    """
    phrase: str           # the word/phrase the sample demonstrates
    audio_path: str       # path relative to data/operator/voice_samples/
    notes: Optional[str] = None


@dataclass(frozen=True)
class WritingSample:
    """One text reference for Content Studio style matching
    (ADR-0068 T5, B315).

    The operator points at writing samples — past emails, blog posts,
    slack messages — that demonstrate their voice. Content Studio
    agents read these as exemplars before producing operator-facing
    text. The sample is a file pointer; the text lives next to the
    profile under ``data/operator/writing_samples/``.

    ``channel`` is operator-defined free-form (e.g. "email",
    "blog", "slack", "academic_paper") — Forest doesn't enforce
    a taxonomy.

    No Reality Anchor seeds — same rationale as VoiceSample.
    """
    title: str            # human-readable name for the sample
    file_path: str        # path relative to data/operator/writing_samples/
    channel: Optional[str] = None
    notes: Optional[str] = None


@dataclass(frozen=True)
class TrustCirclePerson:
    """One person in the operator's trust circle (ADR-0068 T4, B314).

    The operator declares relationships their agents should know about:
    a spouse, manager, accountant, doctor — anyone whose name an agent
    might encounter in operator messages or whose context a domain
    agent might need to reference.

    The Reality Anchor seeds these so an agent claiming "your manager
    is X" gets cross-referenced against the operator's actual entry.

    ``email`` and ``notes`` are optional; only ``name`` + ``relationship``
    are required. The relationship string is operator-defined free-form
    (e.g. "spouse", "engineering manager", "primary care physician")
    — Forest doesn't enforce a taxonomy.
    """
    name: str
    relationship: str
    email: Optional[str] = None
    notes: Optional[str] = None


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
    # ADR-0068 T4 (B314): operator's declared trust circle. Default
    # empty tuple — backward-compat with v1 yamls authored before T4.
    # Each person becomes a Reality Anchor ground-truth seed at boot.
    trust_circle: tuple[TrustCirclePerson, ...] = ()
    # ADR-0068 T5 (B315): operator's reference materials.
    # voice_samples feeds Voice I/O TTS personalization (ADR-0070);
    # writing_samples feeds Content Studio style matching. Both
    # default to empty for backward-compat with pre-T5 yamls.
    voice_samples: tuple[VoiceSample, ...] = ()
    writing_samples: tuple[WritingSample, ...] = ()
    # ADR-0068 T6 (B316): operator's financial + jurisdictional
    # context. Singleton (None when absent — backward-compat with
    # pre-T6 yamls). Reality Anchor seeds currency + tax_residence.
    financial: Optional[FinancialContext] = None
    # Forward-compat slot for tranche T7 (consent wizard records).
    # Stays empty before that tranche.
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
        # ADR-0068 T4 (B314): preserve trust_circle through the
        # save path. Pre-T4 save dropped any field added in later
        # tranches; the explicit forward of trust_circle here is
        # the same pattern T5-T6 will follow.
        trust_circle=profile.trust_circle,
        # ADR-0068 T5 (B315): forward voice + writing samples too.
        voice_samples=profile.voice_samples,
        writing_samples=profile.writing_samples,
        # ADR-0068 T6 (B316): forward financial context.
        financial=profile.financial,
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

    # ADR-0068 T6 (B316): seed currency + tax_residence as HIGH-
    # severity facts when financial context is present. A Finance
    # Guardian agent claiming "your reporting currency is GBP"
    # when the operator's profile says USD is a high-stakes
    # mistake; Reality Anchor catches it before the recommendation
    # lands in operator-facing output.
    if profile.financial is not None:
        seeds.append({
            "id": "operator_currency",
            "severity": "HIGH",
            "statement": (
                f"The operator's reporting currency is "
                f"{profile.financial.currency}."
            ),
            "domain_keywords": [
                "operator", "currency", "finance", "reporting",
            ],
            "canonical_terms": [profile.financial.currency],
            "forbidden_terms": [],
            "source": "operator_profile.yaml",
        })
        seeds.append({
            "id": "operator_tax_residence",
            "severity": "HIGH",
            "statement": (
                f"The operator's tax residence is "
                f"{profile.financial.tax_residence}."
            ),
            "domain_keywords": [
                "operator", "tax residence", "jurisdiction", "finance",
            ],
            "canonical_terms": [profile.financial.tax_residence],
            "forbidden_terms": [],
            "source": "operator_profile.yaml",
        })
        seeds.append({
            "id": "operator_fiscal_year",
            "severity": "MEDIUM",
            "statement": (
                f"The operator's fiscal year starts "
                f"{profile.financial.fiscal_year_start} (MM-DD)."
            ),
            "domain_keywords": [
                "operator", "fiscal year", "tax", "deadline",
            ],
            "canonical_terms": [profile.financial.fiscal_year_start],
            "forbidden_terms": [],
            "source": "operator_profile.yaml",
        })

    # ADR-0068 T4 (B314): seed one fact per trust-circle person.
    # The seed's canonical_terms include the person's name; the
    # Reality Anchor surfaces a contradiction when an agent claims
    # a wrong relationship (e.g. "your accountant is X" when X is
    # actually labeled "spouse" in the operator's profile). Severity
    # HIGH because mis-identifying someone in the operator's trust
    # circle is a higher-stakes mistake than mis-quoting a locale.
    for person in profile.trust_circle:
        seed_id = (
            "operator_trust_"
            + person.relationship.lower().replace(" ", "_")
        )
        statement = (
            f"{person.name} is the operator's "
            f"{person.relationship}."
        )
        if person.email:
            statement += f" Email: {person.email}."
        seeds.append({
            "id": seed_id,
            "severity": "HIGH",
            "statement": statement,
            "domain_keywords": [
                "operator", "trust circle", person.relationship,
            ],
            "canonical_terms": [person.name, person.relationship],
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

    # ADR-0068 T4 (B314): trust_circle is optional + defaults to ().
    # Each entry must be a mapping with at minimum {name, relationship};
    # email + notes are optional. Malformed entries raise rather than
    # silently drop — the operator should see the error and fix the
    # YAML rather than have agents miss a person they expected to be
    # in scope.
    trust_circle = _parse_trust_circle(op.get("trust_circle"), source_path)
    # ADR-0068 T5 (B315): voice_samples + writing_samples. Same
    # forward-compat shape as trust_circle — absent → empty tuple,
    # malformed → raise. No RA seeds; these are operational pointers.
    voice_samples = _parse_voice_samples(
        op.get("voice_samples"), source_path,
    )
    writing_samples = _parse_writing_samples(
        op.get("writing_samples"), source_path,
    )
    # ADR-0068 T6 (B316): financial context. None when absent.
    financial = _parse_financial(op.get("financial"), source_path)

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
        trust_circle=trust_circle,
        voice_samples=voice_samples,
        writing_samples=writing_samples,
        financial=financial,
        extra=extra,
    )


def _parse_trust_circle(
    raw: Any,
    source_path: Path,
) -> tuple[TrustCirclePerson, ...]:
    """Parse the trust_circle list. Returns empty tuple when absent."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise OperatorProfileError(
            f"operator.trust_circle at {source_path} must be a list "
            f"of person mappings; got {type(raw).__name__}"
        )
    people: list[TrustCirclePerson] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise OperatorProfileError(
                f"operator.trust_circle[{idx}] at {source_path} must "
                f"be a mapping; got {type(entry).__name__}"
            )
        for required in ("name", "relationship"):
            if required not in entry:
                raise OperatorProfileError(
                    f"operator.trust_circle[{idx}] at {source_path} "
                    f"missing required field {required!r}"
                )
            if not isinstance(entry[required], str) or not entry[required].strip():
                raise OperatorProfileError(
                    f"operator.trust_circle[{idx}].{required} at "
                    f"{source_path} must be a non-empty string"
                )
        people.append(TrustCirclePerson(
            name=entry["name"],
            relationship=entry["relationship"],
            email=entry.get("email") if entry.get("email") else None,
            notes=entry.get("notes") if entry.get("notes") else None,
        ))
    return tuple(people)


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
    # ADR-0068 T4 (B314): emit trust_circle as a list of person
    # dicts. Optional fields stay omitted when None so the YAML
    # diff stays minimal across writes.
    if profile.trust_circle:
        payload["operator"]["trust_circle"] = [
            _person_to_dict(p) for p in profile.trust_circle
        ]
    # ADR-0068 T5 (B315): voice_samples + writing_samples emit
    # only when non-empty so an operator who hasn't recorded any
    # gets a clean YAML.
    if profile.voice_samples:
        payload["operator"]["voice_samples"] = [
            _voice_sample_to_dict(s) for s in profile.voice_samples
        ]
    if profile.writing_samples:
        payload["operator"]["writing_samples"] = [
            _writing_sample_to_dict(s) for s in profile.writing_samples
        ]
    # ADR-0068 T6 (B316): emit financial only when set. None is the
    # backward-compat default for pre-T6 yamls and stays out of the
    # output entirely.
    if profile.financial is not None:
        payload["operator"]["financial"] = _financial_to_dict(
            profile.financial,
        )
    if profile.extra:
        payload["operator"]["extra"] = dict(profile.extra)

    return yaml.safe_dump(
        payload,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def _person_to_dict(p: TrustCirclePerson) -> dict[str, Any]:
    """Serialize one TrustCirclePerson to a dict. Optional fields
    are omitted when None so the on-disk YAML stays minimal."""
    out: dict[str, Any] = {
        "name": p.name,
        "relationship": p.relationship,
    }
    if p.email is not None:
        out["email"] = p.email
    if p.notes is not None:
        out["notes"] = p.notes
    return out


# ---------------------------------------------------------------------------
# ADR-0068 T5 (B315) — voice + writing samples parsers + serializers
# ---------------------------------------------------------------------------


def _parse_voice_samples(
    raw: Any, source_path: Path,
) -> tuple[VoiceSample, ...]:
    """Parse the voice_samples list. Returns empty tuple when absent."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise OperatorProfileError(
            f"operator.voice_samples at {source_path} must be a list "
            f"of sample mappings; got {type(raw).__name__}"
        )
    samples: list[VoiceSample] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise OperatorProfileError(
                f"operator.voice_samples[{idx}] at {source_path} must "
                f"be a mapping; got {type(entry).__name__}"
            )
        for required in ("phrase", "audio_path"):
            if required not in entry:
                raise OperatorProfileError(
                    f"operator.voice_samples[{idx}] at {source_path} "
                    f"missing required field {required!r}"
                )
            if not isinstance(entry[required], str) or not entry[required].strip():
                raise OperatorProfileError(
                    f"operator.voice_samples[{idx}].{required} at "
                    f"{source_path} must be a non-empty string"
                )
        samples.append(VoiceSample(
            phrase=entry["phrase"],
            audio_path=entry["audio_path"],
            notes=entry.get("notes") if entry.get("notes") else None,
        ))
    return tuple(samples)


def _parse_writing_samples(
    raw: Any, source_path: Path,
) -> tuple[WritingSample, ...]:
    """Parse the writing_samples list. Returns empty tuple when absent."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise OperatorProfileError(
            f"operator.writing_samples at {source_path} must be a list "
            f"of sample mappings; got {type(raw).__name__}"
        )
    samples: list[WritingSample] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise OperatorProfileError(
                f"operator.writing_samples[{idx}] at {source_path} must "
                f"be a mapping; got {type(entry).__name__}"
            )
        for required in ("title", "file_path"):
            if required not in entry:
                raise OperatorProfileError(
                    f"operator.writing_samples[{idx}] at {source_path} "
                    f"missing required field {required!r}"
                )
            if not isinstance(entry[required], str) or not entry[required].strip():
                raise OperatorProfileError(
                    f"operator.writing_samples[{idx}].{required} at "
                    f"{source_path} must be a non-empty string"
                )
        samples.append(WritingSample(
            title=entry["title"],
            file_path=entry["file_path"],
            channel=entry.get("channel") if entry.get("channel") else None,
            notes=entry.get("notes") if entry.get("notes") else None,
        ))
    return tuple(samples)


def _parse_financial(
    raw: Any, source_path: Path,
) -> Optional[FinancialContext]:
    """Parse operator.financial. Returns None when absent."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise OperatorProfileError(
            f"operator.financial at {source_path} must be a mapping; "
            f"got {type(raw).__name__}"
        )
    for required in ("currency", "tax_residence", "fiscal_year_start"):
        if required not in raw:
            raise OperatorProfileError(
                f"operator.financial at {source_path} missing required "
                f"field {required!r}"
            )

    currency = raw["currency"]
    if not isinstance(currency, str) or not _CURRENCY_RE.match(currency):
        raise OperatorProfileError(
            f"operator.financial.currency at {source_path} must be a "
            f"three-letter ISO 4217 code (e.g. 'USD'); got {currency!r}"
        )

    tax_residence = raw["tax_residence"]
    if not isinstance(tax_residence, str) or not _TAX_RESIDENCE_RE.match(
        tax_residence,
    ):
        raise OperatorProfileError(
            f"operator.financial.tax_residence at {source_path} must "
            f"be ISO 3166-1 alpha-2 (optionally with subdivision, e.g. "
            f"'US-CA'); got {tax_residence!r}"
        )

    fiscal_year_start = raw["fiscal_year_start"]
    if not isinstance(fiscal_year_start, str) or not _MMDD_RE.match(
        fiscal_year_start,
    ):
        raise OperatorProfileError(
            f"operator.financial.fiscal_year_start at {source_path} "
            f"must be MM-DD (e.g. '01-01' for calendar year, '04-06' "
            f"for UK personal tax year); got {fiscal_year_start!r}"
        )

    pt_raw = raw.get("preferred_tooling", [])
    if pt_raw is None:
        pt_raw = []
    if not isinstance(pt_raw, list):
        raise OperatorProfileError(
            f"operator.financial.preferred_tooling at {source_path} "
            f"must be a list of strings; got {type(pt_raw).__name__}"
        )
    for idx, t in enumerate(pt_raw):
        if not isinstance(t, str) or not t.strip():
            raise OperatorProfileError(
                f"operator.financial.preferred_tooling[{idx}] at "
                f"{source_path} must be a non-empty string; got {t!r}"
            )

    return FinancialContext(
        currency=currency,
        tax_residence=tax_residence,
        fiscal_year_start=fiscal_year_start,
        preferred_tooling=tuple(pt_raw),
    )


def _financial_to_dict(f: FinancialContext) -> dict[str, Any]:
    """Serialize FinancialContext to a dict. preferred_tooling
    omitted when empty so the YAML stays minimal."""
    out: dict[str, Any] = {
        "currency": f.currency,
        "tax_residence": f.tax_residence,
        "fiscal_year_start": f.fiscal_year_start,
    }
    if f.preferred_tooling:
        out["preferred_tooling"] = list(f.preferred_tooling)
    return out


def _voice_sample_to_dict(s: VoiceSample) -> dict[str, Any]:
    out: dict[str, Any] = {"phrase": s.phrase, "audio_path": s.audio_path}
    if s.notes is not None:
        out["notes"] = s.notes
    return out


def _writing_sample_to_dict(s: WritingSample) -> dict[str, Any]:
    out: dict[str, Any] = {"title": s.title, "file_path": s.file_path}
    if s.channel is not None:
        out["channel"] = s.channel
    if s.notes is not None:
        out["notes"] = s.notes
    return out


def _now_iso() -> str:
    """RFC 3339 UTC timestamp, no sub-second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
