# ADR-0021 — Role genres and agent taxonomy

- **Status:** Accepted (promoted 2026-04-30 — Phase A audit §A-2; see docs/audits/2026-04-30-comprehensive-repo-audit.md). Role genres — T1–T8 implemented; 3 web genres added via ADR-003X.
- **Date:** 2026-04-25
- **Supersedes:** —
- **Related:** ADR-0001 (trait tree), ADR-0002 (DNA + lineage), ADR-0004 (constitution builder), ADR-0008 (local-first model provider — Phase 5 is therapy/accessibility, which this ADR plans for), ADR-0017 (LLM-enriched soul.md narrative), ADR-0018 (tool catalog), ADR-0020 (agent character sheet — the `capabilities.genre` field this ADR populates).

## Context

The Forge today has three roles: `network_watcher`, `log_analyst`, `anomaly_investigator`. Three is enough to ship a coherent product, not enough to capture the design space. The mission of the system — "agents you can spawn for any reasonable job, with the safety properties that match the job" — implies a much larger role catalog. Network watchers are observation-class; therapists are companion-class; ticket creators are actuator-class. Treating those as a flat list produces three problems as the catalog grows:

1. **Default kit shapes get repeated.** Every observation-class role has a standard kit centered on `packet_query` / `log_grep` / `timestamp_window` style tools. ADR-0018 already showed this — the network_watcher and log_analyst standard kits overlap by 50% on `timestamp_window` and structurally rhyme. Without a hierarchy, every new role re-declares the overlap.

2. **Risk profiles get repeated.** A Companion-class role (therapist, accessibility_runtime) inherits a different default risk floor than an Actuator-class role (ticket_creator, deploy_runner). Codifying that floor at the role level means every new role has to re-state it; codifying at the genre level means a new role inherits the floor for free.

3. **The operator's mental model has no scaffold.** When an operator wants a "kind of agent that watches things and reports," they want to pick from a short list of *families*, not a long list of role names. The genre is the orientation step before role-level specifics.

Three shapes for adding hierarchy were considered:

1. **Tag-based (each role has a list of tags like `observation`, `passive`, `low_risk`).** Flexible but unstructured — two roles tagged `observation` might still differ in load profile. Cross-tag conflicts get painful (which tag wins when a role has both `read_only` and `network`?). **Rejected** as too loose for risk decisions.

2. **Single-parent genre (each role belongs to exactly one genre, genres are flat).** Clean ownership, easy lookup, simple to render in a UI. The genre carries the risk profile, the default kit pattern, and the trait emphasis. Roles override only what they need to override. **Selected.**

