# ADR-0024 — Project horizons and long-term direction

- **Status:** Proposed (vision)
- **Date:** 2026-04-27
- **Supersedes:** —
- **Related:** every prior ADR. This is the north-star document; individual decisions still get their own ADRs as they become real.

## Context

After 23 ADRs and four phases of work, the project has a **rock-solid identity-and-audit foundation**: trait engine, DNA + lineage, constitution builder, tamper-evident audit chain, FastAPI daemon, vanilla-JS frontend, tool catalog, genre taxonomy, local-first model provider, character sheet, tool execution runtime through T2 (fast-path dispatcher).

The **next decade** of work is open. A roadmap brainstorm (April 2026, draft kept in [docs/notes/roadmap-brainstorm-2026-04.md](../notes/roadmap-brainstorm-2026-04.md)) sketches phases 0–9 ending in "1-click VR realms with millions of DAU by mid-2029." Most of that vision is exciting, some of it is achievable, and a meaningful chunk is "calendar-dressed wishful thinking" — solo-dev best-case estimates compressed into quarterly stamps with no allowance for the design work that has to happen first.

This ADR exists so the **vision and the plan are clearly separated**. Without it, every conversation about "what's next" risks treating Phase 5 (in-world agentic terminals) as if it were the next sprint. With it, we can keep the vision motivating and the plan honest.

## Decision

The work splits into **three horizons**. Only Horizon 1 is committed; Horizons 2 and 3 are direction, not deadlines.

```
   ┌──────────────────────────────────────────────────────────┐
   │ Horizon 1 — committed (~6–9 months)                      │
   │ ADR-0019 T3–T10, Skill Forge v0.1, ADR-0022 memory v0.1, │
   │ ADR-0023 benchmark fixtures, polished v0.1 release       │
   └──────────────────────────────────────────────────────────┘
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │ Horizon 2 — explore (undated)                            │
   │ Multi-agent over signed message bus, real-time A/V       │
   │ Companion tier, persistent simulation backbone           │
   └──────────────────────────────────────────────────────────┘
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │ Horizon 3 — north star (vision, no timeline)             │
   │ VR/XR entry, federated realms, in-world agentic          │
   │ terminals, social anchoring, marketplace                 │
   └──────────────────────────────────────────────────────────┘
```

### Horizon 1 — committed work (~6–9 months)

These are real, scoped, dependency-mapped. We treat them as the active backlog.

| Work item                                       | Source ADR     | State            |
|-------------------------------------------------|----------------|------------------|
| ADR-0019 T3 — approval queue + endpoint         | ADR-0019       | next             |
| ADR-0019 T4 — per-call accounting               | ADR-0019       | next             |
| ADR-0019 T5 — `.fsf` plugin format + loader     | ADR-0019       | queued           |
| ADR-0019 T6 — genre runtime enforcement         | ADR-0019       | queued           |
| ADR-0019 T7 — `mcp_call.v1` MCP client          | ADR-0019       | queued           |
| ADR-0019 T8–T10 — MCP server, timeouts, polish  | ADR-0019       | queued           |
| ADR-0022 memory v0.1 (episodic + semantic)      | ADR-0022       | queued           |
| ADR-0023 benchmark fixtures + suite             | ADR-0023       | queued           |
| Skill Forge v0.1 (CLI)                          | docs/notes/skill-and-tool-trees.md | designed |
| One-click installers (Mac done; Windows TBD)    | —              | partial          |
| v0.1 release + license clarification + Discussions | —           | tail             |

Skill Forge as a CLI tool is buildable in parallel with T2–T4 because it depends only on the tool catalog YAML schema and an LLM provider, both of which already exist. The frontend version waits for T5 hot-reload.

### Horizon 2 — explorations (no timeline)

These will become real ADRs when their open questions answer. Until then they sit here as direction.

**Multi-agent coordination.** ADR-0016 (session modes + self-spawning) plus the audit chain plus signed message envelopes already covers most of the protocol surface. What's missing is a concrete inter-agent communication bus design and the spawn-time provenance contract for sub-agent identity. Open question: do sub-agents share their parent's audit chain or get their own? Both have implications for memory privacy.

