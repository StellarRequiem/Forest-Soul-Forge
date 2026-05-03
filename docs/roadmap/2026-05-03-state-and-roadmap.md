# State of the Forge — 2026-05-03 (post-v0.3.0)

Honest status report. What's shipped, what's in flight, what got
deferred or interrupted, and the full queue of what's left to
finish, polish, or decide. No padding — call out the wonky stuff,
the half-done bits, and the architectural questions sitting open.

This is the canonical "where are we?" reference filed at the close
of a long working session that included v0.3.0 release + audit +
remediation + scheduler runtime + four-bug frontend cascade fix.

---

## Tag + commit state (verified from disk)

```
Latest tag:        v0.3.0 (annotated, 2026-05-03)
Commits since tag: 7+ (chat.js fixes + scheduler runtime + dialog UX)
Total commits:     240+
Test count:        2106 passing (added 34 scheduler unit tests post-v0.3.0)
Source LoC:        44,648 (verified 2026-05-03 audit)
ADRs filed:        37 (ADR-0001 → ADR-0041, gaps 0009-0015)
Builtin tools:     53
Skill manifests:   26 shipped / 23 installed
Genres:            13
Trait roles:       18
Audit chain:       1100+ entries, hash-verified clean (examples/audit_chain.jsonl)
Daemon process:    running, pid 12700, scheduler heartbeat live, 0 task runners
Frontend:          live at 127.0.0.1:5173, all 8 tabs functional
Test agents:       2 active (test 1, Forge_AuditTight_01)
```

The repo is now **private** on GitHub per operator decision (2026-05-03)
— the audit chain primitives, threat model, and signed-identity
mechanisms are too risky to leave public without a commercial wrapper.

---

## What shipped this session

A v0.3.0 close + audit + remediation arc, then a frontend-cascade
firefight, then a scheduler substrate + ADR.

| Burst | Scope | Commit |
|---|---|---|
| 82 | Full audit doc + drift sentinel + Run 001 driver committed | bc2c804 |
| 83 | Audit P0/P1 remediation + 9 zombie agents archived | 7f7743c |
| 84 | Tag v0.3.0 (annotated release) | 9889740 |
| 85 | ADR-0041 — Set-and-Forget Orchestrator design | f155c37 |
| 86 | ADR-0041 T2 — scheduler runtime + lifespan integration | 91a0012 |
| 86.1 | Hotfix chat.js broken `state` named import | d569e20 |
| 86.2 | Fix .chat-dialog [hidden] CSS + cache-miss dropdowns | 1a6075d |
| 86.3 | Add-participant dialog + sticky-bar elapsed-time hint | (this commit) |

Plus runtime artifacts that aren't in commits:
- **Forge_AuditTight_01** — audit-tuned `software_engineer` agent
  (DNA `f782d3ed1b6b`, dominant domain `audit` with weight 1.8).
  Birthed live during testing; available in the registry now.
- **Run 001** — first autonomous coding loop. FizzBuzz solved in
  2 turns / 15 sec via qwen2.5-coder:7b. Driver pattern
  (`live-test-fizzbuzz.command`) committed with 5-bug ledger.
- **Diagnostic helpers committed** — `verify-burst86-scheduler.command`,
  `start-full-stack.command`, `open-in-chrome.command`,
  `dev-tools/check-drift.sh` (the drift sentinel).

---

## In flight — work started but not finished

### ADR-0041 set-and-forget orchestrator (started Burst 85)

T1 (design ADR) and T2 (runtime substrate) shipped. The scheduler
ticks every 30 seconds in the daemon's asyncio loop — but it has
**zero registered task runners**, so it currently does nothing. The
remaining tranches are what make it actually useful:

| Tranche | What it does | Burst |
|---|---|---|
| T3 | Implement `tool_call` task type — dispatch a single tool call against an agent on a schedule. **Closes ADR-0036 T4** (verifier 24h scan). | next |
| T4 | Implement `scenario` task type — multi-step birth+seed+loop+archive. Port FizzBuzz scenario YAML. | after T3 |
| T5 | Operator control endpoints (`POST /scheduler/tasks/{id}/trigger`, `enable`, `disable`, `reset`). Tests + runbook. | closes T5 |
| T6 (added) | SQLite v13 schema for `scheduled_task_state` so state survives daemon restart. | needed before T5 |

