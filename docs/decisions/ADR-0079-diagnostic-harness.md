# ADR-0079 — Diagnostic harness

**Status:** Proposed
**Date:** 2026-05-17
**Tracks:** Observability / Substrate hygiene
**Supersedes:** none
**Builds on:** ADR-0040 (trust-surface decomposition), ADR-0049
(per-event signatures), ADR-0073 (audit chain segmentation), every
substrate ADR from Phase α
**Unblocks:** ADR-0064 T3+ (telemetry chain hookup defers behind
this), every subsequent rollout's confidence floor

## Context

B350 (2026-05-17) shipped a one-line dispatcher wire to pass
`audit_chain` into `ToolContext`. The substantive finding wasn't
the missing line — it was that **`audit_chain_verify.v1` had been
declared a working tool since ADR-0033 Phase B1 but was actually
dead code on the HTTP path**. The only thing that ever exercised
it successfully was `tests/unit/test_b1_tools.py:58`, which
constructs `ToolContext` by hand and populates
`constraints["audit_chain"]` directly. The dispatcher populated
neither key. Every HTTP-path invocation raised
`ToolValidationError`.

The first real consumer (`archive_evidence.v1`'s
`verify_chain_integrity` step during D3 Phase A live verification)
surfaced the bug. The fix was a single line. The cost would have
been much larger if discovered later: telemetry_steward (D3 Phase
B) calls audit_chain_verify too, as does any future skill that
needs chain integrity.

**The B350 fix is single. The B350-class concern is structural.**
The dispatcher exposes nine subsystems via `ToolContext` fields:
`memory`, `delegate`, `priv_client`, `secrets`, `agent_registry`,
`procedural_shortcuts`, `personal_index`, `provider`, and now
`audit_chain`. Each is wired in `dispatcher.py:999` (the
`ToolContext(...)` constructor call). A typo or a missed wire on
any of them silently kills whatever tools depend on that
subsystem. Unit tests don't catch this — they construct
`ToolContext` directly and bypass the dispatcher.

Beyond ToolContext wiring, the same pattern of "claimed working,
actually dead" can hit:

- **Tool registration** — a tool entry in `tool_catalog.yaml`
  whose Python class isn't registered (silent miss).
- **Skill manifests** — an installed skill whose `${...}`
  reference resolves to a non-existent step output (caught by
  the parser at install time, but only if install was tested).
- **Handoff routing** — a `(domain, capability)` mapping that
  resolves cleanly but points at a role no living agent
  implements.
- **Cross-domain orchestration** — decompose_intent +
  route_to_domain claim to chain via `delegate.v1`, but the
  audit chain shows the cascade events the delegation should
  emit weren't being emitted (hypothetical until verified).
- **Encryption-at-rest** — a constitution file is encrypted at
  rest but the decrypt path silently swallows errors and returns
  empty constraints (hypothetical until verified).
- **Frontend tabs** — Marketplace boot-race (B298) caught one
  instance; others are plausible (Provenance pane, Orchestrator
  pane, Skill Forge).

A **section-by-section diagnostic harness** runs every load-
bearing surface end-to-end against a live daemon, captures
pass/fail per surface, surfaces specific evidence on failure. The
harness is the verification substrate the project has been
implicitly relying on `pytest` for — but `pytest` covers the
unit-test surface, not the runtime-wiring surface.

## Decision

**Decision 1 — Sectional structure.**

Thirteen sections. Each section is one `section-NN-<name>.command`
driver in `dev-tools/diagnostic/`. Each writes a structured
`report.md` to `data/test-runs/diagnostic-NN-<name>/` containing:

- header: section name + timestamp + git SHA + daemon /healthz
  snapshot
- per-check: `[PASS]` or `[FAIL]` + one-line summary + (on
  fail) the exact evidence (response body, log excerpt, traceback)
- footer: total checks, pass count, fail count, abort reason
  if the section aborted early

The sections are sequenced because later ones depend on earlier
ones being green; a failure in section 1 (static config) means
nothing downstream can be trusted.

