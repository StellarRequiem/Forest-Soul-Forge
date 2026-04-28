"""Tool runtime base types — ADR-0019 T1.

Every tool the runtime can invoke implements :class:`Tool`. Tools take
arguments + a context, validate, execute, return a :class:`ToolResult`.
The runtime owns:

- constraint resolution (per-tool, derived from the agent's constitution)
- approval gating (when ``requires_human_approval`` is true)
- audit-chain emission (one entry per call, hashed args + result digest)
- per-session counters (max_calls_per_session enforcement)
- accounting (tokens_used + cost_usd plumbing)

T1 ships the data contracts and the registry; the dispatcher itself
lands in T2 alongside the registry schema v3 bump.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Tool side_effects values must mirror tool_catalog.SIDE_EFFECT_VALUES.
# Re-exported here so tools can validate self-consistency without
# importing the catalog directly (catalog is loaded by the daemon, but
# tool implementations sit one layer below the daemon).
SIDE_EFFECTS_VALUES = ("read_only", "network", "filesystem", "external")


class ToolError(Exception):
    """Base class for tool-runtime failures the runtime should surface
    as ``tool_call_rejected`` events. Subclass for specific shapes."""


class ToolValidationError(ToolError):
    """Raised by ``Tool.validate`` when arguments are malformed.

    Surfaces as a ``tool_call_rejected`` event with reason="bad_args".
    Caught by the runtime BEFORE constraint resolution so a typo in the
    LLM's emitted call doesn't get charged against max_calls_per_session.
    """


@dataclass(frozen=True)
class ToolContext:
    """The runtime's gift to the tool at dispatch time.

    Carries everything a tool implementation needs to do its job
    *without* leaking tool implementations into the agent runtime
    machinery. A pure-function tool can ignore most of it; an
    LLM-wrapping tool reaches for ``provider``; a tool that emits
    additional audit events on its own behalf reaches for ``logger``.

    Note: tools do NOT write audit-chain entries directly. The runtime
    wraps every dispatch in a ``tool_invoked`` entry. Tools that need
    to expose extra detail surface it via ``ToolResult.metadata``.
    """

    instance_id: str
    agent_dna: str
    role: str
    genre: str | None
    session_id: str | None
    constraints: dict[str, Any] = field(default_factory=dict)
    # ``provider`` is the active model provider. None when the runtime
    # is dispatching a non-LLM tool. Typed as Any to avoid forcing
    # tools to import the provider module — most tools don't need it.
    provider: Any = None
    # Operator-facing logger for diagnostic output. Tools should NOT
    # use this for behavior records — those go in ToolResult.metadata
    # so they're hashed into the audit chain.
    logger: Any = None
    # ADR-0022 v0.1 — bound Memory instance for the calling agent.
    # The memory_recall.v1 tool reads from this. Daemon populates;
    # tests can pass via ``constraints["memory"]`` for in-memory
    # exercises that don't construct a full Memory.
    memory: Any = None
    # ADR-0033 A3 — pre-bound delegate callable for cross-agent skill
    # invocation. The dispatcher builds this per-call via a
    # delegator_factory that captures registry/audit/settings; the
    # callable already has the caller's identity baked in, so the
    # delegate.v1 tool only needs to pass target + skill + inputs +
    # reason. None when the daemon didn't wire delegation (test
    # contexts that don't need cross-agent calls); the tool refuses
    # cleanly in that case rather than crashing.
    delegate: Any = None
    # ADR-0033 A6 — bound PrivClient instance for privileged-ops tools
    # (isolate_process.v1, dynamic_policy.v1, tamper_detect.v1's SIP
    # path). The daemon's lifespan calls assert_available() at boot;
    # if the helper isn't installed, this stays None and the
    # privileged tools refuse cleanly with "helper not wired" so the
    # daemon stays up and only those tools degrade.
    priv_client: Any = None


@dataclass(frozen=True)
class ToolResult:
    """What a tool returns from ``execute``.

    ``output`` is the value the agent gets back. ``metadata`` is anything
    the tool wants the audit trail to capture *beyond* the output —
    statistics, warnings, partial-result indicators. Both are JSON-
    serializable; the runtime serializes-and-hashes both into the
    ``tool_invoked`` event's ``result_digest``.

    ``tokens_used`` and ``cost_usd`` are populated by tools that wrap
    LLM calls — the model provider returns them and the tool plumbs
    them through. Pure-function tools return ``None`` for both.

    ``side_effect_summary`` is the operator-facing description of what
    the tool DID. Used by the approval-queue UI on subsequent calls of
    the same tool ("you previously approved this; here's what
    happened — approve again?"). One short sentence is plenty.
    """

    output: Any
    metadata: dict[str, Any] = field(default_factory=dict)
    tokens_used: int | None = None
    cost_usd: float | None = None
    side_effect_summary: str | None = None

    def result_digest(self) -> str:
        """SHA-256 hash of canonical-JSON output+metadata.

        The runtime emits this in the audit-chain entry. Full output
        (which can be large — a packet capture summary, a 10k log
        sample) lives in the registry's ``tool_calls`` table once the
        T2 schema bump lands. Hashing-only in the chain keeps chain
        size reasonable for high-traffic agents.
        """
        body = {"output": self.output, "metadata": dict(sorted(self.metadata.items()))}
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@runtime_checkable
class Tool(Protocol):
    """The contract every runtime-invokable tool implements.

    Implementations live in ``src/forest_soul_forge/tools/`` (built-in)
    or get loaded from ``.fsf`` plugin packages (operator-installed,
    ADR-0019 T5). Either way the registry maps ``(name, version)`` to
    a Tool instance and dispatches against this Protocol.
    """

    name: str
    version: str
    side_effects: str  # must be in SIDE_EFFECTS_VALUES

    def validate(self, args: dict[str, Any]) -> None:
        """Raise :class:`ToolValidationError` if args are malformed.

        Called BEFORE constraint resolution and BEFORE counter increment
        so a typo fails fast without burning the agent's call budget.
        Tools without args (or with permissive args) implement this as
        a no-op.
        """
        ...

    async def execute(
        self,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        """Run the tool. Async so I/O-bound tools (network calls, file
        reads, LLM dispatches) don't block the runtime's event loop.

        Pure-function tools that don't actually await anything still
        declare async — the runtime always awaits the result, and a
        pure async fn that does no awaiting is essentially free.

        The runtime catches ``ToolError`` subclasses and emits
        ``tool_call_rejected``. Anything else (KeyError, RuntimeError)
        is caught at the runtime boundary and converted into a
        ``tool_call_rejected`` with reason="unexpected_exception" plus
        a stack-trace fingerprint — operators see "this tool crashed,"
        agents see a clean error result.
        """
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
@dataclass
class ToolRegistry:
    """Map of ``(name, version) → Tool``. Loaded at daemon lifespan;
    held on ``app.state.tool_registry``.

    NOT frozen — T5 adds plugin hot-reload, which mutates the map at
    runtime. The dataclass is mutable but its public surface is
    documented as add/lookup; callers don't reach in directly.

    Names + versions must match catalog entries. The lifespan runs an
    integrity check: every registered Tool's ``(name, version)`` MUST
    correspond to a real ``ToolDef`` in the loaded catalog, and the
    Tool's ``side_effects`` MUST equal the catalog's. Mismatch is a
    fatal lifespan failure surfaced on /healthz — better to refuse to
    boot than to dispatch a tool whose registered side_effects diverge
    from what the constitution-build path assumed.
    """

    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        """Add a tool to the registry. Raises if a duplicate
        (name, version) is already registered."""
        if tool.side_effects not in SIDE_EFFECTS_VALUES:
            raise ToolError(
                f"tool {tool.name!r}.v{tool.version} declares unknown "
                f"side_effects {tool.side_effects!r}; must be one of "
                f"{list(SIDE_EFFECTS_VALUES)}"
            )
        key = self._key(tool.name, tool.version)
        if key in self.tools:
            raise ToolError(f"duplicate tool registered: {key}")
        self.tools[key] = tool

    def get(self, name: str, version: str) -> Tool | None:
        return self.tools.get(self._key(name, version))

    def has(self, name: str, version: str) -> bool:
        return self._key(name, version) in self.tools

    def all_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.tools.keys()))

    @staticmethod
    def _key(name: str, version: str) -> str:
        return f"{name}.v{version}"


def empty_registry() -> ToolRegistry:
    """Empty registry — used as the lifespan fallback when load fails.
    Mirrors the empty_catalog / empty_engine pattern."""
    return ToolRegistry()