3. **Multi-level taxonomy (genre → subgenre → role, like trait_tree's domain → subdomain → trait).** Mirrors the trait engine's depth, which is appealing for symmetry — but the additional level pays for itself only when subgenres carry distinct risk profiles, which we don't yet have. **Deferred** — keep the door open by making the genre layer extensible to two-level if the catalog grows past ~15 roles.

## Decision

### Seven genres for v1

```
Observer       — passive watching, read-only orientation. Reports findings;
                  doesn't act on them. Examples: network_watcher, log_analyst,
                  signal_listener, dashboard_watcher.

Investigator   — active correlation across observation surfaces. Mostly
                  read-only with controlled network reach (lookups against
                  baselines, threat intel). Examples: anomaly_investigator,
                  incident_correlator, threat_hunter.

Communicator   — wraps other agents' findings into messages, briefs, summaries,
                  translations. Sends output to humans or other systems —
                  human-approval-gated by default. Examples: notifier, briefer,
                  status_reporter, translator.

Actuator       — performs external actions: creates tickets, deploys
                  configurations, sends commands. The most heavily gated
                  genre — every non-read-only action requires human approval
                  regardless of trait values. Examples: ticket_creator,
                  deploy_runner, alert_dispatcher.

Guardian       — safety check, second opinion, refusal arbiter, content
                  review. Reads other agents' output and either blesses or
                  blocks. Strict read-only kit; high evidence_demand,
                  thoroughness, and caution by trait emphasis. Examples:
                  safety_check, content_review, refusal_arbiter.

Researcher     — literature scan, data synthesis, knowledge consolidation.
                  Reads broadly (catalog browse, web fetch with allowlist),
                  emits structured summaries. Long-running by nature; moderate
                  network reach. Examples: paper_summarizer, vendor_research,
                  knowledge_consolidator.

Companion      — therapy-adjacent, accessibility runtime, interactive
                  presence. The Phase 5 path from ADR-0008. Distinct from
                  every other genre by audio/video runtime, persistent memory,
                  emotional-affect-aware policies. Highest privacy floor —
                  local-only providers required for any session involving
                  user state. Examples: therapist, accessibility_runtime,
                  day_companion, learning_partner.
```

Each genre has six descriptive properties:

- **`description`** — operator-facing prose. What this kind of agent IS.
- **`risk_profile`** — the maximum `side_effects` tier the genre's standard kit defaults to (`read_only` for Observer/Guardian; `network` for Investigator/Communicator/Researcher; any for Actuator with human-approval-required; `network` for Companion with the local-only-provider override).
- **`default_kit_pattern`** — abstract tool categories the genre's archetype-bundled kit pulls from (e.g., Observer → `passive_observation` tools; Companion → `interactive_session` tools).
- **`trait_emphasis`** — which traits matter most. Used by the LLM voice renderer (ADR-0017) to weight specific traits in the Voice section, and by the (future) constraint policy to refine per-genre rules.
- **`memory_pattern`** — placeholder for ADR-0022. Observer: short retention. Investigator: episodic with long retention. Companion: long episodic + consolidated. Today: stub.
- **`spawn_compatibility`** — which genres this genre can spawn as children. Most genres can spawn within their own genre; some can spawn across (Observer → Investigator when an observation warrants deeper look; Investigator → Communicator when a finding needs to be reported). **Forbidden by default**: Observer → Actuator without explicit override (a watcher should not be its own action-taker; route through a Communicator that the operator can gate).

### Storage: `config/genres.yaml`

A new YAML file alongside `trait_tree.yaml` and `tool_catalog.yaml`. Shape:

```yaml
version: "0.1"

genres:
  observer:
    description: |
      Passive watching, read-only orientation. ...
    risk_profile:
      max_side_effects: read_only
    default_kit_pattern:
      - passive_observation
      - timestamp_helpers
    trait_emphasis: [vigilance, suspicion, thoroughness, audit_trail_discipline]
    memory_pattern: short_retention
    spawn_compatibility: [observer, investigator]
    roles: [network_watcher, log_analyst, signal_listener, dashboard_watcher]

  investigator:
    description: |
      Active correlation across observation surfaces...
    risk_profile:
      max_side_effects: network
    default_kit_pattern:
      - passive_observation
      - cross_source_correlation
      - baseline_comparison
    trait_emphasis: [evidence_demand, double_checking, lateral_thinking, technical_accuracy]
    memory_pattern: episodic_long
    spawn_compatibility: [investigator, communicator]
    roles: [anomaly_investigator, incident_correlator, threat_hunter]

  # ...four more genres elided for brevity in this ADR; see config/genres.yaml
  # at implementation time for the full list.

  companion:
    description: |
      Therapy-adjacent, accessibility runtime, interactive presence. ...
    risk_profile:
      max_side_effects: network
      provider_constraint: local_only   # ADR-0008 Phase 5 floor
    default_kit_pattern:
      - interactive_session
      - memory_recall
      - schedule_aware
    trait_emphasis: [empathy, patience, warmth, composure, transparency]
    memory_pattern: long_consolidated
    spawn_compatibility: [companion]
    roles: [therapist, accessibility_runtime, day_companion, learning_partner]
```

The `roles:` list under each genre is the **inverse mapping** — every role in the system belongs to exactly one genre. Loading enforces a uniqueness check: each role must appear under exactly one genre, and every role known to the trait engine must be claimed by some genre. Loaders fail closed on either violation.

### Inverse lookup

`core/genres.py` exposes:

```python
def genre_for(role: str) -> GenreDef
def roles_for(genre: str) -> tuple[str, ...]
def all_genres() -> tuple[GenreDef, ...]
def can_spawn(parent_genre: str, child_genre: str) -> bool
```

`spawn` enforces `can_spawn(parent_genre, child_genre)` before accepting the request. Cross-genre spawn that violates the compatibility rule returns 400 with a message naming both genres. Operators who want to override (e.g., birth a one-off Observer-to-Actuator chain for a specific incident) supply `--override-genre-spawn-rule` on the request, which records an audit chain event of its own (`spawn_genre_override`) so the violation is visible after the fact.

### What changes in existing surfaces

1. **soul.md frontmatter** gains a `genre: observer` line, computed from the role at birth time (no per-request override; use the role to pick the genre). Frontmatter parser tolerates its absence — old soul files keep parsing.

2. **constitution.yaml** gains a `genre: observer` line at the same level as `role`. Hash includes it (it's part of policy: "this agent is in genre X, which carries these defaults"). Same back-compat as soul.md — old constitutions parse with a missing genre treated as "unknown genre, no genre-level rules apply."

3. **`tool_catalog.yaml`** gets a `genre_default_tools:` block keyed by genre name — an additional layer of standard kit defined at the genre level. Resolution order at birth: tools_remove (per-request) → tools_add (per-request) → archetype standard_tools (per-role) → genre default_tools (per-genre fallback). Today's archetype-keyed kits stay in place; the genre layer is additive scaffolding for future roles whose role-level kit isn't yet declared.

4. **`tool_constraint_policy`** (ADR-0018 T2.5) gains genre-level rules. New always-rules: "Companion-genre agents → all tools require local-only provider"; "Observer-genre agents → reject any tool whose `side_effects != read_only` from the kit at resolution time (refuse to write a constitution that contradicts the genre's risk profile)." Per-genre rules layer onto the existing trait-conditioned rules.

5. **Character sheet** (ADR-0020) `capabilities.genre` and `capabilities.genre_description` fields populate.

6. **Birth UI** gets a genre selector that **filters the role dropdown** — operator picks "Observer" first, sees the four observer roles, picks one. Skipping the genre step jumps to the unfiltered role list (back-compat).

### Genre vs role: who decides what

| Decision                       | Lives at | Why                                                                 |
| :----------------------------- | :------- | :------------------------------------------------------------------ |
| Trait emphasis (Voice prompt)  | Genre    | Roles within a genre share orientation; voice should reflect genre. |
| Default tool kit               | Role > Genre | Role-specific kit overrides; genre default fills gaps for new roles. |
| Spawn compatibility            | Genre    | Cross-role spawn within a genre is universal; cross-genre is the policy decision. |
| Risk profile floor             | Genre    | Observer-class agents shouldn't have filesystem tools regardless of role. |
| Constitution policies (per ADR-0004) | Role | Role templates already exist; keep that layer.                       |
| Trait values                   | Per-agent | The whole point of birth is configuring traits.                      |
| Memory pattern (post-ADR-0022) | Genre    | Companion needs long memory; Observer doesn't. Genre is the natural carrier. |

## Consequences

**Upside:**

- **Operator UX improves.** Picking from seven genres is less cognitive load than picking from fifteen-plus eventual roles. The genre is the orientation; the role is the specialty.
- **New roles inherit safety properties for free.** Adding a `dashboard_watcher` role just requires declaring it under `observer:` in `genres.yaml`; the read-only floor and the standard kit pattern come along automatically.
- **Voice quality improves.** The LLM voice renderer (ADR-0017) gains genre-aware trait emphasis. A Companion's voice will sound different from an Actuator's even before per-trait tuning, because the genre tells the prompt which traits to lean on.
- **Spawn discipline becomes visible.** An Observer that spawns an Actuator without override is currently invisible until the operator notices the resulting agent's tool surface. With genre compatibility rules, the request fails at submit time; with `spawn_genre_override` the violation is audit-chain visible.
- **Aligns with Phase 5 (ADR-0008).** The Companion genre carries the local-only-provider floor as a structural constraint, not a hand-wavy "remember to set the provider." Therapy/accessibility agents land with the privacy guarantee baked in.

**Downside:**

- **One more YAML to maintain.** Loading enforces consistency between `genres.yaml`, `trait_tree.yaml`, and `tool_catalog.yaml` — every role known to the trait engine must be claimed by a genre, every genre's default_kit_pattern must reference real catalog tags. Drift is detectable at load time but adds a coupling layer.
- **Spawn rules can feel restrictive.** A specific incident might genuinely call for an Observer that creates a ticket directly. The override mechanism handles it, but the friction of writing the override and acknowledging the audit-event is real. We accept that — friction is the feature for cross-genre spawns.
- **Roles that genuinely span genres get awkward.** A `triage_analyst` that observes, investigates, AND communicates picks one parent genre and feels reductive. Mitigation: the `spawn_compatibility` mechanism lets a multi-stage workflow split across genres without forcing one role into multiple homes. If multi-genre roles become common, revisit with a multi-parent variant.

**Out of scope for this ADR:**

- **Subgenres.** A "passive observer" vs. "active scanner" distinction inside the Observer genre might prove useful. v1 is single-level; revisit if the Observer genre's kit/policy variance grows past one shape.
- **Custom operator genres.** v1 ships seven genres. Adding `your_company_specific_genre.yaml` is plausible but introduces a new merge problem. Defer until there's a real second operator with a real new genre.
- **Genre-level constitution templates.** Today constitution policies live at the role level (per ADR-0004). Genre-level templates that role-templates inherit from is a possible extension; v1 keeps the existing role-template surface and adds genre-level *constraints* (via tool_constraint_policy) rather than full templates.
- **Cross-genre lineage analytics.** "Show me all agents whose lineage crossed genre X to Y" is a useful inspection capability but not a v1 feature. The audit chain has the data; querying it is downstream tooling.

## Open questions

1. **Where does the `genre` field on `BirthRequest` live, if anywhere?** Two options: (a) implicit (always derived from role) — keeps the request simple, but means the operator can't override; (b) explicit (operator can set it) — flexibility at the cost of additional validation surface. **Lean (a)** for v1: genre is a property of the role, not a per-request choice. The override mechanism for spawn compatibility is sufficient flexibility.

2. **Should `genre_description` be in soul.md frontmatter?** It's static-per-genre and operators reading the soul file would benefit. **Lean yes** — write the description into a frontmatter `genre_description` field so the soul.md is fully self-contained for that line of inspection. The genre name + description together fit one line of YAML.

3. **How are the 7 genres discoverable?** A `GET /genres` endpoint that returns the loaded genres + their descriptions seems obvious. Defer the implementation to T2 of this ADR's tranche list — but file the endpoint name now so consumers can plan against it.

4. **What happens when a genre spawns its parent's genre?** E.g., an Investigator spawns an Observer (going "back" to passive watching after the active correlation is done). Plausible workflow, currently not in any genre's `spawn_compatibility`. Add it to the relevant genres' compatibility lists when implementing; capture that "downstream + sideways" is the default, "upstream" is on a per-pair basis.

5. **Memory pattern field — does it overlap with ADR-0022's eventual schema?** Yes; the field here is a placeholder string ("short_retention", "episodic_long", "long_consolidated"). When ADR-0022 lands with a concrete memory schema, this field becomes a key into the memory subsystem's allocation table. Forward-compatible enough that I don't think it needs renaming; the implementation tranche can refine.

## Implementation tranches

- **T1** — `config/genres.yaml` with all 7 genres + their roles. `core/genres.py` loader with consistency checks against trait_tree.yaml and tool_catalog.yaml. Tests for load + lookup + spawn_compatibility.
- **T2** — `GET /genres` endpoint (read-only enumeration). Frontend consumes it to populate the genre dropdown.
- **T3** — soul.md frontmatter gains `genre` + `genre_description`. constitution.yaml gains `genre`. Both fields auto-computed at birth time from the role.
- **T4** — `tool_catalog.yaml` gets `genre_default_tools:`. Kit resolution order updated.
- **T5** — `tool_constraint_policy` gains genre-level always-rules (Companion → local-only-provider; Observer → refuse non-read_only tools at resolve time).
- **T6** — `BirthRequest` / `SpawnRequest` validate spawn compatibility; `--override-genre-spawn-rule` records `spawn_genre_override` audit event.
- **T7** — Voice renderer (ADR-0017) consumes `trait_emphasis` from genre to weight the user prompt.
- **T8** — Frontend genre selector → role filter on the birth form.
- **T9** — Character sheet `capabilities.genre` + `genre_description` populated.

T1+T2+T3 is the "genre exists" milestone. T4 is the loadout improvement. T5+T6 are the policy enforcement. T7 is voice-quality follow-on. T8 is UX polish. T9 wires character sheet.
