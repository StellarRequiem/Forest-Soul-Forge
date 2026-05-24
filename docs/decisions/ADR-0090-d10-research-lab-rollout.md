# ADR-0090 — D10 Multi-Agent Research Lab: rollout

**Status:** Accepted (2026-05-23). All four phases shipped:
Phase A (gatherer + analyst), Phase B (critic + lab_synthesizer +
`citation_graph_build.v1` + `confidence_score.v1`), Phase C
(debate_moderator + `claim_provenance.v1` + `debate_orchestrate.v1`),
Phase D (cascade wiring + umbrella birth + domain manifest flipped
to `live`).
**Date:** 2026-05-23
**Tracks:** Domain Rollout / Multi-Agent Research Substrate
**Supersedes:** none
**Builds on:** ADR-0056 (experimenter — hypothesis-test substrate
already shipped; D10 references but does not re-create it),
ADR-0063 (Reality Anchor — verifies analyst per-claim verdicts +
synthesizer conclusions against ground truth), ADR-0067 (cross-
domain orchestrator — D10 is next after D9 closes per the rollout
order D4→D3→D8→D1→D2→D7→D9→**D10**→D5→D6), ADR-0068 (operator
profile — areas_of_focus + expertise_level shape gathering depth
+ decomposition framing), ADR-0076 (vector index for personal
context — `personal_recall.v1` surfaces operator's prior research
threads), ADR-0085 / ADR-0086 / ADR-0087 / ADR-0088 / ADR-0089
(domain-rollout precedents — same four-phase / one-commit-per-
phase shape).

## Context

D9 Learning Coach closed 2026-05-23 (ADR-0089, all four phases
CLOSED, 5 agents alive). Per ADR-0067's rollout-order plan
(D4→D3→D8→D1→D2→D7→D9→**D10**→D5→D6), **D10 Multi-Agent Research
Lab** is next.

D10's value proposition (from `config/domains/d10_research_lab.yaml`):

> Structured multi-agent debate on any topic. analyst vs. critic
> vs. synthesizer in formal turn-based exchange — operator reads
> the transcript. Adversarial collaboration mode: "make the
> strongest case for X" then "make the strongest case for not-X" —
> outputs aren't averaged, both stand. Citation-graph reasoning:
> every claim has provenance back to a source; synthesizer
> reports include the citation graph as a deliverable.
> Experimenter-driven hypothesis testing — when the lab forms a
> hypothesis, the experimenter agent (ADR-0056) runs a short-
> horizon test if testable. Confidence scoring: every conclusion
> with explicit uncertainty + the sources that would move the
> needle.

Five new roles per the domain manifest (the manifest lists six,
but `experimenter` is shipped already per ADR-0056 and is NOT
re-created here):

| Role | Capability | Posture |
|---|---|---|
| `gatherer` | source_gathering | GREEN (sourcing; non-acting) |
| `analyst` | deep_analysis | GREEN (decomposition + per-claim verdict; non-acting) |
| `critic` | adversarial_critique | GREEN (counter-argument; non-acting; guardian read_only) |
| `lab_synthesizer` | research_synthesis | GREEN (aggregation + citation graph + confidence score; non-acting) |
| `debate_moderator` | debate_moderation | GREEN (deterministic turn-ordering; non-acting) |

## Decision

**Decision 1 — Five roles, no new genres; all GREEN.**

| Role | Genre | Trait emphasis | Side-effects ceiling |
|---|---|---|---|
| `gatherer` | researcher | thoroughness + transparency + lateral_thinking | network (web_fetch is load-bearing) |
| `analyst` | researcher | thoroughness + evidence_demand + transparency | read_only (decomposition attestations to private memory) |
| `critic` | guardian | evidence_demand + double_checking + caution | read_only (counter-argument attestations to private memory) |
| `lab_synthesizer` | researcher | thoroughness + evidence_demand + transparency | read_only (synthesis reports to private memory) |
| `debate_moderator` | researcher | transparency + formality + double_checking | read_only (transcripts + turn orderings to private memory) |

The fundamental work of a Research Lab decomposes into:

