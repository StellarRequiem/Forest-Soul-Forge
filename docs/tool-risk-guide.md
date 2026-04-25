# Tool risk guide

Post-ADR-0018, Forest agents have **hands**. This guide is for the operator deciding what to put in those hands.

The Forge will let you ship an agent with any combination of tools the catalog defines. That's powerful and that's the point — a network_watcher that can't query packets is decoration. But the same flexibility means a misconfigured spawn can give an agent capabilities its trait profile can't safely steward. Read this before you tick `tools_add` for anything you don't already understand.

This guide is **operational guidance**, not architecture. The architectural decision is in `ADR-0018`. The risk policy that maps trait values to per-tool constraints lives in `config/tool_constraint_policy.yaml` (T2.5+). This document is the human-readable companion.

## Risk tiers — mapped to `side_effects` in the catalog

Every catalog entry declares a `side_effects` value. That value determines the blast radius if the tool is misused — by a poorly-prompted LLM, a hostile prompt-injection, or a buggy invocation.

### `read_only` — observes existing state, produces no external effect

Examples in the shipped catalog: `packet_query.v1`, `flow_summary.v1`, `log_grep.v1`, `log_aggregate.v1`, `baseline_compare.v1`, `correlation_window.v1`, `timestamp_window.v1`.

**Blast radius:** disclosure within the data the tool can already see. A misused `log_grep` returns log lines the operator has already authorized the agent to read. The threat is mainly information overload (returning too much) and inference (combining read-only views to deduce something the operator didn't intend the agent to know).

**Default policy (T2.5):** no human approval required. Generous per-session call cap (typically 1000+).

**Things that look read_only but aren't:** see "Dual-purpose tools" below. A search tool that hits an external API is `network`, not `read_only`, even if you call it "search".

### `network` — reaches out to a service over the network with payload you control

Examples in the shipped catalog: `dns_lookup.v1`.

**Blast radius:** anything the agent can encode in the request payload becomes visible to the target service and to anyone observing the path. A DNS query for `EXFILTRATED-SECRET-DATA.attacker-domain.com` is a working covert channel. Even read-style network tools (HTTP GET) can carry secrets in headers, query strings, or DNS subdomain encoding.

**Default policy (T2.5):**
- High-caution agents (caution >= 80) → require human approval per call
- Medium-caution agents → cap at low call counts per session, log every call to the audit chain
- Low-caution agents → still cap, still log, no approval required

**Specific concerns:**
- **Outbound URLs in user-provided payloads.** If the tool builds its destination from agent-controlled input, an injected prompt can redirect the call. Lock destinations to allowlists in the tool's implementation.
- **Resolver leaks.** DNS lookups go to whatever resolver the system is configured for. Treat DNS as "any third party who watches the resolver path can see what the agent asked about."
- **Rate-limited APIs.** If the agent burns the rate limit on a misuse, the operator gets locked out at the same time.

### `filesystem` — reads or writes the host filesystem

**Not yet shipped in the catalog** — placeholder in case T5 introduces tools like `read_file.v1` or `write_artifact.v1`.

**Blast radius:** depending on the tool's path scoping, anywhere from "one constrained directory" to "the full disk the daemon process can see." Write tools are higher risk than read tools. Tools that write under paths derived from agent input (e.g., a "save report" tool that takes a filename) are extremely high risk — path traversal attacks are a basic exploitation vector.

**Default policy:** any filesystem tool is human-approval-required regardless of trait values, unless the operator explicitly overrides. Path scoping is in the tool implementation, not in the agent's trust budget.

### `external` — sends, posts, executes, or otherwise produces durable state outside the agent's process

**Not yet shipped in the catalog** — placeholder for tools like `post_message.v1`, `create_ticket.v1`, `send_email.v1`, `execute_command.v1`.

**Blast radius:** durable. An external action is hard or impossible to roll back. A `send_email.v1` invocation reaches its recipient before the operator notices the misuse; a `create_ticket.v1` invocation produces an artifact in your tracking system that pollutes downstream metrics.

**Default policy:** require human approval, always. No trait-value override should bypass this. The operator's caution-level setting applies to whether the daemon prompts before generating the suggested action; whether the action actually fires is controlled by the human in front of the screen, period.

## By-archetype recommendations

The catalog ships standard kits per role. These are the **minimum viable tool surface** for an agent of that role to do its declared job. Adjust by understanding the trade-off, not by reflex.

### `network_watcher`

**Standard kit (in catalog):** `packet_query.v1`, `flow_summary.v1`, `dns_lookup.v1`, `timestamp_window.v1`.

**Safe to add:** any other `read_only` network observation tool — packet aggregations, header parsers, protocol dissectors. Keep `dns_lookup.v1` if you want active probing; remove it if the agent is purely passive.

**Be cautious adding:** any tool that talks back to the network actively — banner grabs, traceroute, nmap-style scans. These straddle `network` and probing-as-attack territory. A network_watcher with a probe tool can become a network_attacker under prompt injection.

**Don't add (without explicit policy):** any `external` tool. A network_watcher's job is observation. If you find yourself wanting to give it a `post_alert.v1`, the right move is usually to spawn a separate `alert_router` archetype that does only that.

### `log_analyst`

**Standard kit:** `log_grep.v1`, `log_aggregate.v1`, `timestamp_window.v1`.

**Safe to add:** other `read_only` log tools — log decoders, structured-event parsers, log-source enumerators.

**Be cautious adding:** anything that lets the agent tell the log system to delete or rotate. That's now in `filesystem` or `external` territory.

**Don't add (without explicit policy):** anything that writes back into the log store. A log_analyst that writes log entries is a log_forger.

### `anomaly_investigator`

**Standard kit:** `packet_query.v1`, `log_grep.v1`, `baseline_compare.v1`, `correlation_window.v1`, `timestamp_window.v1`.

**Safe to add:** any cross-source `read_only` tool — alert history queries, ticket history queries, baseline metrics from other systems.

**Be cautious adding:** anything that writes a finding to a durable store ("create ticket", "open incident"). These are `external` and should be a separate `incident_responder` archetype rather than collapsed into investigator.

**Don't add:** active probes (network_watcher's caution). An investigator that can probe is harder to scope safely than an investigator that can only read.

