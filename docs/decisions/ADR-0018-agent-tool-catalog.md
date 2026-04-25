# ADR-0018 — Agent tool catalog and per-archetype standard tools

- **Status:** Proposed
- **Date:** 2026-04-25
- **Supersedes:** —
- **Related:** ADR-0001 (hierarchical trait tree), ADR-0002 (DNA + lineage), ADR-0004 (constitution builder), ADR-0006 (registry as index over artifacts), ADR-0008 (local-first model provider), ADR-0017 (LLM-enriched soul.md narrative).

## Context

Agents in the Forge currently have personality (a trait profile), structure (a constitution derived from the profile + role), and now voice (an LLM-generated `## Voice` section per ADR-0017). What they don't have is **capability** — concrete tools they can use to do things. A network_watcher with no tool to query packet captures can describe how it would investigate but can't actually investigate. A log_analyst with no tool to grep logs is a personality with no hands.

The product goal — a Forge that produces agents you can spawn and put to work — requires a model for declaring, bundling, and reasoning about each agent's tool surface. This ADR captures that model. It does not implement execution; it captures **what tools an agent has**, not **how the agent invokes them**. Execution is a follow-on ADR / tranche so we can pin the declaration shape first without coupling it to any one runtime.

The shape is informed by:

- **MCP** (Model Context Protocol) tool descriptors — name, description, input_schema. A widely-adopted, declarative format that other tools and runtimes already understand. If our tool decls are MCP-shaped, Forest agents become first-class participants in MCP graphs without retrofit.
- **Real archetypes have natural tool bundles.** A network_watcher's standard kit looks different from a log_analyst's, which differs from an anomaly_investigator's. Hard-wiring "every agent gets every tool" wastes the type system; making each birth specify every tool wastes the user's time. A per-archetype default that's overridable per agent is the right middle.
- **ADR-0006: artifacts are canonical.** Whatever shape we pick must be representable in soul.md / constitution.yaml on disk. The registry mirrors the declaration; rebuild-from-artifacts must recover the full tool surface for each agent.

Three shapes for the tool decl were considered:

1. **Inline schemas in soul.md frontmatter.** Each tool's full input_schema lives in the agent's frontmatter. Pros: artifact is self-contained. Cons: soul.md frontmatter bloats fast — a typical analyst kit is 5–10 tools, each with a non-trivial input_schema, easily 200+ lines of YAML duplicated across every analyst-class agent. Auditors lose signal. Rejected.

2. **Catalog reference, no schemas in soul.md.** soul.md lists tool names; the catalog (`config/tool_catalog.yaml`) holds the full schemas. Pros: soul.md stays tight; canonical schemas live in one place; updating a tool's schema doesn't require rewriting every agent. Cons: an agent's tool surface is no longer self-contained — you need both the soul.md and the catalog at a given version to know what the agent can do. Mitigated by the next decision (catalog versioning).

3. **Hybrid: name + version in soul.md, full schema in catalog.** Pick this. soul.md frontmatter carries `tools: [{name: "packet_query", version: "1"}, ...]`. The catalog file is versioned per-tool (entries like `packet_query.v1`, `packet_query.v2`). Adding a new version of a tool is additive — old agents keep referencing v1 until they're regenerated; new agents pick up v2. Auditors can still answer "what could this agent do" by joining soul.md + catalog at the agent's referenced versions.

Option 3 wins because the audit trail — "what surface did this agent have when it took action X" — survives schema evolution. v1 isn't deleted from the catalog when v2 lands; it's just no longer the default for new births.

## Decision

### Tool declaration

**`config/tool_catalog.yaml`** is the canonical source of tool definitions. Shape:

```yaml
version: "0.1"
tools:
  packet_query.v1:
    name: packet_query
    version: "1"
    description: |
      Query the local pcap store for packets matching a BPF filter
      within a time window.
    input_schema:
      type: object
      required: [filter, start_ts, end_ts]
      properties:
        filter: { type: string, description: "BPF filter expression" }
        start_ts: { type: string, format: date-time }
        end_ts: { type: string, format: date-time }
        limit: { type: integer, minimum: 1, maximum: 10000, default: 100 }
    side_effects: read_only      # read_only | network | filesystem | external
    archetype_tags: [network_watcher, anomaly_investigator]
  log_grep.v1:
    name: log_grep
    version: "1"
    description: ...
    side_effects: read_only
    archetype_tags: [log_analyst, anomaly_investigator]
  ...
```

