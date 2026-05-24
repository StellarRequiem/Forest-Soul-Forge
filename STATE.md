# Forest Soul Forge — current state

A self-contained snapshot for a developer joining the project. What's implemented, what's blocked, what conventions matter, and where to start.

> **Refresh cadence:** this doc + [`README.md`](README.md) update together at every phase boundary (Phase A close, Phase B close, Phase D close, etc.) and after any meaningful architectural finding. The two are designed to stay in sync; STATE.md is the developer-facing current-reality view, README.md is the product-and-mission view.

**Refresh marker (B423, 2026-05-19): the body below freezes the post-B258 baseline; the appendix at the end of this doc reconciles every numeric claim against current disk-truth and lists every closed ADR since.**

---

## Status snapshot as of 2026-05-19 (Burst 420, HEAD 077610c)

| Surface | Current |
|---:|:---|
| Commits on `main` | **575** |
| Python LoC (`src/forest_soul_forge/`) | **87,188** (+27,586 / +46% since v0.5 tag) |
| ADRs filed | **78** (gaps 0009-0015, 0024-0026, 0028-0029 — placeholders) |
| Schema version | **v23** (post-Phase α encryption + vector + provenance) |
| Tests | **214 test files** across `tests/unit/` + `tests/integration/` + `tests/conformance/` |
| Builtin tools (Python files) | **69** (catalog YAML in sync) |
| Skill manifests | **46** in `examples/skills/`, **38** installed |
| Trait roles | **54** |
| Genres | **13** (7 original + 3 security + 3 web) |
| Alive agents in registry | **34 active / 4 archived / 38 total** |
| Audit chain entries | **19,211** at `examples/audit_chain.jsonl` |
| Audit docs filed | **20** in `docs/audits/` |
| Repo-root `.command` scripts | **26** post-B422 consolidation (was 64) |
| Archived commit-burst scripts | **391** under `dev-tools/commit-bursts/` |
| Latest tag | `v0.5.0` (2026-05-04) — v0.6 unanchored (gated on ADR-0044 P6 external integrator validation) |

**Phase α — substrate (10/10 closed):** ADR-0050 encryption-at-rest, ADR-0067 cross-domain orchestrator, ADR-0068 operator profile, ADR-0070 voice I/O, ADR-0071 plugin author kit, ADR-0072 behavior provenance, ADR-0073 audit chain segmentation, ADR-0074 memory consolidation, ADR-0075 scheduler scale, ADR-0076 vector index. Closed across Bursts 281-330 (2026-05-15).

