# 🌲 Forest Soul Forge

> **For developers:** the live state of the codebase — what's implemented, what's blocked, conventions, where to start contributing — lives in [`STATE.md`](STATE.md). It's the companion to this README; this one is product-oriented, that one is current-reality-oriented. Both refresh at every phase boundary.

**A local-first agent foundry where every agent has cryptographically-signed identity, quantified personality, a tamper-evident behavior log, a constitutional rulebook compiled from sliders you set yourself, a runtime that can dispatch tools, run skills, remember across sessions, and delegate work to other agents — all gated, audited, reversible.**

```
┌────────────────┐ build ┌────────────────┐ spawn ┌────────────────┐
│ trait sliders  │──────▶│  Soul + Const  │──────▶│ child inherits │
│ 29 dimensions  │       │ signed identity│       │ lineage + DNA  │
│ 6 domains      │       │ immutable hash │       │ traceable line │
└────────────────┘       └────────┬───────┘       └────────┬───────┘
   caution=85                     │                        │
   empathy=70           dispatch  ▼              delegate  ▼
   thoroughness=85       ┌────────────────┐       ┌────────────────┐
                         │ tools + skills │       │ swarm escalate │
                         │ approval queue │       │ agent_delegated│
                         │ memory subsys  │       │ across tiers   │
                         └────────────────┘       └────────────────┘
```

No cloud lock-in. No silent exfil. No "trust me bro." Every action chains to a tamper-evident JSONL with content-addressed hashes.

---

## 🧮 By the numbers

| | |
|---:|:---|
| **Source LoC (Python)** | ~44,000 across `src/` |
| **Tests (lines)** | ~7,800 across 45 unit suites + 1 integration |
| **ADRs filed** | 26 (`ADR-0001` → `ADR-0033`) |
| **Built-in tools registered** | **31** (5 core + delegate + 4 memory + 21 security swarm) |
| **Genres** | **10** (7 original + 3 security tiers) |
| **Trait roles** | **14** (5 original + 9 swarm) |
| **Skill manifests shipped** | **21** chain + supporting (`examples/skills/`) |
| **Audit event types** | 30+ (lifecycle, dispatch, memory, delegation, swarm) |
| **Frontend modules (vanilla JS)** | 18 (`frontend/js/`) |
| **Operator `.command` scripts** | 19 (start/stop/reset/start-demo/load-scenario/dist-build + 13 ops) |
| **Demo scenarios** | 2 (synthetic-incident + fresh-forge, with presenter scripts) |
| **Isolated demo dir** | `demo/` (start-demo.command points here; prod state untouched) |
| **Distribution** | `dist/build.command` → `forest-soul-forge-<sha>-<date>.zip` |

---

## 🎬 The 60-second pitch

You drag sliders. The forge produces an agent with a content-addressed identity, a machine-readable rulebook compiled from your sliders + your role + your genre, and an LLM-rendered narrative voice. From there the runtime lets that agent **do work** — dispatching versioned tools, running multi-step skills, remembering across sessions, and (if you wire a multi-agent lineage) delegating to other agents through a strictly-audited approval queue.

Every action is local. Every state change is hashed and chained. Every privileged operation hits the operator before the bytes touch the world. Forge → Birth → Run → Audit, all inside `127.0.0.1`.

---

## 🧱 The systems you actually play with

### 🎚️ Trait sliders — 29 dimensions, 6 domains

Drag sliders for `caution`, `empathy`, `thoroughness`, `evidence_demand`, `verbosity`, `directness`, and 23 others. Every trait belongs to a domain (security / audit / cognitive / communication / emotional / embodiment) and a tier (primary / secondary / tertiary). Tier weighting decides how much each trait pulls on the final policy.

Same sliders feed three things deterministically:
- The agent's **DNA** — same profile always produces the same 12-char short ID + 64-char SHA-256.
- The agent's **constitution** — machine-readable rulebook with strictness-wins conflict resolution.
- The agent's **soul.md voice** — LLM-rendered narrative weighted by your genre's signature traits.

### 🎭 Ten genres

Seven shipped with ADR-0021; three more (`security_low / mid / high`) added with ADR-0033 for the defensive plane. Each carries its own trait emphasis, spawn-compatibility table, risk floor, memory ceiling, and approval policy.

