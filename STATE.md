# Forest Soul Forge — current state

A self-contained snapshot for a developer joining the project. What's implemented, what's blocked, what conventions matter, and where to start.

> **Refresh cadence:** this doc + [`README.md`](README.md) update together at every phase boundary (Phase A close, Phase B close, Phase D close, etc.) and after any meaningful architectural finding. The two are designed to stay in sync; STATE.md is the developer-facing current-reality view, README.md is the product-and-mission view.

Last updated: 2026-04-28, after Phase D + E closed live (47-event canonical chain, see [`docs/audits/2026-04-28-phase-d-e-review.md`](docs/audits/2026-04-28-phase-d-e-review.md)).

---

## TL;DR for the first 60 seconds

Forest Soul Forge is a **local-first agent foundry**. You drag trait sliders → forge produces a content-addressed agent (soul.md narrative + constitution.yaml policy + audit-chain provenance + registry row, all four agreeing on the same hash) → that agent dispatches versioned tools, runs YAML skill manifests, persists memory across sessions, and (in theory) delegates work to other agents in its lineage.

Three big things are true today:

1. **The runtime is real** — 31 builtin tools registered, 21 skill manifests installed, 9 swarm agents born live, daemon serving FastAPI on `127.0.0.1:7423`, frontend on `127.0.0.1:5173`. Single-skill execution end-to-end works.
2. **The cross-agent chain fires end-to-end** — the canonical Security Swarm chain (`LogLurker → AnomalyAce → ResponseRogue → VaultWarden`) was verified live 2026-04-28 against a real daemon: 47 audit events, four levels of `delegate.v1` nesting, ordered correctly. Phase E **passes**.
3. **Audit + privacy are the spine** — every state-changing action lands in a hash-chained JSONL. Memory has four scopes (private / lineage / consented / realm) with explicit cross-agent disclosure. No telemetry, no phone-home.

If you read nothing else, read [`docs/decisions/ADR-0033-security-swarm.md`](docs/decisions/ADR-0033-security-swarm.md) — it captures the design discipline the rest of the codebase follows.

---

## The numbers

| | |
|---:|:---|
| Source LoC (Python) | ~44,000 across `src/forest_soul_forge/` |
| Tests (LoC) | ~7,800 across 45 unit suites + 1 integration |
| ADRs filed | 26 (`ADR-0001` → `ADR-0033`, with gaps) |
| Builtin tools registered | 36 (5 core + delegate + 4 memory + 21 security swarm + 5 open-web/verify: web_fetch, browser_action, memory_verify, mcp_call, suggest_agent) |
| Skill manifests shipped | 24 (4 chain + 17 supporting + 3 triune: consult/propose/critique) |
| Schema version | v9 (v8: agent_secrets / v9: memory_verifications) |
| Genres | 13 (7 original + 3 security tiers + 3 web tiers) |
| Trait roles | 14 (5 original + 9 swarm) |
| Audit event types | 30+ |
| Frontend modules (vanilla JS) | 18 |
| `.command` operator scripts | 19 (start/stop/reset + start-demo + load-scenario + dist/build + 13 ops) |
| Demo scenarios | 2 (synthetic-incident + fresh-forge, both with presenter scripts) |
| Data dirs | 2 (top-level prod via start.command + isolated demo/ via start-demo.command) |
| Distribution | `dist/build.command` produces `forest-soul-forge-<sha>-<date>.zip` via git archive |
| Total commits on `main` | 130 |
| Audit docs filed | 1 (`docs/audits/2026-04-28-phase-d-e-review.md`) |

---

## Repo layout

