# Runbook — Plugin Authoring (ADR-0071)

**Scope.** End-to-end workflow for authoring a Forest plugin —
either from scratch (`fsf plugin-new`) or as a wrapper around an
existing upstream MCP server (`fsf plugin-adapt`). Covers the
manifest contract, the tier governance model, the install path,
and the publishing flow to a marketplace registry.

**Audience.** Operators authoring or wrapping plugins.

---

## At a glance

Forest plugins are MCP servers that satisfy the ADR-0043 manifest
contract. Forest's dispatcher loads them at startup, registers
their tools alongside builtin tools, and gates per-call dispatch
by the manifest's `side_effects` tier + per-tool
`requires_human_approval` map.

Two authoring paths:

| Path                | When to use                                                         |
|---------------------|---------------------------------------------------------------------|
| `fsf plugin-new`    | Authoring from scratch. Generates Python tool stubs you implement.  |
| `fsf plugin-adapt`  | Wrapping an existing upstream MCP server. No Python code on your side. |

Both produce an ADR-0043 manifest; both install the same way.

---

## Path 1 — Author from scratch

### Scaffold

```bash
fsf plugin-new my-plugin \
  --tier network \
  --tool fetch_weather \
  --license MIT
```

Generates (under `~/.forest/plugins/my-plugin/`):

```
my-plugin/
├── plugin.yaml         # ADR-0043 manifest pre-filled
├── README.md           # next-steps doc
├── tools/
│   └── fetch_weather.py   # Tool Protocol stub for that tier
├── tests/
│   └── test_fetch_weather.py   # pytest skeleton
└── .gitignore
```

The tool stub body is tier-specific (ADR-0071 T2, B305):

- **read_only** — generic echo stub.
- **network** — `urllib.request.urlopen` with timeout + URL scheme validation.
- **filesystem** — `pathlib.Path.resolve` + `_is_within` helper validating against `ctx.allowed_paths`.
- **external** — `subprocess.run` with timeout + `TimeoutExpired` branch + explicit "never use shell=True" warning.

Each tier ships with a one-paragraph rubric in the tool's
docstring so you see the canonical safety pattern inline.

### Implement

Edit `tools/<tool>.py`:

1. Replace the docstring with what your tool does + when an
   operator should grant access.
2. Implement `validate(args)` — raise `ToolValidationError` on
   bad args. Forest gates this before `execute`; downstream code
   can assume args are well-shaped.
3. Implement `execute(args, ctx)` — return a `ToolResult` with
   `success` + `output` + `audit_payload`. `ctx` carries the
   agent identity, audit handle, registry references; use
   `ctx.audit` to emit additional events for noteworthy side
   observations.

### Test

```bash
cd ~/.forest/plugins/my-plugin
pytest tests/
```

The pytest skeleton uses `SimpleNamespace` for the mock `ctx` so
you don't need a running daemon. Add real test cases per
behavior.

### Install

```bash
fsf plugin install ~/.forest/plugins/my-plugin
```

Forest verifies the manifest schema, computes the tool modules'
sha256, registers the plugin's capabilities. Operator confirms
before activation.

---

## Path 2 — Wrap an existing upstream

### Pull the upstream

Most MCP servers are git repos. Clone the upstream you want to
expose to Forest, e.g.:

```bash
git clone https://github.com/example/cool-mcp-server ~/src/cool-mcp
cd ~/src/cool-mcp
# Build / install per its README. End state: an executable
# binary or a running HTTP endpoint.
```

### Scaffold the wrapper

```bash
fsf plugin-adapt cool-server \
  --transport stdio \
  --command /Users/you/src/cool-mcp/bin/cool-mcp-server \
  --tool search \
  --tool fetch \
  --tier network \
  --license MIT
```

Or for an HTTP-transport upstream:

```bash
fsf plugin-adapt cool-server \
  --transport http \
  --url http://127.0.0.1:9100 \
  --tool search --tool fetch \
  --tier network
```

Generates (under `~/.forest/plugins/cool-server/`):

```
cool-server/
├── plugin.yaml   # ADR-0043 manifest with capabilities:
│                 #   - mcp.cool-server.search
│                 #   - mcp.cool-server.fetch
│                 # entry_point.type: stdio (or http)
│                 # sha256 placeholder for stdio binaries
└── README.md     # install procedure with shasum walkthrough
```

**No Python code.** Forest's `mcp_call.v1` dispatcher bridges
each tool call to the upstream at runtime via the configured
transport.

### Compute the binary sha256 (stdio only)

```bash
shasum -a 256 /Users/you/src/cool-mcp/bin/cool-mcp-server
```

Paste the hex digest into `plugin.yaml` under
`entry_point.sha256`. Forest verifies this hash at install time
and refuses to load a plugin whose binary doesn't match.

For HTTP transport there's no checksum — you're trusting the
endpoint URL. Validate via TLS cert pinning, token auth, or
similar before install.

### Install

```bash
fsf plugin install ~/.forest/plugins/cool-server
```

---

## ADR-0043 manifest reference

The minimal manifest:

