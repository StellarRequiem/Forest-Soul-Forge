"""Memory subsystem helpers — module-level constants, errors, dataclass,
and pure helper functions extracted per ADR-0040 §5+§7.

Originally lived at the top + bottom of `core/memory.py` (a 1177-line
god object); split into this module on Burst 72 of 2026-05-02 so that
agent allowed_paths constraints can target this file specifically
(it's the read-only constants + dataclass surface — no trust-surface
mutations happen here).

The Memory class itself remains in `core/memory/__init__.py` and
imports from this module. Public API at the package level
(`from forest_soul_forge.core.memory import X`) is preserved exactly
via the package __init__.py re-exports.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LAYERS = ("episodic", "semantic", "procedural")
SCOPES = ("private", "lineage", "realm", "consented")

# v0.2 (ADR-0033 / ADR-0022 v0.2) recall modes — control how widely
# the reader can see across other agents' memory stores.
#
#   private   — owner-only, scope='private'. Default. Equivalent to v0.1.
#   lineage   — owner's private+lineage entries + lineage_chain peers'
#               scope='lineage' entries. The swarm's escalation path
#               (security_low → security_mid → security_high).
#   consented — lineage + scope='consented' entries the reader has an
#               active grant for in memory_consents.
#
# `realm` is unreachable until federation lands (Horizon 3); deliberately
# omitted from RECALL_MODES so an attempt to use it raises a clear error
# instead of silently returning empty results.
RECALL_MODES = ("private", "lineage", "consented")

# Genre privacy floors per ADR-0027 §5. The mapping is keyed by genre
# name and gives the **widest scope the genre is allowed to write**.
# Companion is the strictest. Genres absent from this map default to
# "consented" (no enforced ceiling beyond the four scopes themselves).
GENRE_CEILINGS: dict[str, str] = {
    "companion":   "private",
    "observer":    "lineage",
    "investigator": "lineage",
    "researcher":  "consented",
    "communicator": "realm",
    # actuator + guardian are operator-defined per deployment;
    # default to "consented" until explicitly tightened.
}

_SCOPE_RANK = {"private": 0, "lineage": 1, "realm": 2, "consented": 3}

# ADR-0027-amendment §7.1 — six-class enum for ``claim_type``. Schema-level
# CHECK constraint enforces these values; this Python tuple is the source
# of truth for write-time validation. The default 'observation' is the
# safest classification (immutable, high-confidence by default).
CLAIM_TYPES: tuple[str, ...] = (
    "observation",      # direct event log; high reliability
    "user_statement",   # operator-stated; reliability bounded by operator
    "agent_inference",  # agent-derived; explicitly NOT operator's stated word
    "preference",       # operator's stated preference
    "promise",          # operator's stated commitment with implicit deadline
    "external_fact",    # claim sourced outside the agent-operator dyad
)
_CLAIM_TYPE_SET = frozenset(CLAIM_TYPES)

# ADR-0027-amendment §7.2 — three-state confidence. Float confidence
# invites agents to rationalize precision they don't have ("0.73") that
# means nothing the operator can interpret. Three-state aligns with UI.
CONFIDENCE_LEVELS: tuple[str, ...] = ("low", "medium", "high")
_CONFIDENCE_SET = frozenset(CONFIDENCE_LEVELS)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class MemoryError(Exception):
    """Base class for memory subsystem failures."""


class MemoryScopeViolation(MemoryError):
    """Raised when a write exceeds the genre's ceiling.

    ADR-0027 §5 — genre privacy floors are HARD CEILINGS, not
    defaults. The caller must narrow the scope or the operator must
    explicitly override (with the override hitting the audit chain
    as ``memory_scope_override``).
    """


class UnknownLayerError(MemoryError):
    pass


class UnknownClaimTypeError(MemoryError):
    """Raised when a write specifies an unknown claim_type. v11 addition
    (ADR-0027-amendment §7.1)."""


class UnknownConfidenceError(MemoryError):
    """Raised when a write specifies a confidence outside the three-state
    enum (low/medium/high). v11 addition (ADR-0027-amendment §7.2)."""


class UnknownScopeError(MemoryError):
    pass


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MemoryEntry:
    """One row from the memory_entries table.

    The trailing three fields (added in schema v7 / ADR-0022 v0.2) are
    populated only on **disclosed-copy** rows on the recipient's side
    per ADR-0027 §4 minimum-disclosure rule. On originating-side rows
    they are ``None``.

    A row with ``disclosed_from_entry`` set means: "this is a reference
    copy I was told about by another agent, not an original observation
    of mine." Tools that surface memory to operators or LLMs should
    distinguish the two — the summary string is intentionally narrower
    than the original entry's content.
    """

    entry_id: str
    instance_id: str
    agent_dna: str
    layer: str
    scope: str
    content: str
    content_digest: str
    tags: tuple[str, ...]
    consented_to: tuple[str, ...]
    created_at: str
    deleted_at: str | None = None
    disclosed_from_entry: str | None = None
    disclosed_summary: str | None = None
    disclosed_at: str | None = None
    # v11 additions (ADR-0027-amendment §7) — epistemic metadata.
    # Defaults match the schema CHECK column DEFAULTs so that v10-shape
    # in-memory test fixtures (which still use the old append() args)
    # land on the safe classification.
    claim_type: str = "observation"
    confidence: str = "medium"
    last_challenged_at: str | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_disclosed_copy(self) -> bool:
        """True iff this row is a disclosed copy on a recipient store
        (not an original observation). Useful for UI rendering and for
        the audit trail summary."""
        return self.disclosed_from_entry is not None


# ---------------------------------------------------------------------------
# Memory class
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


_OVERLAP_STOPWORDS: frozenset[str] = frozenset({
    # English stopwords trimmed to the high-impact set; the goal is
    # signal-preservation for the Verifier's word-overlap heuristic
    # (ADR-0036 §2.1), not full NLP.
    "a", "an", "the",
    "is", "was", "are", "were", "be", "been", "being",
    "and", "or", "but", "not",
    "i", "you", "he", "she", "it", "we", "they",
    "my", "your", "his", "her", "its", "our", "their",
    "me", "him", "us", "them",
    "this", "that", "these", "those",
    "of", "in", "on", "at", "to", "for", "by", "with", "from",
    "as", "if", "than", "then",
    "do", "does", "did", "have", "has", "had",
    "will", "would", "should", "could", "may", "might", "can",
    "so", "very", "just",
})


def _tokenize_for_overlap(content: str) -> frozenset[str]:
    """Lowercase + split on non-alphanumerics + drop stopwords + drop
    short tokens. Used by ``find_candidate_pairs``. Two entries pair
    when their token sets share >= min_overlap distinct words.
    """
    import re
    raw = re.findall(r"[a-zA-Z0-9]+", content.lower())
    return frozenset(
        t for t in raw
        if len(t) >= 3 and t not in _OVERLAP_STOPWORDS
    )


def _row_to_entry(row) -> MemoryEntry:
    # The v7 disclosed_* columns and v11 claim_type/confidence/
    # last_challenged_at columns may be absent on a row from an older
    # in-memory test fixture or a registry that hasn't been migrated
    # yet. Defensively probe via row.keys() so this helper works on
    # every shape — important for Memory unit tests that build their
    # own SQLite without going through Registry.bootstrap.
    keys = row.keys() if hasattr(row, "keys") else ()
    return MemoryEntry(
        entry_id=row["entry_id"],
        instance_id=row["instance_id"],
        agent_dna=row["agent_dna"],
        layer=row["layer"],
        scope=row["scope"],
        content=row["content"],
        content_digest=row["content_digest"],
        tags=tuple(json.loads(row["tags_json"] or "[]")),
        consented_to=tuple(json.loads(row["consented_to_json"] or "[]")),
        created_at=row["created_at"],
        deleted_at=row["deleted_at"],
        disclosed_from_entry=row["disclosed_from_entry"]
            if "disclosed_from_entry" in keys else None,
        disclosed_summary=row["disclosed_summary"]
            if "disclosed_summary" in keys else None,
        disclosed_at=row["disclosed_at"]
            if "disclosed_at" in keys else None,
        # v11 — defensive: pre-migration rows lack these columns. Defaults
        # match the schema CHECK column DEFAULTs ('observation', 'medium')
        # so a v10-shape row reads as an observation at medium confidence.
        claim_type=row["claim_type"]
            if "claim_type" in keys else "observation",
        confidence=row["confidence"]
            if "confidence" in keys else "medium",
        last_challenged_at=row["last_challenged_at"]
            if "last_challenged_at" in keys else None,
    )
