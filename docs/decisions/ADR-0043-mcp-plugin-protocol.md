# ADR-0043 — MCP-First Plugin Protocol

**Status:** Accepted (2026-05-04). Builds on `mcp_call.v1` from
ADR-003X Phase C4 + `config/mcp_servers.yaml`. Implementation
across Bursts 104-107.

## Context

The integrations roadmap (Burst 102,
`docs/roadmap/2026-05-04-integrations-strategy.md`) ranked an
MCP-first plugin protocol as the highest-leverage v0.5 expansion
move. Anthropic's Model Context Protocol is the de-facto open
standard for tool servers in 2026; the community is publishing
new servers daily (filesystem, postgres, slack, github,
brave-search, linear, jira, hundreds more). Each one is a tool
surface Forest could pick up for free.

Forest already has the bones:

- `mcp_call.v1` (ADR-003X Phase C4) — the dispatcher tool that
  invokes MCP servers by name.
- `config/mcp_servers.yaml` — operator-curated server registry
  with sha256-pinned binaries, side-effect classification per
  ADR-0019, and allowlisted-tool filtering.
- `agent_secrets` (ADR-003X K1) — per-agent secret store the
  servers can pull from.
- The audit chain captures every `mcp_call.v1` dispatch the same
  way it captures any other tool call.

What's MISSING for a real plug-and-play story:

1. **Discovery + installation flow.** Today operators edit YAML
   by hand, stage a binary, compute its sha256. The friction is
   high enough that new users won't do it.
2. **Plugin metadata.** `mcp_servers.yaml` is bare —
   server_name + url + sha256 + side_effects + allowlist. There's
   no description, no version, no author, no required-secrets
   list, no license, no source URL. New users can't tell a
   plugin from another by reading the config.
3. **Hot-reload.** Servers register at daemon lifespan only.
   Adding a new MCP server requires a daemon restart, which
   loses scheduler state, conversation context, and runtime
   counters.
4. **Audit-chain coverage of installation.** `mcp_call`
   dispatches are audited; the act of *adding* an MCP server
   to the registry is not. There's no `plugin_installed` /
   `plugin_enabled` event.
5. **Registry / discovery.** No community catalog of available
   plugins. Operators must know to look for, e.g., the GitHub
   MCP server and find its sha256 manually.
6. **Plugin types beyond MCP servers.** Today's design treats
   MCP servers as the only third-party extension surface. But
   skills (YAML manifests) and tools (Python builtins) are also
   plug-and-play candidates — they just don't have a packaging
   format.

This ADR specifies the upgrade path: keep the
`mcp_servers.yaml` substrate as one half of the storage layer,
add a richer `plugin.yaml` manifest format on top, and ship a
CLI + hot-reload + audit-chain integration around it.

## Decision

Build a plugin protocol with these properties:

1. **MCP-first.** MCP servers are the canonical plugin type;
   skills and custom tools are secondary. The protocol shape is
   designed for MCP and extends to others.
2. **File-system layout under `~/.forest/plugins/`.** Plugins
   live in directories on disk, each with a `plugin.yaml`
   manifest. The daemon walks this directory at startup and
   on hot-reload.
3. **CLI-driven install / enable / disable / secrets.**
   Operators don't edit YAML directly. `fsf plugin install
   <name>` from a registry URL clones the manifest + verifies
   the manifest's sha256 against the registry's pin.
4. **Hot-reload via `/scheduler`-class endpoint.** Operators
   call `POST /plugins/reload` (gated by writes_enabled +
   api_token); the daemon re-walks `~/.forest/plugins/` and
   updates the runtime tool catalog without restart.
5. **Audit-chain integration.** Six new event types capture
   every plugin lifecycle transition. Same evidence footing as
   builtin tools.
6. **Registry as a Git repo.** A community catalog lives in a
   separate `forest-plugins` GitHub repo. Each plugin is a
   subdirectory; `fsf plugin install` clones the relevant subdir.
   No infra to operate.

## Architecture

### File-system layout

```
~/.forest/
├── plugins/
│   ├── installed/
│   │   ├── github-mcp/
│   │   │   ├── plugin.yaml          # manifest
│   │   │   ├── server               # binary (or script)
│   │   │   └── README.md
│   │   ├── filesystem-mcp/
│   │   ├── postgres-mcp/
│   │   └── my-custom-tool/          # operator-authored, not from registry
│   ├── disabled/                     # plugin.yaml moved here when disabled
│   ├── registry-cache.json           # last-fetched plugin catalog
│   └── secrets/                      # per-plugin credentials (already exists,
│                                     # extended with plugin-scope keys)
└── (existing data dirs)
```

### plugin.yaml schema

```yaml
# plugin.yaml — manifest for a single Forest plugin.
schema_version: 1                # protocol version

