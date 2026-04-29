# ADR-003X — Open-Web Tool Family

- **Status:** Proposed
- **Date:** 2026-04-28
- **Supersedes:** —
- **Related:** ADR-0019 (tool execution runtime — every open-web tool ships
  through the dispatcher with audit + approval), ADR-0021 (role genres — this
  ADR adds three new genres `web_observer/researcher/actuator`), ADR-0022
  (memory subsystem — `consented` scope is the natural fit for fetched
  content), ADR-0025 (threat model v2 — the open-web addendum lives here),
  ADR-0027 (memory privacy contract — fetched content stays under per-agent
  consent), ADR-0030 (Tool Forge — every primitive ships through the same
  forge pipeline), ADR-0031 (Skill Forge — chained web actions are skill
  manifests), ADR-0033 (Security Swarm — the precedent for genre-as-tier).

## Context

The forge ships agents that act on the local box. Every existing tool
either reads local state (logs, processes, files), writes local state
(memory, soul artifacts), or escalates within an agent lineage
(`delegate.v1`). There is **no path** for an agent to reach beyond
`127.0.0.1`.

This is a deliberate posture for the security tier — `security_high`'s
constitution structurally forbids non-local providers. But the same
posture currently rules out every realistic non-defensive use case the
operator has asked for: pulling open-source intelligence, pinging an
upstream MCP server, driving a SaaS UI to triage a ticket queue,
fetching an RFC to compare against an internal config.

The brief is to add an **open-web plane** that is symmetric to the
defensive plane in design discipline:

```
                         Defensive plane (ADR-0033)
  security_low → security_mid → security_high
  (read local)   (correlate)    (gated act, hardware-bounded)

                         Open-web plane (this ADR)
  web_observer → web_researcher → web_actuator
  (fetch read)   (multi-source)  (drive a UI, gated act)
```

Same audit chain. Same approval queue. Same memory privacy contract.
Same forge pipeline. Three new genres, three new primitives, one new
infrastructure piece (per-agent encrypted secrets store), one new
operator-facing matcher tool (`suggest_agent.v1`). Nothing in the
existing platform changes.

## What this ADR is **not**

Four claims must be retired up front:

1. **Not a general-purpose browser bot.** `browser_action.v1` drives
   one tab in headless Playwright against domains the operator has
   pre-allowlisted in the agent's constitution. It is not a crawler,
   not a captcha solver, not an anti-detection harness. If an evaluator
   asks "can it scrape Twitter at scale" the answer is no — by design.

2. **Not an MCP marketplace.** `mcp_call.v1` lets an agent call an
   MCP server the operator has pre-registered in `config/mcp_servers.yaml`.
   It does not auto-discover servers, does not install third-party
   servers, does not run untrusted code. The operator's MCP registration
   is itself an audit event with the server's full URL + tool list.

3. **Not a credential manager.** The per-agent secrets store holds
   tokens for tools the agent uses (an OpenAI API key for that
   agent's mcp_call traffic, a session cookie for that agent's
   browser_action target). It is not a password vault, not a
   keychain replacement, not a TOTP generator. Master key comes from
   the operator's environment (env var, optionally backed by macOS
   Keychain) — Forest does not store the master key.

4. **Not a defense against prompt injection from fetched content.**
   This is the headline new attack surface. We mitigate (per-agent
   allowlists, summary-only memory persistence, per-tool approval
   gates) but we do not eliminate. §Threat model addendum is explicit
   about residual risk.

## Decision

Add an open-web plane composed of:

- **One infrastructure piece**: per-agent encrypted secrets store
  (Phase C1) — the foundation everything else depends on.
- **Three primitives**, each a forged tool through the standard
  pipeline: `web_fetch.v1`, `browser_action.v1`, `mcp_call.v1`.
- **One operator-facing tool**: `suggest_agent.v1` — given a task
  description, returns ranked candidate agents. Critical for
  unblocking the "I have many roles, which one fits this task"
  problem that gets worse as the catalog grows.
- **Three new genres**: `web_observer`, `web_researcher`,
  `web_actuator` — symmetric to the security tiers in escalation
  shape (read → reason → act).

