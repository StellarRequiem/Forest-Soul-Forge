# 2026-04-29 — G-track + K-track marathon audit

## TL;DR

In one session: closed Phase D+E aftermath, shipped the entire ADR-003X
open-web tool family (C1-C7), and shipped six K-track parallel features
(K1-K6) that operationalize the open-web work for real-world use. Net:
**12 new feature commits + 4 test/doc commits + 3 audit/STATE commits**,
all on `main`, pushed to origin in three batches. **Zero regressions on
the existing 21-skill Phase D security swarm chain.** Live tests pass
end-to-end against the running daemon for K4, G6, K5, and K6.

## What shipped

### G-track — ADR-003X Open-Web Tool Family

| Phase | Commit  | Deliverable                                                                |
|-------|---------|---------------------------------------------------------------------------|
| C1    | f5cb8fe | `agent_secrets` table + AES-256-GCM SecretsAccessor (master key from env) |
| C2    | 2f67d5b | `web_fetch.v1` — HTTP GET/POST + per-agent host allowlist + secrets auth  |
| C3    | f75e686 | `browser_action.v1` — chromium-only Playwright, ephemeral context per call |
| C4    | 8f7f0b9 | `mcp_call.v1` — JSON-RPC over stdio + SHA256 binary verification          |
| C6    | 952bdc2 | `suggest_agent.v1` — pure-python BM25 over (role + name + genre)          |
| C7    | 952bdc2 | three new genres: `web_observer` / `web_researcher` / `web_actuator`       |

Only C5 (Sigstore-style provenance for MCP servers) and C8 (synthetic-
incident open-web demo) remain from the original ADR-003X plan.

### K-track — operator-facing parallels

| Phase | Commit  | Deliverable                                                                |
|-------|---------|---------------------------------------------------------------------------|
| K1    | bdbdb8d | `memory_verify.v1` (Iron Gate equivalent) + `memory_verifications` table v9 |
| K2    | 2ba39a5 | `POST /audit/ceremony` — operator-emitted ceremony event type              |
| K3    | e72cf65 | `GET /audit/stream` — SSE with Last-Event-ID resume                        |
| K4    | 36724e6 | Triune spawn template — Heartwood/Branch/Leaf seeds + delegate.v1 enforcement |
| K5    | bda12a3 | `fsf chronicle` CLI — HTML+MD export, sanitized-by-default payloads        |
| K6    | 7f1a9fb | `hardware_binding` constitution field + dispatcher quarantine + unbind     |

### Live-test harnesses (regression assets)

| Commit  | Script                       | Verifies                                                |
|---------|------------------------------|---------------------------------------------------------|
| f8031ed | `live-test-k4.command`       | triune mechanics end-to-end against running daemon      |
| c7c0311 | `live-test-g6-k5.command`    | suggest_agent + chronicle export against running daemon |
| (uncommitted as of audit) | `live-test-k6.command`       | hardware binding + quarantine + unbind end-to-end (11/11 steps green on real Mac fingerprint) |

## Numbers

| Metric                       | Before today | After today |
|------------------------------|--------------|-------------|
| Built-in tools registered    | 31           | **36**      |
| Skill manifests shipped      | 21           | **24**      |
| Genres                       | 10           | **13**      |
| Schema version               | v7           | **v9**      |
| Audit event types            | 30+          | **35+**     |
| `.command` operator scripts  | 19           | **22**      |
| Builtin docs/audits filed    | 1            | **3**       |
| Feature commits today        | —            | 12          |

## Architecture decisions made today

### Constitution-as-source-of-truth for additive metadata (K4 + K6)

Both the K4 triune block and the K6 hardware_binding block are **additive
fields in the constitution YAML, OUTSIDE `Constitution.canonical_body()`**.
This means:

1. `constitution_hash` is unchanged when these fields are added/removed —
   no cascading rebuild for every existing agent's hash chain.
2. The agent's contract owns its own bond / binding — symmetric with the
   way policies, risk_thresholds, and out_of_scope already work.
3. Enforcement code reads the YAML at dispatch time (~ms cost) rather
   than maintaining a separate registry table. `delegate.v1` reads the
   triune block via `_load_caller_triune`; `dispatcher.dispatch()` reads
   the hardware_binding via `_hardware_quarantine_reason`.

This pattern scales — K7 / K8 / etc. that need agent-level governance
fields can land the same way without a schema bump.

### `ToolContext.agent_registry` for tools that enumerate (G6)

Added a single new field to ToolContext (`agent_registry: Any = None`),
threaded through the dispatcher's existing `agent_registry` field, wired
in `daemon/deps.py`. This unblocks any tool that needs to look up agents
by id without touching the dispatcher hot path. `suggest_agent.v1` is the
first user; future tools (`bond_query.v1`, `lineage_walk.v1`) get it for
free.

