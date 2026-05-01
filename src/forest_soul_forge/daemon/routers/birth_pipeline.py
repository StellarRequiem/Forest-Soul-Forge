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
def write_artifacts(
    soul_path: Path, soul_md: str,
    constitution_path: Path, constitution_yaml: str,
) -> None:
    """Write the paired artifacts.

    Writing constitution first so a crash between the two leaves a
    dangling constitution instead of a soul that points at nothing —
    easier to detect and clean up.
    """
    constitution_path.write_text(constitution_yaml, encoding="utf-8")
    soul_path.write_text(soul_md, encoding="utf-8")


def rollback_artifacts(soul_path: Path, constitution_path: Path) -> None:
    """Best-effort cleanup when the audit append fails.

    Both unlinks are guarded — a partial-write that left only one of
    the two on disk should still clean up what's there. Errors are
    swallowed because the call site is already on the failure path.
    """
    for p in (soul_path, constitution_path):
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