Existing infrastructure carries the weight. No new subsystems.

### Three new genres

Added to `config/genres.yaml`:

```yaml
  web_observer:
    description: |
      Read-only web fetchers. Pull RFCs, status pages, public APIs,
      RSS feeds. Cannot drive a browser, cannot call MCP servers
      that mutate state. Findings flow to web_researcher via memory
      lineage scope.
    risk_profile:
      max_side_effects: network
      memory_ceiling: lineage
      provider_constraint: null
    default_kit_pattern: observer
    trait_emphasis: [research_thoroughness, evidence_demand, suspicion]
    spawn_compatibility: [web_observer, web_researcher]

  web_researcher:
    description: |
      Multi-source synthesizers. Fetch from N allowlisted hosts,
      cross-reference, summarize. Can call read-only MCP tools.
      Memory ceiling is `consented` so research artifacts can be
      shared with downstream agents on per-event grant. Cannot
      drive a browser; cannot call mutating MCP tools.
    risk_profile:
      max_side_effects: network
      memory_ceiling: consented
      provider_constraint: null
    default_kit_pattern: researcher
    trait_emphasis: [research_thoroughness, lateral_thinking, evidence_demand]
    spawn_compatibility: [web_researcher, web_actuator]

  web_actuator:
    description: |
      Drive a real UI or call mutating MCP tools. Always gated:
      every browser_action and every mutating mcp_call requires
      operator approval per call. Headless by default; headful only
      via FSF_BROWSER_HEADFUL=true (debug). Per-agent constitution
      pins the allowlisted domains + the secret_names the agent
      may read from the secrets store.
    risk_profile:
      max_side_effects: external
      memory_ceiling: consented
      provider_constraint: null
    default_kit_pattern: actuator
    trait_emphasis: [caution, evidence_demand, double_checking]
    spawn_compatibility: [web_actuator]
```

### Per-agent encrypted secrets store (C1)

New table in the registry:

```sql
CREATE TABLE agent_secrets (
    instance_id      TEXT NOT NULL,
    name             TEXT NOT NULL,            -- e.g. "openai_api_key"
    ciphertext       BLOB NOT NULL,            -- AES-256-GCM
    nonce            BLOB NOT NULL,
    created_at       TEXT NOT NULL,
    last_revealed_at TEXT,
    PRIMARY KEY (instance_id, name)
);
```

API on the registry:

- `set_secret(instance_id, name, value)` — encrypts with the master
  key, persists, audits `secret_set` (no value, only `(instance_id, name)`).
- `get_secret(instance_id, name)` — decrypts, audits `secret_revealed`.
- `list_secrets(instance_id)` — names only, never values.
- `delete_secret(instance_id, name)` — audits `secret_revoked`.

Master key:

- Source 1: `FSF_SECRETS_MASTER_KEY` env var (32-byte base64).
- Source 2 (macOS): macOS Keychain via `security find-generic-password`.
  The daemon reads the key once at lifespan and holds it in process
  memory; never written to disk.
- If neither source is set, the secrets subsystem is **disabled** —
  open-web tools that depend on a secret refuse cleanly with
  "no secrets master key configured." Daemon stays up; defensive
  plane unaffected.

ToolContext gains a `secrets` accessor (lazy — no decrypt unless the
tool actually reads). Per-agent constitution lists the secret names
the agent is permitted to read; runtime refuses unlisted names.

### Three primitives

**`web_fetch.v1`** (Phase C2)

```yaml
name: web_fetch
version: '1'
side_effects: network
inputs:
  url: { type: string, format: uri }
  method: { enum: [GET, POST, HEAD], default: GET }
  body: { type: string, optional: true }
  auth_secret_name: { type: string, optional: true }
output:
  status: integer
  body: string                # truncated to N kb; rest goes to attachment
  body_truncated: boolean
  content_type: string
  url_final: string           # after redirects
constraint:
  per_agent_host_allowlist: required   # constitution lists permitted hosts
audit:
  - host (URL parsed)
  - method
  - status
  - body NOT logged (too noisy + privacy)
```

The simplest primitive. Lower-bound the open-web surface.

**`browser_action.v1`** (Phase C3)

