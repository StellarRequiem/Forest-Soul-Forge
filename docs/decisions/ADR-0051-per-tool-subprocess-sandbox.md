# ADR-0051 — Per-Tool Subprocess Sandbox

**Status:** Proposed (2026-05-05). Phase 4 of the security-hardening
arc. Last of the four Phase-4 ADRs (T25 auth, T26 SBOM, T27 sigs,
T28 encryption-at-rest, T29 sandbox). Optional opt-in by design —
acceptable for the trusted-host model, valuable for hardened
deployments.

## Context

Forest's tool runtime (ADR-0019) executes tools in the daemon's
own Python process. The dispatcher gates on constitution +
genre-tier ceiling + posture + grants + approval queue + per-call
counters before the tool runs, but once those gates clear, the tool
runs WITH THE DAEMON'S PRIVILEGES.

The 2026-05-05 outside security review put the gap precisely:

> Tool & skill execution has minimal sandboxing. 53 built-in tools
> + LLM-generated ones run in the same Python process (or via
> plugins). Postures (read_only / network / filesystem) exist and
> require approval, but no OS-level sandbox (no seccomp, no Firejail,
> no containers per agent). Benjamin's point is spot-on: a malicious
> /compromised LLM tool can do real damage if you approve it.
> External tools always need human approval, but once approved they
> execute with the daemon's privileges (or sudo helper's).

The reviewer's diagnosis is right. Forest's defense layers stop a
COMPROMISED AGENT from accidentally firing dangerous tools (the
governance pipeline does its job), but they don't stop a
COMPROMISED TOOL — once Tool-X.v1 is approved by the operator, its
Python code runs with full daemon privileges. A bug or supply-chain
attack in Tool-X means full daemon compromise.

This ADR adds **opt-in per-tool subprocess sandboxing** for tools
with `side_effects != "read_only"`. Default off (trusted-host model
preserved); operators running untrusted tools or hardened deployments
turn it on.

## Decision

This ADR locks **six** decisions:

### Decision 1 — Opt-in, default off

Sandbox enforcement is configured per Forest installation via
`FSF_TOOL_SANDBOX={off,strict,permissive}`:

- **off** (default) — current behavior. Tools run in the daemon's
  Python process. Trusted-host model.
- **strict** — every tool with `side_effects != "read_only"` runs
  in a per-call subprocess sandbox. Refusal on any sandbox setup
  failure. Production-hardened deployments.
- **permissive** — same as strict, but falls back to in-process
  execution with a warning if sandbox setup fails. Useful for
  development on platforms where the sandbox tech isn't available.

**Why opt-in:** the trusted-host model is documented in ADR-0025
(Threat model v2). Operators who accept that model don't need
sandboxing — the cost (50-200ms per dispatch + platform-specific
config) outweighs the benefit. Operators with different threat
models opt in.

This matches the ADR-0042 / ADR-0043 / ADR-0045 / ADR-0049 pattern:
substrate optional, opt-in via env var.

### Decision 2 — Subprocess + platform-specific sandbox technology

Tool execution under sandbox:

1. **Pickle-serialize** the tool's args + ToolContext (the limited
   subset needed by the tool — instance_id, agent_dna, role, genre,
   constitution allowlists; NOT the live audit chain or write_lock
   handles)
2. **Spawn subprocess** with platform-specific sandbox profile:
   - **macOS**: `sandbox-exec -f <profile.sb>` — Apple's
     sandbox(7) framework. Profile restricts file access to
     `allowed_paths`, network to `allowed_hosts` if any, no
     mach-port access except IPC back to daemon
   - **Linux**: `bwrap` (bubblewrap, used by Flatpak) — namespaces
     + seccomp filter. Mount-namespace restricts files; net-namespace
     toggles network access; seccomp blocks dangerous syscalls
     (ptrace, mount, etc.)
   - **Windows**: not supported in v1 of this ADR. Operators on
     Windows can't enable strict mode; permissive falls back
     to in-process.
3. **Subprocess runs Python** with the tool module + args, returns
   pickled result via stdout
4. **Daemon reads result**, applies same audit-chain emit (`tool_call_
   succeeded` / `_failed`) as before

Sandbox profile generation: derived from the tool's declared
`side_effects` + the agent's constitution `allowed_paths` /
`allowed_commands` / `allowed_hosts`. Each dispatch generates a
fresh, minimal profile.

**Why subprocess instead of thread-based isolation:** Python doesn't
have real thread isolation (GIL, shared memory). Subprocess is the
only way to enforce real OS-level boundaries. The 50-200ms cost is
the price.

### Decision 3 — Tool-by-tool sandbox eligibility

Not every tool can run in a subprocess sandbox. Some need access
to in-memory daemon state (the tool registry, the active provider,
the audit chain handle). Forest classifies tools as:

| Class | Examples | Sandbox-able? |
|---|---|---|
| **read-only file/data tools** | log_scan, audit_chain_verify, file_integrity, code_read | ✓ yes |
| **process/network probes** | port_scan_local, dns_lookup, traffic_flow_local | ✓ yes |
| **shell/file write tools** | shell_exec, code_edit | ✓ yes |
| **web tools** | web_fetch, browser_action | ✓ yes (with playwright in subprocess) |
| **memory tools** | memory_recall, memory_write, memory_disclose | ✗ no — need direct registry access |
| **dispatch composition tools** | delegate, llm_think | ✗ no — need provider + write_lock |

Each tool declares `sandbox_eligible: bool` in its catalog entry
(default `true` for new tools). Memory + delegate + llm_think
explicitly opt out (`sandbox_eligible: false`); they continue to
run in-process even under strict mode. The dispatcher emits
`sandbox_skipped` audit event when this happens so the operator
sees the gap.

This lets operators run strict mode meaningfully — most of the
risky tools (shell_exec, code_edit, web_fetch, browser_action) ARE
sandbox-eligible and DO get sandboxed. The subset that can't be
sandboxed is small + well-understood.

### Decision 4 — Sandbox profile from constitution + tool declaration

A sandbox profile is the OS-level enforcement of the constitution's
allowlists. For each tool dispatch under sandbox:

| Tool side_effects | Sandbox profile |
|---|---|
| `read_only` | (sandbox not engaged in default mode; would be no-op anyway) |
| `network` | Allow read on `allowed_paths` (constitution). Allow connect to `allowed_hosts` if declared. Deny all else. |
| `filesystem` | Allow read+write on `allowed_paths`. No network (unless side_effects also network). |
| `external` | Allow read+write on `allowed_paths`. Allow `allowed_commands` exec (no shell, no fork-bomb). Approval gate already required. |

Profile generation is mechanical:
1. Read constitution.yaml's allowed_paths, allowed_commands,
   allowed_hosts, etc.
2. Read tool catalog entry's side_effects
3. Compose minimal sandbox profile (deny-by-default + the explicit
   allowances)
4. Pass to sandbox-exec / bwrap

The profile is REGENERATED PER CALL because constitution can be
mutated post-birth via plugin grants (ADR-0043) and posture (ADR-
0045). No stale profile accidentally allows a previously-permitted
operation that's been revoked.

### Decision 5 — Sandbox failures audit-chain visible

Every sandbox-related event lands in the audit chain via existing
event types (no new types added):

- `tool_call_dispatched` carries `sandbox_mode: "strict"|"permissive"|
  "off"|"skipped"` in event_data
- `tool_call_succeeded` carries `sandbox_used: true|false` so the
  operator can grep for "did this run sandboxed"
- `tool_call_failed` carries `sandbox_violation: true` if the tool
  hit a sandbox boundary (file not in allowed_paths, network call
  to disallowed host, etc.). Profile + violated rule in event_data.
- `tool_call_refused` extends with reason `sandbox_setup_failed`
  for strict-mode dispatches that couldn't construct the sandbox
  (e.g., `bwrap` not installed)

This matches the existing audit pattern — additive event_data
fields, no new event types. ADR-0044's seven ABI surfaces stay
unchanged.

### Decision 6 — Schema is additive (kernel ABI compatible)

Per ADR-0044 Decision 3, this ADR's changes are ADDITIVE:

- New OPTIONAL `sandbox_eligible: bool` field on tool catalog entries
  (default `true` — most tools eligible). Existing entries unchanged
  if not annotated; reader treats missing as `true`.
- New OPTIONAL `sandbox_mode` and `sandbox_used` fields on
  tool_call event_data
- New OPTIONAL `FSF_TOOL_SANDBOX` env var (defaults to `off`)
- No registry schema changes
- No HTTP API changes
- No CLI changes

External integrators reading the chain see ALL sandbox-related
fields as optional event_data extensions — they ignore unknown
fields gracefully. Pre-ADR-0051 readers see chains that worked
fine; post-ADR-0051 readers see chains with sandbox metadata.

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Sandbox abstraction + macOS sandbox-exec | New `forest_soul_forge.tools.sandbox` module. `Sandbox` Protocol with `run(args, profile)` method. macOS impl wraps sandbox-exec(7) with profile generation. | 2-3 bursts |
| T2 | Linux bwrap impl | bubblewrap-based sandbox for Linux. Same Protocol; different command construction. | 1-2 bursts |
| T3 | Tool catalog `sandbox_eligible` field | Add to schema; mark llm_think, memory_*, delegate as `sandbox_eligible: false`. Dispatcher reads this before deciding to sandbox. | 1 burst |
| T4 | Dispatcher integration | Read FSF_TOOL_SANDBOX env; for non-eligible tools or off mode, run in-process; for strict mode, sandbox via T1/T2; emit sandbox metadata. | 1-2 bursts |
| T5 | Profile generation from constitution | Compose sandbox-exec / bwrap profile from constitution allowlists + tool side_effects. Test against canonical tools. | 1 burst |
| T6 | Audit chain extension | sandbox_mode / sandbox_used / sandbox_violation in event_data. Optional fields; existing event types. | 0.5 burst |
| T7 | Permissive mode (fallback to in-process on sandbox failure) | Wrap sandbox attempt in try/except; emit `sandbox_setup_failed` and run in-process when permissive. | 0.5 burst |
| T8 | Documentation + per-platform setup runbook | docs/runbooks/tool-sandbox.md: how to enable, platform requirements (sandbox-exec on macOS, bwrap on Linux), CVE response when a sandbox bypass surfaces. | 0.5-1 burst |

