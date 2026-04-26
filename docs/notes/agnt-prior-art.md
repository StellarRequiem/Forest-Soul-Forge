# AGNT — prior art and pattern-borrow notes

**Status:** Reference / decision record (not an ADR — captures research that informs ADRs).
**Last reviewed:** 2026-04-25.
**Source:** `https://github.com/agnt-gg/agnt`, README on `main`, `LICENSE.md` v1.0 dated 2026-01-01.

This note exists so future ADRs (ADR-0019 tool runtime, ADR-0022 memory subsystem, ADR-0023 benchmark suite) can cite specific AGNT mechanisms as prior art without re-litigating the comparison every time. It also records the license boundary so we don't accidentally take something we shouldn't.

## License boundary (binding)

AGNT is licensed under the **AGNT Community Core License v1.0** — source-available, fair-use, internal-commercial. Not OSI-open-source.

The prohibitions that affect Forest Soul Forge:

- **No fork under another name.** We cannot rename or rebrand AGNT into Forest. Any code we copy from their repo would violate this. Forest is a separate codebase; AGNT is reference material only.
- **No public hosting / SaaS.** Doesn't apply to Forest directly (Forest is local-first), but bars us from running a hosted AGNT on top of Forest infrastructure for external users.
- **No multi-tenant deployment** of AGNT. Same — doesn't bear on Forest itself.
- **Trademark protection** on "AGNT", "AGNT.gg", and product surface names ("SkillForge", "Tool Forge", "Plugin Forge", "Widget Forge", "Agent Forge", "Workflow Forge"). We must not use those compound names for our own surface.

What is unambiguously fine:

- Citing AGNT in our ADRs as prior art with attribution and a link.
- Being inspired by their architectural patterns. **Architecture and concepts are not copyrightable.** Format conventions ("ZIP package + manifest") are not a protected work.
- Implementing similar mechanisms with our own names, our own schema, and our own code.

## Patterns worth borrowing (with our renames and our boundaries)

### 1. Trace → insight → memory loop

**AGNT name:** "Evolution Engine" + "SkillForge" + "Insight extraction across 8 categories."
**What it does there:** Every agent execution emits a trace. The engine analyzes traces to extract insights (facts, preferences, corrections, anti-patterns, prompt refinements, tool preferences, bottlenecks, error patterns). Insights flow into agent memory, which influences the next prompt; skills evolve and are scored with a numeric Skill Evolution Score (SES).

**What we borrow conceptually:** the loop shape. Forest's audit chain (ADR-0005) already captures execution events with cryptographic integrity. ADR-0022 (memory subsystem) and ADR-0023 (benchmark suite) collectively gesture at the same closure — observe, derive insight, persist, score. AGNT is further along on the *evolutionary feedback* side; Forest is further along on the *integrity / audit* side. Putting both together is the shape worth writing toward.

**What we explicitly do NOT borrow:**

- Their numeric "Skill Evolution Score" is their product surface. We design our own scoring under ADR-0023.
- Their 8-category insight taxonomy is a specific schema choice. We pick ours from first principles in ADR-0022.
- Their `traces` / `insights` / `evolutions` SQLite tables — we already have our audit chain + registry; we extend our own schema, not theirs.

**Where to cite this:** ADR-0022 (memory) and ADR-0023 (benchmarks) should both have a "Prior art" section linking here.

### 2. Versioned plugin-package format

**AGNT name:** `.agnt` package format. ZIP archive with `manifest.json` + JS code + bundled deps. Plugin templates live in `backend/plugins/dev/`. Hot-reload supported.

**What we borrow conceptually:** the *idea* of a single distributable file with a manifest, bundled deps, and a tool/capability declaration is the right shape for ADR-0019 (Tool execution runtime). It avoids the "scattered files" problem and gives us versioning, hash addressability, and offline distribution.

**What we explicitly do NOT borrow:**

- The `.agnt` extension. We pick our own — candidate: `.fsf` (Forest Soul Forge) or `.tool` for tool packages specifically.
- Their `manifest.json` schema. We define our own under ADR-0019, including fields they don't have (e.g., `side_effects`, `archetype_tags`, `constitution_hash` of the publishing agent if applicable).
- Their build script. We write our own.

**Where to cite this:** ADR-0019 (Tool execution runtime) — section "Distribution format" should reference this note.

### 3. Per-tool execution accounting (tokens + cost)

