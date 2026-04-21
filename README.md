# Forest Soul Forge

**Local-first blue-team personal agent factory.** Build auditable, trait-controlled AI agents that run 100% on your hardware.

Every agent is defined by quantifiable trait sliders — personality and cognitive skills — that feed both a machine-readable config (`agent_traits.json`) and an auto-generated natural-language persona (`soul.md`). Every action is graded and logged in a tamper-proof audit chain. Human approval gates on high-impact actions.

> **Status:** Phase 0 — scaffolding only. No working code yet.
> This repo is a branch-able companion to the broader Forest ecosystem; proven components will be ported back to the main system once stable.

## Positioning

Blue-team cybersecurity and personal digital defense only. This repo builds tools. The operator is responsible for lawful use.

## Repo layout

See [docs/architecture/layout.md](docs/architecture/layout.md) for the directory map and the reasoning behind each choice.

## Vision

See [docs/vision/handoff-v0.1.md](docs/vision/handoff-v0.1.md) for the original design brief this project was spun up from.

## Phase plan

| Phase | Focus | Status |
|-------|-------|--------|
| 0 | Repo scaffolding, docs structure, license | in progress |
| 1 | Trait tree design (hierarchical, not flat) | not started |
| 2 | Core engines: trait, grading, constitution, audit chain | not started |
| 3 | Agent factory + blue-team starter agents | not started |
| 4 | Streamlit UI + soul.md auto-generation | not started |
| 5 | LangGraph supervisor (Omega layer), emotional vectors | not started |

## License

Apache 2.0 — see [LICENSE](LICENSE).
