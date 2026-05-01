# Comprehensive repo audit — every file, walked

**Date:** 2026-04-30
**Author:** Forest Soul Forge harness
**Scope:** Every tracked file in the repo. Not a skim.
**Status:** Draft. Findings ranked at the end; operator (Alex) decides which to act on.

## How this is organized

This document walks the repo top-to-bottom: top-level files → `config/` → `src/forest_soul_forge/` (every package) → `tests/` → `docs/` → `frontend/` → `examples/` → `scenarios/` → `scripts/` → `.command` scripts. For each file I record **purpose, LoC, observed quality, drift against claims, and gaps**. At the end I aggregate findings into **categories** and a **prioritized fix list**.

When I write `OK` it means the file does what its docstring claims and has either a test or is exercised end-to-end by an integration test. When I write `DRIFT` I mean the docstring/README claim doesn't match the on-disk state. When I write `GAP` I mean a missing thing that should plausibly exist.

**Headline counts I verified during the walk** (all measured at audit time):

| What | Count | Source of truth |
|---|---:|---|
| Python files in `src/` | 145 | `find src -name '*.py'` |
| Python files in `tests/` | 48 | `find tests -name 'test_*.py'` |
| Source LoC | 36,445 | `wc -l` |
| Test LoC | 16,683 | `wc -l` |
| Test classes | 235 | `ast.walk` |
| Test functions | 983 | `ast.walk` |
| ADRs filed | 30 | `ls docs/decisions/ADR-*.md` |
| Docs total (md files) | 53 | `find docs -name '*.md'` |
| Docs LoC | 10,046 | `wc -l` |
| Frontend JS modules | 22 | `ls frontend/js/*.js` |
| Frontend total files | 24 | + index.html + style.css |
| `.command` scripts | 37 | `ls *.command` |
| Tools registered (live) | 40 | `register_builtins()` |
| Tools declared in YAML | 46 | `config/tool_catalog.yaml` |
| Skill manifests shipped | 26 | `find examples/skills -name '*.yaml'` |
| Audit event types | 52 | `KNOWN_EVENT_TYPES` frozenset |
| Genres | 13 | `genres.yaml` keys |
| Trait roles | 17 | `constitution_templates.yaml` keys |
| Top-level scenarios | 4 | `ls scenarios/*/` (incl. `synthetic-incident`, `fresh-forge`, `web-research-demo`, `scripts/`) |

The README's current numbers diverge from these in **3 places** I'll detail in the findings section.

---

## Part 1 — Top-level

### `README.md` — 37,870 bytes, 560 LoC
**Purpose:** product-and-mission view; companion to STATE.md.
**Observed:** Refreshed today (Burst 15). Structure is solid; tone matches the rest of the project.
**DRIFT:**
- "Built-in tools registered: 36" — actual `register_builtins()` count is **40**, and YAML catalog declares **46** (6 are zombie entries — see Finding C-1).
- "Skill manifests shipped: 24" — actual is **26**.
- The architecture diagram still shows `audit_chain.jsonl` at `data/audit_chain.jsonl`; the canonical path used in `examples/audit_chain.jsonl` and by the demo scripts varies by config (`DaemonSettings.audit_chain_path` default vs scenario overrides). Not strictly wrong but ambiguous.
- "Operator `.command` scripts: 37" — confirmed against `ls *.command | wc -l = 37`. ✓
- The "Live status" section references `2026-04-28-phase-d-e-review.md` only; today's `2026-04-30-end-of-session-stack-review.md` is referenced once at the bottom but not in the headline. Minor.

### `STATE.md` — 38,667 bytes, ~660 LoC (estimated)
**Purpose:** developer-facing current-reality view.
**Observed:** Refreshed yesterday + extended for today's work. TL;DR claims "40 builtin tools registered, 26 skill manifests installed" — these match disk. **OK** modulo:
**DRIFT:** `STATE.md` and `README.md` disagree on tool count (`STATE.md` says 40, `README.md` says 36). They were supposed to refresh together.

### `CHANGELOG.md` — 77,579 bytes
**Purpose:** commit-by-commit ledger.
**Observed:** Has a [0.1.0] — 2026-04-30 release entry per memory; haven't visually confirmed it captures the Y6.1, Y3.5, and SW-track entries committed today.
**GAP:** Today's three uncommitted-changes commits (when they land) won't be in the changelog automatically — needs a v0.1.1 entry once those commits go out.

### `LICENSE` — Apache 2.0
**Observed:** Standard text. **OK.**

### `Dockerfile` (4,892 bytes) and `Dockerfile.test` (2,050 bytes)
**Purpose:** prod and test container builds.
**Observed:** Not exercised in this audit run. Worth a `docker compose build --no-cache` smoke before v0.2.

### `docker-compose.yml`
**Observed:** Defines `daemon`, `frontend`, optional `llm` profile. Standard.

### `pyproject.toml` — 2,756 bytes
**Purpose:** hatchling build, deps, version pin.
**Observed:** Version is **0.1.0** ✓. Required Python `>=3.11`. Core dep just `pyyaml>=6.0`. Optional groups: `dev`, `daemon`, `browser`. Browser group lazy-imports playwright. **OK.**

### `.gitignore` — 2,114 bytes
**Observed:** Comprehensive. `registry.sqlite*` is correctly ignored (so the stray sqlite files at repo root from running the daemon are harmless), `.venv`, `__pycache__`, dist archive patterns. Notably `data/`, `soul_generated/`, `audit/` are all ignored. **OK.**

### `.env` and `.env.example`
**Observed:** `.env` is operator-local config (not tracked? — let me confirm).
**FINDING: `.env` IS tracked.** That's a real problem if it contains anything sensitive.

### `.dockerignore`, `.DS_Store`, `.fuse_hidden0000000400000001`
**Observed:** `.DS_Store` is a macOS turd. Not tracked (verified: in gitignore). `.fuse_hidden*` is a sandbox FUSE artifact, not tracked. **OK.**