**AGNT name:** "Per-agent token/cost accounting" + "Per-goal, per-task accounting."
**What it does there:** Every tool call, every LLM call records token usage and cost in SQLite. Aggregated per agent, per goal, per task.

**What we borrow conceptually:** Forest currently doesn't track token/cost. For local-only agents on Ollama this is low value. For agents using frontier providers it is essential — capacity planning, budget alerts, telling the user *why* a long session was expensive.

**What we add when we implement:** schema columns on the tool_invocation event in the audit chain (ADR-0005 v0.2 or v0.3). One-line per-call accounting; aggregation queries on top of the registry.

**Where to cite this:** ADR-0019 (Tool execution runtime) — required for any tool that calls a billed provider.

### 4. Pluggable AI provider layer

**AGNT name:** "15+ AI providers including local CLI tools (Claude Code, Codex CLI, Gemini CLI)."
**What it does there:** abstraction over OpenAI, Anthropic, Gemini, Grok, Groq, Cerebras, DeepSeek, OpenRouter, Together, Kimi, MiniMax, Z-AI, plus local CLI auth flavors.

**What we borrow conceptually:** we already have this in skeleton form (ADR-0008: local-first model provider, with `local` and `frontier` slots). The lesson is *don't lock to one frontier provider*. Our `frontier` slot needs to fan out to multiple options eventually.

**What this points to:** an ADR-0008 amendment or a new ADR (let's call it "ADR-002X — Multi-frontier provider routing") when we add a second frontier provider. Not urgent.

### 5. Model-Context-Protocol (MCP) integration

**AGNT name:** "MCP Integration" — add arbitrary MCP servers, test connections, discover capabilities, NPM search for MCP packages.
**What it does there:** they're an MCP **client**. They consume tools from external MCP servers.

**What we borrow conceptually:** Forest could be an MCP **server** as well as an MCP client. Server: expose Forest agents as tools to other LLMs (an outside agent could spawn a Forest-governed sub-agent for a constrained task). Client: subsume external MCP server tools into Forest's tool catalog.

**Where to cite this:** ADR-0019 (Tool execution runtime) — MCP is a natural transport layer for tool execution. Worth a section "Forest as MCP server / client" in ADR-0019.

## Patterns we explicitly reject

### Workflow DAG designer
AGNT has a visual drag-and-drop workflow builder with 60+ node types. **Forest does not need this.** Forest's spawn lineage is already a DAG at a different abstraction (parent-child agents). Genres (ADR-0021) handle role boundaries the way DAG nodes handle node types in AGNT. Adding a visual designer would conflict with the genre/lineage model and add UI complexity Forest doesn't benefit from.

### "AGI loop" terminology
AGNT calls their execute → evaluate → re-plan cycle an "AGI loop." That's marketing language we don't want. Our equivalent (when we implement it) is just the agent runtime per ADR-0019 plus benchmarks per ADR-0023.

### Marketplace with Stripe Connect
AGNT runs a paid marketplace with referral and revenue share. Forest's tool catalog is a local config file by design. If sharing happens later, it should be via git repos / package registries, not a centralized marketplace.

## Possible future collaboration

The user identified two framings worth revisiting later:

1. **Forest as the policy/identity layer over AGNT's runtime.** AGNT executes; Forest issues the constitution + audit chain that governs. Cleanest separation of concerns.
2. **Tool catalog crossover** — write an adapter between Forest's tool catalog format and AGNT's `.agnt` plugin manifest. Lets either side install the other's surface.

Both are blocked on Forest reaching Phase 5 (we need ADR-0019 implemented first). Both also depend on AGNT's license — they explicitly forbid SaaS / fork-under-another-name, but adapter glue between two separate codebases that the same user installs locally is not a fork. Pre-flight legal check before any deeper move.

For now: **borrow patterns, ship our own implementations, cite AGNT as prior art, revisit collaboration framings post-Phase-5.**

## Cross-references

- ADR-0005 — audit chain (the integrity layer the loop runs over).
- ADR-0008 — local-first model provider (where multi-frontier routing eventually lands).
- ADR-0019 — Tool execution runtime (pending, not yet filed; will cite this note for plugin format and MCP integration).
- ADR-0021 — Role genres (already accepted; orthogonal to AGNT).
- ADR-0022 — Memory subsystem (will cite this note for the trace → insight pattern).
- ADR-0023 — Benchmark suite (will cite this note for evolution scoring; we design our own scoring scheme).