Total estimate: 7-10 bursts. Largest of the four Phase-4 ADRs.

## Consequences

**Positive:**

- Closes the "tool runs with daemon's privileges" gap the outside
  review flagged. A compromised tool hits sandbox boundaries
  before damaging the host.
- Defense-in-depth alongside the other Phase-4 ADRs: T25 closes
  network auth, T27 closes audit forgery, T28 closes data-at-rest
  disclosure, T29 closes tool-execution lateral movement.
- Profile generation is mechanical and auditable — sandbox profiles
  derive from constitution allowlists, so the operator who approved
  the constitution implicitly approved the profile.
- Optional opt-in respects the trusted-host model for operators who
  accept it; available for those who don't.

**Negative:**

- Largest implementation effort of the four Phase-4 ADRs (7-10
  bursts).
- Cross-platform variance: macOS and Linux supported, Windows not
  in v1.
- Per-call latency: 50-200ms subprocess startup + sandbox setup.
  Acceptable for human-driven tool dispatches; noticeable for
  scheduled tasks at high cadence.
- Subprocess serialization complexity: ToolContext can't be fully
  pickled (it has live registry handles, write_lock, etc.). Need
  a SerializableToolContext shape that subprocess workers can
  re-hydrate from. Implementation overhead.
- Some tools structurally can't be sandboxed (memory, delegate,
  llm_think). Operators see those in the audit chain as
  `sandbox_skipped`. The gap is documented but real.

**Neutral:**

- Doesn't change the threat model. Trusted-host stays the default;
  the sandbox is a defense-in-depth layer underneath that for
  operators who want it.
- Doesn't address process-memory exposure in the daemon. The daemon
  itself stays unsandboxed; it spawns sandboxed workers.
- Doesn't replace existing approval gates. Sandbox runs AFTER
  approval — the gate decides "should this run?", the sandbox
  decides "if it runs, what can it touch?"

## What this ADR does NOT do

- **Does not enable sandbox by default.** Default `FSF_TOOL_SANDBOX
  =off` preserves current behavior. Operators opt in.
- **Does not sandbox the daemon itself.** The daemon runs unsandboxed
  with full privileges. This ADR sandboxes individual TOOL DISPATCHES.
- **Does not sandbox memory / delegate / llm_think tools.** Those
  need direct daemon-state access; sandboxing them would require
  IPC for everything they do. Out of scope.
- **Does not implement Windows sandboxing.** Windows operators on
  v1 of this ADR can't run strict mode. Permissive mode falls back
  to in-process. Future ADR can add Windows-specific sandbox tech
  (AppContainer, Win32 job objects, etc.).
- **Does not formalize sandbox bypass auditing.** A sandbox-violated
  event is logged, but exhaustive forensic analysis (e.g., what
  syscall was attempted) is operator-side via the audit chain +
  OS-level audit (auditd / OpenBSM).
- **Does not break the kernel/userspace boundary.** Per ADR-0044
  Decision 3, this is an additive change to tool dispatch (event_data
  fields + opt-in env var). The seven ABI surfaces stay unchanged.

## References

- ADR-0019 — Tool execution runtime (the dispatcher this ADR
  extends)
- ADR-0021 — Role genres (genre kit-tier ceiling — unchanged; this
  ADR adds another layer below it)
- ADR-0025 — Threat model v2 (the trusted-host assumption this ADR
  is opt-in defense AGAINST, not a replacement for)
- ADR-0033 — Security Swarm (existing security genres demonstrate
  the multi-tier defense pattern)
- ADR-0042 — v0.5 product direction (the SoulUX flagship surface
  this ADR targets in opt-in mode)
- ADR-0043 — MCP plugin protocol (plugins are exactly the kind of
  third-party code that benefits most from sandboxing)
- ADR-0044 — Kernel positioning + SoulUX (the kernel/userspace
  boundary this ADR respects via additive opt-in)
- ADR-0045 — Agent posture / trust-light system (the runtime trust
  dial; sandboxing is the OS-level enforcement layer that posture
  is the policy layer for)
- ADR-0049 — Per-event digital signatures (companion ADR; ADR-0049
  knows WHO; ADR-0051 limits WHAT)
- ADR-0050 — Encryption at rest (companion ADR; ADR-0050 protects
  data on disk; ADR-0051 protects the host from the running tool)
- 2026-05-05 outside security review (Cowork session 87fd4f13) —
  the "tools run with daemon privileges" finding that triggered
  this ADR

## Credit

The "tools execute with daemon's privileges" framing came from the
2026-05-05 outside security review. The opt-in / strict / permissive
mode shape came from the plan-before-act discussion in the same
Cowork session — matches Forest's pattern of "substrate is optional,
opt-in via env var" (ADR-0042 T5 Apple Developer, ADR-0043
plugin grants, ADR-0045 posture, ADR-0049 KeyStore).
