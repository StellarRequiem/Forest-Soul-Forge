# ADR-0089 — D9 Learning Coach: rollout

**Status:** Proposed (2026-05-23). Phase A in flight — mentor +
curriculum_designer roles + curriculum_design.v1 builtin tool +
coaching.v1 + curriculum_design.v1 skill manifests + birth scripts
land in this phase. Phases B/C/D queued.
**Date:** 2026-05-23
**Tracks:** Domain Rollout / Operator Learning Coach
**Supersedes:** none
**Builds on:** ADR-0063 (Reality Anchor — verifies operator
claimed understanding against ground truth), ADR-0067 (cross-
domain orchestrator — D9 is next after D7 closes), ADR-0068
(operator profile — expertise_level + areas_of_focus inform every
D9 decision), ADR-0076 (vector index for personal context —
operator's prior writing + study journal feeds personal_recall),
ADR-0085 (D8 rollout precedent), ADR-0086 (D1 rollout precedent —
catalog reads feed curriculum), ADR-0087 (D2 rollout precedent —
calendar / reminders feed spaced repetition), ADR-0088 (D7 rollout
precedent — same four-phase / one-commit-per-phase shape).

## Context

D7 Content Pipeline closed 2026-05-23 (ADR-0088, all four phases
CLOSED, 5 agents alive). Per ADR-0067's rollout-order plan
(D4→D3→D8→D1→D2→D7→**D9**→D10→D5→D6), **D9 Learning Coach** is
next.

D9's value proposition (from
`config/domains/d9_learning_coach.yaml`):

> Backward design from operator goals. "I want to read papers on
> diffusion models comfortably in 3 months." curriculum_designer
> reverse-engineers prereqs from D1 Knowledge Forge state.
> Adaptive difficulty — assessor knows when operator plateaus and
> adjusts. Misconception ledger: every time operator misstates
> something, assessor records it; next session targets the gap.
> Multi-modal: practice problems / Socratic dialogue / spaced
> repetition / project-based. Mastery-gated progression —
> operator doesn't advance until assessor signs off. Reality
> Anchor cross-references operator's claimed understanding
> against ground truth.

Five new roles per the domain manifest:

| Role | Capability | Posture |
|---|---|---|
| `mentor` | coaching | GREEN (coaching-narrative; non-acting) |
| `curriculum_designer` | curriculum_design | GREEN (deterministic DAG composition) |
| `assessor` | knowledge_assessment | YELLOW (every score event operator-gated) |
| `socratic_partner` | socratic_dialogue | GREEN (dialogue-only; no assessments) |
| `spaced_repetition_pilot` | spaced_repetition | YELLOW (every review queue update operator-gated) |

## Decision

**Decision 1 — Five roles, no new genres.**

| Role | Genre | Trait emphasis | Side-effects ceiling |
|---|---|---|---|
| `mentor` | researcher | thoroughness + transparency + warmth | read_only (coaching brief to private memory) |
| `curriculum_designer` | researcher | thoroughness + evidence_demand + transparency | read_only (curriculum attestations to private memory) |
| `assessor` | guardian | evidence_demand + double_checking + caution | filesystem (misconception ledger writes; operator-gated) |
| `socratic_partner` | communicator | empathy + patience + transparency | read_only (dialogue + memory_write of session attestations) |
| `spaced_repetition_pilot` | actuator | caution + evidence_demand + transparency | filesystem (review queue updates; operator-gated; YELLOW) |

Same pattern as ADR-0086 / ADR-0087 / ADR-0088 — the fundamental
work of a Learning Coach decomposes into:

1. **Coaching** (mentor — researcher; narrative + encouragement +
   framing; never measures);
2. **Planning** (curriculum_designer — researcher; deterministic
   topic-prereq DAG from operator goal + catalog);
3. **Assessment** (assessor — guardian; quiz items + scoring +
   misconception logging; YELLOW posture);
4. **Dialogue** (socratic_partner — communicator; Socratic
   questioning sessions; never grades);
5. **Spaced repetition** (spaced_repetition_pilot — actuator;
   SM-2 review queue; composes with D2's schedule_reminder.v1;
   YELLOW posture).

**Decision 2 — Mastery is the assessor's exclusive lane.**

The mentor encourages + frames + corrects but NEVER signs off
mastery. The curriculum_designer plans the path but NEVER measures
progress along it. The socratic_partner asks questions but NEVER
grades the answers. The assessor (Phase B, YELLOW) is the only
role that can certify competence — and every certification event
is operator-gated by the YELLOW posture + per-call approval on
the misconception_log.v1 ledger write.

This is the same separation-of-duties discipline as D7's
writer / editor / style_steward / distribution_pilot split:
composition lives in one role, measurement in another, action in
a third — so audit trails always attribute correctly + so a
single hallucinated mastery claim can't leak through to "operator
moves on" without operator confirmation.

**Decision 3 — assessor + spaced_repetition_pilot default YELLOW.**

Both roles produce durable artifacts that drive operator behavior:
the misconception ledger feeds future sessions, the review queue
schedules operator study time. YELLOW posture ensures every
dispatch queues for operator approval until the operator
explicitly flips to GREEN after the proposal-quality bar is bedded
in. Same pattern as time_steward (ADR-0087), policy_enforcer
(ADR-0085), distribution_pilot (ADR-0088), knowledge_verifier
(ADR-0086).

`requires_human_approval=True` on misconception_log.v1 +
spaced_repetition_schedule.v1 makes the per-call gate the
load-bearing safety regardless of posture; posture is the
secondary discipline.