**Real-time A/V Companion tier.** Voice + camera interaction. Local Whisper.cpp + Piper exists; integration is non-trivial but tractable. Open question: hardware fallback for users without on-device compute. Streaming protocol between daemon and frontend needs a separate ADR; today's HTTP + JSON model doesn't fit.

**Persistent simulation backbone.** The point at which the architecture stops being an identity-factory and starts being a runtime simulation engine. **This is a category boundary**, not a tranche. It deserves its own evaluation ADR comparing Godot vs. Three.js vs. custom WASM-spatial vs. doing nothing. Probably 12+ months of work to even prototype credibly.

### Horizon 3 — north star (vision, no timeline)

These are aimed at, not committed to. They guide architecture choices today (don't paint into a corner that closes off federation; don't bake in assumptions that block VR), but no calendar dates.

- **VR/XR entry** via WebXR + Quest + Vision Pro. Three platforms × per-platform compliance × performance budgets × motion-sickness testing. Years of work.
- **Federated realms**, tiered (private / moderated public / enterprise). The federation protocol is itself a multi-month research effort. ActivityPub-style? Custom signed-event mesh? Separate ADR before any code lands.
- **In-world agentic terminals.** Plain-English commands → Skill Forge runs in background → spawns items, sprites, NPCs in real time. This is "Roblox Studio + LLM content generation." It's a vision, not a sprint.
- **Social anchoring layer.** Consent-based information dissemination across friends' agents. Cross-realm friend lists. Emergent NPC communities. Beautiful idea, **enormous information-flow-control problem** that needs ADR-0027 (memory privacy) landed first.
- **Marketplace** with revenue share. Premise: open-core for the daemon + Skill Forge, premium for cloud realms / marketplace hosting / advanced avatar engine / enterprise licenses. First-agent-free, subscription for extras.

## What carries forward from the brainstorm as-is

These survive the dissection and stay in the design intent:

- **Local-first.** Non-negotiable. Cloud is opt-in for compute the user can't run.
- **Open-core.** Daemon + trait engine + audit chain + tool runtime + Skill Forge stay free forever. Premium = cloud realms, marketplace hosting, enterprise licensing, sponsored genre templates.
- **Audit chain extends to every tool execution.** Already shipping in ADR-0019 T1/T2. Continues to extend through T3–T10 and beyond.
- **Dual-use flagging never blocks by default.** Codified in ADR-0018 T2.5 (tool constraint policy). Stays.
- **Phased parallel branches** (Core Engine / UX-Embodiment / Safety-Audit / Testing / Community-Business) as the structure for any phase. Solid framing even when individual phase contents change.
- **Multi-agent over signed message bus** as the multi-agent design direction. Fits ADR-0016 + audit chain cleanly.
- **Skill Forge as the immediate-impact "wow" feature.** Already designed in [docs/notes/skill-and-tool-trees.md](../notes/skill-and-tool-trees.md). H1 work.

## What gets recast or deferred

The brainstorm bundles or sequences several items in ways that don't survive contact with the existing architecture. Recasts:

- **Sims-level avatar customization and dynamic Python tool generation are different tracks.** They share zero implementation surface — one is asset/render pipeline, the other is Skill Forge. Bundling them as one phase is a category error. Avatar work belongs in the UX-Embodiment branch and can move on its own timeline; Skill Forge belongs in Core Engine.
- **Local image gen needs a hardware fallback.** Stable Diffusion (or equivalent) on-device assumes a GPU. Mac Intel users, Linux without dedicated GPU, older hardware — they're locked out unless there's a degraded path (template-based, frontier-provider fallback, or "no avatar image, use trait-derived ASCII glyph"). Pick one before committing to image gen as a feature.
- **"DoD/enterprise compliance packs" reframed as a sales/services line, not a feature.** FedRAMP/IL5 authorization is an 18-month, six-figure compliance process. Air-gapped mode is partly free already (the daemon runs offline today). Compliance certification itself doesn't get shipped — it gets earned. Removed from any timeline.
- **Federation deferred until ADR-0025 (threat model v2) lands.** Federated realms introduce adversarial operators (malicious hosts, hostile users). Today's threat model is "operator-honest-but-forgetful." Federation requires upgrading that, and the upgrade dictates protocol choice. Order: threat model → protocol → implementation.
- **Persistent worlds get their own evaluation ADR before any code.** Godot vs. custom-spatial vs. Three.js-in-browser vs. defer indefinitely is a multi-thousand-engineer-hour decision. It will not be made by inertia.
- **Calendar dates removed past Horizon 1.** Phases 2–9 in the brainstorm carry quarterly stamps ("Q1 2027", "Q2 2029"). These become aspiration, not commitment. Solo-dev rule of thumb: any timeline past 12 months should be measured in "we're aiming at" not "we will ship by."
- **"Millions of DAU" is removed as a target.** It's a Discord/Roblox-scale outcome. Useful as motivation; useless as a roadmap deliverable.

## Five missing ADRs filed as placeholders

The brainstorm is silent on five topics that bite as soon as the project tries to leave the single-user, local-first, trusted-operator threat model. Each gets a placeholder ADR so it's tracked and can't be forgotten when its dependencies arrive.

| ADR    | Subject                          | Triggers when                             |
|--------|----------------------------------|-------------------------------------------|
| 0025   | Threat model v2                  | Before any federation work begins         |
| 0026   | Provider economics               | Before marketplace cut math is finalized  |
| 0027   | Memory privacy contract          | Before social anchoring (Horizon 3) work  |
| 0028   | Data portability spec            | Before the v0.1 public release            |
| 0029   | Regulatory map                   | Before any feature targeting minors / EU  |

Each placeholder is filed as a stub today. Real content lands when the trigger condition is met.

## Branch structure for any phase

Borrowed from the brainstorm and kept intact — this is the right scaffolding for organizing work even when the contents change:

- **Core Engine** — daemon, runtime, identity, audit, memory.
- **UX / Embodiment** — frontend, avatar, voice, presence.
- **Safety / Audit** — threat model, constraint policy, approval gates.
- **Testing** — unit, integration, benchmarks, adversarial.
- **Community / Business** — license, marketplace, partnerships, governance.

A given tranche typically touches 2–3 branches; rare for a single tranche to touch all five. Tracking which branches a tranche touches in the commit message keeps the workstream visible.

## Consequences

**Positive.**
- The active backlog (Horizon 1) is tractable and dependency-mapped. We can ship without re-debating direction every week.
- The vision (Horizon 3) is preserved as motivation without being treated as a deliverable.
- The five missing-ADR placeholders ensure design debt is tracked, not forgotten.
- Future "what's next" conversations have a reference to point at.

**Negative.**
- Some excitement leaks out when "1-click VR realms by 2029" is downgraded to "vision, no timeline." Necessary trade.
- Three horizons means three decision contexts to keep in mind. Mitigated by the rule "if it's not in Horizon 1, it doesn't have a deadline."

**Neutral.**
- This ADR will be revised as Horizon 1 items land and Horizon 2 items earn promotion. Expect a v2 of this ADR after Horizon 1 ships.

## Cross-references

- Roadmap brainstorm draft (the source material): [docs/notes/roadmap-brainstorm-2026-04.md](../notes/roadmap-brainstorm-2026-04.md)
- Skill Forge design: [docs/notes/skill-and-tool-trees.md](../notes/skill-and-tool-trees.md)
- AGNT prior-art (relevant for plugin format and inter-agent patterns): [docs/notes/agnt-prior-art.md](../notes/agnt-prior-art.md)
- Genre engine: ADR-0021
- Tool runtime: ADR-0019
- Memory: ADR-0022
- Benchmarks: ADR-0023
- Threat model v2 (placeholder): ADR-0025
- Provider economics (placeholder): ADR-0026
- Memory privacy (placeholder): ADR-0027
- Data portability (placeholder): ADR-0028
- Regulatory map (placeholder): ADR-0029