# Identity
name: github-mcp                 # unique key; matches install dir name
display_name: "GitHub (MCP)"
version: "0.3.1"                 # plugin's own version, semver-ish
author: "stellarrequiem"
homepage: https://github.com/...
license: MIT

# Type — determines how Forest registers it
type: mcp_server                 # mcp_server | tool | skill | genre

# What it provides
capabilities:
  - mcp.github.list_issues
  - mcp.github.get_issue
  - mcp.github.create_issue       # listed in capabilities; gated below
  - mcp.github.search_code

# Governance — same vocabulary as ADR-0019 + ADR-003X
side_effects: external           # read_only | network | filesystem | external
requires_human_approval:         # per-tool override map
  list_issues: false
  get_issue: false
  search_code: false
  create_issue: true             # mutating tools always gate

# Runtime config
entry_point:
  type: stdio                    # stdio | http | (future: websocket)
  command: "./server"            # relative to plugin dir
  args: []
  sha256: 0000000000000000000000000000000000000000000000000000000000000000

# Secrets — operator gets prompted on install
required_secrets:
  - name: GITHUB_TOKEN
    description: "GitHub Personal Access Token with repo:read scope"
    env_var: GITHUB_TOKEN

# Optional registry signature for verified plugins
verified_at: 2026-05-04T12:34:56Z
verified_by_sha256: <registry maintainer signature>
```

### CLI surface

Operator commands via `fsf plugin <subcmd>`:

```
fsf plugin install <name>      # clone manifest from registry; prompt secrets
fsf plugin install <git-url>   # install from a Git URL (custom plugins)
fsf plugin list                # installed + enabled state + version
fsf plugin enable <name>       # register tools into runtime catalog
fsf plugin disable <name>      # unregister; move plugin.yaml to disabled/
fsf plugin uninstall <name>    # rm -rf installed/<name>/ + audit emit
fsf plugin secrets set <name> <KEY>=<value>
fsf plugin secrets list <name>
fsf plugin search <query>      # query the registry catalog
fsf plugin info <name>         # plugin.yaml dump
fsf plugin verify <name>       # re-check sha256 + signatures
fsf plugin reload              # hot-reload tool catalog
fsf plugin update <name>       # re-fetch from registry; bump version
```

### HTTP endpoints

```
GET  /plugins                  # list installed + state
GET  /plugins/{name}           # one plugin's manifest + state
POST /plugins/reload           # hot-reload (gated by writes + token)
POST /plugins/{name}/enable    # gated
POST /plugins/{name}/disable   # gated
POST /plugins/{name}/verify    # re-check sha256 (gated)
```

`POST` endpoints are `require_writes_enabled + require_api_token`,
same posture as the writes routes and the scheduler control
endpoints from ADR-0041 T6.

### Audit events (new)

Six event types, all emitted by the plugin lifecycle code:

| Event type | Emitted when |
|---|---|
| `plugin_installed` | Plugin manifest staged in `~/.forest/plugins/installed/` |
| `plugin_enabled` | Operator activated the plugin; tools registered into catalog |
| `plugin_disabled` | Operator deactivated; tools unregistered |
| `plugin_uninstalled` | Plugin directory removed |
| `plugin_secret_set` | Operator updated a plugin secret (value never logged; only the key + plugin name) |
| `plugin_verification_failed` | sha256 mismatch on launch — server refused |

Audit-chain integration means a forensic question like "what
external capabilities did this agent have at time T?" is
answerable by replaying the chain.

### Hot-reload semantics

Calling `POST /plugins/reload` walks
`~/.forest/plugins/installed/`, computes a diff against the
runtime catalog, and applies:

- New plugins: register their tools into the catalog under the
  governance pipeline; emit `plugin_enabled`.
- Removed plugins: unregister their tools; emit
  `plugin_disabled`.
- Modified plugins (manifest sha256 changed): unregister + re-
  register; emit `plugin_disabled` followed by `plugin_enabled`
  with the new version.

Mid-flight tool calls complete normally — the dispatcher
serializes calls under the write lock, so a hot-reload never
mid-execute swaps a tool out from under a running call.

If a plugin's binary sha256 doesn't match its manifest, the
hot-reload skips it and emits `plugin_verification_failed`. The
plugin stays in `installed/` but unregistered until the operator
runs `fsf plugin verify` and resolves the mismatch (operator
either trusts the new sha256 and updates the manifest, or
removes the tampered binary).

### Registry

The community catalog lives in a separate Git repository:

```
forest-plugins/                    # separate GitHub repo
├── plugins/
│   ├── github-mcp/
│   │   └── plugin.yaml             # manifest + signature
│   ├── filesystem-mcp/
│   ├── postgres-mcp/
│   └── ...
├── REGISTRY.json                   # generated index
└── README.md                       # contribution guidelines
```

`fsf plugin install <name>` does:

1. Fetch `REGISTRY.json` (cache for 1h)
2. Look up `<name>` → get its repo path
3. Sparse-checkout that subdir from the registry repo
4. Verify the manifest's `verified_by_sha256` matches the registry
   maintainer's signature
5. Stage into `~/.forest/plugins/installed/<name>/`
6. Prompt for required secrets
7. Emit `plugin_installed` to the audit chain

No server-side infra. GitHub Pages serves the registry; the
client does discovery via the GitHub API or a static JSON.

For unverified plugins (community-contributed but not
maintainer-signed), the install flow surfaces a "this plugin is
unverified — install anyway?" prompt. Operators can opt in
explicitly. Forest never silently runs unsigned binaries.

## Why MCP-first (alternatives considered)

### Plain Python entry-point plugins

Considered. Rejected because Python imports run with full
process privileges. An untrusted plugin could read the audit
chain, rewrite the registry, exfiltrate secrets, or escape the
governance pipeline by mutating the dispatcher's pipeline list.
The MCP wire protocol is a sandbox by construction: each server
runs as a subprocess with explicit capability declaration. The
plugin can only do what its `capabilities:` list says.

### OCI containers per plugin

Considered. Rejected for v0.5 because it requires a container
runtime (Docker / Podman) on the user's machine. Adds a
dependency the SMB segment may not have. The Tauri installer
can't bundle a container runtime cleanly.

Reconsider when:
- v0.6+ enterprise tier surfaces (Kubernetes path implies
  container runtime anyway)
- WASM container runtimes mature enough to embed in the daemon
  (wasmtime + WASI Preview 2 is closest)

### WASM plugins

Considered. Rejected because the WASM Python story is still
rough — most third-party Python tools won't compile via Pyodide
or wasi-py without significant porting. Forest agents calling
into WASM would be calling into a smaller subset of available
tooling than MCP gives.

Reconsider when Pyodide / wasi-py reach feature parity with
CPython 3.12+.

### Direct subprocess spawn (no MCP protocol)

Considered. Rejected because the protocol-shape choice is
load-bearing for ecosystem reuse. MCP servers from the
community library work as Forest plugins for free; a custom
subprocess protocol would require every plugin author to write
their own adapter. We'd be re-inventing Anthropic's protocol
without the network effect.

## Tranche plan

| Tranche | Scope | Burst |
|---|---|---|
| **T1** | This ADR (decision record) | 103 (this) |
| **T2** | `~/.forest/plugins/` directory + plugin.yaml schema validator + `fsf plugin install/list/info/uninstall` CLI surface (no daemon-side wiring yet) | 104 |
| **T3** | Daemon-side hot-reload + `/plugins` HTTP endpoints + tool-catalog re-registration. Bridges the new plugin.yaml to the existing mcp_servers.yaml runtime path. | 105 |
| **T4** | Audit-chain integration: 6 new event types emit from the lifecycle code. Tests for replay-fidelity. | 106 |
| **T5** | Registry repo (`forest-plugins`) bootstrap with 3-5 canonical plugins (filesystem, github, postgres, brave-search, slack). Sets the contribution-guidelines pattern. | 107 |

After T5, the operator can:
- `fsf plugin install github-mcp`
- Get prompted for `GITHUB_TOKEN`
- Use Forest agents that read GitHub issues
- See every `plugin_installed` / `plugin_enabled` event in
  `/audit/tail`
- Disable / uninstall the plugin without restart

T2-T5 are all code, but each is small enough to ship as one
burst. T5 doubles as documentation — the canonical plugins
serve as authoring examples for community contributors.

## Consequences

**Positive.**

- Operators get a first-class extension model. No more YAML-
  editing + binary-staging + sha256-computing rituals.
- The MCP ecosystem becomes Forest's tool catalog. Hundreds of
  community-authored servers usable through the same governance
  pipeline as builtin tools.
- Audit-chain coverage of plugin lifecycle is audit-grade —
  forensic questions stay answerable.
- Hot-reload preserves runtime state across plugin changes.
  Critical for the v0.5 prosumer audience who'll experiment
  with plugin combinations.
- Registry-as-Git-repo means zero infra. Forest never operates
  a hosted catalog; the community owns it.

**Negative.**

- New surface to maintain (CLI, endpoints, registry, signing
  infra). Estimated 4 bursts of work for T2-T5 + ongoing
  contribution review for the registry.
- Sandboxing is the MCP protocol's job, not Forest's. A bad
  MCP server can do bad things to its own subprocess but stays
  contained from Forest's runtime — that's by design but
  worth noting.
- Trust boundary moves: today's `mcp_servers.yaml` makes the
  operator pin every binary's sha256. The registry adds a layer
  where the maintainer signs the manifest. Operators trusting
  the registry are trusting one more party. Mitigation: the
  unverified-plugin warning + the existing per-binary sha256
  pin both stay in place; registry signature is additive.
- Distribution complexity. v0.5 Tauri installer needs to bundle
  the `fsf plugin` CLI, register `~/.forest/plugins/`, and
  coordinate with the daemon's lifespan. Not a small thing.

## Open questions (deferred to T2 or later)

- **Sandboxing.** Should Forest layer additional sandbox
  restrictions on top of MCP's process boundary? E.g., macOS
  sandbox-exec, Linux seccomp. Probably yes for v0.6+; v0.5
  trusts MCP's protocol-level isolation.
- **Plugin signing infrastructure.** Cosign / sigstore vs.
  custom Ed25519. Decided in T5 when the registry actually
  needs a signing pipeline.
- **Plugin updates.** Auto-update vs. operator-invoked. v0.5
  goes operator-invoked (via `fsf plugin update`); auto-update
  considered for v0.6+ alongside the daemon's auto-updater
  (ADR-0042 T5).
- **Skill plugins.** This ADR focuses on MCP servers (type:
  mcp_server). Skill plugins (type: skill) — operator-authored
  multi-step workflows packaged for redistribution — share the
  manifest format but need their own runtime story. Defer to a
  follow-up ADR once the MCP path is real.
- **Cross-plugin dependencies.** A plugin that depends on
  another plugin (e.g., postgres-mcp + a postgres-formatter
  plugin). Not in v0.5; revisit if the registry has 50+ plugins.

## References

- ADR-0019 (Tool Execution Runtime — side_effects classification)
- ADR-003X Phase C4 (mcp_call.v1, the foundation this builds on)
- ADR-003X K1 (agent_secrets store)
- ADR-0041 (Set-and-Forget Orchestrator — same control-endpoint
  + audit-emit + hot-reload patterns)
- ADR-0042 (v0.5 Product Direction — D2 SMB thesis demands
  low-friction extension)
- `docs/roadmap/2026-05-04-integrations-strategy.md` (Burst 102
  framing this as the highest-leverage integration move)
- `config/mcp_servers.yaml.example` (existing config layer this
  upgrades)

---

**Decision:** Build the MCP-first plugin protocol per the
architecture above, in 5 tranches. T1 = this ADR. T2 starts
implementation in Burst 104.