| # | Section | Catches |
|---|---|---|
| 01 | static-config | trait_tree / genres / constitution_templates / tool_catalog / handoffs / domain manifests parse + cross-reference; no orphan role; no genre kit-tier ceiling violation |
| 02 | skill-manifests | every `examples/skills/*.v1.yaml` + every `data/forge/skills/installed/*.v1.yaml` parses through `parse_manifest`; every `${step.out.field}` reference resolves; every `requires:` tool is in the catalog |
| 03 | boot-health | `/healthz` startup_diagnostics all-green; every subsystem reports `status: ok`; daemon HEAD commit SHA matches local HEAD |
| 04 | tool-registration | every tool in `tool_catalog.yaml` actually registers in `/tools/registered`; counts match; no version drift |
| 05 | agent-inventory | every alive agent's constitution loads, references only valid tools, respects its genre's kit-tier ceiling. Replays the B336 narrow-kit failure mode for the whole registry |
| 06 | ctx-wiring | for each of the 9 subsystems the dispatcher claims to wire (memory, delegate, priv_client, secrets, agent_registry, procedural_shortcuts, personal_index, provider, audit_chain), confirm it actually surfaces in `ctx` during a real dispatch via a probe tool. **This is the B350 failure mode generalized — section that would have caught the bug we shipped this morning.** |
| 07 | skill-smoke | minimal dispatch per installed skill that exercises every step; pass if `status=succeeded`, fail loud otherwise. Catches the B350-class for every (skill × tool dependency) combo |
| 08 | audit-chain-forensics | `audit_chain_verify.v1` end-to-end on the live chain; signature coverage spot-check; segment sealing verifier per sealed segment; `body_hash` integrity post-Y7 summarization |
| 09 | handoff-routing | every `(domain, capability)` mapping in handoffs.yaml resolves OR is correctly tagged `domain_planned`; every entry_agent in every domain manifest exists in trait_engine + is claimed by some genre |
| 10 | cross-domain-orchestration | decompose_intent + route_to_domain happy path through the orchestrator singleton; cascade rules fire when targets are live, return `cascade_refused: domain_planned` when not; provenance events captured in the chain |
| 11 | memory-retention | each memory scope writeable per-genre + readable per-lineage; consolidation runner runs cleanly; retention sweep deletes eligible rows + counts |
| 12 | encryption-at-rest | each encrypted file path (registry, audit chain segments, soul/constitution files, telemetry store) decrypts cleanly; round-trip via passphrase + keychain backends both work; no swallowed-error paths |
| 13 | frontend-integration | each tab loads, fetches live data, no boot races (B276/B298-style); each operator-action surface (Pending approvals, Skills install, Connector consent, Provenance pane) works against a live daemon |

**Decision 2 — Sequencing rule.**

Sections run in numeric order. Each section's driver reads the
previous section's `report.md` and aborts with a clear "skipping
section NN because section MM failed" if a load-bearing prior
section red'd. This prevents cascading false positives. The
umbrella runner records which sections were skipped so the
operator can see the dependency chain.

**Decision 3 — Failure handling: fail loud, never abort the
umbrella.**

Individual section drivers abort on first fail within that
section (no point continuing skill-smoke if check #2 already
revealed that the skill loader is broken). The umbrella does
NOT abort — it runs every section regardless and surfaces ALL
failures in the final summary. This is the right shape because
the operator wants the full punch list, not the first failure.

**Decision 4 — Frequency.**

Manual, operator-driven. Recommended cadence:

- Before closing any Phase rollout (D4-advanced, D3 Phase A,
  D3 Phase B, ...). Replaces the live-test-<phase>.command shape
  with a fuller verification.
- Before any release tag (release_gatekeeper's `release_check.v1`
  skill can dispatch the harness as a dependency).
- On-demand when the operator is suspicious that something broke.

NOT in CI. The harness exercises the live daemon; CI doesn't have
a live daemon. CI continues to run `pytest`. The harness covers
the runtime-wiring surface `pytest` can't reach.

**Decision 5 — Report aggregation.**

The umbrella runner `diagnostic-all.command` writes
`data/test-runs/diagnostic-all-<timestamp>/summary.md` with:

