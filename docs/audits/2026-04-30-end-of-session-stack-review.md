# 2026-04-30 — end-of-session stack review

Filed at v0.1.0 release. Compares the codebase to its state at the
2026-04-30 morning load-bearing survey, identifies what closed in
this session, what remains as load-bearing tech debt, and where the
roadmap sits against ADR-0024 horizons.

---

## TL;DR — what changed today

```
                          this morning   →   end of session
  Python LoC                  ~44,000          ~36,400 (post-R refactors split god-objects)
  ADRs                            26               29 (+ 0034 + 003X/003Y drafts)
  Builtin tools                   36               40 (+ code_read / code_edit / shell_exec / llm_think)
  Frontend JS modules             18               22 (+ chat.js + cleanup)
  Audit event types               36+              54 (+ Y-track + ambient + summarized + chain_depth)
  Trait roles                     14               17 (+ system_architect / software_engineer / code_reviewer)
  Schema version                   v9              v10 (+ conversations / participants / turns)
  .command operator scripts       19               36
  Commits today                    0                9
```

**Headline:** ADR-003Y conversation runtime went from "Proposed
draft" to "all 7 phases shipped end-to-end" in one session.
SW-track went from "informal commits" to "formal ADR-0034 + first
real triune task with audit-chain proof." R3 governance pipeline
extracted from `dispatcher.py` per the morning survey's
recommendation #3, unblocking ADR-003Y Y3's per-conversation
rate-limit step (now a 1-line append).

---

## God-object status

| File | Morning size | End size | Status |
|---|---:|---:|---|
| `daemon/schemas.py` | 1,139 LoC | (now a package) | **CLOSED** — R1 split into 13-file `schemas/` package + `conversations.py` (Y1) |
| `tools/dispatcher.py` | 1,108 LoC | 1,279 LoC | **CLOSED via R3** — gained 171 LoC of new orchestrator imports + post_init wiring + Y-track plumbing, but the 8 inline pre-execute checks live in `governance_pipeline.py` (NEW, 533 LoC). dispatch() body went from a 250-line if/elif to a 60-line orchestrator. |
| `registry/registry.py` | 1,192 LoC | 462 LoC | **CLOSED via R4** — 970-line monolith → 462-line façade + 7 per-table accessor files. |
| `daemon/routers/writes.py` | 1,215 LoC | 1,186 LoC | **PARTIAL via R2** — `_perform_create()` collapsed birth/spawn duplication; full extraction to `birth_pipeline.py` module deferred. |

**The morning survey's 4 god-object findings are 3-of-4 closed and
1-of-4 partial.** R2's remaining work (extract birth_pipeline.py
module) is the only structural debt carried forward to v0.2.

---

## ADR closure

