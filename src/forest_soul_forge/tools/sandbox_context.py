"""``SerializableToolContext`` — the pickle-safe subset of ``ToolContext``.

ADR-0051 T1.2 (B261).

When a tool runs inside a subprocess sandbox (per ADR-0051 Decision 2),
the daemon needs to ship the tool's args + context across a process
boundary. The full :class:`forest_soul_forge.tools.base.ToolContext`
carries live daemon handles that CANNOT survive a pickle:

  - ``provider``       — bound provider with HTTP client + creds
  - ``logger``         — bound logger instance
  - ``memory``         — bound Memory backed by SQLite cursor
  - ``delegate``       — closure capturing audit chain + write_lock
  - ``priv_client``    — bound PrivClient with a UNIX-socket fd
  - ``secrets``        — SecretsAccessor with master-key handle
  - ``agent_registry`` — Registry with SQLite connection
  - ``procedural_shortcuts`` — Table with SQLite cursor

These all need to stay in the daemon. The sandboxed subprocess can't
have them; that's exactly the point of sandboxing.

This module defines the JSON-safe shape that DOES cross the boundary:

  - ``instance_id``  — calling agent's instance id
  - ``agent_dna``    — short DNA (12-char prefix typically)
  - ``role``         — agent's role string
  - ``genre``        — agent's genre (or None for ungenred)
  - ``session_id``   — optional session correlation id
  - ``constraints``  — JSON-safe subset of the constraints dict
                        (allowed_paths, allowed_commands,
                        allowed_hosts, allowed_mcp_servers,
                        context_cap_tokens). Live registry refs
                        are dropped.

Tools that need the dropped fields (memory_*, delegate, llm_think)
are exactly the ones that MUST opt out of sandbox via
``sandbox_eligible: false`` in tool_catalog.yaml (per ADR-0051
Decision 3). Those tools continue to run in-process under any
``FSF_TOOL_SANDBOX`` mode.

Hydration shape: ``to_tool_context()`` rebuilds a :class:`ToolContext`
inside the subprocess with the seven non-serializable fields set to
``None``. Sandbox-eligible tools structurally don't touch those
fields, so the None values never get accessed; if a tool DOES try to
touch them, it raises ``AttributeError`` on ``None`` access — which
surfaces as a clean tool failure rather than a silent miscompute.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Constraints keys that ARE JSON-safe and MUST be carried across the
# subprocess boundary. Anything else is dropped during serialization.
# Add to this set when a new constraint key is introduced + is
# actually consumed by sandbox-eligible tools.
_SERIALIZABLE_CONSTRAINT_KEYS: frozenset[str] = frozenset({
    "allowed_paths",
    "allowed_commands",
    "allowed_hosts",
    "allowed_mcp_servers",
    "context_cap_tokens",
    "max_calls_per_session",
    "tokens_per_call_cap",
    "schema_version",
})


def _filter_constraints(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only the JSON-safe constraint keys.

    Drops keys whose values are live handles (e.g., ``"mcp_registry"``
    sometimes carries a callable registry view in test fixtures).
    Errors on the side of dropping: if a key isn't on the allowlist
    above, it doesn't cross the boundary even if its value happens to
    be pickleable today.
    """
    if not raw:
        return {}
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in _SERIALIZABLE_CONSTRAINT_KEYS:
            continue
        # Coerce tuples to lists for round-trip stability; YAML loaders
        # produce lists, but in-process callers sometimes pass tuples
        # for immutability. Pickle handles both, but normalizing here
        # avoids subtle assertEqual surprises in tests.
        if isinstance(v, tuple):
            out[k] = list(v)
        else:
            out[k] = v
    return out


@dataclass(frozen=True)
class SerializableToolContext:
    """The pickle-safe slice of a ``ToolContext``.

    Frozen because the subprocess worker holds it as immutable input
    (matches ToolContext's posture). Round-trips through pickle.dumps
    / pickle.loads losslessly.
    """

    instance_id: str
    agent_dna: str
    role: str
    genre: str | None = None
    session_id: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_tool_context(cls, ctx: Any) -> "SerializableToolContext":
        """Project a full :class:`ToolContext` down to the serializable
        slice.

        ``ctx`` typed as Any (not ToolContext) so this module doesn't
        force the heavy ``base`` import at top level — the subprocess
        worker is started cold and benefits from a lean import graph.

        Drops every live-handle field (provider, logger, memory,
        delegate, priv_client, secrets, agent_registry,
        procedural_shortcuts) and filters ``constraints`` to the
        JSON-safe allowlist.
        """
        return cls(
            instance_id=str(getattr(ctx, "instance_id", "")),
            agent_dna=str(getattr(ctx, "agent_dna", "")),
            role=str(getattr(ctx, "role", "")),
            genre=getattr(ctx, "genre", None),
            session_id=getattr(ctx, "session_id", None),
            constraints=_filter_constraints(
                getattr(ctx, "constraints", None) or {},
            ),
        )

    def to_tool_context(self) -> Any:
        """Re-hydrate a full ``ToolContext`` inside the subprocess worker.

        The seven non-serializable fields are set to None. Tools that
        touch them while running sandboxed are by definition
        ``sandbox_eligible: false`` and shouldn't be running in the
        subprocess at all — the dispatcher gate (ADR-0051 T4) catches
        that case before spawning the worker. If a tool slips through
        and tries to read ``ctx.provider``, the AttributeError on None
        access surfaces as a tool failure that the dispatcher
        translates into a ``tool_call_failed`` audit event.
        """
        # Late import — subprocess worker pays the import cost only
        # when actually rehydrating, not at module load.
        from forest_soul_forge.tools.base import ToolContext

        return ToolContext(
            instance_id=self.instance_id,
            agent_dna=self.agent_dna,
            role=self.role,
            genre=self.genre,
            session_id=self.session_id,
            constraints=dict(self.constraints),
            provider=None,
            logger=None,
            memory=None,
            delegate=None,
            priv_client=None,
            secrets=None,
            agent_registry=None,
            procedural_shortcuts=None,
        )
