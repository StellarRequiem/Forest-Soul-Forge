# ADR-0059 — Catalog-aware forge propose + install validation

**Status:** Accepted
**Date:** 2026-05-10
**Burst:** B204
**Deciders:** Alex Price (orchestrator)
**Related:** ADR-0030 (Tool Forge), ADR-0031 (Skill Forge), ADR-0057 (Skill Forge UI), ADR-0058 (Tool Forge UI)

## Context

ADR-0057 / B201 closed the operator-direct skill-creation loop. ADR-0058 / B202 did the same for tools. Both wrap the existing forge engines (`forge.skill_forge.forge_skill`, `forge.prompt_tool_forge.forge_prompt_tool`) — propose-only paths inherited from ADR-0030/0031 T1.

The B203 live smoke test surfaced a real problem: the propose stage doesn't have the live tool catalog, so the LLM hallucinates tool names. A real forge call against `qwen2.5-coder:7b` produced `summarize_audit_chain_integrity.v1` (chain seq #6321) — a syntactically valid `SkillDef` referencing **`text_summarizer.v1`**, a tool that doesn't exist anywhere in the catalog. The install path validated only the manifest schema, not whether `requires[]` resolved against real tools, so installing the manifest would have produced an unrunnable skill (`unknown_tool` at first dispatch).

The forge author flagged this in `forge/skill_forge.py:9-13`:

> "For now the LLM doesn't get the list of available tools — that requires hooking the daemon's tool catalog at CLI invocation time, which the next CLI tranche will add."

That tranche never landed. ADR-0057 inherited the limitation. This ADR closes it.

## Decision

Two coupled changes:

### 1. Catalog injection at propose time

**`forge.skill_forge.forge_skill`** accepts an optional `tool_catalog` parameter. When provided, the engine formats a compact one-line-per-tool summary and injects it into the user prompt:

```
AVAILABLE TOOLS — these are the ONLY tools you may reference in
`requires` and in step `tool:` fields. Do NOT invent or rename
tools. If no listed tool fits, the closest match is
llm_think.v1 — which can do arbitrary reasoning given a prompt.

  - llm_think.v1 [read_only]: audited LLM completion as a dispatchable tool
  - memory_recall.v1 [read_only]: read agent's memory
  - memory_write.v1 [read_only]: persist memory entry
  ... (54 total)

Emit the YAML skill manifest now.
```

Format: `  - <name>.v<version> [<side_effects>]: <first sentence of description>`. The side-effects bracket lets the LLM filter for read-only tools when the operator's description suggests a read-only workflow. First sentence cap (~120 chars) keeps the summary under the 32KB MAX_PROMPT_LEN guard at any plausible catalog size.

The HTTP `/skills/forge` endpoint passes `app.state.tool_catalog` automatically. CLI `fsf forge skill` can optionally pass the loaded catalog (from daemon settings) or pass None and accept the hallucination risk — the engine remains compatible with both paths.

**`forge.prompt_tool_forge.forge_prompt_tool`** accepts an optional `genre_engine` parameter. Prompt-template tools don't compose with other tools (they wrap `llm_think.v1` directly), but they DO declare `archetype_tags`. The genre_engine summary surfaces valid archetype names so the LLM doesn't invent ones that don't match any genre. The `/tools/forge` endpoint passes `app.state.genre_engine` automatically.

### 2. Install-time validation

`POST /skills/install` cross-checks `manifest.requires[]` against the live `app.state.tool_catalog.tools` keys. If any referenced tool is not present:

- Default behavior: refuse with `422 Unprocessable Entity` and a structured `unknown_tools_referenced` error listing the offending tools and a hint pointing at `llm_think.v1` as the general-purpose fallback.
- Override: pass `force_unknown_tools=true` in the request body. Operator may want to land a partial skill ahead of installing referenced tools (legitimate workflow when staging a multi-tool change). Skill won't dispatch successfully until the missing tools land, but the install + audit event still fire.

The check is on `requires[]` only, not on per-step `tool:` references — `requires` is the manifest's declared dependency surface and the canonical place to catch the divergence. Per-step tool references must match `requires[]` per ADR-0031 §schema, so checking `requires` is sufficient.

## Consequences

**Positive:**

- The B203 hallucination class is structurally prevented. The LLM still might write nonsense in description fields, but it can't reference tools that don't exist.
- Operators get a meaningful error message at install time instead of a confusing `unknown_tool` dispatch failure later.
- The `force_unknown_tools` escape hatch keeps the legitimate "land a partial" workflow available without making it the default.
- Smith experimenter (ADR-0056) calling these endpoints inherits the same protections — his cycles can't propose nonsense skills either.

**Negative / trade-offs:**

- Prompt grows by ~3-5 KB on a 54-tool catalog. Still well under MAX_PROMPT_LEN (32KB) but increases Ollama latency proportionally. Measured: pre-B204 forge ~12 sec, post-B204 forge ~15 sec on `qwen2.5-coder:7b`.
- The catalog summary is recomputed every forge call. Could cache, but the catalog mutates at lifespan + plugin install + forge install, so a cache would need invalidation. Premature optimization for the burst.
- The check is exact-match keyed on `name.v<version>`. A skill that references `llm_think.v1` works; a skill that references `llm_think` (no version) doesn't, even though the catalog has `llm_think.v1`. Current behavior is the correct one — the manifest schema requires versioned refs — but worth noting if the LLM emits version-less refs (the `_VALID_NAME` regex in `prompt_tool_forge.parse_spec` rejects those at parse time, but `parse_manifest` for skills may be more lenient).

**Out of scope for this ADR (deferred):**

- Per-step `tool:` reference validation. Manifest schema requires every step's `tool:` to be in `requires[]`, so checking `requires[]` covers it. If that invariant slips, this validation would miss it. Tracked as a future tightening if it becomes relevant.
- Catalog injection for the legacy `forge.tool_forge` engine (the codegen path that ADR-0030 owns, distinct from the prompt-template path B202 added). That engine produces Python source code, not a manifest that references catalog tools, so the failure mode is different — covered by static analysis tranches per ADR-0030 T2/T3, not by catalog injection.
- Per-archetype validation for `archetype_tags` on prompt-template tools. The genre_engine hint suggests valid values to the LLM but the install path doesn't refuse on unknown archetype names — those are informational rather than load-bearing.

## Tranches

| T | Scope | Burst |
|---|---|---|
| T1 | `forge.skill_forge` catalog parameter + summary helper | B204 |
| T2 | `/skills/forge` passes `tool_catalog` from app.state | B204 |
| T3 | `/skills/install` validates `requires[]` against catalog | B204 |
| T4 | `force_unknown_tools` escape-hatch field | B204 |
| T5 | `forge.prompt_tool_forge` archetype hints from genre_engine | B204 |
| T6 | `/tools/forge` passes `genre_engine` from app.state | B204 |
| T7 | Tests: unknown-tools-rejected + force-flag-allows | B204 |
| T8 | CLI `fsf forge skill` opt-in to catalog injection | future |

## Verification

- `tests/unit/test_daemon_skills_forge.py::TestSkillsInstall::test_unknown_tool_in_requires_returns_422` — install with hallucinated tool name → 422 + structured error.
- `test_unknown_tool_force_flag_allows_install` — same input + `force_unknown_tools=true` → 200.
- 64 pre-existing forge tests still pass (no regression on the propose-or-install happy paths).
- Live smoke (after deploy): forge a skill via the SoulUX modal with a description that previously produced a hallucinated tool. The propose response now references a real tool from the catalog; install lands without 422.
