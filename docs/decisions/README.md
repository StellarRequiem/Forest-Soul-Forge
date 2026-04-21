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
