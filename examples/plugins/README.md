# Forest Soul Forge — example plugins

ADR-0043 T5 (Burst 108). Canonical plugin manifests an operator
can copy as starting points + a community can use as authoring
templates. Each subdirectory holds one `plugin.yaml` showing a
specific shape of plugin.

## Layout

```
examples/plugins/
├── README.md                     # this file — manifest format reference
├── CONTRIBUTING.md               # how to submit to the registry
├── filesystem-reference/
│   └── plugin.yaml               # upstream MCP reference filesystem server
├── brave-search/
│   └── plugin.yaml               # web search via Brave's MCP server
└── forest-echo/
    └── plugin.yaml               # operator-authored custom plugin template
```

The actual binaries are not committed (per `.gitignore`) — each
example points at the canonical upstream binary's sha256, and
the operator stages the real binary alongside the manifest at
install time. Future Burst T6+ may ship a registry that handles
binary download + verification automatically; until then, the
install flow is:

1. Copy the example directory to a working area
2. Download the actual binary from upstream
3. Compute its sha256 + verify it matches the manifest pin
4. `fsf plugin install <copied-dir>`

## Manifest format reference

The full schema is defined in
`src/forest_soul_forge/plugins/manifest.py` (Pydantic model).
Mandatory fields:

```yaml
schema_version: 1                  # always 1 for v0.5
name: my-plugin                    # lowercase hyphens, must match dir name
version: 0.1.0                     # plugin's own version
type: mcp_server                   # mcp_server | tool | skill | genre
side_effects: external             # ADR-0019 classification
entry_point:
  type: stdio                      # stdio | http
  command: ./server                # relative to plugin directory
  sha256: <64-hex-chars>           # binary verification pin
capabilities:                      # tool keys this plugin exposes
  - mcp.my-plugin.do_thing
```

Optional fields (declared per `manifest.py`):

```yaml
display_name: "My Plugin"          # human-readable label
author: "@your-handle"
homepage: "https://github.com/.../my-plugin"
license: MIT
requires_human_approval:           # per-tool gate map
  do_thing: true
  read_thing: false
required_secrets:                  # operator-prompted on install
  - name: api-key
    description: "PAT with read scope"
    env_var: MY_PLUGIN_API_KEY
verified_at: "2026-05-04T12:00:00Z"  # registry signature timestamp
verified_by_sha256: <64-hex-chars>   # registry maintainer signature
```

## Plugin types

`mcp_server` — the canonical type for v0.5. Spawns an MCP server
subprocess; tools become accessible through `mcp_call.v1`. All
three example plugins are this type.

`tool` / `skill` / `genre` — reserved namespaces. Manifests parse
but the runtime doesn't yet register them. Follow-up ADRs will
specify their runtime contracts.

## Side-effects classification

Mirrors ADR-0019. Affects governance gating:

| `side_effects` | Default approval gate | Use when… |
|---|---|---|
| `read_only` | none | Plugin does no I/O outside its own state |
| `network` | none | Plugin makes HTTP calls but doesn't mutate external services |
| `filesystem` | per-call | Plugin writes files |
| `external` | per-call | Plugin calls third-party APIs that mutate state |

The `requires_human_approval` map can override per-tool: a
network-tier plugin can still gate specific tools that do
mutating actions.

## SHA256 verification

Forest verifies the entry-point binary's sha256 against the
manifest's pinned value before EVERY launch. This is the
typosquat / supply-chain-swap defense from ADR-003X Phase C4
§threat-model addendum.

To compute a sha256 for a binary on macOS / Linux:

```bash
shasum -a 256 ./server
# or
sha256sum ./server
```

Forest verifies the lowercase-hex form. A mismatch:

- Logs `plugin_verification_failed` to the audit chain
- Refuses to spawn the server
- `fsf plugin verify <name>` exits 1

Operators resolving a mismatch must either:

- Update the manifest's `entry_point.sha256` to the new value
  (trusting the new binary), OR
- Restore the original binary

Forest never silently runs an unverified binary.

## Capabilities + namespacing

Capabilities are tool keys the plugin contributes to the
catalog. Convention: `mcp.<plugin-name>.<tool-name>`.

```yaml
capabilities:
  - mcp.github-mcp.list_issues
  - mcp.github-mcp.create_issue
```

Forest's bridge (Burst 107 / ADR-0043 T4.5) strips the
`mcp.<plugin-name>.` prefix when populating the runtime
registry, so the MCP server sees its own tool names without
the namespace. Plugins using non-conventional names pass
through verbatim.

## Required secrets

Each `required_secrets` entry pairs a human-meaningful `name`
with the `env_var` the plugin's binary expects. Forest's secret
store (ADR-003X K1) holds the value; `mcp_call.v1` injects it
into the spawned subprocess's environment.

```yaml
required_secrets:
  - name: github-pat
    description: "GitHub Personal Access Token; repo:read scope minimum"
    env_var: GITHUB_TOKEN
```

When implementing a plugin runtime that reads `GITHUB_TOKEN`,
operators set it via:

```bash
fsf plugin secrets set <plugin-name> GITHUB_TOKEN=<value>
```

(The `fsf plugin secrets` CLI lands in a follow-up burst —
today operators set them directly via the existing
`agent_secrets` interface from ADR-003X K1.)

## Authoring a new plugin

1. Pick a name and type. v0.5 only ships runtime support for
   `type: mcp_server`.
2. Find or write the MCP server you want to wrap. Anthropic
   maintains a reference set at
   <https://github.com/modelcontextprotocol/servers>.
3. Compute the binary's sha256.
4. Copy `examples/plugins/forest-echo/` as a starting template
   and edit the manifest.
5. Stage in your plugin root:

   ```bash
   cp -r my-new-plugin/ ~/.forest/plugins/installed/
   # OR for dev iteration:
   fsf plugin install /path/to/my-new-plugin --plugin-root ~/.forest/plugins
   ```

6. Restart the daemon (or `POST /plugins/reload` if the
   daemon's already up) and the new tools should appear in
   `GET /plugins`.

## Submitting to the public registry

See `CONTRIBUTING.md` in this directory for the registry
contribution flow. Until the registry repo (forest-plugins)
exists, plugin authors install locally + share the directory
out-of-band.

## References

- ADR-0043 — MCP-first plugin protocol (full design)
- `src/forest_soul_forge/plugins/manifest.py` — Pydantic schema
  authority
- `config/mcp_servers.yaml.example` — legacy YAML registry path
  (still supported; plugins override it on name conflict)
- ADR-003X Phase C4 — `mcp_call.v1` baseline + threat model