Each entry is keyed by `{name}.{version}` so a tool's history is preserved as new versions land.

### Per-archetype standard bundle

A second top-level mapping in the same file (or a sibling file — TBD in implementation) maps each role to its standard tool kit:

```yaml
archetypes:
  network_watcher:
    standard_tools:
      - packet_query.v1
      - flow_summary.v1
      - dns_lookup.v1
  log_analyst:
    standard_tools:
      - log_grep.v1
      - log_aggregate.v1
      - timestamp_window.v1
  anomaly_investigator:
    standard_tools:
      - packet_query.v1
      - log_grep.v1
      - baseline_compare.v1
      - correlation_window.v1
```

When an agent is birthed, the daemon resolves the role's `standard_tools` from the catalog, applies the per-request `tools_add` / `tools_remove` overrides (see schema below), and writes the resolved list into the agent's soul.md frontmatter. `tools_remove` is filtered by name (any version), `tools_add` accepts `{name, version}` pairs and validates each against the catalog.

### Schema additions

**`BirthRequest` / `SpawnRequest`** gain two optional fields:

```python
tools_add: list[ToolRef] = Field(default_factory=list)
tools_remove: list[str] = Field(default_factory=list)
```

where `ToolRef = {"name": str, "version": str}`. Empty defaults preserve current /birth behavior; the standard archetype kit is used unmodified when neither override is supplied.

**`soul.md` frontmatter** gains:

```yaml
tools:
  - { name: packet_query, version: "1" }
  - { name: flow_summary, version: "1" }
  - { name: dns_lookup, version: "1" }
tool_catalog_version: "0.1"
```

The `tool_catalog_version` field pins the catalog version that was active at birth — needed because resolving v1 references requires the catalog file to know what `packet_query.v1` means. Catalog version bumps that don't change v1's schema don't require rewriting agents.

**`constitution.yaml`** gains a `tools` section that mirrors the soul.md list and adds per-agent constraints:

```yaml
tools:
  - name: packet_query
    version: "1"
    constraints:
      max_calls_per_session: 100        # set by birth or inferred from traits
      requires_human_approval: false    # for read-only, false; for external side-effect tools, true
```

The constitution-side constraints are how trait values become tool policy. A high-`caution` agent gets `requires_human_approval: true` on tools whose `side_effects != "read_only"`. Default constraints are derived from trait values via a small policy table (defined in the implementation tranche). Birth-time override is permitted via `constitution_override` per ADR-0004 / Path D.

### Audit chain

`agent_created` and `agent_spawned` event payloads gain a `tools` field listing the resolved kit (name + version pairs). `tool_invoked` and `tool_failed` are reserved as future event types — they belong to the execution-side ADR, not this one.

### Determinism + reproducibility

The trait profile → tool resolution path stays deterministic given:
- A fixed catalog version,
- A fixed standard archetype mapping,
- A fixed tools_add / tools_remove override set on the request.

Two `/birth` calls with identical inputs and identical catalog version produce identical `tools` lists. This preserves the ADR-0002 / ADR-0004 reproducibility property — adding tools doesn't break it.

`dna` continues to hash only the trait profile (per ADR-0002). `constitution_hash` continues to hash the rendered constitution.yaml (per ADR-0004) — and since `constitution.yaml` now contains the resolved tool list and per-tool constraints, **two agents with the same trait profile but different `tools_add` overrides will have different constitution hashes**. That's correct behavior: their effective surface differs.

## Consequences

**Upside:**