```
Forest-Soul-Forge/
├── src/forest_soul_forge/
│   ├── core/                      # trait engine, constitution, dna, audit_chain,
│   │                              # genre_engine, memory, tool_catalog, tool_policy,
│   │                              # skill_catalog, grading
│   ├── daemon/
│   │   ├── app.py                 # FastAPI lifespan, app.state wiring
│   │   ├── config.py              # DaemonSettings (FSF_* env vars)
│   │   ├── deps.py                # FastAPI dependency injection
│   │   ├── schemas.py             # Pydantic request/response models (1055 LoC)
│   │   ├── routers/               # endpoint implementations
│   │   │   ├── writes.py          # /birth, /spawn, /archive (1127 LoC — needs split)
│   │   │   ├── tool_dispatch.py   # POST /agents/{id}/tools/call
│   │   │   ├── skills_run.py      # POST /agents/{id}/skills/run
│   │   │   ├── pending_calls.py   # approval queue endpoints
│   │   │   ├── memory_consents.py # consent grants
│   │   │   ├── character_sheet.py # GET /agents/{id}/character-sheet
│   │   │   └── ...                # health, audit, traits, genres, tools, skills,
│   │   │                          #   tools_reload, skills_reload, runtime, agents,
│   │   │                          #   preview, skills_catalog
│   │   └── providers/             # local (Ollama) + frontier (OpenAI-compat)
│   ├── tools/
│   │   ├── base.py                # Tool Protocol, ToolContext, ToolResult, registry
│   │   ├── dispatcher.py          # the runtime — gating, counters, audit, queue
│   │   ├── delegator.py           # cross-agent skill invocation factory
│   │   ├── plugin_loader.py       # .fsf package loader
│   │   └── builtin/               # 31 registered tools
│   ├── forge/
│   │   ├── tool_forge.py          # 6-stage tool generation pipeline
│   │   ├── skill_forge.py         # skill manifest generation
│   │   ├── skill_manifest.py      # parses YAML manifests, dispatches args via compile_arg
│   │   ├── skill_runtime.py       # walks the manifest DAG
│   │   ├── skill_expression.py    # ${} interpolation + compiled-arg classes (Template/Literal/Dict/List)
│   │   ├── static_analysis.py     # codegen risk linter
│   │   └── sandbox.py             # subprocess pytest harness
│   ├── registry/                  # SQLite v7 + ingest
│   ├── soul/                      # voice renderer + soul.md generator
│   ├── security/
│   │   └── priv_client.py         # sudo helper wrapper
│   ├── agents/
│   │   └── blue_team/             # placeholder — Phase D agent classes go here
│   └── cli/                       # `fsf` CLI (forge tool/skill, install)
├── config/
│   ├── trait_tree.yaml            # 14 roles, 6 domains, 29 traits
│   ├── genres.yaml                # 10 genre defs with risk profiles
│   ├── tool_catalog.yaml          # 37 tool entries + 12 archetype kits
│   └── constitution_templates.yaml # role_base + trait_modifiers + flagged_combo_policy
├── examples/
│   └── skills/                    # 21 swarm skill manifests
├── data/
│   ├── audit_chain.jsonl          # the canonical chain (rebuildable index in registry)
│   ├── registry.sqlite            # derived index, WAL mode
│   ├── soul_generated/            # generated agent artifacts
│   ├── plugins/                   # operator-installed .fsf packages
│   └── forge/skills/installed/    # runtime skill manifests
├── docs/
│   ├── decisions/                 # 26 ADRs
│   ├── runbooks/                  # security-swarm-bringup, sudo-helper-install,
│   │                              #   end-to-end-smoke-test
│   ├── audits/                    # phase-boundary review docs (1 entry)
│   ├── architecture/              # layout doc
│   ├── PROGRESS.md                # high-level progress log
│   └── vision/                    # handoff notes
├── frontend/                      # vanilla JS, no build, served by nginx
│   ├── index.html
│   ├── css/style.css
│   ├── js/                        # 18 modules: agents, audit, forms, memory,
│   │                              #   pending, providers, radar, skills, state,
│   │                              #   toast, tool-registry, tools, traits, ...
│   ├── nginx.conf
│   └── Dockerfile
├── tests/
│   ├── unit/                      # 45 suites
│   └── integration/               # 1 file (test_full_forge_loop.py)
├── scripts/
│   ├── live-smoke.sh              # 8-stage end-to-end smoke runner
│   ├── security-swarm-birth.sh    # POST /birth × 9
│   ├── security-swarm-install-skills.sh  # cp + reload
│   ├── security-smoke.sh          # synthetic-incident driver
│   └── ...                        # demo + verifiers
├── *.command                      # 16 macOS double-click ops (start/stop/reset/run/...)
└── docker-compose.yml             # daemon + frontend + (optional) ollama
```

---

## Architecture in one paragraph

Browser (vanilla JS) talks to FastAPI daemon over HTTP. Daemon owns a SQLite v7 registry (rebuildable index over the canonical audit chain JSONL), a tool registry + dispatcher (Protocol-based, hash-pinned tools with declarative constraint policy), a skill runtime (YAML manifests interpreted by a bounded interpolation language), a memory subsystem (per-agent SQLite store with four privacy scopes and explicit cross-agent disclosure), a genre engine (10 role families, each with risk floor + memory ceiling + spawn rules), a constitution builder (3-layer composition: role_base + trait_modifiers + flagged_combo_policies), and an audit chain (append-only hash-linked JSONL — source of truth, registry is the index).

Local-first by mission (ADR-0008): default model provider is Ollama on `127.0.0.1:11434`; frontier (OpenAI-compat) is opt-in via `FSF_FRONTIER_ENABLED=true`. Privileged operations (kill PID, push pf rule, read SIP-protected file) flow through a small allowlisted sudo helper (`/usr/local/sbin/fsf-priv`); daemon stays non-root.

