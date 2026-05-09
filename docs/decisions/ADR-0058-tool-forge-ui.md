# ADR-0058 — Tool Forge UI (operator-direct, prompt-template path)

**Status:** Accepted
**Date:** 2026-05-09
**Burst:** B202
**Deciders:** Alex Price (orchestrator)
**Related:** ADR-0030 (Tool Forge), ADR-0019 (Tool runtime), ADR-0044 (Kernel + SoulUX), ADR-0057 (Skill Forge UI), ADR-0056 (Experimenter agent)

## Context

ADR-0057 (B201) closed the operator-direct creation loop for skills. Skills are pure declarative YAML — install means dropping a manifest file + emitting an audit event, no Python touched. Tools have an extra dimension: a Python implementation. Three viable approaches existed for closing the operator-direct creation loop for tools:

1. **Stub-only** — UI creates the catalog entry, marks `requires_human_approval: always`, leaves the Python file empty. Dispatch refuses with "not implemented" until a developer fills it in. Fast to build, limited utility.
2. **Prompt-template tool** — UI creates a tool that's essentially a thin wrapper around `llm_think.v1` with a custom prompt prefix. No new Python needed: a single generic `PromptTemplateTool` class handles them all, parameterized by the catalog entry's stored template.
3. **Plugin-protocol tool** — UI scaffolds an MCP plugin per ADR-0043, operator finishes it externally. Most flexible, but pushes the actual creation outside SoulUX.

Per Alex's directive 2026-05-09 (B201 scoping discussion): **Option 2 for the MVP, Option 3 as the "advanced" follow-up.** The reasoning: option 2 is the most useful path for an operator who wants a custom thinking tool without writing code, and it's the natural complement to skills (which can already chain `llm_think.v1` calls).

## Decision

Add an operator-direct tool creation path through the SoulUX UI, scoped to **prompt-template tools** — read-only LLM wrappers built from a stored template. Concretely:

1. **Generic builtin** — `PromptTemplateTool` (registered as `prompt_template_tool.v1` in the canonical sense, but instantiated MULTIPLE times under operator-chosen names like `summarize_audit.v1`, `headline_event.v1`, etc.). At `__init__` it accepts `name`, `version`, `description`, `input_schema`, `prompt_template`. At `execute` it validates input against the schema, substitutes args into the template, calls `provider.complete()` (same path as `LlmThinkTool`), returns `{response, model, elapsed_ms}`.

2. **Forged tool spec** at `data/forge/tools/installed/<name>.v<version>.yaml`:
    ```yaml
    schema_version: 1
    name: summarize_audit
    version: '1'
    description: Summarize the most recent N audit chain entries.
    implementation: prompt_template_tool.v1
    side_effects: read_only
    archetype_tags: [observer, communicator]
    input_schema:
      type: object
      required: [n_entries]
      properties:
        n_entries: {type: integer, minimum: 1, maximum: 100}
    output_schema:
      type: object
      properties:
        response: {type: string}
    prompt_template: |
      Summarize the following {n_entries} audit chain entries...
    ```

3. **Lifespan walks** `data/forge/tools/installed/` after registering builtins; each spec spawns one `PromptTemplateTool` instance + augments `app.state.tool_catalog` with a synthetic `ToolDef` entry so the dispatcher's catalog cross-check passes.

4. **HTTP endpoints** mirroring ADR-0057 shape:
    - **POST `/tools/forge`** — accepts `{description, name?, version?}`, calls a new `forge.prompt_tool_forge.forge_prompt_tool` engine (one provider call returning a spec.yaml), stages under `data/forge/tools/staged/<name>.v<version>/spec.yaml`, emits `forge_tool_proposed`.
    - **POST `/tools/install`** — accepts `{staged_path, overwrite?}`, validates the spec, copies to installed dir, constructs + registers the `PromptTemplateTool` instance live (no daemon restart), augments catalog, emits `forge_tool_installed`.
    - **GET `/tools/staged`** — list pending.
    - **DELETE `/tools/staged/{name}/{version}`** — discard.

