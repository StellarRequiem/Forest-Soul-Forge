# ADR-0020 — Agent Character Sheet

- **Status:** Proposed
- **Date:** 2026-04-25
- **Supersedes:** —
- **Related:** ADR-0001 (trait tree), ADR-0002 (DNA + lineage), ADR-0004 (constitution builder), ADR-0005 (audit chain), ADR-0006 (registry as index over artifacts), ADR-0007 (FastAPI daemon), ADR-0017 (LLM-enriched soul.md narrative), ADR-0018 (tool catalog). Adjacent in spirit to (and to be implemented in concert with): ADR-0021 (role genres), ADR-0022 (memory subsystem), ADR-0023 (benchmark suite).

## Context

After ADR-0018, an agent has identity (soul.md), a rulebook (constitution.yaml), a voice (Voice section + narrative_*), and a tool surface with per-tool constraints (constitution.yaml `tools:`). Each lives in its proper file under the artifact tree. That layout is correct for storage — files-on-disk is the source of truth per ADR-0006 — but it is wrong for **inspection**.

When an operator asks *"what is this agent"*, today the answer requires reading three files (soul.md, constitution.yaml, audit chain entries), cross-referencing the registry, and mentally composing what they describe together. There's no single artifact that says: this agent's traits are X, its voice sounds like Y, it carries this kit with these constraints, it's been measured to handle Z workload at this latency, it has consumed N% of its allocated memory budget, its lineage descends from these ancestors.

The trait engine, the constitution, the tool catalog, and the (future) memory and benchmark subsystems all describe one piece of an agent. The **character sheet** is the join — the descriptor an operator reaches for when deciding *whether to deploy* this agent, *how to configure* its successor, or *what went wrong* after it misbehaved.

The TTRPG framing is deliberate. A D&D character sheet doesn't store the character's lore in one place and its stats in another and its inventory in a third — it composes them onto a single page so the player can play. Forest agents are characters operators play; the operator's "page" is what we're designing here.

The character sheet is **NOT a new canonical artifact**. ADR-0006 is clear: files-on-disk are authoritative; the registry is a derived index. Adding a third canonical artifact would create a synchronization burden (which file wins when they disagree?) and break the rebuild-from-artifacts contract. Instead, the character sheet is a **derived view** — composed on demand from the canonical artifacts plus measured stats stored in the audit chain and registry.

The view exists at three layers:
1. **A daemon endpoint** — `GET /agents/{instance_id}/character-sheet` — emits structured JSON.
2. **A frontend rendering** — turns the JSON into a readable page (or printable sheet).
3. **A markdown export** — the same data laid out as a single-page markdown for git-able snapshots, audit packets, and human review.

Stats and benchmarks, which don't yet exist (ADR-0022, ADR-0023), are first-class sections of the sheet. They appear empty / "not yet measured" today and fill in as those subsystems land. The character sheet's schema makes room for them now so consumers don't have to be rewritten when the data shows up.

Three shapes for delivering the character sheet were considered:

1. **Single huge YAML file per agent**, regenerated whenever any source artifact changes. **Rejected** — duplicates information from soul.md and constitution.yaml, creates a sync problem, and ADR-0006 is explicit that derived data is the registry's job, not a file's.

2. **Frontend-only assembly** — daemon emits the existing artifacts, frontend stitches them into a sheet view. **Rejected** — every consumer of the descriptor (frontend, CLI tools, future bots, audit reviewers) would need to re-implement the assembly. A daemon-side endpoint owns the join once.

3. **Daemon-side derived JSON view, plus rendering layers** (frontend page + markdown export). **Selected.** The join logic lives in one place; renderers consume the same JSON; the descriptor model evolves in one schema version, not three.

## Decision

### A new endpoint: `GET /agents/{instance_id}/character-sheet`

Read-only, no auth gate beyond what `/agents/{id}` already requires. Returns a structured JSON document per the schema below. ETag-able (the join is deterministic given the source artifacts + measurement timestamps).

When the agent doesn't exist → 404. When source artifacts have drifted from the registry (soul file missing, etc.) → 409 with detail naming the offender, matching the existing `/agents/{id}/regenerate-voice` failure shape.

### Schema sections (`schema_version: 1`)

The descriptor is laid out in eight sections. Sections that don't yet have data show their fields with `null` values, not omitted — consumers can rely on shape. Agents birthed before a section's underlying subsystem ships have the section flagged `not_yet_measured: true`.

```json
{
  "schema_version": 1,
  "rendered_at": "2026-04-25T11:30:00Z",
  "identity": { ... },
  "personality": { ... },
  "loadout": { ... },
  "capabilities": { ... },
  "stats": { ... },
  "memory": { ... },
  "benchmarks": { ... },
  "provenance": { ... }
}
```

#### `identity` — who this agent is

Source: registry agent row + soul.md frontmatter + lineage table.

Fields:
- `instance_id`, `dna`, `dna_full`, `sibling_index`
- `agent_name`, `agent_version`
- `role` (string), `genre` (string, populated after ADR-0021 lands; null today)
- `parent_instance` (nullable), `lineage` (root-first list of ancestor DNAs), `lineage_depth`
- `created_at`, `status` (active | archived)
- `owner_id` (nullable)

