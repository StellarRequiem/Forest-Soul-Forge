# Changelog

All notable changes to Forest Soul Forge are documented in this file.

Format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/). This project uses [Semantic Versioning](https://semver.org/); until 1.0.0 the API is unstable.

## [Unreleased]

(Nothing yet for the next release — v0.3.0 was tagged on 2026-05-03.)

## [0.3.0] — 2026-05-03 (ADR-0036 Verifier Loop + ADR-0040 Trust-Surface Decomposition)

The v0.3 arc shipped two distinct ADRs end-to-end. ADR-0036 added
Verifier-class agents that scan memory for contradictions and flag
them for operator ratification. ADR-0040 introduced the
Trust-Surface Decomposition Rule and proved it by decomposing both
of the codebase's non-cohesive god objects (memory.py and
writes.py) into per-trust-surface packages so a constitution can
grant `allowed_paths` to one surface without inheriting the others.

**Test suite: 1968 → 2072 passing (+104).** Zero regressions
across either arc.

### ADR-0036 Verifier Loop (Bursts 65-70, 7 commits)

Verifier-class agent for scanning agent memory and flagging
contradictions for operator review. ADR-0036 §1 establishes
contradictions as a first-class memory state; T1-T7 implement the
detection, dispatch, ratification, and recall surfaces. T4
(scheduled-task substrate for autonomous scanning) is deferred
to a follow-up release.

| # | Tranche | What |
|---|---|---|
| T1 | role + claim + template | `verifier_loop` role, Guardian-genre claim, constitutional template |
| T2 | tool | `memory_flag_contradiction.v1` (operator-only, L3 initiative) |
| T3a | helper | `Memory.find_candidate_pairs` pre-filter for the scan |
| T3b | runner | `VerifierScan` LLM-dispatching scan runner |
| T5 | endpoint | `POST /verifier/scan` daemon endpoint |
| T6 | schema | v12 — `flagged_state` column on memory_contradictions |
| T7 | recall | `memory_recall.v1` extension surfacing flagged_state |

T4 (scheduled-task substrate / set-and-forget orchestrator) deferred — implementation queued for v0.4 along with the agent self-timing tool family (ADR-0041, drafted post-v0.3).

### ADR-0040 Trust-Surface Decomposition Rule (Bursts 71-81, 11 commits)

New project discipline: count trust surfaces, not LoC. A 1000-LoC
file that owns one cohesive surface is fine. A file with multiple
surfaces MUST decompose so `allowed_paths` can scope grants. The
rule pattern-matched cleanly across two structurally different
codebase shapes (mixin classes for memory, sub-routers for writes).

| Tranche | Bursts | Output |
|---|---|---|
| T1 file the ADR | 71 | `docs/decisions/ADR-0040-trust-surface-decomposition-rule.md` |
| T2 memory.py decomp | 72-76 | `memory/` package: 5 mixins (consents, verification, challenge, contradictions) + helpers + facade |
| T3 writes.py decomp | 77-80 | `writes/` package: 3 sub-routers (birth, voice, archive) + shared helpers + facade |
| T4 cross-references | 81 | STATE.md + CLAUDE.md anchored the rule for future sessions |

### Audit + remediation (Burst 82, post-v0.3.0 work)

`docs/audits/2026-05-03-full-audit.md` — full sweep triggered by
the audit-chain path mystery surfaced during Run 001. Found and
documented: README stale by entire v0.3 arc (test count, ADR
count, trait roles, audit events all wrong), STATE LoC undercounted
by ~8k, STATE commit count 79 stale, .command count 52 stale,
audit chain default path never documented in published docs,
13 zombie test agents accumulated in registry.

`dev-tools/check-drift.sh` committed as the future-proofing
sentinel — runs every numeric claim against disk reality, prints
comparison table. Use before any release tag.

### Run 001 — first autonomous coding-loop test (Burst 110)

Live-test of the Forest tool dispatch infrastructure driving an
LLM in an iterative build loop against local Ollama (qwen2.5-coder:7b).
End-to-end success on FizzBuzz: 2 turns, 15 sec wall, 4/4 tests
passed. Captured 5 driver bugs in `live-test-fizzbuzz.command`
header for future scenario runs.

## [0.2.0] — 2026-05-02 (Phase G.1.A close — programming primitives)

The 10 programming primitives that complete the SW-track agent
change-loop. Where v0.1.2 absorbed external review and added the
initiative ladder, v0.2.0 ships the actuator surface that lets
SW-track agents (Architect / Engineer / Reviewer) actually do
software work end-to-end on a repo: read existing source, run
static gates, propose a change, test it, install a missing dep
when tests reveal one. **Test suite: 1567 → 1968 passing (+401,
+25.6%).** Zero regressions across the entire v0.2 arc.

### Phase G.1.A — programming primitives (10 tools)

The change-loop primitives, in dependency order:

| # | Tool | side_effects | init_floor | Commit |
|---|---|---|---|---|
| 1 | `ruff_lint.v1` | read_only | — | `97d09b3` |
| 2 | `pytest_run.v1` | filesystem | L4 | `3628656` |
| 3 | `git_log_read.v1` | read_only | — | `6288834` |
| 4 | `git_diff_read.v1` | read_only | — | `b077d3e` |
| 5 | `git_blame_read.v1` | read_only | — | `41d642c` |
| 6 | `mypy_typecheck.v1` | read_only | — | `cfe4219` |
| 7 | `semgrep_scan.v1` | read_only | — | `52dc571` |
| 8 | `tree_sitter_query.v1` | read_only | — | `6b3cdcc` |
| 9 | `bandit_security_scan.v1` | read_only | — | `90f80d5` |
| 10 | `pip_install_isolated.v1` | filesystem | L4 | `a59d08f` |

Eight read_only inspection tools + two filesystem-tier actuators
gated at L4 (reversible-with-policy per ADR-0021-am §5). All ten
share a common safety surface: per-agent `allowed_paths` constraint
required, `resolve(strict=True) + is_relative_to` defense, subprocess
invocation with explicit timeout, structured output capped at
configurable limits, refusal-with-clear-error when the underlying
tool isn't installed.

### Architectural additions

- **ADR-0039 Distillation Forge / Swarm Orchestrator (Proposed,
  v0.4 candidate).** Hierarchical multi-agent pattern with two
  grounding features (distillation manifest + orchestration manifest).
  Architectural rule §4: "no god objects, grow new branches grounded
  by a solid feature." MLX-only dependency commitment for the
  distillation subsystem. Filed as Proposed for v0.4 — does NOT
  ship in v0.2.0; the verifier loop (ADR-0036) is a load-bearing
  prerequisite.
- **`docs/audits/2026-05-01-fsf-dispatch-overhead-benchmark-plan.md`.**
  Specifies the benchmark Burst measuring per-dispatch + audit-chain
  serialization overhead. Primary metrics: quiet-load latency, audit
  serialization curve, memory recall cost, gate costs. Outcome
  scenarios A/B/C/D.
- **External-review-readiness pass.** Updated STATE / README +
  new `docs/external-review-readiness.md` (~390 lines) gives the
  next external reviewer a 60-second snapshot, a "what changed
  since last review" table, the load-bearing invariants, ground
  rules, and a directory map.

### Per-tool initiative_level annotations (round 2)

Burst 49 added round 2 of per-tool `required_initiative_level`
annotations: `isolate_process` / `jit_access` / `dynamic_policy` /
`delegate` / `memory_disclose` / `memory_verify` / `memory_challenge`.
Combined with Burst 46's round 1 (`shell_exec` / `browser_action` /
`mcp_call` / `code_edit` / `web_fetch`) and v0.2.0's `pytest_run` +
`pip_install_isolated`, **14 of 51 tools** now carry initiative
annotations. The remaining 37 are read_only or memory-write with
no operator-relevant initiative gate.

### Catches and fixes during the run

- **Burst 58 commit-message backtick gotcha.** Bash command-
  substitution silently ate inline code spans in
  `commit-burst58.command` (commit `cfe4219`). Substance landed
  fine; cosmetic only. Memory entry written
  (`feedback_commit_script_backticks.md`) so future sessions avoid
  it. All subsequent commit scripts use single quotes for inline
  code.
- **Burst 61 `-llll` flag bug.** Initial bandit severity-flag
  construction double-counted the 'l' character. Caught by my own
  test before push; fixed by emitting the count-of-l directly with
  a single dash prefix.
- **Burst 62 mock-capture wrong subprocess.** Initial fake_run
  captured the LAST subprocess call (pip --version detection), not
  the install. Caught by my own test; fixed by capturing all calls
  into a list and asserting on `all_calls[0]`.

## [0.1.2] — 2026-05-01 (SarahR1 absorption release)

Three Proposed ADRs from external reviewer SarahR1 (Irisviel) — the
2026-04-30 comparative review of FSF vs. her Nexus / Irkalla project
— shipped as a coherent absorption arc. **Test suite: 1434 → 1567
passing (+133, +9%).** Zero regressions across the arc. All three
ADRs promoted from Proposed to Accepted on landing.

