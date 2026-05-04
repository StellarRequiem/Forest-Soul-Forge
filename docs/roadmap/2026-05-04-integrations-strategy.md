# Integrations strategy — v0.5+

Filed: 2026-05-04, post-Burst 101 (ADR-0042 T4 — daemon binary).

ADR-0042 locked the v0.5 product direction (Tauri installer, SMB,
local-first, free-forever, single repo). This doc covers the next
question: **what does Forest plug into / get plugged into?**

The answer determines who can adopt Forest without rewriting their
existing agent stack, and how much of the broader ecosystem
Forest can absorb without re-implementing it.

## Three categories

Integrations come in three shapes. Each has different leverage,
different effort, and different alignment with the v0.5 thesis.

### 1. Inbound — tools Forest agents can use

The runtime already has 53 builtin tools, YAML skill manifests,
and `mcp_call.v1` for the Model Context Protocol. Inbound
expansion absorbs the broader ecosystem's tool surface without
re-writing it.

The high-leverage move here: **MCP-first**. Anthropic's Model
Context Protocol is the de-facto open standard for tool servers
in 2026. There's a growing community library — filesystem,
postgres, slack, github, brave-search, hundreds more. Each one
is a tool surface Forest could pick up for free.

Today `mcp_call.v1` exists but the integration is shallow —
operators have to manually configure server endpoints; no
discovery, no governance integration, no audit-chain coverage of
the third-party tool's actions. Burst 103 / ADR-0043 fixes that.

### 2. Outbound — making Forest agents usable in other stacks

Currently zero. Forest agents are sovereign — they live in their
own runtime and don't export anywhere. This is fine for
local-first solo use, but it caps Forest's reach: someone with
an existing LangGraph / CrewAI stack can't drop a Forest agent
into their flow without rewriting.

The high-leverage move: **LangGraph node export**. LangChain has
the largest mindshare in the agent-orchestration space; if
Forest agents drop into LangGraph as first-class nodes, every
LangChain dev becomes a potential user. Implementation is a
small adapter — wrap a Forest agent's `dispatch_tool` interface
behind LangGraph's `Runnable` protocol.

CrewAI is similar shape; if one wraps export, the other is a
small follow-up.

### 3. Deployment — where Forest can run

Today: local desktop only. ADR-0042 D1 commits to Tauri installer
for v0.5. Beyond that:

- **Docker / docker-compose** for self-hosted multi-user. Easy
  win for prosumer teams who want a shared instance without
  cloud relay.
- **Kubernetes / Helm chart** for v0.6+ enterprise. Conflicts
  with the local-first thesis but enterprise buyers ask for
  this. Defer.
- **GitHub Codespaces / dev-container** for "try Forest in 30
  seconds without installing anything". Low effort, big
  onboarding win.

## Ranked recommendations

| Rank | Integration | Leverage | Effort | Aligned with ADR-0042 | Burst |
|---:|---|:---:|:---:|---|---:|
| 1 | **MCP-first plugin protocol** (ADR-0043) | ★★★★★ | M | Yes — local-first; MCP servers run on the user's machine | 103 |
| 2 | **LangGraph node export** | ★★★★ | S | Yes — outbound only, no SaaS surface | 104 |
| 3 | **Plugin SDK + registry** | ★★★★ | M-L | Yes — opens the runtime without commercial-only features | 105+ |
| 4 | **CrewAI agent bridge** | ★★★ | S | Yes — same as LangGraph posture | 106 |
| 5 | **OpenAI Assistants export** | ★★ | S | Partial — depends on cloud OpenAI; less aligned | later |
| 6 | **AutoGen / Semantic Kernel** | ★★ | M | Same as 5 | later |
| 7 | **Docker / dev-container** | ★★★ | S | Yes — operator-friendly self-hosting | 107 |
| 8 | **Kubernetes Helm chart** | ★ | L | Conflicts with v0.5 thesis | v0.6+ enterprise tier |

## MCP-first thesis (ADR-0043 preview)

ADR-0043 locks the plugin protocol decision. Proposed shape:

```
~/.forest/plugins/
├── installed/
│   ├── filesystem-mcp/      # MCP server, downloaded from registry
│   ├── github-mcp/
│   └── my-custom-tool/      # operator-authored
├── registry-cache.json      # last-fetched plugin catalog
└── secrets/                 # already exists — per-plugin credentials
```

A plugin is a directory with a `plugin.yaml` declaring:

- `type`: `mcp_server` | `tool` | `skill` | `genre`
- `entry_point`: path to executable / Python module
- `capabilities`: which Forest tool keys it provides (e.g. `mcp.github.create_issue`)
- `side_effects`: classification per ADR-0019 — gates governance
- `required_secrets`: prompts on install
- `verified_at`: optional sigstore signature for the registry

