# Forest Soul Forge

**Local-first blue-team personal agent factory.** Build auditable, trait-controlled AI agents that run 100% on your hardware.

Every agent is defined by quantifiable trait sliders — personality, cognitive skills, and presentation — that feed both a machine-readable config (`agent_traits.json`) and an auto-generated natural-language persona (`soul.md`). Every action is graded and logged in a tamper-evident audit chain. Human approval gates on high-impact actions.

> **Status:** Phase 2 shipped. Phase 3 (daemon + registry + frontend rewire) in progress.
> This repo is a branch-able companion to the broader Forest ecosystem; proven components will be ported back once stable.

## Mission — two co-equal pillars

The agents built here exist to do two things with the same weight. Not a headline feature and a nice-to-have — two core obligations.

**1. Protect the user and their data.** The blue-team framing: local execution, no silent exfil, auditable behavior, explicit human approval on high-impact actions, tamper-evident record of everything the agent did.

**2. Understand the user.** Adaptive, accessibility-aware interaction as a first-class purpose. Every agent performs mental / emotional / physical status checks on the user as standard practice. Beyond that baseline, the medical / therapeutic tier supports full real-time audio-video interaction via consumer or custom peripherals, operator-or-guardian-provided profile data, and an explicit rapport-building phase during implementation. The goal for that tier: accurate translation and interaction with the world at large for users for whom "default" tone and modality don't fit — sensory impairments, neurodivergence, spectrum conditions, age extremes, ADA accommodations.

These two pillars are specified in the agent's core, not layered on as configuration. A Forest Soul Forge agent that protects its user but doesn't try to understand them is incomplete, and vice versa.

## Positioning

Blue-team defensive cybersecurity and personal digital defense, with accessibility-aware interaction built in. This repo builds tools. The operator is responsible for lawful use.

## What works today

- **Trait tree** — hierarchical YAML (`config/trait_tree.yaml`), currently 5 domains / 26 traits on `main`; v0.2 in working tree adds `embodiment` domain and brings totals to 6 / 29.
- **Agent DNA + lineage** — SHA-256 of canonical TraitProfile, 12-char short ID + full 64-char hash, closure-table ancestry.
- **Grading engine** — role-weighted config grade (0..100), per-domain breakdown, flagged-combination scan, deterministic tie-break.
- **Constitution builder** — three-layer prompt assembly (`role_base` + `trait_modifiers` + `flagged_combinations`), strictness-wins precedence, content-addressed `constitution_hash`.
- **Audit chain** — append-only JSONL, SHA-256 linked, tamper-evident under the documented threat model.
- **Demos** — `scripts/demo_generate_soul.py` end-to-end run; 11 worked examples in `examples/` covering 5 role defaults, 2 stress cases, 3-generation lineage, and a sample audit chain.

## Repo layout

See [docs/architecture/layout.md](docs/architecture/layout.md) for the directory map and the reasoning behind each choice.

## Vision

See [docs/vision/handoff-v0.1.md](docs/vision/handoff-v0.1.md) for the original design brief this project was spun up from.

## Progress detail

See [docs/PROGRESS.md](docs/PROGRESS.md) for current state — what's shipped, what's in the working tree, near-term queue, and the proposed ADR slate.

## Phase plan

| Phase | Focus | Status |
|-------|-------|--------|
| 0 | Repo scaffolding, docs structure, license | done |
| 1 | Trait tree design (hierarchical) | done |
| 2 | Core engines: trait, grading, constitution, audit chain | done |
| 3 | SQLite registry, FastAPI daemon, write endpoints, frontend rewire, Docker | in progress |
| 4 | Accessibility-adaptation runtime + mental/emotional/physical baseline check | planned |
| 5 | Medical / therapeutic tier: real-time A/V, peripherals, rapport protocol, guardian-provided data channels | planned |
| 6 | Provenance bundle (birth certificate), certification record, continuity protocol (power-loss wake, self-observation) | planned |
| 7 | Tamper-proof provenance upgrade path (VIP / deepfake defense) + central-mint hybrid (consumer product) | planned |

## License

Apache 2.0 — see [LICENSE](LICENSE).
