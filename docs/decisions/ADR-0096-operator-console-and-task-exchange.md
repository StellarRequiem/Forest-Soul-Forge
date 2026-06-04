# ADR-0096 — Operator Console + Task Exchange (the bounty board)

**Status:** Accepted (2026-06-04). Wield-points locked with the operator (see
"Decisions" below). Extends ADR-0095 (the synaptic layer) with the thing that
*generates the experience* trust is built from, a human-in-the-loop surface to
drive it, and a **tiered, repeatable, autonomously-runnable training ladder** that
doubles as the system's self-test and its audit/documentation exerciser. Build
proceeds **engine-first** (the training harness) then the console on top.

## Context

The operator wants to sit at the Forest dashboard, **see a set of tasks, click to
send an agent to work on one, and watch it happen live** — buttons and dropdowns,
not curl. Today the dashboard has 16 tabs but none of them is that console (the
existing `orchestrator` tab is the ADR-0067 cross-domain *routing* viewer, not task
assignment), and there is **no live stream** — `statusbar.js` notes everything is
polling "until F6 ships an SSE log tail."

The good news (grounded in the running system): the **backend primitives already
exist**. `POST /agents/{id}/tools/call` dispatches work (proven live, ADR-0019);
the scheduler runs one-shot, recurring, and multi-step `scenario` tasks
(ADR-0041); `/synapse/*` gives live trust readouts and `route_recommend.v1` gives
a trust-ranked agent suggestion (ADR-0095); the audit chain is the live activity
log. So "the console" is mostly **frontend assembly + one optional new stream**.

But the operator's idea went further than a console: tasks should be
**classifiable**, drawn from a **selectable catalog**, and — the sharp part — the
system should be able to **generate its own tasks and post them like a bounty
board to test agents.** That last move is what makes this more than a UI. It
**closes the ADR-0095 loop**: the trust graph is only as meaningful as the
experience that feeds it. A bounty board is a *source of classified, audited
experience* — the mesh's self-calibration harness. Tasks, trust, and routing then
share one taxonomy (`problem_class`), and the loop is:

```
  bounty board generates a classified task
        → route_recommend suggests the best-trusted agent (informs)
        → operator confirms / overrides (capability gated)
        → agent executes (read-only auto-run, or human-gated if side-effectful)
        → acceptance check judges the outcome
        → outcome records to the trust graph (the universal sink, already built)
        → trust sharpens → next routing is better-informed
```

## Decision (the shape — subject to the wield-points below)

### 1. The task model
Every task — catalog or generated — is one object:

- **`problem_class`** (required): the *same* key the trust graph + `route_recommend`
  use. This is the spine: tasks, trust, and routing share one taxonomy. A task
  *is* a unit of classified work, and its outcome *is* a trust observation for
  `(agent, problem_class)`.
- **`mode`**: `one_shot` (a single `tools/call`) · `scenario` (a multi-step
  scheduler workflow) · `conversational` (an `llm_think`/chat objective). All
  three already have backend execution paths.
- **`source`**: `catalog` (operator-defined, selectable) or `bounty` (system-
  generated).
- **`acceptance`**: how success is judged — the function that turns "the agent did
  something" into a `succeeded`/`failed` trust observation. **This is load-bearing:
  trust is only as honest as the acceptance check** (see Governance).
- **`side_effects`**: read-only / sandboxed / side-effectful — drives the auto-run
  vs. human-gate decision.

### 2. The bounty board
A generator that posts classified test tasks to **probe and calibrate** agent
trust. Its default policy targets **uncertainty**: the trust graph already knows
which `(agent, problem_class)` posteriors are widest (under-tested) — the board
prioritizes bounties there, the same Thompson-style exploration `route_recommend`
already does, but as a *task generator* rather than a *router*. Bounties are
**safe by construction** (see Governance): read-only / sandboxed, or human-gated.

### 3. Trust-informed assignment (informs, never auto-grants)
The console suggests the best agent per task via `route_recommend` (ADR-0095). The
operator confirms or overrides. Converting trust into capability — actually
*running* a side-effectful task — stays human-gated (ADR-0094/0095).

### 4. The Operator Console (new dashboard tab)
- **Task list** — catalog + open bounties, filterable by `problem_class` / `mode`
  / `source`.
