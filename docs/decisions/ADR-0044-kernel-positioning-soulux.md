# ADR-0044 — Kernel Positioning + SoulUX Flagship Branding

**Status:** Accepted (2026-05-05). Opens the v0.6 arc. Builds on
the v0.5 work (ADR-0042 + ADR-0043 + ADR-0045) which together shipped
the substantive kernel-shape primitives this ADR formalizes.

## Context

Forest Soul Forge has been positioned, since ADR-0042 (Burst 97), as
a "local-first agent foundry" — product framing that put it
shoulder-to-shoulder with adjacent projects like agnt-gg/agnt
(`v0.5.12`, ~6 months ahead on workflow UX, marketplace, multi-
provider integration). Direct product competition in that lane is
a losing race for a solo project.

The v0.5 arc shipped a different kind of work. Rather than chase
agnt on workflow polish, the bursts since v0.4.0 doubled down on
**governance primitives**:

- ADR-0040 trust-surface decomposition (file-grained
  `allowed_paths`)
- ADR-0041 set-and-forget orchestrator (auditable scheduled tasks)
- ADR-0043 MCP-first plugin protocol (sha256-pinned, audit-traced)
- ADR-0043 follow-up #1 (per-tool requires_human_approval
  mirroring)
- ADR-0043 follow-up #2 (per-(agent, plugin) trust grants)
- ADR-0045 agent posture system (per-agent runtime trust dial)

Composed: every dispatch flows through one governance pipeline,
gated by per-tool config × per-genre policy × per-agent posture ×
per-grant trust tier × initiative ladder × call counter, all
hash-chained in the audit chain. That is **substrate-shape** work,
not product-shape work.

A strategic discussion with Alex (2026-05-05) surfaced the
mismatch: the project has been doing kernel work while marketing
itself as a product. This ADR formalizes the realignment — Forest
becomes the kernel; SoulUX becomes the flagship distribution.

The discussion also surfaced two adjacent projects that map cleanly
to the kernel/distribution split:

- **agnt-gg/agnt** is a *distribution* — Electron desktop app +
  visual workflow designer + marketplace. They've optimized for
  the user-experience layer and have ~6-month head start.
- **ArxiaLayer1/Arxia** is a *coordination substrate* —
  offline-first Layer 1 blockchain in Rust. Different concern;
  could be a future identity / federation layer for Forest agents
  but not a competitor.

Multiple agent-OS projects (AIOS, openfang, agentos variants) are
chasing "the Linux of agent runtime." None has yet earned the
title — the slot is open. Forest's governance/identity/audit
emphasis is the differentiator that maps to that posture better
than UI/UX investment ever could.

## Decision

This ADR locks **four** strategic decisions:

### Decision 1 — Forest is the kernel

Forest Soul Forge takes the **kernel-shape** position in the agent
runtime ecosystem. The project's charter is to be the trusted
governance + identity + audit substrate that other agent OSes /
distributions can build on top of, rather than a polished end-user
product competing with agnt et al.

Kernel-shape responsibilities Forest owns:
- Tool dispatch protocol + governance pipeline
- Audit chain (hash-linked, append-only, JSONL)
- Genre engine + trait engine + constitution model
- Memory model (4 scopes + verification + flagged contradictions)
- Plugin protocol (MCP-first, sha256-pinned)
- Agent posture / trust-light system
- Per-(agent, plugin) trust grants
- Conversation runtime semantics
- Single-writer SQLite discipline