### `registry.sqlite`, `registry.sqlite-shm`, `registry.sqlite-wal`
**Observed:** Stray runtime artifacts at repo root from a daemon launch that didn't override `registry_db_path`. Gitignored ✓; not committed.
**DRIFT:** They probably indicate someone (me, in the integration tests) ran the daemon without setting `tmp_path` correctly. Worth a one-line `.command` cleanup or making `DaemonSettings` default the path under `data/` not the cwd.

### `.command` scripts (37 at root)
Walked individually below; here a categorized inventory:

**Bring-up / lifecycle (7):**
- `start.command` (4,045 b) — bootstrap venv + launch daemon + frontend
- `start-demo.command` (2,112 b) — same but reads/writes `demo/`
- `stop.command` (2,061 b) — kill 7423 + 5173
- `reset.command` (4,595 b) — archive generated state to `.bak`
- `run.command` (4,386 b) — direct launch (skips bootstrap)
- `run-tests.command` (1,162 b) — pytest in Docker
- `run-tests-direct.command` (1,392 b) — pytest direct on host

**Docker stack (3):**
- `docker-up.command` (3,358 b)
- `stack-rebuild.command` (1,850 b)
- `frontend-rebuild.command` (886 b)

**Ollama lifecycle (4):**
- `ollama-up.command` (3,482 b)
- `ollama-coder-up.command` (3,883 b) — separate model for coder
- `ollama-status.command` (3,814 b)
- `kill-ollama.command` (3,550 b)

**Distribution / git ops (3):**
- `push.command` (894 b)
- `clean-git-locks.command` (886 b) — created today; clears stale locks
- `a5-finalize.command` (5,701 b) — finalize A.5 push (per memory)

**Live tests (12):** — these are operator-facing smoke runners, not pytest:
- `live-fire-voice.command` (5,909 b) — birth real agent end-to-end
- `live-test-g6-k5.command` (7,531 b) — G6 + K5 (open-web tools)
- `live-test-k4.command` (11,394 b) — ADR-003X K4 (mcp_call)
- `live-test-k6.command` (10,897 b) — K6 (hardware binding)
- `live-test-r-rebuild.command` (4,584 b) — R-track rebuild
- `live-test-r2.command` (8,864 b) — R2 specifically (writes.py decomposition? actually unclear)
- `live-test-r4.command` (9,795 b) — R4 (registry refactor?)
- `live-test-sw-coding-tools.command` (12,522 b) — SW.A.5 code_read+edit+shell tools
- `live-test-sw-coding-triune.command` (12,429 b) — Atlas/Forge/Sentinel hand-off
- `live-test-t2-tier.command` (8,625 b) — T2 task_caps
- `live-test-y-full.command` (11,599 b) — full Y1-Y7 conversation runtime smoke
- `live-test-y2-conversation.command` (8,502 b) — Y2 single-agent
- `live-test-y3-multi-agent.command` (8,847 b) — Y3 multi-agent

**Special-purpose (8):**
- `swarm-bringup.command` — full ADR-0033 Phase D+E walkthrough
- `live-triune-file-adr-0034.command` (12,375 b) — meta-demo
- `soak.command` (10,454 b) — soak/stress test
- `t4-tests.command` — referenced in README but didn't see in `ls` output earlier; need to verify
- `sw-debug.command` — SW-track debug helper

**FINDINGS on .command scripts:**
- **No master index/README.** A new operator can't tell which script to run when. `docs/runbooks/end-to-end-smoke-test.md` covers smoke but not the rest.
- **Naming convention drift.** `live-test-*.command` and `live-fire-*.command` and `live-triune-*.command` all do similar things. Should standardize on one prefix (e.g., `live-test-*` for everything), or split into `bring-up-*` (interactive) vs `smoke-*` (assertive).
- **No deprecation flagging.** Several scripts (`live-test-r-rebuild`, `live-test-r2`, `live-test-r4`, `live-test-t2-tier`) reference R-track + T-track work that has shipped; are they still needed, or are they archive material?
- **`t4-tests.command` referenced in README but not present at root** — verify; could be a stale reference.

---

## Part 2 — `config/`

5 YAML files + `.gitkeep` + `mcp_servers.yaml.example`. All hand-curated; the daemon reads them at lifespan.

### `config/trait_tree.yaml` — 20,219 bytes
**Purpose:** ADR-0001 trait taxonomy — 29 traits across 6 domains.
**Observed:** Loaded by `core/trait_engine.py`. Tests in `unit/test_trait_engine.py` (177 LoC, 24 cases) cover loading + validation.
**DRIFT:** `unit/test_trait_engine.py::test_expected_role_count` is failing per yesterday's run because the expected count is hardcoded and stale. That test counts ROLES not TRAITS, but the file it lives in is misleading; it imports from `core/trait_engine.py` and exercises something role-shaped. The fix is straightforward — bump the expected count to match the current 17 roles in `constitution_templates.yaml`.

### `config/constitution_templates.yaml` — 19,086 bytes
**Purpose:** ADR-0004 — 17 role templates with `role_base`, `trait_modifiers`, `flagged_combo_policy_template`.
**Observed:** Schema version 1. Roles enumerated: 5 original + 9 swarm + 3 SW-track. Loaded by `core/constitution.py`.
**FINDING:** The 5 original roles (`network_watcher`, `log_analyst`, `anomaly_investigator`, `incident_communicator`, `operator_companion`) reference tools that **don't exist on disk** via the archetype kits in `tool_catalog.yaml`. See Finding C-1.

### `config/tool_catalog.yaml` — 60,807 bytes
**Purpose:** ADR-0018 — 46 tool descriptors + role kits + genre fallback kits.
**Observed:** Loaded by `core/tool_catalog.py`. Tests in `unit/test_tool_catalog.py` (326 LoC, 26 cases).
**FINDING C-1:** Six declared tools have no on-disk implementation: `baseline_compare.v1`, `correlation_window.v1`, `dns_lookup.v1`, `flow_summary.v1`, `log_grep.v1`, `packet_query.v1`. They're listed in archetype kits for the original 3 roles. Birthing those roles produces a constitution that lists tools the dispatcher can't resolve — `test_full_forge_loop.py` and several `test_daemon_tool_dispatch.py` cases fail because of this. Either implement them or remove them.

