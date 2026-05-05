# Forest — the kernel

Forest Soul Forge is positioned as the **agent governance kernel**
in the agent runtime ecosystem (ADR-0044, accepted 2026-05-05).
The flagship distribution that ships Forest with a polished
operator experience is **SoulUX** — Tauri shell + reference
frontend, lives under `apps/desktop/` and `frontend/`. Same
relationship as Linux:Ubuntu or Postgres:Supabase. Other
distributions can build on the same Forest kernel.

This file is the canonical entry point for an external integrator
or contributor who needs to understand:

1. **What the kernel commits to at v1.0** — the seven backward-
   compatibility surfaces.
2. **What the kernel does NOT commit to** — internal layout,
   helper functions, performance characteristics.
3. **Where to find the canonical definition of each surface.**

If you're building a *different* distribution on top of Forest
(headless, mobile, terminal-only, etc.), this is the surface
you target. If you're building an *integration* (a tool, a
plugin, a custom workflow), you target the same surface from
the outside.

## Status

**v0.5.0** ships the substantive kernel work (governance pipeline,
posture system, plugin protocol, grants, per-tool + per-grant
trust dials, audit chain, memory model, conversation runtime).
**v1.0 is not yet committed** — the API stability commitment
lands when external integrator validation arrives (ADR-0044
Decision 4 and Phase 6 of the roadmap).

The seven surfaces below are *what* will be committed to. The
*when* is a future tag. Until then, breaking changes within these
surfaces require an ADR + a deliberate ABI bump signal — not
casual refactor.

---

## The seven kernel ABI surfaces

### 1. Tool dispatch protocol

The contract a tool author + an MCP server author both target.

- **`ToolDispatcher.dispatch()`** signature in
  `src/forest_soul_forge/tools/dispatcher.py`. The kwargs the
  caller passes (instance_id, agent_dna, role, genre, session_id,
  constitution_path, tool_name, tool_version, args, provider,
  task_caps).
- **Outcome dataclass shape**: `DispatchSucceeded |
  DispatchRefused | DispatchPendingApproval | DispatchFailed`
  in the same module. Each field on each variant is part of the
  contract.
- **`mcp_call.v1`** — the dispatcher tool that routes to operator-
  registered MCP servers. The tool's input schema, output schema,
  and the per-server registry shape (in
  `ctx.constraints["mcp_registry"]`) are all v1.0 surfaces.
- **Governance pipeline ordering** — the documented step order
  from `governance_pipeline.py`: HardwareQuarantine → TaskUsageCap
  → ToolLookup → ArgsValidation → ConstraintResolution →
  PostureOverride → GenreFloor → InitiativeFloor → CallCounter →
  McpPerToolApproval → ApprovalGate → PostureGate. Adding a step
  is non-breaking; reordering or removing one is.

### 2. Audit chain schema

The on-disk evidence layer.

- **JSONL line shape** (in `src/forest_soul_forge/core/audit_chain.py`):
  `{seq, timestamp, agent_dna, event_type, event_data,
  prev_hash, entry_hash}`. Hash discipline (`entry_hash` =
  `sha256(prev_hash || canonical_json(event))`) and append-only
  semantics are part of the contract.
- **70+ event-type payload schemas**. Each event type
  (`tool_call_dispatched`, `agent_archived`, `memory_consent_granted`,
  `agent_plugin_granted`, `agent_posture_changed`, etc.) has a
  documented payload shape. Adding fields is non-breaking;
  removing or renaming is.
- **Default chain path** at `examples/audit_chain.jsonl` per
  `daemon/config.py`'s `audit_chain_path` default. Override via
  `FSF_AUDIT_CHAIN_PATH`.

### 3. Plugin manifest schema v1

The contract a plugin author targets.

- **`plugin.yaml` structure** validated by Pydantic in
  `src/forest_soul_forge/plugins/manifest.py`. `schema_version: 1`,
  `name`, `version`, `type` (mcp_server | etc.), `side_effects`,
  `capabilities`, `entry_point` (type + command + sha256),
  `requires_human_approval` per-tool map, `required_secrets`.
- **sha256 entry-point pinning** is the trust boundary. Any
  plugin protocol change that loosens this is a breaking change.
- **Canonical examples** at `examples/plugins/` (forest-echo,
  brave-search, filesystem-reference) demonstrate the manifest
  shape; submission flow at `examples/plugins/CONTRIBUTING.md`.

### 4. Constitution.yaml schema

The agent identity binding.

- **Top-level fields**: `schema_version`, `constitution_hash`,
  `agent` (with dna, role, genre, agent_name, initiative_level),
  `policies`, `tools`, `allowed_mcp_servers`, `allowed_paths`,
  `allowed_secret_names`, plus the renderer's input slots.
- **Per-tool entry shape** in the `tools:` list: `name`,
  `version`, `side_effects`, `constraints`, `applied_rules`.
- **Constitution hash invariant**: a born agent's
  `constitution_hash` is bound to its identity for life. Mutable
  state (status, posture, flagged state) lives on the
  `agents` SQL row, NOT in the constitution.

### 5. HTTP API contract

The integration surface for any non-Python consumer.

- **Read endpoints** (ungated): `/healthz`, `/audit/*`,
  `/plugins`, `/plugins/{name}`,
  `/agents/{id}/plugin-grants`, `/agents/{id}/posture`,
  `/agents/{id}/character-sheet`, `/tools`, `/genres`, `/traits`,
  `/skills`, `/scheduler/tasks`, etc.