```yaml
schema_version: 1
name: my-plugin
display_name: "My Plugin"
version: "0.1.0"
author: "@your-handle"
license: MIT

type: mcp_server
side_effects: read_only

capabilities:
  - mcp.my-plugin.do_thing

requires_human_approval:
  do_thing: false

entry_point:
  type: stdio
  command: ./server
  sha256: "<64-char hex>"

required_secrets: []
```

### Side-effects tiers

| Tier         | Examples                                          | Per-call approval default |
|--------------|---------------------------------------------------|---------------------------|
| `read_only`  | Pure compute, hash check, static-catalog lookup   | false                     |
| `network`    | HTTP fetch, REST clients, RSS readers              | true                      |
| `filesystem` | Read/write files under operator-allowed paths     | true                      |
| `external`   | Subprocess invocation, system commands            | true                      |

Pick the LOWEST tier that covers your tool's actual reach. A
filesystem-tier plugin doesn't gain anything by also claiming
network — but it pays the price (per-call approval prompts)
unnecessarily.

### `requires_human_approval`

Per-tool override map. `false` means the tool fires without
operator-per-call confirmation; `true` surfaces an approval
prompt the operator must accept before each call.

Defaults derive from tier (read_only → false; others → true).
Override per tool when you have a higher-trust tool inside a
higher-tier plugin (rare, but legitimate — e.g. a
filesystem-tier plugin with one read-only `cat` tool the
operator wants to skip confirmation for).

### `required_secrets`

List of secret names the plugin needs at runtime. Forest
resolves them via the ADR-0052 pluggable secrets backend (env
var, keychain, vault) at dispatch time. Operator supplies the
values once via `fsf secret set <name>`; the plugin never sees
the value directly — it gets injected into the tool's `ctx`.

Example for a plugin needing a Brave Search API key:

```yaml
required_secrets:
  - BRAVE_SEARCH_API_KEY
```

---

## Publishing to a marketplace

The marketplace (ADR-0055) is an out-of-process index of
operator-installable plugins. Three steps to publish:

### 1. Pick a registry

Either the canonical `forest-marketplace` repo (operator-curated)
or self-host a registry index file. Forest's marketplace browse
pane (frontend) reads any registry that satisfies the ADR-0055
manifest schema.

### 2. Add your plugin to the registry's index.yaml

```yaml
- id: my-plugin
  display_name: "My Plugin"
  version: "0.1.0"
  description: "What it does in one line."
  side_effects_tier: network
  source: https://github.com/you/my-plugin
  manifest_path: plugin.yaml
  sha256: <sha256 of your plugin tarball>
  install_size_kb: 12
  publisher: "@your-handle"
```

### 3. Submit the PR / push to your registry

For the canonical registry: open a PR to `forest-marketplace`.
For a self-hosted registry: push the updated `index.yaml` to your
publishing endpoint.

Forest operators add your registry to their daemon config
(`FSF_MARKETPLACE_REGISTRIES=https://yourdomain.example/index.yaml`)
and your plugin shows up in their Marketplace tab.

---

## What's NOT in this runbook

- **MCP protocol details.** Forest implements the MCP client
  side per the upstream `modelcontextprotocol/python-sdk`. Your
  upstream server-side speaks the same protocol; that's how the
  bridge works. The protocol spec is at
  <https://modelcontextprotocol.io/specification>.
- **Forest internals.** How `mcp_call.v1` dispatches, how
  governance gates per-call execution, how the audit chain
  records each call. Those are documented in their respective
  ADRs (0019 tool dispatch, 0043 plugin protocol, 0053 per-tool
  grants).
- **Authoring upstream MCP servers.** That's the upstream's
  documentation, not Forest's. The MCP SDK README is the
  starting point.

---

## Common gotchas

- **Forgetting to compute `sha256`.** Plugin install refuses to
  proceed without it. The scaffold's placeholder is all zeros;
  you must paste the real digest.
- **Picking a tier too low.** A `read_only` plugin that secretly
  writes files passes manifest validation but fails the
  dispatcher's governance check at runtime. Operators see a
  tier_violation audit event; their fix is to refuse to grant
  the plugin until the manifest is corrected.
- **Naming collisions.** Two plugins both exposing
  `mcp.search.web` will conflict. Namespace capabilities under
  your plugin name (`mcp.my-plugin.web`) — the adapter does
  this automatically.
- **Mutable upstream behavior.** An upstream MCP server that
  changes its tool surface between versions will break your
  wrapper. Pin to a specific upstream version in your manifest
  and bump it deliberately.

---

## See also

- ADR-0043 — MCP-first plugin protocol (the manifest spec)
- ADR-0053 — per-tool plugin grants (the granular gate above
  per-plugin)
- ADR-0071 — plugin author + adapter kit (this runbook's home ADR)
- ADR-0055 — agentic marketplace (the registry surface)
- `examples/plugins/forest-echo/plugin.yaml` — minimal authored
  example
- `examples/plugins/brave-search/plugin.yaml` — adapter
  example with secrets
- `docs/runbooks/encryption-at-rest.md` — sibling Phase α runbook
- `docs/runbooks/memory-consolidation.md` — sibling Phase α
  runbook
