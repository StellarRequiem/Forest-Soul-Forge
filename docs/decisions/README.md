# Architectural Decision Records (ADRs)

This folder holds one file per architectural decision, in the format popularized by Michael Nygard.

## Why ADRs

When someone (including future you) asks "why did we do it this way?", the answer should be findable in version control, not Slack. ADRs capture:

- What we decided
- Why we decided it at the time (constraints, alternatives, trade-offs)
- What we expected to happen
- What the consequences were

## Conventions

- Filename: `ADR-NNNN-short-slug.md` (e.g. `ADR-0001-apache-2-license.md`).
- Number monotonically. Never renumber; never delete. If a decision is reversed, write a new ADR that references and supersedes the old one.
- Keep them short (under one page when possible). The point is the decision, not prose.

## Template

```markdown
# ADR-NNNN: Title

## Status
Proposed | Accepted | Superseded by ADR-XXXX | Deprecated

## Context
What's the situation? What forces are at play?

## Decision
What did we decide?

## Consequences
What becomes easier? What becomes harder? What are we accepting in trade?

## Alternatives considered
What else did we look at, and why not?
```

## Index

- [ADR-0001 — Hierarchical trait tree with themed domains and tiered weights](ADR-0001-hierarchical-trait-tree.md) — Accepted 2026-04-21
- [ADR-0002 — Agent DNA and lineage](ADR-0002-agent-dna-and-lineage.md) — Accepted 2026-04-21
- [ADR-0003 — Grading engine (config-grade)](ADR-0003-grading-engine.md) — Accepted 2026-04-21
- [ADR-0004 — Constitution builder](ADR-0004-constitution-builder.md) — Accepted 2026-04-21
- [ADR-0005 — Audit chain (tamper-evident, v0.1 threat model)](ADR-0005-audit-chain.md) — Accepted 2026-04-21
- [ADR-0006 — SQLite registry as index over canonical artifacts](ADR-0006-registry-as-index.md) — Accepted 2026-04-23
- [ADR-0007 — FastAPI daemon as frontend backend](ADR-0007-fastapi-daemon.md) — Accepted 2026-04-23
- [ADR-0008 — Local-first model provider](ADR-0008-local-first-model-provider.md) — Accepted 2026-04-24
- [ADR-0016 — Session modes + self-spawning cipher](ADR-0016-session-modes-and-self-spawning-cipher.md) — Proposed 2026-04-24
- [ADR-0017 — LLM-enriched soul.md narrative](ADR-0017-llm-enriched-soul-narrative.md) — Proposed 2026-04-25
- [ADR-0018 — Agent tool catalog and per-archetype standard tools](ADR-0018-agent-tool-catalog.md) — Proposed 2026-04-25
- [ADR-0020 — Agent character sheet](ADR-0020-agent-character-sheet.md) — Proposed 2026-04-25
- [ADR-0021 — Role genres and agent taxonomy](ADR-0021-role-genres-agent-taxonomy.md) — Proposed 2026-04-25
- [ADR-0022 — Memory subsystem](ADR-0022-memory-subsystem.md) — Proposed 2026-04-25
- [ADR-0023 — Benchmark suite](ADR-0023-benchmark-suite.md) — Proposed 2026-04-25