**Decision 4 — Cascade wiring: d1→d9 + d7→d9 ACTIVE; d9→d2 ACTIVE
in Phase D; d9→d10 + d9→d1 + d9→d7 declared INERT.**

Per the ADR-0086 + ADR-0087 + ADR-0088 INERT-cascade pattern:
D1's knowledge_contradiction_flag (gaps → curriculum) and D7's
editing (drafts → curriculum modules) are upstream of D9, so
their cascade rules to D9 land in this rollout's Phase D. D9's
spaced_repetition → D2's reminder + curriculum → D2's
task_prioritization are also ACTIVE in Phase D (D2 ships before
D9, so the receiving capabilities exist). The d9→d10
(research_lab not yet shipped), d9→d1 (assessment feedback path
into librarian — adjacent scope not yet built), and d9→d7
(certification → public drafts — adjacent scope) cascades are
declared INERT.

**Decision 5 — Five new builtin tools land across Phases A/B/D.**

- **Phase A (coaching foundation):** `curriculum_design.v1`
  (read_only) composes a topic-prereq DAG from goal + operator-
  curated catalog + operator-profile expertise; deterministic
  topological walk with stable tie-breaking.
- **Phase B (assessment + misconception ledger):**
  `knowledge_assessment.v1` (read_only) generates quiz items
  from curriculum slug + difficulty;
  `assessment_score.v1` (read_only) scores operator response
  via verify_claim + LLM rubric; `misconception_log.v1`
  (side_effects=filesystem, requires_human_approval=True)
  appends to data/d9/misconceptions.jsonl.
- **Phase C (Socratic dialogue):** no new builtin tools —
  reuses memory_recall/write, llm_think, text_summarize,
  operator_profile_read, personal_recall, delegate.
- **Phase D (spaced repetition + cascade + umbrella):**
  `spaced_repetition_schedule.v1` (side_effects=filesystem,
  requires_human_approval=True) — SM-2 interval computation,
  writes to data/d9/review_queue.jsonl, composes with D2's
  schedule_reminder.v1 for the actual fire-time delivery.

## Implementation tranches

**Phase A — coaching foundation.**
- mentor + curriculum_designer roles in trait_tree / genres /
  constitution_templates / tool_catalog
- curriculum_design.v1 builtin tool (deterministic DAG; ~20 tests)
- Skill manifests: coaching.v1 + curriculum_design.v1
- Birth scripts: birth-mentor.command + birth-curriculum-designer.command
- Runbook + ADR-0089 in Proposed status

**Phase B — assessment + misconception ledger.**
- assessor role (YELLOW)
- knowledge_assessment.v1 + assessment_score.v1 +
  misconception_log.v1 builtin tools
- Skill manifests: knowledge_assessment.v1 +
  misconception_tracking.v1
- Birth script: birth-assessor.command

**Phase C — Socratic dialogue.**
- socratic_partner role (GREEN)
- No new builtin tools — pure orchestration over existing kit
- Skill manifest: socratic_dialogue.v1
- Birth script: birth-socratic-partner.command

**Phase D — spaced repetition + cascade + umbrella.**
- spaced_repetition_pilot role (YELLOW)
- spaced_repetition_schedule.v1 builtin tool (composes with D2)
- Skill manifests: spaced_repetition.v1 + skill_certification.v1
- Birth script: birth-spaced-repetition-pilot.command
- Umbrella: birth-d9-learning-coach.command
- Cascade wiring: ACTIVATE d1→d9 + d7→d9 + d9→d2; declare
  INERT d9→d10 + d9→d1 + d9→d7
- ADR-0089 → Accepted; domain manifest status → live

Each phase = one commit + one push. The operator can verify
phase N before phase N+1 fires.

## Consequences

**Operator leverage.** D9 is the domain operators reach for when
they want to LEARN something. The pipeline's value shows up the
moment a learning goal moves from "I want to understand diffusion
models" to "here's a path, here's where you are, here's today's
focus, here's tomorrow's review queue" without manual planning.

**YELLOW posture friction.** assessor + spaced_repetition_pilot
both default YELLOW. The operator will see the queue more during
D9 bedding-in — documenting the YELLOW→GREEN promotion criteria
in the runbook is load-bearing for adoption.

**Misconception ledger as durable substrate.** Phase B's
misconception ledger is the load-bearing artifact for "adaptive
difficulty" — every time the operator misstates something, the
ledger captures it + the next session targets the gap. This is
the differentiator vs. "static study app" tools that don't learn
from operator state.

**Mastery-gated progression.** Domain manifest's load-bearing
discipline. Every layer enforces it: mentor policy
`forbid_progression_gating`, curriculum_designer policy
`forbid_assessment`, assessor's YELLOW posture +
`requires_human_approval=True` on misconception_log.v1. Three-layer
defense in depth, mirroring D7's three-layer NEVER-auto-publishes
discipline.

**Pacific time everywhere.** Per CLAUDE.md operator constraints,
all timestamps in D9 prompts + curriculum prose are Pacific time.
Operator profile's `timezone` field is the source of truth; the
LLM prompts in each skill manifest explicitly state the
constraint to prevent UTC drift.

**Reality Anchor for learning state.** Phase B's
assessment_score.v1 composes verify_claim.v1 (ADR-0063) to
cross-reference the operator's claimed understanding against
ground truth. This is the differentiator the domain manifest
calls out — no other "learning AI" verifies operator
UNDERSTANDING against operator-asserted facts.