#### `personality` — traits + voice

Source: soul.md frontmatter (trait_values) + soul.md `## Voice` section + trait engine for band labels.

Fields:
- `traits.values: dict[str, int]` — raw 0..100 numbers
- `traits.bands: dict[str, str]` — qualitative band per trait (very low / low / moderate / fairly high / very high)
- `traits.dominant_domain: str` (per ADR-0003 grading)
- `traits.warnings: list[str]` — flagged combinations
- `voice.markdown: str | null` — the Voice section body, or null if `enrich_narrative` was false
- `voice.provider: str` — "local" | "frontier" | "template"
- `voice.model: str`
- `voice.generated_at: str`

#### `loadout` — what tools the agent has

Source: constitution.yaml `tools:` section (resolved kit + per-tool constraints from ADR-0018 T2.5).

Fields:
- `kit: list[ToolView]`, where each ToolView is:
  - `name: str`, `version: str`, `side_effects: str`
  - `description: str` (pulled from the catalog, not a re-derivation)
  - `constraints: dict` — max_calls_per_session, requires_human_approval, audit_every_call, plus future fields
  - `applied_rules: list[str]` — names of constraint policy rules that matched
- `tool_catalog_version: str`
- `kit_summary: str` — one-line human-readable summary (e.g., "5 tools, 2 require approval, 0 external")

#### `capabilities` — what kind of agent this IS

Source: derived from `role` (today) and `genre` (ADR-0021).

Fields (skeletal until ADR-0021):
- `role: str`
- `role_description: str` — pulled from trait engine's role definition
- `genre: str | null` — populated post-ADR-0021
- `genre_description: str | null`
- `inherited_default_kit: list[ToolRef]` — what the archetype's standard kit was at this agent's catalog version, for comparison against the actual loadout above (lets an operator see at a glance what was added/removed)

#### `stats` — measured runtime characteristics

Source: registry `agent_stats` table (ADR-0022 / ADR-0023 will define this), with audit chain `stat_measured` events as the historical record.

Fields (populated as measurements land):
- `avg_latency_ms: float | null` — per-task-kind breakdown when available
- `success_rate: float | null` — fraction of completed sessions that didn't end in escalation
- `memory_burn_avg_mb: float | null`
- `tools_invoked_count: int` — per-tool counts available via drill-down
- `last_active_at: str | null`
- `not_yet_measured: bool` — true today; flips false when the first stat lands

This section is **the most forward-looking** part of the character sheet. The shape exists now so v1 of the endpoint can return a stable JSON; the data fills in as ADR-0022 and ADR-0023 implement.

#### `memory` — what the agent retains

Source: ADR-0022 memory subsystem (future).

Fields (skeletal):
- `working_capacity_tokens: int | null`
- `episodic_capacity_entries: int | null`
- `consolidated_capacity_entries: int | null`
- `current_working_usage_pct: float | null`
- `last_consolidation_at: str | null`
- `not_yet_implemented: bool` — true today

#### `benchmarks` — how well the agent does its job

Source: ADR-0023 benchmark suite (future). Per-archetype battery; results stored as audit chain events.

Fields (skeletal):
- `battery: list[BenchmarkResult]` — per benchmark in the archetype's battery, the last result
- `each result: { name, score, baseline, run_at, model_backend }`
- `not_yet_implemented: bool` — true today

#### `provenance` — the audit hooks

Source: registry + audit chain.

Fields:
- `constitution_hash: str` — full SHA-256
- `tool_catalog_version: str`
- `narrative_provider, narrative_model, narrative_generated_at` (or null)
- `last_audit_seq: int` — most recent chain sequence touching this instance
- `last_audit_entry_hash: str` — entry hash, lets an external verifier cross-check chain integrity for this agent specifically
- `soul_path, constitution_path: str` — relative or absolute paths to the canonical artifacts on disk

### Rendering layers

The frontend gains a **Character Sheet view** under the Agents tab — click an agent, see their sheet rendered. Layout follows the section ordering above; collapsible sections so operators can scan or drill in.

A **markdown export** endpoint — `GET /agents/{instance_id}/character-sheet?format=md` — returns the same data as a single-page markdown document. Useful for:
- Pasting into incident postmortems ("here's what the agent looked like at the time")
- `git diff` between two agents (markdown diffs cleanly)
- Audit packets that need a human-readable snapshot

The markdown export is byte-deterministic given the same source artifacts + the same measurement timestamps, so a `re-export` shows up as zero-diff in version control.

### What this ADR does NOT decide