- per-section pass/fail/skipped + count of checks
- consolidated list of failures across all sections (the punch
  list)
- daemon HEAD commit SHA + git status of the working tree
- duration per section + total
- links to the per-section reports

The format is markdown so the operator can paste into a commit
message, Slack thread, or ADR follow-up directly.

**Decision 6 — Section-as-script, not section-as-library.**

Each section is a standalone `.command` script. Pros: operator
can run any section individually for focused investigation; no
import-tangle if a section's helper code regresses; same shape
as the existing `live-test-*.command` pattern. Cons: shared
helpers (auth_header, log, die, JSON-parse) duplicated per
section. Acceptable trade — the helpers are 20 lines each, easier
to maintain inline than abstract into a sourced library.

## Consequences

**Positive:**

- Catches the B350 failure mode for every subsystem
  simultaneously, not one-bug-at-a-time as consumers surface
  them.
- Replaces the per-rollout `live-test-<phase>.command` proliferation
  with one harness that exercises the live system more fully.
- Operator gets a single command (`diagnostic-all.command`) that
  produces a markdown punch list. Easier to triage than 13
  separate terminal runs.
- The wiring discipline check (section 06) becomes part of the
  development reflex: "did I add a new ToolContext field? Add a
  probe to section 06."

**Negative:**

- ~6 bursts to build the harness. Displaces ADR-0064 T3
  (telemetry chain hookup) and every other in-flight Phase α
  closure → Phase β work by that much.
- Harness itself is maintenance surface. Every new dispatcher
  field, new skill, new tool, new domain adds one entry to the
  relevant section's check list. Without this discipline the
  harness drifts and silently passes things that should fail.
- The frontend section (13) is the trickiest because it needs
  to drive a browser (likely via existing Chrome MCP or Playwright
  if introduced). MVP can stub by hitting the API endpoints the
  tabs would hit; full browser-driven check is T2+ scope for
  section 13.

**Open questions:**

- Should sections capture timing data? Useful for spotting
  performance regressions (e.g., section 04 takes 30s when it
  used to take 2s = some tool's `register()` is hung). Defer to
  T6 — record durations in the report, alert in a future tranche
  if some sections exceed a budget.
- Should the harness assert "no warnings in `/healthz` startup_
  diagnostics" or just "no FAILs"? Default: just FAILs.
  Operators can tune later.
- Should the harness self-bootstrap (spin up a fresh daemon for
  its own use) or assume a daemon is running? Default: assume
  running. The operator's preflight should confirm
  `/healthz` is reachable before the harness fires; if it isn't,
  the harness aborts with a clear "start the daemon first" rather
  than try to launch one (the daemon-launch surface is its own
  subsystem we don't want this harness duplicating).

## Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | This ADR (B351) | Decision doc | 1 burst |
| T2 | Sections 01-04 | static-config + skill-manifests + boot-health + tool-registration | 1 burst (long) |
| T3 | Sections 05-07 | agent-inventory + ctx-wiring + skill-smoke (the B350-class catch zone) | 1 burst (long) |
| T4 | Sections 08-10 | audit-chain-forensics + handoff-routing + cross-domain-orchestration | 1 burst (long) |
| T5 | Sections 11-13 | memory-retention + encryption-at-rest + frontend-integration | 1 burst (long) |
| T6 | Umbrella + runbook | `diagnostic-all.command` + operator runbook + sample summary report. CLOSES the harness. | 1 burst |

Total: ~6 bursts. After T6 lands, the original ADR-0064 T3
(telemetry chain hookup) becomes the next major direction with
the substrate's actual health known.

## See Also

- ADR-0040 — trust-surface decomposition (the structural pattern
  this harness validates per section)
- ADR-0049 — per-event signatures
- ADR-0073 — audit chain segmentation
- B350 — fix(dispatcher): wire audit_chain into ToolContext
  (the surfacing commit that motivated this ADR)
- `live-test-fizzbuzz.command` — canonical autonomous-loop
  driver pattern this harness's section drivers mirror
- `dev-tools/check-drift.sh` — existing numeric-drift sentinel
  (complementary; covers the audit chain dimension only)
