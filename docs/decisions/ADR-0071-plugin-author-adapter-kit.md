# ADR-0071 — Plugin Author + Adapter Kit

**Status:** Accepted (2026-05-14). Phase α of the ten-domain
platform arc. Makes Forest's plugin substrate (ADR-0043) usable by
third-party authors AND adaptable to existing MCP servers.

## Context

Forest already supports MCP plugins via ADR-0043. The plugin
manifest format works, the dispatcher bridge merges third-party
tool catalogs at runtime, per-(agent, plugin) grants gate
invocation. What's missing is **the authoring path**.

Two operator scenarios:

1. **Operator builds their own plugin.** "I want a Forest plugin
   that talks to my Plaid finance account." Today they have to
   read ADR-0043 + the existing canonical examples
   (forest-echo / brave-search / filesystem-reference), figure
   out the manifest schema, write the tools, hope they got the
   manifest right. Friction kills authoring.

2. **Operator adapts an existing MCP server.** The anthropic/mcp-
   servers repo has Slack / GitHub / GDrive / SQLite / Postgres
   adapters. Today there's no path to take one of those and turn
   it into a Forest plugin without rewriting it. That's a huge
   amount of capability sitting unused.

The operator's design lock from 2026-05-14:
> "We will have our own MCP plug-ins, but adaptable for others to
> either build their own or create a port face for them to use."

ADR-0071 ships both authoring paths.

## Decision

This ADR locks **three** decisions:

### Decision 1 — `fsf plugin new <name>` scaffolds a plugin

A new CLI subcommand generates a complete plugin skeleton:

```
~/.forest/plugins/<name>/
├── plugin.yaml          # ADR-0043 manifest, pre-filled with
│                        # operator-supplied posture / tier
├── README.md            # author-facing docs template
├── tools/
│   └── <tool_name>.py   # one starter tool, fully wired
├── tests/
│   └── test_<tool>.py   # pytest skeleton with mock ctx
└── .gitignore           # standard Forest plugin gitignore
```

Operator runs:

```
fsf plugin new forest-plaid --tier network --tool transactions_list
```

The scaffold:
- Pre-fills `plugin.yaml` with `name`, `tier` (read_only / network
  / filesystem / external), `tools: []` array seeded with the
  declared `--tool` name
- Generates a tool stub that implements the Tool Protocol — args
  validation, execute method, return ToolResult
- Adds a test stub that constructs a mock ToolContext and exercises
  the tool's validate + execute paths
- Pre-populates README with the right next-steps (test, register,
  install)

### Decision 2 — `fsf plugin adapt <upstream>` wraps a 3rd-party MCP

The "port face" Alex called out. Lets an operator point at an
existing MCP server (a stdio-spawnable command or a known
`anthropic/mcp-servers/<name>` repo) and generate the Forest
wrapper:

```
fsf plugin adapt @modelcontextprotocol/server-slack
```

Outputs a Forest plugin under `~/.forest/plugins/forest-slack-adapter/`:

- `plugin.yaml` declaring the upstream server's command +
  default posture (`network` for anything that hits external
  APIs)
- A pass-through dispatcher that lists the upstream's tools at
  install time + proxies invocations through Forest's existing
  MCP bridge (ADR-0043 T4.5)
- Per-tool grant defaults derived from the upstream tool's
  metadata (read-only-looking tools default to no-approval; any
  tool name matching `^(write|delete|send|post|create|update)` 
  defaults to requires_human_approval=true)

The operator approves the install just like any other plugin.
Forest's governance pipeline (constitution gates + per-tool
grants + audit chain) applies on top of the upstream MCP server
unmodified — operator gets ELv2-compatible sovereignty over a
plugin that wasn't authored to Forest's discipline.

### Decision 3 — Reference plugin templates ship in-repo

Under `templates/plugins/`:

- `read-only/` — one read-only tool, no external dependencies,
  perfect for "I want to expose this local data source"
- `network/` — one tool that hits a third-party API. Includes
  HTTP client patterns + secret handling via the existing
  ADR-0052 secret store.
- `filesystem/` — one tool that reads/writes scoped paths.
  Includes constitution allowed_paths pattern.
- `mcp-adapter/` — the wrapper shape `fsf plugin adapt`
  generates. Operators can copy this manually to wrap obscure
  upstream servers.

Each template is a working plugin (tested in `tests/conformance/`)
that an operator can `cp -r` and edit.

## Implementation Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | `fsf plugin new <name>` scaffold + read-only template | This burst (B289). Foundation. | 1 burst |
| T2 | network + filesystem templates + tests | 1 burst |
| T3 | `fsf plugin adapt <upstream>` MCP wrapper generator | 1-2 bursts |
| T4 | Plugin author runbook + publishing guide | 0.5 burst |

Total: 3-4 bursts.

## Consequences

**Positive:**

- Operator authoring time from "read ADR + 3 examples" down to
  "run one command + edit one stub."
- Adapter kit unlocks the entire anthropic/mcp-servers ecosystem
  for Forest operators — instant Slack, GitHub, Postgres, GDrive,
  etc. with Forest's governance + audit on top.
- Plugin authoring becomes accessible to operators who aren't
  fluent in Python — they edit the scaffold's tool method, not
  the whole protocol surface.

**Negative:**

- Adapter wraps third-party MCP servers we didn't author.
  Supply-chain risk lives at the upstream's level. ADR-0062's
  IoC scanner catches the worst patterns at install time but
  operators authoring with adapt get the same trust posture as
  installing any plugin.
- Generated code is opinionated — operators who want a different
  shape have to edit after scaffolding. Templates default
  conservatively.

**Neutral:**

- Reuses ADR-0043 plugin substrate end-to-end. No new daemon-
  level wiring required.
- Reuses ADR-0053 per-tool grants for adapted MCPs.
- ELv2 license discipline holds: scaffold output inherits the
  operator's chosen license; adapter output declares the
  upstream's license in its manifest.

## What this ADR does NOT do

- **Does not auto-install plugins.** `fsf plugin new` writes
  files; the operator runs `fsf install plugin <path>` separately
  per ADR-0043's discipline.
- **Does not modify upstream MCP servers.** The adapter is
  pass-through; operators who want to fork-and-modify do that
  manually.
- **Does not ship a plugin marketplace.** ADR-0055 covers federation
  + discovery + signing. T1 is about authoring, not distribution.

## See Also

- ADR-0043 MCP plugin protocol — the substrate ADR-0071 builds on
- ADR-0052 pluggable secrets storage — adapter handles upstream
  secrets via this surface
- ADR-0053 per-tool plugin grants — adapter inherits per-tool
  granularity automatically
- ADR-0055 marketplace — future distribution of authored plugins
