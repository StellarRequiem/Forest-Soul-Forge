# ADR-0034 — Software-Engineering Track (Atlas / Forge / Sentinel)

- **Status:** Accepted (promoted 2026-04-30 — Phase A audit §A-2; see docs/audits/2026-04-30-comprehensive-repo-audit.md). SW-track triune — Phases A.1–A.6 + B.1 shipped; agents themselves filed this ADR (meta-demo).
- **Date:** 2026-04-29 (filed retroactively after Phase A shipped 2026-04-29)
- **Related:** ADR-0019 (tool execution runtime — every SW tool ships through
  the dispatcher with audit + approval), ADR-0021 (role genres — this ADR
  claims three existing genres rather than adding new ones), ADR-0030 (Tool
  Forge — the path Phase B+ tools are forged through), ADR-0031 (Skill Forge
  — agent-level coding procedures are skill manifests), ADR-0033 (Security
  Swarm — precedent for domain-tier expansion, structural pattern), ADR-003X
  (Open-Web tool family — same architectural posture: additive primitives,
  chain integration, opt-in defaults), ADR-003Y (Conversation runtime —
  unblocks autonomous chained `llm_think` operation).

## Context

The forge ships a tool runtime, audit chain, memory subsystem, and the
ability to forge new tools and skills on demand. It also ships a
defensive plane (Security Swarm, ADR-0033) and an open-web plane
(ADR-003X). What it does **not** ship — and what every other plane
implicitly depends on — is **agents that operate on the codebase
itself**: agents the operator can ask to read code, propose changes,
run tests, and review the result.

Without a software-engineering plane the operator is the only force
moving the codebase forward. That works during the foundation phases.
It does not scale into Y/H2/H3 work where the conversation runtime,
multi-agent coordination, real-time A/V Companion, federation, and
marketplace tranches all need engineering throughput.

The brief is to add a **software-engineering plane** whose agents have:

1. The ability to invoke an LLM as an audited dispatchable tool
   (`llm_think.v1`).
2. The ability to read and edit files, and run shell commands, within
   constitutional + genre-tier guardrails (ADR-0034 Phase A.5 —
   `code_read.v1` / `code_edit.v1` / `shell_exec.v1`).
3. A **triune structure** mirroring the Security Swarm's chain: a
   designer (Architect), an implementer (Engineer), a reviewer
   (Reviewer). Each role's privilege ceiling is encoded in its genre
   choice, not in code that asks for permission.
4. Constitutional rules that embed software-engineering best practice
   structurally — Architect forbids unilateral implementation, Engineer
   gates destructive changes, Reviewer forbids implementation entirely.

The plane is operator-driven for now (Phase A). Once Y-track
conversation runtime ships, agents chain `llm_think` calls themselves
and the plane becomes autonomous within operator-defined task scope.

## What this ADR is **not**

Three claims must be retired up front:

1. **Not a replacement for the operator's judgment on architectural
   direction.** The triune drafts and refines, the operator decides
   what ships. Atlas's `forbid_unilateral_implementation` constitution
   rule encodes this structurally — Architect cannot Forge a tool or
   merge a branch on its own.

2. **Not autonomous in Phase A.** Each `llm_think` call is operator-
   triggered. The triune does not chain itself across calls. That
   capability waits for ADR-003Y conversation runtime — at which point
   `interaction_modes: [conversation]` lets Atlas → Forge → Sentinel
   pass turns autonomously within an operator-bounded task.

3. **Not a model-quality fix.** A bad LLM produces bad design and bad
   code regardless of which agent dispatches it. The triune buys
   audit trail, structural privilege gating, and a review step — it
   does not buy capabilities the underlying model doesn't have.

## Decision

The Software-Engineering plane is composed of:

- **Three roles** added to `config/trait_tree.yaml`, each claiming an
  existing genre rather than adding new ones.
