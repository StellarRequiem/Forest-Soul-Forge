# External project alignment read — Irisviel / Nexus

Read of [irisviel.ai/cognitive-architecture/](https://irisviel.ai/cognitive-architecture/)
on 2026-04-29. Captures what we observed, where the design DNA
overlaps with Forest's own direction, and — should the opportunity
arise — what we'd want her input on.

> **Status:** No collaboration agreement, no contact established
> beyond a shared markdown. Forest is proceeding on its own
> roadmap; this doc is an external-project read so we know
> whether to surface our work to her, and what to surface.
>
> **Confidence:** medium-high on what's visible on the public site;
> low on closed pages (no access to Cyborg Psychology / About /
> individual node detail beyond what was inline). First pass.

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

## Why this read matters to Forest's direction

### 1. Stance overlap — strikingly high on Forest's load-bearing axes

| Forest position | Irisviel signal | Match |
|---|---|---|
| **Local-first** | Era 4 description: literally "Local-first cognitive substrate" | ✅ Verbatim |
| **Constitutional governance** | Iron Gate: "Constitution established as the governance framework binding system, operator, and verification protocol" | ✅ Same primitive, same vocabulary |
| **External-verifier-required for sensitive state** | "The system cannot self-verify its own memories; only an external human verifier can promote a memory from unverified to verified status" | ✅ Same pattern as Forest's approval queue + ADR-0027 memory consent |
| **Audit-trailed identity** | The whole site IS an audit trail — every commit, every pivot, every shoulder injury dated and tagged | ✅ Same instinct |
| **Companion-tier accessibility / multimodal** (Forest mission pillar 2) | BCI work in progress, "multimodal encoding of system telemetry as a human-readable signal stream" | ✅ Adjacent — building the receive path Forest's Companion tier was designed for |

Out of the four Forest stance dimensions we'd score against,
Irisviel hits four-for-four. We have not seen a closer-aligned
external project.

### 2. Scope shape — different but compatible

- **Forest is a foundry** — produces many agents, generic substrate,
  multi-genre, multi-tier. The product is the platform.
- **Irisviel/Nexus is one specific, deeply-personal agent** — a
  cognitive architecture for a single operator, with hardware-bound
  embodiment via the BCI. The product is the artifact.

That's a compatible shape difference: Forest could in principle
serve as runtime substrate for an artifact-shaped project like
Nexus. Whether that ever happens is up to Sarah; what matters for
Forest's roadmap is that the alignment **validates the primitives
we're already building**.

### 3. Vocabulary alignment — same primitives, parallel discoveries

Both projects independently arrived at:
- "Constitution" as the governance contract
- External human verification for sensitive state promotions
- Local-first hosting as a non-negotiable design choice
- Audit-trailed everything as the operator's evidence
- Mythology / structured naming for important system components
  (Forest has security_low/mid/high tiers + named genres; Nexus has
  Marduk / Irkalla / Iron Gate)

Independent convergence on this vocabulary is the strongest signal
that the framework isn't arbitrary — it's the shape this problem
takes when two thoughtful builders work on it from different angles.
That validates Forest's existing direction and tells us several
upcoming Forest pieces will naturally land in territory Sarah's been
mapping in parallel.

## How this informs Forest's own roadmap

We're not pivoting Forest to fit Nexus. We are noting that several
items already on or near Forest's roadmap **happen to parallel** the
patterns Sarah's working with. Where the timing makes sense, we'll
prioritize those — they're things Forest needed anyway, and they
have the bonus property that an external thoughtful builder
independently chose the same shape.

| Forest-roadmap item | Standalone reason Forest needs it | Bonus parallel to Nexus |
|---|---|---|
| **K1 — `memory_verify.v1`** (shipped) | ADR-0027 always implied a verification status; until now, only consent grants existed | Maps directly to Iron Gate's "external human verifier promotes" pattern |
| **K2 — `ceremony.v1` endpoint** (shipped) | Operator-emitted milestone events were missing — every chain entry came from an agent | Provides the ceremonial framing Iron Gate uses |
| **K3 — `/audit/stream` SSE** | Replaces F6's status-bar polling with push; useful for the frontend independently | Bonus: gives any external system (BCI included) a clean live signal source |
| **K4 — triune spawn template** | Multi-agent compositions need a canonical pattern; `delegate.v1` exists but no "spawn N coordinated siblings" affordance | Triune is Sarah's architecture name; Forest has needed an N-agent template for a while |
| **K5 — chronicle export CLI** | The audit chain's only consumer is the frontend Audit tab; an HTML export would be useful for any operator | Mirrors the irisviel.ai cognitive-architecture page format |
| **K6 — `hardware_binding`** | Local-first thesis benefits from machine-fingerprint pinning regardless | Sarah's hardware-grounded posture would naturally use this |

Each item has a Forest-internal reason to ship. We're sequencing them
ahead of some lower-priority Forest items because the shape they take
will be visible to Sarah if/when she ever looks, and we'd rather have
her see the strongest possible expression of Forest's design instinct.

## What we'd hope to ask, if she's open

Not a script for an outreach campaign — a list of questions we'd
genuinely want answered. If a conversation happens organically,
these are what we'd be curious about:

1. What's the Nexus thesis in one sentence? (we have signals, not a
   definitive answer)
