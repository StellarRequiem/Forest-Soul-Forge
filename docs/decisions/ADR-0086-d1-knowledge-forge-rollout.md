# ADR-0086 — D1 Personal Knowledge Forge: rollout

**Status:** Proposed (2026-05-23). Phase A CLOSED 2026-05-23;
phases B–D queued. ADR flips to **Accepted** when Phase D closes
and `config/domains/d1_knowledge_forge.yaml` status is updated
to `live`.
**Date:** 2026-05-23
**Tracks:** Domain Rollout / Knowledge Curation
**Supersedes:** none
**Builds on:** ADR-0036 (memory contradiction track — the
verifier_loop kernel substrate that single-agent contradiction
scanning reuses), ADR-0063 (Reality Anchor — operator-asserted
ground truth gates knowledge propagation), ADR-0067 (cross-domain
orchestrator — D1 is the next domain after D8 closes), ADR-0068
(operator profile — the operator's stated areas of focus inform
prospector + librarian scope), ADR-0076 (vector index for personal
context — D1's queryable substrate), ADR-0078 (D3 rollout
precedent for multi-role / no-new-genre pattern), ADR-0085 (D8
rollout precedent for the four-phase / one-commit-per-phase
delivery shape).

## Context

D8 Compliance Auditor closed 2026-05-22 (ADR-0085, all four
phases CLOSED, 5 agents alive). Per ADR-0067's rollout-order
plan and CLAUDE.md's Phase Letter Map, **D1 Personal Knowledge
Forge** is next.

D1's value proposition (from
`config/domains/d1_knowledge_forge.yaml`):

> Active personal knowledge curation. Not a passive note-store —
> a research → summarize → categorize → store loop with
> provenance per fact, daily delta reports, topic genealogy, and
> contradiction flagging via the verifier_loop. Compounds with D9
> Learning Coach and D10 Research Lab; feeds D7 Content Studio
> source material.

The substrate is already in place. Memory layers (private /
lineage / consented / realm — ADR-0008), the audit chain
(ADR-0049 signed), Reality Anchor (ADR-0063), the operator
profile (ADR-0068 stated areas of focus), and the personal
vector index (ADR-0076 hybrid retrieval) are all live. D1 builds
the ROLES that *operate* this substrate against an active
research/curation/synthesis/verification loop.

Four new roles per `config/domains/d1_knowledge_forge.yaml`
(verifier renamed to knowledge_verifier — see Decision 4):

| Role | Capability | Posture |
|---|---|---|
| `librarian` | knowledge_curation | GREEN (audit-emphasis curator) |
| `prospector` | research_gathering | GREEN (network kit — researcher) |
| `synthesizer` | knowledge_summarize | GREEN (read-only synthesis) |
| `knowledge_verifier` | knowledge_contradiction_flag | YELLOW (flags contradictions) |

## Decision

**Decision 1 — Four new roles, no new genres.**

| Role | Genre | Trait emphasis | Side-effects ceiling |
|---|---|---|---|
| `librarian` | guardian | audit + double_checking + transparency | read_only (curation = audit-emphasis catalog discipline) |
| `prospector` | researcher | research_thoroughness + thoroughness + lateral_thinking | network (allowlisted source fetches) |
| `synthesizer` | researcher | thoroughness + research_thoroughness + transparency | read_only (synthesis wraps; never mutates source) |
| `knowledge_verifier` | guardian | evidence_demand + double_checking + caution | read_only (flag-not-rewrite; YELLOW posture surfaces contradictions to operator) |

The roles slot into existing genres — no new genre needed
(precedent: ADR-0078 §Decision 1 added 6 roles across 3 genres;
ADR-0085 §Decision 1 added 5 roles across 3 genres). Personal
knowledge work is fundamentally:

1. **Catalog discipline** (librarian — guardian; audit-emphasis
   curator that owns the operator's knowledge graph + per-fact
   provenance ledger);
2. **Active gathering** (prospector — researcher; allowlisted
   network reach for source fetches; never persists without
   librarian-mediated catalog write);
3. **Synthesis** (synthesizer — researcher; topic genealogy +
   daily-delta synthesis from the catalog; read-only by
   construction);
4. **Verification** (knowledge_verifier — guardian; single-agent
   contradiction scan over the operator's own knowledge corpus;
   YELLOW posture forces operator review before flagged items
   propagate downstream to D9/D10/D7).

**Decision 2 — knowledge_verifier renamed (manifest "verifier").**

