# Repo + documentation audit plan

**Date:** 2026-04-30
**Author:** Forest Soul Forge harness
**Status:** Draft. Operator (Alex) signs off on scope + execution order; harness then runs the audit and fills the gaps.

## Why this exists

We just shipped v0.1.0. Before piling on the v0.2 tool-catalog expansion, the repo and docs need a closing pass. The v0.1.0 README was refreshed today and `STATE.md` was refreshed yesterday, but **scanning surfaced real drift** between what the docs claim and what's on disk. This plan is the audit that closes the gaps.

Findings below are from a 30-minute scan, not exhaustive — the audit itself will be deeper. But the scan is enough to pin the structure and priorities.

## Scan findings (raw, unranked)

### Critical: catalog vs implementation drift
The tool catalog YAML at `config/tool_catalog.yaml` declares **46 tools**; the daemon registers **40** at lifespan; **6 catalog entries have no on-disk implementation**:

```
baseline_compare.v1
correlation_window.v1
dns_lookup.v1
flow_summary.v1
log_grep.v1
packet_query.v1
```

These six are referenced by the original archetype kits (`network_watcher`, `log_analyst`, `anomaly_investigator`). When agents are birthed under those archetypes, the constitution lists tools that don't exist — and the dispatcher's resolution refuses with `unknown_tool`. This is why the existing `test_full_forge_loop.py` integration test fails (the same reason my fresh integration test had to switch to `system_architect`).

This isn't a small thing. It's the root cause of one entire failing test file and represents a zombie 5-tool slice of the catalog. **Fix is binary**: either implement the 6 tools, or remove them from the YAML + role kits + ADRs. We need to decide.

### High: README count drift (post-refresh)
Today's README refresh corrected several counts but introduced new ones that are still off:

| Field | README says | Actual |
|---|---|---|
| Built-in tools registered | 36 | **40** (per `register_builtins`) — and 46 in catalog YAML |
| Skill manifests shipped | 24 | **26** (per `examples/skills/*.yaml` count) |
| Audit event types | 52 | (need to recount; may be off by 1-2 since I added Y-track events) |

### Medium: empty packages
Two top-level packages exist with only `__init__.py`:
- `src/forest_soul_forge/agents/` (has a `blue_team/` subdir but the dir contains no Python)
- `src/forest_soul_forge/ui/`

These are aspirational scaffolding from early commits. Either fill them or remove them — empty dirs in `src/` actively mislead new contributors.

### Medium: god-objects remaining
After R3 closed `dispatcher.py`, two files still over 800 LoC:

| File | LoC | Decomposition target |
|---|---|---|
| `daemon/routers/writes.py` | 1,186 | R2 plan (already in v0.2 priorities): extract `birth_pipeline.py` covering profile build → genre check → lineage → constitution write → audit emit |
| `daemon/routers/conversations.py` | 994 | Y-track grew this from 0 → 994 in one push; should split into `crud.py` + `turns.py` + `bridge_ambient.py` |

### Medium: test coverage gaps in newly-shipped code
Files that have **zero** dedicated unit-test coverage:
- `daemon/routers/conversation_resolver.py` (Y3.5 keyword-rank + parse_mentions + resolution paths)
- `tools/governance_pipeline.py` (R3 — pure functions, easy to test, currently only exercised via dispatcher integration)
- `daemon/routers/conversations_admin.py` (Y7 retention sweep)

The integration test trio I just wrote covers these end-to-end, but unit tests would catch regressions faster and document the intended behaviors.

### Medium: ADR status accuracy
30 ADRs filed. Status distribution from grepping:
- 7 Accepted: 0001-0008 (foundation), 0033 (Security Swarm)
- 19 Proposed: 0016-0024, 0027, 0030-0032, 0034, 003X, 003Y
- 4 Placeholder: 0025 threat-model-v2, 0026 provider-economics, 0028 data-portability, 0029 regulatory-map

**Status drift to investigate:** ADR-003Y went Y1→Y7 shipped end-to-end with audit chain proven; arguably should be promoted Proposed→Accepted. ADR-0034 SW-track had its phases A.1-A.6 + B.1 ship and the agents themselves filed it; same argument for promotion. ADR-0019 (T1-T6 implemented) and ADR-0021 (T1-T8 implemented) ditto. The audit will go ADR-by-ADR and propose status changes.

**Placeholder ADRs:** 0025/0026/0028/0029 are scaffolding — they exist as titles but no body. These are non-trivial topics (threat model v2, provider economics, GDPR/portability/regulatory). Leaving them as placeholders is fine if we tag them "deferred to v0.3+" explicitly. Leaving them placeholder without a deferral note is a gap.