2. What's currently your weakest piece? (where input would be
   useful — and where Forest's adjacent work might be relevant)
3. What's currently your strongest piece? (where there's something
   we could learn from)
4. Where do you want this to go in 12 months?
5. What's open-source vs proprietary? (the site doesn't say)
6. Have you settled on the triune architecture or is that still
   exploratory?
7. The Iron Gate ceremony — is that a one-time bootstrap or
   recurring? (Forest's approval queue is recurring per call; we'd
   want to understand the verification cadence she's converged on)
8. What did you wish you'd had from day one? (the answer here is
   often the most useful for any builder thinking about platforms)

## Stance going forward

- **Forest's roadmap is Forest's roadmap.** We don't change scope
  to fit a project we have no commitment from.
- Where Forest's roadmap independently parallels Nexus, we'll
  sometimes prioritize those items — they're work we'd do anyway,
  and they make Forest's design instinct visible.
- If Sarah ever wants to look at Forest, the door is open. If she
  doesn't, that's also fine. The work stands on its own.
- **Don't presume** in commit messages, naming, docs, or external
  framing that Sarah is "using" Forest or "collaborating with"
  Forest. Use language that describes Forest's choices and notes
  the parallel where honest.

## Frictions worth flagging if a conversation happens

(Not blockers — context for any future framing.)

1. **Branding / authorship.** Nexus is her identity-investment
   project. Any imagined runtime overlap would need Forest to be
   invisible infrastructure, not a co-billed platform.

2. **Architectural commitments may be deep.** Six months in, with
   working hardware, the cost of swapping any subsystem out is real.

3. **Hardware specificity vs general-purpose design.** Nexus is
   bound to specific kit; Forest is hardware-agnostic. Anything
   we'd build for her hardware needs to abstract carefully.

4. **Tone difference.** Forest is laconic-engineering ("don't
   sound clever, ship it"). Irisviel is chronicled-as-art (Iron
   Gate ceremony, Mesopotamian naming, dated personal reflections).
   Compatible voices, but anything written together would need to
   reconcile them deliberately.

5. **Don't know commercial intent.** The site reads as research /
   portfolio — no sign of a startup, customers, funding, investors.

## What we'll do next on our own

The K-track items above (K3-K6) ship on Forest's normal cadence
alongside the open-web work (G4-G6). Each adds capability Forest
needs independently. If Sarah ever wants to see where Forest is, the
chain of K commits + the original ADR-003X are the trail to follow.