- **Assign** — pick an agent (dropdown, **trust-ranked** via `route_recommend`),
  set args.
- **Dispatch** — one button → `tools/call` (or scenario launch).
- **Observe** — a live panel: the agent's audit events, the dispatch result, and
  the **trust delta** for that `problem_class` updating as work lands. v1 polls;
  v2 streams (SSE).

### 5. Outcome → trust loop
Outcomes already record to the synaptic layer through the dispatcher's universal
sink (ADR-0095, shipped). The bounty board needs **no new recording path** — it
just generates the work; the existing sink captures the result. That is why this
is a small build on a big foundation.

### 6. The tiered training ladder (repeatable, autonomous self-test)

A fixed, versioned ladder of **Baseline + Level 1–4** tasks — the *repeatability*
spine. Deterministic, read-only, graded by composition/complexity (not by blast
radius), so:

- **Repeatable** — same tasks, same deterministic acceptance checks, run again and
  again; scores are comparable over time. A drop is a regression signal.
- **Autonomous** — because they are read-only/sandboxed, the harness MAY run them
  on its own throughout (the §1 rail already permits read-only auto-run). A
  continuous self-test the mesh runs against itself.
- **Self-tests a deeper slice per tier:**
  - **Baseline (T0)** — dispatch a pure-function read-only tool; assert the known
    output. Proves the dispatch → audit → acceptance path end-to-end.
  - **T1 — determinism** — a battery of the same tool across inputs; proves
    correctness + repeatability.
  - **T2 — the audit system** — dispatch `audit_chain_verify`; assert the chain
    verifies. The explicit "test the audit system" tier.
  - **T3 — composition + provenance** — a multi-step sequence; assert each step
    *and* that the audit chain grew by exactly the expected number of entries.
  - **T4 — documentation + integrity** — emit a run report (the documentation
    artifact) and assert audit-chain *and* trust-graph integrity both hold. The
    "test the documentation system" tier.
- **Feeds trust** — every tier outcome records to the synaptic layer under a stable
  `training.tN.*` problem_class, so an agent's per-tier competence is evidence-
  backed and trends over time.
- **Keeps audit + docs honest by construction** — the harness verifies the audit
  chain before/after and emits an auditable report; a failure in either *is* a
  failed training run.

The ladder is v1's concrete catalog content and the first thing the bounty board
generalizes — a bounty is "a training task the system invented."

## Governance & safety (non-negotiable — a task-generating system is an autonomy surface)

1. **No autonomous side effects.** System-generated bounties may **auto-run only
   if read-only / sandboxed**. Anything touching filesystem / external / capital /
   execution routes through `ApprovalGateStep` — the always-approval invariant
   (ADR-0094) holds without exception. The operator gates it.
2. **Belief, not capability.** The board MAY generate, classify, route-recommend,
   and record outcomes (all ADR-0095-allowed self-improvement). It MAY NOT grant a
   tool, widen a permission, lift a quarantine, or move capital. Those are
   human-gated.
3. **No belief without verification.** Acceptance checks that feed trust must be
   **deterministic / auditable** where possible. LLM-graded acceptance is allowed
   only **flagged and down-weighted** (and never for safety-relevant classes) —
   otherwise the board manufactures unverified trust, which is the OPERATOR_PROTOCOL
   red line ("a theoretical/fictional source carries zero epistemic weight").
4. **Auditable provenance.** Every bounty, assignment, and outcome lands in the
   audit chain. A generated task is as traceable as a human-authored one.
5. **Operator is the loop — except governed read-only self-test.** Side-effectful
   work is always operator-initiated. The agreed exception: the **tiered training
   ladder** (deterministic, read-only) MAY run autonomously throughout, because §1
   already permits read-only auto-run. It carries a **kill switch + a rate cap**
   and cannot escalate (belief only, ADR-0095). Anything beyond read-only
   self-test stays operator-initiated.

## Decisions (locked 2026-06-04, revisitable)

Resolved with the operator:
- **A** catalog = **YAML** (`config/tasks/`, git-tracked + diffable).
- **B** `problem_class` = **tool-keys for v1**; richer domain labels later.
- **C** bounty policy = **uncertainty-targeted** (v3).
- **D** acceptance = **deterministic-only** (required for repeatable training;
  LLM-graded stays out until gated + down-weighted).