| ADR | Morning status | End status | Phase |
|---|---|---|---|
| ADR-0033 Security Swarm | Accepted (chain proven 2026-04-28) | Accepted | unchanged |
| ADR-003X Open-Web | Mostly shipped (C1-C8 except C5) | Mostly shipped | unchanged — C5 Sigstore still deferred |
| **ADR-003Y Conversation Runtime** | **Proposed draft only** | **All 7 phases shipped + smoke-tested** | Y1+Y2+Y3+Y4+Y5+Y6+Y7 |
| **ADR-0034 SW-track** | **Did not exist** | **Filed retroactively + first real triune task ran** | A.1-A.6 + B.1 shipped (pre-this-session) + B.2 (today's meta-demo) |

ADRs that REMAIN as design debt — placeholders that require a
trigger condition before they're meaningful:

- **ADR-0025** Threat model v2 — gated on federation (Horizon 3)
- **ADR-0026** Provider economics — gated on marketplace launch
- **ADR-0028** Data portability — should ship before v0.1 public release; **light current debt**
- **ADR-0029** Regulatory map — gated on hosted realms or minor users

ADR-0023 (Benchmark Suite), ADR-0022 (Memory subsystem), ADR-0019
T7-T10 (MCP-as-server, approval timeouts) are all Proposed +
implementation-in-flight. None block v0.1.0 ship.

---

## Test coverage status

```
  46 unit test files
   2 integration test files (forge_loop + something else)
   0 frontend test files (Vitest scaffold queued in survey)
```

The morning survey called out two coverage gaps:

1. **Cross-subsystem integration tests** (3-5 wanted; currently 1
   file). NOT addressed today; v0.1.0 ships without them.
2. **Frontend Vitest scaffold** (0 tests for ~4,700 LoC of JS).
   NOT addressed today; v0.1.0 ships without them.

What WAS verified end-to-end today via `.command` smoke drivers
(not unit tests, but real-daemon-level):
- A.5 coding tools (6 cases, all pass)
- SW-track triune filing ADR-0034 (21 audit events captured)
- Y2 single-agent orchestration (4 turns, multi-turn coherence)
- Y3 multi-agent rooms (11 cases, @mention chain + max_depth verified)
- Y-full Y1-Y7 (10 steps, all phases exercised)

Live-smoke is structurally weaker than pytest (no isolated test
fixtures, depends on running daemon, slower to run, no CI gating)
but it tests the integration surface unit tests can't reach. The
morning survey's "3-5 cross-subsystem integration tests" gap is
real but the demo-quality risk is lower than the LoC count
suggests.

---

## Frontend coverage status

```
  4,736 LoC across 22 vanilla JS modules
  0     test files
```

New today: `chat.js` (~330 LoC) for ADR-003Y Y6. The other Y
endpoints (Y4 bridge, Y5 ambient, Y7 sweep) are NOT yet wired
into the Chat tab — they exist only as endpoints. Operator runs
them via curl or the Y-full smoke for now. Y6.1 frontend follow-on
should:
- Bridge button (with from_domain + reason input)
- Ambient nudge button + nudge_kind picker
- Retention sweep status indicator (rooms past their retention window)
- @mention autocomplete in the composer

No critical bug found in chat.js during this session. Manual
verification: rooms list, create-room dialog, participant chips,
turn rendering, @mention highlighting, archive flow all working.

---

## Where we are on ADR-0024 horizons

```
  Horizon 1 — committed (~6-9 months from 2026-04-27)
  ────────────────────────────────────────────────────
    ADR-0019 T3-T10           ✅ T3+T4+T5+T6 shipped; T7+T8+T9 (MCP) shipped
                                via ADR-003X. T10 (approval timeouts) deferred.
    Skill Forge v0.1          ✅ Shipped (CLI + frontend tab + 7 audit events)
    ADR-0022 memory v0.1      ✅ Shipped (v0.1 + v0.2 cross-agent disclosure)
    ADR-0023 benchmark        ⏳ Proposed only — no implementation yet
    Polished v0.1 release     ✅ THIS COMMIT ('v0.1.0 — agents you can talk to')

  Horizon 2 — explore (undated)
  ────────────────────────────────────────────────────
    Multi-agent over signed
      message bus              ✅ delegate.v1 + ceremony.v1 + triune bonds +
                                ADR-003Y conversation rooms = the multi-agent
                                substrate. NOT a 'message bus' in the formal
                                sense (no signed envelopes between hosts) but
                                the within-host equivalent runs end-to-end.
    Real-time A/V Companion    ⏳ Companion genre is structurally there with
                                local_only provider posture, but there's no A/V
                                pipeline yet. Local Whisper.cpp + Piper exists
                                in dev-tools.md but isn't wired into agents.
    Persistent simulation
      backbone                 ⏳ Not started — needs its own ADR (Godot vs
                                Three.js vs custom-WASM evaluation per ADR-0024).

  Horizon 3 — north star (vision)
  ────────────────────────────────────────────────────
    VR/XR entry                   ⏳ no work yet
    Federated realms              ⏳ blocked on ADR-0025 threat model v2
    In-world agentic terminals    ⏳ depends on H2 sim backbone
    Social anchoring layer        ⏳ depends on ADR-0027 v0.3 + federation
    Marketplace                   ⏳ blocked on ADR-0026 provider economics
```

**Major unlock today:** the H2 multi-agent coordination property
exists in functioning code. The 'social layer' is a single browser
window away from being demo-able to a non-developer audience: open
Chat tab, start typing, watch agents respond, see their @mention
chain fire, see the audit trail. That's the H3 social-anchoring
thesis running on a single host.

What the H3 vision needs that we don't have yet:
1. **Federation protocol** — ADR-0025 threat model v2 first
2. **Identity proofs across realms** — Ed25519 over agent DNA,
   probably; folds into federation
3. **VR/XR shell** — separate evaluation ADR
4. **Marketplace economics** — ADR-0026 placeholder

All four are properly captured as deferred work; none are
load-bearing for v0.1.0.

---

## Load-bearing tech debt (carry-forward to v0.2)

Sized + prioritized:

1. **R2 finish — `birth_pipeline.py` module extraction.** writes.py
   is still 1,186 LoC. The 2026-04-30 morning survey gave this 1
   day; lower priority post-Y-track since the duplication is gone
   (only the helpers stayed inline). **Effort: 1 day.**

2. **Cross-subsystem integration tests** (3-5 wanted, 1 exists).
   `dispatcher + memory + delegate`, `tool_dispatch with approval
   queue resume`, `skill_run with multi-tool composition`,
   `conversation_turn → llm_think → audit chain coherence`. Y-full
   smoke covers part of #4 but isn't pytest-discoverable for CI.
   **Effort: 1 day.**

3. **Frontend Vitest scaffold** (0 tests for 4,736 LoC). Pick
   3 test cases for chat.js / pending.js / agents.js to bootstrap.
   **Effort: half day.**

4. **JSONSchema input defaults at runtime** in skill engine. Per the
   2026-04-28 phase-D-E review's worked-around case. Manifest
   authors currently hard-code thresholds. Surface change is small.
   **Effort: half day.**

5. **`mfa_check.v1`** — ADR-0033 B1 9th tool. Deferred pending operator
   scoping ("MFA posture target" question). **Gated, not effort-bounded.**

6. **Pytest version of `security-smoke.sh`** (E2). Lets CI gate on
   the canonical chain. Smoke shell suffices for the operator loop
   today. **Effort: 1 day.**

7. **Companion-tier real-time A/V interaction**. Mission pillar 2.
   Needs Whisper.cpp + Piper integration + a new ADR for streaming
   protocol between daemon and frontend. **Effort: weeks.**

8. **HSM hardware adapter** for VaultWarden's key_rotate.v1.
   **Gated on operator hardware decision.**

9. **External product MCP adapters** (Wazuh / Suricata / Defender).
   **Gated on operator install of those products.**

Tier 1 (items 1-4) is ~3 days of focused refactor + testing work
that I'd recommend before public release if traction picks up.
Tier 2 (5-9) is operator-decision-gated.

---

## What I'd recommend for v0.2

**Theme: "Plays well in public."** v0.1.0 ships demonstrable
end-to-end. v0.2.0 is the round of work that makes it usable by
people other than the operator.

```
  v0.2 priorities (ordered by demonstrable impact)

  1. Y6.1 — frontend wires Y4 bridge + Y5 ambient nudge + Y7 sweep
            (currently endpoint-only). Half day.

  2. Y3.5 — suggest_agent.v1 fallback in conversation_resolver
            (when no addressing/mentions, BM25-rank participants
            against the body to pick the relevant responder).
            Half day.

  3. R2 finish — extract birth_pipeline.py from writes.py.
            1 day.

  4. Integration test trio — dispatcher + memory + delegate;
            approval queue resume; conversation_turn → llm_think.
            1 day.

  5. Frontend Vitest scaffold + 3 example tests.
            Half day.

  6. README.md headline refresh — bump LoC counts, mention Chat
            tab, mention SW-track + ADR-0034. v0.1 README is from
            Phase E; the world has shifted.
            Half day.

  7. ADR-0023 benchmark fixture v1 — pick one fixture per genre,
            establish a baseline, surface in character sheet
            'benchmarks' section (currently `not_yet_measured: true`).
            2 days.

  Total: ~5.5 days for v0.2 — usable + tested + demonstrable.
```

Items 1-3 are highest-leverage. Items 4-5 raise quality bar for
contributors. Items 6-7 are visibility.

---

## Closing read

v0.1.0 is shippable as a self-hosted operator-driven local-first
agent foundry with full conversation runtime + browser UI + audit
trail. It's NOT yet hardened for public download by a stranger
(gaps: README is stale, frontend tests don't exist, integration
tests are thin) but THAT'S the next round, not blockers for the
release tag.

The H1 commitments are functionally complete. The H2 'multi-agent
substrate' exists. The H3 'social layer' demo runs in a single
browser window. The load-bearing tech debt is sized and tracked.

Vision-vs-reality: the project shifted today from 'foundation
mostly done, design captured' to 'the headline feature actually
works.' That's a different kind of release.

The Forge has hands now. It can talk. The chain proves what it
said. Time to ship.