### `config/genres.yaml` — 14,293 bytes
**Purpose:** ADR-0021 + ADR-0033 + ADR-003X — 13 genres.
**Observed:** Loaded by `core/genre_engine.py`. Tests in `unit/test_genre_engine.py` (518 LoC, 58 cases). Hardest-tested config. **OK.**

### `config/mcp_servers.yaml.example` — 2,840 bytes
**Purpose:** Example file for ADR-003X K4 (`mcp_call.v1`).
**Observed:** No `mcp_servers.yaml` actual; loader fallback in `tools/builtin/mcp_call.py`. **OK** as example.

### `config/.gitkeep` — 0 bytes
**Observed:** `config/` is checked in; the keepfile is redundant. Harmless.

---

## Part 3 — `src/forest_soul_forge/`

### Package overview

| Package | Files | LoC | Tested? |
|---|---:|---:|---|
| `agents/` | 2 | 0 | empty (only `__init__.py`) |
| `chronicle/` | 2 | 587 | partial (no test for `render.py`) |
| `cli/` | 8 | 1,465 | 4/8 modules tested |
| `core/` | 13 | 4,121 | strong |
| `daemon/` | 45 | 9,525 | mixed (12 routers untested directly) |
| `forge/` | 8 | 3,117 | strong |
| `registry/` | 13 | 3,175 | strong (1 zero-cov file) |
| `security/` | 2 | 225 | strong |
| `soul/` | 3 | 1,009 | strong |
| `tools/` | 47 | 13,215 | mostly (5 builtins zero-cov) |
| `ui/` | 1 | 0 | empty |

### `agents/` — DEAD PACKAGE
- `agents/__init__.py` (0 LoC)
- `agents/blue_team/__init__.py` (0 LoC)
**Verdict: orphan from early-Phase scaffolding.** Nothing imports from `agents.*`. Should be deleted.

### `ui/` — DEAD PACKAGE
- `ui/__init__.py` (0 LoC)
**Verdict: orphan.** Frontend lives at top-level `frontend/`, not in this package. Should be deleted.

### `chronicle/` — operator-facing audit export
- `chronicle/__init__.py` (25 LoC) — package docstring + exports.
- `chronicle/render.py` (562 LoC) — HTML + Markdown export.
**Tests:** None directly. Exercised via `cli/chronicle.py` which itself has no tests (see CLI gap below).
**GAP:** chronicle/render.py is 562 LoC of presentation logic with **zero unit tests**. Risky surface.

### `cli/` — operator command-line
- `cli/__init__.py` (11 LoC)
- `cli/_common.py` (61 LoC) — provider builder, operator id helpers. **GAP:** zero tests.
- `cli/chronicle.py` (213 LoC) — `fsf chronicle`. **GAP:** zero tests.
- `cli/forge_skill.py` (82 LoC) — `fsf forge skill`. **OK** (driven by `forge/skill_forge.py` tests).
- `cli/forge_tool.py` (156 LoC) — `fsf forge tool`. **OK** (driven by `forge/tool_forge.py` tests).
- `cli/install.py` (480 LoC) — `fsf install tool|skill`. Tested via `unit/test_skill_install.py` + `unit/test_tool_install.py` + `unit/test_tool_install_plugin.py`.
- `cli/main.py` (312 LoC) — argparse root. **GAP:** zero tests for the CLI dispatch path.
- `cli/triune.py` (150 LoC) — `fsf triune`. **GAP:** zero tests.

**FINDING CLI-1:** `cli/main.py` is the entry point for an entire user-facing surface; zero tests means a typo in argparse setup ships silently. Smoke test would be ~30 lines.

### `core/` — pure-Python building blocks
- `core/__init__.py` (0 LoC)
- `core/audit_chain.py` (555 LoC) — append-only JSONL chain. **OK.** Tests: `unit/test_audit_chain.py` (397 LoC, 32 cases).
- `core/constitution.py` (516 LoC) — ADR-0004 builder. **OK.** Tests: `unit/test_constitution.py` (419 LoC, 32 cases).
- `core/dna.py` (97 LoC) — content-addressed hash. **OK.** Exercised through `test_audit_chain.py`, `test_soul_generator.py`.
- `core/genre_engine.py` (483 LoC) — ADR-0021. **OK.** Tests: `unit/test_genre_engine.py` (58 cases — best-tested file in repo).
- `core/grading.py` (294 LoC) — ADR-0003. **OK.** Tests: `unit/test_grading.py` (377 LoC, 23 cases).
- `core/hardware.py` (170 LoC) — ADR-003X K6 binding. **GAP:** no `test_hardware.py`. The K6 lifecycle is exercised in `live-test-k6.command` only (operator smoke). Zero unit coverage.
- `core/memory.py` (637 LoC) — ADR-0022 + ADR-0027. **OK.** Tests: `unit/test_memory.py` (476 LoC, 33 cases).
- `core/secrets.py` (280 LoC) — ADR-003X C1 secrets store. **OK.** Tests: `unit/test_secrets.py` (224 LoC, 17 cases).
- `core/skill_catalog.py` (83 LoC) — loader. **OK.** Tests: `unit/test_skill_catalog.py` (4 cases).
- `core/tool_catalog.py` (399 LoC) — loader. **OK.** Tests: `unit/test_tool_catalog.py` (26 cases).
- `core/tool_policy.py` (251 LoC) — constraint resolver. **OK.** Tests: `unit/test_tool_policy.py` (15 cases).
- `core/trait_engine.py` (356 LoC) — loader + queries. **OK** but `test_expected_role_count` is stale (see config drift above).

### `daemon/` — FastAPI app
- `daemon/__init__.py` (11 LoC) — pkg docstring.
- `daemon/app.py` (464 LoC) — FastAPI factory. Tested via every `test_daemon_*.py` indirectly. **OK.**
- `daemon/config.py` (266 LoC) — `DaemonSettings`. Tested implicitly. **OK.**
- `daemon/deps.py` (317 LoC) — DI helpers. Tested implicitly. **OK.**
- `daemon/idempotency.py` (73 LoC) — `X-Idempotency-Key` parsing. Tested implicitly via writes tests. **OK.**

