# ADR-0056 — Experimenter Agent (Smith)

**Status:** Proposed (2026-05-07). Userspace-only delivery — uses
existing kernel ABI surfaces (ADR-0019 dispatcher, ADR-0041
scheduler, ADR-0043 plugin protocol, ADR-0045 posture, ADR-0052
secrets). Adds one new pipeline step (`ModeKitClampStep`), one
constitution shape (the Experimenter's), and a chat-tab review
pane. No schema migrations, no new audit event types.

Pairs with ADR-0001 D2 (identity invariance — Smith's
constitution_hash is immutable per cycle; new tools/skills it
drafts require operator-approved `tools_add` to grant), ADR-0008
(frontier provider opt-in), ADR-0021-amendment (initiative
ladder L0-L5), ADR-0048 (computer-control allowance — Smith
inherits the same posture-clamp surface).

## Context

The operator wants an agent that:

1. Has access to the maximally-permissive legal kit — every
   tool in `tool_catalog.yaml` modulo the ones that touch
   identity surfaces (audit chain, registry, constitution
   hashes themselves).
2. Can use the strongest model the system can safely run —
   on this hardware that's qwen2.5-coder:14b locally OR
   Claude Sonnet 4.6 via the frontier provider (verified
   2026-05-07 via the smoke test).
3. Operates in **three modes**:
   - **explore** — scheduled-timer-driven, read-only.
     Reads the codebase + audit chain + memory, identifies
     improvement candidates, writes findings to its own
     procedural + semantic memory. No mutations.
   - **work** — operator-fired or scheduled, full kit.
     Plans, implements, tests, commits. Branch-isolated.
     One target per cycle.
   - **display** — operator-pull review surface. Recent
     cycle reports + diff viewer + approve/deny + the
     agent's "request to add X to my kit" entries. Operator
     approvals trigger merges + `tools_add`/`skills_reload`
     calls. Denials reinforce-negative the underlying
     pattern via ADR-0054 T5's `memory_tag_outcome.v1`.
4. Can draft new tools/skills/plugins when needed and
   request operator approval to use them — closing the
   self-recursive-improvement loop without bypassing
   ADR-0001 D2's identity-immutability rule.
5. Has **YELLOW posture default** with operator-toggleable
   swap to RED (refuse all non-read-only) or GREEN (auto-
   approve within genre cap). Posture is per-agent; the
   chat-tab "posture toggle" UI exists today (ADR-0045).

The substrate is mostly there. ADR-0019 governance pipeline
accepts new pre-execute steps (ADR-0054 T3 added
`ProceduralShortcutStep` as proof). ADR-0041 scheduler accepts
new `tool_call` and `scenario` task types via
`config/scheduled_tasks.yaml` (3 scheduled tasks already running
on this Mac). ADR-0043 plugin grants give per-(agent, plugin)
fine-grained access. ADR-0045 posture clamps non-read-only
dispatches. ADR-0052 makes the frontier API key reachable
without storing secrets in plaintext.

What's missing:

- The **mode tagging** at dispatch time + a pipeline step that
  clamps eligible tools per mode.
- A **branch-isolation constraint** on `shell_exec.v1` so
  Smith's commits can never reach `main` directly.
- An **explore-mode scheduled task set** in
  `scheduled_tasks.yaml`.
- A **display-mode UI** in the chat tab with diff viewer +
  approve/deny + cycle-report list.
- A **self-augmentation flow** — agent's "request to add X"
  surfaces as a queue the operator approves, which then
  triggers automated `tools_add`.
- A **birth-time kit definition** for the
  `experimenter` role with frontier provider preference.

## Decision

Build the Experimenter agent as a **single identity** with three
operating modes selected per-dispatch. Default name: **Smith**.
Reasons for one identity rather than three:

- Unified memory + audit + reinforcement. What Smith learns in
  explore mode is available in work mode; reinforcement from
  display mode flows back to procedural shortcuts that fire on
  the next explore.
- Single posture toggle. Operator flips Smith yellow→red on
  cycle 7 because cycle 6 went badly; that single switch
  affects every mode immediately.
- Simpler mental model. "Smith is in explore mode right now"
  is easier to reason about than "the explore-Smith agent."

### Decision 1 — Birth-time constitutional kit

Smith's constitution lists every tool in `tool_catalog.yaml`
EXCEPT those that touch identity-immutable surfaces:

- **Excluded:** any tool that mutates `examples/audit_chain.jsonl`
  or `data/registry.sqlite` directly. The audit chain is
  append-only by the kernel itself; the registry is rebuilt from
  artifacts. No agent should write either.
- **Included:** llm_think, code_read, code_edit, shell_exec,
  pytest_run, ruff_lint, mypy_typecheck, semgrep_scan,
  bandit_security_scan, tree_sitter_query, git_log_read,
  git_diff_read, git_blame_read, pip_install_isolated,
  memory_recall, memory_write, memory_tag_outcome,
  memory_flag_contradiction, audit_chain_verify, file_integrity,
  log_scan, log_aggregate, traffic_flow_local, port_scan_local,
  posture_check, continuous_verify, jit_access, dns_lookup,
  web_fetch, browser_action, mcp_call, suggest_agent, delegate,
  + all six computer-control tools from ADR-0048.

Per-tool constraints in Smith's constitution:

- `code_edit.v1`: `allowed_paths` = `[src/, tests/, docs/,
  config/, dev-tools/, frontend/]` — the work tree, never
  `examples/`, `data/`, or `~/.fsf/`.
- `shell_exec.v1`: `allowed_commands` = `[git, pytest, ruff,
  mypy, semgrep, bandit, python, pip, fsf, curl]` —
  expressly EXCLUDES `rm -rf`, raw `git push origin main` (the
  branch-isolation enforcer; see Decision 3), `sudo`, `dd`,
  `mkfs`.
- `git_log_read`/`diff_read`/`blame_read`: `allowed_paths` =
  whole repo (read-only access is fine).
- `web_fetch.v1`: `allowed_hosts` = a curated list of
  research-relevant domains (docs.python.org, github.com,
  arxiv.org, etc.) — extensible at operator request via
  `tools_add` flow.
- `mcp_call.v1`: `allowed_mcp_servers` = empty initially.
  Operator grants per-server via the marketplace UI (ADR-0055
  M5 once it lands; until then via `POST
  /agents/{id}/plugins/grant`).
- `delegate.v1`: empty allowlist initially. Smith doesn't
  cross-call other agents in cycle 1; operator opens this up
  once trust is established.

### Decision 2 — Three-mode system via per-dispatch tagging

Mode is a string on `task_caps`: `task_caps.mode` ∈ `{explore,
work, display, none}`. The dispatcher reads it and hands it to
a new pipeline step.

#### `ModeKitClampStep`

Sits in the dispatcher's pipeline AFTER `ConstraintResolutionStep`
(so it can read `dctx.resolved.constraints`) and BEFORE
`PostureGateStep` (so the kit clamp applies before posture
adjudicates the result).

Behavior:

- `mode == "explore"` — refuses any tool whose
  `side_effects` is not `read_only`. Allows: code_read,
  git_*_read, llm_think, memory_recall, memory_tag_outcome,
  audit_chain_verify, web_fetch (read), file_integrity,
  posture_check, etc. Refuses: code_edit, shell_exec,
  pytest_run (writes pytest cache), pip_install_isolated,
  computer_click, computer_type, computer_run_app, etc.
- `mode == "work"` — full kit per Decision 1's constitution.
  No clamp.
- `mode == "display"` — refuses everything except
  `memory_recall`, `memory_tag_outcome`, `git_diff_read`,
  `git_log_read`, `audit_chain_verify`. Smith in display
  mode is read-only review of its own past cycles.
- `mode == "none"` (default if unset) — pass through to the
  rest of the pipeline; behaves like a normal agent dispatch.

Refusal reason: `mode_kit_clamp` — distinct from
`genre_floor_violated` so an operator inspecting a refusal
sees which gate fired.

The step is a no-op for any agent that isn't Smith (configurable
via `experimenter_role` setting; default `experimenter`).