Kernel-shape responsibilities Forest does **NOT** own:
- Workflow visual designer (agnt's strength)
- Marketplace UI / paid plugin distribution
- Multi-provider authentication UX
- End-user onboarding flows beyond CLI / minimal frontend
- Billing, tenancy, multi-org isolation
- Any UX-first product polish that doesn't change kernel API

### Decision 2 — SoulUX is the flagship distribution

The Tauri desktop shell + the polished frontend reference
implementation get promoted from "the Forest desktop app" to
"SoulUX — the flagship distribution that ships Forest." Same
relationship as Linux ↔ Ubuntu, Postgres ↔ Supabase, BSD ↔ macOS.

The repository hierarchy stays single-repo for v0.6 but the
internal package boundary becomes:

```
Forest-Soul-Forge/
  src/forest_soul_forge/         ← THE KERNEL (Forest)
    daemon/                       ← kernel runtime
    tools/                        ← governance pipeline
    core/                         ← audit chain, memory, etc.
    registry/                     ← schema, persistence
    plugins/                      ← plugin loader
    cli/                          ← fsf CLI
  apps/desktop/                   ← Tauri shell (SoulUX flagship)
  frontend/                       ← reference UI (ships in SoulUX)
  examples/                       ← canonical configs / plugins
  docs/                           ← spec + ADRs + audits
```

Naming convention going forward:
- "Forest" = the kernel + reference daemon. CLI is `fsf`.
  Repository: `StellarRequiem/Forest-Soul-Forge`.
- "SoulUX" = the flagship distribution (Tauri shell + frontend
  bundled around Forest). The thing operators install when they
  want a polished experience.
- A future community could build a different distribution
  (terminal-only, server-headless, mobile, etc.) on the same
  Forest kernel. SoulUX is not the only possible userspace.

The rebrand is **intentionally light** at v0.6: README + STATE
banner + an SoulUX-named build target in `apps/desktop/`. No
repository move, no audit-chain string changes, no API renames.
Heavier rebrand artifacts (separate `soulux/` repo, distinct
website, etc.) wait until external integrator validation arrives
(Decision 4) — premature branding investment without ecosystem is
marketing theater.

### Decision 3 — Lock the kernel ABI surfaces

The v1.0 release will commit to backward compatibility on a
specific set of kernel surfaces. Identifying them now lets v0.6+
work consciously toward the freeze rather than discover at v1.0
that something needs locking.

Kernel ABI surfaces (committed-to in v1.0):

1. **Tool dispatch protocol** — `ToolDispatcher.dispatch()`
   signature, `DispatchSucceeded | DispatchRefused |
   DispatchPendingApproval | DispatchFailed` outcome shape, the
   `mcp_call.v1` contract.
2. **Audit chain schema** — JSONL line shape, hash-linking
   discipline, the 70 (and growing) event types' payload schemas.
3. **Plugin manifest schema v1** — `plugin.yaml` structure,
   per-tool `requires_human_approval` map, sha256 entry-point
   pinning.
4. **Constitution.yaml schema** — agent identity binding,
   `allowed_mcp_servers`, `allowed_paths`, per-tool constraints,
   `initiative_level`, genre claim.
5. **HTTP API contract** — every endpoint under `/agents/`,
   `/plugins/`, `/tools/`, `/audit/`, `/scheduler/`, `/healthz`
   keeps shape across v1.x.
6. **CLI surface** — `fsf` subcommands and exit codes.
7. **Schema migrations** — registry SQLite schema migrations are
   strictly additive; downgrade is allowed via
   `rebuild_from_artifacts`.

Kernel internals (NOT committed-to, free to refactor):

- Module layout under `src/forest_soul_forge/`
- Internal helper functions, dataclass shapes that aren't
  serialized
- Test helpers and fixtures
- Performance characteristics
- `examples/` and `docs/` content

Documentation deliverable for v0.7+: publish a `docs/spec/v1/`
directory that's the **source of truth** for the kernel ABI,
versioned independently from the implementation. The reference
implementation (this codebase) becomes one implementation of the
v1 spec. Other implementations could exist.

### Decision 4 — External integrator path is the load-bearing milestone

A kernel becomes "the kernel" only when somebody else builds on
it. We can't declare ourselves the Linux of anything. The
load-bearing v0.6+ deliverable is the FIRST external integrator —
either an outside party adopting Forest as their governance layer,
or a second internal distribution we build (e.g., a headless
server distribution alongside SoulUX desktop) that exercises the
kernel API in ways the primary distribution doesn't.

Concrete recruiting candidates (in priority order):

1. **agnt-gg/agnt** — the most direct overlap. Their workflow
   designer + marketplace could ride on top of Forest's
   governance/audit kernel, leaving them to focus on UX. Likely
   conversation: "you have product velocity, we have governance
   discipline; here's the kernel API, do you want it?"
2. **AIOS (agiresearch/AIOS)** — academic project; if Forest's
   governance discipline maps to their security/sandbox interests,
   they could adopt or extend.
3. **Internal second distribution** — build a headless / server
   variant of Forest in v0.7+ that exercises the kernel API
   without the SoulUX shell. Validates the kernel/userspace
   boundary by being a second consumer.

Until at least one external integrator validates the kernel API,
the "kernel" claim is aspirational. The work below is
preparation — the validation comes from outside the codebase.

## 7-phase roadmap to defensible kernel posture

| # | Phase | Cost | Blocks |
|---|---|---|---|
| 1 | Kernel/userspace boundary lock in current repo | 3-5 bursts | none |
| 2 | Publish formal kernel API spec (`docs/spec/v1/`) | 2-3 bursts | phase 1 |
| 3 | True headless mode + SoulUX frontend split | 3-5 bursts | phases 1, 2 |
| 4 | Conformance test suite for kernel API | 3-4 bursts | phase 2 |
| 5 | License posture + governance ADR | 1-2 bursts | parallel |
| 6 | First external integrator | months, not bursts | phases 2-4 |
| 7 | v1.0 release with API stability commitment | 1 burst | phase 6 |

Phases 1, 2, 5 are doable in the next ~10 bursts. Phase 6 is the
load-bearing milestone — every other phase is preparation.

## Consequences

**Positive:**

- Clarifies what the project IS so contributors / integrators
  have a coherent target.
- Stops the ambiguity around "are we agnt's competitor or
  complement?" — explicit complement.
- Operationalizes the v0.5 governance work as the differentiator
  it already is.
- Sets a finite v1.0 commitment (the kernel ABI surfaces above)
  rather than open-ended "1.0 means production-ready."
- The light-touch rebrand defers expensive marketing investment
  until external validation justifies it.

**Negative:**

- Acknowledges Forest will never out-product agnt on workflow UX
  — closes off a path some users might have wanted.
- Kernel work is less visible than UX work. Marketing surface
  shrinks; reach-via-SoulUX-distribution becomes the funnel.
- v1.0 commitment binds future flexibility on the named ABI
  surfaces. Has to be picked carefully, even at v0.6.
- "Kernel" claim has to be earned via external integrators, which
  is a months-long recruiting effort, not a coding effort. The
  most uncertain path on the roadmap.

**Neutral:**

- Single-repo posture preserved through v0.6. Decision can revisit
  at v0.7+ if the kernel/userspace separation matures into
  something that warrants two repos.
- Existing namespacing (`forest_soul_forge` Python package, `fsf`
  CLI) preserved. SoulUX gets a new `apps/desktop/` build target
  but doesn't displace the existing artifacts.

## What this ADR does NOT do

This is a **strategy + naming** ADR, not an implementation ADR. It
files the direction; it does not file the bursts that execute it.
The 7-phase roadmap above is the parent index; each phase will
spawn its own ADR or burst-level decomposition when picked up.

In particular:
- It does NOT rename the repo.
- It does NOT rename the Python package.
- It does NOT rename the CLI.
- It does NOT publish the kernel ABI spec yet (Phase 2).
- It does NOT add a SoulUX build target (Phase 3 of the roadmap).
- It does NOT pick a license posture (Phase 5).

## References

- ADR-0042 — v0.5 Product Direction (predecessor; locked the
  product framing this ADR amends to a kernel framing)
- ADR-0043 — MCP-First Plugin Protocol (the substrate-shape
  delivery)
- ADR-0045 — Agent Posture / Trust-Light System (the latest
  governance primitive)
- ADR-0040 — Trust-Surface Decomposition Rule (the file-grained
  governance principle that makes the kernel/userspace split
  natural)
- ADR-0001 — Audit chain (the load-bearing primitive everything
  else hangs off)
- ADR-0007 — Constitution as immutable hash (the identity
  invariant that distinguishes Forest from runtime-mutable
  systems)

## Credit

The kernel-positioning framing surfaced in a chat with Alex
2026-05-05 about how Forest's actual differentiation
(governance/identity/audit/coherence) maps to substrate-shape
positioning, while UI/UX competition with agnt et al. is a losing
race. The SoulUX naming intuition came from the same conversation.