---

## What's implemented, by subsystem

### ✅ Foundation (ADR-0001 → 0008, all Accepted)

- **Trait engine** — 14 roles × 29 traits × 6 domains. `TraitEngine(config/trait_tree.yaml)` validates + computes per-role profiles. Domain-weight constraints enforced (`[0.4, 3.0]`).
- **Constitution builder** — 3-layer composition. `build_constitution(role, profile)` returns deterministic YAML; `constitution_hash` is content-addressed and identity-defining.
- **Agent DNA + lineage** — 12-char short ID + 64-char SHA-256 derived from canonical profile. Closure-table queries (`agent_ancestry`) for O(depth) lineage walks.
- **Audit chain** — append-only JSONL with `prev_hash` + `entry_hash`. Hash-chain integrity verifiable via `scripts/verify_audit_chain.py`. KNOWN_EVENT_TYPES gate; tolerates unknowns with a flag.
- **SQLite registry** — schema v7 (migration from v6 for memory v0.2). WAL mode, single-writer discipline via `app.state.write_lock`. Rebuildable from the chain.
- **FastAPI daemon** — port 7423. CORS allowlist. X-FSF-Token auth (optional). X-Idempotency-Key on writes. Lifespan diagnostics surface YAML load failures without aborting boot.
- **Local-first provider** — Ollama by default, OpenAI-compat optional. Per-task model routing (`local_model_classify`, `local_model_generate`, etc.).
- **LLM-enriched soul.md** — `## Voice` section auto-generated, weighted by genre's `trait_emphasis`. Falls back to deterministic template on model failure.

### ✅ Tool runtime (ADR-0019, T1–T6 implemented)

- **Tool Protocol + Registry** — every tool declares `name`, `version`, `side_effects` ∈ {`read_only`, `network`, `filesystem`, `external`}. Registry maps `(name, version) → Tool`. Hot-reload via `POST /tools/reload`.
- **ToolContext** — threaded through every dispatch: `instance_id`, `agent_dna`, `role`, `genre`, `session_id`, `constraints`, `provider`, `logger`, `memory`, `delegate`, `priv_client`.
- **Constraint policy (`core/tool_policy.py`)** — declarative rules (`high_caution_approval_on_side_effects`, `external_always_human_approval`, `filesystem_always_human_approval`, etc.) emit a `ResolvedConstraints` per (profile, tool) pair.
- **Genre risk-floor** — per-genre `max_side_effects` enforced at dispatch; e.g. Companion can't fire network tools, Observer can't fire write-class.
- **Approval queue** — calls with `requires_human_approval` suspend; persisted to `tool_call_pending_approvals` table; operator approves/rejects via `/pending-calls/{id}/approve|reject`; dispatcher resumes against the same args + idempotency key.
- **Per-genre approval graduation (ADR-0033 A4)** — `security_high` gates everything beyond `read_only`; `security_mid` gates filesystem + external; `security_low` defers to per-tool config.
- **Per-call accounting** — `tokens_used` + `cost_usd` flow into `tool_calls` table → character-sheet roll-ups.
- **`.fsf` plugin format** — operator-installed tools land in `data/plugins/<name>.v<version>/`; loader runs at lifespan + on `POST /tools/reload`.
- **`delegate.v1`** — built-in cross-agent skill invocation. Lineage gating + `agent_delegated` audit event. ⚠ blocked on the dict-args gap from manifests.
- **PrivClient (ADR-0033 A6)** — wraps `/usr/local/sbin/fsf-priv` for `kill-pid`, `pf-add`, `pf-drop`, `read-protected`. Gated behind `FSF_ENABLE_PRIV_CLIENT=true`. Daemon boots fine without it; privileged tools refuse cleanly with "no PrivClient wired."

### ✅ Memory subsystem (ADR-0022 v0.1 + v0.2)

- **Three layers** per agent: episodic (events), semantic (facts), procedural (routines).
- **Four scopes**: `private` / `lineage` / `consented` / `realm` (Horizon 3 — reserved). Default-by-genre per ADR-0027.
- **Auto-lineage discovery** — `memory_recall.v1` with `mode=lineage` walks `agent_ancestry` to compute readable IDs.
- **Cross-agent disclosure** — `memory_disclose.v1` materializes a summary-only copy on the recipient's store per ADR-0027 §4 minimum-disclosure rule. Original content never moves.
- **Per-event consent** — `POST /agents/{id}/memory/consents` issues a grant; `DELETE` revokes. Frontend Memory tab has UI.
- **Per-genre memory ceiling** enforcement on every write.

### ✅ Forge (ADR-0030 + 0031)

