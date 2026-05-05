# Kernel / Userspace Boundary — Forest Soul Forge

ADR-0044 Phase 1.1, Burst 118 (2026-05-05).

This document formalizes which directories of the repository are
**kernel** (the substrate Forest commits to backward-compatibility
on at v1.0 — see ADR-0044 Decision 3) versus **userspace** (the
flagship SoulUX distribution + reference frontend, where breaking
changes are routine).

The boundary is a contract: kernel surfaces are stable and external
integrators target them; userspace surfaces are the reference
implementation's product polish. A future community distribution
could replace any userspace component without touching the kernel.

## Directory map

| Path | Side | Notes |
|---|---|---|
| `src/forest_soul_forge/` | **kernel** | THE Forest Python package. Every subpackage below ships under the kernel ABI commitments unless flagged otherwise. |
| `src/forest_soul_forge/daemon/` | **kernel** | FastAPI app, lifespan, dependency injection, HTTP routers. The HTTP API contract under `/agents`, `/plugins`, `/tools`, `/audit`, `/scheduler`, `/healthz` is part of the v1.0 freeze. |
| `src/forest_soul_forge/tools/` | **kernel** | Tool dispatcher, governance pipeline, per-tool builtins, `mcp_call.v1`. The `ToolDispatcher.dispatch()` signature + outcome-dataclass shape is part of the v1.0 freeze. |
| `src/forest_soul_forge/core/` | **kernel** | Audit chain (hash-linked JSONL), memory model, genre engine, secrets, tool catalog, constitution loader. |
| `src/forest_soul_forge/registry/` | **kernel** | SQLite schema, migrations, per-table accessors. Schema is strictly additive; downgrade is via `rebuild_from_artifacts`. |
| `src/forest_soul_forge/plugins/` | **kernel** | Plugin loader, manifest validation, repository, errors. `plugin.yaml` schema v1 is part of the v1.0 freeze. |
| `src/forest_soul_forge/cli/` | **kernel** | The `fsf` CLI surface. Subcommands + exit codes are part of the v1.0 freeze. |
| `src/forest_soul_forge/forge/` | **kernel** | Tool / skill forge runtimes (skill manifest interpreter, skill expression compiler, sandbox). |
| `src/forest_soul_forge/soul/` | **kernel** | Voice renderer + soul.md generator. The on-disk soul artifact format is part of the v1.0 freeze. |
| `src/forest_soul_forge/security/` | **kernel** | PrivClient (sudo helper). The privileged-ops boundary. |
| `src/forest_soul_forge/agents/` | **kernel** | Bound-agent classes (placeholder + Phase D blue-team agents). |
| `config/` | **kernel-adjacent** | YAML configuration *schema* (trait_tree, genres, tool_catalog, constitution_templates) is part of the v1.0 freeze. The specific *values* in these files are operator-customizable — they're not part of the freeze. |
| `examples/` | **hybrid** | Mostly userspace (canonical example plugins, scenarios — narrative content for operators). Two exceptions are **kernel-adjacent seed state**: `examples/audit_chain.jsonl` is the default `audit_chain_path` per `daemon/config.py` (the live chain operators read; CLAUDE.md notes this), and `examples/skills/*` are the canonical authored skill manifests the install scripts copy into runtime `data/forge/skills/installed/`. Both are kernel-adjacent because the daemon's defaults and CLI fallbacks reference them. The rest (plugins, scenarios, README content) is userspace and free to evolve. |
| `apps/desktop/` | **userspace (SoulUX)** | The Tauri 2.x desktop shell that bundles the daemon as a sidecar. The flagship distribution's installer surface. ADR-0042 T3.1 + T4. |
| `frontend/` | **userspace (SoulUX)** | Vanilla-JS reference frontend. Not part of the kernel — a different distribution could replace it entirely with no kernel impact. |
| `docs/decisions/` | **kernel** | ADRs are the kernel's design record. |
| `docs/architecture/` | **kernel** | Architectural overview docs (incl. this one). |
| `docs/audits/` | **kernel** | Phase-boundary audit timeline. |
| `docs/runbooks/` | **kernel-adjacent** | Operational runbooks. Some apply to kernel ops; some to SoulUX-distribution ops. |
| `docs/spec/` | **kernel** | Formal kernel API spec lives here. `kernel-api-v0.6.md` (Burst 127, 2026-05-05) is the contract-grade specification of all seven ABI surfaces. Future spec versions (v1.0 once external integrator validation arrives per ADR-0044 Phase 6) ship as additional version-tagged files. |
| `dist/` | **userspace (SoulUX)** | Build helpers for the SoulUX distribution: PyInstaller spec, daemon-binary build script, zip-archive builder. |
| `data/` | **operator state** | Live registry, audit chain (dev fallback), generated agents, installed skills, installed plugins. Not part of the kernel API. |
| `soul_generated/` | **operator state** | Generated soul.md + constitution.yaml artifacts. Output of the kernel; not source. |
| `tests/` | **kernel** | Unit + integration tests target the kernel. Conformance test suite (ADR-0044 Phase 4) lands here under a separate subdir. |
| `dev-tools/` | **kernel** | Drift sentinel, repo-hygiene scripts. Used to verify kernel claims in `STATE.md` etc. against disk. |
| Repo-root `*.command` scripts | **userspace (operator)** | Day-to-day operator commands (start, stop, reset, run, push, live-test-*, swarm-bringup, clean-git-locks, etc.). Not part of the kernel API. |
| `dev-tools/commit-bursts/*.command` | **userspace (developer history)** | Per-burst commit scripts + release-tag scripts. Archived from repo root in Burst 128. One-shot history; not operationally re-runnable. Not part of the kernel API. |