- **Write endpoints** (gated by `require_writes_enabled` +
  `require_api_token`): `/birth`, `/spawn`, `/regenerate-voice`,
  `/archive`, `/agents/{id}/tools/call`, `/plugins/reload`,
  `/plugins/{name}/{enable,disable,verify}`,
  `/agents/{id}/plugin-grants` (POST + DELETE),
  `/agents/{id}/posture` (POST), `/scheduler/tasks/*`, etc.
- **Request/response schemas** in
  `src/forest_soul_forge/daemon/schemas/`. Adding optional fields
  is non-breaking; removing or renaming is.

### 6. CLI surface

The `fsf` command-line interface.

- **Subcommands**: `forge`, `install`, `triune`, `chronicle`,
  `plugin {list,info,install,uninstall,enable,disable,verify,
  grant,revoke,grants}`, `agent posture {get,set}`.
- **Exit codes** per `src/forest_soul_forge/plugins/errors.py`:
  4 = user error / not found, 5 = duplicate, 6 = validation,
  7 = generic plugin error / server error / network failure.
  0 = success.
- **`--daemon-url`** + **`--api-token`** + `$FSF_API_TOKEN`
  fallback are the auth surface for daemon-talking subcommands.

### 7. Schema migrations

The persistence contract.

- **Strictly additive forward migrations** in
  `src/forest_soul_forge/registry/schema.py`'s `MIGRATIONS` dict.
  Adding tables, columns with defaults, indexes — all fine.
  Dropping columns, tightening constraints, renaming —
  forbidden. Escape hatch: `rebuild_from_artifacts`.
- **Current schema version: v15**. Each version's migration is
  documented inline in `MIGRATIONS[N]`.
- **Audit chain is the source of truth**. The registry is a
  derived, rebuildable index. A v15 → v16 forward migration that
  loses derivable data is fine; one that loses canonical artifact
  state is a bug.

---

## What the kernel does NOT commit to

- **Internal module layout** under `src/forest_soul_forge/`. Free
  to refactor (e.g., the ADR-0040 trust-surface decompositions of
  memory.py → memory/ and writes.py → writes/ were ABI-preserving
  internal restructures).
- **Helper functions and private dataclass shapes** that aren't
  serialized.
- **Test helpers and fixtures**.
- **Performance characteristics**. v1.0 doesn't commit to
  throughput or latency floors.
- **`examples/` content**. Canonical examples evolve.
- **`docs/` content**. ADRs, audits, runbooks all evolve.
- **Userspace** — `apps/desktop/`, `frontend/`, `dist/`, repo-root
  `*.command` scripts. SoulUX is the reference distribution; a
  community could replace any of those wholesale.

---

## Where to find each surface in code

| Surface | Canonical location |
|---|---|
| Tool dispatch | `src/forest_soul_forge/tools/dispatcher.py`, `src/forest_soul_forge/tools/governance_pipeline.py`, `src/forest_soul_forge/tools/builtin/mcp_call.py` |
| Audit chain | `src/forest_soul_forge/core/audit_chain.py`, `examples/audit_chain.jsonl` |
| Plugin manifest | `src/forest_soul_forge/plugins/manifest.py`, `examples/plugins/README.md` |
| Constitution | `src/forest_soul_forge/core/constitution.py`, `config/constitution_templates.yaml`, `soul_generated/*.constitution.yaml` |
| HTTP API | `src/forest_soul_forge/daemon/routers/`, `src/forest_soul_forge/daemon/schemas/` |
| CLI | `src/forest_soul_forge/cli/main.py` and per-command modules |
| Schema migrations | `src/forest_soul_forge/registry/schema.py` (`MIGRATIONS` dict, `DDL_STATEMENTS` tuple) |

---

## How to make a kernel change

1. **Confirm it's a kernel change.** Read
   `docs/architecture/kernel-userspace-boundary.md` for the
   directory map. If your change is in `apps/desktop/` or
   `frontend/`, it's userspace — move fast, no ABI implications.
2. **Confirm the ABI implication.** Does the change touch one of
   the seven surfaces above?
   - **No** → internal refactor, no ABI work needed.
   - **Yes, additive** → fine; document it in an ADR or commit
     message that names which surface.
   - **Yes, breaking** → file an ADR explaining why. Breaking
     changes after v1.0 require a major version bump and a
     deliberate ABI signal in the release notes.
3. **Update the relevant ADR** if the change refines a previously-
   accepted design. ADRs are the kernel's evolutionary record.
4. **Verify with the suite + drift sentinel.** Tests catch
   behavior drift; `dev-tools/check-drift.sh` catches numeric
   drift in claims (LoC, test counts, etc.).

## How to integrate against the kernel from outside

If you're writing a plugin, target **plugin manifest schema v1**
(`examples/plugins/forest-echo/` is the minimal template).

If you're writing a tool to ship in `tools/builtin/`, target
**tool dispatch protocol** (`src/forest_soul_forge/tools/base.py`'s
`Tool` Protocol + `ToolContext` shape).

If you're writing an external service that talks to Forest,
target the **HTTP API contract** (the daemon's OpenAPI / FastAPI
auto-generated docs at `/docs` are authoritative).

If you're writing a different distribution (a "second SoulUX"),
target all seven surfaces above. The reference frontend
(`frontend/js/`) is one example consumer of the HTTP API; a
distribution can replace it entirely.

## References

- ADR-0044 — Kernel Positioning + SoulUX Flagship Branding (the
  parent strategic ADR)
- `docs/architecture/kernel-userspace-boundary.md` — directory-
  level boundary map (Burst 118)
- ADR-0001 — Audit chain protocol (the load-bearing primitive)
- ADR-0007 — Constitution as immutable hash
- ADR-0019 — Tool dispatch + governance pipeline
- ADR-0040 — Trust-surface decomposition rule
- ADR-0043 — MCP-first plugin protocol
- ADR-0045 — Agent posture / trust-light system
