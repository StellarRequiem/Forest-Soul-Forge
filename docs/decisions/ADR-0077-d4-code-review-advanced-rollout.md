# ADR-0077 — D4 Code Review domain: advanced rollout

**Status:** Proposed
**Date:** 2026-05-15
**Tracks:** Phase α tail / Domain Rollout
**Supersedes:** none
**Builds on:** ADR-0034 (SW-track triune), ADR-0067 (cross-domain
orchestrator), ADR-0072 (behavior provenance + handoffs),
ADR-0044 (kernel/SoulUX), ADR-0058 (Tool Forge UI)

## Context

Phase α (substrate) is complete: ten scale ADRs closed and the
cross-domain orchestrator can route ambiguous intents through
preferences + active learned rules to a hardcoded handoff. The
next direction is **domain rollouts** — actually birthing the
agents each ten-domain domain needs and wiring them into the
handoff catalog.

D4 Code Review is the first domain rollout for two reasons:
self-improvement priority (D4 is the only domain whose graduates
can produce more domain rollouts), and substrate maturity (the
SW-track triune has been alive in the registry since 2026-04-30
and has demonstrated a 21-event audit chain doing software work
on itself per the canonical SW-track scenario).

The triune that exists today — system_architect (researcher),
software_engineer (actuator), code_reviewer (guardian) — covers
the design → implement → review hot loop. What it does NOT
cover, and what the operator hits during real-world PR work:

1. **Tests-first discipline.** The triune today implements first
   and tests after, which inverts the discipline the operator
   wants. A test_author role drafts test cases against the
   spec before software_engineer writes code, then re-runs them
   after to prove the change.
2. **Schema/data migrations.** software_engineer can write a
   migration, but the operator's audit posture for migrations
   demands a different governance shape — write-lock contention
   awareness, FK-cascade analysis, rollback rehearsal. A
   migration_pilot role owns these.
3. **Pre-release gating.** code_reviewer signs off on a PR's
   correctness; it does NOT sign off on release-readiness
   (drift sentinel, conformance suite pass, signed-artifact
   reproducibility, changelog completeness). A release_gatekeeper
   role owns the kernel/SoulUX boundary check from ADR-0044.

## Decision

**Decision 1 — Birth three new roles into D4 Code Review.**

- `test_author` — Genre: researcher. Trait emphasis on
  cognitive + audit. Reads the spec, proposes test cases,
  writes failing tests, hands back to software_engineer.
- `migration_pilot` — Genre: guardian. Trait emphasis on
  security + audit + cognitive. Owns the migration write-path
  under the daemon's `write_lock`, runs rehearsal in a
  scratch SQLite before issuing the real one, audit-chains
  every step.
- `release_gatekeeper` — Genre: guardian. Trait emphasis on
  audit + security + communication. Runs the conformance
  suite + drift sentinel + changelog-completeness check
  before tagging a release.

**Decision 2 — Capabilities + handoffs.yaml entries.**

| domain          | capability         | role                  | skill |
|---|---|---|---|
| d4_code_review  | test_proposal      | test_author           | `propose_tests.v1` |
| d4_code_review  | migration_safety   | migration_pilot       | `safe_migration.v1` |
| d4_code_review  | release_gating     | release_gatekeeper    | `release_check.v1` |

Three new skills also ship: `propose_tests.v1`, `safe_migration.v1`,
`release_check.v1`. T4 specifies them in detail; T2 ships the
roles + a stub skill mapping that returns "not_yet_implemented"
until the real skills land in T4.

**Decision 3 — Cascade rule wiring.**

The D4 manifest already declares `handoff_targets`. T3 turns the
documented intent into actual `cascade_rules` in handoffs.yaml:

```yaml
- source_domain: d4_code_review
  source_capability: review_signoff
  target_domain: d8_compliance
  target_capability: compliance_scan
  reason: "every PR triggers compliance pass (ADR-0072 / ADR-0077)"
- source_domain: d4_code_review
  source_capability: release_gating
  target_domain: d1_knowledge_forge
  target_capability: index_artifact
  reason: "release notes auto-index as knowledge artifacts"
```

Note: the cascade to `d3_local_soc` (detection rules contributed
back) and the cascade to `d7_content_studio` (PR descriptions →
changelog) wait for those domains to be dispatchable (status:
partial or active in the registry). T6 of the D3 rollout adds
the first; T7 cascade ships then.

**Decision 4 — Birth via operator review, not auto.**

Per ADR-0072's behavior-provenance discipline, learned-rule
auto-adaptation is opt-in. New agent births are ALSO operator-
reviewed: the operator either (a) runs `dev-tools/birth-test-author.command`
manually after reviewing the soul + constitution drafts, or (b)
runs `fsf agents birth-d4-advanced` which walks all three births
through the approval queue. The cascade rules in handoffs.yaml
go through normal PR review.

**Decision 5 — Test posture.**

The D4 rollout is test-driven by its own discipline. Before any
new agent is birthed, an integration test in
`tests/integration/test_d4_advanced_handoff.py` exercises the
end-to-end flow: software_engineer's review_signoff fires the
d8_compliance cascade, the compliance scan returns within
budget, the audit chain captures the cascade source provenance.

## Consequences

**Positive:**
- The first domain rollout uses the actual cross-domain
  orchestrator that Phase α stood up.
- D4 graduates produce real PRs against the Forest repo —
  bootstrapping the self-improvement loop.
- The test_author role gives operators a way to enforce tests-
  first discipline without changing the software_engineer
  agent's existing kit.

**Negative:**
- Three new agents push the total active agent count past the
  resource-budget headroom on the M4 mini. T1 includes a
  capacity check before sign-off — if the budget would slip,
  the rollout pauses to right-size the genre ceilings.
- Cascade rules introduce new failure modes (cascade to a
  domain that's `status: planned`). The integration test
  covers the refused-cascade path so the operator surface
  shows `cascade_refused: planned_domain` rather than silent
  failure.

**Open questions:**
- Should test_author run synchronously inside software_engineer's
  dispatch loop, or asynchronously via the scheduler? Decision
  deferred to T2 — implementation will pick the simpler shape
  first and refactor if latency is a problem.
- The `release_gatekeeper` is the right role to wire the drift
  sentinel and conformance suite; SHOULD it also own the SBOM
  generation that's currently in `dev-tools/generate-sbom.command`?
  Deferred to T5.

## Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | This ADR (B331). Foundation. | 1 burst |
| T2 | Add the three roles to trait_tree.yaml + genres.yaml + soul/constitution templates + stub skills | 1-2 bursts |
| T3 | Wire handoffs.yaml entries + cascade rules + tests | 1 burst |
| T4 | Implement the three skills (propose_tests / safe_migration / release_check) | 2-3 bursts |
| T5 | Add release_gatekeeper SBOM ownership decision + dev-tools/birth-d4-advanced.command operator script | 1 burst |
| T6 | Operator runbook for D4 advanced workflow | 1 burst |

Total: 7-9 bursts.

## See Also

- ADR-0034 SW-track triune (the existing D4 substrate)
- ADR-0067 cross-domain orchestrator (the routing rail)
- ADR-0072 behavior provenance (the cascade rule precedence)
- ADR-0044 kernel/SoulUX positioning (release_gatekeeper's
  conformance check rationale)
- ADR-0058 Tool Forge UI (where test_author / migration_pilot
  may forge new helper tools at runtime)
- `config/domains/d4_code_review.yaml` (the domain manifest
  this ADR extends)