**Domain rollouts (per ADR-0067 dependency order D4 → D3 → D8 → D1 → D2 → D7 → D9 → D10 → D5 → D6):**
- **D4 Code Review** — CLOSED (ADR-0077, Bursts 331-340). TestAuthor-D4, MigrationPilot-D4, ReleaseGatekeeper-D4 alive.
- **D3 Local SOC** — **CLOSED — fully live, all 15 SOC agents alive (2026-05-22).** Phase A CLOSED (ADR-0078, Bursts 342-347; ForensicArchivist-D3 alive). Phase B telemetry pipeline (ADR-0064) CLOSED Bursts 348-386. Phase C detection-as-code (ADR-0065) CLOSED Bursts 389-392 — DetectionEngineer-D3 alive; Sigma-subset rule engine + per-batch scan hook + 8-rule starter library; runbook `docs/runbooks/detection-as-code.md`. Phase D SOAR playbooks (ADR-0066) CLOSED Bursts 454-459 — playbook_pilot + purple_pete alive (PlaybookPilot-D3, PurplePete-D3); SOAR playbook DSL + PlaybookEngine (`src/forest_soul_forge/security/playbook/`) + purple-team scenario substrate (`.../purple_team/`); 3-playbook + 5-scenario starter libraries; runbook `docs/runbooks/soar-playbooks.md`. The full Detect → Respond → Test loop is live.
- **D8 Compliance Auditor** — **CLOSED — fully LIVE, all 5 agents alive (2026-05-22).** ADR-0085 all 4 phases shipped. Roles: audit_archivist, evidence_collector (Phase A); compliance_scanner (Phase B); policy_enforcer (Phase C, YELLOW); report_generator (Phase D). Three new builtin tools: `framework_check.v1` (25 tests), `policy_lint.v1` (20 tests), `audit_packet_generate.v1` (13 tests). Five skills (evidence_collection, long_term_archival, compliance_scan, policy_enforcement, compliance_reporting). SOC2 seed framework at `config/compliance_frameworks/soc2.yaml`. Cascade wiring d4→d8 + d3→d8 live. Umbrella birth `dev-tools/birth-d8-compliance.command`. Runbook `docs/runbooks/d8-compliance-ops.md`.
- **D1 Personal Knowledge Forge** — **CLOSED — fully LIVE, all 4 agents alive (2026-05-23).** ADR-0086 all 4 phases shipped. Roles: librarian + prospector (Phase A), synthesizer (Phase B), knowledge_verifier (Phase C, YELLOW), + delta substrate (Phase D). Three new builtin tools: `topic_genealogy_build.v1` (28 tests), `knowledge_contradiction_scan.v1` (28 tests; single-agent scope per Decision 3), `daily_knowledge_delta.v1` (19 tests). Six skills (`knowledge_curation.v1`, `research_gathering.v1`, `topic_genealogy.v1`, `knowledge_summarize.v1`, `knowledge_contradiction_flag.v1`, `daily_knowledge_delta.v1`). Cascade wiring d8→d1 active; d1→d9/d10/d7/d2 declared INERT. Umbrella birth `dev-tools/birth-d1-knowledge-forge.command`. Runbook `docs/runbooks/d1-knowledge-forge-ops.md`. Manifest's bare `verifier` entry_agent renamed to `knowledge_verifier` per ADR-0086 Decision 2 to avoid collision with verifier_loop / reality_anchor.
- **D2 Daily Life OS** — **CLOSED — fully LIVE, all 5 agents alive (2026-05-23).** ADR-0087 all 4 phases shipped. Roles: coordinator + inbox_triager (Phase A), time_steward (Phase B, YELLOW), task_prioritizer (Phase C), reflector (Phase D). Four new builtin tools: `schedule_reminder.v1` (filesystem; 23 tests), `calendar_block.v1` (external; 28 tests; graceful degrades when forest-calendar absent), `task_rank.v1` (read_only; 38 tests; deterministic urgency/impact/effort ranking), `decision_journal_compile.v1` (read_only; 29 tests; audit-chain decision/deferral/pattern bucketer). Seven skills (`daily_orchestration.v1`, `inbox_triage.v1`, `schedule_reminder.v1`, `calendar_management.v1`, `task_prioritization.v1`, `daily_reflection.v1`, `decision_journal.v1`). Cascade wiring d1→d2 morning_briefing ACTIVE; d2→d3/d5/d6/d7 declared INERT. Umbrella birth `dev-tools/birth-d2-daily-life-os.command`. Runbook `docs/runbooks/d2-daily-life-os-ops.md`.
- **D7 Content Pipeline** — **CLOSED — fully LIVE, all 5 agents alive (2026-05-23).** ADR-0088 all 4 phases shipped. Roles: writer + content_researcher (Phase A, GREEN); style_steward (Phase B, guardian, GREEN); editor (Phase C, guardian, GREEN); distribution_pilot (Phase D, actuator, YELLOW). Four new builtin tools: `voice_profile_build.v1` (read_only; 21 tests; deterministic stylometric profiler), `voice_match_check.v1` (read_only; 22 tests; scores drafts against profile + flags drift with span pointers), `format_adapt.v1` (read_only; 26 tests; adapter to twitter_thread / linkedin_post / newsletter / blog), `publish_schedule.v1` (external; 32 tests; queues to data/d7/publish_queue.jsonl for forest-publish connector). Seven skill manifests (`draft_writing.v1`, `content_research.v1`, `voice_profile_build.v1`, `voice_matching.v1`, `editing.v1`, `format_adaptation.v1`, `scheduled_publishing.v1`, `performance_tracking.v1`). Cascade wiring d1→d7 knowledge_curation + d2→d7 daily_reflection ACTIVE; d4→d7 + d7→d9 declared INERT. Umbrella birth `dev-tools/birth-d7-content-studio.command`. Runbook `docs/runbooks/d7-content-pipeline-ops.md`. Manifest's bare `researcher` entry_agent renamed to `content_researcher` per ADR-0088 Decision 2 to avoid collision with the researcher genre name.
- **D9 Learning Coach** — **CLOSED — fully LIVE, all 5 agents alive (2026-05-23).** ADR-0089 all 4 phases shipped. Roles: mentor + curriculum_designer (Phase A, researcher, GREEN); assessor (Phase B, guardian, YELLOW); socratic_partner (Phase C, communicator, GREEN); spaced_repetition_pilot (Phase D, actuator, YELLOW). Five new builtin tools: `curriculum_design.v1` (read_only; 27 tests; deterministic topic-prereq DAG composer), `knowledge_assessment.v1` (read_only; 19 tests; quiz-item envelope generator), `assessment_score.v1` (read_only; 17 tests; strict-match + Jaccard lexical-overlap scorer), `misconception_log.v1` (filesystem; 18 tests; misconception ledger writer — NOT in any role's standard kit because guardian's read_only ceiling rejects filesystem; operator dispatches directly post-review per the D1 knowledge_verifier separation pattern), `spaced_repetition_schedule.v1` (filesystem; 25 tests; SM-2 interval computation + queue write to data/d9/review_queue.jsonl). Seven skill manifests (`coaching.v1`, `curriculum_design.v1`, `knowledge_assessment.v1`, `misconception_tracking.v1`, `socratic_dialogue.v1`, `spaced_repetition.v1`, `skill_certification.v1`). Cascade wiring d1→d9 knowledge_contradiction_flag + d7→d9 editing + d9→d2 spaced_repetition + d9→d2 curriculum_design ACTIVE; d9→d10 + d9→d1 + d9→d7 declared INERT. Umbrella birth `dev-tools/birth-d9-learning-coach.command`. Runbook `docs/runbooks/d9-learning-coach-ops.md`.
- **D10 Multi-Agent Research Lab** — **IN FLIGHT — Phase A shipped (2026-05-23).** ADR-0090 Phase A landed: gatherer + analyst roles (researcher genre, GREEN posture). No new builtin tools (both roles reuse existing kit — gatherer loads web_fetch, analyst loads verify_claim as load-bearing tools). Two skill manifests (`source_gathering.v1`, `deep_analysis.v1`). Birth scripts `dev-tools/birth-gatherer.command` + `dev-tools/birth-analyst.command`. Runbook `docs/runbooks/d10-research-lab-ops.md`. Phases B (critic + lab_synthesizer + citation_graph_build.v1 + confidence_score.v1), C (debate_moderator + claim_provenance.v1 + debate_orchestrate.v1), D (cascade + umbrella + live) pending. Manifest's bare `synthesizer` entry_agent will be renamed to `lab_synthesizer` in Phase B (Decision 2) to avoid collision with D1's synthesizer. Manifest's `experimenter` entry_agent is referenced from ADR-0056 (shipped) — NOT re-created.
- **D5 Smart Home Brain** — **IN FLIGHT — Phase A shipped (2026-05-24).** ADR-0091 Phase A landed: home_steward (researcher, GREEN) + home_sentinel (guardian, GREEN). No new builtin tools (both roles read_only-by-construction over home_state memory attestations; no outbound network surface). Two skill manifests (`home_orchestration.v1`, `home_security.v1`). Birth scripts `dev-tools/birth-home-steward.command` + `dev-tools/birth-home-sentinel.command`. Runbook `docs/runbooks/d5-smart-home-ops.md`. Phases B (energy_warden + comfort_optimizer + energy_anomaly_scan.v1 + comfort_recommend.v1), C (routine_composer + routine_compose.v1 + home_state_snapshot.v1), D (cascade + umbrella + live) pending. Substrate-ready: D5 ships without forest-home-assistant connector present (operator supplies `home_state_snapshot` attestations; connector ingests them later via the same shape). routine_composer is the only YELLOW role (queue-driven actuation per ADR-0091 Decision 2 — no direct device control in D5).
- **D6** — upstream; not started. (D5 is in flight per ADR-0067 rollout order; D6 ships last.)

