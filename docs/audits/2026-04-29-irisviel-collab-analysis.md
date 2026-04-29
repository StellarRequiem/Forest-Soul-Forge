# Possible collaboration — Irisviel / Nexus

Read of [irisviel.ai/cognitive-architecture/](https://irisviel.ai/cognitive-architecture/)
on 2026-04-29. Captures who they appear to be, how it lines up with
Forest's stated positions, where the natural collab seams are, and
what to clarify before any conversation.

> **Confidence:** medium-high on what I read; low on what's behind
> closed pages (no access to Cyborg Psychology / About / individual
> node detail beyond what was inline). This is a first pass.

---

## What Irisviel is, in one paragraph

A solo build by **Sarah Rain (Las Vegas)** — a personal cognitive
architecture project named **Nexus**, chronicled in horizontal-timeline
form on irisviel.ai. Started ~Oct 2025 with self-directed research,
first commit Nov 2025, currently in "Era 4" (the "Irkalla Era") which
is explicitly described as "**Local-first cognitive substrate and
longitudinal research**". The project has been through at least one
architectural pivot ("Schema crisis" → "triune" architecture) and
formalized governance via an "Iron Gate ceremony" (Feb 14, 2026)
that established a **Constitution as the governance framework binding
system, operator, and verification protocol**. The system is
hardware-real: a Proxmox cluster anchored on **Marduk** (EPYC 7502,
256GB ECC, multi-GPU), an **RTX PRO 6000 Blackwell** (96GB GDDR7
ECC) as the ML workhorse, and most recently an **OpenBCI Ultracortex
Mk IV** BCI headset for "stable multimodal encoding of system
telemetry as a human-readable signal stream. Read-only at first; no
stimulation loop." Naming convention is Mesopotamian-mythology +
neuroanatomy — Marduk, Irkalla, Iron Gate, Corpus Callosum, Nexus.

This is **not a typical AI startup**. It's a deeply personal,
hardware-grounded research project by someone with serious skin in
the game (real hardware investment, months of focused work, a
chronicle that includes "shoulder injury racking Marduk" as a
build-log entry).

## The three things that matter for collab fit

### 1. Stance overlap — strikingly high on Forest's load-bearing axes

| Forest position | Irisviel signal | Fit |
|---|---|---|
| **Local-first** | Era 4 description: literally "Local-first cognitive substrate" | ✅ Verbatim match |
| **Constitutional governance** | Iron Gate: "Constitution established as the governance framework binding system, operator, and verification protocol" | ✅ Same primitive, same vocabulary |
| **External-verifier-required for sensitive state** | "The system cannot self-verify its own memories; only an external human verifier can promote a memory from unverified to verified status" | ✅ Same pattern as Forest's approval queue + ADR-0027 memory consent |
| **Audit-trailed identity** | The whole site IS an audit trail — every commit, every pivot, every shoulder injury dated and tagged | ✅ Same instinct |
| **Companion-tier accessibility / multimodal** (Forest mission pillar 2) | BCI work in progress, "multimodal encoding of system telemetry as a human-readable signal stream" | ✅ Adjacent — Sarah is building the receive path Forest's Companion tier was designed for |

Out of the four Forest stance dimensions I'd score against, **Irisviel
hits four-for-four**. I have not seen a closer-aligned project in this
session.

### 2. Scope shape — different but complementary

- **Forest is a foundry** — produces many agents, generic substrate,
  multi-genre, multi-tier. The product is the platform.
- **Irisviel/Nexus is one specific, deeply-personal agent** — a
  cognitive architecture for a single operator, with hardware-bound
  embodiment via the BCI. The product is the artifact.

This is the cleanest possible complementarity: **Nexus could be a
particularly opinionated agent built on Forest's runtime**. Forest
provides the audit chain, the constitution builder, the approval
queue, the per-agent secrets store, the dispatcher; Nexus provides
the depth of cognitive architecture, the hardware-bound embodiment,
the BCI integration, and the lived chronicle of what it actually
takes to keep a system like this stable over months.

### 3. Vocabulary alignment — same primitives, parallel discoveries

Both projects independently arrived at:
- "Constitution" as the governance contract
- External human verification for sensitive state promotions
- Local-first hosting as a non-negotiable design choice
- Audit-trailed everything as the operator's evidence
- Mythology / structured naming for important system components
  (Forest has security_low/mid/high tiers + named genres; Nexus has
  Marduk / Irkalla / Iron Gate)

Independent convergence on this vocabulary suggests both projects are
solving the same problem with the same instincts — the framework is
not arbitrary, it's the shape this problem takes. That's the strongest
possible signal that a collaboration would be substantive rather than
performative.

## Where Forest could supplement Nexus

Three concrete angles, ranked by leverage:

### A. Forest's runtime as Nexus's substrate (highest leverage)

Forest already has the infrastructure Nexus needs and is building
incrementally:

- **Audit chain** (ADR-0005) — hash-chained JSONL with verifier;
  Iron Gate ceremony is the operator-side ritual that Forest's
  approval queue already gates structurally.
- **Constitution builder** (ADR-0004) — three-layer composition
  (role base + trait modifiers + flagged combos), content-addressed
  hash; matches Sarah's "Constitution as governance framework"
  almost exactly.
- **Per-agent encrypted secrets store** (ADR-003X G2 — shipped
  today) — Nexus probably needs API tokens for any open-web
  integration; this is solved.
- **Genre engine** (ADR-0021) — risk-floor enforcement at dispatch;
  Nexus's "triune" architecture could be expressed as three custom
  Forest genres if she wanted that.
- **Skill manifests** (ADR-0031) — YAML-defined agent procedures;
  Sarah's "memory pipeline restored" / "embedder/reranker
  optimization" / "first commit" each look like skills that could
  be expressed declaratively.

**What this would look like in practice:** Sarah's Nexus runs as
one (or three, given triune) agents within a Forest daemon. She gets
the runtime + audit + constitutional infrastructure for free; we get
a real-world stress test from the most demanding possible operator.

### B. Forest's Companion tier matures via Nexus's BCI work

Forest's mission pillar 2 (the Companion / accessibility tier) is
designed in ADRs (0008 + 0021) but **not implemented**. Sarah is
literally building the hardware-embodiment side that pillar was
sketched against. This is mutually accelerating:

- Sarah has the BCI hardware + signal pipeline; Forest has the
  agent-runtime side that consumes the signal.
- Read-only-at-first is exactly the right starting posture for a
  Companion-tier integration (matches Forest's per-tier approval
  graduation discipline).
- Sarah's "stable multimodal encoding of system telemetry as a
  human-readable signal stream" describes the *substrate* Forest's
  Companion-tier agents would speak to.

The natural artifact would be a tool family parallel to
ADR-003X — call it ADR-003Y, **bci_telemetry.v1**, **bci_signal_window.v1**,
etc. — gated behind a new `companion` genre.

### C. Forest's Tool Forge + Skill Forge shorten Sarah's iteration loop

The chronicle shows a lot of bespoke pipeline work — "memory pipeline
restored," "embedder/reranker optimization 3920ms → 309ms,"
"corpus_callosum implemented." Each of these looks like something
that could ship as a forged tool / skill in Forest's pipeline (Tool
Forge is ADR-0030; Skill Forge is ADR-0031; both are partially
implemented). If Sarah is building each pipeline component bespoke,
Forest's forge could compress that.