### Low: missing operator runbooks
Only 3 runbooks exist:
- `end-to-end-smoke-test.md`
- `security-swarm-bringup.md`
- `sudo-helper-install.md`

Missing runbooks for:
- ADR-003Y conversation runtime — how to open a room, add participants, fire ambient nudges, run retention sweep
- SW-track triune — birth Atlas/Forge/Sentinel, hand them a coding task
- Demo scenarios — what's in `synthetic-incident` and `fresh-forge`, when to use each
- Forge tool/skill — `fsf forge tool` and `fsf forge skill` end-to-end
- Plugin install / `.fsf` package format

### Low: ADR-0023 benchmark suite — declared but not implemented
ADR-0023 describes a benchmark suite (provider latency, dispatch overhead, audit-chain append throughput). No `benchmarks/` directory exists. This is OK if "deferred to v0.3" is explicit; otherwise it's an unfunded design.

### Low: no CLAUDE.md
A project-root `CLAUDE.md` would let me persist project-specific conventions (naming, what counts as "in scope", what tools/MCP servers Alex prefers, etc.) so future sessions don't have to re-derive them from memory. Currently `MEMORY.md` carries some of this but it's session-scoped, not repo-scoped.

### Low: docs/PROGRESS.md vs docs/audits/ vs docs/notes/
Three parallel docs lineages exist. From eyeballing:
- `docs/PROGRESS.md` — looks legacy, single file
- `docs/audits/` — dated retros, the canonical one going forward
- `docs/notes/` — design exploration scratchpad
- `docs/roadmap/` — has one file, dated

These should consolidate. Audits become the canonical timeline, notes become archived prior-art, roadmap stays as it is. PROGRESS.md should either retire or merge into STATE.md.

### Low: pre-existing test failures
Today's run: 96 failures across the unit suite. Categories:
1. **Python 3.10 vs 3.11 mismatch** in the sandbox (project requires 3.11+; sandbox Python is 3.10). Many failures here are environmental, not code bugs.
2. **brew formula vs formulae** — `tests/unit/test_b1_shellout_tools.py::test_brew_parses_outdated_json` — Homebrew renamed the JSON key; test is stale.
3. **Registry test fixtures** — several `test_tool_dispatcher.py::TestRegistryApprovalQueue` cases have FK constraint failures; fixtures don't seed agents before recording approvals.
4. **Role count drift** — `test_trait_engine.py::test_expected_role_count` likely off because we added 3 SW-track + 9 Swarm roles since the test was written.
5. **The 6-tools catalog drift** above.

Categorizing 96 failures into these buckets and fixing or skipping each takes maybe a half-day. **None block shipping** but they do block CI ever going green.

---

## Plan structure

I'm proposing a 5-phase audit. Each phase is sized so you can review the output before approving the next. Each phase ends with a short report + a delta to STATE/README/CHANGELOG so the docs track the audit results in real time.

### Phase 1 — Catalog truth (½ day)
**Goal:** the catalog YAML, the registered impls, the role kits, and the README counts all agree.

Deliverables:
- Decision on the 6 zombie tools: implement or remove? (My read: remove from YAML + role kits + ADR-0021 mentions; the original archetype kits predate the Swarm and are now dead weight. Network-discovery is better served by ADR-003X web tools + future blue-team `nmap_scan.v1` red-team gated equivalents.)
- Patch tool catalog YAML
- Patch role kits in `tool_catalog.yaml::archetypes`
- Update README "By the numbers" to actual counts
- Update STATE.md TL;DR claim "40 builtin tools" → confirmed
- Update CHANGELOG entry

**Sign-off question:** **remove** the 6 zombie tools or **implement** them?

### Phase 2 — ADR audit (1 day)
**Goal:** every ADR's status reflects on-disk reality. Placeholders are explicitly deferred or filled.

For each of the 30 ADRs:
1. Read the ADR
2. Verify each Phase/Tranche claim against current code
3. Update Status: line + Phase status section
4. For placeholders: flip to "deferred to vX.Y" with one paragraph of rationale, OR drop the ADR
5. Cross-references — verify every "see ADR-N" link points to a real file

Output: a single audit doc summarizing each ADR's status change + a single PR-style commit on the ADR files.

**Sign-off question:** keep all 4 placeholder ADRs as "deferred" with a paragraph each, or remove them?

### Phase 3 — Test suite triage (½ day)
**Goal:** unit suite passes cleanly OR every failing test has a documented "why it's skipped" pytest marker.