External catalyst: [SarahR1 (Irisviel)](https://github.com/SarahR1) —
see `CREDITS.md` for the full attribution + adopted/declined ledger.

### ADR-0027-amendment — epistemic memory metadata

Closes ADR-0038 H-6 ("memory overreach / inferred-preference
cementing") at the data layer.

- **T1 + T2 — schema v10 → v11 + MemoryEntry write/read paths**
  (commit `fcd8d2c`). Three new columns on `memory_entries`:
  `claim_type` (six-class enum: observation / user_statement /
  agent_inference / preference / promise / external_fact),
  `confidence` (three-state: low / medium / high),
  `last_challenged_at` (nullable). New `memory_contradictions`
  table with FK back to entries + CHECK enum on
  `contradiction_kind`. Forward-only additive migration; default
  values land for pre-migration rows. New typed errors
  `UnknownClaimTypeError` / `UnknownConfidenceError`.
- **T3 — `memory_recall.v1` epistemic enrichments** (commit
  `24ec62b`). Recall always surfaces the new fields. Optional
  `surface_contradictions` parameter attaches unresolved
  contradictions per entry. Optional `staleness_threshold_days`
  parameter flags `is_stale`. K1 verification fold: verified
  entries surface as `confidence=high` regardless of stored
  value.
- **T4 — `memory_challenge.v1` tool** (commit `fdef95b`). Operator-
  driven scrutiny stamp on a memory entry. Distinct from
  contradictions; surfaces through staleness flag. Operator-only
  by convention via `challenger_id` arg + constitutional kit
  gating.
- **T7 (operator-driven `memory_reclassify.v1`)** deferred to v0.3.

### ADR-0021-amendment — initiative ladder

Adds the L0–L5 initiative ladder, orthogonal to the existing
side-effects ceiling.

- **T1 — genres.yaml fields + loader** (commit `03b3d60`). All 13
  genres pinned to per-§3 mapping: Companion L2 max / L1 default,
  Actuator L5/L5, Observer L3/L3, Investigator L4/L3, etc.
- **T2 — constitution derived fields** (commit `823e69c`). Two new
  fields on `Constitution`: `initiative_level` + `initiative_ceiling`.
  Both hashed (changes the constitution-hash for new births).
  YAML emission conditional: defaults (L5/L5) keep pre-amendment
  artifacts byte-identical for back-compat.
- **T3 — `InitiativeFloorStep` dispatcher** (commit `4e9b8cf`).
  Pipeline step in R3 governance, between `GenreFloorStep` and
  `CallCounterStep`. Opt-in per tool: tools that declare
  `required_initiative_level` get gated; others pass through. New
  `_load_initiative_level` defensive helper. Per-tool annotation
  audit deferred (v0.3 candidate to convert opt-in to enforcement
  on web_fetch / web_actuator / shell_exec / etc.).

### ADR-0038 — Companion harm model

Eight-harm taxonomy (H-1 sycophancy through H-8 self-improvement
narrative inflation) with per-harm structural mitigations.

- **T1 — `min_trait_floors` mechanic** (commit `03b3d60`).
  Companion declares `evidence_demand >= 50, transparency >= 60`
  (H-1 sycophancy mitigation). Birth refuses below-floor profiles.
- **T2 — voice safety filter** (commit `fb75c6f`). New
  `voice_safety_filter.py` module with conservative denylist of
  nine sentience-claim pattern categories. Wired into voice
  renderer post-processing: hits trigger template fallback.
  Hard refuse, not soft warn.
- **T3 — Companion §honesty constitutional template** (commit
  `ddf0326`). Three new policies on `operator_companion`:
  `forbid_sentience_claims` (H-2), `forbid_self_modification_claims`
  (H-8), `external_support_redirect` (H-3, require_human_approval).
  Two new out_of_scope entries (H-4):
  `claim_romantic_relationship`,
  `assume_intimacy_beyond_configured_role`. New operator-duty
  (H-7 burnout awareness).
- **T4–T6 (telemetry + per-call gates) + T7 (dashboard)** deferred
  to v0.3 — operator dashboard work + per-call gate plumbing, not
  blocking the structural floor.

### Other

- **`CREDITS.md`** introduced (commit `889e362`). First entry:
  SarahR1 (Irisviel). Documents both adopted and declined-with-
  reasoning items. Future contributor work goes here with the
  same discipline.
- **`docs/audits/2026-05-01-sarahr1-review-response.md`** — saved
  response in audit trail. Disk-citation corrections of stale
  claims, three adoption announcements, three pushbacks, three
  questions back at her.

### Files NOT changed

Audit chain format, DNA derivation, constitution-hash semantics
(field additions, not invariant changes), schema rebuild path,
single-writer SQLite discipline, `memory_verify.v1` (K1) API,
genre kit-tier ceiling enforcement, tool-of-the-turn dispatcher
public API, the seven `genres.yaml` original genres' identities.

See `docs/decisions/ADR-0027-amendment-epistemic-metadata.md`,
`docs/decisions/ADR-0021-amendment-initiative-ladder.md`,
`docs/decisions/ADR-0038-companion-harm-model.md` for the
per-amendment rationale + open questions + tranche ledgers.

## [0.1.1] — 2026-04-30 (audit + hardening release)

The post-v0.1.0 hardening + cleanup pass. **Test suite: 992 → 1439
passing (+447, +45%); 122 broken → 0; 1 documented xfail.** No
behavior shift beyond two §0-gated bug fixes (one-line each); the
rest is coverage closure, decomposition, documentation, and
verified-not-removed cleanup.

### Phase A — critical hardening (96 + 26 broken cases → 0)

- **`fix: brew formula off-by-one (patch_check.v1)`** — One-line
  production bug fix. Was producing `brew:formula` because of a
  naive `[:-1]` slice on `formulae`; now preserves the kind verbatim
  as `brew:formulae`.
- **`fix: memory recall ordering tiebreaker (core/memory.py)`** —
  Two-line production bug fix. `ORDER BY created_at DESC` wasn't
  airtight under sub-microsecond writes; tiebreaker on `rowid DESC`
  makes the documented "newest first" guarantee deterministic.
- **`test: shared seed_stub_agent fixture`** — Phase A audit traced
  43 FK-constraint failures to a single missing-seed pattern; the
  shared helper is the durable fix.
- **`test: Tool Forge static analysis fixture indent`** — fixed a
  textwrap.dedent + body-interpolation bug that produced unparseable
  Python in 15 fixtures.
- **`test: stale assertion updates`** — role count 5→17, genre count
  10→13, schema_version 6→10, security_mid `network`→`external`.
- **`test: priv_client edge cases`** — fixed two test bugs.
- **`test: xfail v6→v7 migration with documented reason`** — SQLite
  ≥3.35 ALTER TABLE semantics make the test setup brittle even
  though production migration works.

### C-1 zombie tool dissection

Six catalog entries had no on-disk implementation. Per-tool §0
verdict in `docs/audits/2026-04-30-c1-zombie-tool-dissection.md`:

- **IMPLEMENT `dns_lookup.v1`** — new ~190 LoC tool using stdlib
  socket. 20 unit tests.
- **SUBSTITUTE 4 tools** with existing equivalents: `log_grep` →
  `log_scan`, `flow_summary` → `traffic_flow_local`,
  `baseline_compare` → `behavioral_baseline + anomaly_score`,
  `correlation_window` → `log_correlate`.
- **DEFER `packet_query.v1`** to Phase G `tshark_pcap_query`.

Catalog count: 46 → 41.

### Phase B — coverage closure (+323 unit-test cases)

Eleven files that had zero unit-test coverage now have it:

- `tools/governance_pipeline.py` (R3) — 37 cases
- `daemon/routers/conversation_resolver.py` (Y3.5) — 23 cases
- `daemon/routers/conversations_admin.py` (Y7) — 19 cases
- `core/hardware.py` (K6) — 30 cases
- `chronicle/render.py` — 59 cases (covers all per-event sanitizers
  + secret-leak prevention)
- `soul/voice_renderer.py` — 29 cases (all 4 provider-error paths)
- `cli/main.py` — 22 argparse-dispatch smoke cases
- `cli/triune.py` + `cli/chronicle.py` — 15 cases
- `daemon/providers/local.py` + `frontier.py` — 28 cases
- 8 untested tools (`code_read`, `code_edit`, `shell_exec`,
  `llm_think`, `mcp_call`, `browser_action`, `suggest_agent`,
  `memory_verify`) — 61 batched smoke cases
- `dns_lookup.v1` (newly implemented) — 20 cases

Frontend Vitest scaffold (B.4) deferred to v0.3.

### Phase C — decomposition

Two god-objects had pure helpers extracted:

- `conversations.py` 994 → 852 LoC. Helpers in
  `daemon/routers/conversation_helpers.py` (226 LoC, 30 unit tests).
- `writes.py` 1186 → 1096 LoC. Helpers in
  `daemon/routers/birth_pipeline.py` (194 LoC, 33 unit tests).

Net 232 LoC out of god-objects + 420 LoC of newly-testable helpers.

### Phase D — documentation

- **CLAUDE.md** at repo root with harness conventions
- **8 ADR status promotions** Proposed → Accepted (0019, 0021, 0022,
  0027, 0030, 0031, 0034, 003Y) with audit reference
- **4 ADR placeholders explicitly Deferred to v0.3+** with rationale
  (0025, 0026, 0028, 0029)
- **7 new operator runbooks**: `conversation-runtime.md`,
  `sw-track-triune.md`, `demo-scenarios.md`, `forge-tool-skill.md`,
  `plugin-package-format.md`, `memory-subsystem.md`, `triune-bond.md`
- **`command-scripts-index.md`** covering all 37 `.command` scripts
- **examples/README.md** orientation
- **5 audit / planning docs**: comprehensive repo audit, audit plan,
  C-1 dissection, Phase E verdicts, v0.2→v1.0 roadmap with external-
  review §7 addendum
- **Tool catalog expansion survey** with ~80 candidate tools

### Phase E — verification + cleanup (under §0 gate)

Net effect: **zero deletions of load-bearing code**. Per-candidate
verdicts in `docs/audits/2026-04-30-phase-e-cleanup-verdicts.md`:

- `agents/` + `ui/` empty packages: **KEEP-WITH-COMMENT**
- `scripts/initial_push.sh`: **KEEP-WITH-GUARD** (`exit 1` early)
- `.env` tracked: **KEEP** (verified non-sensitive)
- registry default path: **FIX-IN-PLACE** (`data/registry.sqlite`)
- `docs/PROGRESS.md`: **ARCHIVE** to `docs/_archive/`
- `scripts/verify_*.py`: **KEEP**

### Files NOT changed (preserved verbatim)

- Audit chain format
- DNA derivation
- Constitution hash semantics
- Schema (still v10)
- All daemon endpoints (URLs + payloads unchanged)
- Frontend behavior (8 tabs, same wiring)
- Single-writer SQLite discipline
- All `.command` script names

## [0.1.0] — 2026-04-30

The "agents you can actually talk to" milestone. Three new tracks shipped in
one session: SW-track (the agent foundry can do software work on itself),
ADR-003Y conversation runtime (operator + agents in multi-turn rooms with
audit-trailed governance), and R3 governance pipeline extraction (tool
dispatch refactored from a god-method into composable pre-execute steps).
Nine commits, ~8,100 LoC net, every commit live-verified.

### SW-track — Atlas/Forge/Sentinel coding triune

- **`feat: SW.A.5 — code_read.v1 / code_edit.v1 / shell_exec.v1`** (commit `5ef6747`).
  Three new built-in tools giving software_engineer agents real filesystem +
  shell capability. `code_read` is read-only with `Path.resolve()` + allowlist
  defense against `../../etc/passwd` and symlink escape; `code_edit` is
  filesystem-tier with atomic temp+rename writes; `shell_exec` is external-tier
  with argv-list-only dispatch (no `shell=True`), allowed_commands allowlist,
  cwd allowlist, mandatory subprocess timeout. All three live-verified
  end-to-end.
- **`fix: SW.A.5 live-test driver — match actual response shape`** (commit
  `82770b3`). Driver assertions now match the actual response schema
  (`failure_exception_type`, not body-grep). Also fixed the
  `a5-finalize.command` tools-registered curl path (`/tools` → `/tools/registered`).
- **`feat: SW-track — file ADR-0034 + first real triune task harness`**
  (commit `c7252e7`). Files **ADR-0034** retroactively (470 lines) capturing
  the SW-track design: three roles claiming three existing genres
  (system_architect → researcher, software_engineer → actuator,
  code_reviewer → guardian). The `live-triune-file-adr-0034.command`
  orchestration script is Phase B.2 — the meta-demo where the triune
  participates in filing its own ADR. Live-verified: 21 audit events
  documenting 3 births + 1 ceremony + 5 code_reads + 3 llm_thinks + 3 archives.

### R3 — extract governance_pipeline from dispatcher

- **`refactor: R3 — extract governance_pipeline from dispatcher.py`**
  (commit `6ea59a0`). The 2026-04-30 morning load-bearing survey identified
  `ToolDispatcher.dispatch()` as a god-method. This commit extracts the 8
  pre-execute checks into `tools/governance_pipeline.py` (NEW, 533 LoC) with
  `DispatchContext` + `StepResult` + `PipelineStep` protocol +
  `GovernancePipeline` runner + 9 step classes. `dispatcher.py` shrinks
  1368 → 1279 LoC; `dispatch()` becomes a clear orchestrator over named
  steps. **ADR-003Y Y3's per-conversation rate-limit is now a 1-line
  append** in `__post_init__` — the structural payoff. 84 tests pass across
  tool_dispatcher + skill_runtime + delegate_tool + b1_tools. Public API of
  `dispatch()` unchanged.

### ADR-003Y — Conversation runtime (Y1-Y7 all phases)

- **`feat: Y1 — schema v10 + conversations CRUD router`** (commit `af07582`).
  Schema v10 migration (additive only): `conversations`,
  `conversation_participants`, `conversation_turns` tables.
  `ConversationsTable` accessor + `daemon/routers/conversations.py` (NEW)
  with full CRUD endpoints. 8 new audit event types in `KNOWN_EVENT_TYPES`.
  `body_hash` (SHA-256) persists for tamper-evidence even after Y7 purges
  body content.
- **`feat: Y2 — single-agent conversation orchestration`** (commit `3d83afb`).
  `auto_respond=True` flag on `POST /turns` triggers the single-agent
  orchestration: dispatch `llm_think.v1` with prior conversation history as
  context, append the agent's response as a follow-up turn, return both
  turns. Reuses the R3 governance pipeline unchanged.
- **`feat: Y3 — multi-agent rooms with @mention chain`** (commit `7803084`).
  Lifts Y2's "exactly 1 agent" cap. Resolution order: `addressed_to` →
  `@AgentName` mentions → fallback to first agent. After each agent
  response, `@mentions` parsed for next responder, capped at
  `max_chain_depth` (default 4). Self-mentions filtered. New
  `daemon/routers/conversation_resolver.py` extracts the resolution logic
  as pure functions for testability.
- **`feat: Y4+Y7+Y5 — bridge / retention sweep / ambient mode`** (commit
  `ff5ef4d`). Three Y phases stacked. **Y4** `POST
  /conversations/{id}/bridge` with operator attribution + same-domain
  refusal. **Y7** `POST /admin/conversations/sweep_retention` walks turns
  past their retention window, dispatches `llm_think.v1` for tight
  summaries, atomically replaces body with summary via
  `summarize_and_purge_body`. Operator-triggered with `dry_run` support.
  **Y5** `POST /conversations/{id}/ambient/nudge` for proactive agent
  turns. Two structural gates: constitution `interaction_modes.ambient_opt_in`
  (default false) AND `FSF_AMBIENT_RATE` quota
  (minimal=1/normal=3/heavy=10 per agent per conversation per day). Emits
  `ambient_nudge` audit event BEFORE dispatch.
- **`feat: Y6 — frontend Chat tab`** (commit `6ae0eb1`). Vanilla JS Chat
  tab wired to all the Y1-Y3 endpoints. New `frontend/js/chat.js` (~330
  LoC) with rooms list grouped by domain, participant chips, multi-line
  composer with `max_chain_depth`/`max_response_tokens` controls,
  @mention highlighting, archive button. localStorage stashes the active
  conversation_id so a refresh resumes the operator in the same room.

### Numbers (since 2026-04-28 Phase E close)

| | 2026-04-28 | 2026-04-30 |
|---|---:|---:|
| Python LoC | ~44,000 | ~36,400 (after R-track refactors split god-objects) |
| ADRs | 26 | 29 (ADR-0034 SW-track filed + 003X/003Y drafts) |
| Builtin tools | 36 | 40 (+ code_read/edit/shell_exec/llm_think) |
| Skill manifests | 24 | 26 |
| Frontend JS modules | 18 | 22 (+ chat.js + cleanup) |
| Audit event types | 36+ | 54 (+ Y-track + ambient + summarized + chain_depth) |
| Trait roles | 14 | 17 (+ system_architect, software_engineer, code_reviewer) |
| Genres | 10 | 13 (+ web_observer/researcher/actuator from ADR-003X) |
| `.command` scripts | 19 | 36 |
| Schema version | v9 | v10 (added conversations + participants + turns tables) |

### What v0.1.0 makes demonstrable end-to-end

1. Drag trait sliders → birth a content-addressed agent (deterministic DNA, hash-pinned constitution, LLM-rendered Voice section)
2. Birth Atlas + Forge + Sentinel → bond into a triune via `ceremony.v1` with `restrict_delegations: true`
3. Open Chat tab → create room → add agents as participants
4. Type a turn with `@AgentName` → that agent responds via governed `llm_think`
5. Agent's response `@mentions` another participant → chain extends, capped at `max_chain_depth`
6. Cross-domain bridge invites an outside-domain agent with operator attribution
7. Ambient nudge surfaces a proactive agent turn (opt-in + rate-gated)
8. Retention sweep summarizes purged turn bodies (`body_hash` preserves tamper-evidence)
9. Audit tab shows EVERY action — dispatched, succeeded, refused, bridged, summarized — hash-chained
10. The "social layer through agentic cooperation" thesis runs in a single browser window

### Phase 4 — file ADR-0023 (Benchmark Suite) as Proposed — 2026-04-25

- **`docs/decisions/ADR-0023-benchmark-suite.md`**, status Proposed. Per-genre benchmark battery + per-stat performance budget + cross-backend comparability. Closes the measurement gap: trait tuning becomes empirical, drift becomes detectable, the LM Studio swap path matures into a tuning tool. Completes the four-ADR vision quartet (0020 character sheet + 0021 genres + 0022 memory + 0023 benchmarks).
- **Per-genre batteries** sketched for all seven genres (~25 fixtures total): Observer (signal_detection / false_positive_rate / time_to_detection / tool_invocation_focus), Investigator (correlation_recall / hypothesis_quality / alternative_consideration), Communicator (conciseness / accuracy_preservation / audience_calibration), Actuator (pre_action_verification / false_execution / escalation_appropriateness), Guardian (refusal_accuracy / false_refusal_rate / policy_alignment), Researcher (source_diversity / citation_accuracy / synthesis_quality), Companion (empathy_alignment / boundary_keeping / retention_fidelity — the last one depends on ADR-0022 memory).
- **Three scoring classes:** numerical (deterministic — detection_rate, latency_ms, etc.), rubric (LLM-as-judge with structured criteria, runs locally per ADR-0008), composite (weighted combination). Bias toward numerical to keep model-time cost bounded; rubric is opt-in per fixture.
- **Versioned fixtures.** `signal_detection.v1` is frozen once committed; v2 is a parallel entry. Same audit-trail discipline as the tool catalog (ADR-0018). Agent results referencing v1 stay reasonable-about even after v2 lands.
- **Run lifecycle.** `POST /agents/{id}/benchmark` queues + runs sequentially in v1. Per-run files in `data/benchmark_runs/{run_id}/`. Audit chain captures `benchmark_run_started / benchmark_fixture_complete / benchmark_run_complete / benchmark_run_aborted` events with score + metadata, NEVER the model output (privacy + size). Full transcripts live in run files (rebuildable via re-run).
- **Cross-backend comparable.** Each run records `model_backend` exactly (e.g., `local:llama3.2:1b`, `local:llama3.1:8b`, `frontier:gpt-4o-mini`). Side-by-side comparison: same fixtures + same agent + different backends → quality delta. The LM Studio swap path documented in `dev-tools.md` becomes a benchmarking tool.
- **Per-genre performance budget** in `genres.yaml` (post-ADR-0021) — `battery_pass_threshold`, `flagged_below`, `avg_latency_ms_max`, `max_tokens_per_session`. Character sheet (ADR-0020) `benchmarks` section pulls current vs. budget for green/amber/red.
- **Tool catalog gains `benchmark_run.v1`** with `side_effects: external` (durable run records + audit events + model invocations). Constraint policy (ADR-0018 T2.5) gates appropriately. Used by Guardian-class agents to assess other agents periodically and flag drift.
- **Open questions captured (5):** rubric judge — self vs Guardian-class (lean Guardian once mature, self as MVP fallback with "self-judged" flag); baseline authoring (run templated agent at fixture authoring time); Guardian benchmark runs writing to memory (yes, consolidated only); reproducibility under stochastic models (record seed + temperature; flag non-pinned runs); multi-tenant fixture libraries (defer until second operator).
- **Out of scope (deferred):** continuous benchmarking (run on every commit), A/B testing across populations, external benchmark integration (HumanEval, BFCL, MMLU — those measure model capability not Forest-agent capability), cost ceiling enforcement, public result publication.
- **Implementation tranches T1–T10** captured. T1+T2+T3+T5 is "agents can be measured." T4 unblocks rubric-scored fixtures. T6+T7+T8 wire ongoing measurement into the rest of the system. T9 cross-backend comparison view. T10 real-data fixture authoring.
- **Vision quartet complete.** ADR-0020 (what an agent IS) + ADR-0021 (what kind it is) + ADR-0022 (what it remembers) + ADR-0023 (how good it is) — four ADRs, 8 follow-on tranches each, all Proposed-status with implementation deferred. The Forge has its full design horizon captured on disk; subsequent work is execution against a known map.

### Phase 4 — file ADR-0022 (Memory Subsystem) as Proposed — 2026-04-25

- **`docs/decisions/ADR-0022-memory-subsystem.md`**, status Proposed. Three-layer memory model per agent (working / episodic / consolidated) with explicit retention windows, privacy defaults per genre, and an audit-chain integration that records *acts* without recording *contents*. Companion genre carries a strict tier — local-only-provider for memory ops, no cross-agent reads, all overrides explicit and audit-logged. Per ADR-0008's privacy floor amplified into a memory-shape contract.
- **Three-layer model:** `working` (current session, token-bounded, RAM-backed with on-session-end checkpoint), `episodic` (append-only event log, configurable retention window per genre, JSONL on disk + SQLite index), `consolidated` (distilled summary updated by periodic consolidation jobs, Markdown + JSONL pair, kept indefinitely until explicit operator action).
- **On-disk layout** mirrors the soul/constitution pattern — `data/agent_memory/{instance_id}/` per agent. `memory_index.sqlite` is derived per ADR-0006, rebuildable from JSONL files.
- **Privacy contract by genre table:** Observer (yes frontier, siblings only, 30 days) → Companion (NO frontier, NO cross-agent, indefinite consolidated). Companion's local-only-provider floor is enforced via a tool_constraint_policy always-rule per ADR-0018 T2.5 — structural, not procedural.
- **Three forgetting paths**, all explicit: time-based expiration (consolidation absorbs or drops), operator-initiated (`POST /agents/{id}/memory/forget` with audit chain `memory_forgotten` event recording the act but not the content), and (Companion-class, post-MVP) user-initiated via the interactive surface. GDPR-friendly erasure built into the architecture.
- **Audit chain integration is intentionally privacy-preserving.** Memory writes produce `memory_appended` / `memory_promoted` / `memory_consolidated` / `memory_forgotten` events carrying entry hashes + types + tags, **never the content itself**. Auditors get verifiable presence ("17 entries between T1 and T2, 4 of type X") without intrusion.
- **Constitution.yaml gains `memory_budget:`** block per-agent — derived from genre defaults at birth, overridable via constitution_override. memory_budget IS in constitution_hash, so two agents with the same trait profile but different memory budgets have different rulebook hashes (correct — their effective state shape differs).
- **Tool catalog gains `memory_recall.v1`** (read-only, agent reads its own memory). Symmetric write tools deliberately NOT in v1 — memory writes are operator-driven via daemon endpoints; auto-write tools land when ADR-0019 (runtime) defines how an agent decides to retain something.
- **Read/Write API:** GET /agents/{id}/memory/{working,episodic,consolidated,budget}, POST /agents/{id}/memory/{episodic,promote,consolidate,forget}, plus working memory's session-scoped affordances (append/read/clear). All write endpoints auth-gated.
- **Open questions captured (5):** working memory spill-to-disk frequency (lean session-end), consolidation prompt source (lean agent-itself for MVP, Guardian-class for follow-on), tombstoned recall when entry past retention but hash in audit chain (yes — be transparent), token vs entry budget for working memory (lean tokens, derive entries), schema versioning per JSONL entry (migrate on read, not write).
- **Out of scope (deferred):** vector embeddings for semantic search (defer until episodic exceeds 10k entries on a typical agent), cross-genre shared knowledge graphs (multi-writer coordination), memory diffing across re-births, structured backup/restore tooling, per-tool memory tagging.
- **Implementation tranches T1–T11** captured. T1+T2+T3+T4 is "agents can remember." T5+T6 add consolidation + forgetting. T7+T8 wire the rest of the system. T9 unblocks character sheet (ADR-0020) `memory` section. T10+T11 are polish and scale.

### Phase 4 — file ADR-0021 (Role Genres / Agent Taxonomy) as Proposed — 2026-04-25

- **`docs/decisions/ADR-0021-role-genres-agent-taxonomy.md`**, status Proposed. Hierarchical taxonomy above the flat role list. Seven genres for v1: **Observer** (network_watcher, log_analyst, signal_listener, dashboard_watcher), **Investigator** (anomaly_investigator, incident_correlator, threat_hunter), **Communicator** (notifier, briefer, status_reporter, translator), **Actuator** (ticket_creator, deploy_runner, alert_dispatcher), **Guardian** (safety_check, content_review, refusal_arbiter), **Researcher** (paper_summarizer, vendor_research, knowledge_consolidator), **Companion** (therapist, accessibility_runtime, day_companion, learning_partner — the Phase 5 path from ADR-0008).
- **Each genre carries six properties:** description, risk_profile (max side_effects tier), default_kit_pattern, trait_emphasis, memory_pattern (placeholder for ADR-0022), spawn_compatibility (which child genres this genre can spawn). Roles inherit these properties; per-role overrides are explicit and rare. Companion genre carries the local-only-provider floor as a structural constraint per ADR-0008.
- **Spawn compatibility rules.** Most genres can spawn within their own genre; some can spawn across (Observer → Investigator when an observation warrants deeper looking). Cross-genre spawns that violate compatibility return 400 unless the operator passes `--override-genre-spawn-rule`, which records a `spawn_genre_override` audit event so the violation is visible after the fact. Friction is the feature — codifies the separation of concerns between watching and acting.
- **`config/genres.yaml`** as the canonical source. Loader enforces consistency: every role known to the trait engine must be claimed by exactly one genre; every genre's default_kit_pattern must reference real catalog tags. Drift fails closed at daemon startup.
- **What changes in existing surfaces** when implementation lands (T1–T9): soul.md frontmatter gains `genre` + `genre_description` (auto-computed from role); constitution.yaml gains `genre` (in hash); `tool_catalog.yaml` gets `genre_default_tools:` (resolution fallback after archetype standard_tools); `tool_constraint_policy` gains genre-level always-rules (Companion → local-only-provider, Observer → reject non-read-only at resolve time); BirthRequest validates spawn compatibility; voice renderer (ADR-0017) consumes `trait_emphasis` from genre to weight the user prompt; frontend adds genre selector → role filter; character sheet (ADR-0020) `capabilities.genre` populates.
- **Decision lattice captured** in a who-decides-what table — trait emphasis at genre, default tool kit at role > genre, spawn compatibility at genre, risk profile floor at genre, constitution policies stay at role per ADR-0004, trait values per-agent, memory pattern at genre per future ADR-0022.
- **Out of scope (deferred):** subgenres (single-level v1; revisit if catalog grows past ~15 roles), custom operator genres (defer until second operator with second use case), genre-level constitution templates (today's role-template surface stays; genres add *constraints* via tool_constraint_policy rather than full templates), cross-genre lineage analytics (audit chain has the data; downstream tooling).
- **Implementation tranches T1–T9** captured. T1+T2+T3 is the "genre exists" milestone; T4 is the loadout improvement; T5+T6 are policy enforcement; T7 wires voice quality follow-on; T8 is UX; T9 wires character sheet.

### Phase 4 — file ADR-0020 (Agent Character Sheet) as Proposed — 2026-04-25

- **`docs/decisions/ADR-0020-agent-character-sheet.md`**, status Proposed. Designs the descriptor that consolidates an agent's identity, personality, loadout, capabilities, stats, memory, benchmarks, and provenance into a single served view. Frames the Forge as character creation in a TTRPG sense — operators play characters; the character sheet is what the operator gets back when they ask "what is this thing."
- **Decision: derived JSON view, not a new canonical artifact.** Per ADR-0006, files-on-disk are authoritative; adding `character_sheet.yaml` would duplicate information from soul.md and constitution.yaml and create a sync burden. The character sheet is composed on demand from the canonical artifacts plus measured stats stored in the audit chain and (future) registry. Three rendering layers: a JSON endpoint at `GET /agents/{instance_id}/character-sheet`, a frontend page, and a markdown export at `?format=md` for git-able snapshots / postmortems.
- **Eight sections** in the schema, each with documented source authority: `identity` (registry + soul.md), `personality` (traits + voice), `loadout` (constitution.yaml `tools:` per ADR-0018 T2.5), `capabilities` (role + future genre per ADR-0021), `stats` (future ADR-0022/0023 measurements), `memory` (future ADR-0022), `benchmarks` (future ADR-0023), `provenance` (constitution_hash + tool_catalog_version + last audit entry hash + paths to canonical artifacts).
- **Forward-compatible by design.** Sections for measurements, memory, and benchmarks exist with `null` / `not_yet_*` flags today; the data fills in as ADR-0022 and ADR-0023 implement without consumer rewrites. Adding fields is additive; removing or renaming requires a `schema_version` bump.
- **Out of scope (deliberately):** real-time / streaming character sheet, multi-agent comparison view, structured diff endpoint, PDF rendering (markdown export covers it; pandoc is a downstream step). Editing API also out — character sheets are read-only; mutation happens at the source layer (re-birth, tools_add/remove, regenerate-voice).
- **Implementation tranches captured** (T1–T6): T1 endpoint + 5 minimum-viable sections, T2 markdown export, T3 frontend view, T4–T6 wire genre / memory / benchmarks as their ADRs ship. T1+T2+T3 is the "sheet exists" milestone; T4–T6 are pure consumer adds.
- **Open questions captured in the ADR** (5 items) — last_audit_entry_hash anchoring choice, markdown layout, embedded role description, voice regeneration provenance hook, treatment of archived agents (yes — postmortems are the most useful inspection target).
- **Implementation deliberately not in this commit** — the ADR captures the design space so subsequent tranches can pick it up cleanly. The next ADR draft (ADR-0021 Role Genres) builds on the character sheet's `capabilities.genre` field.

### Phase 4 — ADR-0018 T2.5 — declarative tool constraint policy — 2026-04-25

- **`src/forest_soul_forge/core/tool_policy.py`** — small declarative engine that derives per-tool constraints from the trait profile. Rules are hardcoded as a tuple of `_Rule` dataclasses for v1 (operators today are this project's developers; the YAML-driven version lands when a second editor shows up). Four baked rules:
  1. `caution >= 80` → `requires_human_approval = true` on any tool whose `side_effects != "read_only"`.
  2. `thoroughness >= 80` → `max_calls_per_session = 50` on `network` and `external` tools.
  3. **Always:** `filesystem` tools require human approval. No trait override bypasses this.
  4. **Always:** `external` tools require human approval. Same.
  Defaults: `max_calls_per_session=1000`, `requires_human_approval=false`, `audit_every_call=true`. Each rule layers — a high-caution + high-thoroughness agent on a network tool gets BOTH the approval gate AND the call cap.
- **`ResolvedConstraints`** dataclass carries the resolved values + a tuple of every rule that matched, in declaration order. The matched-rule names ride along into both constitution.yaml and the audit chain so an auditor can answer "why is this constraint set what it is" without re-deriving — the trail is in the artifact.
- **`Constitution`** dataclass gains a `tools: tuple[dict, ...]` field, included in `canonical_body()` so `constitution_hash` covers it. Per ADR-0018's reproducibility section: two agents with identical trait profiles but different tool surfaces produce different constitution hashes — correct, since their effective policy differs. Same DNA, different rulebook.
- **`/preview` updated** to accept `tools_add` / `tools_remove` and resolve them through the same kit + policy path /birth uses. Existing `test_preview_matches_birth_hash` continues to pass — /preview-with-defaults still matches /birth-with-defaults because the resolved tool surface is identical on both sides. Predictive parity preserved as ADR-0018 §"Reproducibility" requires.
- **Audit chain**: `agent_created` and `agent_spawned` `event_data.tools` is now the full per-tool resolved structure (`{name, version, side_effects, constraints, applied_rules}`) rather than just `{name, version}`. Operator can read constraints + rule trace without opening the constitution.yaml file. `tool_catalog_version` continues to pin which catalog the agent was birthed against.
- **Tests added** (`tests/unit/test_tool_policy.py`, 15 cases):
  - **TestDefaults** (3): low-caution read-only uses defaults; default audit_every_call is true; default max_calls_per_session is generous.
  - **TestTraitRules** (5): high-caution requires approval on network; high-caution does NOT affect read-only; high-thoroughness caps network calls; high-thoroughness does NOT cap read-only; high-caution + high-thoroughness layer correctly on network.
  - **TestSafetyFloor** (3): filesystem always-rule fires even on low-caution agents; external always-rule same; external + high-thoroughness layers (approval + call cap together).
  - **TestEdgeCases** (4): missing trait in profile doesn't crash (rule reports non-match); resolved constraints dict is sorted-keys (byte-stable); rule_names() returns all four; resolve_kit_constraints preserves order.
- **Test stub for tool_policy unit tests** (`_StubProfile`) is intentionally minimal — only carries `trait_values`. Avoids dragging the full TraitEngine into pure-policy tests.
- **Pre-existing tests unchanged.** `test_preview_matches_birth_hash` continues to pass because both endpoints now resolve tool surfaces identically. Tool override tests in `TestToolKit` continue to pass because the audit `tools` field shape grew from `{name, version}` to `{name, version, side_effects, constraints, applied_rules}` — the names check still uses `name` field. 108 tests total across the focused harness, all green.
- **Forward compat baked in deliberately** — the constitution `tools[]` entry shape carries `constraints` today, with room for future fields (`memory_budget`, `benchmark_targets`, `capability_pins`) without breaking parsers. Per the "thorough and robust not tight corners" directive.
- **Future ADRs queued (Proposed status, not yet drafted):** ADR-0020 Agent Character Sheet, ADR-0021 Role Genres / Agent Taxonomy, ADR-0022 Memory Subsystem, ADR-0023 Benchmark Suite. Each captures a piece of the broader vision (genres of agentic roles, memory budgets, benchmarks, comprehensive descriptors) without committing implementation. Tasks #30–#33 track them; pick one to draft when ready.

### Phase 4 — file ADR-0018 (Agent tool catalog) as Proposed — 2026-04-25

- **`docs/decisions/ADR-0018-agent-tool-catalog.md`**, status Proposed. Captures the decision shape for giving agents concrete tool surfaces (e.g. `packet_query`, `log_grep`, `baseline_compare`) bundled by archetype and overridable per birth. Hybrid declaration model: `config/tool_catalog.yaml` is the canonical source of MCP-style tool descriptors keyed by `{name}.{version}`, soul.md frontmatter carries name+version refs (not the full schemas), constitution.yaml carries per-agent constraints derived from the trait profile (e.g. high-caution agent gets `requires_human_approval: true` on any tool whose `side_effects != "read_only"`).
- **Rationale captured in the ADR** for the hybrid choice over inline-schema and pure-name-reference alternatives. Version pinning preserves the audit trail across catalog evolution — an agent birthed under catalog v0.1 can be reasoned about even after the catalog reaches v0.5 because v1 tools aren't deleted, only superseded as defaults.
- **Reproducibility preserved.** `dna` continues to hash only the trait profile (ADR-0002). `constitution_hash` now also covers the resolved tool list + per-tool constraints, so two agents with the same profile but different `tools_add` overrides will have different constitution hashes — correct behavior, since their effective surface differs. /preview must pass the same overrides as the eventual /birth to get hash parity (already true for constitution_override).
- **Implementation deliberately not in this commit.** Tranches captured in the ADR (T1 catalog loader → T2 resolver → T3 schema+tests → T4 frontend → T5 runtime in a separate ADR). T5 is the bigger half — tool invocation, MCP transport, sandbox boundary — and gets its own design pass.
- **Out of scope** (deferred to follow-ups): execution / runtime, federation across catalogs, hot-reload, frontend catalog editor, per-session enforcement of constraint fields.

### Phase 4 — live-fire ADR-0017 against real Ollama + Dockerfile permission fix — 2026-04-25

- **`live-fire-voice.command`** (new permanent dev-experience helper). Rebuilds the daemon container with the latest source, brings up the full `--profile llm` stack including ollama, waits for daemon health, hits `/runtime/provider` to confirm provider chain, then `POST /birth` with `enrich_narrative=true`, then dumps the resulting soul.md's `narrative_*` frontmatter and the `## Voice` section. Auto-writes a minimal `.env` pointing the daemon at `llama3.2:1b` (the model that fits in default Docker container memory) when no `.env` exists. Uses `set -uo pipefail` (no `-e`) so a failing curl still surfaces diagnostic output instead of bailing the script.
- **Live-fire result captured for the record.** Birthed `network_watcher` agent "VoiceTest" → 201 Created → soul.md written with `narrative_provider: "local"`, `narrative_model: "llama3.2:1b"`, `narrative_generated_at: "2026-04-25 09:09:45Z"`. The Voice section is three coherent paragraphs in second person; the model picked up on the very-high double_checking / caution / evidence_demand / technical_accuracy traits and translated them into narrative voice ("precision that borders on meticulousness, always double-checking your work", "deliberate and calculated, weighing the pros and cons"), and placed the agent in role context ("analyzing network traffic", "dissect each packet"). Deterministic frontmatter (dna, constitution_hash, lineage, trait_values) is byte-for-byte what we'd expect from a deterministic build — adding the `## Voice` section disturbs nothing else, exactly as ADR-0017 promised.
- **`Dockerfile` permission normalization.** Caught during the live-fire run: `trait_tree.yaml` on the host had mode 600 (umask-077-style restrictive), and `COPY` preserves source permissions, so the file landed in the image as `-rw-------` and the non-root `fsf` user (uid 1000) couldn't read it. The daemon's lifespan tolerantly caught the `PermissionError` and set `app.state.trait_engine = None`, so `/healthz` and `/runtime/provider` worked but every write endpoint 503'd with the misleading "trait engine not available (check FSF_TRAIT_TREE_PATH)" message. **Fix:** `RUN chmod -R a+rX /app/src /app/config` after the COPY (and the same for `/app/examples` after its later COPY). Capital-`X` adds execute only on directories, so files become 644 and dirs stay 755. Image is now robust to whatever umask happened to be in effect when the host files were created — no more silent /birth 503s on a fresh clone whose contributor's umask was tighter than 022.
- **Diagnostic that surfaced this** (saved into `live-fire-voice.command` step 5b for posterity): docker exec into fsf-daemon, `whoami` + `id` + `ls -la /app/config` + python `open()` on the trait file with try/except. The `ls` output (`-rw------- 1 root root 16221 Apr 23 18:30 trait_tree.yaml`) and the explicit PermissionError in the python output named the issue precisely. Worth preserving — same diagnostic catches any future "the daemon comes up but writes 503" failure mode whether it's a mode bit, a missing file, or a launchd-style filesystem quirk.

### Phase 4 — implement ADR-0017 (LLM-enriched soul.md `## Voice` section) — 2026-04-25

- **`src/forest_soul_forge/soul/voice_renderer.py`** (new). `async def render_voice(provider, *, profile, role, engine, lineage, settings) -> VoiceText` — single-call API. System prompt asks for 2–4 short paragraphs in the agent's voice; user prompt embeds role description, top three domain weights, the very-high (≥80) and very-low (<20) trait names. Validates `settings.narrative_task_kind` against the `TaskKind` enum and falls back to a templated `VoiceText` (with a diagnostic note in the model field) on bad config rather than raising at birth time. Catches `ProviderUnavailable` / `ProviderDisabled` / `ProviderError` and returns a templated `VoiceText` with `provider="template"`. The system prompt and template fallback live in `soul/` because they're product decisions, not provider plumbing — keeping them out of `LocalProvider`/`FrontierProvider` lets the renderer evolve independently.
- **`SoulGenerator.generate()`** gains `voice: VoiceText | None = None` keyword arg. When supplied, emits a `## Voice` section between the intro paragraph and the first domain section, plus three optional frontmatter fields (`narrative_provider`, `narrative_model`, `narrative_generated_at`) sandwiched between `constitution_file` and the lineage block. When `voice` is None, the soul renders exactly as before — preserving backward compatibility for callers that don't enrich and for the rebuild-from-artifacts path that reads existing soul.md bodies as authoritative.
- **`BirthRequest` / `SpawnRequest`** gain `enrich_narrative: bool | None = None`. None defers to `settings.enrich_narrative_default`; explicit True/False overrides per-request. Tests that need deterministic soul.md output set `enrich_narrative=False` (or use `enrich_narrative_default=False` in their settings fixture).
- **`writes.py` /birth and /spawn**: voice renderer is invoked **outside** the write_lock, before lock acquisition. Holding a `threading.Lock` across a 1–4 second network call would serialize unrelated births for no benefit — the renderer's only side effect is the returned `VoiceText`, not registry state. The handlers are sync (FastAPI threadpool dispatch); we bridge async-to-sync via `asyncio.run()`, which creates a new event loop in the worker thread and tears it down — no conflict with FastAPI's main loop. The resulting `VoiceText` is passed into `SoulGenerator.generate(voice=...)` and into the audit `event_data` via `_voice_event_fields()` (returns an empty dict when voice is None so the spread is unconditional).
- **New `DaemonSettings` fields** (all sensible defaults; no `.env` change required for a fresh clone):
  - `enrich_narrative_default: bool = True` — global default for `BirthRequest.enrich_narrative` when the field is omitted.
  - `narrative_task_kind: str = "generate"` — string-validated against `TaskKind` at render time. Operators routing narrative voice through their conversation-tuned model set this to `"conversation"`; the resolved model behind each task_kind is independently configurable via `FSF_LOCAL_MODEL_<KIND>` / `FSF_FRONTIER_MODEL_<KIND>` already.
  - `narrative_max_tokens: int = 400, ge=1, le=8192`.
  - `narrative_temperature: float | None = None, ge=0.0, le=2.0` — when set, passed through; unset → provider default.
- **Audit chain**: `agent_created` and `agent_spawned` events gain three optional `event_data` keys (`narrative_provider`, `narrative_model`, `narrative_generated_at`). Old events without them parse cleanly via the existing tolerant ingest. **No registry schema bump** — additive optional fields are forward-compatible.
- **Test stub fix:** `_StubProvider` in `tests/unit/test_daemon_writes.py` gained a `models` property mirroring `_models` (parity with `LocalProvider` / `FrontierProvider` so `_resolve_model_tag` resolves to `"stub:latest"` instead of `"unknown"` in tests). Existing `write_env` fixture sets `enrich_narrative_default=False` so all pre-ADR-0017 tests stay byte-deterministic; new tests opt in explicitly via the request body or via the dedicated `enrich_env` fixture.
- **Tests added** in `tests/unit/test_daemon_writes.py`:
  - `TestEnrichNarrative` (6 cases): enrich=True inserts `## Voice` and frontmatter `narrative_*`; enrich=False inserts neither; settings-default path via `enrich_env`; provider raising `ProviderUnavailable` produces a templated `VoiceText` with `narrative_provider="template"` and the italic provenance line; audit `event_data` carries the three narrative_* fields when enriched; spawn path mirrors birth (parent unenriched, child enriched).
  - `TestVoiceRendererUnit` (3 cases): direct `render_voice()` call with stub returns provider="local" + model="stub:latest" + non-empty markdown; provider raising `ProviderUnavailable` returns templated `VoiceText`; bad `narrative_task_kind` returns templated `VoiceText` with the misconfig recorded in the model field rather than raising.
- **What this commit does NOT do** (per ADR-0017's "out of scope" section): no regenerate-narrative endpoint, no multi-pass generation, no streaming, no prompt-version field, no soul.md schema bump. Those land if and when there's a concrete need.

### Phase 4 — file ADR-0017 (LLM-enriched soul.md narrative) as Proposed — 2026-04-25

- **`docs/decisions/ADR-0017-llm-enriched-soul-narrative.md`**, status Proposed. Lays out the design for adding LLM-generated `## Voice` content to soul.md without disturbing any reproducibility invariant: empirically verified that constitution_hash hashes the sibling constitution.yaml (not soul.md), dna hashes trait_values directly, and the registry's ingest parser explicitly reads only the frontmatter (its own docstring: *"We only need the frontmatter."*). The body is text-only, never read by code. Adding non-deterministic content to it breaks no contracts.
- **Resolved questions captured in the ADR**: renderer lives in a new `soul/voice_renderer.py` (product concern, not provider plumbing); `task_kind` defaults to `GENERATE` but is operator-overridable via `FSF_NARRATIVE_TASK_KIND` so users routing narrative voice through their conversation-tuned model don't have to fight the design; in-process LRU cache for twin births deferred (rare, low-cost); `max_tokens` defaults 400, overridable via `FSF_NARRATIVE_MAX_TOKENS` for tuning; `enrich_narrative` opt-out wired through `BirthRequest` and `SpawnRequest` plus a global default `FSF_ENRICH_NARRATIVE_DEFAULT=true` so deterministic test mode is one settings flip.
- **Failure modes** are gentle by design: any `ProviderError` / `ProviderUnavailable` / `ProviderDisabled` from the renderer is caught at the soul-generator layer and produces a templated fallback Voice block with `narrative_provider: "template"` recorded in frontmatter. `/birth` never fails because Ollama is down.
- **Backward compatibility** explicitly preserved: three new optional frontmatter fields (`narrative_provider`, `narrative_model`, `narrative_generated_at`) round-trip through the existing tolerant ingest unchanged; old soul files without them still parse. `agent_birthed` / `agent_spawned` event payloads gain the same three optional fields without bumping the registry schema. Re-rendering a soul.md from artifacts during a registry rebuild reads the existing body as authoritative — does not re-call the LLM, so a `git checkout` of an old soul.md gives byte-for-byte that day's Voice content.
- **Implementation does not start in this commit.** ADR is filed as Proposed; the next commit is the implementation tranche (voice_renderer + SoulGenerator changes + writes.py wiring + schemas + tests + CHANGELOG entry for the actual feature).
- **Not in this ADR's scope** (deferred to follow-ups): regenerate-narrative endpoint, multi-pass generation, streaming the body back to the client, prompt-version field, soul.md schema_version bump (additive optionals don't require it).

### Phase 4 — first feature using the LLM stack: `POST /runtime/provider/generate` — 2026-04-25

- **New endpoint `POST /runtime/provider/generate`** in `daemon/routers/runtime.py`. Body: `{prompt, system?, task_kind?, max_tokens?, temperature?}`. Response: `{response, provider, model, task_kind}`. Auth-protected via `require_api_token` (when `FSF_API_TOKEN` is set, the header is required — same pattern as the other write-ish endpoints). Routes through `providers.active().complete(...)` so it works against `local` (Ollama) or `frontier` (configured but disabled by default) without code changes — flipping the active provider via `PUT /runtime/provider` redirects this endpoint's traffic on the next call.
- **Why it exists** (rationale per the Phase 3 verification follow-up note): the LLM provider layer was scaffolded since Phase 1 but had no caller — `/preview`, `/birth`, and `/spawn` are deterministic (trait engine + hash + template). This endpoint is the first feature that invokes `provider.complete()`, giving the rest of the stack (auth, healthcheck, provider switching, task-kind routing) a real consumer. It's also the natural HTTP surface for the future Telegram/Slack bot — those external integrations will message this endpoint instead of calling the Anthropic/Ollama SDKs directly, so all completion traffic flows through the daemon's policy stack.
- **Schemas** in `daemon/schemas.py`: `GenerateRequest` validates `prompt: min_length=1`, `task_kind: TaskKind` (defaults to `CONVERSATION`), `max_tokens: 1–8192`, `temperature: 0.0–2.0`. Bounds are conservative caps to fail loudly at the daemon edge rather than push wildly large requests upstream and wear an unexpected token bill on a frontier provider. `GenerateResponse` echoes the resolved provider name + model tag + task_kind so clients can debug routing without round-tripping through `/runtime/provider`.
- **Error mapping is explicit**, not a 500-everywhere blanket. `ProviderUnavailable` (Ollama not running) → **503** with detail `provider unavailable: …`. `ProviderDisabled` (`FSF_FRONTIER_ENABLED=false`) → **503** with detail `provider disabled: …`. `ProviderError` (upstream non-2xx — e.g. Ollama returns 500 for "model requires more memory than available", or a frontier provider returns 429) → **502** Bad Gateway. **500 stays reserved for actual daemon bugs.** This lets clients distinguish "I need to start Ollama" from "the model is too big for the running container's RAM" from "your daemon crashed", which matters when a Telegram bot is the user's only feedback channel.
- **Model tag resolution** via `_resolve_model_tag(provider, task_kind)` in `runtime.py`. Reads the provider's `.models` attribute when present (both `LocalProvider` and `FrontierProvider` expose it as a `dict[TaskKind, str]`). Falls back to `"unknown"` if a future provider opts out — the Protocol doesn't mandate `.models`. Looks up by both `TaskKind` enum and `task_kind.value` string so a provider that keys its dict by string still resolves cleanly. Documented as non-authoritative: for a definitive answer, query `/runtime/provider` which round-trips through the live healthcheck.
- **Test stub fix:** `_StubProvider` in `tests/unit/test_daemon_readonly.py` now exposes a `models` property mirroring `_models`. Required so `_resolve_model_tag` can find a model tag during tests; without it, the `model` field in `GenerateResponse` would always be `"unknown"` against the stub. `test_daemon_writes.py`'s independent stub left as-is — it doesn't exercise the new endpoint.
- **Tests added** in `tests/unit/test_daemon_readonly.py::TestGenerate` (7 cases): happy-path → `[stub] ping` echoed back with `provider="local"`, `model="stub:latest"`, `task_kind="conversation"`; option pass-through (system/task_kind/max_tokens/temperature all reach `provider.complete()` kwargs as expected — verified via monkeypatched capturing replacement); empty prompt → 422; `ProviderUnavailable` → 503; `ProviderDisabled` → 503 (different detail wording); generic `ProviderError` → 502; auth required when `FSF_API_TOKEN` configured (no header → 401, wrong header → 401, correct header → 200).
- **No ADR.** Small additive endpoint, fits within the ADR-0008 provider architecture; the rationale is captured here. A real ADR will be needed when we wire generation into `/birth`/`/spawn` since that breaks the bit-for-bit reproducibility of soul.md artifacts that ADR-0004 currently assumes.

### Phase 3 — Ollama LLM roundtrip verified end-to-end — 2026-04-25

- **`ollama-up.command`** — verification harness. `docker compose --profile llm up -d` (idempotent: brings up daemon + frontend + ollama, leaves already-running services alone), waits for ollama on `localhost:11434`, pulls a model, then exercises the chain in three layers: (1) `docker exec fsf-daemon curl http://ollama:11434/api/tags` proves container-to-container reachability over the compose network, (2) `curl http://127.0.0.1:7423/runtime/provider` proves the daemon's healthcheck path returns `status=ok` with the loaded models present and `missing=[]`, (3) an inline `python -c` invocation inside `fsf-daemon` instantiates `LocalProvider` and calls `.complete()` against the live Ollama backend — exercises the real httpx client + URL building + response-shape validation in the daemon's actual code path. Result: provider returns the asked phrase ("FSF wire check OK") verbatim. Wire fully verified.
- **`kill-ollama.command`** — companion helper. Many Mac dev environments have Ollama installed via Homebrew, which registers a launchd-managed service (`homebrew.mxcl.ollama`) that respawns `ollama serve` on port 11434 within seconds of any `kill`. That conflicts with the docker compose `ollama` service, which also wants 11434. The script: lists current listeners on 11434, dumps all ollama-related host processes for diagnostic clarity, runs `brew services stop ollama` (the proper way to stop a Homebrew-managed launchd service), boots out matching launchd plists in user + system scope, and finally kills any lingering PID. Verifies `port 11434 is FREE` before returning. Belt-and-braces — tries multiple plist locations (Mac App Store, DMG, brew) so it works across install methods.
- **Verification model: `llama3.2:1b`.** The harness pulls a 1.3 GB model rather than the daemon's configured default `llama3.1:8b`. Reason: Docker Desktop's default Linux VM allocates ~2 GiB to containers; `llama3.1:8b` requires 4.8 GiB at runtime and Ollama returns a 500 with "model requires more system memory than is available". The 1B model fits comfortably. **`llama3.1:8b` is still pulled and indexed by Ollama** (`docker exec fsf-ollama ollama list` shows both); only the runtime load fails until Docker memory is bumped. To use 8B+ models for actual Forest work, raise Docker Desktop → Settings → Resources → Memory to 8+ GiB and Apply & Restart; no daemon config change needed (the model tags in `local_model_*` env vars are loaded lazily on first request).
- **Findings worth noting for future Phase 4 work:** no daemon endpoint currently invokes `provider.complete()` — `/preview`, `/birth`, and `/spawn` are all deterministic (trait engine + hash + template). The provider layer is fully scaffolded (registry, healthcheck, switching, both `local` and `frontier` impls) but unused by features today. Wiring generation into `/birth`/`/spawn` (LLM-enriched soul.md prose) or adding a minimal `/runtime/provider/generate` endpoint is the natural next step.

### Phase 3 — docker compose stack (daemon + frontend + optional ollama) — 2026-04-24

- **Production `Dockerfile`** (separate from `Dockerfile.test`). `python:3.12-slim` base, non-root `fsf` user (uid 1000 to match common host UIDs on bind-mounted `./data`), installs only `[daemon]` extras (no pytest/mypy in the runtime image), `PYTHONUNBUFFERED=1` so uvicorn's stdout reaches `docker logs` in real time. `EXPOSE 7423`; `HEALTHCHECK` hits `/healthz` via `curl` so `docker ps` liveness is authoritative and compose's `depends_on: condition: service_healthy` is honest. The daemon binds `0.0.0.0` **inside** the container — host exposure is controlled by compose's port mapping, which pins host-side to `127.0.0.1` for every service. Local-first posture preserved.
- **`scripts/docker-entrypoint.sh`.** Idempotent first-boot prep: `mkdir -p /app/data/{artifacts,soul_generated}`, then — **only if `/app/data/artifacts` is empty** — seeds from the baked `/app/examples` tree with `cp -a` so a fresh `docker compose up` has something to index without clobbering user data on subsequent boots. Seeding runs at container start, not build time, because the bind-mount doesn't exist until runtime. Wired via `ENTRYPOINT` in the Dockerfile; the original `CMD` (uvicorn) is preserved as the exec target.
- **`frontend/Dockerfile` + `frontend/nginx.conf`.** `nginx:1.27-alpine`, ~25MB, explicit MIME mapping for `.js`/`.mjs` to guarantee ES modules load with `application/javascript` (some browsers refuse `text/plain`-served modules). gzip on for text assets at comp level 5, short `must-revalidate` cache on `index.html`, moderate cache on `/css/` + `/js/` (flip to `immutable` once filenames are content-hashed). Minimal security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: no-referrer`. CSP intentionally omitted — the frontend calls a user-configurable API origin via `?api=...`, so a tight CSP belongs in deployment-specific config, not the baked image. `HEALTHCHECK` wgets `/` to prove both nginx liveness and that the static files are in place.
- **`docker-compose.yml`** with three services:
  - `daemon` — builds from root `Dockerfile`, binds `127.0.0.1:7423:7423`, mounts `./data:/app/data`, loads optional `.env` from repo root, `depends_on` is empty (daemon boots fine without Ollama; UI just shows "local - degraded" until the backend is up).
  - `frontend` — builds from `./frontend`, binds `127.0.0.1:5173:80`, `depends_on: daemon: condition: service_healthy` so the UI doesn't flash red banners during first boot.
  - `ollama` — `profiles: ["llm"]`, opt-in via `docker compose --profile llm up`. Kept off the default `up` path so a fresh clone doesn't pull a ~2GB image + force a model download on first run. Model cache persists at `./data/ollama/`. When the profile is active, the daemon reaches it over the compose network at `http://ollama:11434` (baked default in `FSF_LOCAL_BASE_URL`); when it isn't, users on a frontier provider or a host-side Ollama override the URL in `.env`.
- **State persistence: host bind-mount at `./data`.** ADR-0006 treats the SQLite registry as an index that's **rebuildable from artifacts** — the audit chain JSONL and soul files ARE the source of truth. Bind-mounts keep that tree visible on the host for `cat`, `git add`, rotation, and out-of-band inspection. Named volumes would hide the data behind `docker cp` ceremony, which is wrong for a system whose canonical data is a files-on-disk contract. Subdirs: `data/registry.sqlite{,,-shm,-wal}`, `data/artifacts/` (seeded from `/app/examples`), `data/artifacts/audit_chain.jsonl`, `data/soul_generated/`, `data/ollama/` (llm profile only). `.gitignore` carves out `data/*` except `data/.gitkeep`.
- **`.env.example`.** Documents every env knob compose will respect (`FSF_API_TOKEN`, `FSF_DEFAULT_PROVIDER`, `FSF_LOCAL_*`, `FSF_FRONTIER_*`, `FSF_CORS_ALLOW_ORIGINS`) with inline guidance. `.env` itself is gitignored. Nothing is required for a fresh clone — defaults produce a working stack; the file exists to make overrides discoverable rather than tribal knowledge.
- **`.dockerignore` cleanup.** The root `.dockerignore` was test-image-specific; now shared between `Dockerfile` and `Dockerfile.test` with `data/` added to the exclusion list so compose runtime state never bloats the build context. Frontend still excluded from the root context because it has its own build context at `./frontend/`.
- **CORS: unchanged.** The daemon's baked-in allowlist (`http://localhost:5173`, `http://127.0.0.1:5173`, `null`) already covers the compose-published frontend origin, so no config drift between the launcher flow and the compose flow. The browser — not another container — calls the daemon, so the allowlist keys off the public origin the browser sees, not an in-network hostname.
- **Known gap: Linux host UID != 1000.** If your host UID isn't 1000, the bind-mounted `./data/` will be owned by a UID the `fsf` container user can't write to. Override at run-time with `docker compose run --user "$(id -u):$(id -g)" daemon …` or `chown -R 1000:1000 data/` once. macOS / Docker Desktop handles this transparently. Noted here rather than hacked around so the image stays predictable across hosts.

### Phase 3 — idempotency, auth, preview, traits, Docker test infra — 2026-04-24

- **`X-Idempotency-Key` layer.** All three write endpoints (`/birth`, `/spawn`, `/archive`) now honor the header per ADR-0007. Request hash is `sha256(endpoint || NUL || json.dumps(body, sort_keys=True, default=str))`, computed *before* the write_lock so the lookup inside the lock is a pure SELECT. Replay returns the original status code + response bytes verbatim via a raw `fastapi.Response` (bypasses `response_model` re-serialization, so the client byte-for-byte sees the original). Same key + different body returns **409 Conflict** with a message naming the endpoint. Empty key or key longer than 200 chars returns **400**. Missing key → normal write, no caching.
- **New registry table `idempotency_keys`.** Columns: `key TEXT PRIMARY KEY, endpoint TEXT NOT NULL, request_hash TEXT NOT NULL, status_code INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL`; index on `created_at` for future TTL sweeps. Added to `CREATE_SCHEMA_SQL` via `CREATE IF NOT EXISTS`, so the schema version stays at **2** — fresh databases pick it up and existing v2 databases absorb the table on next bootstrap without a version bump.
- **Registry API additions.** `Registry.lookup_idempotency_key(key, endpoint, request_hash) -> tuple[status, response_json] | None` and `Registry.store_idempotency_key(...)`. Lookup raises the new `IdempotencyMismatchError` (409-mapped) when the key exists but the `(endpoint, request_hash)` tuple doesn't match. Store uses `INSERT OR IGNORE` so the first-write-wins semantics hold even under the theoretical race where two concurrent requests pass the lookup-miss check. (In practice the write_lock serializes them, but the DB-level guarantee is the belt to the lock's suspenders.)
- **Write-handler wiring.** Each handler in `daemon/routers/writes.py` now:
  1. *Before* the lock: reads `X-Idempotency-Key`, computes the request hash from `req.model_dump(mode="json")`.
  2. *First thing inside the lock:* `_maybe_replay_cached(...)` — on hit, returns the cached `Response` immediately; on endpoint/hash mismatch, raises 409.
  3. On successful write: `_cache_response(...)` stores the response JSON + status code keyed by `(key, endpoint, request_hash)`.
  4. Archive's already-archived short-circuit runs *before* the lock and is deliberately *not* cached — it's a derived read of existing state, not a mutation, so caching it would couple an idempotency key to a response that might change if someone manually reverts the status.
- **`X-FSF-Token` auth.** `DaemonSettings.api_token: SecretStr | None` (env `FSF_API_TOKEN`). When set, every non-`/healthz` route requires the exact token in the `X-FSF-Token` header — missing token → 401 `missing_api_token`, wrong token → 401 `invalid_api_token`. `/healthz` stays unauthenticated so process monitors can hit it, and it surfaces `auth_required: true|false` in the body so a UI can decide whether to prompt. Matches the shape promised by ADR-0007; the optional-by-default behavior keeps the common laptop case keyless.
- **`GET /traits` read endpoint.** Returns the full loaded trait tree as JSON — domains with their subdomains, subdomains with their traits, traits with scale + description + tier. Source for the frontend's radar chart and slider panel. Uses a fresh `TraitEngine.to_dict()` projection rather than re-reading the YAML on every request; engine is singletonized in app.state at startup.
- **`POST /preview` zero-write endpoint.** Body mirrors `BirthRequest` minus `agent_name`/`agent_version` (not needed — no artifact is produced). Response is the per-slider-nudge feedback payload: `dna`, `dna_full`, `role`, `constitution_hash_derived` (pre-override), `constitution_hash_effective` (post-override, what `/birth` would store), full `grade` report (overall score + dominant domain + per-domain intrinsic/weighted + warnings), live `flagged_combinations` evaluated against this profile, and `effective_profile` echoed back post-validation so the UI can spot clamped domain weights. **No file I/O, no chain append, no registry insert, no soul markdown or constitution YAML in the response** (too heavy for every slider nudge — the authoritative bytes are produced by `/birth`). Override folds into the effective hash identically to `/birth` (`sha256(derived || "\noverride:\n" || override)` per Path D) so `/preview` → `/birth` round-trip produces bit-identical hashes. Unknown role → 400.
- **Docker test infrastructure.** Sandbox Python environments that can't reach PyPI couldn't run pytest at all — `pip install pytest fastapi ...` returned 403 through the proxy and the project's `.venv` symlinks a Python that doesn't exist in Linux containers. Solution lives on the user's machine, not in the sandbox: `Dockerfile.test` (deps-only image, Python 3.12-slim, pinned to `pyproject.toml`'s `[dev]` + `[daemon]` groups), `.dockerignore` (keeps the build context lean; source is never COPYed in), `scripts/docker_test.sh` (bind-mounts `$PWD:/app`, `PYTHONPATH=/app/src`, auto-rebuilds on `Dockerfile.test` or `pyproject.toml` mtime change against the image's creation time — so the usual loop is edit-and-rerun with no rebuild). Full suite **28/28 green** on Mac in ~0.9s; idempotency filter **5/5 green** in ~0.4s. The image is deliberately separate from the production daemon/frontend compose (Task #8) so test deps don't bloat the runtime image.
- **Tests added** in `tests/unit/test_daemon_writes.py`:
  - `TestIdempotency` (5 cases): same key + same body → cached replay; same key + different body → 409; empty key → 400; spawn replay; archive replay.
  - `TestAuth` (4 cases): missing token → 401; wrong token → 401; correct token passes; `/healthz` surfaces `auth_required: true` without requiring the token itself.
  - `TestTraitsEndpoint` (1 case): `/traits` returns a well-formed tree with known role/domain shape.
  - `TestPreviewEndpoint` (4 cases): `/preview` hash matches the corresponding `/v1/birth` call; override changes hash; preview is zero-write (no files, no chain entry, no registry row); unknown role → 400.

### Phase 3 — write endpoints — 2026-04-24

- **Registry schema bumped 1 → 2.** New column `agents.sibling_index INTEGER NOT NULL DEFAULT 1` plus composite index on `(agent_dna, sibling_index)`. Existing databases are rejected at bootstrap with a version mismatch rather than silently migrated — the rebuild path from artifacts is the upgrade procedure. Added `schema_version()` assertion updates to the three bootstrap tests that previously pinned v1.
- **Twin agents are now first-class.** Two agents with identical `TraitProfile` produce the same `dna` (as designed — DNA is the canonical profile hash), but they are disambiguated by `sibling_index` starting at 1. The first sibling keeps the clean `role_dna` `instance_id`; the second and beyond get a `_N` suffix (`role_dna_2`, `role_dna_3`, …). The suffix is only applied when load-bearing — no cosmetic `_1` on first siblings. `Registry.next_sibling_index(dna)` is a pure read; pairing it with the INSERT inside the write_lock closes the twin-birth race.
- **Soul frontmatter extended.** `SoulGenerator.generate()` accepts `instance_id`, `parent_instance`, and `sibling_index` keyword-only arguments and emits them into the frontmatter between the identity block and the constitution block. `ParsedSoul` and the ingest parser learned to read them back out, so rebuild-from-artifacts round-trips the new fields without loss.
- **New event type `agent_archived`.** Added to `audit_chain.KNOWN_EVENT_TYPES`. Event data carries `instance_id`, `agent_name`, `role`, `reason`, optional `archived_by`, and `archived_at`.
- **Three write endpoints on the FastAPI daemon:**
  - `POST /birth` — create a root agent. Body: `{profile: TraitProfileIn, agent_name, agent_version, owner_id?, constitution_override?}`. Returns `AgentOut` with 201. Errors: 400 on unknown role / unknown trait / invalid trait value / invalid domain weight.
  - `POST /spawn` — same body plus `parent_instance_id`. Reconstructs parent `Lineage` from registry (`get_ancestors` reversed to root-first), builds the child lineage, writes artifacts with `parent_instance` in the frontmatter. 404 on unknown parent.
  - `POST /archive` — body: `{instance_id, reason, archived_by?}`. Flips status to `archived`. Idempotent: archiving an already-archived agent returns 200 with the current row, no duplicate event. 404 on unknown instance.
- **Ordering discipline per ADR-0006.** Every write handler:
  1. Validates input (profile → 400 on any engine error, so no file I/O happens before we know the request is well-formed).
  2. Builds soul + constitution bytes outside the lock — pure computation, any failure surfaces as 400.
  3. Acquires `app.state.write_lock` (threading.Lock, correct primitive since FastAPI dispatches sync handlers on a threadpool).
  4. Calls `next_sibling_index`, builds `instance_id`, writes constitution then soul to `soul_output_dir`.
  5. Appends one audit-chain entry. **This is the commit point.**
  6. Mirrors the entry + registers the agent (birth/spawn) or updates status + mirrors the archive event (archive) into the registry.
  7. If step 5 fails, step 4 rolls back (unlinks both artifact files) so no ghost artifacts exist without chain acknowledgment. If step 6 fails after step 5 succeeded, the registry can be rebuilt from artifacts and will re-derive the same state.
- **Constitution override (Path D).** `BirthRequest` / `SpawnRequest` accept an optional `constitution_override` string — arbitrary YAML that gets appended to the derived constitution file under a `# --- override ---` marker. The override bytes are SHA-256-folded into the `constitution_hash` stored in the soul frontmatter: `sha256(derived_hash || "\noverride:\n" || override_yaml)`. Tampering with the override after mint invalidates verification. Event data records `constitution_source: "derived+override"` vs `"derived"` so auditors can filter.
- **`allow_write_endpoints` gate.** New `DaemonSettings.allow_write_endpoints` (default `True`). When `False`, all three POST endpoints return 403 via a `require_writes_enabled` dependency. Lets a read-only deployment refuse mutations at the edge without changing routing.
- **Settings expanded.** `trait_tree_path` (default `config/trait_tree.yaml`), `constitution_templates_path` (default `config/constitution_templates.yaml`), `soul_output_dir` (default `soul_generated`). CORS `allow_methods` extended to `["GET", "PUT", "POST"]`.
- **App factory lifespan bootstraps write-path singletons.** `trait_engine`, `audit_chain`, and the `threading.Lock` write_lock are built once at startup and stashed on `app.state`. Trait engine and audit chain construction are wrapped in try/except so a missing/corrupt file doesn't prevent read-only endpoints from serving; write endpoints return 503 in that case.
- **New deps helpers.** `get_trait_engine`, `get_audit_chain`, `get_write_lock`, `get_settings`, `require_writes_enabled` in `daemon/deps.py`.
- **Schemas.** Added `TraitProfileIn`, `BirthRequest`, `SpawnRequest(BirthRequest)`, `ArchiveRequest`; added `sibling_index: int = 1` to `AgentOut`.
- **Tests.** `tests/unit/test_daemon_writes.py` — 14 end-to-end cases across `TestBirth`, `TestSpawn`, `TestArchive`, `TestAuditMirror`, `TestWritesDisabled`. Uses the real trait tree + constitution templates + a scratch registry and audit chain, with stub provider so no Ollama dependency. Covers twin sibling_index=2, unknown role/trait/value → 400, override changes hash, child lineage chain, unknown parent → 404, idempotent archive, audit mirror for both `agent_created` and `agent_archived`, and writes-disabled → 403.
- **Archive audit mirror fix.** Initial implementation appended `agent_archived` to the chain but didn't mirror it into the registry's `audit_events` table, so `/audit/tail` (which reads from the mirror) never surfaced it. Added explicit `registry.register_audit_event(...)` call inside the write_lock after the chain append — birth/spawn got the mirror for free via `register_birth(audit_entry=...)`, archive doesn't insert a row so it needs the call explicitly.

### Phase 3 bootstrap — 2026-04-23

- **ADR-0006 (registry-as-index)** — `docs/decisions/ADR-0006-registry-as-index.md`. SQLite at `state/registry.sqlite` is a derived, rebuildable index; canonical artifacts (soul.md, constitution.yaml, audit/chain.jsonl) remain authoritative. Dual identity: 12-char `dna` + 64-char `dna_full` + UUID v4 `instance_id`. v1 schema: `agents`, `agent_ancestry` (closure table with self-edge at depth 0), `audit_events`, `agent_capabilities` (stub), `tools` (stub), `registry_meta`. One-way sync path `artifact → audit chain → registry`; `rebuild_registry()` is the escape hatch. Explicit deviations from the Grok-proposed schema are recorded in the ADR: UUID split into dna + instance_id; audit_log demoted to index so ADR-0005 tamper-evidence is preserved; owner_id nullable; capabilities/tools stubbed now to avoid a future migration.
- **ADR-0007 (FastAPI daemon)** — `docs/decisions/ADR-0007-fastapi-daemon.md`. Localhost-bound single process (`:7423`) as the only frontend-to-engines path; Electron and in-browser ports rejected with reasons. `asyncio.Lock`-serialized writes; WAL-mode SQLite for concurrent reads; optional `FSF_API_TOKEN` shared secret; strict CORS allowlist. v0.1 endpoint surface split read/write with `/preview` as the zero-write path for live slider feedback. Every write endpoint honors `X-Idempotency-Key`. Write-ordering rule: audit chain first, then artifact, then registry — so partial failures are recoverable via `rebuild_registry`. Daemon refuses writes if `AuditChain.verify()` fails on startup.
- **Trait tree v0.2** — `config/trait_tree.yaml` bumped from 0.1 to 0.2. New `embodiment` domain with `presentation` subdomain adds three tertiary traits: `visual_density`, `signature_warmth`, `motion_liveliness`. `motion_liveliness` is forward-compat for Phase 5+ avatar/screen-presence work, parked in the tree now so it has a hashed home in DNA rather than requiring a future schema bump. Each role gained an explicit `embodiment` weight (network_watcher 0.5, log_analyst 0.5, anomaly_investigator 0.7, incident_communicator 1.2, operator_companion 1.5). Trait count 26 → 29.
- **Grading: float-tolerance tie-break** — `_dominant_domain` now uses an absolute epsilon (1e-9) instead of bare `==` on weighted scores. Tier weights include 0.3 (tertiary), which isn't exactly representable; a subdomain of all-tertiary traits produces `0.3+0.3+0.3 = 0.8999999999999999`, which bled into per-domain scores as `50.00000000000001`. That was a latent bug — the canonical-order tie-break depended on exact float equality — exposed when `embodiment.presentation` (all tertiary) joined the tree. Fixed with comment explaining why epsilon was chosen over rounding or rationals.
- **Canonical domain order** extended in `src/forest_soul_forge/core/grading.py` to append `embodiment`. Existing tie-break for non-canonical domains still works (they sort after canonical); the explicit append matches the comment's instruction and documents the new domain's position.
- **Examples regenerated** under `examples/` — every soul, every sibling `.constitution.yaml`, and `audit_chain.jsonl` rebuilt under v0.2. Every DNA shifted (adding traits changes the canonical profile hash). Old v0.1 DNAs are preserved only in git history.
- **Test harness fix** — `scripts/run_tests_no_pytest.py` now builds a fresh `tmp_path` per test invocation, matching pytest's semantic. Previously it built one `tmp_path` per module, which caused the new `test_audit_chain.py` tests to see state from prior tests since they all opened `tmp_path / "chain.jsonl"`. Uncovered while verifying v0.2; not a v0.2 regression but a pre-existing harness gap. Live tests: **144/144** under the harness; three verify scripts (grading 23/23, constitution 34/34, audit_chain 32/32); demo end-to-end green.
- **Demo script robustness** — `scripts/demo_generate_soul.py` now truncates `examples/audit_chain.jsonl` in place rather than unlinking it. Some mounts (containerized dev environments, read-only bind-mount segments) allow writes but forbid unlink on files they own; truncate-then-refill reaches the same end state without the permission error.
- **Frontend placeholder** — `frontend/` contains a React+ESM scaffold produced during design-tab iteration (`index.html`, `js/app-data.js`, `js/llm-client.js`, plus three dead `ForestApp-*.js` files). Kept as-is in this commit; Task #7 (rewire to call the daemon) will delete the dead files and wire real API calls.

### Added
- Initial repo scaffolding: directory structure, README, LICENSE (Apache 2.0), `.gitignore`, `pyproject.toml`.
- Vision brief preserved in `docs/vision/handoff-v0.1.md`.
- Directory layout rationale in `docs/architecture/layout.md`.
- ADR and audit indexes in `docs/decisions/README.md` and `docs/audits/README.md`.
- Phase 1: hierarchical trait tree design — `docs/architecture/trait-tree-design.md` (5 domains, 10 subdomains, 26 traits, 5 role presets, 7-phase expansion roadmap).
- Phase 1: trait tree schema — `config/trait_tree.yaml`.
- Phase 1: ADR-0001 (hierarchical trait tree with themed domains and tiered weights), status Accepted.
- Phase 2: core engines.
  - `src/forest_soul_forge/core/trait_engine.py` — loads and validates `trait_tree.yaml`, exposes typed API (Trait, Domain, Subdomain, Role, TraitProfile, FlaggedCombination). Includes profile builder, effective-weight calculator, and flagged-combination scanner.
  - `src/forest_soul_forge/soul/generator.py` — converts a TraitProfile into a structured `soul.md`, ordered by effective domain weight, with tier-based trait inclusion and warning surfacing.
  - `tests/unit/test_trait_engine.py` and `tests/unit/test_soul_generator.py` — pytest unit tests. 68 passing via the stdlib harness; awaiting pytest run on the user's machine for full fidelity.
  - `scripts/demo_generate_soul.py` — end-to-end smoke test that generates example `soul.md` files under `examples/` and verifies weight math.
  - `scripts/run_tests_no_pytest.py` — stdlib-only test harness so we can exercise the pytest suite in environments without pytest available.
  - Runtime dep added: `pyyaml>=6.0`.
- Phase 2 remainder: grading engine, constitution builder, audit chain.
  - **Grading engine (ADR-0003)** — `src/forest_soul_forge/core/grading.py`. Pure function `grade(profile, engine) -> GradeReport` computes a config-grade score: subdomain scores are tier-weighted means of trait values, intrinsic domain score is the mean of its subdomains, overall is the role-weighted mean of intrinsic domain scores. Tertiary-tier traits below `TERTIARY_MIN_VALUE` (40) surface as warnings. Dominant domain is selected with a canonical tie-break (security → audit → emotional → cognitive → communication). `GradeReport.render()` produces a CLI-friendly multi-line summary. Frozen dataclasses throughout; fully deterministic. Tests: `tests/unit/test_grading.py` (~20 cases) plus sandbox smoke `scripts/verify_grading.py` (23/23 passing).
  - **Constitution builder (ADR-0004)** — `src/forest_soul_forge/core/constitution.py` + `config/constitution_templates.yaml`. Three-layer composition: `role_base` (per-role policies and thresholds), `trait_modifiers` (threshold-triggered policies, e.g. `caution>=80` adds `caution_high_approval`), `flagged_combinations` (dangerous trait intersections emit `forbid` policies). Conflict resolution is strictness-wins across the ordered set `{allow, require_human_approval, forbid}`; weaker rules keep their entry but record a `superseded_by` pointer. Non-ordered rules like `require_explicit_uncertainty` stack without conflict. `constitution_hash` is SHA-256 over canonical JSON of the rulebook body only — identity (role, DNA, agent name) is bound in the soul frontmatter, not the hash. This lets two agents with the same profile share a constitution hash while keeping distinct DNA. Tests: `tests/unit/test_constitution.py` (~20 cases) plus `scripts/verify_constitution.py` (34/34 passing).
  - **Soul ↔ constitution binding** — `SoulGenerator.generate()` accepts `constitution_hash` and `constitution_file` keyword-only arguments; when supplied, both are emitted into the frontmatter immediately after `generated_at`. Passing exactly one of the pair raises `ValueError` — they are an atomic pair or both absent.
  - **Audit chain (ADR-0005)** — `src/forest_soul_forge/core/audit_chain.py`. Append-only hash-linked JSONL log; SHA-256 over canonical JSON of `{seq, prev_hash, agent_dna, event_type, event_data}` — timestamp is deliberately excluded to keep clock skew from breaking verification. Auto-genesis on open (a fresh file gets a `chain_created` entry synchronously). Eight known event types (`chain_created`, `agent_created`, `agent_spawned`, `constitution_regenerated`, `manual_override`, `drift_detected`, `finding_emitted`, `policy_violation_detected`); unknown event types verify as warnings, not failures, for forward-compat. `verify()` walks from seq=0 and reports the first structural break (seq gap, prev_hash mismatch, entry_hash mismatch, invalid JSON) with the offending seq and reason. v0.1 is **tamper-evident, not tamper-proof** — see ADR-0005 for the threat model. `_recompute_head` tolerates malformed trailing lines on open so `verify()` remains callable against corrupted files. Operator-facing docs at `audit/README.md`. Tests: `tests/unit/test_audit_chain.py` (~24 cases) plus `scripts/verify_audit_chain.py` (32/32 passing).
  - **Demo script upgrade** — `scripts/demo_generate_soul.py` now builds a constitution for every generated soul, writes a sibling `<stem>.constitution.yaml`, binds the hash into the soul frontmatter, and records `agent_created` / `agent_spawned` events to `examples/audit_chain.jsonl`. The run ends with an audit chain verify() that must return ok=True with the expected entry count (11 = genesis + 5 role defaults + 2 stress + 3 lineage).
  - **Examples regenerated** under `examples/` — every soul now has a sibling `.constitution.yaml` plus frontmatter binding, and the checked-in `audit_chain.jsonl` shows the full demo event stream.
  - **ADR-0002 amendment** — `src/forest_soul_forge/soul/dna.py` moved to `src/forest_soul_forge/core/dna.py` (with all five call-site imports updated) so that `core/` no longer imports from `soul/`. `soul/` now depends on `core/` only, not the reverse. Amendment note appended to ADR-0002 explaining the relocation.

- Wave 1 polish (ADR-0002 — Agent DNA and lineage):
  - `src/forest_soul_forge/soul/dna.py` — deterministic SHA-256 hash of the canonical `TraitProfile` (role + sorted trait_values + sorted domain_weight_overrides). 12-char short DNA + 64-char full form. `verify()` helper accepts either form.
  - `Lineage` dataclass modeling the ancestor chain (root-first). Spawning agents use `Lineage.from_parent(parent_dna, parent_lineage, parent_agent_name)` to extend the chain; grandchildren preserve the full root-first ancestor list.
  - Every generated `soul.md` now opens with a YAML frontmatter block containing `dna`, `dna_full`, role, agent metadata, `parent_dna`, `spawned_by`, full `lineage` array, `lineage_depth`, every trait_value (sorted), and any domain_weight_overrides. The body becomes self-verifying: re-hash the frontmatter's trait block and compare to `dna`.
  - New prose format: each trait renders as `- **name** — value/100 (band). scale-text.` with an italicized description on the next line. `scale.mid` is now populated for all 26 traits, eliminating the earlier awkward `"low / high"` concat for moderate-band values.
  - Spawned agents (lineage depth > 0) emit a `## Lineage` footer listing the full root-first ancestor chain plus the agent's own DNA.
  - Docs: `docs/decisions/ADR-0002-agent-dna-and-lineage.md`, ADR index updated.
  - Examples regenerated under `examples/`, plus new `lineage_parent_huntmaster.soul.md`, `lineage_child_scout.soul.md`, `lineage_grandchild_forager.soul.md` demonstrating a 3-generation chain.

### Changed
- `Trait` dataclass gained `scale_mid` — required in `trait_tree.yaml` for v0.1, with a graceful fallback for legacy schemas that lack it.
- Soul prose rewritten to use bold trait names + banded values + italic descriptions. Drops the earlier robotic repeating-text format.
- `SoulGenerator.generate()` signature: new keyword-only `lineage: Lineage | None` parameter. Root agents default to `Lineage.root()`.

### Not yet started
- Agent factory (which will consume the `Lineage` primitives) and blue-team agents.
- Streamlit UI.
- LangGraph supervisor layer.
- Wave 2: SVG radar chart, profile diff tool, CLI (`generate`/`diff`/`list`/`validate`).
- Wave 3: expanded README, CONTRIBUTING.md, SECURITY.md, Makefile, `.editorconfig`, pre-commit, CI skeleton, golden-file snapshot tests.