- **E** auto-run = **autonomous for read-only/sandboxed *training* tasks** (the
  tiered ladder), governed by the §1 read-only rail + a kill switch + rate cap;
  everything side-effectful stays operator-initiated + gated.
- **F** live = **polling v1**, SSE v2.
- **G** scope = **engine-first**: build the tiered training harness (repeatable,
  autonomous, audit/doc-validating) first, then the console on top.

## Phasing

- **v1 — Console (no/low backend change).** New `console` tab: catalog tasks from
  an operator-authored source, agent dropdown trust-ranked via `route_recommend`,
  `one_shot` dispatch, **polled** live panel (activity + outcome + trust delta).
  Reuses `tools/call` + `/synapse` + `/agents` + audit. Ships fast, proves the UX.
- **v2 — Modes + live.** `scenario` and `conversational` modes; the **SSE event
  stream** (the F6 gap) for true real-time observation; scheduler task control
  (view / enable / trigger).
- **v3 — The bounty board.** The generator (uncertainty-targeted classified
  tasks) + the acceptance-check framework + the closed self-calibration loop.
  This is where it becomes the mesh's test harness.

## [YOUR CALL] — the wield-points

- **A. Catalog format** — operator-authored YAML (`config/tasks/*.yaml`, git-
  tracked, diffable, fits the existing config discipline) **vs.** DB-backed +
  UI-authored (richer, but a new write surface + migration).
- **B. What `problem_class` *is*** — reuse tool keys (`llm_think.v1`) — zero new
  taxonomy, but coarse — **vs.** a richer domain taxonomy (`regulatory_timing`,
  `code_review`, …) — far more useful routing, but you own the taxonomy and must
  keep it disciplined (sprawl risk).
- **C. Bounty generation policy** — uncertainty-targeted (calibration-first,
  recommended) · round-robin · operator-seeded templates only · LLM-synthesized
  (most powerful, highest "unverified test" risk).
- **D. Acceptance checks** — deterministic-only (safe, limited) **vs.** allow
  LLM-graded (flexible, must be weighted + gated per Governance §3).
- **E. Auto-run boundary** — every dispatch operator-initiated (v1 default,
  safest) **vs.** allow the board to auto-dispatch *read-only* bounties to idle
  agents (autonomous calibration — powerful, needs the §1 rails + a kill switch).
- **F. Live observation** — polling for v1 (cheap, ships now) **vs.** build SSE
  now (real-time, net-new endpoint + frontend wiring).
- **G. Scope of this build** — you chose *spec first*. After your redlines: build
  v1 only, or v1+v2, or all the way to a v3 demo?

## Risks

- **Autonomy surface.** A system generating work agents run is the riskiest thing
  here; the Governance rails are the mitigation and are non-negotiable.
- **Unverified trust.** LLM-graded acceptance feeding the trust graph would poison
  it — gate + down-weight, or keep deterministic.
- **Taxonomy sprawl.** Undisciplined `problem_class` proliferation makes trust +
  routing noisy. Pick B deliberately.
- **Frontend size.** The console is real UX (vanilla-JS tab, ~the size of
  `orchestrator.js`/`agents.js`); v1 keeps it small by reusing endpoints.

## Relationship to prior ADRs

- **ADR-0095 (synaptic layer).** This is its other half: ADR-0095 records and
  routes on trust; ADR-0096 *generates the classified experience* that makes trust
  real, and gives the operator the wheel. `route_recommend` becomes the console's
  assignment suggester; the universal sink already captures bounty outcomes.
- **ADR-0094 (always-approval invariant).** The auto-run boundary (§1) is exactly
  this invariant applied to generated tasks: read-only may run; side-effectful is
  gated.
- **ADR-0041 (scheduler).** `scenario`/`tool_call` task types are the v2 execution
  substrate; the bounty board is a new *source* of scheduler tasks, not a new
  runtime.
- **ADR-0067 (orchestrator/domains).** Distinct: that routes *intents across
  domains*; this assigns *agents to classified tasks*. They can later share the
  domain taxonomy if wield-point B picks the richer option.