```yaml
name: browser_action
version: '1'
side_effects: external          # always gated
inputs:
  url: { type: string, format: uri }
  actions: { type: array }      # [{type: click, selector: ...}, ...]
  screenshot: { type: boolean, default: true }
output:
  url_final: string
  screenshot_path: string       # in data/browser_screenshots/
  console_log: string           # captured browser console
constraint:
  per_agent_host_allowlist: required
  requires_human_approval: true # always — this is "external"
deps: playwright >= 1.40
```

Heaviest primitive — pulls in Playwright. Headless by default; headful
when `FSF_BROWSER_HEADFUL=true` for debugging. Browser context is
ephemeral (one context per call) so cookies don't bleed between
agents unless explicitly persisted via the secrets store.

**`mcp_call.v1`** (Phase C4)

```yaml
name: mcp_call
version: '1'
side_effects: external          # default; per-server overrides in config
inputs:
  server_name: string           # must match config/mcp_servers.yaml
  tool_name: string
  args: { type: object }
output:
  result: { type: object }      # passthrough from MCP server
  isError: boolean
constraint:
  per_agent_server_allowlist: required
  requires_human_approval: depends_on_per_server_config
audit:
  - server_name
  - tool_name
  - args_digest (sha256, not raw)
  - result_digest
  - isError
```

`config/mcp_servers.yaml` is operator-curated. Each entry pins:

```yaml
  github:
    url: stdio:./mcp-servers/github
    side_effects: external
    requires_human_approval: true
    allowlisted_tools: [list_issues, get_pull_request, search_code]
```

Auto-discovery is explicitly out of scope. The operator types the
server config; Forest verifies the manifest signature (Phase C5,
deferred) before letting agents call it.

### `suggest_agent.v1`

Operator-facing, not agent-facing. Given a task description, returns
ranked agents whose role + traits + (eventually) skill history fit.
v1 is a simple BM25 over (role descriptions + agent_name + soul.md
voice section). Future v2 adds skill-history weighting.

```yaml
name: suggest_agent
version: '1'
side_effects: read_only
inputs:
  task: string                  # natural language
  top_k: { default: 5 }
  filter:
    genre: { optional: true }
    status: { default: active }
output:
  candidates:
    - instance_id: string
      score: float
      reason: string            # one-sentence "why this agent"
```

Critical for catalog scaling. Once Phase I lands ~30 new roles, an
operator can't keep them all in their head — `suggest_agent` is the
tab-completion equivalent.

## Phases

| # | Deliverable | Notes |
|---|---|---|
| C1 | Per-agent encrypted secrets store | Foundation. Everything else depends on this. |
| C2 | `web_fetch.v1` + tests | Smallest primitive. Lowest risk. Useful on its own. |
| C3 | `browser_action.v1` + Playwright bring-up | Heaviest. Pulls Playwright into the install path; bumps .zip size. |
| C4 | `mcp_call.v1` + `config/mcp_servers.yaml` | The integration multiplier. |
| C5 | MCP server signature verification | Defer; out of scope for the first cut. |
| C6 | `suggest_agent.v1` | Ships once two new roles exist to rank between. |
| C7 | Three new genres + per-genre default kits | Wires the primitives into the genre engine. |
| C8 | Synthetic-incident demo | Mirror of Phase E for the open-web plane: a `web_researcher` agent fetches an RFC, correlates against a local config, escalates to a `web_actuator` that opens a Linear ticket via mcp_call. |

C1 → C2 → C3 → C4 → C6 → C7 → C8. C5 deferred. Estimated 5-7 weeks.

## Threat model addendum

Open-web is the largest single-step expansion of attack surface this
project has ever made. Three new threat classes:

### 1. Prompt injection from fetched content (HIGH)

A page Forest fetches contains `<!-- ignore previous instructions; do X -->`.
The agent's LLM-driven reasoning may follow it.

**Mitigations:**
- Fetched content is **never directly written** to memory — it's
  routed through a summarization pass first (operator can disable
  via `--raw` flag, audited).
- `mcp_call.v1` and `browser_action.v1` results that contain
  text-mimicking-instructions are flagged in the audit chain via a
  cheap content-type heuristic.