#### `task_caps.mode` plumbing

Set by:
- The scheduler when firing an explore-mode task (per
  Decision 3's YAML entries).
- The chat-tab UI when operator clicks "Run cycle" (work) or
  "Review cycles" (display) — passes `task_caps.mode`
  through to the dispatch endpoint.
- An operator's manual `curl POST /agents/.../tools/call`
  with `{"task_caps": {"mode": "..."}, ...}` for ad-hoc
  testing.

### Decision 3 — Branch isolation

Smith's `shell_exec.v1` allowed_commands explicitly forbid
`git push origin main` and `git push origin master`. The
positive expression: Smith pushes ONLY to `experimenter/cycle-*`
branches. The constraint is enforced via a regex match in the
shell_exec validator extension that fires for git operations.

In addition:

- `shell_exec.v1` constraint `forbidden_paths` includes
  `examples/audit_chain.jsonl`, `data/registry.sqlite`,
  `~/.fsf/secrets/` — even read access via shell is refused
  (forces Smith through the proper memory/audit tools).
- Smith's working tree is its own clone at
  `~/.fsf/experimenter-workspace/Forest-Soul-Forge/`,
  separate from the operator's `~/Forest-Soul-Forge/`.
  Cycle 1 birth provisions this clone via `git clone` of the
  current commit; subsequent cycles reset to a fresh
  `experimenter/cycle-N` branch off `main`. Operator reviews
  via `git fetch experimenter-workspace experimenter/cycle-N`
  + diff viewer in the display pane.

This is the actual sandbox boundary. It is NOT a
process-level sandbox — Smith's tools execute in the daemon's
process space with the operator's filesystem permissions. The
boundary is git + path constraints. Real, but not bulletproof
against an adversarial agent with creative tool composition.
The audit chain is the post-hoc detector.

### Decision 4 — Frontier provider preference

Smith's birth profile sets `default_provider: frontier`. Every
`llm_think` dispatch routes to Anthropic (claude-sonnet-4-6 by
default; operator can swap via Smith's constitution YAML).
Local provider remains as fallback if frontier is unreachable.

Cost guardrail: Smith's `task_caps.usage_cap_tokens` defaults to
`100000` per session. Hits cap → dispatcher refuses with
`task_usage_cap_exceeded`. Operator raises explicitly per
cycle when needed.

### Decision 5 — Display-mode UI

New pane in the chat tab labeled "Cycles" (sibling to
"Conversations" + "Settings"). Layout:

- **Top:** posture toggle for Smith (red/yellow/green) + a
  "fire next cycle" button that sets `task_caps.mode=work`
  and dispatches.
- **Middle:** list of recent cycles, each row showing
  cycle_id, target (the backlog item or operator-stated
  goal), status (running/passed/failed/awaiting_review),
  branch name, and an expand control.
- **Per-cycle expand:** the cycle report (markdown rendered),
  the diff (via `git_diff_read.v1` against `main`), tests
  output (pass/fail summary), and Smith's
  `requested_tools` list — tools/skills it built and wants
  added to its kit.
- **Bottom of expand:** Approve / Deny / Counter-propose
  buttons. Approve triggers `git merge experimenter/cycle-N
  --no-ff` + `tools_add` for any requested tools (with
  per-tool yes/no checkboxes) + a `memory_tag_outcome.v1`
  call with `outcome=good` against the cycle's procedural-
  shortcut row. Deny triggers `outcome=bad`. Counter-propose
  opens an inline text editor that becomes the operator's
  feedback message routed to Smith's next explore-mode tick.

### Decision 6 — Self-augmentation flow

Smith proposes new tools via a structured cycle-report block:

```yaml
requested_tools:
  - name: log_correlation_window
    version: "1"
    side_effects: read_only
    reason: |
      Cycle 12 found I need a windowed log correlation that
      doesn't exist in the catalog. I built the implementation
      in src/forest_soul_forge/tools/builtin/log_correlation_window.py
      + tests. Operator approval grants it to my kit so cycle
      13 can use it.
    files:
      - src/forest_soul_forge/tools/builtin/log_correlation_window.py
      - tests/unit/test_log_correlation_window.py
      - config/tool_catalog.yaml  # patch adding the entry
```

Operator approval triggers:

1. Smith's branch already contains the source. Merge it.
2. Patch `config/tool_catalog.yaml` to register the tool
   (Smith's branch already includes this patch).
3. Restart the daemon (or hot-reload the catalog via
   `POST /tools/reload`).
4. `POST /agents/smith/tools/add` with `name=log_correlation_window`,
   `version=1`. This is the kernel's existing constitution-mutation
   path (ADR-0019 T-something). `constitution_hash` for Smith
   updates — but per ADR-0001 D2 the hash is immutable per
   IDENTITY, not per CONSTITUTION. The agent's instance_id and
   DNA stay constant; only its kit grows.

Per ADR-0001 D2 verification: this is the SAME mutation path
the operator uses today via the existing `tools_add` endpoint.
Smith doesn't bypass anything — it just packages the request.

### Decision 7 — Posture default + operator swap surface

Smith births at posture YELLOW. Every non-read-only dispatch
queues for operator approval via the existing pending-call path
(ADR-0019 T3). The chat-tab existing posture toggle (red /
yellow / green badge clickable in the agent header) works as-is
for Smith — no new UI, just point the existing widget at
Smith's instance_id.

GREEN posture risk: full kit auto-fires within genre cap.
For Smith with engineer genre + ENGINEERING_HEAVY kit-tier, that
includes shell_exec.v1 — operator should only flip GREEN after
several clean cycles establish trust. Until then, YELLOW is the
default and every action is a one-click confirm.

RED posture: refuses every non-read-only call. Smith can still
explore (read-only mode passes posture clamp), but cannot
mutate anything. Useful for "I want Smith to keep gathering
ideas while I'm asleep, not act on them."

## Implementation Tranches

- **E1** — birth Smith. Constitutional kit per Decision 1,
  branch-isolation constraint per Decision 3, frontier
  preference per Decision 4, posture YELLOW default. Provision
  the experimenter-workspace clone. Land as B187.
- **E2** — `ModeKitClampStep` + `task_caps.mode` plumbing per
  Decision 2. Dispatcher pipeline insertion. Tests:
  explore-mode refuses code_edit, work-mode passes through,
  display-mode refuses everything except the read-only
  review subset. Land as B188.
- **E3** — explore-mode scheduled tasks in
  `scheduled_tasks.yaml`. Conservative cadence: hourly
  during waking hours (configurable per-operator), off
  overnight by default. Land as B189.
- **E4** — display-mode chat-tab pane per Decision 5.
  Frontend-heavy. Land as B190.
- **E5** — self-augmentation flow per Decision 6 — operator
  approval triggers automated merge + `tools_add`. Land as
  B191.
- **E6** — operator safety runbook
  (`docs/runbooks/experimenter-cycle-runbook.md`) covering
  what to look for in cycle review, when to flip RED, how
  to abandon a cycle (delete the branch, weaken the
  shortcut). Land as B192.

E1 + E2 + E3 is enough to run a first cycle end-to-end. E4
makes review pleasant; E5 closes the recursive loop; E6 is
operational polish.

## Consequences

**Positive:**

- One agent, one set of tracks, one posture toggle. The
  three-mode system is operator-controlled per-dispatch
  rather than operator-context-switching across three
  identities.
- Reuses every existing kernel substrate. No schema bump.
  No new audit event types. The branch-isolation boundary is
  a YAML config, not a new architecture.
- Frontier provider verified working end-to-end as of B185 +
  the 2026-05-07 smoke test. Smith inherits that wiring; no
  additional plumbing needed.
- The display-mode pane gives the operator a clear "what
  happened in cycle N + do I approve it" surface. The
  reinforcement loop closes via the existing ADR-0054 T5
  `memory_tag_outcome.v1` substrate — Smith's procedural
  shortcuts strengthen on operator approval, weaken on
  denial.

**Negative:**

- Path-and-branch isolation isn't process isolation. A
  sufficiently-creative Smith dispatch could in principle
  compose tools to escape the constraints. The defense is
  audit-chain visibility plus posture clamps plus operator
  review — adequate for "let it try and see," not adequate
  for unattended autonomy.
- Frontier provider costs API spend on every llm_think
  dispatch in work mode. The 100k token usage_cap is the
  per-session brake; operator-set per-task caps are the
  finer-grained brake.
- Self-augmentation broadens the attack surface every
  cycle. Display-mode review is the gate; if the operator
  approves a malicious tool, that's on the operator. The
  marketplace's three-layer trust model (ADR-0055
  Decision 5) doesn't apply here because Smith is building
  its OWN tools — there's no third-party signature to verify.
  This is the right trade-off (Smith's tools live in the
  operator's repo with a clear `git blame` trail) but
  worth flagging.

**ADR-0001 D2 verification:** Smith has ONE
constitution_hash + ONE DNA bound at birth. The
`tools_add` flow doesn't change either — it only grows the
agent's `tools[]` list, which is per-instance state. Identity
invariance preserved.

**ADR-0044 D3 verification:** No kernel ABI changes. New
pipeline step + new task_caps.mode field + new constitution
shape are all additive. Pre-E2 daemons reading post-E2
constitutions just ignore the unknown mode tag and dispatch
through the rest of the pipeline as before.

**ADR-0008 verification:** Frontier provider is opt-in per
Smith's constitution `default_provider: frontier`. Other
agents stay on local provider. The operator's
"medical/therapeutic data must not leak" framing is preserved
— Sage (operator-facing assistant) still routes to local
qwen2.5-coder:7b; Smith routing to Claude is the Smith-specific
opt-in.

## Followups — first-cycle trial findings (2026-05-07)

The first work-mode cycle dispatch went through four iterations
(v1 → v4) with operator-side path validation between each. The
runtime did exactly what E2's ModeKitClampStep + frontier routing
were supposed to do: every dispatch landed cleanly, mode-clamped
to work, routed to claude-sonnet-4-6, audit-chained. The
substrate is sound. What surfaced is a gap in the *iteration
model*, not the runtime.

**Finding 1 — cycle dispatches are stateless across versions.**
Each cycle revision is a fresh `llm_think` call. Smith's prior
plan output is not threaded into the next cycle's prompt
automatically — there's no machinery that says "here is what you
proposed last time, here is the operator's review, revise." When
the operator asks for a "minimal fix to v3," Smith fabricates
what v3 might have looked like rather than revising the actual
v3.

Concrete evidence from the v4 dispatch
(`dev-tools/smith-cycle-1-plan-response-v4.json`): Section 2 of
the response presents a diff whose "before" side does not match
v3's actual `_seed_conversation` helper, and Section 3
("revised file") is an entirely different file targeting an
imagined `client.respond()` method on a non-existent
`ForestSoulForgeClient` class. The kwargs-fix instruction was
applied correctly in isolation; the surrounding structure
collapsed because Smith had no v3 in context.

By contrast, the v2 → v3 jump worked cleanly because the operator
embedded the full ground-truth code blocks (endpoint source,
fixture pattern source, real schema) directly in the v3 prompt.
Smith iterated against in-prompt material correctly. Iteration
failed once that scaffolding was removed.

**Finding 2 — frontier routing is markedly higher-quality than
local.** Cycle 1.1 on local qwen2.5-coder:7b produced
specifics-light output (vapor target: HSM adapter for a
non-existent tool). Cycle 1.2 on claude-sonnet-4-6 (post-B193
provider override) produced structured, locatable, partially-
runnable output. The 2.3x richer output translates directly to
a target the operator can actually evaluate.

**Finding 3 — Smith's `min_confidence_to_act=0.55` correctly
flags but does not stop.** Across all four cycles, Smith's
"Risks / blast radius" sections accurately identified at least
one of the gaps in the corresponding plan (vapor target in 1.1,
patch-target uncertainty in 1.2, interface-kwargs uncertainty in
1.3, none in 1.4 because Smith had lost the thread entirely).
This is the right behavior for a YELLOW-posture experimenter:
flag the risk, let the operator gate. The constitution's
approval-for-destructive-changes clause is doing its job.

**Decision: file follow-up tranche E7 — prior-cycle artifact
threading.**

When the operator fires a revision dispatch (cycle N → cycle N+1
on the same target), the daemon should:

1. Look up the prior cycle's plan output by `(instance_id,
   cycle_id)` from the audit chain.
2. Inject the prior plan's full text into the next dispatch's
   system message under a clearly-labeled `<prior_cycle>` block.
3. Inject the operator's review notes (the `decision` payload
   from `POST /agents/{id}/cycles/{cycle_id}/decision`) under
   `<operator_feedback>`.
4. Cap the threaded context at ~8k tokens — older cycles get
   summarized rather than dropped, preserving the iteration
   trail.

E7 is gated on Smith doing a few more cycles in the current
flat-prompt mode so we collect more data on where the iteration
model breaks down. Operator preference: ship E7 once we have
evidence from at least 3-4 distinct targets, not just one.

**Finding 4 — verbatim wrappers restore literal-copy compliance.**
Cycle 1.5 confirmed the prior-cycle-threading fix (Finding 1) but
exposed a separate failure mode: even with the v3 file body
embedded in-prompt, when the operator wrote out the corrected
helper kwargs in plain prose, Smith paraphrased them. He
substituted `id=conversation_id, session_tag="agent-test-001"`
for the explicit `domain="general", operator_id="test-operator",
conversation_id=conversation_id` from the prompt. Neither `id`
nor `session_tag` is a parameter of `create_conversation` — pure
hallucination, but with the structural shell of v3 still intact.

Cycle 1.6 tested whether a `<copy_verbatim id="helper">` block
with explicit "copy character-for-character; deviation fails the
cycle" framing would stop the paraphrasing. It did. Smith quoted
the block back verbatim in section 2 of his response, used the
exact kwargs in section 3, and self-audited the match in
section 5. Verbatim wrappers belong alongside `<prior_cycle>`
threading in the E7 prompt-construction toolkit.

**Finding 5 — Smith's tests caught a real bug.** The test file
v6 produced (helper from cycle 1.6, structure from cycle 1.3,
audit.append assumptions verified by operator) ran 3/4 green
on first apply. The remaining failure was
`test_most_recent_wins_with_multiple_matching_events`: the
endpoint was returning the OLDEST matching shortcut, not the
newest. Root cause: B195's route handler did
`for entry in reversed(entries)` against
`audit.tail(200)`, but `tail` already returns newest-first
(line 367 of audit_chain.py: `return list(reversed(keepers))`).
The route was double-reversing into oldest-first iteration and
returning the FIRST oldest match. Fix: drop the `reversed()`
call, iterate `entries` directly. Without Smith's test, this
would have shipped to operators and reinforced stale shortcuts
on chatty conversations.

**Decision: cycle 1 closes 4/4 green.** Final state:
- `tests/unit/test_last_shortcut_route.py` — Smith's structure +
  operator-supplied helper + `allow_write_endpoints=True` fixture
  fix (audit chain initialization is gated on that flag in
  `daemon/app.py` L229; flipping it is required for any test that
  exercises the chain).
- `routers/conversations.py` L961 — dropped `reversed(entries)`,
  iterate directly. Bug introduced in B195, surfaced by Smith's
  test, fixed in cycle 1 close.

Cycle 1 produced more value than the test file alone: the
iteration trail discovered two prompt-engineering primitives
(Findings 1 + 4) that should be wired into the E7 cycle dispatcher,
and the test itself caught a B195 regression. Pattern to repeat:
Smith picks small undertested surfaces, operator iterates the plan
with prior-cycle threading + verbatim wrappers, the green test on
disk closes the loop and may surface real bugs along the way.