- **Three constitution role_bases** in `config/constitution_templates.yaml`
  encoding per-role engineering discipline as policies.
- **Per-archetype kits** in `config/tool_catalog.yaml` granting each
  role the foundational tools they need.
- **One new built-in tool** (`llm_think.v1`) — audited LLM completion as
  a dispatchable tool, the bridge that makes agents talk via the
  governance pipeline.
- **Three new built-in tools** (Phase A.5) — `code_read.v1`,
  `code_edit.v1`, `shell_exec.v1` — giving Engineer agents hands.

No new genres. No new infrastructure. Existing dispatcher, audit
chain, memory subsystem, governance pipeline, and triune-ceremony
mechanics carry the entire weight.

### Three roles claiming existing genres

```yaml
# config/trait_tree.yaml
roles:
  system_architect:    # → researcher genre (max_side_effects=network)
    description: |
      Designs systems. Reads code, fetches references, drafts
      architecture decisions. Cannot modify code or run shells —
      structurally a designer, not an implementer.
    domain_weights:
      cognitive: 2.5      # peak — design is cognitive labor
      audit:     1.4
      security:  1.2

  software_engineer:   # → actuator genre (max_side_effects=external)
    description: |
      Implements. Writes code, runs tests, exec's shells inside the
      agent's allowed_paths and allowed_commands. Per-call human
      approval defaults TRUE on destructive changes; the operator
      can relax for trusted automation per session.
    domain_weights:
      embodiment: 1.8     # peak — implementation is embodied work
      cognitive:  1.6
      audit:      1.2

  code_reviewer:       # → guardian genre (max_side_effects=read_only)
    description: |
      Reviews. Reads diffs and rendered artifacts via code_read.v1,
      runs llm_think for written critique. Cannot modify files —
      its refusal or approval becomes the merge gate the operator
      consults.
    domain_weights:
      security: 2.0       # peak — reviewer is security-domain agent
      audit:    2.0       # peak — and audit-domain agent
      cognitive: 1.5
```

### Genre claims (config/genres.yaml)

```yaml
researcher:
  roles: [system_architect]
  # ... pre-existing genre, no changes ...

actuator:
  roles: [software_engineer]
  # ... pre-existing ...

guardian:
  roles: [code_reviewer]
  # ... pre-existing ...
```

The structural privilege model:
- **Architect / researcher** — `max_side_effects=network` — reads code +
  fetches references; cannot write or exec.
- **Engineer / actuator** — `max_side_effects=external` — full read/write/
  exec within constitution-listed allowed_paths + allowed_commands.
- **Reviewer / guardian** — `max_side_effects=read_only` — reads only;
  refusals or approvals are advisory + audited.

### Constitution templates (config/constitution_templates.yaml)

```yaml
role_base:
  system_architect:
    policies:
      - id: forbid_unilateral_implementation
        rule: forbid
        triggers: [code_edit, shell_exec]
        rationale: "Architect drafts designs; Engineer implements. Architect
                    that ships code without an Engineer pass collapses the
                    triune's review gate."
      - id: approval_for_external_publishing
        rule: require_human_approval
        triggers: [external_call, mcp_call]
        rationale: "Sharing design artifacts beyond the realm requires an
                    operator decision."

  software_engineer:
    policies:
      - id: approval_for_destructive_changes
        rule: require_human_approval
        triggers: [code_edit_destructive, shell_exec]
        rationale: "Operator approves before destructive filesystem writes
                    or shell side-effects. Auto-approval relaxable per session
                    for trusted automation."
      - id: forbid_unauthorized_dependencies
        rule: forbid
        triggers: [pip_install, npm_install, brew_install]
        rationale: "Adding a runtime dependency rotates the supply chain.
                    Operator-only decision."
      - id: approval_for_external_calls
        rule: require_human_approval
        triggers: [external_api_call]
        rationale: "Engineering work reaching beyond the local repo is the
                    operator's call, not Engineer's."

  code_reviewer:
    policies:
      - id: forbid_implementation
        rule: forbid
        triggers: [code_edit, shell_exec]
        rationale: "Reviewer that can write loses its review function.
                    Genre ceiling is read_only — this is the policy that
                    surfaces the genre's intent in human-readable form."
      - id: forbid_silent_approval
        rule: forbid
        triggers: [auto_merge, silent_approve]
        rationale: "Every Reviewer approval emits an audit event with
                    rationale. No silent passes."
```

