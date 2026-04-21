# Repo Layout

This document explains the directory structure and the reasoning behind each choice. If you add a new top-level directory, update this file in the same change.

## Map

```
forest-soul-forge/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── .gitignore
├── pyproject.toml
│
├── docs/
│   ├── vision/           # Design briefs, positioning, product narrative
│   ├── architecture/     # System structure, trait tree design, diagrams
│   ├── decisions/        # ADRs — one file per architectural decision
│   ├── audits/           # Security reviews, compliance notes, phase audits
│   └── changelog/        # Detailed per-phase change logs (supplements CHANGELOG.md)
│
├── config/
│   ├── agent_traits.json   # Flat slider schema (machine-readable)
│   └── trait_tree.yaml     # Hierarchical trait tree (to be designed)
│
├── src/forest_soul_forge/
│   ├── core/               # trait_engine, grading, constitution, audit_chain
│   ├── agents/             # base_agent, factory, blue_team/
│   ├── soul/               # soul.md generator
│   └── ui/                 # Streamlit dashboard
│
├── tests/
│   ├── unit/
│   └── integration/
│
├── scripts/                # One-off utilities, setup helpers
│
└── examples/               # Sample agents, sample soul.md outputs, fixture data
```

## Why these choices

**`src/forest_soul_forge/` (src layout instead of top-level package).** Standard Python packaging. Forces you to install the package to import it, which catches missing-module bugs before they reach users. Also avoids name collisions when this project is later pulled into the main Forest ecosystem.

**`docs/decisions/` vs `docs/audits/` — kept separate.** Decisions are forward-looking ("we chose X over Y because"). Audits are retrospective ("we reviewed the code, here's what we found"). Mixing them makes both harder to scan. ADRs drive code; audits verify code.

**`docs/vision/` is stable, `docs/changelog/` is growing.** Vision documents capture intent at a point in time — they age but don't churn. Per-phase changelogs grow with the project. Keeping them in different folders signals the difference.

**`config/trait_tree.yaml` separate from `agent_traits.json`.** YAML supports comments and nested structures cleanly; JSON is better for machine consumption and simple key-value sliders. The hierarchical tree needs comments during design; the flat slider schema does not.

**`examples/` doubles as fixtures.** Living reference for what a good agent config / generated soul.md looks like, and concrete input/output pairs for integration tests.

**`scripts/` for operational helpers only.** Anything meant to be called from code belongs in `src/`. Anything you run once from the command line (migrations, ad-hoc data fixes, setup) belongs here.

## What's intentionally missing right now

- No `.github/workflows/` yet. Add CI after there's code to test.
- No `docker/` directory. Add when we containerize.
- No `Dockerfile` or `docker-compose.yml`. Same reason.
- No `src/forest_soul_forge/__init__.py` with code. Empty package markers only until Phase 2.
