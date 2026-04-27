# Roadmap brainstorm — April 2026

**Status:** Source material for ADR-0024 (project horizons). Preserved as-is so the dissection in ADR-0024 has something concrete to point at.
**Author:** Alex (drafted with Grok assistance).
**Captured:** 2026-04-27.

This is the original draft roadmap brainstormed during a work break. It sketches phases 0–9 ending in "1-click VR realms" with consumer adoption by mid-2029. ADR-0024 dissects it into committed work / explorations / north-star vision and explains what got recast.

Read this as the **vision document**. ADR-0024 is the **plan**.

---

## Forest-Soul-Forge Full Roadmap: From Current Foundation (April 2026) to Persistent VR Digital Universe + Ecosystem

This is a straightforward, heavily detailed, realistic roadmap built directly from the repo's current state (as of April 26, 2026). You have already shipped the rock-solid foundation: trait engine (29 dims / 6 domains), DNA + lineage, constitution builder, tamper-evident audit chain, FastAPI daemon, vanilla-JS frontend, tool catalog, genre taxonomy, local-first stack (Docker + Ollama), and all accepted ADRs 0001–0008. Tool execution runtime (ADR-0019) is the immediate next keystone.

The roadmap keeps your core philosophy intact: local-first, tamper-evident, 2A-style empowerment (flag/log dual-use tools, never block by default), open-core, and radical user ownership. It branches into layered workstreams so you (or contributors) can parallelize without losing momentum.

Each phase includes:
- Timeline (solo/small-team realistic estimate)
- Goals + deliverables
- Layered branches (Core Engine, UX/Embodiment, Safety/Audit, Testing, Community/Business)
- Dependencies, risks, success metrics

Overall timeline to "sci-fi vision": 3–4 years to consumer 1-click VR realms (mid-2029), with usable prototypes much earlier. Business layer weaves in from Phase 4 onward.

### Phase 0: Foundation Polish & v0.1 Release (Now – Q2 2026, 6–8 weeks)

**Goal:** Ship a stable, documented v0.1 that people can actually run and trust before adding magic.

**Key Deliverables**
- Tool execution runtime (ADR-0019) fully live in sandbox venv.
- Character sheet endpoint (ADR-0020).
- Memory subsystem basics (ADR-0022 — short-term episodic + long-term semantic).
- Full test suite + benchmark fixtures (ADR-0023).
- One-click Docker installer script + Windows/Mac helpers.

**Layered Branches**
- Core Engine: Complete sandbox venv tool runner with plain-English → code gen → auto-test → risk flagging.
- UX/Embodiment: Polish live preview sliders; add simple static avatar preview (trait-derived image prompt).
- Safety/Audit: Extend audit chain to every tool execution event (prompt, generated code, flags, outcome).
- Testing: Per-genre benchmarks + automated escape attempts.
- Community/Business: Public roadmap ADR, license clarification (Apache 2.0 core + future premium), GitHub Discussions enabled.

Dependencies: None (all ADRs already filed). Risks: Sandbox escapes → mitigate with strict resource limits + read-only defaults. Success: 100% test pass rate, first external contributors filing issues, >500 stars.

### Phase 1: Skill Forge + Full Embodiment (Q3 2026, 3 months)

**Goal:** Deliver the "imagination level customization" you described — Sims avatars + dynamic tool creation.

**Key Deliverables**
- Skill Forge UI: plain-English prompt → agent generates Python tool in isolated venv → auto-scan/risk flags → human approval gate → content-addressed storage + DNA link.
- Sims-level avatar system: sliders for appearance, animations, voice params, outfits (local image gen via Stable Diffusion or similar + real-time preview).
- Dual-use flagging baked into constitution (explicit "restricted" tags logged but not blocked).

**Layered Branches**
- Core Engine: Tool descriptor auto-generation from natural language + static analysis.
- UX/Embodiment: Drag-and-drop avatar editor + live 2D preview in Forge tab.
- Safety/Audit: Every forged tool gets permanent audit entry + lineage inheritance.
- Testing: Sandbox stress tests with adversarial prompts.
- Community/Business: First marketplace prototype (JSON index of community tools).

Dependencies: Phase 0 runtime. Success: Users can describe "make me a plant-identifier tool with camera support" and get a flagged, installable skill attached to their agent.

### Phase 2: Advanced Runtime + Multi-Agent Coordination (Q4 2026, 3 months)

**Goal:** Agents can actually do things together reliably.

**Key Deliverables**
- Full memory subsystem (episodic, semantic, procedural layers tied to DNA).
- Multi-agent spawning + inter-agent communication bus (signed messages via audit chain).
- Real-time A/V Companion tier (voice + camera interaction).