1. **Gathering** (gatherer — researcher; allowlisted web_fetch +
   D1 catalog reads; never analyzes);
2. **Analysis** (analyst — researcher; per-claim decomposition
   with verify_claim cross-check; never critiques);
3. **Adversarial critique** (critic — guardian; counter-argument
   + counter-evidence; never synthesizes);
4. **Synthesis** (lab_synthesizer — researcher; aggregation +
   citation graph + confidence score; never acts);
5. **Moderation** (debate_moderator — researcher; deterministic
   turn-ordering from transcript + role-set + question; never
   acts).

All five roles are GREEN posture because the lab's deliverable
is always a memory-attested report; none of the roles cross the
external boundary themselves. Operator-driven sharing (export to
forest-files, post to forest-github, etc.) is a separate explicit
action outside the lab.

**Decision 2 — `lab_synthesizer` renamed from manifest's
`synthesizer` to avoid D1 collision.**

The domain manifest names the synthesis role `synthesizer`. D1
Knowledge Forge already has a role of that name (ADR-0086 Phase
B, researcher genre, kit:
`topic_genealogy_build.v1 + daily_knowledge_delta.v1`). Reusing
the bare name would collide in the trait_tree + genres registry
+ make the dispatcher's role-to-skill resolution ambiguous.

Phase B introduces the role as `lab_synthesizer` (same
disambiguation pattern as D1's `knowledge_verifier` vs.
`verifier_loop`, and D7's `content_researcher` vs. the researcher
genre). The runbook + STATE.md call out the rename so operators
don't expect a bare `synthesizer` from D10.

**Decision 3 — Separation of duties is the load-bearing
governance discipline.**

Five distinct lanes:

- The gatherer **sources** but NEVER analyzes (`forbid_analysis`).
- The analyst **decomposes + verdicts per claim** but NEVER
  critiques (`forbid_critique`) + NEVER synthesizes
  (`forbid_synthesis`).
- The critic **counter-argues** but NEVER synthesizes
  (`forbid_synthesis`) + NEVER overwrites the analyst's verdict
  (`forbid_analyst_verdict_overwrite`, Phase B).
- The lab_synthesizer **aggregates + scores** but NEVER critiques
  (`forbid_critique`, Phase B) + NEVER mutates the analyst's
  decompositions (`forbid_decomposition_mutation`, Phase B).
- The debate_moderator **orders turns** but NEVER takes a turn
  (`forbid_substantive_contribution`, Phase C).

Same separation-of-duties pattern as D7's
writer / editor / style_steward / distribution_pilot split + D9's
mentor / curriculum_designer / assessor / socratic_partner /
spaced_repetition_pilot split. The discipline is enforced at
constitution-policy layer regardless of posture; GREEN means
"this role's non-action surface doesn't need YELLOW-posture
operator-gating per dispatch" but the cross-role boundary is
hard.

**Decision 4 — `experimenter` is referenced, not re-created.**

The domain manifest lists `experimenter` (ADR-0056, shipped) as
an entry_agent. D10 cascade wiring (Phase D) routes hypothesis-
test dispatches to the existing Experimenter-Smith agent rather
than birthing a D10-specific one. The lab's
`hypothesis_testing.v1` skill (Phase C) is composed to dispatch
the experimenter's existing skill via `delegate.v1`. This avoids
duplicating substrate that ADR-0056 already proved out + keeps
the experimenter's identity stable across domains.

**Decision 5 — Four new builtin tools across Phases B–C; no
filesystem-class tools.**

| Phase | Tool | Side-effects | Role consumer |
|---|---|---|---|
| B | `citation_graph_build.v1` | read_only | lab_synthesizer |
| B | `confidence_score.v1` | read_only | lab_synthesizer |
| C | `claim_provenance.v1` | read_only | debate_moderator (also analyst follow-ups) |
| C | `debate_orchestrate.v1` | read_only | debate_moderator |

All four are read_only. The lab does not write to filesystem;
all deliverables are memory attestations. This keeps the entire
lab within researcher + guardian ceilings without YELLOW
posture, and means the operator can run a full debate without
per-call approval prompts — the surface is non-acting end-to-
end.

