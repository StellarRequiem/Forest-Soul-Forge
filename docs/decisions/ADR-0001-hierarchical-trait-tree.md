# ADR-0001: Hierarchical Trait Tree with Themed Domains and Tiered Weights

## Status
Accepted 2026-04-21.

## Context

The original handoff called for a "full hierarchical talent tree like web of scaling emotional and personality traits" but delivered a flat two-category slider schema (`core_traits`, `cognitive_skills`). Flat sliders can't express:

- **Thematic structure** — users need to reason about "security traits" or "audit traits" as coherent groups, not individual knobs among two dozen.
- **Intra-theme importance** — not every trait pulls equally on behavior. A high `caution` score should dominate behavior more than a high `sarcasm` score in the same agent.
- **Role-based emphasis** — a Network Watcher and an Operator Companion should weight the same trait catalog very differently.
- **Phased growth** — adding new traits to a flat list gets unwieldy fast; a tree lets each domain grow independently.

## Decision

Adopt a three-axis hierarchical trait tree, defined fully in [docs/architecture/trait-tree-design.md](../architecture/trait-tree-design.md) and implemented in [config/trait_tree.yaml](../../config/trait_tree.yaml).

1. **Domains (themes)** — five top-level categories: `security`, `audit`, `emotional`, `cognitive`, `communication`. Each is a coherent behavioral axis.
2. **Subdomains and trait tiers** — each domain has subdomains; each subdomain has traits marked primary (weight 1.0), secondary (0.6), or tertiary (0.3). This is the "tier the weights patterned per theme" directive — internal hierarchy within each theme.
3. **Role-based domain weights** — each agent role assigns a multiplier (0.4–3.0) to each domain, shifting which themes dominate without changing the underlying trait values.

Grading math (to be implemented in the Phase 2 trait engine):

```
behavior_score = Σ_domains [ role_weight × Σ_subdomains [ Σ_traits [ tier_weight × value × relevance ] ] ]
```

## Consequences

**Easier:**
- Adding new traits without rebalancing the whole system (drop into a subdomain with a tier assignment).
- Creating new agent roles (adjust role weights only; trait catalog unchanged).
- Explaining agent behavior to users ("this agent is security-dominant with a muted emotional weighting").
- Auto-generating `soul.md` in thematically-ordered prose rather than flat lists.

**Harder:**
- Initial schema design — more upfront work than flat sliders.
- UI presentation — sliders have to be grouped and show tier context.
- Migration if we restructure — five domains is a commitment; renaming or merging them later is a breaking change.

**Accepted trade-offs:**
- Five domains is locked for v0.1. User-defined domains are out of scope; users can only add traits within existing domains.
- Tier weights are fixed at 1.0 / 0.6 / 0.3. Not user-tunable in v0.1 to prevent drift between agent definitions.
- Role weights have a floor of 0.4 — no domain can be completely silenced. This prevents accidentally disabling audit/security checks.

## Alternatives considered

**Flat sliders with optional groupings.** Keep the flat schema, just add a `group` field for UI purposes. Rejected because grouping alone doesn't capture tier (primary vs. tertiary) or role emphasis. Would have to bolt those on later.

**Free-form trait tags.** Traits have arbitrary string tags instead of a fixed domain taxonomy. Rejected because tag proliferation makes role presets and grading math impossible to reason about. Taxonomy is a constraint worth accepting.

**Deeper tree (4+ levels).** e.g. domain → subdomain → cluster → trait. Rejected for v0.1 as over-engineered. Three levels (domain → subdomain → trait with tier) is enough to express the weight pattern without requiring users to navigate a complicated tree.

**Per-trait numeric weights instead of three tiers.** Rejected because unbounded per-trait weights drift toward an ad-hoc mess. Three discrete tiers force the designer to make an explicit primary/secondary/tertiary judgment.

## Phased expansion plan

This ADR covers v0.1 of the schema. Growth happens in declared phases, each of which gets its own ADR (ADR-0002+) when it lands.

| Schema version | Expansion | Target phase |
|---|---|---|
| v0.1 | 5 domains, 10 subdomains, 26 traits, 5 role presets | Phase 1 (now) |
| v0.2 | Add 5–10 more traits per domain; deepen tone/interpersonal subdomains | Phase 3 (after first working agents) |
| v0.3 | Add new domains: `ethics` (fairness, harm avoidance), `memory` (continuity, revision discipline), `tool_use` (tool-selection temperament) | Phase 4 |
| v0.4 | Enable dynamic trait drift — traits adjust based on outcomes with operator-auditable bounds | Phase 5 |
| v0.5 | Cross-agent swarm traits — e.g., collective valence for supervisor/worker coordination | Phase 6 (LangGraph supervisor layer) |

Each phase is an opportunity to decide whether the previous phase's traits earned their keep. Traits that never influence output in practice get cut at the phase boundary.

## Decisions confirmed at acceptance (2026-04-21)

1. All five domains retained (security, audit, emotional, cognitive, communication).
2. 26-trait v0.1 catalog accepted as-is.
3. Tier weight ratios fixed at 1.0 / 0.6 / 0.3.
4. Five role presets accepted: network_watcher, log_analyst, anomaly_investigator, incident_communicator, operator_companion.
5. `paranoia` renamed to `threat_prior` for neutral blue-team framing.