| Genre | Vibe | Risk floor | Memory ceiling |
|---|---|---|---|
| **Observer** | Watches, reports, doesn't act | read_only | lineage |
| **Investigator** | Drills into a finding across surfaces | network | lineage |
| **Communicator** | Wraps findings into briefings; outbound human-gated | network | consented |
| **Actuator** | Tickets, deploys, alerts — all gated | external | lineage |
| **Guardian** | Safety check, second opinion, refusal arbiter | read_only | private |
| **Researcher** | Literature scan, allowlisted reach | network | consented |
| **Companion** | Therapy / accessibility / interactive presence | network + local-only | private |
| **security_low** | Always-on patrol — patches, gatekeepers, log lurkers | read_only | lineage |
| **security_mid** | Anomaly, NDR, SOAR-style triage | external | lineage |
| **security_high** | Paranoid apex — zero-trust, vault, deception | external + local-only | private |

Spawning across an incompatible genre boundary requires `--override-genre-spawn-rule` and emits a dedicated `spawn_genre_override` audit event.

### 🛠️ Tool runtime + approval queue

Every tool is a versioned, hash-pinned implementation that satisfies a Protocol-style contract. The dispatcher threads a `ToolContext` (instance_id, role, genre, session_id, memory, delegate, provider, **priv_client**) through every call and enforces:

- **Constitution gating** — the agent must list this tool in its constitution
- **Per-session call counter** — `max_calls_per_session` from the constraint policy
- **Genre risk floor** — Companion-genre agents can't fire network tools; Observer can't fire write-class
- **Approval queue** — calls with `requires_human_approval` (or elevated by genre policy) suspend; the operator approves or rejects via the Approvals tab; the dispatcher resumes against the same args + idempotency key
- **Per-genre approval graduation** — `security_high` gates everything beyond `read_only`; `security_mid` gates filesystem + external; `security_low` defers to tool config
- **Per-call accounting** — tokens_used + cost_usd flow into the character sheet

### 📚 Skill runtime

YAML manifests that orchestrate tool calls into reusable procedures. Bounded interpolation language: `${step.field}` references, comparison operators (`>=`, `==`, `in`, `not in`), and registered functions (`count`, `any`, `all`, `len`, `default`).