- Agents finally have hands. The product can graduate from "personality + structure" to "personality + structure + capability."
- MCP-shaped descriptors mean Forest agents drop cleanly into MCP-native runtimes without translation. The ecosystem already understands what `input_schema` means.
- Per-archetype defaults reduce the cognitive load of birthing an agent. A user who wants a generic network_watcher doesn't have to specify packet_query, flow_summary, etc. — they get the kit.
- Versioning preserves the audit trail across catalog evolution. An agent birthed under catalog v0.1 can be reasoned about even after the catalog is at v0.5, because v0.1 tools aren't deleted.
- Constitution-side per-tool constraints make trait values load-bearing in tool policy. A high-caution agent and a low-caution agent with the same profile-derived tool kit still differ in how each tool is allowed to run. That's the right place to spend the trait signal.

**Downside:**

- Soul.md is no longer self-contained — auditors need the catalog file at the agent's referenced version to fully understand the tool surface. Mitigated by versioning + by keeping the catalog small and committed.
- Catalog versioning adds a process discipline: tool authors must bump the version key on any schema change rather than silently editing v1. Easy to get wrong. The implementation should validate at daemon startup that no two agents reference a `name.version` that's been edited from its committed shape (a kind of catalog-integrity check).
- Constitution-hash now varies with tool overrides. Calls to `/preview` must pass the same overrides as the eventual `/birth` to get hash parity — already true for `constitution_override`, just one more dimension.

**Out of scope for this ADR (deliberately):**

- Tool execution. How does a running agent actually call `packet_query`? That requires a runtime — agent process, MCP transport, sandbox boundary, response handling. Substantial design space; gets its own ADR.
- Tool discovery from external sources (network MCP servers, Anthropic-hosted tools, third-party catalogs). v1 is a local catalog file only; federation is later.
- Per-session tool limits enforcement. The constraint fields above are descriptive in v1; enforcement (rate limiting, approval flows) is a runtime concern.
- A frontend UI for editing the catalog. v1 is YAML in git; editing happens via PRs.
- Hot-reload of the catalog. Daemon reads the catalog on lifespan startup; changes require a restart. Adequate for v1.

## Open questions

1. **Catalog file location.** `config/tool_catalog.yaml` is the obvious choice (sibling to `trait_tree.yaml` and `constitution_templates.yaml`), but a separate `catalog/tools.yaml` directory would allow per-tool files (one YAML per tool) which scales better past ~30 tools. Defer to implementation; start with single file.

2. **How are tool schemas validated?** JSONSchema-style `input_schema` blocks should be validated at daemon startup so a malformed catalog fails closed rather than producing broken agent decls. Pick a validator (`jsonschema`?) and run it during the catalog-load lifespan step.

3. **What does it mean to remove a "standard" tool?** If `network_watcher`'s standard kit includes `packet_query.v1` and a birth request says `tools_remove: [packet_query]`, do we silently allow it (the user knows what they're doing) or refuse (an unfit agent is worse than no agent)? Lean **allow with a warning recorded in the audit event**.

4. **Constraint derivation policy.** The mapping from trait values to per-tool constraints (e.g. `caution >= 80 → requires_human_approval: true on side_effects != read_only`) is policy. Where does it live — hardcoded in code, or as a YAML table the operator can edit? Lean **YAML in `config/tool_constraint_policy.yaml`** so it's transparent and auditable.

5. **Naming: `tools` vs. `capabilities` vs. something else.** `tools` matches MCP nomenclature and is what most readers will expect. Stick with it.

## Implementation tranches (rough; refine when the work starts)

- **T1 — catalog file + loader.** `config/tool_catalog.yaml` with 6–10 starter tools across the three current archetypes. `core/tool_catalog.py` loader with schema validation.
- **T2 — daemon resolution.** Birth/spawn handlers resolve the standard kit, apply tools_add/tools_remove, write to soul.md frontmatter + constitution.yaml.
- **T3 — schema + tests.** BirthRequest/SpawnRequest gain the new fields; tests for kit resolution, override paths, version pinning, audit event_data shape.
- **T4 — frontend toggle.** UI surface for tools_add / tools_remove. Shows the standard kit for the selected role with checkboxes; adding tools-not-in-kit needs a separate picker.
- **T5 — runtime (separate ADR).** Tool invocation, MCP transport, sandboxing. Out of scope here.

T1 + T2 + T3 are the "agent has hands" milestone. T4 is polish. T5 is its own design pass.