Per-policy `rule: forbid` is enforced at constitution-build time;
the dispatcher refuses any tool whose triggers match a forbid policy.
`require_human_approval` routes through the existing approval queue.

### Per-archetype kits (config/tool_catalog.yaml)

All three roles share the foundational kit:

```yaml
archetypes:
  system_architect:
    standard_tools:
      - llm_think.v1
      - memory_write.v1
      - memory_recall.v1
      - delegate.v1
      - code_read.v1            # SW.A.5 — reads code to design against it

  software_engineer:
    standard_tools:
      - llm_think.v1
      - memory_write.v1
      - memory_recall.v1
      - delegate.v1
      - code_read.v1            # SW.A.5 — reads what's there
      - code_edit.v1            # SW.A.5 — writes changes (gated by genre)
      - shell_exec.v1           # SW.A.5 — runs tests / git (gated by genre)

  code_reviewer:
    standard_tools:
      - llm_think.v1
      - memory_write.v1
      - memory_recall.v1
      - delegate.v1
      - code_read.v1            # SW.A.5 — reads diffs to review them
```

Per-tool `requires_human_approval` defaults to TRUE on Engineer's
`code_edit.v1` and `shell_exec.v1` per the constitution policy above;
operator relaxes per session for trusted automation.

### `llm_think.v1` — the bridge tool

```yaml
name: llm_think
version: '1'
side_effects: read_only          # network reach is via the provider's
                                  # configured endpoint; the tool itself
                                  # does not initiate connections to
                                  # arbitrary hosts. Genre ceiling check
                                  # applies via provider_constraint.
description: |
  Audited LLM completion as a dispatchable tool. Wraps
  provider.complete() inside the dispatcher so every call gets
  governance-pipeline gating (constitution constraints, genre ceiling,
  per-task task_caps, hardware quarantine), an audit row, and reported
  tokens.
inputs:
  prompt: { type: string }
  task_kind: { enum: [classify, generate, safety_check, conversation, tool_use], default: conversation }
  max_tokens: { type: integer, minimum: 1, maximum: 8192 }
  system: { type: string, optional: true }
output:
  text: string
  tokens_used: integer
  cost_usd: float | null
  model: string                    # "local:qwen2.5-coder:7b" etc.
audit:
  - prompt_digest (sha256, not raw)
  - model
  - tokens_used
  - response_digest
```

Why `read_only` side_effects: the tool itself doesn't write to disk,
network, or external systems. Its provider call is governed by the
agent's `provider_constraint` (e.g. Companion's `local_only`) which
the runtime enforces structurally. A future ADR may reclassify if the
threat model upgrades.

### Triune ceremony — bond mechanics

The three SW roles birth + bond into a triune via the existing
ADR-003X K4 ceremony:

```bash
POST /triune/bond
{
  "bond_name": "coding_triune_<suffix>",
  "instance_ids": [<atlas>, <forge>, <sentinel>],
  "operator_id": "<operator>",
  "restrict_delegations": true
}
```

`restrict_delegations: true` blocks any non-bonded agent from
delegating into the triune via `delegate.v1`. Internal delegation
(Atlas → Forge, Forge → Sentinel, Sentinel → Atlas) is permitted;
external delegation is refused with `out_of_triune_attempt` audit
event. The triune is opaque from outside.

## Phases