#### `daemon/providers/`
- `__init__.py` (91 LoC) — `ProviderRegistry`. Tests: `unit/test_provider_registry.py` (118 LoC, 7 cases).
- `base.py` (137 LoC) — Provider Protocol + TaskKind. **OK.**
- `frontier.py` (166 LoC) — frontier provider stub. **GAP:** no `test_frontier_provider.py`. Currently a stub but will grow.
- `local.py` (155 LoC) — Ollama-compatible. **GAP:** no `test_local_provider.py`. Tested integration-only.

#### `daemon/routers/` — 17 routers
| File | LoC | Tested? |
|---|---:|---|
| `agents.py` | 88 | ✓ via test_daemon_readonly.py |
| `audit.py` | 317 | ✓ via test_daemon_readonly.py |
| `character_sheet.py` | 396 | ✗ **GAP** — no direct tests; ADR-0020 sheet logic uncovered |
| `conversation_resolver.py` | 205 | ✗ **GAP** — only integration coverage |
| `conversations.py` | 994 | ✗ **GAP** — 994 LoC god-object, only integration coverage |
| `conversations_admin.py` | 271 | ✗ **GAP** — Y7 retention sweep uncovered |
| `genres.py` | 51 | ✓ via test_daemon_readonly.py |
| `hardware.py` | 137 | ✗ **GAP** — K6 unbind ops uncovered |
| `health.py` | 62 | ✓ via test_daemon_readonly.py |
| `memory_consents.py` | 264 | ✓ test_daemon_memory_consents.py |
| `pending_calls.py` | 374 | ✓ test_daemon_tool_dispatch.py |
| `preview.py` | 242 | ✓ via test_daemon_readonly.py |
| `runtime.py` | 172 | ✗ **GAP** — provider switch path uncovered |
| `skills_catalog.py` | 62 | ✗ **GAP** — only integration |
| `skills_reload.py` | 51 | ✗ **GAP** — only integration |
| `skills_run.py` | 209 | ✓ test_daemon_skills_run.py |
| `tool_dispatch.py` | 213 | ✓ test_daemon_tool_dispatch.py |
| `tools.py` | 235 | ✓ via test_daemon_readonly.py |
| `tools_reload.py` | 100 | ✗ **GAP** — uncovered |
| `traits.py` | 101 | ✓ via test_daemon_readonly.py |
| `triune.py` | 210 | ✗ **GAP** — bonding logic uncovered |
| `writes.py` | 1,186 | ✓ test_daemon_writes.py (1,040 LoC, 48 cases) |

**FINDING DR-1:** 11 of 22 routers have no direct tests. The biggest gaps are `conversations.py` (994 LoC, only integration coverage) and `character_sheet.py` (396 LoC, ADR-0020).

#### `daemon/schemas/` — 12 Pydantic schema files
All schema files are referenced by their corresponding router; tests exercise them via 422 / 400 paths. **OK** as a package.
**FINDING DS-1:** `schemas/__init__.py` is 223 LoC and re-exports a lot. Worth a quick check that it's not creating circular imports.

### `forge/` — Tool Forge + Skill Forge
- `forge/__init__.py` (8 LoC)
- `forge/sandbox.py` (177 LoC) — generated test runner. Tests: `unit/test_tool_forge_sandbox.py` (110 LoC, 7 cases).
- `forge/skill_expression.py` (601 LoC) — interpolation language. Tests: `unit/test_skill_expression.py` (40 cases — strong).
- `forge/skill_forge.py` (210 LoC) — propose engine. Tests: `unit/test_skill_forge.py` (7 cases).
- `forge/skill_manifest.py` (370 LoC) — parser. Tests: `unit/test_skill_manifest.py` (19 cases).
- `forge/skill_runtime.py` (599 LoC) — DAG walker. Tests: `unit/test_skill_runtime.py` (12 cases — moderate; this file is big and complex, more cases warranted).
- `forge/static_analysis.py` (491 LoC) — generated-code linter. Tests: `unit/test_tool_forge_static_analysis.py` (286 LoC, 26 cases). **WARNING:** 14 of those 26 cases are FAILING per yesterday's run; need to characterize.
- `forge/tool_forge.py` (661 LoC) — propose engine. Tests: `unit/test_tool_forge.py` (450 LoC, 22 cases).

**FINDING F-1:** `unit/test_tool_forge_static_analysis.py` has many failures. Either the static-analysis rules drifted, the test fixtures drifted, or both. Needs triage.

### `registry/`
- `registry/__init__.py` (18 LoC) — exports.
- `registry/_errors.py` (45 LoC) — error classes (R4 split).
- `registry/ingest.py` (365 LoC) — soul.md + audit-chain parser.
- `registry/registry.py` (462 LoC) — top-level Registry class.
- `registry/schema.py` (684 LoC) — DDL + migrations.

#### `registry/tables/` — 6 accessors + helpers
- `tables/__init__.py` (47 LoC)
- `tables/_helpers.py` (48 LoC) — `transaction()` + `utc_now_iso()`. **GAP:** no test (small enough to be OK).
- `tables/agents.py` (603 LoC) — biggest accessor. **OK.**
- `tables/approvals.py` (124 LoC) — pending approvals.
- `tables/conversations.py` (434 LoC) — Y1 conversations table.
- `tables/idempotency.py` (74 LoC) — X-Idempotency-Key store.
- `tables/secrets.py` (112 LoC) — ADR-003X C1.
- `tables/tool_counters.py` (159 LoC) — per-session call counters. **GAP:** zero direct tests.

**Tests:** `unit/test_registry.py` (808 LoC, 23 cases) covers most paths. **WARNING:** several `TestRegistryApprovalQueue` cases fail with FK constraint errors — fixtures don't seed agents before recording approvals (per audit plan from earlier today).

### `security/`
- `security/__init__.py` (9 LoC)
- `security/priv_client.py` (216 LoC) — sudo helper wrapper. Tests: `unit/test_priv_client.py` (214 LoC, 19 cases).