**Tool Forge** — 6-stage pipeline: DESCRIBE → PROPOSE (LLM emits spec) → CODEGEN (Python + tests) → REVIEW (operator reads diff) → PROVE (sandboxed pytest) → INSTALL. CLI: `fsf forge tool "..."`.

**Skill Forge** — manifest parser (`forge/skill_manifest.py`) + interpolation language (`forge/skill_expression.py`) + runtime (`forge/skill_runtime.py`). Manifests are YAML with `${step.field}` references; engine emits 7 audit event types per skill run (`skill_invoked`, `skill_step_complete`, etc.).

### ✅ Genres (ADR-0021 + ADR-0033)

10 genres total. Each carries `description`, `risk_profile` (max_side_effects + memory_ceiling + optional provider_constraint), `default_kit_pattern`, `trait_emphasis`, `memory_pattern`, `spawn_compatibility`, claimed `roles`.

| Genre | Risk floor | Memory ceiling | Roles |
|---|---|---|---|
| observer | read_only | lineage | network_watcher, log_analyst |
| investigator | network | lineage | anomaly_investigator |
| communicator | network | consented | incident_communicator |
| actuator | external | lineage | (aspirational) |
| guardian | read_only | private | (aspirational) |
| researcher | network | consented | (aspirational) |
| companion | network + local-only | private | operator_companion |
| **security_low** | read_only | lineage | patch_patrol, gatekeeper, log_lurker |
| **security_mid** | external | lineage | anomaly_ace, net_ninja, response_rogue |
| **security_high** | external + local-only | private | zero_zero, vault_warden, deception_duke |

`security_mid`'s `max_side_effects=external` was a recent fix — `isolate_process.v1` (external) is a mid-tier tool per ADR-0033, so the genre ceiling needed to permit it. Per-tool `requires_human_approval` (auto-applied via `external_always_human_approval`) is the actual safety gate.

### ✅ Security Swarm (ADR-0033 Phase A → E)