Operator workflow:

```bash
fsf plugin install github-mcp        # downloads, validates, stages
fsf plugin list                       # shows installed + enabled state
fsf plugin enable github-mcp          # registers tools into catalog
fsf plugin secrets set github-mcp \\
    GITHUB_TOKEN=ghp_xxx               # secrets piped to the MCP server
```

Daemon hot-reloads the tool catalog without restart. Audit
chain emits `plugin_installed` / `plugin_enabled` /
`plugin_secret_set` so every external capability gets the same
evidence footing as builtin tools. Each MCP call goes through
the standard `ToolDispatcher` — constitution checks, genre
kit-tier ceiling, initiative ladder, approval gates all apply
to MCP-server-provided tools.

Why this shape over alternatives:

- **Plain Python entry-point plugins.** Considered. Rejected
  because Python imports run with full process privileges; an
  untrusted plugin could read the audit chain or rewrite the
  registry. MCP servers run as subprocesses with explicit
  capability declaration; sandboxing is the protocol's job, not
  Forest's.
- **OCI containers per plugin.** Considered. Rejected for v0.5
  because it requires a container runtime (Docker / Podman) on
  the user's machine. Adds a dependency the SMB segment may not
  have. v0.6+ container option could land alongside the
  Kubernetes story.
- **WASM plugins.** Considered. Rejected because the WASM Python
  story is still rough; most third-party Python tools won't
  compile cleanly. Reconsider when Pyodide / wasi-py matures.

## LangGraph node export (preview)

Proposed adapter shape:

```python
# forest_soul_forge/adapters/langgraph.py

from langgraph.graph import StateGraph
from forest_soul_forge.adapters.langgraph import as_langgraph_node

# In the user's existing LangGraph app:
graph = StateGraph(MyState)
graph.add_node("forest_agent", as_langgraph_node(
    instance_id="security_analyst_abc123",
    daemon_url="http://127.0.0.1:7423",
    api_token=os.environ["FSF_API_TOKEN"],
))
graph.add_edge("forest_agent", "next_step")
```

`as_langgraph_node` is a thin function returning a callable that:

1. Takes the LangGraph state dict
2. Maps it to a Forest tool dispatch (typically `llm_think`)
3. Posts to the local daemon
4. Maps the result back to the state shape LangGraph expects

This is ~150-200 LoC of adapter + tests. Shippable in one burst.
The Forest agent is opaque to LangGraph — its constitution,
trait profile, and audit posture stay intact; LangGraph just
sees a node that takes state in and returns state out.

## Plugin SDK + registry (preview)

For v0.5+ once ADR-0043 lands:

- `fsf plugin scaffold <name>` — generates a plugin.yaml stub +
  example MCP server skeleton
- A community registry served from GitHub Pages (no infra cost)
  — each plugin is a directory in a separate `forest-plugins`
  repo. Forest's `plugin install` clones the relevant subdir.
- Verified plugins: registry maintainers sign manifests; client
  verifies signatures before install. Falls back to "unverified
  — install anyway?" for community-authored plugins.

This is a v0.5+ multi-burst arc. Land ADR-0043 first; build the
SDK once we see what plugins people actually want to write.

## What's NOT in this roadmap

- **Any cloud / SaaS surface for Forest itself.** ADR-0042 D1
  commits to Tauri installer; we're not running cloud
  infrastructure. Other people's cloud services (GitHub, Slack,
  etc.) are fine — Forest connects via MCP to their APIs, not
  the other way around.
- **A Forest-specific agent marketplace.** The plugin registry
  is for tools/MCP servers. Selling agents per se requires
  pricing + payment + IP licensing infrastructure that's a
  v1.x enterprise concern.
- **Direct Anthropic / OpenAI / Google integrations beyond what
  ADR-0008 already specifies (provider abstraction).** Forest
  delegates LLM calls to a configured provider; adding more
  provider backends is straightforward but doesn't move the
  integration story.

## Recommended sequence

1. **Burst 102 (this).** This doc + README "Who is this for?"
2. **Burst 103.** ADR-0043 — MCP-first plugin protocol (decision
   record). Locks the design before code lands.
3. **Bursts 104-106.** ADR-0043 implementation tranches:
   - T1: `plugin install` / `enable` / `disable` / `secrets`
     CLI surface + plugin.yaml schema
   - T2: hot-reload of tool catalog when plugins activate
   - T3: audit-chain integration (`plugin_*` events)
4. **Burst 107.** LangGraph node export adapter.
5. **Burst 108+.** CrewAI bridge, Docker / dev-container, plugin
   registry SDK, depending on what gets traction.

The MCP-first arc unblocks Forest absorbing the entire MCP
ecosystem. After that, every integration question becomes
"is there an MCP server for X?" — and the answer is usually yes.
