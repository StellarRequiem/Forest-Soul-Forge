# Runbook — diagnostic harness (ADR-0079)

**Scope.** When + how to run the section-by-section diagnostic
harness against the live daemon.

**Audience.** Operator preparing to close a Phase rollout, tag
a release, or triage a "something feels off" suspicion.

**Why it exists.** B350 (2026-05-17) shipped a one-line dispatcher
fix for `audit_chain_verify.v1`. The substantive finding was that
the tool had been declared working since ADR-0033 Phase B1 but was
actually dead code on the HTTP path — unit tests passed because
the test fixture built `ToolContext` by hand, the dispatcher never
wired the chain in, and no consumer surfaced the bug until D3
Phase A's `archive_evidence.v1` became the first real caller. The
harness exists to catch the B350-class proactively rather than
one-bug-at-a-time as consumers surface them.

---

## At a glance

13 sections, each a standalone `.command` driver:

| # | Section | Catches |
|---|---|---|
| 01 | static-config | YAML parse + cross-reference drift |
| 02 | skill-manifests | dead skills / missing tool deps |
| 03 | boot-health | startup_diagnostics regressions |
| 04 | tool-registration | catalog ↔ /tools/registered drift |
| 05 | agent-inventory | per-agent kit violations |
| **06** | **ctx-wiring** | **B350-class subsystem-not-wired bugs** |
| 07 | skill-smoke | skill loader drift |
| 08 | audit-chain-forensics | chain integrity + signature coverage |
| 09 | handoff-routing | (domain, capability) routing gaps |
| 10 | cross-domain-orchestration | orchestrator wiring |
| 11 | memory-retention | substrate endpoint reachability |
| 12 | encryption-at-rest | encryption state + secret backend |
| 13 | frontend-integration | per-tab API endpoint reachability |

Sections 01-02 + 09 are pure on-disk (no daemon needed).
Everything else needs a live daemon at `http://127.0.0.1:7423`.

---

## When to run

- **Before closing any Phase rollout** (e.g., D3 Phase A, D3 Phase B,
  D4-advanced). Replaces the per-rollout `live-test-<phase>.command`
  shape with fuller coverage.
- **Before tagging a release.** `release_gatekeeper`'s
  `release_check.v1` skill can dispatch the harness as a
  dependency.
- **When something feels off.** "The daemon is degraded" / "agents
  aren't dispatching like they used to" / "the frontend's Marketplace
  is stuck loading" — fire the harness and read the consolidated
  punch list before deep-diving.
- **NOT in CI.** CI doesn't have a live daemon; CI runs pytest.
  This harness covers the runtime-wiring surface pytest can't
  reach.

---

## How to run

### Run everything (preferred)

From Finder, double-click:

```
dev-tools/diagnostic/diagnostic-all.command
```

This is the umbrella. It runs all 13 sections sequentially (~15-60s
total depending on daemon state), and writes:

- `data/test-runs/diagnostic-all-<timestamp>/summary.md` — aggregated
  per-section status + consolidated FAIL punch list
- `data/test-runs/diagnostic-all-<timestamp>/section-NN-name.stdout.log`
  — per-section stdout
- `data/test-runs/diagnostic-NN-name/report.md` — each section's
  own structured report (also updated by individual runs)

Exit code: 0 if all sections PASS, non-zero if any FAIL or MISSING.

### Run an individual section

Useful for focused investigation. Same path pattern:

```
dev-tools/diagnostic/section-NN-<name>.command
```

Each is independently runnable. Writes its `report.md` to the same
per-section directory. Exit code: 0 PASS, non-zero FAIL.

### Run before the daemon is up

Sections 01, 02, 09 work without a live daemon (pure on-disk).
The other sections will report a "daemon unreachable" abort at
the top of their `report.md`.

---

## Reading the summary

`summary.md` has three sections:

1. **Section results table** — per-section PASS/FAIL/MISSING with
   duration and a link to the per-section report.
2. **Consolidated punch list** — every `[FAIL]` line from every
   section's report.md, grouped by section. This is the operator's
   primary triage view.
3. **Tally** — sections run / PASS / FAIL / MISSING.

If the consolidated punch list is empty, the substrate is green
and a Phase rollout / release can proceed.

---

## What each non-PASS status means

- **`[PASS]`** — the check succeeded.
- **`[FAIL]`** — real issue worth a focused fix-burst. Each FAIL
  line includes the specific evidence (response body, error
  message, file path).
- **`[INFO]`** — informational. Common shapes: operator opt-in
  defaults (`encryption_at_rest=off`, `priv_client=disabled`),
  capabilities declared in a domain manifest but not yet wired
  in handoffs.yaml (expected during a rollout), planned domains
  whose dependent capabilities can't resolve yet.