**Tooling discipline closed since B258:** ADR-0079 diagnostic harness (15 sections, daily 8am scheduled task), ADR-0080 capability tree UI, ADR-0081 substrate wiring coverage (WiringSentinel + 4-hour cadence). Latest diagnostic-all run (2026-05-19T18:41:29Z) reports **14 PASS / 1 FAIL** — single FAIL is 3 orphan tools in section-15, down from 6 in May-19 morning. Triune-Main (Engineer-Main + Reviewer-Main + Architect-Main) live-verified with real multi-agent delegate chain.

**Discipline pass (B422-B424) in flight:**
- **B422** — script consolidation: 64 → 26 repo-root `.command` files. 38 moved via `git mv` into `dev-tools/{verify-archive,live-tests,commit-bursts}` + `dev-tools/` top-level.
- **B423** (this commit) — STATE.md + KERNEL.md refresh after 162 bursts of drift.
- **B424** — ADR-0082 Kernel Freeze Posture: codify "no new top-level kernel subsystems without external integrator demand."

The body that follows reflects the B258 baseline. Treat the snapshot above as the load-bearing current truth.

---

Last updated: 2026-05-13, post-Burst 258 (ADR-0062 closed — supply-chain hardening complete). Bursts 234-258 layered four overlapping arcs onto the post-B233 baseline. (1) **ADR-0053 Per-Tool Plugin Grants — full arc** (B234-B241): schema v17→v18 with `tool_name` column on `agent_plugin_grants`; accessor + endpoint + dispatcher specificity-wins resolver + frontend interactive per-tool toggle grid (B240). (2) **ADR-0049 Per-event signatures — full arc** (B242-B244): AgentKeyStore wrapper at B242, ed25519 keypair at birth (schema v18→v19 adding `agents.public_key`) at B243, sign-on-emit + verify-on-replay + strict-mode flag + operator runbook at B244. **Audit chain is now tamper-PROOF for agent-emitted events.** (3) **License pivot — ADR-0046 Amendment 1** (B245): Apache 2.0 → Elastic License 2.0 (ELv2); commits ≤ `f799757` remain Apache 2.0 per §4 irrevocability; LICENSE.history documents the cutover; GitHub repo topics updated to `source-available` + `elastic-license-v2`. (4) **Three-ADR closure arc — ADR-0061 + ADR-0062 + ADR-0063 all closed end-to-end** (B246-B258, see [`docs/audits/2026-05-13-three-adr-arc.md`](docs/audits/2026-05-13-three-adr-arc.md)): **ADR-0061 Agent Passport** closed B248 (HTTP `POST /agents/{id}/passport` + `fsf passport mint/show/fingerprint` CLI + minted/refused audit events); **ADR-0062 Supply-Chain Scanner + Install Gate** closed B258 (6/6 tranches: IoC catalog at `config/security_iocs.yaml` with 16 rules drawn from npm Shai-Hulud / PyPI LiteLLM / Axios / Anthropic MCP-STDIO-RCE incident IOCs + `security_scan.v1` builtin + install-time gate on three install endpoints + forge-stage scanner with `REJECTED.md` quarantine marker + SoulUX **Security** tab + `/security/*` router); **ADR-0063 Reality Anchor** closed B256 (7/7 tranches: `config/ground_truth.yaml` 14-fact operator-asserted truth catalog + `verify_claim.v1` builtin + `RealityAnchorStep` in governance pipeline + `reality_anchor` singleton-per-forest role + conversation pre-turn hook + schema v19→v20 `reality_anchor_corrections` table with repeat-offender detection + SoulUX **Reality** tab + `/reality-anchor/*` router). v0.5 ADRs remain canonical: **ADR-0042**, **ADR-0043**, **ADR-0045**. New ADRs since B233: **ADR-0049** (per-event signatures, all 8 tranches B242-B244), **ADR-0053** (per-tool plugin grants, all 6 tranches B234-B241), **ADR-0061** (agent passport, closed B248), **ADR-0062** (supply-chain scanner, closed B258), **ADR-0063** (reality anchor, closed B256). License: **ELv2 from `f799757` onward** (B245). Schema: **v20** (post-B255 reality_anchor_corrections table). HEAD: `cd83e83` (post-B258). Latest tag still **v0.5.0** (2026-05-04); v0.6 not yet tagged — gated on integrator validation per ADR-0044 P7. v0.4.0 shipped 2026-05-04 (ADR-0041). v0.3.0 shipped 2026-05-03 (ADR-0036 + ADR-0040). v0.2.0 shipped 2026-05-02 (Phase G.1.A). v0.1.2 shipped 2026-05-01 (ADR-0027-am + ADR-0021-am + ADR-0038). v0.1.1 shipped 2026-04-30. See [CHANGELOG.md](CHANGELOG.md) and [CREDITS.md](CREDITS.md) for the full attribution + ledger.