**Layered Branches**
- Core Engine: Agent runtime with tool calling, memory recall, self-spawning.
- UX/Embodiment: Live voice chat interface + avatar animation sync.
- Safety/Audit: Inter-agent action approvals + cross-agent audit visibility.
- Testing: Multi-agent simulation benchmarks.
- Community/Business: Open beta invite system.

Dependencies: Phase 1. Success: Two agents can collaborate on a task with full audit trail.

### Phase 3: Persistent Simulation Worlds (Q1–Q2 2027, 4–5 months)

**Goal:** Move from single agents to shared 2D/3D simulation spaces (no VR yet).

**Key Deliverables**
- Persistent world engine (Godot or custom lightweight spatial sim, local-first).
- Agentic NPCs: spawned descendants that socialize, form relationships, remember interactions via shared memory layers.
- Basic world editor (spawn rooms, items via forged tools).

**Layered Branches**
- Core Engine: Spatial memory + NPC behavior loop.
- UX/Embodiment: 2D canvas view of world with live avatars.
- Safety/Audit: Realm-level audit chains + consent for cross-agent memory sharing.
- Testing: Emergent behavior stress tests.
- Community/Business: Public realm templates on GitHub.

Dependencies: Phase 2 memory + multi-agent.

### Phase 4: VR / XR Integration & Human Entry (Q3–Q4 2027, 5 months)

**Goal:** Humans step into the worlds via VR headsets.

**Key Deliverables**
- WebXR / Quest / Vision Pro support (WebXR fallback for broad access).
- Human avatar sync (your real-time Companion avatar becomes your in-world body).
- 1-click world launcher from the daemon.

**Layered Branches**
- Core Engine: Low-latency sync protocol between local daemon and spatial engine.
- UX/Embodiment: Full avatar customization now renders in 3D.
- Safety/Audit: Per-realm constitutions + no-contact blocking (greyed-out avatars).
- Testing: Multi-user latency + motion sickness tests.
- Community/Business: First paid cloud-hosting tier for heavy VR compute (open-core local remains free).

Dependencies: Phase 3.

### Phase 5: In-Universe Agentic Terminals (Q1 2028, 3 months)

**Goal:** The "code reality" moment you described.

**Key Deliverables**
- Agentic terminals inside worlds: plain-English commands → Skill Forge runs in background → spawns items, sprites, animations, NPCs in real time.
- All creations stay content-addressed and lineage-linked.

**Layered Branches**
- Core Engine: In-world tool execution sandbox.
- UX/Embodiment: Terminal UI that looks native to the world.
- Safety/Audit: Realm-specific risk floors + instant rollback.
- Community/Business: Marketplace for user-created assets (revenue share).

### Phase 6: Social Anchoring Layer (Q2 2028, 3 months)

**Goal:** AI companions become the new social fabric.

**Key Deliverables**
- Consent-based information dissemination (your Guardian tells friends' agents about life events).
- Cross-realm friend lists and shared spaces.
- NPC socialization engine matures into emergent communities.

Dependencies: Phase 5.

### Phase 7: Federation, Realms & Governance (Q3–Q4 2028, 5 months)

**Goal:** Tiered worlds at scale.

**Key Deliverables**
- Federated realm system (private, moderated public, enterprise/gov).
- Black-Mirror-style moderation (flagged users = grey static avatars).
- DoD/enterprise compliance packs (hardened audit, air-gapped mode).

### Phase 8: Moderation, Safety at Scale & Infrastructure (Q1 2029, 4 months)

**Goal:** Make it production-grade for millions.

**Key Deliverables**
- Self-moderation tools + gov-regulation hooks (opt-in).
- Dedicated high-security realm templates for labs/government.

### Phase 9: Monetization, Ecosystem & Universal Adoption (Q2 2029 onward)

**Goal:** 1-click download becomes the default way people "live" digitally.

**Key Deliverables**
- Open-core model finalized: core + Skill Forge free forever; premium = cloud realms, advanced avatar engine, marketplace hosting, enterprise licenses.
- First-agent-free + subscription for extras.
- Official partnerships (hardware makers, cloud hosts, VR platforms).
- App-store 1-click installer (Mac/Windows/Quest/Android).

**Business Branches (woven in from Phase 4)**
- Revenue: subscriptions, marketplace cut (10–20%), enterprise licensing, sponsored genre templates.
- Go-to-market: open beta → creator program → partnerships with VR platforms.

**Final Success Metric:** Millions of daily active users treating their forged companions and realms as their primary social/digital life layer — with full audit transparency and user ownership intact.