## Dual-purpose tools — looks low-risk, isn't

These are tool *patterns* (not specific catalog entries) that are commonly mis-categorized. If you're adding a tool of one of these patterns, treat it as one tier higher than its declared `side_effects`.

### Search APIs masquerading as read-only

A `web_search.v1` tool that hits Google/Bing/etc. is **`network`**, not `read_only`. The query itself is data leaving the machine. Crafted queries can encode secrets:

```
search query: "site:attacker.com /BASE64_OF_LEAKED_DATA"
```

The query sits in the search engine's logs forever and an attacker who controls a domain in the search results can recover the encoded data via referrer leakage.

### Lookup tools that allow free-form input

`dns_lookup.v1` is in the catalog as `network` for exactly this reason. Any tool that takes a free-form name + makes a network call with it inherits the same covert-channel risk:

- `whois_lookup` (network)
- `reverse_dns` (network)
- `geoip_lookup` (likely network — most implementations hit a service)

If the implementation uses an entirely local database (e.g., MaxMind installed locally), the side_effects can drop to `read_only` — but verify the implementation, don't trust the name.

### Read tools that aggregate sensitive data

A `log_grep` that returns a single line is read-only. A `log_grep` that returns 10,000 lines containing customer email addresses is a data export. The line between observation and exfiltration is "how much, and where does it go next." Per-call limits in the input_schema (the `limit` field on `log_grep.v1`, capped at 10000) are the architectural answer; the policy answer is: cap at lower numbers for higher-caution agents, and audit-log every call regardless.

### "Convert" / "format" / "render" tools that hit external services

A tool called `markdown_to_pdf.v1` that runs locally is `read_only`. A tool with the same name that POSTs the markdown to a hosted rendering service is `network`, and the markdown content (potentially sensitive) is now in that service's logs. Same name, very different risk. **Always read the implementation, not just the description.**

### Time-window helpers that make the wrong call

`timestamp_window.v1` is shipped as `read_only` because it takes natural-language windows and converts them to absolute timestamps — pure helper. But a hypothetical `timestamp_relative_to_event.v1` that queries a timeline service to anchor "the day before the breach" is `network`, even if the operator description sounds the same.

### LLM tools that wrap other tools

Any tool that delegates to a model invocation (e.g., a `summarize_logs.v1` that pipes log lines through a frontier provider for summarization) is at minimum `network` in its side_effects (because the log lines leave the machine and reach the provider). **The "log lines leave the machine" property is the load-bearing one** — a local-LLM-backed summarizer is `read_only`, a frontier-LLM-backed one is `network` and arguably `external`.

ADR-0008's local-first posture has direct teeth here: when adding LLM-derived tools to an agent, route them through `task_kind` so the operator can pick the local provider for sensitive workloads. Don't bake a frontier provider into the tool's implementation.

## High-risk tool patterns — always require approval

If a tool fits any of these descriptions, it should require human approval per call regardless of the agent's trait values:

1. **Anything that writes to a shared resource the operator depends on.** Ticket systems, message queues, alerting systems, configuration stores.
2. **Anything that executes code or shell commands**, even in a sandbox. Sandboxes have escape histories.
3. **Anything that sends a message to a human** (email, Slack, SMS, push notification, ticket comment). The recipient can't easily distinguish "the agent decided to" from "the operator decided to."
4. **Anything that mutates external accounts** — creating, modifying, or deleting users, permissions, API keys, billing items.
5. **Anything that downloads + executes** (a `pip install`, a `curl | sh`-style helper, a "run this script from a URL" pattern). These are RCEs by design.
6. **Anything that bridges to a payment system** — even read-only price lookups, when paired with any account-mutating tool, become a path to ordering things on the operator's behalf.
7. **Anything that publishes** to a public surface — social media, public webhooks, public S3 buckets, public Git repos. The agent's output becomes the operator's reputation.

## Tool selection checklist

Before adding a new tool to an agent — whether via `tools_add` at birth or a new archetype default — answer these:

1. **What's the worst single-invocation outcome** if this tool is misused? Disclosure? Cost? Reputation? Service outage? An invocation chain to something worse?
2. **Where does the input come from?** If any field is agent-controllable (it is, by definition, in this architecture), what's the input validation in the tool's implementation? Path traversal? URL allowlisting? Length limits?
3. **Where does the output go?** Returned to the agent only, or also written somewhere durable? If durable, who can see it later, and is that what the operator wants?
4. **What's the rate limit on the underlying resource?** If the tool talks to an API with a 1k-calls-per-day budget, can the agent exhaust that budget in one bad session?
5. **Is there a less-capable tool that satisfies the same need?** A read-only summary tool is almost always preferable to a read+write tool for the same domain. Pick the smaller surface.
6. **Does the trait profile actually call for this?** A high-caution / high-evidence_demand agent doesn't need a tool that produces fast-but-shallow results. Match the tool's character to the agent's character.
7. **What audit-event shape does invocation produce?** Tools that emit useful audit events (tool_invoked / tool_failed with parameters and resolved destination) are auditable; tools that don't are not. Prefer auditable.

## Recommended starting posture

For a new operator unfamiliar with the agent's domain:

- **Start with the standard archetype kit unmodified.** Don't add any tools on the first birth. Watch what the agent does with what it has. Spend at least one full investigation cycle observing before expanding capability.
- **Add tools one at a time, not in bundles.** A bundle of three new tools means three new failure modes simultaneously; a bundle of one means one.
- **Re-read the shipped tool's `description` field before adding it.** The description in the catalog is authoritative for what the tool actually does — it can disagree with what the tool's name suggests, and the description wins.
- **Keep `enrich_narrative=true` on for high-stakes agents.** The Voice section is the operator's window into how the agent will use its tools. Reading it before deploying the agent costs nothing and catches mismatches.
- **Audit-tail review.** After every birth, spend two minutes looking at the agent's `agent_created` event in the audit chain. Does the resolved tool list match what you intended? Does the constitution's per-tool constraints look reasonable? If not, archive and re-birth before deploying.

## When in doubt, archive and re-birth

ADR-0006 makes the artifact tree authoritative; the registry rebuilds from artifacts. That gives you a free property: **archiving a misbuilt agent and birthing a corrected one costs less than fighting a deployed agent's behavior.** If you find yourself reaching for `regenerate-voice` to paper over a tool surface that was never right, stop and re-birth instead. The audit chain captures both events; the lineage stays clean.

## Per-archetype quick reference

|              Archetype | Standard kit (this commit)                                                    | Safe to add                                  | Be cautious                  | Don't add (no explicit policy)        |
| ---------------------: | :---------------------------------------------------------------------------- | :------------------------------------------- | :--------------------------- | :------------------------------------ |
|       `network_watcher` | packet_query, flow_summary, dns_lookup, timestamp_window                       | other read-only network observation          | active probes (banner grabs) | any `external` tool                    |
|         `log_analyst`   | log_grep, log_aggregate, timestamp_window                                      | other read-only log tools                    | log rotation / deletion      | log writes                            |
| `anomaly_investigator` | packet_query, log_grep, baseline_compare, correlation_window, timestamp_window | cross-source read-only tools                 | active probes                | ticket / incident creation tools      |

## See also

- `ADR-0018` — the architectural decision for the catalog model and version-pinning behavior.
- `ADR-0008` — local-first model provider, which has direct implications for any LLM-wrapped tool.
- `config/tool_catalog.yaml` — the canonical source. Read entries before recommending them.