---

## TL;DR for the first 60 seconds

Forest Soul Forge is a **local-first agent foundry**. You drag trait sliders → forge produces a content-addressed agent (soul.md narrative + constitution.yaml policy + audit-chain provenance + registry row, all four agreeing on the same hash) → that agent dispatches versioned tools, runs YAML skill manifests, persists memory across sessions, and (in theory) delegates work to other agents in its lineage.

Four big things are true today:

1. **The runtime is real** — 53 builtin tools registered, 26 skill manifests in `examples/skills/` (23 installed for live runs), 9 swarm agents + Atlas/Forge/Sentinel coding triune born live, daemon serving FastAPI on `127.0.0.1:7423`, frontend on `127.0.0.1:5173`. Tool dispatch routes through the R3-extracted `GovernancePipeline` (composable pre-execute steps). MCP plugin protocol (ADR-0043) lets operators install third-party MCP servers under `~/.forest/plugins/`; the dispatcher bridge merges them into the same `mcp_call.v1` registry agents already use.
2. **The cross-agent chain fires end-to-end** — the canonical Security Swarm chain (`LogLurker → AnomalyAce → ResponseRogue → VaultWarden`) was verified live 2026-04-28: 47 audit events, four levels of `delegate.v1` nesting. The SW-track triune followed (2026-04-30): 21-event audit chain proving the foundry can do software work on itself.
3. **Operator + agents talk in real conversations** — ADR-003Y conversation runtime Y1-Y7 all shipped. Multi-room, multi-turn, `@mention` chain passes, cross-domain bridges, opt-in ambient nudges, retention-window summarization, browser Chat tab. Every turn flows through the R3 governance pipeline; every dispatch + bridge + nudge is in the audit chain.
4. **Audit + privacy are the spine** — every state-changing action lands in a hash-chained JSONL. Memory has four scopes (private / lineage / consented / realm) with explicit cross-agent disclosure. Conversation turn bodies have retention windows; `body_hash` (SHA-256) persists for tamper-evidence even after Y7 lazy summarization purges the body. No telemetry, no phone-home.

If you read nothing else, read [`docs/decisions/ADR-0033-security-swarm.md`](docs/decisions/ADR-0033-security-swarm.md) (defensive plane) and [`docs/decisions/ADR-003Y-conversation-runtime.md`](docs/decisions/ADR-003Y-conversation-runtime.md) (interactive plane) — they capture the design discipline the rest of the codebase follows.

---

## The numbers

