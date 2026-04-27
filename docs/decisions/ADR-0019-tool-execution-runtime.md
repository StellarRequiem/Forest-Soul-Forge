# ADR-0019 — Tool execution runtime

- **Status:** Proposed
- **Date:** 2026-04-26
- **Supersedes:** —
- **Related:** ADR-0004 (constitution builder — the rulebook the runtime enforces), ADR-0005 (audit chain — the runtime emits per-call entries here), ADR-0006 (artifact-authoritative storage), ADR-0007 (FastAPI daemon — the host process), ADR-0008 (local-first model provider — the runtime fans tool calls through the active provider's tool-use loop), ADR-0017 (voice renderer — adjacent runtime concern, same provider machinery), ADR-0018 (tool catalog — the source of truth for what's invokable), ADR-0021 (role genres — the runtime checks genre's provider_constraint at call time), ADR-0022 (memory subsystem — runtime writes to it), ADR-0023 (benchmark suite — runtime is what gets benchmarked).
- **Prior art note:** [docs/notes/agnt-prior-art.md](../notes/agnt-prior-art.md) §"Versioned plugin-package format" §"Per-tool execution accounting" §"MCP integration" — patterns we lean on with our own implementation.

## Context

After ADR-0018 + ADR-0021, agents have a **kit** (specific tool refs from a catalog) with **per-tool constraints** (max_calls_per_session, requires_human_approval, audit_every_call) carved into their constitution_hash. ADR-0017 gave them a **voice**. ADR-0006 gave them a **provenance trail**. ADR-0022 will give them **memory**. ADR-0023 will give them **measurement**.

What they cannot do yet is **work**.

The constitution declares what tools the agent has access to. The catalog declares what each tool does in principle. The constraints declare under what conditions a tool can be called. **There is no component that actually invokes a tool inside an agent's session.** Without one, the entire trait/genre/constraint machinery is a configuration system for agents that can't yet run.

The tool execution runtime is the missing piece that turns "agent with configured kit" into "agent that performed work." Concretely:

- Receives a tool-call request (from an LLM-driven agent loop, an operator-issued direct invocation, a workflow node, etc.)
- Resolves the request against the agent's loaded constitution + kit + constraint policy
- Decides: allow autonomously / route through human approval / refuse with rationale
- For allowed calls: dispatches to the tool's implementation, captures inputs/outputs/errors
- For approval-required calls: holds the call in a queue, surfaces an approval prompt, only dispatches after the human signs off
- Writes a `tool_invoked` audit-chain entry per call (decision, resolved arguments, result digest, latency, token/cost if any)
- Updates per-session counters (max_calls_per_session enforcement)
- Returns the call result to the requester

The constitution is the spec; the runtime is the executor that **does what the spec says**. Without a runtime, the spec might as well be a static document.

Three shapes for the runtime were considered:

1. **Inline-with-the-LLM-call** — the runtime is a function the model provider calls during a tool-use generation. The LLM emits `tool_call(name, args)`, the runtime dispatches synchronously, returns the result to the next decoding step. **Strength:** matches the OpenAI / Anthropic tool-use API shape exactly. **Weakness:** synchronous within the LLM call means a slow tool blocks the model's wall-clock; approval gating becomes a long-pause-on-streaming problem; failure in the tool collapses the LLM session.

2. **Separate background queue** — every tool call is enqueued, the LLM session pauses awaiting result, a worker dispatches off-thread. **Strength:** approval-required calls are a natural fit (they're already async). **Weakness:** simple read-only tool calls round-trip slower than they need to; the queue is one more piece of infrastructure (and durability question) on top of an already-busy stack.

3. **Tiered: synchronous fast path + async slow path** — read-only tools with no approval requirement run inline (path 1); anything requiring approval, anything with side effects, and anything taking longer than a threshold runs through the queue (path 2). The runtime decides at call time which path to take based on the resolved constraints. **Strength:** fast tools are fast, gated tools are gated, no operator-confusion about "why did this read take 5 seconds." **Weakness:** two code paths to maintain. **Selected.**

## Decision

### Runtime architecture

```
                         ┌─────────────────────────────┐
   tool_call request ──▶ │ Tool Execution Runtime      │
   (from LLM loop,       │                             │
    operator, workflow)  │  1. resolve_constraints     │
                         │  2. dispatch_decision       │
                         │  3. fast path  OR  queue    │
                         │  4. emit audit entry        │
                         │  5. update counters         │
                         └────┬────────────────────────┘
                              │
              ┌───────────────┼─────────────────────┐
              ▼               ▼                     ▼
    ┌─────────────────┐ ┌─────────────────┐ ┌───────────────────┐
    │ fast path       │ │ approval queue  │ │ refusal           │
    │ (read_only,     │ │ (filesystem,    │ │ (constraint       │
    │  no approval)   │ │  external,      │ │  forbids /        │
    │                 │ │  long-running)  │ │  genre violation) │
    │ → tool impl     │ │ → operator yes  │ │ → 403 + audit     │
    │ → result        │ │ → tool impl     │ └───────────────────┘
    │ → audit         │ │ → result        │
    │ → return        │ │ → audit         │
    └─────────────────┘ │ → return        │
                        └─────────────────┘
```

Three call paths, one runtime entry point.

### Tool implementation contract

Every tool is a Python class implementing this interface:

```python
class Tool(Protocol):
    name: str           # matches catalog entry, e.g. "packet_query"
    version: str        # matches catalog entry, e.g. "1"
    side_effects: str   # mirrors catalog declaration (sanity check)

    async def execute(
        self,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult: ...

    def validate(self, args: dict[str, Any]) -> None:
        """Raise ToolValidationError if args are malformed.
        Called before constraint resolution so a typo fails fast."""
```

**`ToolContext`** is the runtime's gift to the tool — it carries the agent's instance_id, dna, role, genre, the active session_id, the per-tool constraints already resolved by the constitution, the audit-chain handle (read-only — tools don't write entries directly, the runtime does), the active model provider (for tools that wrap LLM calls — `summarize`, `classify`, etc.), and a logger.

**`ToolResult`** is `{"output": Any, "metadata": {...}, "tokens_used": int|None, "cost_usd": float|None, "side_effect_summary": str|None}`. The metadata is whatever the tool wants the audit trail to capture; the side_effect_summary is what shows up in the operator's approval prompt for next time.

Tools implementing the Protocol live in `src/forest_soul_forge/tools/`. The runtime's tool registry maps `(name, version) → Tool instance` and is loaded once at lifespan startup, the same way the trait engine and tool catalog are.

### Distribution format: `.fsf` plugin packages

For tools the operator builds locally or installs from external sources, we adopt a versioned plugin-package format. ZIP archive containing:

```
my_plugin.fsf
├── manifest.json           # { name, version, tools: [...], required_python }
├── tools/
│   ├── my_tool.py
│   └── helpers/
└── deps/                   # optional: bundled wheels for offline-friendly install
```

The `.fsf` extension is ours; the manifest schema is ours. **Inspired by** AGNT's `.agnt` format and VSCode `.vsix` (per the AGNT prior-art note), but neither is reused — license boundary respected, schema fields chosen for our use case (`required_constraint_compatibility`, `archetype_tags`, `mcp_passthrough`).

Plugins drop into `~/.fsf/plugins/` (or the project's `data/plugins/` for development). Loaded best-effort at lifespan; failed loads degrade gracefully (the plugin's tools simply aren't registered; the daemon stays up).

### Constraint resolution at call time

Per-tool constraints baked into the constitution at birth (ADR-0018 T2.5 + ADR-0021 T5) get re-resolved at call time. The runtime reads the constitution.yaml for the current agent, finds the `tools[]` block, locates the entry matching `(name, version)`, and applies:

- **`audit_every_call: true`** → emit a `tool_invoked` audit entry (always true today; reserved for future "log-volume reduction" knob).
- **`max_calls_per_session: N`** → check the session counter; refuse with a `tool_call_rejected` audit + 403 when N is hit. Counter scoped to the agent's session, persisted in the registry so a daemon restart doesn't reset budgets mid-session.
- **`requires_human_approval: true`** → enqueue rather than dispatch; emit a `tool_call_pending_approval` audit entry; return a deferred-result handle. The frontend shows a queue; the operator approves or denies; either path emits a follow-on audit entry (`tool_call_approved` / `tool_call_denied`) and either dispatches or refuses.

### Genre-level enforcement

ADR-0021 T5 already rejects an *agent's birth* when its kit exceeds the genre's `max_side_effects` ceiling. The runtime does the symmetric runtime check: when a tool gets dynamically added to a session (e.g. a Researcher agent loads a new tool mid-session for an experiment), the runtime applies the same kit-tier rule before dispatch.

For Companion's `provider_constraint: local_only`, the runtime hard-refuses any tool call that wraps an LLM and is being routed through the frontier provider. The constraint is structural (it's in the constitution_hash) but the runtime is what *enforces* it at the moment of invocation.

### Audit events

Five new audit event types:

| Event | Trigger | Payload |
|-------|---------|---------|
| `tool_invoked` | Every tool dispatch (fast or post-approval) | name, version, args_digest, result_digest, latency_ms, tokens, cost |
| `tool_call_rejected` | Refusal at constraint resolution time | name, version, reason, args_digest |
| `tool_call_pending_approval` | Approval-required call enqueued | name, version, args_digest, queue_id |
| `tool_call_approved` | Operator approved | queue_id, operator_id, approval_latency_ms |
| `tool_call_denied` | Operator denied | queue_id, operator_id, denial_reason |

`args_digest` and `result_digest` are SHA-256 hashes of canonicalized JSON — operators wanting the full call body inspect the registry's `tool_calls` table (a new schema bump). Hashes-only in the chain keeps the chain a reasonable size for high-traffic agents.

### Token + cost accounting

Per the AGNT prior-art note: every tool call records `tokens_used` and `cost_usd` when applicable. Tools that don't call LLMs return `None` for both; tools that wrap a model provider call (the `summarize`-style cluster) return real numbers from the provider response.

The registry's new `tool_calls` table (ADR-0006 schema bump from v2 → v3) accumulates per-agent, per-session, per-tool aggregates. The character sheet's `stats` section (ADR-0020) — currently `not_yet_measured` — fills in from these aggregates once the runtime is live.

### MCP integration

Forest already references MCP twice: as a possible adapter target (per the AGNT note) and as a transport surface. The runtime closes both:

- **As MCP client.** A new tool family — `mcp_call.v1` — accepts `{server_url, tool_name, args}` and proxies the request through the runtime's standard constraint+approval pipeline. Operators can install MCP servers (e.g. `mcp-grep`, `mcp-fs-read`) and have agents invoke their tools without writing Forest-specific Python wrappers.
- **As MCP server.** A new daemon endpoint `/mcp` exposes the runtime's tool registry as MCP tools. External LLM clients (Claude Desktop, an LSP-style assistant in an editor) can call Forest-governed tools and get the full constitution + audit treatment, even though the LLM itself is outside Forest. This is the path to making Forest a *governance layer* over arbitrary agent runtimes.

### Session model

A **session** is a unit of agent operation: the agent is "running" between session_start and session_end events. Sessions live in the registry; their lifecycle is bookended by audit events (`session_started`, `session_ended`). Per-session counters (max_calls_per_session) live on the session row.

ADR-0016 (session modes + self-spawning cipher) is closely related but orthogonal — that ADR is about ephemeral vs persistent-fork sessions and how they wake. This ADR is about what happens *during* a session. The two compose: ADR-0016 tells you when the session is alive; ADR-0019 tells you what runs while it is.

## Consequences

**Upside:**

- **The first phase where agents do work.** Every previous ADR was about configuring agents; this one is about running them. Tools become invokable surface, sessions become units of work, the audit chain becomes a real-time record of effort, costs become visible.
- **Genre + constraint policies become enforced**, not declarative. Companion's `local_only` is a policy that *blocks frontier-provider tool calls at runtime*, not a sticker on the agent.
- **Character sheet stats fill in.** `not_yet_measured` becomes real numbers as soon as the first agent calls its first tool.
- **Plugin ecosystem becomes possible.** Tools authored locally, packaged as `.fsf`, installable into the runtime. Operators stop being limited by the eight tools Forest ships with.
- **MCP both directions.** Forest consumes external MCP servers AND exposes its own tool registry as one. The same governance layer covers both.

**Downside:**

- **Significant scope.** This isn't a single-PR landing. Sub-tranches are large enough that each is roughly the size of ADR-0021's full implementation arc.
- **Approval-queue UX is genuinely hard.** "Operator presses approve, tool dispatches, result comes back" is the easy case. "Operator was AFK for 6 hours, returns to a backlog of 47 pending calls" is the realistic one. The approval UI is a real product surface, not a checkbox.
- **MCP-server passthrough is a security surface we haven't had before.** External LLMs invoking Forest tools means our authn/z story has to extend beyond X-FSF-Token. JWT-with-scope is the obvious next step but explicit out-of-scope here.
- **Schema bump (registry v2 → v3) is a one-way migration.** Forward migration is straightforward (new `tool_calls` table, alembic-style migration). Backward isn't, and we accept that.

**Out of scope for this ADR:**

- **Multi-tenant isolation.** Forest is local-first; per-user isolation isn't a v1 concern. Operators trust their own agents.
- **Hardware acceleration of tool execution.** GPU-bound tools (image classification, embedding generation) need a different scheduling story; revisit when a real such tool ships.
- **Distributed runtime.** Single-host for v1. Multi-host (e.g. agents spawning across nodes) is a Phase 7+ concern.
- **Autonomous self-improvement of tool code** (à la AGNT's SkillForge). Operators write tool code; the runtime executes it. Letting the runtime *modify* tool code is a separate (and much more delicate) ADR.
- **Long-running streaming tool output.** Tools return one `ToolResult`. Streaming output back to the LLM session is a Phase 7+ extension; for v1, tools that need streaming (live log tail, real-time correlation) compose against a buffered-snapshot interface.

## Open questions

1. **Where do approval prompts live?** Frontend modal vs. terminal CLI vs. push notification. **Lean:** start with frontend modal in the existing Agents tab; CLI subcommand follows once the modal proves out.

2. **Per-session counter persistence on crash.** If the daemon restarts mid-session, are accumulated counters preserved? **Lean yes** — counters are part of the session row in the registry, which is rebuildable from the audit chain. The chain is the source of truth, the counters are derived.

3. **What's the timeout-without-approval policy?** Tool call enqueued, operator never responds. **Lean:** configurable per-genre via a new `approval_timeout_seconds` field on `risk_profile` (default 24h for Actuator/Companion, 2h for everyone else). On timeout, emit `tool_call_denied` with reason `approval_timeout` and refuse the call.

4. **Sandbox / process isolation for tools.** Today all tools run in the daemon's process. A misbehaving plugin can crash the daemon. **Lean for v1:** in-process. **Defer:** separate-process tool sandbox via a worker model (similar to how docker-compose runs containers) — file as an amendment ADR once a real misbehaving-plugin incident drives it.

5. **`mcp_call.v1` schema.** The args shape — does the agent specify `server_url` per call (flexible) or does the operator pre-register MCP servers and the agent just specifies `server_alias` (safer)? **Lean:** server_alias. Operators register `("threat_intel", "mcp://localhost:9100/")` once; agents say "call threat_intel.lookup_ip" and don't get to pick arbitrary URLs.

## Implementation tranches

- **T1** — Tool Protocol + ToolContext + ToolResult dataclasses. Tool registry with lifespan load. One reference tool implementation: `timestamp_window.v1` (already in the catalog; trivial pure function — perfect first runtime exercise). **Landed.**
- **T2** — Fast-path dispatcher. Constraint resolution at call time. `tool_call_dispatched` + `tool_call_succeeded` + `tool_call_failed` + `tool_call_refused` audit events. Per-session counter wiring (registry schema v3 bump, `tool_call_counters` table). `POST /agents/{id}/tools/call` endpoint. **Landed.**
- **T3** — Approval queue. `tool_call_pending_approval` (T2-set) + `tool_call_approved` + `tool_call_rejected` audit events. `tool_call_pending_approvals` registry table (schema v5). `GET /agents/{id}/pending_calls`, `GET /pending_calls/{id}`, `POST .../approve`, `POST .../reject` endpoints. Frontend modal that shows pending calls and approve/reject buttons with reason capture. **Landed.**
- **T4** — Per-call accounting. tokens_used + cost_usd plumbing. `tool_calls` registry table (schema v4). Character sheet stats section populates from real numbers. **Landed.**
- **T5** — Plugin format + loader. `.fsf` schema. Daemon lifespan loads plugins from `data/plugins/`. Hot-reload deferred.
- **T6** — Genre-level runtime enforcement (pulled forward from original T9). `genre_floor_violated` refusal reason. Companion → reject non-local provider at dispatch. Observer → reject non-read_only at dispatch (catches tools added mid-session that the kit-tier guard didn't see at birth). Symmetric with ADR-0021 T5 build-time check. **Landed.**
- **T7** — Reference plugin: a small but real one. Candidate: `mcp_call.v1` (needs T8) or `web_search.v1` against an allowlist. (Was T6 originally.)
- **T8** — MCP-as-client. `mcp_call.v1` family. `~/.fsf/mcp_servers.yaml` operator config (server_alias → URL). (Was T7 originally.)
- **T9** — MCP-as-server. `/mcp` endpoint exposes the tool registry. Authn via X-FSF-Token initially; JWT-with-scope deferred. (Was T8 originally.)
- **T10** — Per-genre `approval_timeout_seconds`. New field on `risk_profile`. Sweeper on the daemon that fires `tool_call_denied` for timed-out queue entries.

T1–T4 + T6 was the "agents can run" milestone — landed. T5 + T7–T9 is "ecosystem." T10 is "the policies are enforced everywhere they should be."

**Renumbering note:** T6 (genre runtime enforcement) was pulled forward from the original T9 because it composes naturally with T2's dispatcher and ADR-0021 T5's build-time check. The original T6/T7/T8 (reference plugin, MCP-client, MCP-server) shifted up by one to T7/T8/T9 to accommodate. Other tranches' contents are unchanged.

## Cross-references

- ADR-0018 — tool catalog (the registry of *what is a tool*)
- ADR-0021 T5 — kit-tier enforcement at birth (this ADR adds the symmetric runtime check)
- ADR-0022 — memory subsystem (the runtime writes to it after each call)
- ADR-0023 — benchmark suite (uses this runtime to actually run benchmarks)
- ADR-0016 — session modes (when the session is alive vs paused)
- ADR-0006 — registry as derived index (a registry v3 schema bump lands with T2)