## Where the friction would be

Honest about the not-fits:

1. **Branding / authorship.** Nexus is Sarah's identity-investment
   project. Embedding it in Forest's runtime risks her work feeling
   like "an agent on someone else's platform." Need to be very
   careful that any collab framing preserves Nexus's identity — best
   case is Forest is invisible infrastructure, Nexus is the visible
   work.

2. **Architectural commitments may already be deep.** Six months
   in, with Marduk + the Proxmox cluster + the migration history,
   Sarah has shipped a working system. Forest's value-add has to
   exceed the cost of ripping out + rewiring whatever she already has.
   Question to ask: what subsystems would she actually want to swap
   vs keep? The answer might be "none, but I want the audit chain
   pattern + the constitution composer for something else."

3. **Hardware-bound vs general-purpose.** Sarah's BCI work
   presupposes a specific hardware kit (Ultracortex Mk IV, EPYC,
   RTX 6000). Forest is hardware-agnostic by design. Any tool family
   we build for her hardware locks Forest to that hardware unless we
   abstract carefully. Probably solvable but worth flagging.

4. **Tone difference.** Forest is laconic-engineering ("don't
   sound clever, ship it"). Irisviel is more chronicled-as-art
   (Iron Gate ceremony, Mesopotamian naming, dated personal
   reflections). Compatible, but a collab that produces docs together
   needs to negotiate voice up front.

5. **Don't know her commercial intent.** The site reads as
   research / portfolio — no sign of a startup, customers, funding,
   investors. If she's pre-commercial that's perfectly fine for a
   collab; if she's about to raise, the collab terms are a different
   shape entirely. Ask explicitly.

## What I'd ask in the first conversation

1. **What's the Nexus thesis in one sentence?** (we have signals,
   not a definitive answer)
2. **What's currently your weakest piece?** (where she'd want
   help — the gap is the natural collab seam)
3. **What's currently your strongest piece?** (where she could
   teach us)
4. **Where do you want this to go in 12 months?** (research paper?
   personal companion? open-source release? commercial product?)
5. **What's open-source vs proprietary?** (the site doesn't say)
6. **Have you settled on the triune architecture or is that still
   exploratory?** (we'd want to know before suggesting Forest
   maps cleanly to it)
7. **The Iron Gate ceremony — is that a one-time bootstrap or
   recurring?** (Forest's approval queue is recurring per call;
   want to understand the verification cadence)
8. **What do you wish you'd had from day one?** (this is the most
   useful question — the answer is exactly what Forest could supply)

## Recommendation

**Worth pursuing.** This is the highest-alignment external project I'd
expect to find in this space. The independent convergence on
"local-first + constitutional + externally-verified + audit-trailed"
is rare and signals deep agreement on the shape of the problem.

**Don't lead with "use our platform."** Lead with curiosity about her
work and what she wishes she'd had. The collaboration shape will
emerge from that conversation more honestly than from me prescribing
it. If Forest's audit chain or constitution composer or secrets store
is genuinely useful to her, she'll say so unprompted; if not, that's
also valuable signal.

**Concrete first move:** invite her to look at Forest, specifically
the [`docs/decisions/`](../decisions/) folder (the ADR catalog).
She'll either recognize the patterns and want to talk, or she'll
have already solved each problem differently and we'll learn from
the divergence. Either outcome is useful.

If she's open to it, the cheap experiment is: forge a single
"Nexus-companion" agent on Forest with the constitution she'd write
for it — watch which Forest primitives she reaches for naturally,
which she ignores, which she'd want changed. That's a one-day
exercise that reveals the actual fit.
