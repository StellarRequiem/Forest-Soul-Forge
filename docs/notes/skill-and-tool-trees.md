# Skill tree, tool tree, and Skill Forge — design overview

**Status:** Design note (not an ADR — captures structural thinking that informs ADR-0019 T6+ and a future Skill Forge ADR).
**Last reviewed:** 2026-04-26.

This note exists so we have a single place to look when reasoning about *what an agent can do* and *how new capabilities get added*. Three layers:

1. **Skill tree** — what an agent IS (the trait profile). Lives in `config/trait_tree.yaml`.
2. **Tool tree** — what an agent HAS (the tool catalog + per-archetype kits + per-genre fallbacks). Lives in `config/tool_catalog.yaml` and `config/genres.yaml`.
3. **Skill Forge** — how operators add to layer 2 without writing a manifest from scratch. Future feature; this note captures the design.

## Layer 1 — the skill tree (today)

29 traits across six domains:

| Domain | Subdomains | Trait count | Tier mix |
|--------|-----------|------------:|----------|
| Security | defensive_posture, threat_awareness | 5 | 3 primary + 1 secondary + 1 tertiary |
| Audit | verification, transparency | 4 | 2 primary + 1 secondary + 1 tertiary |
| Cognitive | analysis, exploration | 5 | 3 primary + 1 secondary + 1 tertiary |
| Communication | style, tone | 6 | 2 primary + 1 secondary + 3 tertiary |
| Emotional | interpersonal, self_regulation | 5 | 2 primary + 2 secondary + 1 tertiary |
| Embodiment | presentation | 3 | 0 primary + 0 secondary + 3 tertiary |

**Observation:** Embodiment is a single tertiary-only subdomain. Once Companion-tier real-time A/V lands, embodiment will need motion, voice, and tactile subdomains promoted from "tertiary curiosity" to "primary emphasis." A future trait-tree v0.3 expansion is in scope when ADR-0019 T7+ + Phase 5 work begin.

**No skill tree changes pending right now.** The structure is well-shaped for the genres we have. Track with: `tests/unit/test_trait_engine.py` pins the shape; new traits get added there + in `config/trait_tree.yaml`.

## Layer 2 — the tool tree (today)

8 tools across 4 side-effect tiers:

| Tier | Count | Tools | Genre ceiling |
|------|------:|-------|---------------|
| read_only | 7 | packet_query, flow_summary, log_grep, log_aggregate, timestamp_window, baseline_compare, correlation_window | Observer, Guardian (and below) |
| network | 1 | dns_lookup | Investigator, Communicator, Researcher, Companion |
| filesystem | 0 | — | Actuator + filesystem-rule always-approval |
| external | 0 | — | Actuator only, always-approval |

**Observation:** the filesystem and external tiers are empty. Every Actuator-genre role has no native tool surface — operators today have to supply everything via `tools_add`. Once ADR-0019 T2 lands, building one or two reference Actuator tools (`notify_operator.v1`, `ticket_create.v1`) is the next concrete tool-catalog expansion.

## Layer 2.5 — integration possibles

Ten candidate tools sketched. Grouped by family because each family has different runtime properties:

### Family A — MCP proxy

- **`mcp_call.v1`** — generic MCP-server invocation by alias. Lands with ADR-0019 T7.

This single tool unlocks the entire MCP ecosystem. Once shipped, any operator with `mcp-grep`, `mcp-fs-read`, `mcp-jira`, or arbitrary other servers installed gets those tools for free. Side-effects tier inferred per call from the MCP tool's own declaration.

### Family B — LLM-wrapping

- **`summarize.v1`** — read_only, compresses text. Tokens + cost flow through to character-sheet stats.
- **`classify.v1`** — read_only, picks one label from a fixed taxonomy.
- **`translate.v1`** — read_only, source → target language translation.

These wrap `provider.complete()` and report tokens_used + cost_usd. They make agents richer without expanding side-effects exposure. Companion genre will lean heavily on `translate` for accessibility scenarios.

### Family C — inter-agent

- **`delegate_to_agent.v1`** — spawn a sub-agent inside a parent's session.

This composes with ADR-0021 T6 (spawn-compat validation) and ADR-0016 (session modes). The `delegate_to_agent` tool's side_effects is whatever the spawned agent can do — there's a recursion problem here that needs design before T2. Defer until after the runtime is actually executing things.

### Family D — system

