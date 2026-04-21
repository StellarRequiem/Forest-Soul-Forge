"""Agent DNA — deterministic hash of a trait profile.

DNA is the identity fingerprint of an agent. Two profiles with the same role,
trait values, and domain-weight overrides produce the same DNA, regardless of
field order or timestamp. Two profiles that differ anywhere on those axes
produce different DNA.

Lineage (parent_dna, ancestor chain, spawned_by) is *metadata* and is NOT
hashed into DNA — if it were, every descendant would invalidate its own hash
the moment it was spawned.

Hash: sha256 over a canonical UTF-8 JSON rendering (sorted keys, no whitespace,
integers for trait values, floats for weights). `short()` returns the first 12
hex chars, which is what shows up in soul.md headers — like a git short SHA.

Design reference: docs/decisions/ADR-0002-agent-dna-and-lineage.md
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from forest_soul_forge.core.trait_engine import TraitProfile

SHORT_LEN = 12


def canonical_payload(profile: TraitProfile) -> dict:
    """Return the identity-bearing fields of a profile, sorted and normalized."""
    return {
        "role": profile.role,
        "trait_values": {k: int(profile.trait_values[k]) for k in sorted(profile.trait_values)},
        "domain_weight_overrides": {
            k: float(profile.domain_weight_overrides[k])
            for k in sorted(profile.domain_weight_overrides)
        },
    }


def dna_full(profile: TraitProfile) -> str:
    """Full 64-char sha256 hex digest of the canonical profile."""
    payload = canonical_payload(profile)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def dna_short(profile: TraitProfile) -> str:
    """First 12 hex chars of the full DNA — used in display and frontmatter."""
    return dna_full(profile)[:SHORT_LEN]


def verify(profile: TraitProfile, claimed_dna: str) -> bool:
    """True iff the profile hashes to the claimed DNA (short or full form)."""
    full = dna_full(profile)
    return claimed_dna == full or claimed_dna == full[:SHORT_LEN]


@dataclass(frozen=True)
class Lineage:
    """Ancestral chain for a spawned agent.

    `ancestors` is root-first: ancestors[0] is the root, ancestors[-1] is the
    direct parent. A root agent (spawned by a human, not another agent) has an
    empty ancestors list and parent=None.
    """

    parent_dna: str | None
    ancestors: tuple[str, ...]  # short DNAs, root-first
    spawned_by: str | None  # parent agent name

    @classmethod
    def root(cls) -> "Lineage":
        return cls(parent_dna=None, ancestors=(), spawned_by=None)

    @classmethod
    def from_parent(cls, parent_dna: str, parent_lineage: "Lineage", parent_agent_name: str) -> "Lineage":
        """Build a child lineage: extend the parent's ancestor chain with the parent itself."""
        return cls(
            parent_dna=parent_dna,
            ancestors=parent_lineage.ancestors + (parent_dna,),
            spawned_by=parent_agent_name,
        )

    @property
    def depth(self) -> int:
        """Number of ancestors between this agent and the root. 0 = root agent."""
        return len(self.ancestors)

    def is_root(self) -> bool:
        return self.parent_dna is None
