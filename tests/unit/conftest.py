"""Shared fixtures for unit tests.

Centralizes the FK-seeding helper that several test files need: SQLite
foreign-key enforcement is enabled in ``schema.CONNECTION_PRAGMAS``, so
any test that exercises a downstream table (memory_entries,
tool_call_pending_approvals, tool_call_counters, etc.) must first seed
a row in ``agents`` matching the ``instance_id`` it's about to use.

The Phase A audit (2026-04-30) traced 43 FK-constraint failures across
the unit suite to this single missing-seed pattern. The helper here is
the durable fix.
"""
from __future__ import annotations

from typing import Any


def seed_stub_agent(
    registry: Any,
    instance_id: str = "agent_a",
    *,
    role: str = "network_watcher",
    parent_instance: str | None = None,
) -> None:
    """Insert a minimal agent row so FK constraints on dependent tables
    are satisfied during unit testing.

    Reaches into the registry's underlying connection deliberately — the
    public ``register_birth`` API requires a full ``ParsedSoul`` with
    on-disk artifacts, which is overkill when the test only needs the
    row to exist as a foreign-key target.

    The row is created with stub paths and a placeholder DNA. Tests that
    care about specific agent metadata should still go through the real
    registration path.

    Args:
      registry: a live ``Registry`` instance (typically
                ``Registry.bootstrap(path)`` in test fixtures).
      instance_id: the agent ID to seed. Default ``"agent_a"`` matches
                   the most common test default.
      role: the role string. Default ``"network_watcher"``.
      parent_instance: optional parent for lineage tests; default None.
    """
    registry._conn.execute(
        "INSERT OR IGNORE INTO agents ("
        "  instance_id, dna, dna_full, role, agent_name, parent_instance,"
        "  owner_id, model_name, model_version, soul_path, constitution_path,"
        "  constitution_hash, created_at, status, legacy_minted, sibling_index"
        ") VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, 0, 1)",
        (
            instance_id,
            "stub_dna_short",
            "stub_dna_full_" + ("x" * 50),
            role,
            f"StubAgent_{instance_id}",
            parent_instance,
            f"/tmp/stub-{instance_id}.soul.md",
            f"/tmp/stub-{instance_id}.constitution.yaml",
            "stub_constitution_hash",
            "2026-04-27T00:00:00Z",
            "active",
        ),
    )
    registry._conn.commit()