- **No new canonical artifact.** Adding `character_sheet.yaml` alongside soul.md and constitution.yaml is rejected. The sheet is a derived view; it doesn't get its own hash, its own ingest path, or its own registry table.
- **No measurement subsystem yet.** The `stats`, `memory`, and `benchmarks` sections render `null`/`not_yet_*` until ADR-0022 and ADR-0023 implement. The endpoint and the schema are the minimum viable scaffold.
- **No editing API.** Character sheets are read-only. Editing happens at the source layer — change traits → birth a new agent; change tools → birth/spawn with overrides; change voice → `regenerate-voice`; change memory/stats happens through the future runtime.
- **No PDF rendering.** Markdown export is the v1 portable format. PDF can be a downstream step (printing the markdown via pandoc); not a daemon concern.

## Consequences

**Upside:**

- **One place to look.** Operators no longer mentally compose three files into an agent's identity. The descriptor is one endpoint, one rendering, one schema.
- **Forward-compatible by design.** Sections for measurements, memory, and benchmarks exist with `null`/flags today; the data fills in as those subsystems land without consumer rewrites.
- **Aligns with the TTRPG framing the project's been heading toward.** Character creation IS what the Forge does; the character sheet is what the operator gets back. The artifact + descriptor model maps cleanly onto how players think about characters.
- **Audit-friendly.** The provenance section gives external verifiers exactly the hooks they need (constitution_hash, last audit entry hash, paths to canonical artifacts) to spot-check the sheet against the artifact tree without parsing the join logic.
- **Gateway to the rest of the vision.** ADR-0021's genre, ADR-0022's memory, and ADR-0023's benchmarks all become coherent fields on a known descriptor rather than disconnected concepts.

**Downside:**

- **A new endpoint to maintain.** Every section needs a source-of-truth lookup; if the source layer changes shape, the endpoint changes too. We accept this — the join logic existing somewhere is unavoidable, and centralizing it in the daemon beats spreading it across consumers.
- **Stale data risk.** A character sheet rendered at T1 becomes wrong at T2 if the agent's stats shift. Mitigation: the `rendered_at` timestamp is included so consumers can decide whether the snapshot is fresh enough; ETag-ing avoids serving stale bytes when nothing changed.
- **Schema evolution discipline.** Adding fields is additive (consumers tolerate unknown fields); removing or renaming requires a `schema_version` bump and a coordinated consumer update. Keep removals rare; prefer deprecation + parallel field for a release.
- **Rendering effort.** A nice-looking character sheet UI is real frontend work — collapsible sections, color-coded constraints, etc. The JSON endpoint is small; the human-facing view earns its budget separately.

**Out of scope for this ADR (deferred):**

- Real-time character sheet (live-updating as stats change). v1 is request/response. Consider streaming if the operator UX demands it post-ADR-0023.
- Diffing two sheets directly. Markdown export + standard diff tools cover the operator's case; a structured-diff endpoint is overkill until proven needed.
- Multi-agent comparison view ("show me three log_analysts side-by-side"). Frontend can compose this from three individual fetches; no daemon-side change required.
- Sheet templating per genre. Genres might want different section emphasis (a Companion's character sheet might lead with personality and voice; a Guardian's leads with capabilities and stats). Worth revisiting in ADR-0021. v1 ships one layout.

## Open questions

1. **Should `last_audit_entry_hash` be the entry hash of the `agent_created` event, or of the most recent event touching this instance?** Most recent gives a "freshness anchor" for verification; created-event gives a stable identity hook. Lean **most recent**, since the chain is append-only and the freshness anchor is more useful in practice. The created-event hash is recoverable via the chain anyway.

2. **Where does the markdown export's "single-page" target sit?** A literal one-printed-page constraint is too tight for an agent with a large kit. Lean **section-collapsible markdown**, where each section emits a `<details>` block. Renders inline when expanded; folds when not.

3. **Should the JSON response embed the trait engine's full role description, or just the role name?** Embed the description — it's static, the bytes are small (<500 chars typical), and it saves consumers from a separate `/traits/role/{name}` lookup. If the engine ever serves descriptions dynamically, revisit.

4. **Versioning interaction with `/agents/{id}/regenerate-voice`.** If an agent's voice is regenerated, the character sheet's `personality.voice` section reflects the new content on the next render. Should the sheet expose a "voice was regenerated at T" indicator? Probably yes — it's a meaningful provenance signal. Add `voice.regenerated_at: str | null` to the personality section.

5. **What about archived agents?** Do they get a character sheet? **Yes** — the descriptor is for inspection, and archived agents are the most useful inspection target (postmortems). The `identity.status` field carries the state.

## Implementation tranches (for when this exits Proposed)

- **T1** — daemon endpoint + JSON schema. Minimum viable: identity + personality + loadout + capabilities + provenance sections. Stats / memory / benchmarks sections render `not_yet_*: true`.
- **T2** — markdown export at `?format=md` with collapsible sections.
- **T3** — frontend Character Sheet view. Reads the JSON, renders the sections, colors the constraints (green for read-only, amber for network, red for filesystem/external).
- **T4** — wire post-ADR-0021 genre fields once that ADR ships.
- **T5** — wire post-ADR-0022 memory fields when memory data is available.
- **T6** — wire post-ADR-0023 benchmarks fields when battery results land.

T1+T2+T3 is the "sheet exists" milestone. T4-T6 fill in the empty sections as the underlying subsystems implement.