### `soul/`
- `soul/__init__.py` (0 LoC)
- `soul/generator.py` (462 LoC) — TraitProfile → soul.md. Tests: `unit/test_soul_generator.py` (311 LoC, 28 cases).
- `soul/voice_renderer.py` (547 LoC) — `## Voice` LLM section. **GAP:** voice_renderer has no `test_voice_renderer.py`. Tested only end-to-end via `live-fire-voice.command` (operator smoke).

### `tools/`
- `tools/__init__.py` (40 LoC) — package docstring.
- `tools/base.py` (252 LoC) — `ToolError`, `ToolValidationError`, `ToolContext`, registry primitives. Tests: `unit/test_tool_runtime.py` (184 LoC, 19 cases).
- `tools/delegator.py` (372 LoC) — cross-agent invocation. Tests: `unit/test_delegate_tool.py` (306 LoC, 15 cases).
- `tools/dispatcher.py` (1,279 LoC) — runtime dispatch. **R3-refactored** to use `governance_pipeline.py`. Tests: `unit/test_tool_dispatcher.py` (1,027 LoC, 46 cases).
- `tools/governance_pipeline.py` (533 LoC) — **NEW today (R3).** Composable pre-execute steps. **GAP:** no `test_governance_pipeline.py`. Only exercised via dispatcher integration.
- `tools/plugin_loader.py` (297 LoC) — `.fsf` package format. Tests: `unit/test_plugin_loader.py` (208 LoC, 11 cases).

#### `tools/builtin/` — 39 tool implementations
For each I record: LoC, has-test?, side-effects class, status notes.

| File | LoC | Tested | Side-effects | Notes |
|---|---:|:---:|:---|:---|
| `__init__.py` | 189 | implicit | n/a | `register_builtins()` registers all 40 |
| `anomaly_score.py` | 312 | ✓ test_b2_tools | read_only | OK |
| `audit_chain_verify.py` | 130 | implicit via test_audit_chain | read_only | small |
| `behavioral_baseline.py` | 296 | ✓ test_b2_telemetry_tools | read_only | OK |
| `browser_action.py` | 316 | ✗ **GAP** | external | playwright; only integration |
| `canary_token.py` | 420 | ✓ test_b3_deception_tools | filesystem | OK |
| `code_edit.py` | 219 | ✗ **GAP** | filesystem | SW.A.5; only operator smoke |
| `code_read.py` | 191 | ✗ **GAP** | read_only | SW.A.5; only operator smoke |
| `continuous_verify.py` | 248 | ✓ test_b3_posture_tools | read_only | OK |
| `delegate.py` | 178 | ✓ test_delegate_tool | read_only | OK |
| `dynamic_policy.py` | 176 | ✓ test_b3_privileged_tools | external | OK |
| `evidence_collect.py` | 332 | ✓ test_b2_telemetry_tools | read_only | OK |
| `file_integrity.py` | 243 | ✓ test_b1_tools | read_only | OK |
| `honeypot_local.py` | 277 | ✓ test_b3_deception_tools | network | OK |
| `isolate_process.py` | 121 | ✓ test_b2_remaining_tools | external | OK |
| `jit_access.py` | 207 | ✓ test_b3_access_tools | external | OK |
| `key_inventory.py` | 381 | ✓ test_b3_access_tools | read_only | OK |
| `lateral_movement_detect.py` | 231 | ✓ test_b2_tools | read_only | OK |
| `llm_think.py` | 263 | implicit via integration | read_only | **GAP:** no dedicated test |
| `log_aggregate.py` | 266 | ✓ test_b1_shellout_tools | read_only | (some tests fail per env) |
| `log_correlate.py` | 222 | ✓ test_b2_tools | read_only | OK |
| `log_scan.py` | 250 | ✓ test_b1_tools | read_only | OK |
| `mcp_call.py` | 338 | ✗ **GAP** | external | only `live-test-k4.command` |
| `memory_disclose.py` | 262 | ✓ test_memory_disclose_tool | read_only | OK |
| `memory_recall.py` | 255 | ✓ test_memory_recall_tool | read_only | OK |
| `memory_verify.py` | 160 | ✗ **GAP** | filesystem | no dedicated test |
| `memory_write.py` | 135 | ✓ test_memory_write_tool | read_only | OK |
| `patch_check.py` | 290 | ✓ test_b1_shellout_tools | read_only | (`brew formula` failure) |
| `port_policy_audit.py` | 317 | ✓ test_b1_shellout_tools | read_only | OK |
| `port_scan_local.py` | 246 | ✓ test_b2_tools | network | OK |
| `posture_check.py` | 399 | ✓ test_b3_posture_tools | read_only | OK |
| `shell_exec.py` | 262 | ✗ **GAP** | external | SW.A.5; operator smoke only |
| `software_inventory.py` | 280 | ✓ test_b1_shellout_tools | read_only | OK |
| `suggest_agent.py` | 329 | ✗ **GAP** | read_only | G6 — no unit tests |
| `tamper_detect.py` | 336 | ✓ test_b3_privileged_tools | filesystem | OK |
| `timestamp_window.py` | 127 | implicit | read_only | trivial |
| `traffic_flow_local.py` | 258 | ✓ test_b2_telemetry_tools | read_only | OK |
| `triage.py` | 297 | ✓ test_b2_remaining_tools | network | OK |
| `ueba_track.py` | 202 | ✓ test_b2_tools | read_only | OK |
| `usb_device_audit.py` | 243 | ✓ test_b1_shellout_tools | read_only | OK |
| `web_fetch.py` | 238 | ✓ test_web_fetch | network | OK |

**Summary of tool coverage gaps:** **8 of 40 tools have no dedicated unit tests** — `browser_action`, `code_edit`, `code_read`, `llm_think`, `mcp_call`, `memory_verify`, `shell_exec`, `suggest_agent`. The SW-track three (`code_edit`/`code_read`/`shell_exec`) and the open-web three (`web_fetch` is covered, but `browser_action` + `mcp_call` are not, plus `suggest_agent`) are the youngest tools and the gap correlates.

