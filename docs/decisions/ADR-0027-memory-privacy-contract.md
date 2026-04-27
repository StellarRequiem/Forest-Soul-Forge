# ADR-0027 — Memory privacy contract (information flow control)

- **Status:** Placeholder
- **Date:** 2026-04-27
- **Triggers when:** Before social anchoring (Horizon 3) work; before any feature lets one agent disclose another agent's memory to a third party.
- **Related:** ADR-0022 (memory subsystem — the data this contract governs), ADR-0021 (role genres — Companion has the strict contract; this ADR formalizes it across genres), ADR-0024 (horizons), ADR-0025 (threat model v2).

## Why this is a placeholder

ADR-0022 establishes that agents have memory. ADR-0021 gives each genre a privacy posture (Companion: strict; Observer: permissive). What's missing is a **formal information-flow contract**: when can agent A's memory of conversation X be referenced by agent B? Under what consent? With what audit trail?

This becomes load-bearing the moment two scenarios materialize:

1. **Multi-agent coordination** (Horizon 2) — sub-agents reading parent's memory, peer agents sharing context.
2. **Social anchoring** (Horizon 3) — "your Guardian tells your friend's agent about a life event you opted into sharing." Beautiful idea, GDPR-grade information-flow problem.

GDPR, COPPA, and similar regimes all care about *purpose limitation*, *data minimization*, *consent withdrawal*, and *deletion guarantees*. None of these are in the codebase today. The audit chain helps (every memory read could be hashed into it) but doesn't decide the policy.

This stub exists so when memory-consuming features start touching multi-agent or social surfaces, the contract is designed up front.

## Sketch of what this will cover

- **Read scopes**: per-agent-private, per-lineage (parent + descendants), per-realm (Horizon 3), per-explicit-consent.
- **Consent model**: per-event consent (opt-in per disclosure) vs. per-relationship consent (opt-in once, applies to category) vs. tiered consent (different scopes for different friends).
- **Memory deletion**: when a user deletes a memory, what propagates? Is the audit-chain entry redacted (breaks the hash chain) or marked-tombstone (preserves chain, hides content)?
- **Data minimization**: when agent A summarizes a conversation for agent B, what subset crosses the boundary? Default to most-private, override with explicit consent.
- **Genre privacy floors**: each genre's strictness becomes a hard ceiling. Companion-class agents cannot disclose memory beyond their scope no matter what the user clicks.
- **Audit obligations**: every cross-agent memory read writes a `memory_read` event to the chain. Already half-implemented — formalize.

## Cross-references

- ADR-0022 — memory subsystem (the substrate).
- ADR-0021 — genres (already encode privacy postures by category).
- ADR-0024 — horizons (when this matters).
- ADR-0025 — threat model v2 (what attacker can see this memory).
- ADR-0029 — regulatory map (GDPR / COPPA constraints feed in here).
