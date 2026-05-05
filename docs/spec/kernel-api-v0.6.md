# Forest Kernel API — v0.6

**Status:** Draft (Burst 127, 2026-05-05). ADR-0044 Phase 2.

This document is the canonical, contract-grade specification of the
Forest agent-governance kernel's seven backward-compatibility
surfaces. It pins every stable interface with a version number, an
error envelope, and an ABI compatibility commitment.

[`KERNEL.md`](../../KERNEL.md) at the repo root is the elevator-
pitch index over these surfaces. This document is the deeper read:
field-by-field schemas, per-endpoint error envelopes, version-
compatibility rules, and the explicit *what we will and will not
change without a major bump* list.

If you are integrating against Forest from outside (writing a
plugin, building a different distribution, talking to the daemon
from another language), this is the reference. If you are
contributing to the kernel itself, this is the document that says
which changes need an ADR before they ship.

---

## 0. Scope and conventions

### 0.1 What this document covers

The seven kernel ABI surfaces enumerated in ADR-0044 Decision 3:

1. **Tool dispatch protocol** — the runtime contract for tool
   authors, MCP server authors, and anyone calling the dispatcher
   directly.
2. **Audit chain schema** — the on-disk JSONL format + hash chain
   invariants + the catalog of event-type payload shapes.
3. **Plugin manifest schema v1** — `plugin.yaml` validated by
   Pydantic; the trust boundary for operator-installed plugins.
4. **Constitution.yaml schema** — the agent identity binding;
   immutable per born agent.
5. **HTTP API contract** — the integration surface for any non-
   Python consumer (frontend, third-party services, CLI).
6. **CLI surface** — the `fsf` command-line interface; subcommands
   + exit codes + auth.
7. **Schema migrations** — the SQLite-registry persistence
   contract; strictly additive.

### 0.2 What this document does NOT cover

- Internal module layout under `src/forest_soul_forge/`. Free to
  refactor (e.g. ADR-0040 trust-surface decompositions reshaped
  `memory.py` → `memory/` and `writes.py` → `writes/` without
  touching the ABI).
- Helper functions, private dataclass shapes, and other internals
  that aren't serialized to disk, JSON, or the wire.
- Test helpers and fixtures.
- Performance characteristics. v1.0 will not commit to throughput
  or latency floors. (Future spec extension may.)
- Userspace (`apps/desktop/`, `frontend/`, `dist/`, repo-root
  `*.command` scripts). SoulUX is the reference distribution; a
  community could replace any of those wholesale.

### 0.3 Status of this spec

The kernel API has **not yet committed to v1.0 stability.** ADR-
0044 Decision 4 + Phase 6 of the roadmap require external integrator
validation before the freeze. **This spec describes the surfaces
*as they will be committed to* once that gate is cleared.** Until
then, breaking changes within these surfaces require an ADR plus a
deliberate ABI bump signal in the release notes — not casual
refactor.

When v1.0 lands:

- Each surface in this document moves from "intended commitment" to
  "frozen commitment under semver."
- The conformance test suite (ADR-0044 Phase 4) becomes the
  enforceable definition of what "compatible with kernel API v1"
  means.
- Breaking changes require a major version bump (v2 spec) and at
  least one full minor cycle of deprecation warnings.

### 0.4 Versioning policy

The kernel API spec is versioned independently from the kernel
implementation, the tool catalog, and the registry schema:

- **Kernel implementation version** — the Python package version
  (`pyproject.toml`'s `version`). Currently **v0.5.0**. Tracks the
  release tag.
- **Spec version** — this document's version. Currently **v0.6**.
  Tracks ADR-0044 Phase 2's drafts toward v1.0.
- **Tool catalog version** — `config/tool_catalog.yaml`'s top-level
  `version` field. Currently **"0.1"**. Independent of spec
  version because tools are catalog content, not kernel API.
- **Registry schema version** — integer in
  `src/forest_soul_forge/registry/schema.py`. Currently **15**.
  Strictly monotonically increasing.
- **Plugin manifest version** — `plugin.yaml`'s `schema_version:`.
  Currently **1**. Will increment if the manifest grammar changes
  in a non-additive way.
- **Audit chain version** — implicit. The hash-linked JSONL format
  and the `KNOWN_EVENT_TYPES` set. New event types are additive;
  schema changes to existing event types require an ADR.

A change is **non-breaking** if every existing valid input still
produces an indistinguishable result and every existing valid
output still parses. A change is **breaking** if it removes a
field, tightens a type, renames anything, or changes semantics
even when bytes are identical.

### 0.5 Error envelope discipline

Every error returned by a kernel surface uses one of these three
shapes:

**HTTP error envelope** (FastAPI conventions):
```json
{
  "detail": "<human-readable>",
  "code": "<machine-readable kebab-case>",
  "context": { ... optional structured detail ... }
}
```

**CLI error envelope** (stderr line + exit code):
```
fsf <subcommand>: <human-readable>
  hint: <optional next-step suggestion>
```
Exit codes per `src/forest_soul_forge/plugins/errors.py`:
- `0` — success
- `4` — user error / not found (NotFoundError, InvalidInputError)
- `5` — duplicate (DuplicatePluginError)
- `6` — validation (ManifestValidationError)
- `7` — generic plugin / server / network failure

**Dispatcher outcome dataclass** (Python in-process):
- `DispatchSucceeded(result, audit_seq, ...)` — happy path
- `DispatchRefused(reason, code, audit_seq, ...)` — pre-execution
  policy refusal (constraint, validation, genre floor, posture)
- `DispatchPendingApproval(approval_id, audit_seq, ...)` — call
  queued for operator
- `DispatchFailed(exception, traceback, audit_seq, ...)` — runtime
  exception during execute

Codes are kebab-case strings; common ones include
`tool-not-in-constitution`, `genre-side-effect-floor-exceeded`,
`posture-red`, `grant-trust-tier-too-low`,
`approval-required`, `unknown-tool`, `args-validation-failed`.

The same code may surface across HTTP, CLI, and dispatcher when a
single underlying refusal is the cause. Code lists are normative;
codes never get renamed without a major bump.

---

## 1. Tool dispatch protocol

**Canonical location:** `src/forest_soul_forge/tools/dispatcher.py`,
`src/forest_soul_forge/tools/governance_pipeline.py`,
`src/forest_soul_forge/tools/builtin/mcp_call.py`.

### 1.1 ToolDispatcher.dispatch() signature

```python
def dispatch(
    self,
    *,
    instance_id: str,
    agent_dna: str,
    role: str,
    genre: str,
    session_id: str,
    constitution_path: Path,
    tool_name: str,
    tool_version: str,
    args: dict[str, Any],
    provider: ProviderProtocol | None = None,
    task_caps: TaskCaps | None = None,
    idempotency_key: str | None = None,
) -> DispatchOutcome:
```

All eleven kwargs are part of the v1.0 freeze. Adding new
keyword-only parameters with defaults is non-breaking; reordering
or removing is breaking.

### 1.2 DispatchOutcome dataclass shape

`DispatchOutcome` is a sum type over four variants:

```python
@dataclass(frozen=True)
class DispatchSucceeded:
    result: dict[str, Any]
    audit_seq: int
    tokens_used: int | None
    cost_usd: float | None
    side_effect_summary: str | None
    result_digest: str  # sha256(canonical_json(result + metadata))

@dataclass(frozen=True)
class DispatchRefused:
    reason: str          # human-readable
    code: str            # kebab-case error code (see §0.5)
    audit_seq: int
    detail: dict[str, Any] | None  # structured context

@dataclass(frozen=True)
class DispatchPendingApproval:
    approval_id: int
    audit_seq: int
    expected_args_digest: str  # for resume-with-same-args check
    queued_at: datetime

@dataclass(frozen=True)
class DispatchFailed:
    exception_type: str
    exception_message: str
    traceback: str
    audit_seq: int
```

Field-level changes:
- **Adding a field with a default** — non-breaking.
- **Adding a new variant** — non-breaking (consumers should `match`
  with a default branch).
- **Removing a field, renaming a variant, changing a type** —
  breaking, requires major bump.

### 1.3 Governance pipeline step ordering

The dispatcher routes every call through this fixed step sequence
(see `governance_pipeline.py`):

| # | Step | Purpose | Skip on success? |
|---:|---|---|---|
| 1 | `HardwareQuarantineStep` | ADR-003X K6 hardware-binding mismatch → refuse | No |
| 2 | `TaskUsageCapStep` | Per-task token / cost ceiling check | No |
| 3 | `ToolLookupStep` | Resolve `(name, version)` → Tool instance | No |
| 4 | `ArgsValidationStep` | JSONSchema validation against tool's input_schema | No |
| 5 | `ConstraintResolutionStep` | Compute ResolvedConstraints from constitution + tool_policy.py rules | No |
| 6 | `PostureOverrideStep` | ADR-0045 yellow → escalate-on-side-effect; red → blanket refuse for non-`read_only` | No |
| 7 | `GenreFloorStep` | Tool's `side_effects` exceeds genre's `max_side_effects` → refuse | No |
| 8 | `InitiativeFloorStep` | ADR-0021-am: tool's required initiative > role/agent ceiling → refuse | No |
| 9 | `CallCounterStep` | `max_calls_per_session` reached → refuse | No |
| 10 | `McpPerToolApprovalStep` | ADR-0043 per-tool approval-mirroring — plugin-tool requires approval but the plugin grant didn't elevate this tool → refuse | No |
| 11 | `ApprovalGateStep` | `requires_human_approval=True` → emit `tool_call_pending_approval`, return `DispatchPendingApproval` | Yes (terminal on pending) |
| 12 | `PostureGateStep` | ADR-0045 final yellow-tier check on side-effecting tools (post-approval); red-dominates per-grant precedence | No |

Each step's contract is its input (the immutable `DispatchContext`)
and its output (`StepResult`: `Continue | Refused(code, detail) |
PendingApproval(approval_id)`). The pipeline halts on the first non-
`Continue` result.

**Adding a step is non-breaking** — older callers pass through the
new step the same way (every step must default to `Continue` for
unfamiliar input).

**Reordering steps is breaking.** A step that fires earlier or
later changes the precedence of refusals; an integrator that
expected `genre-side-effect-floor-exceeded` to fire before
`posture-red` would see different bytes for the same call. The
ordering above is the v0.6 freeze.

**Removing a step is breaking** — an integrator may rely on a
specific refusal code firing.

### 1.4 mcp_call.v1 contract

The dispatcher tool that routes to operator-registered MCP servers
(per ADR-0043). Its three-part contract:

**Input schema** (subset of JSONSchema; pinned at v1):
```yaml
type: object
required: [server, tool, args]
properties:
  server:        # MCP server name, must be in mcp_registry
    type: string
  tool:          # Tool name within that server
    type: string
  args:          # Whatever the tool expects (opaque to dispatcher)
    type: object
  timeout_ms:    # Optional; default 30000; capped at 120000
    type: integer
```

**Output schema** (any of these shapes):
- `{ ok: true, result: <opaque>, server: <name>, tool: <name>, latency_ms: <int> }`
- `{ ok: false, error: <string>, code: <kebab-case>, server: <name>, tool: <name> }`

**MCP registry shape** (in `ctx.constraints["mcp_registry"]`):
```python
{
    "server-name-1": {
        "type": "mcp_server",
        "transport": "stdio" | "http",
        "command": [str, ...],         # for stdio
        "url": str,                    # for http
        "tools": {"tool-1": {...}, ...},
        "side_effects": "read_only" | "network" | "filesystem" | "external",
        "trust_tier": int,             # 0-5 per ADR-0045
        "manifest_path": str,          # back-pointer to plugin.yaml
    },
    ...
}
```

The registry shape is part of the v1.0 freeze. Adding sibling keys
is fine; removing or renaming any of the listed keys is breaking.

---

## 2. Audit chain schema

**Canonical location:** `src/forest_soul_forge/core/audit_chain.py`,
`examples/audit_chain.jsonl` (default chain path per
`daemon/config.py`'s `audit_chain_path`).

### 2.1 JSONL line shape

Every chain entry is a single line of JSON with these exactly-seven
top-level fields, ordered by schema convention:

```json
{
  "seq": <int, monotonically increasing from 1>,
  "timestamp": "<ISO 8601 UTC, e.g. 2026-05-05T18:30:13Z>",
  "agent_dna": "<12-char short DNA, or null for non-agent events>",
  "event_type": "<known event-type string>",
  "event_data": { ... event-type-specific payload ... },
  "prev_hash": "<sha256 hex of previous entry, or all-zeros for seq=1>",
  "entry_hash": "<sha256 hex of this entry, see §2.2>"
}
```

Reordering fields in the serialized JSONL is **breaking** — the
canonical-JSON form (sorted keys, no whitespace) is what
`entry_hash` is computed over. Adding a new top-level field is
breaking for the same reason.

### 2.2 Hash discipline

```
canonical_event = canonical_json({
    "seq": seq,
    "timestamp": timestamp,
    "agent_dna": agent_dna,
    "event_type": event_type,
    "event_data": event_data,
    "prev_hash": prev_hash,
})
entry_hash = sha256(canonical_event).hexdigest()
```

`canonical_json` is sorted-keys, separators `(',', ':')`,
ensure_ascii=True. The `entry_hash` field itself is excluded from
the hash input.

For `seq=1`, `prev_hash` is `"0" * 64`.

**Hash chain integrity** is verifiable independently via
`scripts/verify_audit_chain.py`. Tampering breaks the chain at the
first mutated entry.

### 2.3 Append-only contract

- Entries are written via `AuditChain.append(event_type, agent_dna,
  event_data)` only. Direct file writes are forbidden.
- `append` acquires `app.state.write_lock` (a `threading.RLock`),
  computes hashes, fsyncs the JSONL line, and only then returns
  the new `seq`.
- Existing entries are never rewritten or deleted. The chain is the
  canonical record; the SQLite registry is a derived index that can
  always be rebuilt from the chain.

Operator workflows that look like deletion (archiving an agent,
forgetting a memory, expiring a consent) emit *new* events that
record the state change, not modifications to old entries.

### 2.4 Event type catalog

The canonical set lives in `KNOWN_EVENT_TYPES` in `audit_chain.py`.
Current count: **70 event types** organized by subsystem.

Adding a new event type to `KNOWN_EVENT_TYPES` is non-breaking;
existing consumers that don't recognize it should ignore it (the
chain tolerates unknowns with a flag).

Adding fields to an existing event's `event_data` payload is non-
breaking. Removing or renaming a field within an existing event
type is **breaking**.

The full catalog (organized by subsystem) — each entry's payload
shape is canonically defined by the `_emit_*` helper that creates it
in the codebase:

**Lifecycle** (8): `daemon_started`, `daemon_stopped`,
`config_loaded`, `migration_applied`, `agent_born`, `agent_spawned`,
`agent_archived`, `agent_unarchived`.

**Tool dispatch** (7): `tool_call_dispatched`, `tool_call_succeeded`,
`tool_call_refused`, `tool_call_failed`,
`tool_call_pending_approval`, `tool_call_approved`,
`tool_call_rejected`.

**Skill runtime** (7): `skill_invoked`, `skill_step_complete`,
`skill_step_failed`, `skill_succeeded`, `skill_failed`,
`skill_skipped`, `skill_step_skipped`.

**Memory** (8): `memory_appended`, `memory_disclosed`,
`memory_consent_granted`, `memory_consent_revoked`,
`memory_promoted`, `memory_consolidated`, `memory_forgotten`,
`memory_flagged_contradiction`.

**Cross-agent** (3): `agent_delegated`, `agent_handoff`,
`swarm_escalation`.

**Conversation runtime (ADR-003Y)** (7):
`conversation_started`, `conversation_archived`,
`conversation_participant_joined`, `conversation_participant_left`,
`conversation_turn`, `conversation_bridge_emitted`,
`conversation_summary_generated`.

**Verification (ADR-0036)** (1): `verifier_scan_completed`.

**Scheduler (ADR-0041)** (7): `task_registered`, `task_triggered`,
`task_succeeded`, `task_failed`, `task_disabled`, `task_enabled`,
`task_reset`.

**Plugin lifecycle (ADR-0043)** (5): `plugin_installed`,
`plugin_enabled`, `plugin_disabled`, `plugin_verified`,
`plugin_uninstalled`.

**Posture + grants (ADR-0045 / ADR-0043 follow-ups)** (3):
`agent_plugin_granted`, `agent_plugin_revoked`,
`agent_posture_changed`.

**Open-web (ADR-003X)** (3): `mcp_call_dispatched`,
`browser_action_dispatched`, `web_fetch_dispatched`.

**Hardware-binding (ADR-003X K6)** (2): `hardware_binding_set`,
`hardware_binding_quarantined`.

**Triune-bonded (ADR-0034)** (1): `triune_bonded`.

**Misc** (8): `agent_renamed`, `consent_audited`,
`provider_routing_decision`, `dispatcher_idempotency_replay`,
`scheduler_run_completed`, `dispatch_pre_check_failed`,
`audit_export_emitted`, `chain_verified`.

**Deferred** (1): `plugin_secret_set` — placeholder per ADR-0043
follow-up #4, not yet emitted; reserved for the secrets-storage
landing.

### 2.5 Audit chain versioning

The chain itself does not carry a version field — the `seq` series
is monotonic and the format hasn't changed since v0.1. Any future
schema bump (e.g. adding a top-level field, changing canonical-JSON
rules) requires an ADR + a one-time chain-rewrite migration plus
hash-rechain. That's a major-bump event, not a minor.

---

## 3. Plugin manifest schema v1

**Canonical location:**
`src/forest_soul_forge/plugins/manifest.py` (Pydantic model),
`examples/plugins/README.md` (operator guide),
`examples/plugins/{forest-echo,brave-search,filesystem-reference}/`
(canonical examples).

### 3.1 plugin.yaml top-level shape

```yaml
schema_version: 1                        # int, must equal 1
name: example-mcp-server                 # str, slug; matches dirname
version: "1.0.0"                         # semver str
type: mcp_server                         # enum: mcp_server (only type at v1)
description: "..."                       # str, free text
author: "Author Name <email@host>"       # str
license: "Apache-2.0"                    # SPDX identifier
homepage: "https://..."                  # optional URL
side_effects: read_only                  # enum: read_only|network|filesystem|external
capabilities: [tools]                    # list of capability strings
trust_tier: 1                            # int 0-5 per ADR-0045 default tier
required_secrets: []                     # list of secret_name strings
entry_point:                             # nested object (see §3.3)
  type: stdio
  command: ["python", "-m", "..."]
  sha256: "<64-char hex>"
requires_human_approval:                 # optional per-tool override map
  send_message: true
  read_history: false
allowed_paths:                           # optional, only for filesystem
  - "${HOME}/.allowlisted/**"
allowed_hosts:                           # optional, only for network
  - "api.example.com"
```

### 3.2 Field-level rules

- `schema_version` must equal 1. Rejected if 0 or 2.
- `name` matches `^[a-z][a-z0-9-]{1,62}[a-z0-9]$` (kebab-case slug,
  3-64 chars). The dirname under `~/.forest/plugins/` must equal
  `name`.
- `version` must be valid semver per
  https://semver.org/spec/v2.0.0.html.
- `type` is `mcp_server` at v1. Future types (e.g. `tool_pack`,
  `agent_pack`) require `schema_version: 2`.
- `side_effects` is the worst-case posture across all tools the
  plugin exposes. The dispatcher uses this for the genre kit-tier
  check; tighter per-tool postures are a runtime concern.
- `trust_tier` is the *default* tier when an operator grants this
  plugin to an agent. Operators can override via `--trust-tier`
  in `fsf plugin grant`; the per-grant value takes precedence per
  ADR-0045 T3+T4.
- `required_secrets` lists secret names; operators must
  explicitly permit each via `allowed_secret_names` in the
  agent's constitution. Plugin can never read a secret not in
  this list.

### 3.3 entry_point sub-shape

```yaml
entry_point:
  type: stdio                            # enum: stdio | http
  # for stdio:
  command: ["python", "-m", "module"]    # argv list
  cwd: "."                               # optional, relative to plugin dir
  env:                                   # optional env-var map
    FOO: "bar"
  # for http:
  url: "http://127.0.0.1:8080"           # required
  health_path: "/health"                 # optional, default "/healthz"
  # both:
  sha256: "<64-char hex>"                # of the entry_point file or descriptor
```

The `sha256` field is the trust boundary. The loader recomputes
this at install time and at every load and refuses to start a
plugin whose sha256 has drifted. **Loosening this is a breaking
change** — the v1 spec mandates sha256 pinning.

### 3.4 Validation error envelope

`ManifestValidationError` (the Python exception) carries:
- `manifest_path: Path`
- `field: str` — the dotted path (e.g. `entry_point.sha256`)
- `reason: str` — human-readable
- `code: str` — kebab-case (e.g. `sha256-mismatch`,
  `unknown-side-effects`, `invalid-semver`, `name-collision`)

Surfaces as HTTP 422 + the standard envelope (§0.5) and CLI exit
6 + stderr line.

---

## 4. Constitution.yaml schema

**Canonical location:**
`src/forest_soul_forge/core/constitution.py` (renderer + hasher),
`config/constitution_templates.yaml` (per-role base + modifier
templates),
`soul_generated/<agent-name>/<dna>/constitution.yaml` (per-agent
materialized output).

### 4.1 Top-level fields

```yaml
schema_version: 2                        # int; v2 since ADR-0042
constitution_hash: "<sha256 hex>"        # immutable per agent
agent:
  dna: "<12-char short DNA>"
  full_dna: "<64-char SHA-256>"
  role: "<role from trait_tree.yaml>"
  genre: "<genre from genres.yaml>"
  agent_name: "<operator-chosen name>"
  initiative_level: 1                    # ADR-0021-am 1-5
  born_at: "<ISO 8601 UTC>"
policies:                                # list of policy dicts
  - id: "<kebab-case policy id>"
    rule: "<short imperative>"
    triggers: ["<event_type>", ...]
    rationale: "..."
risk_thresholds:
  auto_halt_risk: 0.85
  escalate_risk: 0.70
  min_confidence_to_act: 0.65
out_of_scope: ["<topic>", ...]
operator_duties: ["<duty>", ...]
drift_monitoring:
  per_turn:
    - check: "<check name>"
      threshold: 0.5
tools:                                   # list of per-tool entries
  - name: log_scan
    version: "1"
    side_effects: read_only
    constraints:
      requires_human_approval: false
      max_calls_per_session: 100
    applied_rules: ["external_always_human_approval", ...]
allowed_mcp_servers: []                  # list of server names
allowed_paths: []                        # list of glob strings
allowed_secret_names: []                 # list of secret names
hardware_binding:                        # optional, ADR-003X K6
  enabled: false
  signature: null
```

### 4.2 Constitution hash invariant

The `constitution_hash` is computed at birth time over a
canonical-JSON serialization of the constitution body (excluding
`constitution_hash` itself, `born_at`, and `agent.full_dna`). The
result is bound to the agent's identity for life:

- A born agent's hash is in `agents.constitution_hash`.
- Re-rendering with the same trait profile produces the same hash.
- Verification at agent reload re-renders and compares; mismatch
  refuses to load.

**Mutable state** (status, posture, flagged state) lives on the
`agents` SQL row, NOT in the constitution. Posture changes do not
re-hash.

### 4.3 Schema versioning

- v1 → v2 bump landed in ADR-0042. v2 added `agent.initiative_level`,
  `hardware_binding`, and tightened `tools[*].applied_rules`
  semantics.
- A v2 → v3 bump would require an ADR + a one-time re-render
  migration for all existing agents (they keep their v2 hash, but
  newly-born agents get the v3 shape).

Adding optional top-level fields is non-breaking *for existing
agents* (their constitution_hash stays valid) but it *is* a spec
change that requires an ADR. The hash invariant means any field
that's new becomes load-bearing the moment a new agent is born
under it.

---

## 5. HTTP API contract

**Canonical location:**
`src/forest_soul_forge/daemon/routers/` (endpoint implementations),
`src/forest_soul_forge/daemon/schemas/` (Pydantic request/response
models).

### 5.1 Auth model

- `X-FSF-Token` HTTP header. When `FSF_API_TOKEN` env var is set on
  the daemon, all *write* endpoints require this token. Read
  endpoints are ungated by default.
- `FSF_REQUIRE_WRITES_ENABLED=true` — if set, write endpoints also
  require this gate to be flipped (defense in depth for kiosked
  installs).
- CORS allowlist via `FSF_CORS_ALLOW_ORIGINS` (comma-separated).
  Default is `127.0.0.1:5173` for the reference frontend.

### 5.2 Idempotency

All write endpoints accept `X-Idempotency-Key`. Repeated calls with
the same key + same body return the prior response without re-
executing. The key space is per-endpoint, scoped by daemon lifetime
(no cross-restart memo).

### 5.3 Endpoint catalog (read)

| Endpoint | Purpose | Stable |
|---|---|---|
| `GET /healthz` | Health + startup_diagnostics | ✓ |
| `GET /audit/tail?n=N` | Last N entries from canonical chain | ✓ |
| `GET /audit/agent/{id}` | Indexed by agent | ✓ |
| `GET /audit/by-dna/{dna}` | Indexed by DNA | ✓ |
| `GET /audit/stream` | SSE live stream (ADR-003X K3) | ✓ |
| `GET /agents` | List born agents | ✓ |
| `GET /agents/{id}` | Per-agent detail | ✓ |
| `GET /agents/{id}/character-sheet` | Roll-up view | ✓ |
| `GET /agents/{id}/posture` | ADR-0045 current posture | ✓ |
| `GET /agents/{id}/plugin-grants` | ADR-0043 grants | ✓ |
| `GET /agents/{id}/memory/{scope}` | Memory query | ✓ |
| `GET /agents/{id}/hardware/binding` | ADR-003X K6 status | ✓ |
| `GET /tools` | Tool catalog | ✓ |
| `GET /tools/{name}/{version}` | Per-tool detail | ✓ |
| `GET /skills` | Skill manifests | ✓ |
| `GET /skills/{name}/{version}` | Per-skill detail | ✓ |
| `GET /traits` | Trait engine state | ✓ |
| `GET /genres` | Genre engine state | ✓ |
| `GET /plugins` | Installed plugins | ✓ |
| `GET /plugins/{name}` | Per-plugin detail | ✓ |
| `GET /scheduler/tasks` | ADR-0041 task list | ✓ |
| `GET /scheduler/tasks/{id}` | Per-task detail | ✓ |
| `GET /pending-calls` | Approval queue | ✓ |
| `GET /memory-consents` | Consent grants | ✓ |
| `GET /conversations` | ADR-003Y rooms | ✓ |
| `GET /conversations/{id}` | Per-room detail | ✓ |

### 5.4 Endpoint catalog (write — gated)

| Endpoint | Purpose | Stable |
|---|---|---|
| `POST /birth` | New agent | ✓ |
| `POST /spawn` | Lineage child | ✓ |
| `POST /agents/{id}/regenerate-voice` | Rebuild soul.md voice | ✓ |
| `POST /agents/{id}/archive` | Soft-archive | ✓ |
| `POST /agents/{id}/unarchive` | Restore | ✓ |
| `POST /agents/{id}/tools/call` | Tool dispatch via HTTP | ✓ |
| `POST /agents/{id}/skills/run` | Skill manifest run | ✓ |
| `POST /agents/{id}/posture` | ADR-0045 set posture | ✓ |
| `POST /agents/{id}/plugin-grants` | ADR-0043 grant | ✓ |
| `DELETE /agents/{id}/plugin-grants/{plugin_name}` | Revoke | ✓ |
| `POST /agents/{id}/memory/consents` | Grant consent | ✓ |
| `DELETE /agents/{id}/memory/consents/{event_id}` | Revoke | ✓ |
| `POST /agents/{id}/hardware/unbind` | ADR-003X K6 unbind | ✓ |
| `POST /pending-calls/{id}/approve` | Approve queued call | ✓ |
| `POST /pending-calls/{id}/reject` | Reject queued call | ✓ |
| `POST /tools/reload` | Re-scan tool registry | ✓ |
| `POST /skills/reload` | Re-scan skill manifests | ✓ |
| `POST /plugins/reload` | Re-scan plugin root | ✓ |
| `POST /plugins/{name}/enable` | Enable plugin | ✓ |
| `POST /plugins/{name}/disable` | Disable plugin | ✓ |
| `POST /plugins/{name}/verify` | Re-verify sha256 | ✓ |
| `POST /scheduler/tasks/{id}/trigger` | Manual trigger | ✓ |
| `POST /scheduler/tasks/{id}/{enable,disable,reset}` | Control | ✓ |
| `POST /conversations` | Open room | ✓ |
| `POST /conversations/{id}/turns` | Post turn | ✓ |
| `POST /conversations/{id}/archive` | Archive room | ✓ |

Adding endpoints is non-breaking. Adding optional fields to
request/response bodies is non-breaking. Removing endpoints,
removing required fields from requests, or removing fields from
responses is breaking.

### 5.5 OpenAPI

The daemon serves OpenAPI 3.0 at `/openapi.json` and Swagger UI at
`/docs`. The auto-generated schema is normative for request/
response shapes — contributors must regenerate the locked
`docs/spec/openapi-v0.6.json` (future P2 sub-task) at every kernel
change.

### 5.6 HTTP error envelope

Per §0.5. FastAPI's default 422 (validation) envelope is augmented
with a `code` field via the `validation_error_handler` in
`daemon/error_handlers.py`. Common codes:

| HTTP status | Common codes |
|---:|---|
| 400 | `invalid-input`, `args-validation-failed`, `idempotency-conflict` |
| 401 | `auth-required`, `invalid-token` |
| 403 | `writes-disabled`, `tool-not-in-constitution`, `posture-red`, `genre-side-effect-floor-exceeded`, `grant-trust-tier-too-low` |
| 404 | `agent-not-found`, `tool-not-found`, `plugin-not-found`, `task-not-found` |
| 409 | `agent-already-archived`, `plugin-already-installed`, `duplicate-grant` |
| 422 | `manifest-validation-error`, `constitution-validation-error` |
| 423 | `migration-in-progress`, `chain-verifying` |
| 503 | `daemon-shutting-down`, `provider-unavailable` |

---

## 6. CLI surface

**Canonical location:** `src/forest_soul_forge/cli/main.py` and
per-command modules under `src/forest_soul_forge/cli/`.

### 6.1 Subcommand tree

```
fsf
├── forge
│   ├── tool <description>            # 6-stage tool generation
│   └── skill <description>           # skill manifest generation
├── install
│   ├── tool <staged-path>            # promote staged tool to data/plugins/
│   └── skill <manifest-path>         # copy skill to data/forge/skills/installed/
├── triune
│   └── bond <agent-id-1> <agent-id-2> <agent-id-3>
├── chronicle
│   ├── per-agent <agent-id>          # HTML+MD export
│   ├── per-bond <bond-id>            # triune chronicle
│   └── full-chain                    # full audit chain export
├── plugin
│   ├── list
│   ├── info <plugin-name>
│   ├── install <plugin-archive>
│   ├── uninstall <plugin-name>
│   ├── enable <plugin-name>
│   ├── disable <plugin-name>
│   ├── verify <plugin-name>
│   ├── grant <agent-id> <plugin-name> [--trust-tier N] [--scope ...]
│   ├── revoke <agent-id> <plugin-name>
│   └── grants <agent-id>
└── agent
    └── posture
        ├── get <agent-id>
        └── set <agent-id> <green|yellow|red> [--reason "..."]
```

Each leaf subcommand has a `--help` page that documents its
flags, exit codes, and example invocations. The `--help` text is
considered documentation, not API.

### 6.2 Common flags

- `--daemon-url URL` — override the daemon URL (default
  `http://127.0.0.1:7423`).
- `--api-token TOKEN` — passes `X-FSF-Token`; falls back to
  `$FSF_API_TOKEN` env var.
- `--json` — emit machine-readable output instead of human-friendly.
- `--quiet` / `--verbose` — control log verbosity.

### 6.3 Exit codes

Per `src/forest_soul_forge/plugins/errors.py`:

| Code | Meaning | Source exception |
|---:|---|---|
| 0 | Success | — |
| 1 | Reserved (Click default for unhandled errors) | — |
| 2 | Reserved (Click usage error) | — |
| 4 | User error / not found | `NotFoundError`, `InvalidInputError` |
| 5 | Duplicate | `DuplicatePluginError` |
| 6 | Validation | `ManifestValidationError` |
| 7 | Generic plugin/server/network failure | `PluginError`, `ServerError`, `NetworkError` |

Codes 0, 4, 5, 6, 7 are part of the v1.0 freeze. Code 1 and 2 are
Click's defaults and reserved — kernel code never raises them
deliberately.

### 6.4 Auth fallback chain

1. `--api-token <value>` flag.
2. `$FSF_API_TOKEN` env var.
3. Anonymous (works for read endpoints when daemon doesn't require
   token; fails on write endpoints with exit 7 + "auth-required").

---

## 7. Schema migrations

**Canonical location:**
`src/forest_soul_forge/registry/schema.py` — `MIGRATIONS` dict and
`DDL_STATEMENTS` tuple.

### 7.1 Strict-additive forward migration policy

Allowed in a forward migration:
- `CREATE TABLE`
- `ALTER TABLE … ADD COLUMN <name> <type>` (column must have a
  default, or the existing rows must be backfillable)
- `CREATE INDEX`
- Constraint additions that all existing rows already satisfy

Forbidden (requires a major bump + an artifact-rebuild migration):
- `DROP TABLE`
- `ALTER TABLE … DROP COLUMN`
- Constraint tightening that some existing rows fail
- Type changes that lose information
- Renames

The escape hatch for a destructive change is
`rebuild_from_artifacts`: replay the audit chain from `seq=1` into
a fresh schema. The audit chain is the canonical record; the
registry is the rebuildable index.

### 7.2 Current schema version

**v15.** Each migration is documented inline in `MIGRATIONS[N]`:

| Version | Landed | Purpose |
|---:|---|---|
| v1 | v0.1.0 | Initial schema — `agents`, `tool_calls`, `audit_chain_index` |
| v2 | v0.1.1 | Added `tool_call_pending_approvals`, `tool_call_counters` |
| v3 | v0.1.1 | Added `agent_ancestry` closure table |
| v4 | v0.1.2 | Added `memory_entries`, `memory_disclosures`, `memory_consents` |
| v5 | v0.1.2 | Added `consent_status` view and per-event consent tracking |
| v6 | v0.1.2 | Added `epistemic_*` columns to `memory_entries` |
| v7 | v0.1.2 | Memory v0.2 — privacy scopes refinement |
| v8 | v0.2.0 | Added `agent_secrets` (ADR-003X C1) |
| v9 | v0.3.0 | Added `memory_verifications` (ADR-0036 T1) |
| v10 | v0.3.0 | Added `conversations` table family (ADR-003Y) |
| v11 | v0.3.0 | Epistemic memory column refinements |
| v12 | v0.3.0 | `flagged_state` column on `memory_contradictions` (ADR-0036 T6) |
| v13 | v0.4.0 | `scheduled_task_state` (ADR-0041 T5) |
| v14 | v0.5.0 | `agent_plugin_grants` table + `idx_plugin_grants_active` (ADR-0043 follow-up #2 / Burst 113a) |
| v15 | v0.5.0 | `agents.posture` column + `idx_agents_posture` (ADR-0045 T1 / Burst 114) |

### 7.3 Migration file format

Each `MIGRATIONS[N]` entry is a function `migrate_to_vN(conn:
sqlite3.Connection) -> None` that:
- Runs idempotent DDL via `conn.execute(...)`.
- Updates `schema_version` row in `schema_meta` table.
- Does not touch the audit chain.
- Does not depend on the previous version's logic existing in code
  (each migration is self-contained — older migrations can be
  refactored or compressed at major bumps).

The bootstrap sequence on a fresh DB runs all migrations in order
from v0 → vN. On an existing DB, only the missing migrations run.

### 7.4 Audit chain interaction

`audit_chain.jsonl` IS the source of truth. The registry tables
are the index. A v15 → v16 forward migration that loses derivable
data is fine — `rebuild_from_artifacts` replays the chain. A
migration that loses *canonical* artifact state (the chain itself,
or `soul_generated/<dna>/*`) is a bug.

---

## 8. ABI compatibility commitments

### 8.1 Pre-v1.0 (now)

Breaking changes within the seven surfaces require:
1. An ADR explaining the change and its rationale.
2. A deliberate ABI bump signal in the release notes (a one-line
   "Breaking ABI" entry in CHANGELOG.md).
3. Updates to this spec document.
4. Updates to KERNEL.md if the surface name changes.

Non-breaking additions (new endpoints, new event types, new fields
with defaults, new exit codes, new policies) require:
1. An ADR if the change is design-relevant.
2. CHANGELOG.md entry under the relevant `### Added` section.
3. Spec doc updates so the catalog stays current.

### 8.2 v1.0 (future)

The v1.0 freeze trigger is **external integrator validation**
(ADR-0044 Decision 4 + Phase 6). At least one community-built
distribution or substantive third-party integration must ship
against the kernel and report compatibility.

After v1.0:
- Breaking changes within any of the seven surfaces require a major
  version bump (kernel implementation v2.0.0).
- A minor cycle (at minimum) of deprecation warnings precedes any
  breaking change.
- The conformance test suite (Phase 4) becomes the enforceable
  definition of "compatible with kernel API v1."
- The spec moves to git-tagged versions (`docs/spec/kernel-api-v1
  .md`); v0.6 is preserved as the pre-stability draft.

### 8.3 Post-v1.0 (theoretical v2)

A v2 spec would coexist with v1; integrators target a specific
version. The kernel implementation could ship v1 and v2 surfaces
simultaneously during a transition window (similar to Linux
kernel ABI deprecation cycles). This is theoretical — Forest is
not at v1 yet, so v2 planning is premature.

---

## 9. Conformance

This spec is the *what*. The conformance test suite (ADR-0044
Phase 4, **shipped Burst 130 at `tests/conformance/`**) is the
*enforceable check* that any build of the kernel actually honors
what this document promises.

The conformance suite is HTTP-only (no internal Python imports);
it can run against any Forest-kernel build, including non-Python
implementations, PyInstaller binaries, and second distributions.
Install via:

```bash
pip install "forest-soul-forge[conformance]"
pytest tests/conformance/ -v
```

By default it tests `http://127.0.0.1:7423`; override via
`FSF_DAEMON_URL` env var. See `tests/conformance/README.md` for
full usage.

The unit suite (`tests/unit/`, 2,386 tests at v0.5.0) remains the
internal regression gate; conformance is the external one.

Phase 4 produces:
- A conformance test runner that an external build can install
  and run against its own kernel implementation.
- A pass/fail report keyed to this spec's section numbers.
- A version-compatibility matrix (e.g. "build X passes spec v0.6
  §1 + §2 + §3, fails §4.2").

---

## 10. Open questions for v1.0 freeze

These are surface-level questions that need resolution before the
v1.0 freeze can land. None of them are blocking the v0.6 spec
draft — they're things to think through during the runway.

1. **HTTP API versioning.** Today the daemon serves a single
   unversioned path tree (`/agents`, `/audit`, etc.). At v1.0
   should the spec freeze the unversioned path, or move to
   `/v1/agents` etc. so future v2 surfaces can coexist? Kubernetes
   uses versioned paths; Postgres doesn't. Decision is not made.

2. **CLI subcommand stability vs. extension.** `fsf agent` only
   has `posture` at v0.6. Future subcommands (`fsf agent rename`,
   `fsf agent forget`) are non-breaking additions but reshape the
   `--help` tree. Should `--help` text be considered API?

3. **MCP plugin types beyond `mcp_server`.** The manifest grammar
   reserves `type:` for future extension (`tool_pack`,
   `agent_pack`). Pinning v1 to `mcp_server` only forces a v2
   manifest bump for new types — fine, but the manifest schema
   could instead pre-allow `type` extensions with a forward-compat
   union. Decision is not made.

4. **Event-type catalog ossification.** Adding a new event type is
   non-breaking; should *removing* one ever be allowed? Probably
   no — even unused event types in the catalog are part of the
   tamper-evidence story for old chain entries. v1.0 should freeze
   the event-type set as a strict superset over time.

5. **Schema version vs. spec version coupling.** The registry
   schema (currently v15) and the API spec (v0.6) version
   independently. Should v1.0 of the spec require a corresponding
   schema version (e.g. spec v1.0 ⇒ schema ≥ v15)? Probably yes,
   to make conformance testing tractable.

6. **Constitution schema_version v3 or stay at v2.** v2 has been
   stable since ADR-0042. Burst 124's role-inventory expansion
   added 24 new role_base entries but didn't change the schema
   shape. v1.0 spec freeze is a natural moment to assess whether
   any v3-grade refinements (e.g. tighter `applied_rules`
   semantics, hardware_binding-by-default) should land first.

These will be revisited in the Phase 6 external-integrator dry-run
before the v1.0 freeze commitment lands.

---

## References

- [`KERNEL.md`](../../KERNEL.md) — root-level kernel/userspace ABI
  summary (Burst 119, ADR-0044 P1.2)
- [`docs/architecture/kernel-userspace-boundary.md`](../architecture/kernel-userspace-boundary.md) — directory-level boundary map
  (Burst 118, ADR-0044 P1.1)
- ADR-0044 — Kernel Positioning + SoulUX Flagship Branding
- ADR-0001 — Audit chain protocol (the load-bearing primitive)
- ADR-0007 — Constitution as immutable hash
- ADR-0019 — Tool dispatch + governance pipeline
- ADR-0040 — Trust-surface decomposition rule
- ADR-0043 — MCP-first plugin protocol
- ADR-0045 — Agent posture / trust-light system
- ADR-0046 — License posture + governance
