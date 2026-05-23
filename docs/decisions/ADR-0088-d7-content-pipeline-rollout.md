# ADR-0088 — D7 Content Pipeline: rollout

**Status:** Accepted (2026-05-23). All four phases CLOSED —
D7 Content Pipeline LIVE with all 5 agents alive (Writer-D7,
ContentResearcher-D7, StyleSteward-D7, Editor-D7,
DistributionPilot-D7 YELLOW); 4 new builtin tools
(voice_profile_build.v1, voice_match_check.v1, format_adapt.v1,
publish_schedule.v1) with 101 unit tests total; 7 skill
manifests; cascade wiring d1→d7 knowledge_curation +
d2→d7 daily_reflection ACTIVE; umbrella + runbook live.
**Date:** 2026-05-23
**Tracks:** Domain Rollout / Operator Content Pipeline
**Supersedes:** none
**Builds on:** ADR-0063 (Reality Anchor — verify_claim.v1 gates
editor fact-checking), ADR-0067 (cross-domain orchestrator — D7
is next after D2 closes), ADR-0068 (operator profile — voice
samples + areas_of_focus inform every D7 decision), ADR-0076
(vector index for personal context — voice profile build +
match), ADR-0085 (D8 rollout precedent), ADR-0086 (D1 rollout
precedent), ADR-0087 (D2 rollout precedent — same four-phase /
one-commit-per-phase shape).

## Context

D2 Daily Life OS closed 2026-05-23 (ADR-0087, all four phases
CLOSED, 5 agents alive). Per ADR-0067's rollout-order plan
(D4→D3→D8→D1→D2→**D7**→D9→D10→D5→D6), **D7 Content Pipeline**
is next.

D7's value proposition (from
`config/domains/d7_content_studio.yaml`):

> End-to-end content pipeline: idea → researched → drafted →
> edited → fact-checked → ready-to-publish. NEVER auto-publishes
> — distribution is always operator-gated. Style steward learns
> operator voice from their archive (with consent), maintains a
> voice profile, flags drift. Reality Anchor verifies every
> factual assertion in a draft before publish-ready. Multi-format
> pipeline: one idea → blog draft + Twitter thread + newsletter +
> LinkedIn post, each its own pass.

Five new roles per the domain manifest:

| Role | Capability | Posture |
|---|---|---|
| `writer` | draft_writing | GREEN (drafts-to-private-memory) |
| `content_researcher` | content_research | GREEN (allowlisted network) |
| `editor` | editing | GREEN (read-only review + format adaptation) |
| `style_steward` | voice_matching | GREEN (read-only voice profile + match) |
| `distribution_pilot` | scheduled_publishing | YELLOW (every queue gated) |

## Decision

**Decision 1 — Five roles, no new genres.**

| Role | Genre | Trait emphasis | Side-effects ceiling |
|---|---|---|---|
| `writer` | researcher | thoroughness + transparency + lateral_thinking | read_only (drafts-to-private-memory) |
| `content_researcher` | researcher | research_thoroughness + evidence_demand + transparency | network (operator-allowlisted source pulls) |
| `editor` | guardian | evidence_demand + double_checking + caution | read_only (review + format adaptation; never publishes) |
| `style_steward` | guardian | thoroughness + double_checking + transparency | read_only (voice profile build + match) |
| `distribution_pilot` | actuator | caution + evidence_demand + transparency | external (publish queue; YELLOW posture; every dispatch operator-gated) |

Same pattern as ADR-0085 / ADR-0086 / ADR-0087 — the
fundamental work of a Content Pipeline decomposes into:

1. **Sourcing** (content_researcher — researcher; pulls source
   material from allowlisted external sources + lineage memory);
2. **Composition** (writer — researcher; long-form drafting
   from briefs + outlines + voice samples);
3. **Voice discipline** (style_steward — guardian; learns
   operator voice; flags drift in drafts);
4. **Editing** (editor — guardian; fact-check via verify_claim,
   voice-match via voice_match_check, format adapt via format_adapt);
5. **Distribution** (distribution_pilot — actuator; queues
   publishes; YELLOW posture forces operator review).

**Decision 2 — Rename the manifest's bare `researcher` to
`content_researcher`.**

The domain manifest at `config/domains/d7_content_studio.yaml`
lists an entry agent named `researcher`. That name collides
with the *researcher genre* in `config/genres.yaml`. The
trait-engine + genre-loader can technically coexist with the
collision (genres are container; roles are claimed under them)
but it's operationally confusing — every diagnostic that prints
"role X in genre Y" reads "researcher in researcher" which
helps no one.

Rename the role to `content_researcher`. Same disambiguation
pattern as D1's manifest `verifier` → `knowledge_verifier`
(ADR-0086 Decision 2) which avoided a similar collision with
`verifier_loop` + `reality_anchor`.

**Decision 3 — distribution_pilot defaults YELLOW.**