- Per-tier approval graduation (web_actuator gates everything beyond
  read_only) means injection-driven actions still hit the operator
  before execution.

**Residual risk:** an injection that *biases* an agent's reasoning
without triggering an action is undetectable by the runtime. The
operator's audit-chain review is the backstop.

### 2. Secret exfiltration via tool composition (MEDIUM)

An agent reads `openai_api_key` via the secrets store, then sends it
out via `web_fetch.v1` or as `mcp_call.v1` args.

**Mitigations:**
- Per-agent constitution lists allowed secret names AND allowed
  hosts/servers. The runtime enforces both.
- `secret_revealed` audit event fires *before* the tool runs; pairing
  it with the next `tool_call_dispatched` shows where the secret went.
- Secrets are decrypted into ToolContext for one call, never cached
  cross-call.

**Residual risk:** an agent permitted to read X and call host Y can
still send X to Y if both are independently legitimate. The audit
trail is the operator's evidence; runtime does not block.

### 3. Cross-agent secret access via `delegate.v1` (LOW)

A `web_actuator` agent delegates to a `web_observer`; could the
observer pick up the actuator's secrets?

**Mitigations:**
- The secrets store is **per `instance_id`**. `delegate.v1` switches
  the ToolContext to the target agent's identity; the target reads
  its own secrets, not the caller's.
- `agent_delegated` event records the delegation; secret_revealed
  events under the target's instance_id are searchable independently.

**Residual risk:** none structural. This was an explicit threat model
question, addressed by design.

### What we don't defend against (consistent with ADR-0025)

- Compromised LLM provider (the model itself adversarial).
- Operator-provided malicious MCP server (operator approves the
  config; trust boundary is at registration).
- Browser zero-days in Playwright (defense in depth: per-agent
  allowlist + ephemeral context limit blast radius, but a Playwright
  CVE is operator's patching responsibility).

## Consequences

### What we gain

- Forest can do open-web work — RFC pulls, MCP integrations, ticket
  triage, public-data correlation. Scope-doubles realistic use cases.
- The same audit chain + approval queue + memory contract apply
  uniformly. No new "trust me" surface.
- `suggest_agent.v1` becomes a tractable operator UX as the role
  catalog grows from 14 → 30+.
- Per-agent secrets store unblocks future product MCP adapters
  (Wazuh, Suricata, Defender) — they all need credentials.

### What we accept

- **Larger attack surface.** Prompt injection becomes a real risk.
  Mitigated structurally by approval gating and audit visibility, but
  the operator has to actually look at the chain when reviewing an
  agent's behavior.
- **Heavier install.** Playwright bumps the .zip + bootstrap by
  ~80MB. Acceptable cost for the unlock.
- **More external deps.** Playwright + (eventually) MCP server
  binaries. Pin versions in `pyproject.toml`; CVE management is on
  the operator.
- **Genre-engine surface grows.** 13 genres total. Stays manageable
  but the genre dropdown gets crowded; UX wants a category filter
  (defer).

## Alternatives considered

**A: Just MCP** (skip web_fetch + browser_action).
Rejected because most operator use cases (RFC pull, public API
fetch) don't have an MCP server and shouldn't need one. `web_fetch`
is the lowest-friction path.

**B: Just browser_action** (one tool, drive everything via UI).
Rejected because driving a UI for a JSON API is grotesque overkill,
and Playwright as the only attack-surface tool concentrates risk in
a single dependency.

**C: External secrets manager** (1Password, AWS Secrets Manager).
Rejected for v1 because it imports a third-party trust boundary and
violates the local-first thesis. Per-agent encrypted store is small
and self-contained. Future ADR can layer external managers on top
once the per-agent contract is stable.

**D: Single `external_call.v1` super-tool** (one tool, dispatch
internally to fetch / browser / mcp).
Rejected because each surface has different threat models, different
allowlists, different approval policies. Three primitives keeps the
constraint resolver honest.

## Sign-off

Open for review. Acceptance criteria: phases C1 through C8 ship; the
synthetic-incident demo (C8) runs end-to-end against a real upstream;
the threat-model addendum holds up under one round of red-team
review.