The contrast with D9's `misconception_log.v1` (filesystem,
operator-gated per call) is intentional: D9 mutates an operator-
visible ledger; D10 produces operator-readable reports. Different
governance surface = different tool ceiling.

## Phase plan

### Phase A — gathering + analysis foundation (SHIPPED 2026-05-23)

- Add `gatherer` (researcher, GREEN) + `analyst` (researcher,
  GREEN) to `trait_tree.yaml`, `genres.yaml`,
  `constitution_templates.yaml`, `tool_catalog.yaml`.
- No new builtin tools — both roles reuse existing kit.
- Skill manifests: `source_gathering.v1`, `deep_analysis.v1`.
- Birth scripts: `dev-tools/birth-gatherer.command`,
  `dev-tools/birth-analyst.command`.
- Runbook: `docs/runbooks/d10-research-lab-ops.md`.

### Phase B — adversarial critique + synthesis (SHIPPED 2026-05-23)

- Add `critic` (guardian, GREEN) + `lab_synthesizer` (researcher,
  GREEN) to trait_tree / genres / constitution_templates /
  tool_catalog.
- Two new builtin tools:
  - `citation_graph_build.v1` — directed graph (nodes=claims,
    edges=claim→source). Node ID = SHA-256 of normalized claim
    text. read_only. ~20 tests.
  - `confidence_score.v1` — per-claim aggregation: source count +
    verify_claim verdicts + critic-counter density → calibrated
    band (low / medium / high). read_only. ~20 tests.
- Skill manifests: `adversarial_critique.v1`,
  `research_synthesis.v1`, `citation_graph.v1`.
- Birth scripts: `dev-tools/birth-critic.command`,
  `dev-tools/birth-lab-synthesizer.command`.

### Phase C — debate moderation + hypothesis testing (SHIPPED 2026-05-23)

- Add `debate_moderator` (researcher, GREEN).
- Two new builtin tools:
  - `claim_provenance.v1` — walks citation graph from a target
    claim back to root sources. read_only. ~15 tests.
  - `debate_orchestrate.v1` — deterministic turn-ordering from
    transcript + role-set + question. read_only. ~15 tests.
- Skill manifests: `debate_moderation.v1`,
  `hypothesis_testing.v1` (composes the existing experimenter
  via `delegate.v1`).
- Birth script: `dev-tools/birth-debate-moderator.command`.

### Phase D — cascade + umbrella + domain live (SHIPPED 2026-05-23)

- No new roles or builtin tools.
- Skill manifest: `research_lab.v1` (umbrella composition).
- Cascade wiring in `handoffs.yaml`:
  - ACTIVATE: d1→d10 (knowledge_summarize → source_gathering),
    d10→d1 (research_synthesis → knowledge_curation),
    d10→d9 (research_synthesis → curriculum_module),
    d10→d7 (research_synthesis → content_drafting).
  - Declare INERT: d9→d10 deep_research_request,
    d10→d4 adr_proposal, verifier_loop→d10.
- Umbrella: `dev-tools/birth-d10-research-lab.command`.
- Flip `d10_research_lab.yaml` status to `live`.
- Flip this ADR to Accepted.

## Consequences

**Forensic-replay of research is a first-class property.** Every
"the lab concluded X" event in the audit chain has a citation
graph + a confidence score + the dissenting arguments preserved
in the critic's attestations. No other research-AI workflow
gives the operator that audit surface today.

**The two-role gatherer + analyst split is intentional friction.**
Operators who want "just go research X" will need to dispatch
both. The cost is one extra dispatch; the benefit is per-role
governance (gatherer's allowlist boundary; analyst's verify_claim
boundary) + per-role auditability. ADR-0067 orchestrator routing
can collapse this into a single operator surface later.

**No filesystem mutation.** The lab is end-to-end memory-based.
Operators who want a markdown report on disk dispatch a separate
explicit action via the writer or via direct file write. This
keeps every D10 dispatch GREEN posture, no per-call approval
needed, and makes the substrate fully auditable from the chain
alone.
