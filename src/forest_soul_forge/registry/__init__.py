"""Registry package — SQLite index over canonical Forest Soul Forge artifacts.

See docs/decisions/ADR-0006-registry-as-index.md for the layering story.
Canonical artifacts (soul.md / constitution.yaml / audit/chain.jsonl) are the
source of truth; this package maintains a derived index that can be rebuilt
from scratch at any time.
"""
from forest_soul_forge.registry.registry import (
    Registry,
    RegistryError,
    REGISTRY_SCHEMA_VERSION,
)

__all__ = [
    "Registry",
    "RegistryError",
    "REGISTRY_SCHEMA_VERSION",
]