- **`[SKIP]`** — the check was bypassed because a precondition
  wasn't met (subsystem disabled by env flag, tool not
  registered, dependent agent missing). Not a failure.
- **`MISSING`** (umbrella only) — the section's `.command` script
  was deleted or moved.

---

## Recovery — common failure modes

### Section 03 reports "daemon unreachable"

The daemon at `http://127.0.0.1:7423` isn't responding.

```bash
# from Finder:
dev-tools/force-restart-daemon.command
```

Wait 6-8 seconds for lifespan, then re-run the harness.

### Section 03 reports degraded subsystems

The startup_diagnostics tri-state filter (B353) splits noise from
signal:

- `disabled`, `off`, `skipped`, `not_configured`, `n/a` → INFO.
  These are intentional operator opt-out defaults.
- `failed`, `error`, `degraded`, `broken` → FAIL. Real signal.

If the FAIL line names `tool_runtime`, run the focused probe:

```
dev-tools/diagnostic/probe-tool-runtime.command
```

That dumps the full `tool_runtime` diagnostic entry to
`data/test-runs/probe-tool-runtime/healthz.json` so you can see
the exact registry/catalog mismatch (or other error).

### Section 06 reports a subsystem "appears not wired"

This is the B350-class catch. The dispatcher's `ToolContext`
constructor at `dispatcher.py:999` is missing the typed-field
assignment for that subsystem. Fix shape (per B350):

1. Confirm the subsystem has a typed field on `ToolContext` in
   `src/forest_soul_forge/tools/base.py`. If not, add one
   (default None).
2. In `dispatcher.py:999`, add `subsystem_name=self.subsystem_attr`
   to the `ToolContext(...)` constructor call.
3. In the tool that depends on the subsystem, prefer
   `ctx.subsystem_name` over any constraints-dict fallback.

### Section 08 reports chain verify FAIL

The audit chain has an integrity break. The error names
`broken_at_seq=N, reason=<text>`. Common shapes:

- `seq gap: expected N+1, got N` — a chain entry was skipped or
  the JSONL file was edited.
- `prev_hash mismatch` — an entry's `prev_hash` doesn't match the
  prior entry's `entry_hash`. Indicates the chain was modified
  out-of-band.

Recovery requires inspecting the chain around the broken seq.
Don't blindly truncate — the chain is the project's tamper-
evidence substrate.

### Section 09 reports unmapped capabilities

INFO line, not FAIL. Expected during rollouts where a domain
manifest declares a capability before its handoffs.yaml mapping
lands. Track via `git log config/handoffs.yaml` to confirm the
mapping is on someone's queue.

### Section 13 reports a tab FAIL

The frontend tab's API endpoint isn't responding. Required tabs
(Agents, Skills, Tools, Marketplace, Pending, Orchestrator)
return FAIL on 404; optional tabs (Provenance, Scheduler,
Conversations) return INFO. Fix:

- 404: the router isn't mounted in the daemon. Check
  `src/forest_soul_forge/daemon/app.py` for the relevant
  `app.include_router(...)` line.
- 500: the endpoint is wired but raising. Check daemon stderr
  via the running `force-restart-daemon.command` terminal.

---

## Extending the harness

A new section is one new `.command` driver in
`dev-tools/diagnostic/`. Add it to the umbrella's `SECTIONS`
array, and (if it's a numbered section) update this runbook's
at-a-glance table.

A new check in an existing section is an addition to that
section's per-check loop. Each check produces one
`(STATUS, name, evidence)` tuple appended to the section's
`results` list.

**Discipline reminder:** every new dispatcher-owned subsystem on
`ToolContext` should get a probe in section 06. Every new
operator-facing tab should get an endpoint in section 13. Every
new domain manifest gets covered automatically by section 09.

---

## Reference

- `ADR-0079` — diagnostic harness decision doc
- `B350` — fix(dispatcher): wire audit_chain into ToolContext —
  the surfacing commit that motivated this ADR
- `B353` — section 03 noise filter (tri-state status handling)
- `dev-tools/diagnostic/section-NN-*.command` — section drivers
- `dev-tools/diagnostic/probe-tool-runtime.command` — focused
  probe for tool_runtime registry/catalog drift
- `dev-tools/diagnostic/diagnostic-all.command` — umbrella runner
- `data/test-runs/diagnostic-*/` — section reports + run logs
- `live-test-fizzbuzz.command` — canonical autonomous-loop driver
  pattern this harness's section drivers follow