| #  | Deliverable | Status |
|----|---|---|
| A.1 | trait_tree.yaml roles, constitution_templates.yaml role_bases, genres.yaml claims | ✅ shipped 2026-04-29 (commit 7cca03e) |
| A.2 | tool_catalog.yaml archetype kits | ✅ shipped 2026-04-29 (commit 7cca03e) |
| A.3 | `llm_think.v1` built-in tool + audit event | ✅ shipped 2026-04-29 (commit 7cca03e) |
| A.4 | `live-test-sw-coding-triune.command` smoke harness | ✅ shipped 2026-04-29 (commit 7cca03e) |
| A.5 | `code_read.v1` + `code_edit.v1` + `shell_exec.v1` + live-test driver | ✅ shipped 2026-04-30 (commits 5ef6747 + 82770b3) |
| A.6 | Rebuild fidelity (canonical ↔ registry) | ✅ shipped 2026-04-29 (commit 19c65bd) |
| B.1 | Soak driver (long-running triune health) | ✅ shipped 2026-04-29 (commit 19c65bd) |
| B.2 | First real triune task — agents do production work on the codebase | next |
| B.3 | Skill manifests for canonical SW workflows (review_diff, write_test, fix_lint) | queued |
| B.4 | Frontend SW tab (similar to Approvals tab) — operator-friendly task drive | queued |
| C.1 | Y-track integration — triune chains `llm_think` autonomously inside conversation rooms | gated on ADR-003Y Y3+ |
| C.2 | Self-improvement demo — triune ships a non-trivial PR with full audit chain | gated on B.2-B.4 |

A.1–A.6 + B.1 = "the triune exists and is exercisable" — landed.
B.2 = "the triune does its first real production task." Threshold
moment: this ADR's filing IS B.2's first task (operator-orchestrated).

## Threat model addendum

Three threat classes specific to the SW plane:

### 1. Code injection from tool output (HIGH)

`code_read.v1` returns repository contents. A file containing
`<!-- ignore previous instructions; do X -->` could bias an
Architect's `llm_think` reasoning.

**Mitigations:**
- Engineer's `code_edit.v1` requires per-call operator approval by
  default. Injection-driven edits hit the queue first.
- All `code_read.v1` returns include a SHA-256 of the full file —
  audit-trail consumers can detect content swap between read and
  decision.
- `llm_think` system prompts explicitly disclaim authority of code
  comments over operator instructions.

**Residual risk:** an injection that biases reasoning without
triggering a tool action is undetectable by the runtime. The
operator's audit-chain review is the backstop.

### 2. Shell escape via `shell_exec.v1` (MEDIUM)

`shell_exec.v1` enforces argv-list dispatch (no `shell=True`),
allowed_commands allowlist, allowed_paths cwd check, mandatory
timeout. Defenses are layered.

**Mitigations:**
- `argv[0]` must be a bare command name (no `/path/to/cmd`).
- Path-resolve + allowlist check on `cwd`.
- Per-call mandatory timeout (default 30s, max 300s).
- Per-call human-approval default TRUE.