## What "kernel" commits to

Per ADR-0044 Decision 3, the kernel commits at v1.0 to backward
compatibility on these surfaces:

1. **Tool dispatch protocol.** `ToolDispatcher.dispatch()`
   signature, the `DispatchSucceeded | DispatchRefused |
   DispatchPendingApproval | DispatchFailed` outcome shape, the
   `mcp_call.v1` contract.
2. **Audit chain schema.** JSONL line shape, hash-linking
   discipline, the 70+ event-type payload schemas.
3. **Plugin manifest schema v1.** `plugin.yaml` structure,
   per-tool `requires_human_approval` map, sha256 entry-point
   pinning.
4. **Constitution.yaml schema.** Agent identity binding,
   `allowed_mcp_servers`, `allowed_paths`, per-tool constraints,
   `initiative_level`, genre claim.
5. **HTTP API contract.** Every endpoint under `/agents/`,
   `/plugins/`, `/tools/`, `/audit/`, `/scheduler/`, `/healthz`.
6. **CLI surface.** `fsf` subcommands and exit codes.
7. **Schema migrations.** Strictly additive; downgrade via
   `rebuild_from_artifacts`.

What the kernel does NOT commit to:

- Internal module layout under `src/forest_soul_forge/`
- Internal helper functions / private dataclass shapes that
  aren't serialized
- Test helpers and fixtures
- Performance characteristics
- `examples/`, `docs/`, and userspace surfaces

## What "userspace" means

Userspace under SoulUX (current flagship distribution) is free to
evolve. The Tauri shell, frontend modules, build scripts,
installer, dashboards — all userspace. Renaming a frontend JS
module, restructuring the Tauri commands, redesigning the
operator dashboard: not breaking changes from the kernel's
perspective.

A future distribution could replace `apps/desktop/` + `frontend/`
entirely (e.g., a TUI-only distribution, a server-headless
distribution, a mobile PWA distribution) and the kernel API
contract remains intact. That is the load-bearing test of the
boundary.

## How a contributor uses this doc

If you're modifying code under `src/forest_soul_forge/` (kernel):
- Be conscious of whether your change touches a v1.0 ABI surface
  (the seven listed above). If yes, the change goes through an
  ADR or a deliberate ABI bump — not a casual refactor.
- Internal refactors of kernel code are fine if the public
  surface is unchanged. ADR-0040 trust-surface decomposition is
  a worked example: ~100 LoC of memory.py → memory/ package
  reorganization that left the public API intact.

If you're modifying code under `apps/desktop/`, `frontend/`,
`dist/`, or repo-root `*.command` scripts (userspace):
- Move fast. These don't bind v1.0.
- Don't import from kernel internals (anything under
  `src/forest_soul_forge/` that isn't documented as public). Treat
  the kernel's HTTP API as the integration point.

If you're adding a new surface that could be either:
- Default to **userspace**. Promotion to kernel happens when an
  external integrator (or design discipline) demands it.

## Phase 1.2+ work (queued)

This doc is Phase 1.1 of the ADR-0044 7-phase roadmap. Subsequent
sub-bursts of Phase 1 will:

- Add a top-level `KERNEL.md` summarizing the seven ABI surfaces
  with link-outs to the canonical locations (each is currently
  scattered across its own ADR).
- Audit existing imports for kernel-touches-userspace and
  userspace-touches-kernel-internals violations. Likely some
  cleanup needed in `frontend/js/` (which talks to the daemon's
  HTTP API today — the right pattern).
- Add a `dev-tools/check-kernel-userspace.sh` sentinel that
  parses imports + flags violations.

Phase 2 (`docs/spec/v1/` formal kernel API spec) is a separate
arc that won't reuse this doc directly — this doc is the
*directory boundary*; the spec is the *interface contract*.

## References

- ADR-0044 — Kernel Positioning + SoulUX Flagship Branding (the
  parent strategic ADR)
- ADR-0040 — Trust-Surface Decomposition Rule (the file-grained
  governance principle that makes kernel/userspace separation
  natural)
- ADR-0042 — v0.5 Product Direction (predecessor; locked the
  product framing this kernel/userspace ADR amends)