Approach:
1. Categorize the 96 failures into the 5 buckets identified above
2. Fix the simple ones (role count drift, brew formula rename) — these are maybe a dozen lines total
3. Patch the registry test fixtures (FK constraint) — that's the biggest functional bug
4. Add `pytest.skip` markers with `reason=` strings to the environmental ones (Python 3.11-only behaviors)
5. Fix or skip the 6-tools catalog drift downstream tests once Phase 1 lands
6. Add unit tests for `conversation_resolver.py`, `governance_pipeline.py`, `conversations_admin.py` (the 3 zero-coverage files)

**Sign-off question:** target "pytest passes clean on Python 3.11" or just "documented skips" for the env-mismatch ones?

### Phase 4 — Documentation completeness (1 day)
**Goal:** docs reflect what the project actually does and how to operate it.

Deliverables:
- New runbooks: `conversation-runtime.md`, `sw-track-triune.md`, `demo-scenarios.md`, `forge-tool-skill.md`, `plugin-package-format.md`
- `docs/architecture/layout.md` refresh — verified directory map
- ADR cross-reference verification (catch broken links)
- README final pass — mention the new survey + audit docs
- STATE.md numerical refresh
- New `CLAUDE.md` at repo root capturing project-specific conventions for me + future sessions
- `docs/PROGRESS.md` retired or merged into STATE.md
- `docs/notes/` reviewed; obsolete notes archived to `docs/notes/_archive/`

**Sign-off question:** which 5 runbooks are highest priority? My ranking is conversation-runtime > sw-track-triune > demo-scenarios > forge-tool-skill > plugin-package-format, but you may want demo-scenarios first if a demo is imminent.

### Phase 5 — Code health closing pass (½ day)
**Goal:** dispose of leftover smells before the v0.2 tool catalog expansion piles on.

Deliverables:
- Empty `src/forest_soul_forge/agents/` and `ui/` packages: fill or remove
- R2 birth_pipeline.py extraction (decomposing writes.py from 1186 LoC) — already on v0.2 priority list, but worth doing during the audit pass since we're touching the docs anyway
- conversations.py decomposition into crud.py + turns.py + bridge_ambient.py
- TODO/FIXME markers (only 5 — all harmless?) reviewed and either resolved or converted to GitHub-style issue comments
- Frontend modules: 22 files, 0 tests. Vitest scaffold lands here (already on v0.2 priority list).

**Sign-off question:** include the R2 + conversations.py decompositions in this pass, or defer to v0.2 proper?

---

## What I am NOT proposing to do during this audit

- **Add the v0.2 tools.** The tool-catalog expansion survey is its own work stream; it doesn't belong in the audit. Audit closes, then we start with `ruff_lint.v1`.
- **Refactor for ADR-0024 horizons.** The horizons brainstorm is a v0.3+ topic; touching it during the audit just bloats scope.
- **Rewrite the frontend.** The vanilla JS approach is intentional (no build step); refactoring is a v0.2 product decision, not an audit-time decision.
- **Add new ADRs unless deletion of existing ones makes one necessary.** ADRs are decision records; auditing existing decisions is in scope, *making* new decisions is not.

---

## Estimate

| Phase | Effort | Risk |
|---|---|---|
| 1 — catalog truth | ½ day | low (deterministic deletes / counts) |
| 2 — ADR audit | 1 day | low (30 ADRs × ~15 min each = 7.5h sustained) |
| 3 — test suite triage | ½ day | medium (registry FK bug may take a couple iterations) |
| 4 — documentation | 1 day | low (5 new runbooks ≈ 1.5h each, all driven from existing code) |
| 5 — code health | ½ day | medium (decompositions touch hot files; need test pass after) |
| **Total** | **3.5 days** | |

If you want to compress, Phase 5 can defer to v0.2 proper since R2 was already on that priority list. That gets us to **3 days** without losing the audit value.

## What I need from you

Five sign-off questions, all from the phase descriptions above. Quoting them inline:

1. **Catalog drift:** remove the 6 zombie tools or implement them?
2. **Placeholder ADRs:** keep all 4 as "deferred" with paragraph rationale, or remove?
3. **Test suite target:** "pytest passes clean on Python 3.11" or "documented skips for env-mismatch"?
4. **Runbook priority:** confirm conversation > sw-track > demo > forge > plugin, or reorder?
5. **Phase 5 scope:** include R2 + conversations.py decomposition during the audit, or defer to v0.2 proper?

Once you answer those five, I start with Phase 1 immediately. If you approve all five with single-word answers I'll just go.

---

## Tracking

Each phase will land as its own commit on `main` (followable per project-instruction "no random stuff, branches off the main vision") with a matching short audit doc under `docs/audits/2026-04-30-audit-phase-N.md`. The closing commit will tag `v0.1.1` since the cumulative effect is bug-fix + accuracy + completeness, not new features.
