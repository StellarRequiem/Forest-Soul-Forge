# ADR-0026 — Provider economics (frontier LLM cost model)

- **Status:** Placeholder
- **Date:** 2026-04-27
- **Triggers when:** Before marketplace cut math is finalized; before frontier provider becomes a billed product surface.
- **Related:** ADR-0008 (local-first model provider — the architecture this builds on), ADR-0019 T4 (per-call accounting — the metering infrastructure), ADR-0024 (horizons).

## Why this is a placeholder

The codebase already has both a `LocalProvider` (Ollama) and a `FrontierProvider` (any OpenAI-compatible HTTPS endpoint with a key). Per-call token + cost accounting is being added in ADR-0019 T4. What's **not** decided is the economics:

- Who pays when a user's local model is too small for a task and the daemon falls back to frontier?
- Does the open-core daemon ship with a "bring your own key" frontier provider only, or is there a hosted-frontier subscription that abstracts the key?
- If the hosted version exists, what's the markup? Is it pass-through, per-token-with-margin, or flat-rate-with-cap?
- Marketplace forge tools that wrap LLM calls (`summarize.v1`, `classify.v1`, `translate.v1`): does the tool author see any cut, or is it operator-pays-the-LLM-bill?

These are not technical decisions — the technical infrastructure is already (or about to be) there. They're business/product decisions that have to be made before the marketplace launches with prices on it.

This stub exists so when marketplace work begins, the cost-flow design is on the agenda.

## Sketch of what this will cover

- **Cost flow taxonomy**: local-only (free), BYO-key frontier (free to FSF, costs to user's frontier account), hosted frontier (FSF-billed), marketplace-tool-with-LLM (who pays whom).
- **Margin model**: pass-through, per-token markup, flat subscription tiers. Trade-offs of each.
- **Free-tier shape**: how much can a user do without paying anything? "First agent free" + something? Local-only default?
- **Marketplace revenue split**: 80/20 author/platform like the App Store? 90/10? 70/30? Industry comps.
- **Audit-trail implications**: every paid tool call should be hashed into the audit chain so the user can verify their bill against the chain. ADR-0019 T4 makes this possible; this ADR decides the contract.

## Cross-references

- ADR-0008 — local-first provider; this ADR is the economic counterpart.
- ADR-0019 T4 — accounting infrastructure.
- ADR-0024 — horizons; marketplace lives in Horizon 3.