The runtime walks the DAG, dispatches each tool through the same `ToolDispatcher` (so the agent's constraints + counter + audit chain stay coherent), and emits seven audit events per skill run.

### 🏭 Tool Forge + Skill Forge

Describe what you want in English; the forge generates a hash-pinned implementation. Six-stage pipeline:

1. **DESCRIBE** — operator types plain English
2. **PROPOSE** — LLM emits a candidate spec
3. **CODEGEN** — Python/YAML emitted; static analysis flags risk patterns
4. **REVIEW** — operator reads the diff
5. **PROVE** — sandbox-run generated tests
6. **INSTALL** — emit to `data/forge/staged/`, append catalog diff, audit-chain entry

CLI: `fsf forge tool` and `fsf forge skill`. Installer: `fsf install tool` (default `--plugin`, hot-loadable) and `fsf install skill`.

### 🧠 Memory subsystem

Per-agent store with three layers (episodic / semantic / procedural) and four scopes:

| Scope | Who can read | Default for |
|---|---|---|
| `private` | The owning agent only | Companion / Guardian / security_high |
| `lineage` | Owner + parent + descendants | Observer / Investigator / Actuator / security_low / security_mid |
| `consented` | Explicit allowlist of agent IDs | Researcher / Communicator |
| `realm` | Any agent in the same realm | Reserved for Horizon 3 federation |

Cross-agent disclosure follows ADR-0027's minimum-disclosure rule: the recipient gets a **summary + back-reference**, never the original content. Per-event consent flows through `POST/DELETE/GET /agents/{id}/memory/consents` and the Memory tab.

### 🛡️ Security Swarm — three-tier defensive plane

ADR-0033. Nine canonical agents arranged as low → mid → high:

```
Low Swarm (security_low)              ┌─ patches, inventories, audits ─┐
  PatchPatrol  · Gatekeeper  · LogLurker
                       │
                       ▼  via memory.lineage + delegate.v1
Mid Swarm (security_mid)             ┌─ correlate, score, contain ─┐
  AnomalyAce  · NetNinja  · ResponseRogue
                       │
                       ▼
High Swarm (security_high)           ┌─ zero-trust apex ─┐
  ZeroZero  · VaultWarden  · DeceptionDuke
```

Every link is an audit event. Every memory write respects its tier's ceiling. Every privileged step hits the approval queue. The chain is **explicit and inspectable** — there is no hidden swarm gossip.

### 🔐 Sudo helper for privileged ops

Three swarm tools (`isolate_process.v1`, `dynamic_policy.v1`, `tamper_detect.v1` SIP path) need elevated capabilities. The daemon stays non-root; a small allowlisted helper at `/usr/local/sbin/fsf-priv` runs under `sudo NOPASSWD` for exactly four operations (`kill-pid`, `pf-add`, `pf-drop`, `read-protected`).

Two layers of defense: the helper has its own argparse + per-op allowlists, and the daemon-side `PrivClient` refuses bad input before shell-out. Gated behind `FSF_ENABLE_PRIV_CLIENT=true` — daemon boots fine without it; privileged tools refuse cleanly with "no PrivClient wired."

See [docs/runbooks/sudo-helper-install.md](docs/runbooks/sudo-helper-install.md).

### 📃 The constitution

Three composition layers, hash-pinned:

1. **Role base** — per-role policy template (now includes all 9 swarm roles)
2. **Trait modifiers** — rules triggered by trait values (e.g. `caution ≥ 80` → require human approval on state-changing actions)
3. **Flagged combinations** — every operator-flagged trait combo becomes a `forbid` policy

Strictness wins on conflict. Hash covers policies + thresholds + scope + duties + drift + tools + genre. Two agents with different genres but identical everything-else have **different** constitution hashes — by design.

### 📓 The audit chain

Append-only JSONL at `data/audit_chain.jsonl`. Every birth, spawn, archive, voice-regeneration, override, tool dispatch, skill invocation, memory operation, consent grant, and cross-agent delegation emits a hash-chained entry. The chain is the source of truth — the SQLite registry is rebuildable from it.

```jsonl
{"seq":42,"prev_hash":"a1b2…","entry_hash":"c3d4…","event_type":"agent_spawned",…}
{"seq":43,"prev_hash":"c3d4…","entry_hash":"e5f6…","event_type":"memory_disclosed",…}
{"seq":44,"prev_hash":"e5f6…","entry_hash":"7890…","event_type":"agent_delegated",…}
```

### 🖥️ The Forge UI

Vanilla JS on nginx. No build step, no framework lock-in. **Seven tabs:**

| Tab | What it shows |
|---|---|
| **Forge** | Trait sliders, genre + role pickers, live preview with DNA + grade + dominant domain + radar chart + constitution_hash |
| **Agents** | Every agent born, with parent/child links, status, archive controls |
| **Approvals** | Pending tool calls awaiting operator decision; approve or reject inline |
| **Skills** | Installed skill catalog; per-card "Run on agent…" form with inputs JSON + result inline |
| **Tools** | Registered tools (built-in / plugin / unknown), with reload-from-disk button |
| **Memory** | Per-agent entries by mode, consent grants, disclosed copies with left-border accent |
| **Audit** | Tail of the chain, filterable by agent |

---

## 🟢 Live status — what's verified end-to-end

The Phase E synthetic-incident smoke ran on `2026-04-28` against the live stack. **The canonical Security Swarm chain fires end-to-end.** Verified:

- ✅ Daemon restart picks up YAML config changes (trait_tree + tool_catalog + genres + constitution_templates)
- ✅ All 9 swarm agents birth cleanly via `POST /birth` against the security tier kits
- ✅ All 21 skill manifests install and reload (`POST /skills/reload` returns `count=21, errors=[]`)
- ✅ `POST /agents/{id}/skills/run` reaches the engine and executes step-by-step
- ✅ Real tool semantics validated: `timestamp_window` → `log_scan` → `memory_write` round-trip captured 3 matches against a seeded canary log
- ✅ Comparison predicates (`>=`, `==`, etc.) work; Python-truthy-style conditionals work
- ✅ Audit chain captures lifecycle events (`agent_created`, `chain_created`, etc.) and runtime events stream to `data/audit_chain.jsonl`
- ✅ **Cross-agent chain proven**: `LogLurker → AnomalyAce → ResponseRogue → VaultWarden`, four levels of `delegate.v1` nesting, **47 ordered audit events** (4 `skill_invoked` + 12 `tool_call_dispatched` + 12 `tool_call_succeeded` + 4 `skill_completed` + 3 `agent_delegated` + 12 `skill_step_started/completed` pairs)

The audit doc at [`docs/audits/2026-04-28-phase-d-e-review.md`](docs/audits/2026-04-28-phase-d-e-review.md) captures the six findings that surfaced live (and were fixed in this round) — chiefly the skill engine's structured-arg stringification, a non-reentrant write_lock that deadlocked nested delegations, and a delegator/install-script path mismatch. ADR-0033 promoted from Proposed → **Accepted** as a result.

---

## 🚀 Quick start

```bash
# 1. Clone and enter
git clone https://github.com/StellarRequiem/Forest-Soul-Forge.git
cd Forest-Soul-Forge

# 2. One-click bootstrap + launch (macOS — recommended)
./start.command          # checks Python ≥3.11, makes .venv, pip-installs, starts stack
                         # ~30s first run, ~5s after that. Browser opens automatically.

# Alternative: Docker (any OS, requires Docker Desktop)
docker compose --profile llm up -d
open "http://127.0.0.1:5173/?api=http://127.0.0.1:7423"
```

That's it. `start.command` is the safe entry point for first-time
contributors and evaluators — it bootstraps the venv on first run and
fast-paths after that. For day-to-day "venv is built, just bring it
up" use, `run.command` is still the direct shortcut.

### macOS double-click ops (`.command` scripts)

The three demo-edition entry points (front of pack) and the rest of
the operator scripts:

```
start.command                bootstrap + launch (recommended first run)
start-demo.command           same as start.command but reads/writes the isolated demo/ dir
stop.command                 kill any process on ports 7423 + 5173
reset.command                archive all generated state, back to clean slate
                             (renames data/audit_chain.jsonl + demo/* etc. to .bak)

scenarios/load-scenario.command   load a pre-built demo scenario (interactive picker)
                                  optional 2nd arg: prod (default) or demo

dist/build.command           build a distributable .zip via git archive
                             (ships as forest-soul-forge-<sha>-<date>.zip)

run.command                  launch daemon + frontend directly (skips bootstrap)
swarm-bringup.command        ADR-0033 Phase D+E one-shot bring-up + smoke
docker-up.command            daemon + frontend via Docker (add --profile llm for Ollama)
stack-rebuild.command        rebuild both Docker containers --no-cache
frontend-rebuild.command     rebuild only the frontend container
ollama-up / kill-ollama      local model lifecycle
live-fire-voice.command      birth a real agent end-to-end
run-tests / t4-tests         dockerized pytest harness
push.command                 git push origin main
```

### Pre-built demo scenarios

For a clean demo without running swarm-bringup from scratch:

```bash
# Replace your top-level state (gets archived to .bak.<timestamp> first):
./scenarios/load-scenario.command synthetic-incident   # the headline 47-event chain
./scenarios/load-scenario.command fresh-forge          # empty slate, drive Forge from scratch
./start.command

# OR isolated demo path — load into demo/, leave prod state untouched:
./scenarios/load-scenario.command synthetic-incident demo
./start-demo.command
```

The `demo` target is the cleanest path for rehearsals — your real
agents/audit chain stay put while you demo against an isolated
`demo/` directory. Each scenario ships with a presenter script —
see [`scenarios/README.md`](scenarios/README.md).

### CLI for power users

```bash
fsf forge tool   "scan a directory for files older than N days"
fsf forge skill  "morning sweep: scan logs, baseline diff, alert on signals"
fsf install tool data/forge/staged/<name>.v1/    # default --plugin (hot-loadable)
fsf install skill data/forge/staged/<name>.v1/skill.yaml
```

### Bring up the Security Swarm

```bash
./scripts/security-swarm-birth.sh         # /birth × 9 — one per role
./scripts/security-swarm-install-skills.sh # copy + reload all 21 skill manifests
./scripts/security-smoke.sh               # synthetic incident drives the chain
# OR all three at once (with health probe + diagnostic surfacing):
./swarm-bringup.command
```

Read [`docs/runbooks/security-swarm-bringup.md`](docs/runbooks/security-swarm-bringup.md) for the full operator walkthrough.

---

## 🏛️ Architecture at a glance

```
┌─────────────────────────────────────────────────────────────────┐
│ Browser (vanilla JS, nginx static serve)                        │
│  Forge · Agents · Approvals · Skills · Tools · Memory · Audit   │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP (CORS-allowed, X-FSF-Token)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ FastAPI daemon  127.0.0.1:7423                                  │
│   /healthz · /agents · /traits · /genres · /tools · /preview    │
│   /birth · /spawn · /archive · /agents/{id}/regenerate-voice    │
│   /agents/{id}/tools/call · /agents/{id}/skills/run             │
│   /pending-calls · /pending-calls/{id}/approve|reject           │
│   /agents/{id}/memory/consents (POST | DELETE | GET)            │
│   /tools/registered · /tools/reload · /skills · /skills/reload  │
│   /agents/{id}/character-sheet · /audit/tail · /runtime/...     │
│   X-Idempotency-Key, single-writer SQLite lock, write_lock      │
└─────────┬───────────────┬──────────────┬──────────────┬─────────┘
          │               │              │              │
          ▼               ▼              ▼              ▼
┌─────────────────┐  ┌──────────┐  ┌────────────┐  ┌──────────────┐
│ Trait + genre   │  │ Tool +   │  │ Memory     │  │ Audit chain  │
│ + tool engines  │  │ skill    │  │ subsystem  │  │ (JSONL,      │
│ + constraint    │  │ runtimes │  │ (sqlite +  │  │ hash-linked) │
│ policy          │  │          │  │ consents)  │  │              │
└────────┬────────┘  └────┬─────┘  └─────┬──────┘  └──────┬───────┘
         └────────────────┴───────┬──────┴────────────────┘
                                  ▼
                     ┌───────────────────────────┐
                     │ SQLite registry v7        │
                     │ (rebuildable from chain)  │
                     └────────────┬──────────────┘
                                  ▼
                     ┌──────────────────────────┐
                     │ LLM provider (pluggable) │ ← local-first (Ollama)
                     │ + sudo helper (optional) │   /usr/local/sbin/fsf-priv
                     └──────────────────────────┘
```

Read [`docs/architecture/layout.md`](docs/architecture/layout.md) for the directory map. Read the [ADR index](docs/decisions/) for every architectural decision and why.

---

## 📚 ADRs (architectural decision records)

Every non-trivial design choice has its own ADR. Files live in [`docs/decisions/`](docs/decisions/).

| #  | Decision                                                  | Status   |
|----|-----------------------------------------------------------|----------|
| 0001 | Hierarchical trait tree with themed domains + tiers     | Accepted |
| 0002 | Agent DNA and lineage (content-addressed identity)      | Accepted |
| 0003 | Grading engine (config-grade per profile)               | Accepted |
| 0004 | Constitution builder (three-layer composition)          | Accepted |
| 0005 | Audit chain (tamper-evident JSONL)                      | Accepted |
| 0006 | SQLite registry as derived index                        | Accepted |
| 0007 | FastAPI daemon as frontend backend                      | Accepted |
| 0008 | Local-first model provider                              | Accepted |
| 0016 | Session modes + self-spawning cipher                    | Proposed |
| 0017 | LLM-enriched soul.md narrative                          | Proposed |
| 0018 | Agent tool catalog                                      | Proposed |
| 0019 | Tool execution runtime (T1–T6 implemented)              | Proposed |
| 0020 | Agent character sheet                                   | Proposed |
| 0021 | Role genres / agent taxonomy (T1–T8 implemented)        | Proposed |
| 0022 | Memory subsystem (v0.1 + v0.2 implemented)              | Proposed |
| 0023 | Benchmark suite                                         | Proposed |
| 0024 | Project horizons + roadmap brainstorm                   | Proposed |
| 0025 | Threat model v2                                         | Placeholder |
| 0026 | Provider economics                                      | Placeholder |
| 0027 | Memory privacy contract (information-flow control)      | Proposed |
| 0028 | Data portability                                        | Placeholder |
| 0029 | Regulatory map                                          | Placeholder |
| 0030 | Tool Forge (T1–T4 implemented)                          | Proposed |
| 0031 | Skill Forge (T1, T2a/T2b, T5, T7, T8 implemented)       | Proposed |
| 0032 | CLI architecture                                        | Proposed |
| 0033 | **Security Swarm** (Phases A–E1 shipped + chain proven live 2026-04-28) | **Accepted** |
| 003X | **Open-Web Tool Family** (web_fetch + browser_action + mcp_call + secrets + suggest_agent + 3 web genres) | Proposed |

Don't trust the doc — trust the code. Every Accepted ADR has a corresponding implementation; every Proposed ADR is in flight or queued.

---

## ✅ What's running today

### Foundation (Accepted)
- Trait engine (29 traits, 6 domains, role-weighted grading)
- Constitution builder + content-addressed hash
- Agent DNA + lineage chain (closure-table queries)
- Audit chain (hash-linked, rebuildable registry, 30+ event types)
- FastAPI daemon (auth, idempotency, CORS, write-lock, lifespan diagnostics)
- Local-first provider (Ollama + frontier slot for OpenAI-compat)
- LLM voice renderer with template fallback

### Tool runtime (ADR-0018 + 0019 + 0033 A6)
- Tool catalog + per-archetype kits + per-genre fallback
- Trait-driven constraint policy
- ToolDispatcher with 5 audit event types, per-call accounting, genre risk-floor enforcement
- Approval queue: persisted tickets, list / approve / reject / resume endpoints, frontend modal
- Per-genre approval graduation (security_high gates everything beyond read_only)
- Plugin loader + `.fsf` package format + `POST /tools/reload`
- `delegate.v1` cross-agent skill invocation with lineage gating + audit
- **PrivClient** lifespan wiring (gated by `FSF_ENABLE_PRIV_CLIENT`)

### Genres (ADR-0021 + ADR-0033)
- 10 genres total (7 original + 3 security tiers)
- Spawn-compat rules with operator override (audited)
- Kit-tier enforcement + voice-renderer trait_emphasis weighting
- Per-genre memory ceiling enforcement at write path
- Genre selector in frontend with role-list filtering

### Memory (ADR-0022 v0.1 + v0.2)
- Per-agent store with episodic/semantic/procedural layers
- Four scopes (private / lineage / consented / realm-reserved)
- `memory_write.v1`, `memory_recall.v1` (mode arg + auto-lineage discovery), `memory_disclose.v1`
- Cross-agent disclosure with summary-only minimum-disclosure rule
- Per-event consent grants via POST/DELETE/GET endpoints
- Frontend Memory tab with mode selector + grant/revoke UI

### Forge (ADR-0030 + ADR-0031)
- Tool Forge: describe → propose → codegen → review → prove (sandboxed pytest) → install
- Skill Forge: manifest parser + interpolation language + skill runtime + 7 audit events
- `fsf install tool` / `fsf install skill` with hot-reload endpoints

### Security Swarm (ADR-0033 Phases A → E)
- Phase A foundation: 3-tier security genre family + memory v0.2 + delegate.v1 + approval graduation + sudo helper
- Phase B toolkit: **26 of 27 tools shipped** (`mfa_check.v1` deferred pending operator scoping)
  - Low tier (8): `audit_chain_verify`, `file_integrity`, `log_scan`, `log_aggregate`, `patch_check`, `software_inventory`, `port_policy_audit`, `usb_device_audit`
  - Mid tier (10): `behavioral_baseline`, `anomaly_score`, `log_correlate`, `lateral_movement_detect`, `ueba_track`, `port_scan_local`, `traffic_flow_local`, `evidence_collect`, `triage`, `isolate_process`
  - High tier (8): `posture_check`, `continuous_verify`, `jit_access`, `key_inventory`, `dynamic_policy`, `tamper_detect`, `canary_token`, `honeypot_local`
- Phase D1: 9 swarm role kits + per-role constitution role_bases
- Phase D2: 21 skill manifests (4 chain + 17 supporting)
- Phase D3: bring-up scripts (`security-swarm-birth.sh`, `security-swarm-install-skills.sh`)
- Phase E1: synthetic-incident smoke test (`security-smoke.sh`) — **chain proven end-to-end 2026-04-28** (47 audit events, see [`docs/audits/2026-04-28-phase-d-e-review.md`](docs/audits/2026-04-28-phase-d-e-review.md))
- Operator runbook: `docs/runbooks/security-swarm-bringup.md`

### Frontend
- 7 tabs (Forge / Agents / Approvals / Skills / Tools / Memory / Audit)
- Live-preview radar chart, character-sheet view, plugin reload, consent grants

### Ops
- Docker compose stack with optional `llm` profile
- Direct-run path via `run.command` (no Docker)
- 16 macOS `.command` scripts for one-double-click ops (incl. demo-edition `start`/`stop`/`reset`)
- `scripts/live-smoke.sh` 8-stage end-to-end smoke runner

---

## 📅 What's next

Per the [Phase B + D + E audit](docs/audits/2026-04-28-phase-d-e-review.md), ranked by leverage:

| Priority | Item | Why |
|---|---|---|
| **1** | Open-web tool family ([ADR-003X](docs/decisions/), unfiled) + Phase C1 secrets store | Next major direction: `mcp_call.v1` + `browser_action.v1` + `web_fetch.v1` + per-agent encrypted secrets store + `suggest_agent.v1`. Now that the audit-mirror gap is closed, every open-web event will be visible via `/audit/tail` from day one |
| **2** | Decompose `daemon/routers/writes.py` (1127 LoC kitchen-sink router) | Smell — split before open-web routers add more endpoints |
| **3** | 3-5 integration tests for cross-subsystem flows | Currently 1 integration test for the whole stack |
| **4** | Frontend test scaffold (Vitest + jsdom) | Real coverage gap; 3,500 LoC of JS, 0 tests |
| **5** | JSONSchema input defaults at runtime in the skill engine | So manifests can rely on declared defaults instead of hard-coding values inline |
| **6** | `mfa_check.v1` | Deferred — operator hasn't scoped what "MFA posture" means |
| **7** | Pytest version of `security-smoke.sh` (E2) | Shell suffices for the operator loop; pytest fixture would let CI gate on the chain |
| **8** | Frontend Swarm tab (E3) | Per-tier agent listing + recent chain events viewer |
| **9** | Companion-tier real-time A/V interaction | Mission pillar 2 — accessibility-first |
| **10** | HSM hardware adapter for VaultWarden's `key_rotate.v1` | Gated on operator hardware decision |
| **11** | External product MCP adapters (Wazuh / Suricata / Defender / etc.) | Gated on operator install |

---

## 🎯 Mission

Two co-equal pillars, specified in the agent's core (not layered on as configuration):

**1. Protect the user and their data.** Local execution. No silent exfil. Auditable behavior. Explicit human approval on high-impact actions. Tamper-evident record of everything the agent did. The Security Swarm extends this from "the agent is well-behaved" to "the agent's environment is actively defended."

**2. Understand the user.** Adaptive, accessibility-aware interaction as a first-class purpose. Every agent performs mental / emotional / physical status checks on the user as standard practice. The medical / therapeutic tier (Companion genre) supports real-time A/V interaction via consumer or custom peripherals, operator-or-guardian-provided profile data, and explicit rapport-building. Goal: accurate translation and interaction with the world for users for whom "default" tone and modality don't fit — sensory impairments, neurodivergence, spectrum conditions, age extremes, ADA accommodations.

A Forest Soul Forge agent that protects its user but doesn't try to understand them is incomplete, and vice versa.

---

## 🤝 Get involved

- **Run it locally** — `docker-up.command` and you're forging in 60 seconds.
- **Read an ADR** — start with [ADR-0001](docs/decisions/ADR-0001-hierarchical-trait-tree.md) (trait tree), [ADR-0021](docs/decisions/ADR-0021-role-genres-agent-taxonomy.md) (genres), or [ADR-0033](docs/decisions/ADR-0033-security-swarm.md) (security swarm). They're short and cite their own trade-offs.
- **Birth an agent** — drag sliders, click Birth, inspect `data/soul_generated/`. Try Spawn. Try the Skills tab. Try the Memory tab.
- **Forge a tool or skill** — `fsf forge tool "..."` from the CLI. Six-stage pipeline keeps you in control at every step.
- **Bring up the Security Swarm** — `./swarm-bringup.command` walks the full Phase D + E sequence on your machine.
- **Open an issue** — feature ideas, role requests, genre additions all welcome.

---

## 📜 License

Apache 2.0 — see [LICENSE](LICENSE).

This project does not collect data. There is no telemetry. There is no phone-home. Your agents and their souls live entirely on your hardware. The license to use them is the same as the license to use any other text file you've written: yours, fully, with no asterisks.

---

```
                ╱│╲
               ╱ │ ╲
              ╱  │  ╲      Forest Soul Forge
             ╱╲  │  ╱╲     local-first, audit-trailed, policy-gated
            ╱  ╲ │ ╱  ╲    forge with character. ship with audit.
           ╱    ╲│╱    ╲
          ━━━━━━━┷━━━━━━━
                ╰─╯
```