5. **Frontend** — "+ New tool" button on the Tools tab; modal mirrors the Skills modal: description textarea + name + version → Forge → spec.yaml preview → Install / Discard.

## Consequences

**Positive:**
- Closes the loop for non-developer operators who want a custom thinking tool. The whole tool lifecycle (forge → install → dispatch → audit) happens through the UI; no shell required.
- Reuses the proven dispatcher pipeline. A forged tool is a normal `ToolRegistry` entry — constitution constraints, genre kit-tier ceiling, posture checks, audit emit all work identically to a builtin.
- Prompt-template tools are `side_effects: read_only` by construction, so they fit inside Guardian-genre agents without human approval per call. Operator can grant them to any agent the same way they'd grant `llm_think.v1`.
- Smith (ADR-0056) can drive these endpoints from his propose-cycle path the same way operators do; chain shape is identical.

**Negative / trade-offs:**
- Limited expressiveness — a prompt-template tool can only do what `llm_think.v1` can do, plus templated input substitution. It can't do real I/O (network, filesystem, external commands). For those, ADR-0043 plugin protocol remains the path.
- Template substitution is naive `{var_name}` style. Doesn't support conditionals or loops. By design — anything more complex argues for a real implementation, not a template.
- Live tool registration (without daemon restart) is new. Pre-B202 the only live tool addition was via plugin loader on `POST /tools/reload`. The install endpoint adds a second live-registration path; both go through the same `ToolRegistry.register` so the cross-check / write_lock discipline is preserved.
- Catalog augmentation is now multi-source: `tool_catalog.yaml` (ground truth), plugins from `data/plugins/`, and now forged tools from `data/forge/tools/installed/`. The catalog merge order matters for collisions; documented in the install path docstring (forged tools refused at install time if the name collides).

**Out of scope for this ADR (deferred):**
- Tool editing — install is overwrite-or-create only; editing a live tool's template requires re-forge + re-install. Inline edit is governance-sensitive and gets its own ADR.
- Plugin-protocol tools (option 3) — separate path for operators who want real I/O. Tracked as a follow-up; not blocked on this work.
- Smith driving the endpoints — natural follow-up but it's a runtime test, not a code burst. Tracked as B203.
- Forged-proposals subsection in the Approvals tab — deferred from B201 alongside this ADR's matching deferral. Modal handles install/discard inline; the unified proposals queue is a UX nice-to-have.

## Alternatives considered

**A. Stub-only mode** — rejected as already addressed: limited utility, requires a developer to follow up before the tool actually does anything.

**B. Plugin-scaffold from UI** — rejected for the MVP because it pushes the implementation work outside SoulUX. Will revisit as a follow-up arc.

**C. Inline Python sandbox** — let operators write Python in the modal. Rejected: reintroduces the security boundary the plugin protocol exists to manage. Plugin protocol IS the answer for "operator wants to write code."

## Tranches

| T | Scope | Burst |
|---|---|---|
| T1 | `PromptTemplateTool` builtin + register_builtins integration | B202 |
| T2 | Lifespan walk of `data/forge/tools/installed/` + catalog augmentation | B202 |
| T3 | `forge.prompt_tool_forge` engine | B202 |
| T4 | POST `/tools/forge` + `/tools/install` + GET/DELETE staged | B202 |
| T5 | Frontend "New tool" modal on Tools tab | B202 |
| T6 | Tests | B202 |
| T7 | Smith driving the endpoints (runtime demo) | B203 |
| T8 | Plugin-protocol path (option 3) — deferred follow-up | future |

## Verification

- Unit tests cover both endpoints, lifespan registration of forged tools, the `PromptTemplateTool` execute path with template substitution, and an end-to-end forge→install→dispatch cycle.
- Live smoke: from the SoulUX Tools tab, type "a tool that summarizes the last N audit chain entries", click Forge, click Install. Tools tab refresh shows the new tool in the registered list. From a test agent, dispatch the new tool and verify the chain shows `tool_call_dispatched` → `tool_call_succeeded` with the expected response shape.
