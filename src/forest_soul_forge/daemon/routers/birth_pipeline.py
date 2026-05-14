"""Pure-function helpers for the /birth and /spawn pipelines.

Extracted from ``writes.py`` during the 2026-04-30 Phase C.2
decomposition. These functions are stateless / I/O-only — given
inputs, return outputs (or perform a single named filesystem
operation) — with no FastAPI / dependency-injection coupling. That
makes them unit-testable in isolation rather than requiring a full
TestClient setup.

What's extracted:
  - **String / path math** — ``safe_agent_name``, ``instance_id_for``,
    ``soul_path_for``
  - **Hashing** — ``derive_constitution_hash``
  - **Adapters** — ``to_agent_out``, ``voice_event_fields``,
    ``chain_entry_to_parsed``
  - **Filesystem** — ``write_artifacts``, ``rollback_artifacts``
  - **Time** — ``idempotency_now``

What stays in writes.py:
  - HTTPException-raising helpers (``_build_trait_profile``,
    ``_parent_lineage_from_registry``, etc.) — they're still pure-
    function-shaped, but raising HTTPException couples them to
    FastAPI in a way that makes "is this a logic helper or a route
    helper?" ambiguous. Keeping them with the routes preserves
    that boundary; future tranches can decide whether to extract.
  - The big ``_perform_create`` orchestrator. It calls into all the
    helpers above plus the FastAPI ones; pulling it would force
    pulling everything.

Naming: the originals were prefixed with ``_`` for module-private.
The extracted versions drop the underscore because they're now
intentionally module-public for the routers that import them. The
import in writes.py uses ``import-as-alias`` so call sites don't
need to change.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from forest_soul_forge.core.at_rest_encryption import (
    EncryptionConfig,
    decrypt_text,
    encrypt_text,
)
from forest_soul_forge.core.audit_chain import ChainEntry
from forest_soul_forge.daemon.schemas import AgentOut
from forest_soul_forge.registry.ingest import ParsedAuditEntry
from forest_soul_forge.soul.voice_renderer import VoiceText


# ---------------------------------------------------------------------------
# String / path math
# ---------------------------------------------------------------------------
def safe_agent_name(name: str) -> str:
    """Filename-safe rendering of the agent name.

    Whitelist-only: letters, digits, hyphen, underscore. Everything
    else becomes underscore. Keeps filenames portable across OSes
    and prevents a malicious agent name from being a traversal vector.
    """
    out: list[str] = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out) or "agent"


def instance_id_for(role: str, dna_short_hex: str, sibling_index: int) -> str:
    """Build the canonical instance_id.

    First sibling (the common case) gets the clean ``role_dna`` form.
    Twins and beyond append ``_N`` so the ID is unique and the suffix
    only appears when it's load-bearing.
    """
    base = f"{role}_{dna_short_hex}"
    return base if sibling_index <= 1 else f"{base}_{sibling_index}"


def soul_path_for(
    out_dir: Path, agent_name: str, instance_id: str
) -> tuple[Path, Path]:
    """Return ``(soul_path, constitution_path)`` under the output dir.

    Side-effect: creates ``out_dir`` if it doesn't exist (idempotent).
    The two paths share the same base — ``<safe_name>__<instance_id>``
    — so the soul + constitution always travel together on disk.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_agent_name(agent_name)
    base = f"{safe}__{instance_id}"
    return out_dir / f"{base}.soul.md", out_dir / f"{base}.constitution.yaml"


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------
def derive_constitution_hash(
    derived_hash: str, constitution_override: str | None
) -> str:
    """Fold an optional override YAML into the constitution hash.

    Path D: when the caller supplies ``constitution_override``, we bind
    its bytes to the agent's constitution hash so tampering with the
    override invalidates verification. When absent, the derived hash is
    used untouched — behavior is identical to the no-override case.
    """
    if not constitution_override:
        return derived_hash
    h = hashlib.sha256()
    h.update(derived_hash.encode("utf-8"))
    h.update(b"\noverride:\n")
    h.update(constitution_override.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
def to_agent_out(row) -> AgentOut:
    """Adapt an ``AgentRow`` registry dataclass to its Pydantic shape."""
    return AgentOut(**asdict(row))


def voice_event_fields(voice: VoiceText | None) -> dict:
    """Optional narrative_* fields for audit event_data.

    Returns an empty dict when voice is None so callers can ``**spread``
    into the event payload without conditionals.
    """
    if voice is None:
        return {}
    return {
        "narrative_provider": voice.provider,
        "narrative_model": voice.model,
        "narrative_generated_at": voice.generated_at,
    }


def chain_entry_to_parsed(entry: ChainEntry) -> ParsedAuditEntry:
    """Lift a :class:`ChainEntry` into a :class:`ParsedAuditEntry`.

    The registry's ``register_birth`` signature takes the parsed form
    (that's what the rebuild path also produces), so we translate once
    here rather than teach the registry two shapes.
    """
    return ParsedAuditEntry(
        seq=entry.seq,
        timestamp=entry.timestamp,
        prev_hash=entry.prev_hash,
        entry_hash=entry.entry_hash,
        agent_dna=entry.agent_dna,
        event_type=entry.event_type,
        event_data=dict(entry.event_data),
    )


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------
# ADR-0050 T5 (B271) — soul + constitution file encryption. When the
# daemon lifespan resolved a master key (FSF_AT_REST_ENCRYPTION=true),
# write_artifacts encrypts both payloads via encrypt_text and writes
# them to ``<original>.enc`` extensions. The ``.enc`` suffix is the
# operator's visual signal — a directory listing shows at a glance
# which agents have plaintext artifacts vs encrypted ones. Mixed
# directories (some agents born under plaintext mode, others under
# encryption) are supported per ADR-0050 Decision 6: the read helpers
# detect the on-disk variant transparently.
def _enc_path(path: Path) -> Path:
    """Return the ``.enc`` sibling for the given plain artifact path."""
    return path.with_name(path.name + ".enc")


def write_artifacts(
    soul_path: Path, soul_md: str,
    constitution_path: Path, constitution_yaml: str,
    encryption_config: Optional[EncryptionConfig] = None,
) -> tuple[Path, Path]:
    """Write the paired artifacts; return the actual paths written.

    Writing constitution first so a crash between the two leaves a
    dangling constitution instead of a soul that points at nothing —
    easier to detect and clean up. This ordering is preserved under
    encryption: encrypted constitution first, then encrypted soul.

    When ``encryption_config`` is set (ADR-0050 T5):
      - both payloads are encrypted via :func:`encrypt_text`
      - both are written to ``<original_path>.enc`` extensions
      - the returned ``(soul_actual, const_actual)`` tuple carries
        the on-disk paths — callers tracking artifact paths in the
        audit log / registry get the real disk locations

    When ``encryption_config`` is None (default), payloads are
    written plaintext to the requested paths. Returned tuple equals
    the input paths unchanged. Pre-T5 callers that ignore the return
    value continue to work — the side-effect is identical to the
    pre-T5 behavior.
    """
    if encryption_config is not None:
        soul_actual = _enc_path(soul_path)
        const_actual = _enc_path(constitution_path)
        const_payload = encrypt_text(constitution_yaml, encryption_config)
        soul_payload = encrypt_text(soul_md, encryption_config)
    else:
        soul_actual = soul_path
        const_actual = constitution_path
        const_payload = constitution_yaml
        soul_payload = soul_md

    const_actual.write_text(const_payload, encoding="utf-8")
    soul_actual.write_text(soul_payload, encoding="utf-8")
    return soul_actual, const_actual


def read_soul_md(
    soul_path: Path,
    encryption_config: Optional[EncryptionConfig] = None,
) -> str:
    """Read soul.md transparently; decrypt the ``.enc`` variant if present.

    Detection order:
      1. ``soul_path.enc`` exists → encrypted variant; decrypt via
         :func:`decrypt_text`. ``encryption_config`` is required;
         passing None raises a clear error rather than failing
         downstream with cryptic base64-decode noise.
      2. ``soul_path`` exists → plaintext (legacy or operator opt-out);
         return the raw text.
      3. Neither exists → propagate the :class:`FileNotFoundError`
         from the plaintext read. Caller already handles this
         (e.g., character_sheet returns 404).

    Mixed deployments (some encrypted, some plaintext) are explicitly
    supported per ADR-0050 Decision 6 — operators can flip the env
    var at any agent boundary; existing agents stay on whatever shape
    they were birthed under until rotated by the T8 CLI.
    """
    enc_path = _enc_path(soul_path)
    if enc_path.exists():
        if encryption_config is None:
            raise RuntimeError(
                f"soul artifact is encrypted at {enc_path} but no "
                f"encryption_config was provided; operator must enable "
                f"FSF_AT_REST_ENCRYPTION with the same master key the "
                f"agent was birthed under"
            )
        return decrypt_text(
            enc_path.read_text(encoding="utf-8"),
            encryption_config,
        )
    return soul_path.read_text(encoding="utf-8")


def read_constitution_yaml(
    constitution_path: Path,
    encryption_config: Optional[EncryptionConfig] = None,
) -> str:
    """Read constitution.yaml transparently; decrypt the ``.enc`` variant.

    Mirror of :func:`read_soul_md`; same detection order, same error
    semantics. Returns the raw YAML text — callers parse with
    ``yaml.safe_load`` after this.
    """
    enc_path = _enc_path(constitution_path)
    if enc_path.exists():
        if encryption_config is None:
            raise RuntimeError(
                f"constitution artifact is encrypted at {enc_path} but "
                f"no encryption_config was provided; operator must enable "
                f"FSF_AT_REST_ENCRYPTION with the same master key the "
                f"agent was birthed under"
            )
        return decrypt_text(
            enc_path.read_text(encoding="utf-8"),
            encryption_config,
        )
    return constitution_path.read_text(encoding="utf-8")


def write_soul_md(
    soul_path: Path,
    soul_md: str,
    encryption_config: Optional[EncryptionConfig] = None,
) -> Path:
    """Rewrite soul.md in place; preserves the on-disk variant.

    Used by the voice renderer's regenerate path: read existing
    soul → modify in memory → write back. If the existing soul is
    encrypted (``.enc`` variant on disk), the new content is also
    written encrypted; if it was plaintext, the rewrite stays
    plaintext. The on-disk variant is sticky per agent — operators
    can't accidentally downgrade an encrypted soul to plaintext via
    a voice rewrite.

    Returns the actual path written.
    """
    enc_path = _enc_path(soul_path)
    if enc_path.exists():
        if encryption_config is None:
            raise RuntimeError(
                f"cannot rewrite encrypted soul at {enc_path} without "
                f"encryption_config; voice rewrite refused to downgrade "
                f"to plaintext"
            )
        enc_path.write_text(
            encrypt_text(soul_md, encryption_config),
            encoding="utf-8",
        )
        return enc_path
    soul_path.write_text(soul_md, encoding="utf-8")
    return soul_path


def rollback_artifacts(soul_path: Path, constitution_path: Path) -> None:
    """Best-effort cleanup when the audit append fails.

    Both unlinks are guarded — a partial-write that left only one of
    the two on disk should still clean up what's there. Errors are
    swallowed because the call site is already on the failure path.

    Tries both the plain path AND the ``.enc`` variant so the rollback
    is correct regardless of which write_artifacts mode was used.
    """
    for p in (soul_path, constitution_path, _enc_path(soul_path),
              _enc_path(constitution_path)):
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------
def idempotency_now() -> str:
    """ISO-8601 UTC timestamp for the idempotency-cache row."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