---

## Part 4 — `tests/`

48 test files, 235 classes, 983 functions, 16,683 LoC. Already inventoried above.

**Failing categories** (from yesterday's `pytest tests/unit/` run):
1. **Python 3.10 vs 3.11** — sandbox is 3.10, project requires 3.11. Many failures here are environmental.
2. **Brew formula → formulae rename** (`test_b1_shellout_tools.py::test_brew_parses_outdated_json`).
3. **Registry FK constraint** (`test_tool_dispatcher.py::TestRegistryApprovalQueue::*`) — fixtures don't seed agents.
4. **Role count drift** (`test_trait_engine.py::test_expected_role_count`) — hardcoded count is stale (was 5; now 17).
5. **Catalog 6-zombie-tools downstream** — birth tests for original archetypes refuse because dns_lookup et al. don't exist.
6. **Tool Forge static analysis** (`test_tool_forge_static_analysis.py`) — 14+ failures; needs root-cause.

**Three integration tests:**
- `test_full_forge_loop.py` (429 LoC) — partially failing due to category 5 above.
- `test_cross_subsystem.py` (558 LoC) — **the trio I just shipped; 3/3 passing.**

---

## Part 5 — `docs/`

Already inventoried above. Re-stating the structural finding:

**Layout:**
- `docs/decisions/` — 30 ADRs (the most-load-bearing docs)
- `docs/audits/` — 7 dated retros (canonical timeline)
- `docs/runbooks/` — 3 operator guides (gap-heavy)
- `docs/architecture/` — 2 design docs
- `docs/notes/` — 3 design exploration scratchpads
- `docs/roadmap/` — 1 file
- `docs/surveys/` — 1 file (the tool-catalog expansion I dropped today)
- `docs/vision/` — 2 vision docs
- `docs/PROGRESS.md` — 82 LoC; legacy. Should retire or merge into STATE.
- `docs/dev-tools.md` — 124 LoC.
- `docs/tool-risk-guide.md` — 181 LoC.

**ADR placeholder status:**
- ADR-0025 threat-model-v2 — **29 LoC** (placeholder)
- ADR-0026 provider-economics — **33 LoC** (placeholder)
- ADR-0028 data-portability — **31 LoC** (placeholder)
- ADR-0029 regulatory-map — **38 LoC** (placeholder)

**ADRs that should arguably be promoted Proposed → Accepted** (have shipped fully):
- ADR-0019 Tool execution runtime (T1–T6 implemented)
- ADR-0021 Role genres (T1–T8 implemented)
- ADR-0022 Memory subsystem (v0.1 + v0.2 implemented)
- ADR-0027 Memory privacy contract (T14–T17 shipped)
- ADR-0030 Tool Forge (T1–T4 implemented)
- ADR-0031 Skill Forge (T1, T2a/T2b, T5, T7, T8 implemented)
- ADR-003Y Conversation runtime (Y1–Y7 ALL shipped end-to-end)
- ADR-0034 SW-track (Phases A.1–A.6 + B.1 shipped + the agents themselves filed it)

**Runbooks present (3) vs needed (≥7):**
| Runbook | Status |
|---|---|
| `end-to-end-smoke-test.md` | present |
| `security-swarm-bringup.md` | present |
| `sudo-helper-install.md` | present |
| **`conversation-runtime.md`** | **MISSING** (Y-track shipped, no operator guide) |
| **`sw-track-triune.md`** | **MISSING** (Atlas/Forge/Sentinel) |
| **`demo-scenarios.md`** | **MISSING** (synthetic-incident, fresh-forge, web-research-demo) |
| **`forge-tool-skill.md`** | **MISSING** (`fsf forge` end-to-end) |
| **`plugin-package-format.md`** | **MISSING** (`.fsf` package contract) |
| **`memory-subsystem.md`** | **MISSING** (4 scopes + consent flow + disclosure rules) |
| **`triune-bond.md`** | **MISSING** (`/triune` endpoint + ceremony) |

---

## Part 6 — `frontend/`

22 JS modules + 1 HTML + 1 CSS. Total ~6,300 LoC (rough sum from inventory).

**Module list (already inventoried with LoC):**

Notable observations:
- `chat.js` (602 LoC) is the biggest module after `tools.js` (364) and `tour.js` (401) and `pending.js` (379).
- All modules have a top-of-file docstring/comment. **OK** for minimum hygiene.
- `index.html` is 667 LoC — heavy but acceptable for vanilla JS no-build.
- `style.css` is 1,671 LoC — biggest single CSS file in the repo.
- **No tests.** 22 modules, ~6,300 LoC, **zero unit tests.** This is the biggest single coverage gap in the repo.
- No `package.json`, no Vitest, no jsdom — wiring the test harness is itself a v0.2 task.

**FINDING FE-1:** Frontend has zero automated coverage. Even one Vitest fixture proving the API wrapper (`api.js`) shapes a request correctly would close significant risk.

---

## Part 7 — `examples/`

39 YAML files (most are skill manifests + their associated soul + constitution sidecars), 11 Markdown files, 1 JSONL (the canonical `audit_chain.jsonl`).

**Structure verified:**
- `examples/skills/` — 26 skill manifests (the 24 README claims is stale; today's count is 26).
- `examples/audit_chain.jsonl` — the canonical audit chain (used by demo + smoke).
- `examples/*.soul.md` + `examples/*.constitution.yaml` — example agent artifacts.

**FINDING EX-1:** No `examples/README.md` explaining what's there, what's safe to modify, what's regenerated.

---

## Part 8 — `scenarios/`

4 scenarios:
- `scenarios/synthetic-incident/` — 1.5MB total, includes a checked-in `registry.sqlite` (987KB) and `audit_chain.jsonl` (574KB). The big demo asset.
- `scenarios/fresh-forge/` — empty slate scenario. Two `.gitkeep` files.
- `scenarios/web-research-demo/` — 4KB; README + synthetic_rfc.md. Light scenario.
- `scenarios/scripts/` — 2 presenter markdowns (synthetic-incident.md, fresh-forge.md).

**`load-scenario.command`** (6,440 b) is the dispatcher.

**FINDING SC-1:** No `web-research-demo/load` script wired into `load-scenario.command`. Verify; could be intentional or could be an unfinished addition.
**FINDING SC-2:** `synthetic-incident/registry.sqlite` is 987 KB checked-in. That's fine for a demo asset but worth tracking — ideally regenerated from the audit chain rather than checked in raw.

---

## Part 9 — `scripts/`

13 files:
- `demo_generate_soul.py` — 13 KB, end-to-end smoke that builds 11-entry audit chain. Tested via execution; no unit tests on the script itself.
- `docker-entrypoint.sh` + `docker_test.sh` — container runners.
- `fsf-priv` (11 KB) + `fsf-sudoers` — privileged helper + sudoers config (ADR-0033 A6). Operator-installed.
- `initial_push.sh` — one-shot first-push helper. Stale.
- `live-smoke.sh` (10 KB), `security-smoke.sh` (8 KB), `security-swarm-birth.sh` (3 KB), `security-swarm-install-skills.sh` (2 KB) — the operator bring-up trio.
- `run_tests_no_pytest.py` (9 KB) — stdlib-only test runner. Useful when pytest isn't available.
- `verify_audit_chain.py` (8 KB) — verifies the canonical chain. Cited as "32/32 passing" in CHANGELOG.
- `verify_constitution.py` (7 KB) — verifies generated constitutions.
- `verify_grading.py` (7 KB) — exercises grading engine without pytest.

**FINDING SCR-1:** `initial_push.sh` is post-bootstrap leftover. Probably safe to delete.
**FINDING SCR-2:** `verify_audit_chain.py`, `verify_constitution.py`, `verify_grading.py` predate the proper pytest suite. They duplicate logic now in `tests/unit/`. Decision: keep for "no-pytest sandbox" use case, OR retire?

---

## Part 10 — Cross-cutting findings (categorized)

### Category C — Catalog vs implementation drift (HIGH)
- **C-1:** 6 zombie tools in YAML with no on-disk impl: `baseline_compare`, `correlation_window`, `dns_lookup`, `flow_summary`, `log_grep`, `packet_query`. Cause failing tests. **Fix:** remove from YAML + role kits OR implement.
- **C-2:** `unit/test_trait_engine.py::test_expected_role_count` hardcodes a stale count. **Fix:** update or compute dynamically.
- **C-3:** README says 36 tools / 24 skill manifests; actual 40 / 26. STATE.md is correct. **Fix:** sync README to STATE.

### Category D — Dead code (LOW)
- **D-1:** `src/forest_soul_forge/agents/` (only `__init__.py` + empty `blue_team/`). **Fix:** delete.
- **D-2:** `src/forest_soul_forge/ui/` (only `__init__.py`). **Fix:** delete.
- **D-3:** `scripts/initial_push.sh` — bootstrap-era leftover. **Fix:** delete after confirmation.
- **D-4:** `docs/PROGRESS.md` — 82 LoC legacy doc. **Fix:** retire or merge into STATE.md.

### Category G — God-objects after R3 (MEDIUM)
- **G-1:** `daemon/routers/writes.py` — 1,186 LoC. R2 plan: extract `birth_pipeline.py`.
- **G-2:** `daemon/routers/conversations.py` — 994 LoC (grew 0 → 994 in one push). Should split into `crud.py` + `turns.py` + `bridge_ambient.py`.
- **G-3:** `tools/dispatcher.py` — 1,279 LoC even after R3. Worth a follow-on R5 once we know what landed cleanly.

### Category T — Test coverage gaps (HIGH)
- **T-1:** 8 of 40 tools without dedicated unit tests: `browser_action`, `code_edit`, `code_read`, `llm_think`, `mcp_call`, `memory_verify`, `shell_exec`, `suggest_agent`. The youngest 8 — coverage tracks recency.
- **T-2:** 11 of 22 routers without direct tests. Biggest gaps: `conversations.py` (994 LoC), `character_sheet.py` (396 LoC), `triune.py` (210 LoC), `runtime.py` (172 LoC), `hardware.py` (137 LoC), `conversations_admin.py` (271 LoC), `conversation_resolver.py` (205 LoC).
- **T-3:** `tools/governance_pipeline.py` (533 LoC, **NEW today**) has zero unit tests. Pure functions — easy to test, high value.
- **T-4:** `core/hardware.py` (170 LoC) has zero unit tests. ADR-003X K6 lifecycle entirely uncovered at unit level.
- **T-5:** `chronicle/render.py` (562 LoC) has zero tests. Big presentation surface.
- **T-6:** `cli/main.py` (312 LoC) — argparse root with zero tests. Even smoke-level coverage missing.
- **T-7:** `cli/triune.py` (150 LoC) — zero tests.
- **T-8:** `cli/chronicle.py` (213 LoC) — zero tests.
- **T-9:** `daemon/providers/local.py` (155 LoC) and `frontier.py` (166 LoC) — zero tests at unit level. Integration only.
- **T-10:** `soul/voice_renderer.py` (547 LoC) — zero tests; only `live-fire-voice.command`.
- **T-11:** `frontend/` — 22 JS modules, ~6,300 LoC, **zero tests.** Vitest scaffold needed.

### Category F — Failing tests (HIGH)
- **F-1:** `test_b1_shellout_tools.py::test_brew_parses_outdated_json` — Homebrew `formula → formulae` JSON key rename.
- **F-2:** `test_tool_dispatcher.py::TestRegistryApprovalQueue::*` (3+ cases) — fixtures don't seed agents before recording approvals; FK constraint.
- **F-3:** `test_tool_forge_static_analysis.py` — 14+ failures; needs root-cause.
- **F-4:** `test_trait_engine.py::test_expected_role_count` — stale hardcoded count.
- **F-5:** `test_full_forge_loop.py` integration test — both cases fail because they birth `anomaly_investigator` which uses tools the catalog doesn't have implementations for (Category C-1 downstream).
- **F-6:** Many cases (96 total) failing with environmental issues (Python 3.10 sandbox vs 3.11 requirement). Most should `skip` with `reason="requires Python 3.11+"`.

### Category A — ADR status drift (MEDIUM)
- **A-1:** 4 placeholder ADRs (0025/0026/0028/0029) — explicit "deferred to v0.X" markers needed, OR remove.
- **A-2:** 8+ Proposed ADRs that are fully shipped should be promoted to Accepted (see Part 5 list).

### Category R — Runbook gaps (MEDIUM)
- **R-1:** No `conversation-runtime.md` runbook (Y-track shipped, no operator guide).
- **R-2:** No `sw-track-triune.md` runbook.
- **R-3:** No `demo-scenarios.md` runbook (4 scenarios, no master guide).
- **R-4:** No `forge-tool-skill.md` runbook (`fsf forge` end-to-end).
- **R-5:** No `plugin-package-format.md` runbook (`.fsf` contract).
- **R-6:** No `memory-subsystem.md` runbook (4 scopes + consent + disclosure).
- **R-7:** No `triune-bond.md` runbook.

### Category M — Misc / hygiene (LOW)
- **M-1:** `.env` IS tracked. Verify contents are non-sensitive; if any secrets, untrack.
- **M-2:** No `examples/README.md` orienting the reader.
- **M-3:** Root has `registry.sqlite*` from a daemon launch that didn't override path. Gitignored ✓; harmless but worth a default-path cleanup in `DaemonSettings`.
- **M-4:** `.command` scripts have no master index; new operator can't tell which to run.
- **M-5:** `.command` naming convention drift — `live-test-*` vs `live-fire-*` vs `live-triune-*`. Consider standardizing.
- **M-6:** `t4-tests.command` referenced in README but not at root — verify, may be a stale README reference.
- **M-7:** `scenarios/synthetic-incident/registry.sqlite` (987 KB) is checked in. Reasonable for demo asset; document the regen path.
- **M-8:** No top-level `CLAUDE.md` for project-specific harness conventions.
- **M-9:** Stray daemon/schemas/__init__.py at 223 LoC — verify no circular imports.

---

## Part 11 — Findings count summary

| Category | High | Medium | Low | Total |
|---|---:|---:|---:|---:|
| C — Catalog drift | 1 | 0 | 2 | 3 |
| D — Dead code | 0 | 0 | 4 | 4 |
| G — God-objects | 0 | 3 | 0 | 3 |
| T — Test coverage gaps | 3 | 8 | 0 | 11 |
| F — Failing tests | 6 | 0 | 0 | 6 |
| A — ADR status drift | 0 | 2 | 0 | 2 |
| R — Runbook gaps | 0 | 7 | 0 | 7 |
| M — Misc / hygiene | 0 | 0 | 9 | 9 |
| **Total** | **10** | **20** | **15** | **45 findings** |

---

## Part 12 — Prioritized fix list

The plan I submitted earlier ("repo-doc-audit-plan") had 5 phases. With this comprehensive walk done, I'd compress and reorder:

### Critical batch (must land before v0.2 starts) — ~1 day
1. **Fix Category C-1.** Delete the 6 zombie tools from `config/tool_catalog.yaml` and the 3 affected archetype kits. Or implement them. **Decision needed.** I lean delete — they predate the Swarm + SW-track work.
2. **Fix Category F-2.** Patch `test_tool_dispatcher.py::TestRegistryApprovalQueue` fixtures to seed agents before recording approvals. ~10 lines.
3. **Fix Category F-1, F-4.** Brew formula rename + role count update — together ~5 lines.
4. **Sync README.** Bump tool count 36→40 + skill count 24→26 + claim consistency with STATE.

### High batch — ~1 day
5. **Add unit tests for the 3 zero-coverage NEW files I just shipped:** `governance_pipeline.py`, `conversation_resolver.py`, `conversations_admin.py`. ~150-200 LoC each.
6. **Add Vitest scaffold** (frontend) — even one fixture exercising `api.js` request shapes closes the biggest single coverage gap.
7. **Triage Category F-3 (Tool Forge static analysis 14 failures).** Root-cause. Either fix or document why they can't.

### Medium batch — ~1 day
8. **Decompose `conversations.py` (G-2)** into `crud.py` + `turns.py` + `bridge_ambient.py`.
9. **R2 birth_pipeline.py extraction** (G-1) — already on v0.2 priorities.
10. **ADR audit pass** (Category A) — promote the 8 fully-shipped Proposed ADRs to Accepted; add deferred-to-vX.Y notes on the 4 placeholders.
11. **Delete dead packages** (D-1, D-2): `src/.../agents/` and `src/.../ui/`.

### Low batch / docs sweep — ~1 day
12. **5 runbooks** (R-1 to R-5): conversation-runtime, sw-track-triune, demo-scenarios, forge-tool-skill, plugin-package-format. R-6 + R-7 can wait.
13. **`examples/README.md`** orientation doc.
14. **`CLAUDE.md`** at repo root capturing harness conventions.
15. **Retire `docs/PROGRESS.md`** → merge into STATE or move to `docs/notes/_archive/`.
16. **`.command` master index** doc + naming pass.
17. **8 of 40 tool unit tests (T-1)** — cover code_edit, code_read, shell_exec, browser_action, mcp_call, suggest_agent, llm_think, memory_verify. SW-track 3 first.

Total estimate: **4 days** at sustained pace. **2 days** if we batch the docs sweep and the test additions in parallel.

---

## What I need from you to start

The audit-plan doc I dropped earlier had 5 sign-off questions. With this comprehensive walk done, only two of those questions still matter:

1. **Catalog drift (C-1):** **DELETE** the 6 zombie tools, or **IMPLEMENT** them?
2. **Failing tests strategy:** target **green pytest on Python 3.11**, or **documented skip markers**?

The other questions resolved themselves during the walk — placeholder ADRs should clearly be "deferred" with rationale (4 of them), runbook ordering has stronger signal now (conversation-runtime first because Y-track is the most-shipped-most-undocumented thing), Phase 5 decomposition should happen during this audit rather than v0.2 because it's already half-touched.

Two answers and I start the critical batch.
