# Forest as your governance kernel — a pitch for integrators

ADR-0044 Phase 6 outreach material (Burst 131, 2026-05-05).

This document is the 1-pager for inviting external projects to
build on Forest as their governance + audit + identity substrate.
The load-bearing v0.6+ deliverable per ADR-0044 Decision 4 is the
**first external integrator** — until somebody else builds on the
kernel, the "kernel" claim is aspirational.

If you're an evaluator reading this for the first time: yes, you
are the audience.

## What Forest is

Forest is a local-first **agent governance kernel**. Imagine the
slot Linux fills in the OS stack, but for AI agents — substrate
that other distributions and integrations build on. The flagship
distribution that ships Forest with a polished operator experience
is **SoulUX** (Tauri shell + reference frontend). Other
distributions can build on the same kernel.

Concretely, Forest ships:

1. **Tool dispatch protocol** with a 12-step governance pipeline
   (hardware quarantine → posture override → genre floor →
   per-grant trust tier → call counter → approval gate → posture
   gate). Every dispatch flows through it; nothing bypasses.
2. **Audit chain** — append-only, hash-linked JSONL. Every state
   change is one entry; every entry's `entry_hash =
   sha256(canonical_json(prev_hash || event))`. Tamper-evident.
3. **MCP-first plugin protocol** with sha256-pinned entry points,
   per-(agent, plugin) trust grants, and a runtime-mutable
   green/yellow/red posture dial.
4. **Constitution model** — content-addressed agent identity
   (DNA + role + genre + immutable hash). A born agent's
   constitution_hash is bound to its identity for life.
5. **Memory subsystem** with four privacy scopes (private /
   lineage / consented / realm) and explicit cross-agent
   disclosure semantics.
6. **Tool catalog** — 53 builtins, hash-pinned, with declarative
   constraint policy (per-genre kit-tier ceiling, per-tool
   approval, per-agent posture).
7. **Conversation runtime** — multi-room, multi-turn, @mention
   chain passes, retention-window summarization, all flowing
   through the same governance pipeline.

Plus: the kernel ABI is **specified** in
[`docs/spec/kernel-api-v0.6.md`](spec/kernel-api-v0.6.md) — 1,042
lines of contract-grade detail. And there's a **conformance test
suite** (`tests/conformance/`) you can run against your own build
to verify API compatibility.

## What Forest is not

- A workflow visual designer. (That's agnt's strength.)
- A marketplace UI / paid plugin distribution.
- A multi-provider authentication UX.
- An end-user onboarding flow beyond CLI / minimal frontend.
- Billing, tenancy, multi-org isolation.
- Any UX-first product polish that doesn't change kernel API.

If your project's competitive advantage is in any of those layers,
**Forest is your complement, not your competitor.** You provide
the operator experience; Forest provides the substrate.

## Why integrate against Forest

### If you're a workflow / agent-OS project

You've optimized for UX, marketplace, or multi-provider polish.
Your governance/audit story is probably ad-hoc — a layer of
checks added late in the build, with mismatched assurances. Forest
gives you that layer for free, validated against its own
conformance suite, with a published spec you can verify
independently.

Concrete value adds:
- Every dispatch is audit-traced by default. No "we'll add
  observability later."
- Per-tool human-approval gates. No "we'll add safety later."
- Per-(agent, plugin) trust grants with a documented precedence
  matrix. No "we'll figure out trust boundaries later."
- Hash-linked tamper-evident audit. No "we'll figure out
  compliance later."

You stay focused on workflow + UX; Forest's kernel handles the
governance discipline.

### If you're a research / academic project

If your work involves agent safety, sandboxing, or governance
primitives, Forest is the lab. The kernel ABI surfaces are
documented; the implementation is open-source; the test suite is
runnable; the audit chain produces machine-readable evidence
trails. Build experiments on top, propose ABI changes via ADR,
contribute back if your changes graduate.

### If you're a security / compliance team

You need agents but don't trust the runtime. Forest lets you
*verify* the runtime — every action is gated, audited, reversible.
The audit chain is operator-evidence (what the daemon did),
hash-linked so tampering surfaces. Plugins are sha256-pinned so
substitution attacks fail loudly. Tool catalog mismatches between
declaration and implementation are caught at lifespan.

### If you're building a different distribution

A terminal-only TUI, a server-headless deployment, a mobile shell,
a different desktop framework — Forest is the kernel; SoulUX is
just one userspace. The kernel runs headless (see
`docs/runbooks/headless-install.md`); the spec freezes the surfaces
you'd target; the conformance suite verifies your build.

## What we need from you

Per ADR-0044 Decision 4, the v1.0 stability commitment unlocks
when at least one external integrator validates the kernel. We
are not declaring v1.0 from a single project; that would be
self-congratulation.

What we'd like:

1. **Read the spec.** [`docs/spec/kernel-api-v0.6.md`](spec/kernel-api-v0.6.md) — 30 minutes. Tell us what's wrong, what's
   underspecified, what's missing.
2. **Run the conformance suite against your build / fork / use
   case.** Install via `pip install
   "forest-soul-forge[conformance]"` and `pytest
   tests/conformance/`. Tell us what fails. (See `tests/conformance/README.md`.)
3. **Build something.** A plugin, an integration, a second
   distribution, a research extension. Anything that exercises
   the kernel API in ways the SoulUX flagship doesn't.
4. **Tell us what hurts.** ABI choices that are wrong, error
   messages that mislead, semantics that surprise. File issues
   on the GitHub repo, propose ADRs, send us a message.

In return, we commit to:

- **No breaking ABI changes without a major-bump signal** + an
  ADR + a deprecation cycle. Pre-v1.0 we still might break
  things, but only with notice and reasoning.
- **Spec maintenance.** Every ABI change updates the spec doc;
  every conformance failure prompts either a fix in the kernel
  or an ADR'd carve-out.
- **Visible-by-default governance posture.** Decisions land in
  `docs/decisions/`; design discussions land in audit docs.
  No "trust me bro" — every architectural choice has a paper
  trail you can read and challenge.
- **Apache 2.0 license** with patent grant. ADR-0046 is the
  governance posture.

## What's at stake

Multiple agent-OS projects are chasing "the Linux of agent
runtime" today (AIOS, openfang, the agentos variants). None has
yet earned the title — the slot is open, and we think Forest's
governance/identity/audit emphasis maps to that posture better
than UI/UX investment ever could.

We can't claim Forest is the Linux of anything. **You**, building
on it and reporting back, would.

## How to start

- **Repo:** https://github.com/StellarRequiem/Forest-Soul-Forge
- **Spec:** [`docs/spec/kernel-api-v0.6.md`](spec/kernel-api-v0.6.md)
- **Quickstart for integrators:** [`docs/integrator-quickstart.md`](integrator-quickstart.md)
- **Headless install:** [`docs/runbooks/headless-install.md`](runbooks/headless-install.md)
- **License + governance:** [`docs/decisions/ADR-0046-license-and-governance.md`](decisions/ADR-0046-license-and-governance.md)
- **The kernel positioning ADR:** [`docs/decisions/ADR-0044-kernel-positioning-soulux.md`](decisions/ADR-0044-kernel-positioning-soulux.md)
- **Contact:** alexanderprice91@yahoo.com or via GitHub issues