| Phase | Status |
|---|---|
| **A — foundation** | ✅ shipped — security genre family, memory v0.2, delegate.v1, approval graduation, sudo helper |
| **B1 — low-tier tools (8/9)** | ✅ shipped — patch_check, software_inventory, port_policy_audit, usb_device_audit, log_scan, log_aggregate, audit_chain_verify, file_integrity. `mfa_check` deferred (operator hasn't scoped MFA posture target yet). |
| **B2 — mid-tier tools (10)** | ✅ shipped — behavioral_baseline, anomaly_score, log_correlate, lateral_movement_detect, ueba_track, port_scan_local, traffic_flow_local, evidence_collect, triage, isolate_process |
| **B3 — high-tier tools (8)** | ✅ shipped — posture_check, continuous_verify, jit_access, key_inventory, dynamic_policy, tamper_detect, canary_token, honeypot_local |
| **D1 — swarm role kits + constitution role_bases** | ✅ shipped — 9 roles in trait_tree.yaml + 9 archetype kits in tool_catalog.yaml + 9 role_bases in constitution_templates.yaml |
| **D2 — skill manifests** | ✅ shipped — 21 manifests in `examples/skills/` (4 canonical chain + 17 supporting). All 21 parse + install. |
| **D3 — bring-up scripts** | ✅ shipped — `scripts/security-swarm-{birth,install-skills}.sh`, `scripts/security-smoke.sh`, `swarm-bringup.command`, operator runbook |
| **E1 — synthetic-incident smoke** | ✅ shipped + **passes live**. Canonical chain `LL → AA → RR → VW` produces 47 ordered audit events; see [`docs/audits/2026-04-28-phase-d-e-review.md`](docs/audits/2026-04-28-phase-d-e-review.md). |

### ✅ Frontend

Seven tabs (Forge, Agents, Approvals, Skills, Tools, Memory, Audit). Vanilla JS, no build step, no framework lock-in. Served by nginx in Docker or by `python -m http.server` directly.

### ✅ Ops

- Docker Compose with optional `llm` profile (Ollama)
- Direct-run path via `run.command` (no Docker — port 7423 daemon + 5173 frontend, foreground tail)
- 13 macOS `.command` scripts:
  - `docker-up`, `stack-rebuild`, `frontend-rebuild`
  - `run`, `kill-ollama`, `ollama-up`
  - `live-fire-voice`, `run-tests`, `t4-tests`
  - `push`
  - `swarm-bringup` (Phase D + E one-shot)
- `scripts/live-smoke.sh` (forge end-to-end smoke, 8 stages)

---

## What's blocked or unfinished

### ✅ Closed in this round (Phase D + E + audit-tail follow-up)

- **Skill-engine dict-args gap** — fixed via `compile_arg(value)` recursive type-dispatched compiler in `forge/skill_expression.py`. Dict/list/literal YAML values now flow through to the tool validator unchanged; nested `${...}` interpolation still works. Commit `04c0d27`.
- **`write_lock` non-reentrant** — `threading.Lock()` → `threading.RLock()` in `daemon/app.py`. Nested `delegate.v1` calls (caller's skill_run → delegator → target's skill_run on the same thread) no longer self-deadlock. Commit `d215fd1`.
- **Delegator looked at wrong manifest path** — install script writes flat `<name>.v<version>.yaml`; delegator was reading subdir `<name>.v<version>/skill.yaml`. Now tries flat-then-subdir. Commit `41c6f5d`.
- **Peer-root swarm chain delegations refused** — chain manifests now set `allow_out_of_lineage: true`; the override is itself an audit event, so cross-tier delegations remain visible. Commit `4ed194b`.
- **JSONSchema input defaults at runtime** — engine doesn't apply them. Worked around by hard-coding the `investigate_finding` contain-threshold to literal `1`. Engine-side fix is queued; manifest authors should reference inputs explicitly until then. Commit `4f241ea`.
- **`/audit/tail` only returned lifespan events** — `daemon/routers/audit.py` now reads the canonical JSONL via `AuditChain.tail(n)` instead of querying the registry's lifespan-only mirror. Per ADR-0006, the JSONL is the source of truth and the registry is a derived index; tailing the source is the right primary path. Indexed `/audit/agent/{id}` and `/audit/by-dna/{dna}` queries still hit the registry where the index actually helps. Bounded-memory deque keeps tail O(N) regardless of chain size; tolerant of malformed lines (consistent with `_recompute_head`).

The full incident report — symptom, file, fix, commit — lives in [`docs/audits/2026-04-28-phase-d-e-review.md`](docs/audits/2026-04-28-phase-d-e-review.md).

### ⚠ Items in the queue (ranked by leverage)

| Item | Status / blocker | Effort |
|---|---|---|
| `daemon/routers/writes.py` decomposition | 1127 LoC kitchen-sink router; smell, not bug. Should split before open-web routers add more endpoints. | ~1 day refactor |
| Integration tests | 1 file (forge loop). Need 3–5 covering dispatcher + memory + delegate, tool_dispatch with approval queue resume, skill_run multi-tool composition. | ~1 day |
| Open-web ADR-003X + Phase C1 (per-agent encrypted secrets store) | Design captured in `MEMORY.md`; primitives = `mcp_call.v1` + `browser_action.v1` + `web_fetch.v1` + `suggest_agent.v1`. Three new genres: `web_observer`, `web_researcher`, `web_actuator`. | ~5 rounds of build |
| Frontend test scaffold | 0 tests for 3,500 LoC of JS. Vitest + jsdom. | ~half day |
| `mfa_check.v1` | Deferred — operator hasn't scoped what "MFA posture" means (TOTP enrolled? SAML SSO + MFA enforced? Per-account MFA capability matrix?) | unknown |
| JSONSchema input defaults at runtime in the skill engine | So manifests can rely on declared defaults instead of hard-coding values inline. | small |
| Pytest version of the smoke (E2) | Shell script suffices for the operator loop; pytest fixture would let CI gate on the chain. ADR-0023-style. | ~1 day |
| Frontend Swarm tab (E3) | Per-tier agent listing + recent chain events viewer. | ~1 day |
| Companion-tier real-time A/V | Mission pillar 2. Designed in ADRs (0008 + 0021), no implementation yet. | unknown, large |
| HSM hardware adapter (VaultWarden's `key_rotate.v1`) | Gated on operator hardware decision (which HSM). | gated |
| External product MCP adapters (Wazuh / Suricata / 1Password / Defender / etc.) | Gated on operator install of those products. | gated |

---

## Conventions a contributor needs to know

### File contract

- **canonical artifacts on disk are source of truth** — `data/audit_chain.jsonl` and `data/soul_generated/*` files; SQLite registry is rebuildable from them
- **YAMLs in `config/` are machine-readable contracts** — changing them changes the hash of every agent that depends on them. Versioning is by file (no `_v2.yaml` parallel files; in-place edits are deliberate breaks)
- **manifests in `examples/skills/` ship as committed reference** — `data/forge/skills/installed/` is gitignored runtime state. The install scripts copy from `examples/` to the runtime dir.

### Hash discipline

- `dna` = SHA-256 of canonical trait profile (deterministic; same sliders always → same DNA)
- `constitution_hash` = SHA-256 over policies + thresholds + scope + duties + drift + tools + genre. Two agents differing only in genre have different hashes — by design.
- `audit_chain.jsonl`: each entry's `entry_hash = SHA-256(prev_hash || event_json)`. Tamper-evident.
- Every tool call's audit entry carries a `result_digest` (SHA-256 of canonical-JSON `output + metadata`) — full output lives in the registry's `tool_calls` table; the chain stays small.

### Side-effect classification

Every tool declares `side_effects ∈ {read_only, network, filesystem, external}`. The runtime gates on this. `external` tools always require human approval (auto-applied via `external_always_human_approval` rule). Companion-genre agents structurally cannot fire `network`+ tools. `security_high` agents structurally cannot fire `external` tools without explicit operator approval per call.

If you're adding a tool, classify honestly — don't mark a tool `read_only` to bypass the queue. The classification flows through the catalog → constraint resolver → dispatcher. Mismatches between what the tool actually does and what it declares is a **safety bug**, not a typing nit.

### Audit chain event types

`KNOWN_EVENT_TYPES` is in `core/audit_chain.py`. Adding a new event type means appending to the set + writing the emission code. The chain tolerates unknowns with a flag (forward-compat) but new types should be registered explicitly.

Dispatcher emits:
- `tool_call_dispatched` — every dispatch
- `tool_call_succeeded` — terminal success
- `tool_call_refused` — pre-execution refusal (constraint, validation, genre floor)
- `tool_call_failed` — runtime exception
- `tool_call_pending_approval` — queued for operator
- `tool_call_approved` / `tool_call_rejected` — operator decision

Skill runtime emits:
- `skill_invoked` / `skill_step_complete` / `skill_step_failed` / `skill_succeeded` / `skill_failed` / etc.

Cross-agent: `agent_delegated`. Memory: `memory_appended`, `memory_disclosed`, `memory_consent_granted`, `memory_consent_revoked`, `memory_promoted`, `memory_consolidated`, `memory_forgotten`.

### Single-writer SQLite

`app.state.write_lock` (a `threading.Lock`) serializes all writes. Read endpoints don't acquire it. Don't bypass — race conditions on the registry are the kind of bug that's nearly impossible to repro after the fact.

### Idempotency

Mutating endpoints accept `X-Idempotency-Key`. Repeat with the same key + same body returns the prior response without re-executing. Implementation in `daemon/idempotency.py`.

### How to add a tool

1. Subclass the `Tool` Protocol in `src/forest_soul_forge/tools/builtin/<name>.py`. Implement `validate(args)` and `execute(args, ctx)`. Declare `name`, `version`, `side_effects`.
2. Register in `src/forest_soul_forge/tools/builtin/__init__.py` (import + `__all__` + `register_builtins()` body).
3. Add a catalog entry in `config/tool_catalog.yaml` under `tools:` with `name`, `version`, `description`, `input_schema`, `side_effects`, `archetype_tags`. The lifespan integrity check verifies the registry's `(name, version, side_effects)` matches the catalog.
4. Add tests in `tests/unit/test_<name>.py`. Validation refusals + happy path + (where applicable) failure paths.
5. If the tool is privileged (`external` + operator-must-approve-per-call), inherit the auto-approval rule from `tool_policy.py` — don't add new logic.

### How to add a skill manifest

1. Author the YAML in `examples/skills/<name>.v<version>.yaml` per the schema in `forge/skill_manifest.py`.
2. Required top-level keys: `schema_version: 1`, `name`, `version`, `description`, `requires` (list of `<tool>.v<version>` keys), `inputs` (JSONSchema-ish), `steps` (DAG), `output` (templated map).
3. Step kinds: `tool` (call a tool), `for_each` (iterate with nested steps + `${each}` binding), conditional via `when:`.
4. Expression engine supports: `${step.field}`, dotted drilling, `==`/`!=`/`<`/`<=`/`>`/`>=`/`in`/`not in`, registered functions `count`/`any`/`all`/`len`/`default`. **No** `gte()` / `gt()` / `defined()`.
5. **Structured args** (`tags: [...]`, `inputs: {...}`, etc.) flow through `compile_arg` and reach the tool validator unchanged. Nested `${...}` interpolation works inside dicts and lists.
6. **JSONSchema `default:` values are NOT applied by the engine at runtime.** A `when:` predicate referencing an unset input field will skip the step silently. Until the engine grows defaults, manifest authors should reference inputs explicitly (e.g. hard-coded thresholds) or rely on `required:` to surface the missing-input error at parse time.

### How to add a role

1. Add to `config/trait_tree.yaml` under `roles:` with `description` + `domain_weights` (security/audit/cognitive/communication/emotional/embodiment, each in `[0.4, 3.0]`).
2. Claim it in `config/genres.yaml` under one genre's `roles:` list.
3. Add a `role_base` entry in `config/constitution_templates.yaml`.
4. Optionally add a per-role archetype kit in `config/tool_catalog.yaml` under `archetypes:`. (Otherwise the kit resolver falls back to `genre_default_tools`.)
5. The lifespan validates `every TraitEngine role is claimed by some genre` — failure surfaces on `/healthz` `startup_diagnostics`.

---

## How to run things locally

### Bring up the stack

```bash
# First-time bootstrap + launch (handles venv creation, pip install, then runs):
./start.command

# Day-to-day "venv exists, just run" shortcut:
./run.command

# Stop a running stack (kills processes on 7423 + 5173):
./stop.command

# Reset to clean state (archives audit chain + registry + soul artifacts):
./reset.command

# Load a demo scenario (pre-built data state — see scenarios/README.md):
./scenarios/load-scenario.command synthetic-incident          # default = prod target
./scenarios/load-scenario.command synthetic-incident demo     # isolated demo/ target

# Run the daemon against the isolated demo/ dir (F7) — production
# state at top-level audit_chain.jsonl + registry.sqlite is untouched:
./start-demo.command

# Docker alternative (any OS):
docker compose --profile llm up -d
open "http://127.0.0.1:5173/?api=http://127.0.0.1:7423"
```

`start.command` is the safe entry point for first-time contributors —
checks Python ≥3.11, makes the .venv, pip-installs editable, then
delegates to `run.command`. Repeat invocations skip the work that's
already done. `scenarios/load-scenario.command` archives current state
and copies a frozen snapshot into place — useful for repeatable demos
or recovering quickly after a `reset`.

### Run tests

```bash
# In Docker (matches CI)
./run-tests.command
# OR locally if you have pytest in your venv
.venv/bin/pytest tests/unit/ -v
```

### Bring up the Security Swarm

```bash
# All in one
./swarm-bringup.command

# OR step-by-step
./scripts/security-swarm-birth.sh
./scripts/security-swarm-install-skills.sh
./scripts/security-smoke.sh
```

The smoke seeds a synthetic log, drives `LogLurker.morning_sweep`, and inspects the audit chain. **Verified end-to-end on 2026-04-28**: 47 ordered audit events, four levels of `delegate.v1` nesting (`LL → AA → RR → VW`), every tool dispatch + agent delegation captured.

### Forge a tool from the CLI

```bash
fsf forge tool "scan a directory for files older than N days"
# review the staged spec at data/forge/staged/<name>.v1/
fsf install tool data/forge/staged/<name>.v1/
```

### Inspect the audit chain

```bash
# Walk + verify hashes
python scripts/verify_audit_chain.py
# Tail the daemon's view
curl -s http://127.0.0.1:7423/audit/tail?n=50 | jq '.events[].event_type' | sort | uniq -c
```

### Check daemon health

```bash
curl -s http://127.0.0.1:7423/healthz | jq '
  "status: " + .status,
  "diagnostics: " + (.startup_diagnostics | length | tostring),
  (.startup_diagnostics[] | "  [\(.status)] \(.component): \(.error // "")")
'
```

A healthy daemon shows ~6 diagnostics, all `ok` or `disabled`. `failed` or `degraded` on `trait_engine`, `tool_runtime`, or `genre_engine` means restart didn't pick up YAML changes.

---

## Where to start contributing

If you want to make immediate impact, pick from this list (top = highest leverage):

1. **Decompose `daemon/routers/writes.py`** into per-resource routers. 1127 LoC right now; will only grow when open-web routers land. ~1 day refactor with thorough endpoint tests.
2. **File ADR-003X** for the open-web tool family + Phase C1 (per-agent encrypted secrets store). Design is captured in memory (see [`/sessions/.auto-memory/project_open_web_integration.md`]). Three primitives: `mcp_call.v1`, `browser_action.v1`, `web_fetch.v1`. `suggest_agent.v1` for operator-facing job matching. Three new genres (`web_observer`, `web_researcher`, `web_actuator`).
3. **Add 3–5 cross-subsystem integration tests.** Currently 1 file. Highest value: dispatcher + memory + delegate, tool_dispatch with approval-queue resume, skill_run with multi-tool composition. ~1 day.
4. **Frontend test scaffold** (Vitest + jsdom). 3,500 LoC JS, 0 tests. ~half day for the scaffold + 2-3 example tests; future PRs add tests alongside UI changes.
5. **JSONSchema input defaults at runtime** in the skill engine — small surface change, lets manifests rely on declared defaults instead of hard-coding values inline.

If you want to read code first, start with:

1. [`docs/decisions/ADR-0033-security-swarm.md`](docs/decisions/ADR-0033-security-swarm.md) — the design discipline
2. `src/forest_soul_forge/tools/dispatcher.py` — the runtime
3. `src/forest_soul_forge/forge/skill_manifest.py` + `skill_runtime.py` — the skill engine (and the gap)
4. `src/forest_soul_forge/core/audit_chain.py` — the privacy spine
5. `src/forest_soul_forge/daemon/app.py` — the lifespan + app.state wiring

---

## ADR map

| # | Decision | Status |
|---|---|---|
| 0001 | Hierarchical trait tree | Accepted |
| 0002 | Agent DNA + lineage | Accepted |
| 0003 | Grading engine | Accepted |
| 0004 | Constitution builder | Accepted |
| 0005 | Audit chain | Accepted |
| 0006 | SQLite registry as derived index | Accepted |
| 0007 | FastAPI daemon | Accepted |
| 0008 | Local-first model provider | Accepted |
| 0016 | Session modes + self-spawning cipher | Proposed |
| 0017 | LLM-enriched soul.md narrative | Proposed |
| 0018 | Agent tool catalog | Proposed |
| 0019 | Tool execution runtime | Proposed (T1–T6 implemented) |
| 0020 | Agent character sheet | Proposed |
| 0021 | Role genres | Proposed (T1–T8 implemented) |
| 0022 | Memory subsystem | Proposed (v0.1 + v0.2 implemented) |
| 0023 | Benchmark suite | Proposed |
| 0024 | Project horizons | Proposed |
| 0025 | Threat model v2 | Placeholder |
| 0026 | Provider economics | Placeholder |
| 0027 | Memory privacy contract | Proposed |
| 0028 | Data portability | Placeholder |
| 0029 | Regulatory map | Placeholder |
| 0030 | Tool Forge | Proposed (T1–T4 implemented) |
| 0031 | Skill Forge | Proposed (T1, T2a/T2b, T5, T7, T8 implemented) |
| 0032 | CLI architecture | Proposed |
| 0033 | Security Swarm | **Accepted** (Phases A–E1 shipped + chain proven live 2026-04-28) |
| 003X | Open-Web Tool Family (web_fetch + browser_action + mcp_call + secrets store + suggest_agent + 3 web genres) | Proposed — C1 (secrets), C2 (web_fetch), C3 (browser_action), C4 (mcp_call), C6 (suggest_agent), C7 (3 web genres) shipped 2026-04-29; C8 (demo) in flight |
| 003X K | K-track parallels (memory verification, ceremony events, SSE stream, triune spawn) | K1 (memory_verify), K2 (ceremony.v1), K3 (/audit/stream), K4 (triune bond + Heartwood/Branch/Leaf seeds + delegate.v1 enforcement) shipped 2026-04-29; K5 (chronicle CLI), K6 (hardware_binding) in flight |

ADRs that are `Proposed` but have `(... implemented)` are Decision-record-paper-trail proposed: the design is in flight, parts are committed, the doc itself just hasn't been promoted to `Accepted` because a few tranches remain. ADR-0033 was promoted on 2026-04-28 once the canonical Security Swarm chain fired end-to-end through the smoke.

---

## Threat model in one paragraph

The agent runtime is built around the assumption that the local user trusts the local machine. The audit chain is the **operator's** evidence that the daemon (and the agents it births) didn't go off the rails — not evidence FOR the daemon to anyone else. We do not defend against root-level compromise of the user's box. We do defend against:

- **Daemon-internal logic errors** — a tool author mis-classifying side_effects, a skill author asking for too much access. Catalog cross-checks + per-genre kit-tier enforcement + per-call approval gating cover these.
- **Agent prompt injection** — every state-changing action is gated, audited, reversible. The dispatcher's approval queue is the runtime equivalent of "the agent asks the operator before doing something durable."
- **Cross-agent privilege creep** — `delegate.v1` enforces lineage gating + emits `agent_delegated`. Memory disclosure is summary-only per ADR-0027. Cross-tier writes in the swarm fire `swarm_escalation` events.
- **Tampering with the canonical record** — audit chain hashes are linked; `audit_chain_verify.v1` walks them; chain breaks are LogLurker's highest-severity finding.

What we don't defend against (out of scope per ADR-0025):

- Supply-chain attacks on the wheel
- A compromised host process attaching to the daemon's SQLite file
- Side-channel attacks on the LLM provider
- Operator-side social engineering (the operator IS the trusted root)

---

## License + ethos

Apache 2.0. No telemetry. No phone-home. No data collection. The agents and their souls live entirely on your hardware. The license to use them is the same as the license to use any text file you've written: yours, fully, with no asterisks.

The mission is two co-equal pillars: **protect the user and their data**, and **understand the user**. An agent that does the first without the second is a guard dog. An agent that does the second without the first is a salesman. Forest Soul Forge agents do both, or they don't ship.