Without T3, the scheduler is decorative. With T3 it can run the
verifier scan on a timer (long-deferred ADR-0036 work). With T4 it
can run the FizzBuzz autonomous coding loop unattended. **My
recommendation: ship T3 next.** Single tool-call dispatch from a
schedule is small, well-scoped, immediately useful, and unblocks
the verifier closure.

---

## Audit-remediation backlog (from 2026-05-03 audit, P1/P2)

Burst 83 closed every P0 finding. The P1/P2 items are still open:

| Item | Severity | Effort | Note |
|---|---|---|---|
| ADR statuses unstandardized (37 ADRs without frontmatter) | P1 | 1 burst | `ADRs labeled 'Proposed (T1-Tn implemented)' look like drift to outsiders. Need a structured `status:` field on every one. |
| Initiative annotation reconciliation | P1 | 1-2 bursts | Catalog YAML has 2 annotations; source files have 23. Decide where they canonically live + backfill. |
| ADR-INDEX.md with gap explanation | P2 | small | Brief explainer that ADR-0009 through ADR-0015 are intentionally absent (placeholder slots never used). |
| Skill manifests "shipped vs installed" wording | P2 | small | Already addressed in STATE.md; README still uses old phrasing. |

These are low priority but they're the items an outside reader
would flag first. None block any current work; they're polish.

---

## Frontend polish — surfaced this session, partially done

The chat-tab cascade exposed the broader truth: **the frontend
hadn't been clean-loaded in weeks**. Four latent bugs surfaced;
two more are still open:

| # | Bug | Status |
|---|---|---|
| 1 | `chat.js` imported `{ state }` (no such export) | FIXED 86.1 |
| 2 | `.chat-dialog[hidden]` overridden by `display:flex` | FIXED 86.2 |
| 3 | New-room + bridge dropdowns empty without Agents-tab visit | FIXED 86.2 |
| 4 | window.prompt() copy/paste broken | FIXED 86.3 |
| 5 | Send-turn round-trip blocks UI (5-30s) | PARTIAL 86.3 (visible elapsed-time; real fix = async dispatch) |
| 6 | Agents-tab card click handler — programmatic click didn't open detail pane | OPEN, needs investigation |
| 7 | sweep + ambient dialogs share `.chat-dialog` class — same hidden-bug? | UNTESTED |
| 8 | `/diagnostics` link bottom-right of frontend — does it actually work? | UNTESTED |
| 9 | Mobile / responsive layout — never considered | OPEN (v0.4 territory) |

**The real fix for #5** is async dispatch + `/audit/stream` for live
agent-reply arrival. That's a multi-burst effort that touches the
conversations runtime, not just the frontend. Reasonable to defer
to v0.4.

**Items 6, 7, 8 want a frontend audit pass** — open every tab,
exercise every button, log the result. ~1 burst. Should happen
before any v0.4 app work because that work assumes the existing
frontend is the demo surface.

---

## v0.4 architectural decisions — pending operator call

These came up in conversation and got logged as TBD. Each is real
work that depends on a direction call.

### App platform shape (raised in app-roadmap conversation, ~mid-session)

The broad outline is filed (three-repo: foundry / bridge / app, Tauri
desktop, game-character-creation Forge UX, simple+power-user dual
mode). What's NOT decided:

- **Repo branding** — `harness-app` + `harness-bridge` are placeholders.
- **Mobile platform priority** — iOS first? Both day one? Tauri Mobile vs React Native?
- **Account model** — local-first per-device sovereign vs cloud-account-required?
- **Free-tier policy** — Local-mode-only forever as free tier? Or trial-then-paid?
- **Customer/vertical thesis** — regulated-vertical compliance market vs SMB AI-agent market vs prosumer/dev-tool? The audit-chain moat appeals to compliance buyers, not SMB.

### Local server / multi-user (raised this session)

Two interpretations, contradictory:
- **(A) Shared cloud server** — you host, others log in, isolated tenants.
  Real architectural shift. Adds: account model, multi-tenancy,
  hosting cost, security perimeter, billing.
- **(B) Tauri-bundled installer** — each user runs the daemon on
  their own machine. "Their pages" = their local frontend.
  Matches local-first ethos.

**My recommendation:** B first (Tauri installer), A as a later optional
overlay if needed. B preserves the audit/privacy moat. A becomes a SaaS
business with all that implies.

### Benchmark suite (raised re: support-ticket-triage proposal)

The third-party proposal is a fine foundation but doesn't differentiate
FSF from generic agent frameworks. **Two governance tracks would fix
that:**
- **Adversarial role-injection** — score refusal rate + audit-event emission.
- **Constitution-tamper detection** — modify the constitution file mid-run,
  verify FSF detects it.

These exercise the audit chain + governance pipeline (the actual moat).

### ADR-0042 agent self-timing (proposed, not started)

Original plan: spawned-at + realm-joined-at timestamps + a
`time_since_birth.v1` tool so agents can time themselves. Briefly
mentioned in the orchestrator-arc planning, never filed. Sits as a
candidate for after the orchestrator T3-T5 lands.

---

## Quality gaps (from audit + stress of real use)

| Gap | Status |
|---|---|
| Integration tests | 1 file. Need 3-5 covering dispatcher+memory+delegate, approval-queue resume, skill_run multi-tool. ~1 day. |
| Frontend test coverage | 0 tests for 3,500 LoC of vanilla JS. The chat.js cascade is exactly what frontend tests would have caught. ~half day for Vitest+jsdom scaffold + 2-3 example tests. |
| Y5 ambient mode | Scaffolding only — never made functional. ADR-0041's scheduler is now the right substrate to fulfill it. |
| ADR-0036 T4 | Was deferred → now subsumed by ADR-0041 T3. Still need to officially mark T4 closed when T3 ships. |
| `/diagnostics` panel | Link visible bottom-right of frontend. Untested whether the panel renders anything useful. |
| Live agent-latency dashboard | Operator-level: "qwen2.5-coder:7b averages 8.2s on chat turns." Data exists in audit chain; no aggregation surface. The "diagnostics + test data + markable improvement from real interaction" ask. |

---

## Side quests this session — got distracted from what

| Original direction | Detoured into | What's left of original |
|---|---|---|
| Run 002+ scenario series (10 × 30min coding tests) | Validated runtime via Run 001; never built T4 substrate, never authored 002-010 | Queued behind ADR-0041 T3+T4 |
| v0.4 app planning doc (Burst 82 in old plan) | Audit took priority; orchestrator design (Burst 85) jumped ahead | Still pending — file as ADR-0042 or roadmap doc once T3 lands |
| Tag v0.3.0 → file v0.4 planning | Tagged but pivoted to scheduler implementation | The planning conversation happened; no doc was filed |
| Burst 87 = ADR-0036 T4 implementation | Subsumed by ADR-0041 T3 | Will close when T3 ships |
| Burst 88 = ADR-0042 agent self-timing | Never started | Open candidate after orchestrator |
| Diagnostics dashboard | Came up as "test data + markable improvement" | Open — strong candidate for next |
| Verify chat tab | Discovered it was dead → 4-bug cascade → most of late-session work | Mostly done; see frontend polish gaps above |

---

## Recommended sequence (next 5 bursts, in priority order)

1. **Burst 87 — Frontend audit pass.** Open every tab, test every button.
   Fix latent bugs while context is hot. Document anything found in a
   short audit doc. *Why first:* the chat-tab cascade proved we have
   latent UX bugs from rare-clean-load conditions. The Agents-tab card
   click already showed a hint of one. Better to sweep now than
   surface them piecemeal during demos.

2. **Burst 88 — ADR-0041 T3 (tool_call task type).** Closes
   ADR-0036 T4 (verifier scheduled scan), proves the scheduler can
   actually do something, ships the smallest possible unit of value
   on top of the substrate.

3. **Burst 89 — ADR-0041 T6 (persistence).** SQLite v13 schema for
   scheduled_task_state. Scheduler state survives daemon restart.
   Critical for "set and forget."

4. **Burst 90 — ADR-0041 T4 (scenario task type) + FizzBuzz YAML port.**
   Now you can run autonomous coding loops on a timer. Original
   "10 scenarios × 30 min" ask becomes feasible.

5. **Burst 91 — Diagnostics dashboard.** Aggregate audit chain into
   p50/p95/p99 latency per tool, per agent. Token-budget tracker.
   The "markable improvement from real interaction" feedback loop.
   Closes the observability gap that's been latent.

After these five: tag v0.4.0-pre or roll into v0.4 app-platform work.

---

## Decisions awaiting orchestrator (in order of urgency)

1. **Frontend polish first vs orchestrator T3 first?** Both reasonable.
   Frontend polish keeps demos honest; orchestrator T3 unblocks autonomous testing.
2. **Multi-user direction A (cloud) vs B (Tauri installer)?** Affects every v0.4 architectural call.
3. **Customer/vertical thesis** — needed before app UX work begins.
4. **Mobile platform** — Tauri Mobile vs React Native vs PWA-first?
5. **Free-tier policy** — local-only-forever as the moral commitment, or paid eventually?

---

## "Wonky stuff" — honest list

Things you'd notice if you poked at them, in roughly descending importance:

- Chat-bar latency makes auto-respond conversations feel broken even though they work — needs async dispatch (multi-burst fix)
- Agents-tab card click handler doesn't reliably open the detail pane on programmatic click (didn't fully diagnose during this session)
- The 4 chat dialogs (new-room, bridge, sweep, ambient, plus the new add-participant) all share `.chat-dialog` class — sweep and ambient haven't been visually tested post-86.2 fix
- `/diagnostics` link in frontend status bar — never verified it does anything useful
- The 23 installed skills are all security-flavored; no coding skill manifest exists in `data/forge/skills/installed/` — Run 001 had to bypass the skill engine entirely
- ADR statuses are inconsistent (audit P1)
- 1 integration test file is the entire integration coverage
- Frontend has 0 automated tests
- v0.4 app planning doc was promised but never filed
- 5 prior `Forge_FB001_*` zombie agents from Run 001 v1-v5 attempts: archived in Burst 83, but the Run 001 driver itself doesn't archive on exit — future scenario runs will leak again
- Forge_AuditTight_01 is alive in the registry but hasn't actually done any work yet — birthed and named only

---

## Honest read on session dynamics

This session covered a LOT of ground. Pattern: orchestrator-style
work (audit + tag + scheduler ADR + scheduler runtime) interleaved
with firefighting (the chat.js cascade). The firefighting was high
yield — surfaced 4 latent bugs that had been sitting for ~2 weeks.
The orchestrator work was steady — design + substrate + Forge born
live + verified.

The cost is that the **forward arc (Run 002+, v0.4 app, ADR-0042)
didn't advance.** Most of the remaining work in the queue above is
either substrate-completion (T3-T6) or polish (frontend audit, ADR
status, diagnostics). The big architectural decisions (multi-user
direction, customer thesis) are still open and will be the gate for
v0.4 work to actually start.

---

## What "ready" looks like

For the next operator-facing demo:
- All chat-tab tabs functional (frontend audit pass closes this)
- Scheduler can run at least one task type (T3 closes this)
- Diagnostics surface shows latency + token budget (Burst 91 closes this)
- README + STATE current (already cleaned in Burst 83)
- v0.3.0 tag captured (already done)

For v0.4 app-platform work to begin:
- Direction calls (multi-user A/B, customer/vertical, mobile, free-tier)
- ADR-0042 filed (game-character creation + harness-app/bridge architecture)
- Foundation polish complete (frontend tests, integration test trio)

For real-customer pilot:
- Customer vertical chosen
- ONE workflow built end-to-end in that vertical (not five)
- Audit chain export format standardized
- Pricing scaffold + Stripe integration

---

**Bottom line:** the substance is solid. The audit chain works,
test count holds at 2106 with no regressions, the v0.3 arc shipped
two ADRs end-to-end, the autonomous coding loop is proven, the
scheduler heartbeat is live. What's left is polish, decisions, and
the v0.4 build itself. The substrate underneath that build is
ready for it.