The manifest at `config/domains/d1_knowledge_forge.yaml` lists
the fourth role as `verifier`. That bare name collides with
two existing kernel roles:

- `verifier_loop` (ADR-0036) — singleton-per-forest memory
  contradiction auditor that operates over agent memory layers
  cross-cutting all domains.
- `reality_anchor` (ADR-0063) — singleton-per-forest pre-action
  ground-truth gate that verifies claims against operator-
  asserted facts.

A D1-scoped `verifier` would be confusing in three places:
catalog UI (which "verifier" is which?), constitution templates
(which policies bind?), and audit-chain entries (a
`role:verifier` tag without disambiguation can't distinguish
D1 / Y-track / kernel).

The rename to `knowledge_verifier` is mechanical: every config
file uses the new name; the manifest's `role: verifier` line
is updated when D1 goes live (Phase D close). The capability
name `knowledge_contradiction_flag` already encodes scope.

**Decision 3 — ADR-0036 Phase C scope narrowed to single-agent.**

The original ADR-0036 envisioned a verifier_loop that cross-
walks contradictions ACROSS agents (the lineage memory layer
is shared by design). That cross-agent scan has been deferred
to v0.4 per `config/domains/d1_knowledge_forge.yaml`'s
`depends_on_substrate` line `ADR-0036  # cross-agent
contradiction scan (deferred to v0.4)`.

For D1 MVP, the `knowledge_contradiction_scan.v1` builtin tool
that ships in Phase C is scoped to **single-agent** scans only —
it walks the calling agent's own private + lineage memory for
contradicting statements about a topic, flags them, never
rewrites. This matches the existing `memory_flag_contradiction.v1`
substrate (ADR-0036 T2) which is already single-agent.

The cross-agent path stays deferred: when v0.4 ships the
cross-agent verifier_loop wiring, `knowledge_contradiction_scan`
will gain an optional `scope: cross_agent` parameter that gates
on operator consent (the audit-chain trail then crosses tier
boundaries, which is a different governance surface than D1
ships in v0.3).

**Decision 4 — Connector posture: graceful degradation.**

D1's manifest declares three connector dependencies:
`forest-files` (local note files), `forest-notes`
(Obsidian / Apple Notes), `forest-browser-history` (source
provenance). None of these plugins ship in v0.3 — they're
operator-installable via the ADR-0043 MCP plugin loader at
runtime.

The D1 roles operate with **graceful degradation**: when a
connector isn't installed, the role falls back to:

- `forest-files` absent → librarian + prospector only see notes
  written via the agent's own `memory_write.v1` (private +
  lineage memory layers — the existing kernel substrate).
- `forest-notes` absent → no Obsidian/Apple-Notes ingest; the
  catalog only sees memory-layer entries.
