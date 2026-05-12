# examples/tools/

Forge-shipped prompt-template tool specs. Each `<name>.v<version>.yaml`
file is a `prompt_template_tool.v1` spec ready to install.

## What these are

The kernel ships builtin tools (Python-coded, in
`src/forest_soul_forge/tools/builtin/`) and supports operator-forged
tools (data-only `.yaml` specs at
`data/forge/tools/installed/<name>.v<version>.yaml`).

**This directory is the third category: tracked seed specs that
ship with the repo but install into the operator's local
forge-tools directory on demand.** They're the same shape as
operator-forged tools — just authored by the Forest team or
contributed via PR rather than via the natural-language Forge UI.

Used for:
- Marketplace seed content (these are the first entries the
  marketplace registry will point at).
- Reference examples for plugin authors.
- A starting kit of useful capabilities new operators can install
  without forging anything themselves.

## How to install

```bash
# Copy any single tool to the runtime install dir:
cp examples/tools/text_summarize.v1.yaml data/forge/tools/installed/

# Or copy all of them:
cp examples/tools/*.yaml data/forge/tools/installed/

# Restart the daemon for lifespan to pick them up:
./force-restart-daemon.command

# Or from inside Forest, hit POST /plugins/reload (no restart needed)
# Actually for forged tools the lifespan walks at startup; reload is
# only for ADR-0043 MCP plugins. Restart is currently required.
```

After install, each tool can be granted to any agent via the
Agents tab's **Tool grants (runtime)** pane (ADR-0060 T6, B223).

## What's in this directory

| File | What it does |
|---|---|
| `text_summarize.v1.yaml` | Text → 3 concise bullet points |
| `code_explain.v1.yaml` | Code + language hint → plain-English walkthrough |
| `commit_message.v1.yaml` | Diff → conventional-commits message |
| `regex_explain.v1.yaml` | Regex + flavor → meaning + matching examples |
| `email_draft.v1.yaml` | Bullets + recipient/intent/tone → ready email |

More land as B231 ships tools 6-10.

## Authoring conventions

Per ADR-0060 + ADR-0055, all tools shipped here follow:

1. **`schema_version: 1`** — locked to the prompt_template_tool spec.
2. **Instructional template** per the B210 substrate fix: the
   `prompt_template` is what gets SENT to another LLM at runtime,
   not a literal answer with placeholders. Use imperative voice
   ("Summarize the following…", "Translate the…").
3. **All properties required** — the `prompt_template_tool.v1`
   substrate doesn't substitute defaults; every `{var}` in the
   template must be in `required:`. The operator passes "auto" or
   "" for "I don't know" rather than omitting the arg.
4. **`side_effects: read_only`** unless the tool truly performs
   external actions. Read-only tools dispatch through any agent's
   posture (including red) without per-call approval gates.
5. **`archetype_tags`** picks the roles this tool is most useful
   for. Used by the marketplace's recommendation surface.
6. **`forged_by: forest-team`** for team-authored seed entries;
   community PRs use the contributor's handle.
7. **Description must explain WHEN to use** the tool, not just
   WHAT it does. The marketplace renders this verbatim.