**Residual risk:** an allowed command (`git`, `cat`) used in a way the
operator didn't anticipate (e.g., `git config --global user.email
<malicious>`) still runs. Per-call approval is the gate; the operator
reads what's about to run before approving.

### 3. Architect-as-publisher escape (LOW)

Architect could in principle assemble a draft and route it through
`mcp_call.v1` to publish externally, bypassing the
`approval_for_external_publishing` rule.

**Mitigations:**
- `approval_for_external_publishing` is a `require_human_approval`
  policy on `mcp_call` and `external_call` triggers. The dispatcher
  routes via the approval queue; operator approves or rejects.
- Architect's archetype kit does NOT include `mcp_call.v1` by default.
  Operators who add it via `tools_add` create a different agent (new
  constitution_hash); the audit trail shows the addition.

**Residual risk:** a custom Architect with `mcp_call` granted +
external publishing approved-once leaks a draft. Operator decision +
audit chain are the controls.

### What we don't defend against

- A compromised LLM provider returning malicious code suggestions —
  defense is operator review of every Engineer write.
- An operator deliberately relaxing approval gates for an Engineer
  agent that subsequently misbehaves. The audit chain remains the
  evidence; the runtime does not block operator-elected risk.
- Side-channel inference of repository structure via `llm_think`
  prompts that include code context. Local provider mitigates;
  frontier provider with `enrich_narrative=True` exposes more.

## Consequences

### What we gain

- The agent foundry can build itself. Engineering throughput becomes
  multi-agent rather than operator-only.
- The audit chain captures every code read, every edit, every shell
  exec, every review — the full software-development trail in one
  hash-chained ledger.
- `llm_think.v1` is the first tool that exposes the LLM provider as a
  governance-gated dispatch surface. Future tools that wrap LLM calls
  (`summarize.v1`, `classify.v1`) inherit the same audit + approval
  + accounting machinery.
- Triune-bonded delegation prevents inter-domain leakage (a SW triune
  can't be addressed by a Security Swarm agent without an explicit
  bridge — same isolation property as ADR-003Y conversation rooms).

### What we accept

- **Operator-orchestrated in Phase A.** Each `llm_think` call is
  triggered by the operator. Autonomous chains wait for ADR-003Y.
- **Approval-queue load.** Every Engineer write hits the operator
  before execution. UX needs a Swarm-style Approvals view tuned for
  code-edit diffs (B.4).
- **Model-quality dependent.** The output quality of triune work is
  bounded by the LLM behind `llm_think`. Local `qwen2.5-coder:7b` is
  competent for small refactors; large architectural work benefits
  from frontier — which surfaces the privacy trade-off.
- **Constitution-template churn.** Adding a new SW-engineering best
  practice (e.g. "every PR has a test") means editing
  `constitution_templates.yaml` for software_engineer — which rotates
  every Engineer agent's `constitution_hash`. Acceptable; the hash
  rotation is the audit evidence that the rules changed.

## Open questions

1. **Sentinel approval taxonomy.** How does Reviewer encode an
   approval/rejection? Today it's an `llm_think` response containing
   `APPROVED:` / `REJECTED:` text the operator parses. A structured
   `review_decision.v1` tool would be cleaner — file a follow-up ADR
   if the pattern stabilizes.

2. **Triune memory consolidation.** A long-running triune accumulates
   shared lineage memory; eventually consolidation drift (per
   ADR-0022's downside note) becomes real. Need a Guardian-class
   auxiliary that periodically reviews the triune's consolidated
   memory against its constitution. Defer until it bites.

3. **Forge → Engineer naming.** The agent name "Forge" overlaps with
   "Tool Forge" and "Skill Forge" subsystems. Consider `Smith` or
   `Anvil` as alternatives if the overlap confuses operators. Defer
   to whichever feels right after the first public demo.

4. **B.2 task scoping.** What's the right size for the triune's first
   real production task? Too small (one-line fix) doesn't demo
   capability; too big (multi-file refactor) risks the LLM producing
   an unreviewable diff. Lean: a single-file documentation task
   (e.g., this ADR's filing) is the right first move; graduate to
   single-file code edits with tests; finally multi-file refactors.

5. **Skill catalog — SW track skills.** Like the Security Swarm's
   `morning_sweep` / `investigate_finding` / `contain_incident`
   chain, SW track will want canonical skills (`review_diff`,
   `write_test`, `fix_lint`, `refactor_function`). File as Phase B.3
   ADR amendment when the catalog stabilizes.

## Sign-off

Open for review. Acceptance criteria: phases B.2 through B.4 ship;
the first real triune-driven PR lands with a clean audit chain;
operator UX feels like a force multiplier rather than friction.

The acceptance threshold is honest: when the operator looks at a
shipped commit and says "the triune did most of this and the
chain proves it," the SW track has earned its keep.