### Sanitization-by-default in chronicle export (K5)

Each event_type has a hand-written sanitizer in `SANITIZERS`. Default
output is type + timestamp + safe one-liner. Raw `event_data` only
embedded when `--include-payload` is passed. Operators can share
chronicles without leaking memory contents, secret names, or tool-call
digests beyond what's already metadata-public. The 19-entry sanitizer
table is the actual contract; everything else is just rendering.

## What we learned

### "Constitution-extension instead of schema bump" is the K-track pattern

K1 schema-bumped (`memory_verifications` table v9) because the FK constraint
on `memory_consents.recipient_instance` blocked the simpler sentinel-
recipient approach. Every subsequent K-track item (K4 triune, K6 hardware)
intentionally avoided schema bumps by extending the constitution YAML
with additive blocks. Result: zero migration friction for K2-K6, and
`constitution_hash` integrity preserved across the entire roadmap.

### The dispatcher is the right place for cross-cutting governance checks

Three checks now live at the top of `dispatcher.dispatch()`:
1. Hardware quarantine (K6) — reads constitution YAML
2. Tool lookup (existing)
3. Validation, constraints, counter, approval (existing)
4. Delegate enforcement happens inside `delegate.v1`'s closure, which
   in turn calls back into the dispatcher (K4)

The triune-internal-call path also bypasses the lineage gate inside the
delegator factory. **All cross-cutting governance happens at exactly two
layers — dispatcher entry + delegator entry — which makes it auditable.**

### Live-test harnesses pay for themselves immediately

Every K-track item has a dedicated `live-test-*.command` that drives the
real daemon end-to-end. The K4 harness caught two bugs that inline tests
missed:
- `?limit=N` vs `?n=N` parameter name mismatch on `/audit/tail`
- `event_data` field is serialized as `event_json` string in the audit
  list response

Both bugs were in the test scripts, not the production code — but
without the harness we'd have shipped K4 thinking it worked, and the
operator would have hit the wrong field shape later.

## What didn't go cleanly

### Sandbox can't reach the live daemon

Every live-test required driving Finder to double-click the `.command`
file, then reading the Terminal output via screenshot. Workable but
tedious — turning my full-text Terminal output into screenshots loses
information when output exceeds the visible region.

**Future:** if I'm doing this regularly, write a `live-test-runner.command`
that invokes the named test, captures full output to `data/live-tests/<name>__<ts>.log`,
and the sandbox can `Read` the log file directly. Removes the screenshot bottleneck.

### Test harness vs. production drift

The K4 harness initially failed because:
- It used `?limit=` (production endpoint expects `?n=`)
- It accessed `event_data.bond_name` (response actually has `event_json` as a serialized string)

Both were correct against my mental model of the API; both wrong against
the actual schema. **Lesson:** when scaffolding a live harness, either
hit the endpoint once with `curl` and inspect the response shape, OR
generate the harness from the OpenAPI spec. Today I had to learn the
shape from failed runs.

## Cross-check from the LocalLLM Discord

Read of `#general` (RheeTodd, Ryan Fav, Kazara, dfanz0r — ~04:00–04:55 UTC)
surfaced four ideas worth queueing:

1. **Governance-relaxed audit category** — when an operator flips
   `requires_human_approval=false` or `allow_out_of_lineage=True`, emit a
   distinct `governance_relaxed` event that's harder to miss than the
   current blended events. Optional TTL-bounded relaxations (sudo timeout
   pattern).
2. **Per-model trait floors** — Ryan Fav calibrates per model
   (gpt120b safe-but-messy; qwen3.6 too eager → VM). Forest currently
   throws this wisdom away. A `provider_posture_overrides` map in the
   constitution would let operators codify it.
3. **Defensive linguistic priming** — Ryan Fav's "abusing english" trick
   to make the model self-restrain via word choice. A `defensive_priming`
   field per genre that injects a system-prompt prefix during high-side-
   effect tool calls.
4. **Agent-initiated Tool Forge** — gated `forge_tool.v1` tool the agent
   can call when it's rewritten the same code N times. Today's Tool Forge
   is operator-side; this would be agent-initiated, audited, gated.

The Discord thread also independently arrives at Forest's pitch:
RheeTodd's "what if you could mitigate the risk to near zero chance of
failure that isn't direct human error" is Forest's value prop verbatim.
And RheeTodd's 4:38 AM message — "what if you could have a sheet with
sliders to tweak settings to fit the exact pattern you want it to follow
and remember them with specific skills and tools attached" — describes
the existing Forge tab from the outside.

## What's next

