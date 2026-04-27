# ADR-0029 — Regulatory map (EU AI Act / COPPA / CSAM / etc.)

- **Status:** Placeholder
- **Date:** 2026-04-27
- **Triggers when:** Before any feature targeting minors or EU users; before public availability past local-first single-user.
- **Related:** ADR-0024 (horizons), ADR-0027 (memory privacy — interacts with GDPR), ADR-0028 (portability — Article 20), ADR-0025 (threat model — content moderation is partly a security problem).

## Why this is a placeholder

A local-first, single-user FSF install has minimal regulatory surface. The user is running software on their own machine; there's no service provider in the legal sense.

The regulatory surface **explodes** the moment any of these land:

- **Hosted realms** — FSF becomes a service provider. EU AI Act applies. GDPR applies for any EU user. CCPA for California.
- **Multi-user shared spaces** — content moderation obligations. CSAM scanning is non-optional in most jurisdictions for any platform that hosts user-generated visual content (Horizon 3 in-world creations qualify).
- **Minors as users** — COPPA in the US. Age-appropriate design code in the UK. EU AI Act has stricter rules for minor-facing AI.
- **High-risk AI use cases** — EU AI Act Annex III. If FSF agents are used for hiring, credit, health, education, etc., they may be high-risk and have to register.

None of these apply today (single-user local FSF). Most apply in Horizon 3 (federated realms). At least one — CSAM scanning — has criminal liability attached, so it cannot be treated as an afterthought.

This stub exists so when each of those scenarios materializes, the regulatory checklist is written *before* the feature ships, not in response to a complaint.

## Sketch of what this will cover

- **Per-jurisdiction baseline**: EU (AI Act, GDPR, DSA), US (state-by-state for CCPA-class laws; COPPA federally), UK (DPA 2018, OCA), and a "rest-of-world" lookup for the top markets we'd plausibly serve.
- **Per-feature mapping**: which Horizon 2/3 features trigger which regulatory category. Hosted realms → DSA + GDPR + AI Act. Minor-facing → COPPA + UK age-appropriate design.
- **CSAM scanning policy**: any user-generated visual content in a hosted realm is subject to detection obligations. Pick a provider (PhotoDNA-equivalent), document the audit trail, document operator obligations.
- **Open-source carveouts**: AI Act has limited carveouts for free-and-open-source models. FSF's daemon is open-source; the hosted realm service would not be carveable. Important to keep the line clear.
- **Data residency**: where does memory live for an EU user? Hosted realms with EU users probably need EU-resident storage. Local-first sidesteps this for self-hosted deployments.
- **Age-gate design**: how is age verification implemented? The frontend has no concept of identity today; this is a whole subsystem.
- **Adverse-event reporting**: AI Act and incoming similar laws require service providers to report serious incidents. Operational obligation; needs runbook.

## Cross-references

- ADR-0024 — horizons (most regulatory surface lives in Horizon 3).
- ADR-0027 — memory privacy (GDPR Articles 5, 6, 17, 20).
- ADR-0028 — portability (GDPR Article 20).
- ADR-0025 — threat model (content moderation overlaps with abuse defense).