- **`notify_operator.v1`** — push toast to frontend (and optionally OS notification). Communicator-genre default. external-tier, always human-approval-gated.
- **`schedule.v1`** — schedule a future invocation of another tool. Cron-flavored. Pairs with the session model.
- **`web_fetch.v1`** — HTTP GET against an operator-allowlisted domain set. Researcher-genre default. network-tier.
- **`memory_recall.v1`** — read from the agent's memory layer. Lands with ADR-0022. Genre privacy contract enforced (Companion strict).
- **`benchmark_run.v1`** — run a named benchmark suite against the current agent. Lands with ADR-0023. Records scores into the character-sheet `benchmarks` section.

## Layer 3 — Skill Forge (concept)

Operator-facing flow for inventing a new tool without writing a manifest by hand.

```
1. Operator describes the tool in plain English.
   → "I need a tool that takes a CIDR range and returns a count of
      distinct source IPs that have appeared in the last hour."

2. Forge proposes scaffolding.
   → Infers side_effects tier from the description (this reads pcap → read_only).
   → Suggests name, version, archetype_tags, input_schema, output shape.

3. Operator refines.
   → Tweak schema, rename, adjust tags. Live re-validation against
     catalog rules + constraint policy.

4. Forge writes the implementation.
   → Drops a Python file under src/forest_soul_forge/tools/builtin/
     (or packages it as a .fsf for distribution).
   → Generates a test file with happy-path + failure cases.
   → Appends a catalog entry to config/tool_catalog.yaml.

5. Hot-reload + test.
   → Runs the test file inside the dockerized harness.
   → Registers the new tool with the running daemon (T5 + T1 wiring).
   → Operator sees it in the Tools panel; can birth/spawn an agent
     that uses it without a stack-rebuild.

6. Commit + share.
   → Local-only / .fsf package / future plugin index.
```

**What's needed for each step**, in dependency order:

| Step | Depends on | Cost |
|------|-----------|-----:|
| 1, 3 | UI work — frontend page with the description input + schema editor | small |
| 2 | LLM-wrapping prompt against the active provider, fed with the catalog YAML schema as context | small |
| 4 | LLM-wrapping codegen + file emission. Operator reviews diff before accept. | medium |
| 5 | ADR-0019 T5 (hot-reload) + ADR-0019 T1 (registry) | depends on T5 — landing in this phase |
| 6 | `.fsf` packaging from T5 | small once T5 ships |

**Practical sequence:** Steps 1-4 are buildable as a CLI tool *today* (no runtime, no hot-reload, just description-to-file-diff). Steps 5-6 require ADR-0019 T5 (`.fsf` plugin loader + hot-reload). A v0.1 Skill Forge as a CLI is a realistic "while T2-T4 are landing" parallel project. The frontend version follows once T5 is in.

## Decision points (open)

1. **Where does Skill Forge live?** CLI subcommand vs. dedicated frontend tab vs. inline-in-Tools-panel. **Lean:** start CLI (faster iteration, no UI work), promote to frontend tab when ADR-0019 T5 lands and hot-reload is real.

2. **Code-generation provider.** Local model (probably too weak for usable Python codegen) vs. frontier (better quality, costs money). **Lean:** frontier for codegen by default with a "use local" toggle for operators with strong-enough local models. The codegen output goes through the operator's review either way; the model isn't trusted, just helpful.

3. **Where do `.fsf` packages live?** `~/.fsf/plugins/` (per-user, OS-standard) vs. `data/plugins/` in the project. **Lean:** both — `data/plugins/` for development of plugins-being-authored, `~/.fsf/plugins/` for operator-installed plugins. Loader scans both.

4. **Skill Forge as an agent or a CLI script?** The recursive option: build a Researcher-genre agent whose role is "tool author." Give it `summarize.v1`, `web_fetch.v1`, and the codegen tool. Let it operate on operator descriptions. Probably premature for v1 but fun to consider.

## Tracking

This note is reviewed alongside ADR-0019 implementation tranches. Items that turn into real decisions get promoted to ADRs:
- Skill Forge → its own ADR when it gets implemented (Phase 6+ candidate).
- Integration possibles → tracked individually as ADR-0019 T-tranches reference them by name.
- Tool tree expansion (Actuator tier) → ADR amendment to ADR-0018 once first external-tier tool ships.

## Cross-references

- ADR-0001 — trait tree (skill tree's source of truth)
- ADR-0018 — tool catalog (tool tree's source of truth)
- ADR-0019 — tool execution runtime (Skill Forge depends on T5)
- ADR-0021 — role genres (constrains tool tree by side-effects tier)
- ADR-0022 — memory subsystem (sources `memory_recall.v1`)
- ADR-0023 — benchmark suite (sources `benchmark_run.v1`)
- docs/notes/agnt-prior-art.md (the SkillForge pattern is named after AGNT's `SkillForge` — different shape, similar inspiration; same prior-art rules apply)