distribution_pilot is the only actuator-tier role in D7. Publish
actions are external + irreversible from the operator's
perspective (a published post can't be silently un-published).
YELLOW posture ensures every dispatch queues for operator
approval until the operator explicitly flips to GREEN after the
proposal-quality bar is bedded in — same pattern as time_steward
(ADR-0087), policy_enforcer (ADR-0085), knowledge_verifier
(ADR-0086).

`requires_human_approval=True` on `publish_schedule.v1` makes
the per-call gate the load-bearing safety regardless of posture;
posture is the secondary discipline (auto-pause every other
non-read-only dispatch the role can fire).

**Decision 4 — Cascade wiring: d1→d7 + d2→d7 ACTIVE; d4→d7 +
d7→d9 declared INERT.**

Per the ADR-0086 + ADR-0087 INERT-cascade pattern: D1's
knowledge_curation and D2's daily_reflection are upstream of
D7 in the rollout order, so their cascade rules to D7 land in
this rollout's Phase D. D4's release-notes path to D7 is
declared but inert (not in v0.5 scope); the d7 → d9 path
(D9 Learning Coach not yet shipped) is also declared inert.

**Decision 5 — Three new builtin tools land across Phases B/C/D.**

- **Phase B (voice profiling):** `voice_profile_build.v1`
  (read_only) derives a voice profile from operator writing
  samples; `voice_match_check.v1` (read_only) scores a draft
  against the profile + flags drift with span pointers.
- **Phase C (editing + adaptation):** `format_adapt.v1`
  (read_only) adapts one primary draft to a target format
  (twitter_thread, linkedin_post, newsletter, blog).
- **Phase D (distribution):** `publish_schedule.v1`
  (side_effects=external, requires_human_approval=True) queues
  a publish to `data/d7/publish_queue.jsonl` for a future
  forest-publish connector to pick up. The fire is unattended
  by design — the creation is operator-reviewed.

## Implementation tranches

**Phase A — drafting foundation.**
- writer + content_researcher roles in trait_tree / genres /
  constitution_templates / tool_catalog
- No new builtin tools — reuse existing (web_fetch, memory_*,
  llm_think, audit_chain_verify, text_summarize, operator_profile_read,
  personal_recall, delegate)
- Skill manifests: draft_writing.v1 + content_research.v1
- Birth scripts: birth-writer.command + birth-content-researcher.command
- Runbook + ADR-0088 in Proposed status

**Phase B — voice profiling.**
- style_steward role (GREEN)
- voice_profile_build.v1 + voice_match_check.v1 builtin tools
- Skill manifests: voice_profile_build.v1 + voice_matching.v1
- Birth script: birth-style-steward.command

**Phase C — editing + format adaptation.**
- editor role (GREEN)
- format_adapt.v1 builtin tool
- Skill manifests: editing.v1 + format_adaptation.v1
- Birth script: birth-editor.command

**Phase D — distribution + cascade + umbrella.**
- distribution_pilot role (YELLOW)
- publish_schedule.v1 builtin tool
- Skill manifests: scheduled_publishing.v1 + performance_tracking.v1
- Birth script: birth-distribution-pilot.command
- Umbrella: birth-d7-content-studio.command
- Cascade wiring: ACTIVATE d1→d7 (knowledge_curation → content_drafting),
  d2→d7 (daily_reflection → content_seed); declare INERT d4→d7,
  d7→d9
- ADR-0088 → Accepted; domain manifest status → live

Each phase = one commit + one push. The operator can verify
phase N before phase N+1 fires.

## Consequences

**Operator leverage.** D7 is the domain operators reach for
when they want to PUBLISH something. The pipeline's value
shows up the moment a draft moves from "research scattered
across notes" to "draft + voice-checked + fact-checked +
format-adapted" without manual handoffs.

**YELLOW posture friction.** distribution_pilot's YELLOW default
means every publish queues. The operator will see the queue more
during D7 bedding-in — documenting the YELLOW→GREEN promotion
criteria in the runbook is load-bearing for adoption.

**Voice profile as durable substrate.** Phase B's voice profile
is built once from operator samples and reused by every
downstream skill (writer composition, editor voice-match check).
Updating samples invalidates the profile and forces a rebuild;
the audit chain captures the rebuild event so drift is
attributable.

**NEVER auto-publishes.** Domain manifest's load-bearing
discipline. Every layer enforces it: writer policy
`forbid_publish`, editor genre's read_only ceiling,
distribution_pilot's YELLOW posture + `requires_human_approval=True`
on publish_schedule.v1. Three-layer defense in depth.

**Pacific time everywhere.** Per CLAUDE.md operator constraints,
all timestamps in D7 prompts + draft prose are Pacific time.
Operator profile's `timezone` field is the source of truth; the
LLM prompts in each skill manifest explicitly state the
constraint to prevent UTC drift.