- `forest-browser-history` absent → provenance for a fact is
  capped at the source URL the prospector fetched it from
  (web_fetch.v1's response metadata); no "I read this last
  Tuesday at 3pm" context.

The roles' constitutions REQUIRE provenance per fact regardless;
the falls-back-to-memory path produces less-rich provenance, not
no provenance. Skill manifests probe for connector availability
at run time + select the path; the codepath never assumes a
connector is present.

**Decision 5 — Four-phase delivery, one commit per phase.**

Each phase = one commit + one push; phases proceed sequentially
because each builds on the prior. Same shape as ADR-0078 (D3)
and ADR-0085 (D8).

- **Phase A — intake foundation.** `librarian` + `prospector`
  roles in trait_tree / genres / constitution_templates /
  tool_catalog. Skill manifests `knowledge_curation.v1` +
  `research_gathering.v1`. Birth scripts. Runbook Phase A
  section. No new builtin tools — reuse `web_fetch`,
  `memory_write/recall`, `code_read`, `personal_recall`,
  `llm_think`, `text_summarize`.

- **Phase B — synthesis.** `synthesizer` role + new builtin tool
  `topic_genealogy_build.v1` (read-only; walks memory entries
  tagged `topic:<slug>` + their provenance to construct a topic
  graph: nodes are claims/facts, edges are "supports" /
  "refines" / "contradicts" derived from tag relationships and
  the audit chain's temporal ordering). Skill manifests
  `knowledge_summarize.v1` + `topic_genealogy.v1`. ~20 unit
  tests.

- **Phase C — verification.** `knowledge_verifier` role + new
  builtin tool `knowledge_contradiction_scan.v1` (single-agent
  scope per Decision 3; walks the agent's private + lineage
  memory for statements contradicting a target topic; flags via
  the existing `memory_flag_contradiction.v1` substrate; never
  rewrites). Skill manifest `knowledge_contradiction_flag.v1`.
  ~20 unit tests. YELLOW posture per Decision 1.

- **Phase D — delta + cascade + umbrella.** New builtin tool
  `daily_knowledge_delta.v1` (read-only synthesis; walks the
  audit chain for one operator-named window + buckets memory
  writes / contradiction flags / topic_genealogy_built events
  by topic + emits a "what changed in your knowledge today"
  report). Skill manifest `daily_knowledge_delta.v1`. Cascade
  entries in `config/handoffs.yaml`: d8→d1 active (regulation
  updates flow into the librarian's catalog), d1→d9/d10/d7/d2
  declared INERT (cascades wired but defaulted off — the
  ADR-0067 spec mentioned them as natural compounding paths,
  but each downstream domain is upstream of D1 in the rollout
  order so the receiving roles don't exist yet). Umbrella
  birth script `birth-d1-knowledge-forge.command`. Runbook
  final section. Diagnostic harness section-09 D1 extensions.
  ~13 unit tests.

## Consequences

**Positive.**

1. The operator gets an active knowledge-curation loop, not a
   passive note store. The librarian owns catalog discipline +
   provenance; the prospector reaches out for sources; the
   synthesizer builds topic genealogies + daily deltas; the
   knowledge_verifier flags contradictions for operator review.
2. The cascade wiring d8→d1 (regulation updates flow into the
   librarian's catalog) closes a load-bearing compliance gap —
   the operator no longer has to manually relay framework
   updates into personal context.
3. Reality Anchor (ADR-0063) integration is load-bearing: the
   knowledge_verifier's flags get cross-checked against
   operator-asserted ground truth before they propagate to the
   downstream domains. False contradictions don't poison the
   graph.
4. D1's vector-index integration (ADR-0076) makes
   "what have I learned about X?" a single `personal_recall.v1`
   call instead of a multi-step memory walk.

**Negative.**

1. Four new roles per ADR-0040 trust-surface count: each role
   gets its own constitution template + tool catalog block.
   That's four new governance surfaces the operator has to
   understand.
2. Cross-agent contradiction scanning is deferred (Decision 3).
   Single-agent scans flag less; the false-negative rate is
   higher than the v0.4 path will deliver. Mitigation: the
   knowledge_verifier's YELLOW posture forces operator review,
   so the operator catches the false negatives the single-agent
   scan misses.
3. Graceful-degradation paths (Decision 4) mean the prospector
   + librarian work without the operator's notes-app + browser-
   history. The MVP behavior IS the fallback behavior until
   the operator installs the connectors. Documented in the
   runbook.

**Phase status footer.**

- **Phase A** — intake foundation. Status: CLOSED (2026-05-23).
  Ships: `librarian` + `prospector` roles in trait_tree /
  genres / constitution_templates / tool_catalog; birth scripts
  `birth-librarian.command` + `birth-prospector.command`; skill
  manifests `examples/skills/knowledge_curation.v1.yaml` +
  `examples/skills/research_gathering.v1.yaml`; operator runbook
  `docs/runbooks/d1-knowledge-forge-ops.md`. No new builtin
  tools.
- **Phase B** — synthesis. Status: CLOSED (2026-05-23). Shipped:
  `synthesizer` role in trait_tree / genres / constitution_templates /
  tool_catalog; new builtin tool `topic_genealogy_build.v1` with
  28 unit tests; skill manifests
  `examples/skills/knowledge_summarize.v1.yaml` +
  `examples/skills/topic_genealogy.v1.yaml`; birth script
  `dev-tools/birth-synthesizer.command`.
- **Phase C** — verification. Status: pending. Ships:
  `knowledge_verifier` role (YELLOW posture); new builtin tool
  `knowledge_contradiction_scan.v1`; skill manifest
  `examples/skills/knowledge_contradiction_flag.v1.yaml`; birth
  script `dev-tools/birth-knowledge-verifier.command`.
- **Phase D** — delta + cascade + umbrella. Status: pending.
  Ships: new builtin tool `daily_knowledge_delta.v1`; skill
  manifest `examples/skills/daily_knowledge_delta.v1.yaml`;
  cascade entries in handoffs.yaml; umbrella birth script
  `dev-tools/birth-d1-knowledge-forge.command`; runbook final
  section; diagnostic harness section-09 D1 extensions;
  `d1_knowledge_forge.yaml` status flipped to `live`.

ADR flips to **Accepted** when Phase D closes and
`d1_knowledge_forge.yaml` status is updated to `live`.