| | |
|---:|:---|
| Source LoC (Python) | **59,602** across `src/forest_soul_forge/` (was 56,113 at Burst 212; +3,489 across Bursts 213-233 — ADR-0060 runtime catalog grants substrate + posture-matrix governance step, `mcp_call.v1` HTTP transport, marketplace install router). |
| Tests (passing) | **2,800 unit + integration** (sandbox baseline post-B233; was 2,738 at Burst 212, +62 across Bursts 213-233 = B222 ADR-0060 T5 28-test suite + B225 mcp_call HTTP transport 10 tests + B227 marketplace install tests + B221 posture-matrix tests). Plus **conformance suite** at `tests/conformance/` (Burst 130, ADR-0044 P4 — now 7 sections: §1 dispatch / §2 audit chain / §3 plugin manifest / §4 constitution / §5 HTTP API / §6 CLI / §7 schema). Conformance is HTTP-only — external integrators run it against their own Forest-kernel build via `pip install "forest-soul-forge[conformance]" && pytest tests/conformance/`. |
| ADRs filed | **57 files / 55 unique numbers** (`ADR-0001` → `ADR-0060`, with gaps 0009-0015; ADR-003X open-web + ADR-003Y conversation runtime — Y1-Y7 all shipped; ADR-0021-am + ADR-0027-am amendments + ADR-0038 all Accepted in v0.1.2). v0.5/v0.6 active ADRs: **ADR-0042 v0.5 Product Direction** (T5 signing/auto-updater gated on Apple Developer decision); **ADR-0043 MCP-First Plugin Protocol** (T1-T5 + follow-ups #1/#2/#3 shipped; #4 plugin_secret_set deferred — addressed by ADR-0052); **ADR-0044 Kernel Positioning + SoulUX**; **ADR-0045 Agent Posture / Trust-Light System**; **ADR-0046 License + Governance** (Accepted 2026-05-05; **Amended 2026-05-12 B245** — license switched Apache 2.0 → Elastic License 2.0 / ELv2; commits through `f799757`/B244 remain irrevocably Apache 2.0, B245 onward is ELv2); **ADR-0047 Persistent Assistant Chat** (6/6 tranches shipped Bursts 135-158; T4 per-tool granularity now fully shipped via ADR-0053 substrate, B240); **ADR-0048 Computer-Control Allowance** (T1-T6 shipped; T4 per-tool granularity fully shipped via ADR-0053 substrate, B240); **ADR-0049 Per-event signatures** (Accepted 2026-05-12, all 8 tranches shipped Bursts 242-244 — audit chain is now tamper-PROOF for agent-emitted events; ed25519 sign-on-emit + verify-on-replay + strict-mode flag + operator runbook); **ADR-0050/0051 Encryption-at-rest + Per-tool subprocess sandbox** (drafted, no implementation — remainder of the Phase 4 security-hardening runway); **ADR-0052 Pluggable Secrets Storage** (Proposed, closes ADR-0043 follow-up #4) + **ADR-0053 Per-Tool Plugin Grants** (Accepted 2026-05-12, all 6 tranches shipped Bursts 235-241); **ADR-0054 Procedural-Shortcut Dispatch** (T1-T6 + T5b shipped Bursts 161-195 + T6 docs runbook B217 — substrate DEFAULT OFF until operator opts in via `procedural_shortcut_enabled`; T6 chat-tab review card UI half still queued); **ADR-0055 Agentic Marketplace** (expanded 2026-05-11 with D8-D11/M7-M10; Phase A shipped B227-B229 per `docs/roadmap/2026-05-11-marketplace-roadmap.md`; Phases B/C/D queued); **ADR-0056 Experimenter Agent** (E1-E7 shipped Bursts 188-197); **ADR-0057 Skill Forge UI** (operator-usable end-to-end post-B208); **ADR-0058 Tool Forge UI — prompt-template path** (operator-usable end-to-end post-B210); **ADR-0059 Catalog-Aware Propose** (forge prompts inject the live tool catalog so the LLM can't hallucinate tool names); **ADR-0060 Runtime Tool Grants** (Accepted 2026-05-11, T1-T6 all shipped Bursts 219-223). |
| Builtin tools registered | **54** (catalog and `tools/builtin/` source files in sync — was 53 at Burst 124; +1 across Bursts 125-199). Plus **plugin-contributed MCP servers** at runtime via ADR-0043 T4.5 dispatcher bridge (loaded from `~/.forest/plugins/`; not counted in catalog total). |
| Skill manifests | **36 shipped** in `examples/skills/` — 26 canonical authored set + 10 marketplace seed skills landed B232+B233 (morning_briefing, code_review_quick, meeting_followup, bug_report_polish, commit_changelog, agent_activity_digest, memory_consolidate, incident_first_pass, release_notes, agent_introspect). All 10 marketplace seed skills validated via `parse_manifest` with full ref-scope coverage. Skills loaded at runtime from examples/ via the catalog regardless of install state. |
| Marketplace seed tools | **10 shipped** in `examples/tools/` (new directory, B230+B231) — prompt-template-tool.v1 instances: text_summarize, code_explain, commit_message, regex_explain, email_draft, tone_shift, slug_generate, sql_explain, action_items_extract, sentiment_analyze. All 10 validated via `parse_spec`. Together with the 10 marketplace seed skills, this is the **20-item marketplace seed catalog** the `forest-marketplace` sibling repo (when scaffolded) will index as its first entries. Activation per-tool: `cp examples/tools/*.yaml data/forge/tools/installed/` + daemon restart; per-skill analogous under `data/forge/skills/installed/`. |
| Plugin examples | **3 canonical** in `examples/plugins/` (forest-echo / brave-search / filesystem-reference) covering the read_only / network / filesystem governance posture spectrum. Plus README.md (manifest format reference) + CONTRIBUTING.md (registry submission flow). |
| Schema version | **v20** (v8: agent_secrets / v9: memory_verifications / v10: conversations / v11: epistemic memory; v12: flagged_state column on memory_contradictions for ADR-0036 T6; v13: scheduled_task_state for ADR-0041 T5; v14: agent_plugin_grants table for ADR-0043 follow-up #2 / B113a; v15: agents.posture column for ADR-0045 T1 / B114; v16: reserved; v17: agent_catalog_grants table for ADR-0060 T1 / B219; **v18: agent_plugin_grants.tool_name column for ADR-0053 T1 / B235** — per-tool plugin grants; **v19: agents.public_key column for ADR-0049 T4 / B243** — ed25519 per-agent keypair; **v20: reality_anchor_corrections table for ADR-0063 T6 / B255** — claim_hash PRIMARY KEY + repetition_count, sibling to memory_contradictions but for pre-action ground-truth verification). |
| Genres | 13 (7 original + 3 security tiers + 3 web tiers); each genre now carries `max_initiative_level` + `default_initiative_level` per ADR-0021-am §3. Burst 124 closed the genre-dropdown bug where most genres' role lists pointed at undefined roles. |
| Tools with initiative annotations | **2 in catalog YAML** (`pip_install_isolated.v1` L4 from v0.2.0, `memory_flag_contradiction.v1` L3 from v0.3) + **23 builtin source files** mention initiative inline. The catalog is the configuration of record per ADR-0018 — most annotations didn't propagate from source. Reconciliation still queued. |
| Trait roles | **44** (5 original + 9 swarm + 3 SW-track + 1 verifier_loop + 24 Burst 124 expansion + 2 added in Bursts 125-199: ADR-0047 `assistant`, ADR-0056 `experimenter`). Unchanged across Bursts 200-212. |
| Alive agents in registry | **23** (post-B214 swarm re-acceptance; unchanged through B233 — no births/archives since). Non-swarm 14: assistant, dashboard_watcher, experimenter, incident_correlator, knowledge_consolidator, operator_companion, paper_summarizer, signal_listener, software_engineer (×2), status_reporter, system_architect, translator (B210 sandbox), vendor_research. **Plus all 9 blue-team agents alive**: patch_patrol, gatekeeper, log_lurker, anomaly_ace, net_ninja, response_rogue, zero_zero, vault_warden, deception_duke. ADR-0033 Security Swarm re-acceptance evidence at seqs 7593-7643 with 3 `agent_delegated` hops at 7612/7621/7630. **B216 closed the 24/7 ops gap**: launchd LaunchAgents installed for daemon + scheduler so the swarm survives logout/reboot; the swarm now runs continuously between operator-scheduled patrols. |
| Installed skills | **26** post-B214 (was 2 post-B212; +24 from the canonical ADR-0033 swarm skill set reloaded during the re-acceptance smoke — `morning_sweep`, `investigate_finding`, `contain_incident`, `key_audit`, `daily_patch_sweep`, `device_inventory`, etc.). Plus the 2 from the natural-language Forge UI arc: `smoke_blurb.v1`, `summarize_audit_chain.v1`. Marketplace seed skills (B232-B233) live in `examples/skills/` but are NOT installed by default — operators opt in. |
| Installed forged tools | **1** (`translate_to_french.v1`) — live-dispatched successfully at seq 7554 producing "Bonjour, comment allez-vous aujourd'hui ?" Marketplace seed tools (B230-B231) live in `examples/tools/` but are NOT installed by default — operators opt in. |
| Audit event types | **76** in `KNOWN_EVENT_TYPES` post-B233 (was 73 post-B212; +3 across Bursts 213-233 for ADR-0060 `agent_tool_granted` + `agent_tool_revoked` and marketplace `marketplace_plugin_installed`). |
| Frontend modules (vanilla JS) — **SoulUX-distribution metric, not kernel** | **26** (unchanged Bursts 213-233 — `catalog-grants.js` and `marketplace.js` were added B223 + B228 but replaced/consolidated existing module slots). Per ADR-0044 the frontend is part of the SoulUX flagship distribution; the kernel runs without it. See `docs/runbooks/headless-install.md`. |
| `.command` operator scripts | **68 at repo root** (was 59 at Burst 212; +9 across Bursts 213-233 for swarm re-acceptance + launchd install + marketplace smoke + uninstall helpers) plus **204 archived** under `dev-tools/commit-bursts/` (was 172 at Burst 212; +32 per-burst commit scripts B202-B233) plus **16 in `dev-tools/`** (drift / lock-clearing / rebuild helpers). Convention since Burst 128: one-shot commit scripts land in the archive folder; reusable helpers live in `dev-tools/`. |
| Demo scenarios | 2 (synthetic-incident + fresh-forge, both with presenter scripts) |
| Data dirs | 2 (top-level prod via start.command + isolated demo/ via start-demo.command). Plus `~/.forest/plugins/` operator-managed plugin root (separate from repo per ADR-0043 §plugin root layout). |
| Distribution | `dist/build.command` produces `forest-soul-forge-<sha>-<date>.zip` via git archive. ADR-0042 T4 adds **`dist/build-daemon-binary.command`** (PyInstaller single-file binary) and `apps/desktop/` Tauri 2.x shell that bundles the binary as a sidecar. Tauri signing + auto-updater (T5) gated on Apple Developer account decision. |
| Total commits on `main` | **389** post-B233 (was 369 post-B212; +20 across Bursts 213-233 — swarm re-acceptance + launchd + ADR-0060 6-tranche arc + marketplace expansion + Phase A + 20-item seed catalog). |
| Audit docs filed | **15** (most recent: `docs/audits/2026-05-08-chain-fork-incident.md` — B199 forensic record of 6 historical chain forks at seqs 3728/3735-3738/3740 + the 3-layer fix that closed the race going forward). |
| Live audit chain path | **`examples/audit_chain.jsonl`** (per `daemon/config.py` `audit_chain_path` default — NOT `data/audit_chain.jsonl` which is the dev fixture). Override via `FSF_AUDIT_CHAIN_PATH`. **~8,870 entries** on 2026-05-12 (was ~7,581 on 2026-05-11; +1,289 across one day — dominated by `dashboard_watcher`'s scheduled polling now that launchd keeps it running 24/7 post-B216, plus B225 HTTP mcp_call smoke, B227 marketplace install events, B219-B223 ADR-0060 grant lifecycle events). |
| Drift sentinel | `dev-tools/check-drift.sh` — runs every numeric claim against disk reality. Run before any release tag. |

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
│   │   │   ├── writes/            # /birth, /spawn, /regenerate-voice, /archive
│   │   │   │                      # (ADR-0040 T3 — per-endpoint sub-routers:
│   │   │   │                      #  birth.py, voice.py, archive.py + _shared.py)
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

Genres (post-Burst-124):

| Genre | Risk floor | Memory ceiling | Roles |
|---|---|---|---|
| observer | read_only | lineage | network_watcher, log_analyst, **dashboard_watcher**, **signal_listener** |
| investigator | network | lineage | anomaly_investigator, **incident_correlator**, **threat_hunter** |
| communicator | network | consented | incident_communicator, **briefer**, **notifier**, **status_reporter**, **translator** |
| actuator | external | lineage | **alert_dispatcher**, **deploy_runner**, **ticket_creator** |
| guardian | read_only | private | **content_review**, **refusal_arbiter**, **safety_check** |
| researcher | network | consented | **knowledge_consolidator**, **paper_summarizer**, **vendor_research** |
| companion | network + local-only | private | operator_companion, **accessibility_runtime**, **day_companion**, **learning_partner**, **journaling_partner** |
| **security_low** | read_only | lineage | patch_patrol, gatekeeper, log_lurker |
| **security_mid** | external | lineage | anomaly_ace, net_ninja, response_rogue |
| **security_high** | external + local-only | private | zero_zero, vault_warden, deception_duke |
| **web_observer** | read_only (allowlisted hosts) | lineage | **web_watcher** |
| **web_researcher** | network (allowlisted hosts) | consented | **web_researcher** |
| **web_actuator** | external (allowlisted hosts + per-action approval) | lineage | **web_actuator** |

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

### ⚠ Items in the queue (ranked by leverage, post-Burst-124)

| Item | Status / blocker | Effort |
|---|---|---|
| **ADR-0044 P2 — formal kernel API spec** | ✅ **Shipped Burst 127** — `docs/spec/kernel-api-v0.6.md`, 1,042 lines, all 7 ABI surfaces specified. |
| **ADR-0044 P3 — headless + SoulUX split** | ✅ **Shipped Burst 129** — `docs/runbooks/headless-install.md` + `scripts/headless-smoke.sh` + kernel-first docstrings. Kernel runs without SoulUX. |
| **ADR-0044 P4 — conformance test suite** | ✅ **Shipped Bursts 130 + 132** — `tests/conformance/` HTTP-only suite, JSONSchema fixtures, idempotency probe, markdown report generator. |
| **Housekeeping bundle (Burst 126)** | ✅ **Shipped Burst 126** — verifier_loop archetype backfilled, Phase G ownership clarified, audit chain synced. |
| **.command scripts archival** | ✅ **Shipped Burst 128** — 100 commit-* + tag-* scripts moved to `dev-tools/commit-bursts/`. |
| **JSONSchema input defaults at runtime in the skill engine** | ✅ **Shipped Burst 133** — `_apply_schema_defaults` helper in `skill_runtime.py` + 11 unit tests. Operator-supplied values always win; defaults fill omitted keys. |
| **Frontend test scaffold** | ✅ **Shipped Burst 133** — Vitest + jsdom scaffold at `frontend/`. `npm test` runs sanity + api seed tests; future PRs add tests alongside UI changes. |
| Integration tests | 1 file (forge loop). Need 3–5 covering dispatcher + memory + delegate, tool_dispatch with approval queue resume, skill_run multi-tool composition. | ~1 day |
| **ADR-0044 P6 outreach materials** | ✅ **Shipped Burst 131** — `docs/integrator-pitch.md` + `docs/integrator-quickstart.md`. Actual integrator validation (cold-emailing) is months not bursts; pitch is the asset. |
| ADR-0042 T5 — Tauri code-signing + auto-updater | Gated on Apple Developer account decision. | gated |
| ADR-0043 #4 — `plugin_secret_set` audit event | Deferred pending secrets-storage decision. | small once unblocked |
| ADR-0036 cross-agent contradiction scan | Deferred to v0.4 per ADR-0036 trade-offs. | medium |
| ADR-0038 T4-T6 telemetry/disclosure_intent_check/external_support_redirect | Deferred to v0.3 per ADR-0038 status. | medium |
| `mfa_check.v1` | Deferred — operator hasn't scoped "MFA posture" target. | unknown |
| Pytest version of the smoke (E2) | Shell script suffices; pytest fixture would let CI gate on the chain. | ~1 day |
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

1. **ADR-0044 P2 — formal kernel API spec.** The next major milestone for the v0.6 kernel arc. The 42-role inventory + KERNEL.md + boundary doc that landed in Bursts 118-124 give the spec a stable surface to write against. Output: `docs/architecture/kernel-api-v0.6.md` pinning every stable interface with version numbers, error envelopes, and ABI compatibility commitments.
2. **Add 3–5 cross-subsystem integration tests.** Currently 1 file. Highest value: dispatcher + memory + delegate, tool_dispatch with approval-queue resume, skill_run with multi-tool composition. ~1 day.
3. **Frontend test scaffold** (Vitest + jsdom). 3,500 LoC JS, 0 tests. ~half day for the scaffold + 2-3 example tests; future PRs add tests alongside UI changes.
4. **JSONSchema input defaults at runtime** in the skill engine — small surface change, lets manifests rely on declared defaults instead of hard-coding values inline.
5. **Burst 126 housekeeping bundle.** audit_chain.jsonl sync, verifier_loop archetype backfill, KERNEL.md cross-references, Phase G zombie comment ownership clarification post-ADR-0044.

If you want to read code first, start with:

1. [`KERNEL.md`](KERNEL.md) — root-level kernel/userspace ABI summary (Burst 119, ADR-0044 P1.2)
2. [`docs/architecture/kernel-userspace-boundary.md`](docs/architecture/kernel-userspace-boundary.md) — full boundary doc (Burst 118)
3. [`docs/decisions/ADR-0044-kernel-positioning-soulux.md`](docs/decisions/ADR-0044-kernel-positioning-soulux.md) — the v0.6 strategic posture
4. [`docs/decisions/ADR-0033-security-swarm.md`](docs/decisions/ADR-0033-security-swarm.md) — the design discipline
5. `src/forest_soul_forge/tools/dispatcher.py` — the runtime
6. `src/forest_soul_forge/forge/skill_manifest.py` + `skill_runtime.py` — the skill engine
7. `src/forest_soul_forge/core/audit_chain.py` — the privacy spine
8. `src/forest_soul_forge/daemon/app.py` — the lifespan + app.state wiring

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
| 003X | Open-Web Tool Family (web_fetch + browser_action + mcp_call + secrets store + suggest_agent + 3 web genres + C8 demo) | C1 (secrets), C2 (web_fetch), C3 (browser_action), C4 (mcp_call), C6 (suggest_agent), C7 (3 web genres), C8 (open-web demo via local HTTP + 2 skills + ceremony emit) all shipped 2026-04-29 — only C5 (Sigstore provenance) deferred |
| 003X K | K-track parallels (memory verification, ceremony events, SSE stream, triune spawn, chronicle export, hardware binding) | K1 (memory_verify), K2 (ceremony.v1), K3 (/audit/stream), K4 (triune bond + Heartwood/Branch/Leaf seeds + delegate.v1 enforcement), K5 (fsf chronicle CLI + per-agent/per-bond/full-chain HTML+MD export with sanitized-by-default payloads), K6 (opt-in hardware_binding constitution field + dispatcher quarantine + /agents/{id}/hardware/unbind operator endpoint) all shipped 2026-04-29 |
| 0034 | SW-track triune (Atlas / Forge / Sentinel) | **Accepted** — born live 2026-04-30, 21-event audit chain |
| 0035 | Persona Forge | Proposed (v0.3 candidate) |
| 0036 | Verifier Loop | **Proposed (T1+T2+T3a+T3b+T5+T6+T7 implemented in v0.3; T4 scheduled-task substrate now closed by ADR-0041 T3 in v0.4-rc — register a `tool_call` task with `verifier_scan` as the tool name)** |
| 0037 | Observability dashboard | Proposed (v0.3 candidate) |
| 0038 | Companion harm model | **Accepted** (v0.1.2 — credit: SarahR1) |
| 0039 | Distillation Forge / Swarm Orchestrator | Proposed (v0.4 candidate) |
| 0040 | Trust-surface decomposition rule | **Accepted** — T1 (file ADR), T2 (memory.py 5-mixin decomposition, Bursts 72-76), T3 (writes.py 4-sub-router decomposition, Bursts 77-80), T4 (this STATE.md / CLAUDE.md cross-references, Burst 81) all shipped 2026-05-02 |
| 0041 | Set-and-Forget Orchestrator | **Accepted** — all 5 implementation tranches shipped: T1 design (Burst 85), T2 runtime + lifespan (Burst 86), T3 tool_call task type + audit emit (Burst 89), T4 scenario task type runtime (Burst 93), T5 SQLite v13 persistence (Burst 90), T6 operator control endpoints — trigger / enable / disable / reset (Burst 91). FizzBuzz YAML scenario port (Burst 94, closes Burst 81 P1) replaces the bash live-test driver as the canonical autonomous coding-loop scenario. v0.4.0-rc tagged 2026-05-04 with the tool_call-only checkpoint; v0.4.0 supersedes it 2026-05-04 once T4 + the FizzBuzz port landed. |
| 0042 | v0.5 Product Direction (Tauri desktop shell + PWA-first frontend) | **Accepted** — T1 (PyInstaller daemon binary) + T2 (responsive frontend pass) + T3.1 (Tauri shell + sidecar bundling) + T4 (build pipeline) shipped; **T5 code-signing + auto-updater gated on Apple Developer account decision**. |
| 0043 | MCP-First Plugin Protocol | **Accepted** — T1 (manifest schema) + T2 (loader) + T3 (governance gates) + T4 (dispatcher bridge) + T5 (3 example plugins covering read_only / network / filesystem postures) shipped Bursts 95-108. Follow-ups: #1 per-tool approval mirroring (Burst 111), #2 frontend Tools-tab plugin awareness (Burst 112), #3 plugin grants substrate + operator surface (Bursts 113a/113b — schema v14 + post-birth grant ergonomics). **#4 plugin_secret_set audit event deferred** pending secrets-storage decision. |
| 0044 | Kernel Positioning + SoulUX Flagship Branding | **Accepted** (Burst 117). Repositions Forest as agent-governance kernel; SoulUX = opinionated default distribution. P1 (kernel/userspace boundary doc + KERNEL.md + dev-tools sentinel) shipped Bursts 118-120. P5 (license + governance via ADR-0046) + P5.1 (CONTRIBUTING + CoC) shipped Bursts 121-122. **P2 formal kernel API spec next.** P3 headless + SoulUX split / P4 conformance test suite / P6 first external integrator / P7 v1.0 stability commitment all queued. |
| 0045 | Agent Posture / Trust-Light System | **Accepted** — T1 (schema v15 agents.posture column + green/yellow/red CHECK + idx_agents_posture) + T2 (HTTP/CLI operator surface + agent_posture_changed audit event) + T3+T4 (PostureGateStep at end of governance pipeline with full red-dominates per-grant precedence matrix) implementation-complete Bursts 114-115. |
| 0046 | License Posture + Governance | **Accepted** (Burst 121, ADR-0044 Phase 5). Originally locked Apache 2.0. **Amended 2026-05-12 (B245):** license switched to Elastic License 2.0 (ELv2). Commits through `f799757` (B244) remain irrevocably Apache 2.0 per Apache §4; B245 onward is ELv2. Three restrictions: no competing managed service, no key-circumvention, no notice removal. Bus-factor + maintainer continuity governance unchanged. See `LICENSE.history` + ADR-0046 Amendment 1 for the cutover detail. |

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

**Elastic License 2.0 (ELv2)** as of B245 (2026-05-12). Source-available with three restrictions: no competing managed service, no license-key circumvention, no notice removal. Commits through `f799757` (B244) remain irrevocably Apache 2.0 per Apache §4 — operators who pulled before B245 retain their Apache rights for those versions. See `LICENSE`, `LICENSE.history`, and ADR-0046 Amendment 1 for full context.

No telemetry. No phone-home. No data collection. The agents and their souls live entirely on your hardware. The audit chain stays on your disk. The license change doesn't touch any of that — it controls who can commercially redistribute Forest as a managed service, not what Forest does to operator data.

The mission is two co-equal pillars: **protect the user and their data**, and **understand the user**. An agent that does the first without the second is a guard dog. An agent that does the second without the first is a salesman. Forest Soul Forge agents do both, or they don't ship.
