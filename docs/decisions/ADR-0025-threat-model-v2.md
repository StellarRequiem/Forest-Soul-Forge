# ADR-0025 — Threat model v2 (adversarial operators / federation)

- **Status:** Placeholder
- **Date:** 2026-04-27
- **Triggers when:** Before any federation work begins (Horizon 3 federated realms — see ADR-0024).
- **Related:** ADR-0005 (audit chain — current threat model lives in its docstring), ADR-0024 (project horizons), ADR-0007 (FastAPI daemon — the surface that gets attacked).

## Why this is a placeholder

Today's threat model is **operator-honest-but-forgetful**. The audit chain is *tamper-evident* (a root attacker with write access plus the builder code can forge a valid chain — explicitly out of scope) rather than *tamper-proof*. That's right for a single-user, local-first deployment.

The moment the project introduces federation — multiple operators hosting realms, users moving between them, agents from one operator's realm interacting with agents from another — the threat model has to upgrade. Adversarial operators (malicious realm hosts) and hostile users (in-realm griefing, DoS, data exfiltration) become real categories.

Federation protocol choice (ActivityPub-style? signed-event mesh? something custom?) **depends on which threats the upgraded model takes seriously**. Picking a protocol before the threat model is reckless; doing the threat model before federation is needed is premature.

This stub exists so the dependency is tracked. When federation work earns a green light, this ADR gets written before any code lands.

## Sketch of what this will cover

- **Adversary categories**: malicious realm host, malicious user inside a realm, network attacker between realms, supply-chain attacker (Skill Forge published tools), insider attacker (operator with privileged access).
- **Per-category threats**: data exfiltration, identity forgery, audit tampering, denial-of-service, side-channel inference (genre / trait leakage), griefing.
- **Mitigations**: per-realm signing keys, audit-chain anchoring across realms (Merkle-tree summary?), rate limiting, sandboxing for forged tools, identity proofs that survive cross-realm.
- **Out-of-scope**: nation-state attackers (FSF is open-source; no claim of ability to defend against APTs).

## Cross-references

- ADR-0005 — current single-user threat model.
- ADR-0024 — horizons; federation lives in Horizon 3.
- ADR-0027 — memory privacy (separate but related: information flow inside/across realms).