**Immediate (next session):**
- Live-test K6 against the running daemon (script ready: `live-test-k6.command`)
- Push any test-harness commits resulting from live-test fixes
- Broad heavy/light audit of the codebase (Alex's stated priority after K6)

**Queued from the Discord cross-check** (in priority order):
1. Governance-relaxed audit event category + TTL-bounded relaxations (~1 day)
2. Per-model trait floors via constitution `provider_posture_overrides` (~2 days)
3. Defensive linguistic priming per genre (~1 day)
4. Agent-initiated Tool Forge (~3 days, ADR worth filing)

**Open from ADR-003X:**
- C5 — Sigstore-style provenance for MCP servers (deferred, not blocking)
- C8 — synthetic-incident open-web demo (mirror of Phase E1 for open-web)

**Open from the cross-check vision opinion:**
- Inter-realm handshake protocol (own ADR; affects identity + federation)
- Life-event schema vocabulary
- Attachment health checks (Companion-genre policy)
- Growth reflection loops (scheduled-task pattern)

## Verification status as of this audit

| Phase | Inline tests | Live test |
|-------|--------------|-----------|
| C1 (secrets store) | ✅ | ✅ (E2E test post-G2) |
| C2 (web_fetch) | ✅ | ⏳ (no live harness) |
| C3 (browser_action) | ✅ | ⏳ |
| C4 (mcp_call) | ✅ | ⏳ |
| C6 (suggest_agent) | ✅ | ✅ (live-test-g6-k5) |
| C7 (web genres) | ✅ | ✅ (live-test-g6-k5 — `/genres` probe) |
| K1 (memory_verify) | ✅ | ⏳ |
| K2 (ceremony) | ✅ | ⏳ |
| K3 (audit/stream) | ✅ | ⏳ |
| K4 (triune) | ✅ | ✅ (live-test-k4) |
| K5 (chronicle) | ✅ | ✅ (live-test-g6-k5 — chronicle render block) |
| K6 (hardware_binding) | ✅ | ✅ (live-test-k6 — 11/11 steps green; verified on real macos_ioplatform fingerprint) |

Six of twelve phases have a dedicated live-test harness. Adding the
remaining six is a small multi-day investment that pays back the next
time we touch any of those endpoints.

## File map of today's work

```
src/forest_soul_forge/
├── core/
│   ├── secrets.py                       NEW (G2)
│   └── hardware.py                      NEW (K6)
├── chronicle/                           NEW (K5)
│   ├── __init__.py
│   └── render.py
├── tools/builtin/
│   ├── web_fetch.py                     NEW (G3)
│   ├── browser_action.py                NEW (G4)
│   ├── mcp_call.py                      NEW (G5)
│   ├── memory_verify.py                 NEW (K1)
│   └── suggest_agent.py                 NEW (G6)
├── tools/delegator.py                   MOD (K4 — restrict_delegations)
├── tools/dispatcher.py                  MOD (K6 — quarantine check + agent_registry)
├── tools/base.py                        MOD (K6 — agent_registry field)
├── daemon/routers/
│   ├── audit.py                         MOD (K2, K3)
│   ├── triune.py                        NEW (K4)
│   ├── hardware.py                      NEW (K6)
│   └── writes.py                        MOD (K6 — bind_to_hardware)
├── daemon/schemas.py                    MOD (K2, K4, K6)
├── daemon/app.py                        MOD (router registration)
├── daemon/deps.py                       MOD (G6 — agent_registry plumbing)
└── cli/
    ├── triune.py                        NEW (K4)
    └── chronicle.py                     NEW (K5)

config/
├── genres.yaml                          MOD (G6 — 3 web genres)
├── tool_catalog.yaml                    MOD (G3, G4, G5, G6)
└── mcp_servers.yaml.example             NEW (G5)

examples/
├── constitutions/triune/                NEW (K4 — heartwood/branch/leaf seeds)
└── skills/
    ├── triune_consult.v1.yaml           NEW (K4)
    ├── triune_propose.v1.yaml           NEW (K4)
    └── triune_critique.v1.yaml          NEW (K4)

live-test-k4.command                     NEW
live-test-g6-k5.command                  NEW
live-test-k6.command                     NEW
docs/audits/2026-04-29-grtrack-ktrack-marathon.md   THIS FILE
docs/audits/2026-04-29-irisviel-alignment-read.md   (earlier in session)
docs/vision/2026-04-29-phase-i-role-catalog-seed.md (earlier in session)
```

## Closing note

The K-track was supposed to take a few days based on the original ADR
sequencing (C1 → ... → C8 estimated 5-7 weeks). It compressed into one
session because the architectural pattern — additive constitution
fields + dispatcher-layer enforcement — is composable enough that each
new K-track item is genuinely a sprint-scale addition. The risk is that
"composable enough" can also mean "we're stacking features without
load-testing the foundation." The next session's broad audit (Alex's
explicit ask) is the right time to surface where the foundation is
actually carrying weight vs. where it's just shimmed in.
