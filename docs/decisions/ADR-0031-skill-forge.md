# ADR-0031 — Skill Forge

- **Status:** Accepted (promoted 2026-04-30 — Phase A audit §A-2; see docs/audits/2026-04-30-comprehensive-repo-audit.md). Skill Forge — T1, T2a/T2b, T5, T7, T8 shipped + 26 manifests live.
- **Date:** 2026-04-27
- **Related:** ADR-0030 (Tool Forge — Skill Forge composes tools that ADR-0030 produces), ADR-0019 (tool execution runtime — skill execution dispatches each step through the same runtime), ADR-0021 (genres — skills inherit constraint posture from the agent that runs them), ADR-0022 (memory subsystem — skills are the natural granularity for memory checkpoints), ADR-0024 (horizons — Skill Forge is the H1 "wow" feature alongside Tool Forge).

## Context

Tools are **primitives**. ADR-0030's Tool Forge produces them: one input, one output, one well-defined side-effect class. Tools answer "what can I do?"

What an agent actually *does* in a session is rarely a single tool call. It's a **composition** — fetch this, summarize that, classify the result, notify the operator if a threshold is breached. Today the composition lives in:

- An LLM's chain-of-thought (ad-hoc, not auditable, drifts every run).
- A hand-written Python script the operator maintains (one-off, doesn't share constraints with the agent's runtime, no audit trail).
- The orchestration logic of an agent's prompt (mixed in with reasoning; can't be replayed).

None of those compose with the rest of the architecture. A composition needs to be:

- **A first-class artifact** — versioned, hash-pinned, content-addressed, audit-logged, like every other thing in the project.
- **Replayable** — the same skill on the same inputs produces the same dispatched-tool sequence.
- **Constraint-respecting** — every tool call inside a skill goes through the runtime's normal constraint + genre + counter machinery. The skill doesn't have its own privileges.
- **Forgeable** — operator describes "what should happen when X" in English, Skill Forge proposes a manifest, operator reviews + installs.

Skill Forge is what turns one-off compositions into reusable, auditable, replayable **skills**.

A skill is to a tool what a function is to a CPU instruction. The runtime is the CPU; tools are instructions; skills are functions written in those instructions.

## Decision

A **skill** is a YAML manifest that describes a **DAG of tool calls** with declarative data flow. Skill Forge is the LLM-assisted authoring pipeline (mirroring ADR-0030's Tool Forge stages); the **skill runtime** is a small interpreter that walks the DAG and dispatches each step through the existing tool runtime.

### Why declarative YAML, not Python

A skill could be a Python function calling `dispatcher.dispatch(...)` over and over. We chose YAML manifests instead, for these reasons:

- **Audit-chain affinity.** A YAML manifest hashes cleanly. `skill_hash` becomes part of every `skill_invoked` audit entry the same way `constitution_hash` is part of `agent_created`.
- **LLM-emittable.** Skill Forge's stage 2 needs the LLM to emit a manifest from English. YAML is robustly emittable; Python from an LLM has 30× the failure modes (syntax, imports, sandbox escapes).
- **Diffable.** Operator review is a `git diff` of YAML, not a code review of generated Python.
- **Constraint-friendly.** Static analysis of "which tools does this skill call?" is a YAML query, not an AST walk.
- **Escape hatch.** A skill step *can* call a `run_python.v1` tool with an inline snippet for genuinely imperative cases, but the call is a tool dispatch — it goes through the runtime, gets an audit entry, respects the agent's constraints. The escape hatch doesn't bypass the system.

### Skill manifest shape

```yaml
schema_version: 1
name: scan_pcap_for_anomalies
version: "1"
description: |
  Walk a packet capture, identify the busiest source IPs in the last hour,
  cross-reference them against a known-bad list, and notify the operator
  if any matches are found.
forged_at: "2026-04-27T18:30:00Z"
forged_by: "alex"
forge_provider: "frontier:claude-3-7-opus"
forge_prompt_digest: "sha256:..."

# Required tools — checked at install time. Dispatching the skill
# without all required tools available raises BEFORE the first step.
requires:
  - timestamp_window.v1
  - flow_summary.v1
  - threat_intel_lookup.v1   # forged via ADR-0030 if missing
  - notify_operator.v1

# Inputs the skill accepts. Validated against the schema before any
# step runs.
inputs:
  type: object
  required: [pcap_path]
  properties:
    pcap_path: {type: string}
    window:    {type: string, default: "last 1 hours"}

# The DAG. Each step has a unique id; later steps reference earlier
# step outputs via ${step_id.field} interpolation.
steps:
  - id: window
    tool: timestamp_window.v1
    args:
      expression: "${inputs.window}"

  - id: top_ips
    tool: flow_summary.v1
    args:
      pcap: "${inputs.pcap_path}"
      start: "${window.start}"
      end:   "${window.end}"
      group_by: source_ip
      limit: 10

  # Branching — `for_each` runs the inner steps once per element.
  # Elements are exposed as ${each.<field>}.
  - id: cross_reference
    for_each: "${top_ips.results}"
    steps:
      - id: lookup
        tool: threat_intel_lookup.v1
        args:
          ip: "${each.source_ip}"
        # `unless` lets the step skip itself based on a predicate
        # over previously-bound names. Cheap escape from running
        # an LLM-cost tool when the input is obviously safe.
        unless: "${each.is_internal}"

  # Conditional — `when` runs the step only if the predicate holds.
  - id: alert
    when: "any(cross_reference.lookup.matched_known_bad)"
    tool: notify_operator.v1
    args:
      severity: high
      summary: "matched ${count(cross_reference.lookup.matches)} known-bad IPs"
      detail:  "${cross_reference.lookup.matches}"

# What the skill returns to its caller. Same interpolation language.
output:
  windowed_top_ips: "${top_ips}"
  alert_fired:     "${alert.fired or false}"
  matches:         "${cross_reference.lookup.matches}"
```

### Interpolation language

A small, deliberately-limited expression language. Designed so an audit-chain reader can verify "this skill could only ever call these tools with these arg shapes":

- `${step_id.field}` — bind a value from a previous step's output.
- `${inputs.field}` — bind a skill input.
- `${each.field}` — inside `for_each`, the current iteration element.
- Functions: `count(list)`, `any(list)`, `all(list)`, `len(string)`, `default(value, fallback)`. Closed list. No arbitrary Python.
- Comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`.
- Boolean: `and`, `or`, `not`.

No loops, no string concatenation operators, no arithmetic beyond what's needed for `count()`. The expression evaluator is ~150 lines.

### Skill runtime

The skill runtime is an interpreter. It:

1. **Validates inputs** against the manifest's input schema.
2. **Walks steps** in declaration order. Each step:
   - Resolves args via the interpolation language.
   - Dispatches the tool through the existing tool runtime (so constraints, genre, audit, counter, accounting all apply normally).
   - Binds the result under the step's `id`.
3. **Handles control flow:**
   - `when` — skip the step if the predicate is false.
   - `unless` — same, inverted.
   - `for_each` — iterate over a list, dispatching the inner steps once per element.
4. **Emits audit events** at skill granularity in addition to the per-tool dispatch events.
5. **Returns the skill's output** assembled per the manifest's `output:` block.

```
                        skill_invoked  ← skill-level "we started"
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
    skill_step_started  skill_step_started  ...
            │                 │
   tool_call_dispatched  tool_call_dispatched
   tool_call_succeeded   tool_call_succeeded
            │                 │
    skill_step_completed skill_step_completed
                              │
                              ▼
                       skill_completed
```

A skill that fails mid-execution emits `skill_step_failed` and `skill_completed` with `outcome=failed`. Steps that were skipped emit `skill_step_skipped`. The audit chain has the full execution trace.

### How the constraint model layers

The skill itself **doesn't have its own constraints**. Every tool call inside the skill goes through the agent's existing constraint policy. If the agent's constitution says `notify_operator.v1` requires human approval, the skill execution pauses on the `alert` step the same way a direct tool call would — the approval queue (ADR-0019 T3) gets a ticket, the operator approves or rejects, the skill resumes.

Skills do, however, get their own **per-skill audit cap** (analogous to `max_calls_per_session` for tools). Configured per-agent in the constitution: "this agent can run no more than 50 skills per session." Catches a runaway skill loop.

### Skill catalog

Mirrors `tool_catalog.yaml`:

```yaml
# config/skill_catalog.yaml
schema_version: 1
skills:
  - name: scan_pcap_for_anomalies
    version: "1"
    skill_hash: "sha256:..."
    description: "Walk a packet capture, identify..."
    requires_tools: [timestamp_window.v1, flow_summary.v1, ...]
    archetype_tags: [network_watcher, anomaly_investigator]
    # Skills can be runnable by certain genres only (analogous to genre
    # kit defaults for tools).
    runnable_by_genres: [observer, investigator]
    skill_path: "data/skills/installed/scan_pcap_for_anomalies.v1.yaml"
```

Loaded at lifespan onto `app.state.skill_catalog`. The catalog is also content-addressed: a skill's hash is over its manifest, so two skills with the same logic have the same hash regardless of file metadata.

### Skill Forge pipeline (mirrors ADR-0030)

```
1. DESCRIBE    Operator types: "When the network watcher sees a
               new IP that isn't in our allowlist, look it up in
               threat intel and ping me on Slack."
                  ▼
2. PROPOSE     LLM emits a candidate manifest. Crucial: it lists
               the required tools; if any don't exist, the operator
               is offered Tool Forge to fill the gap before
               continuing. Skill Forge cooperates with Tool Forge.
                  ▼
3. CODEGEN     For skills, codegen IS the manifest emission; no
               separate Python step. The manifest itself is the
               artifact.
                  ▼
4. REVIEW      Operator reads the YAML, edits as needed.
                  ▼
5. PROVE       Run the skill in a dry-run mode against a synthetic
               capture / a stub provider. Each step's output is
               surfaced to the operator before commitment. Tool
               calls happen through normal runtime → audit chain
               records the dry-run as `skill_invoked` with
               `dry_run=true` flag.
                  ▼
6. INSTALL     Manifest written to data/skills/installed/.
               Catalog YAML diff. Audit `forge_skill_installed`.
```

### New audit event types

```
forge_skill_proposed   { skill_name, version, description, requires_tools,
                         forged_by, forge_provider }
forge_skill_installed  { skill_name, version, skill_hash,
                         proposed_seq, forged_by, installed_at }
skill_invoked          { skill_name, version, instance_id, session_id,
                         args_digest, dry_run }
skill_step_started     { skill_invoked_seq, step_id, tool_key,
                         resolved_args_digest }
skill_step_completed   { skill_invoked_seq, step_id, tool_call_seq,
                         duration_ms }
skill_step_skipped     { skill_invoked_seq, step_id, reason }
skill_step_failed      { skill_invoked_seq, step_id, exception_type,
                         exception_message }
skill_completed        { skill_invoked_seq, outcome, output_digest,
                         total_steps, executed_steps, skipped_steps,
                         failed_step_id }
```

That's seven new event types. Same `KNOWN_EVENT_TYPES` machinery as the tool runtime — additions are forward-compat (verifier tolerates) but added explicitly so they're not flagged as unknown.

### Storage layout

```
data/skills/
  staged/              # forged but not yet installed
    <name>.v<version>/
      manifest.yaml
      forge.log
      dry_run_results.yaml
  installed/           # canonical, loaded into the catalog
    <name>.v<version>.yaml
```

### CLI surface (v0.1)

```bash
$ fsf forge skill "When watcher sees an unknown IP, look it up and ping me"
[Skill Forge] proposing manifest via local:llama3...
  name: alert_on_unknown_ip
  version: 1
  requires_tools:
    - flow_summary.v1                 ✓ in catalog
    - threat_intel_lookup.v1          ✗ not found — offer Tool Forge?
    - notify_operator.v1              ✗ not found — offer Tool Forge?

Forge missing tools? [y/N] y
[Tool Forge] forging threat_intel_lookup.v1...
  ... (full ADR-0030 flow runs in-process)
[Tool Forge] forging notify_operator.v1...
  ...

[Skill Forge] resuming with all required tools available.
Continue? [y/N/edit] y

[Skill Forge] dry-running against synthetic input...
  step "window"            ran in 2ms, returned a 1-hour window
  step "top_ips"           ran in 12ms, returned 10 source IPs
  step "cross_reference"   ran 10× via for_each, 1 match
  step "alert"             would have fired (dry_run, suppressed)

Review:  data/skills/staged/alert_on_unknown_ip.v1/
Install? [y/N] y
[Skill Forge] installed → data/skills/installed/alert_on_unknown_ip.v1.yaml
[Skill Forge] catalog diff written → data/skills/staged/.../catalog-diff.yaml
[Skill Forge] audit_seq=271 forge_skill_installed
```

### Frontend surface (deferred)

A "Skill Forge" tab on the frontend wraps the same pipeline with a richer review pane. The pane shows the DAG visually (each step a node, data flow arrows between them), the dry-run results inline. Same audit events.

A separate "Skills" tab lists installed skills + lets the operator invoke one (`POST /agents/{id}/skills/run`).

## Implementation tranches

- **T1** — Manifest schema + parser + interpolation language + validator. Standalone module: feed it a manifest YAML, get a parsed `SkillDef`. No runtime yet.
- **T2** — Skill runtime. Walks a `SkillDef` step-by-step, dispatches each tool through the existing `ToolDispatcher`. Emits all 7 new audit events. No CLI yet.
- **T3** — `POST /agents/{instance_id}/skills/run` endpoint. Body: `{skill_name, version, args, session_id}`. Returns a skill outcome (mirrors tool dispatch). Same write-lock discipline.
- **T4** — `fsf forge skill` CLI v0.1: propose-only mode (LLM emits manifest, operator reviews + saves to staged/, no install/run). Cooperates with Tool Forge for missing primitives.
- **T5** — Skill catalog loader. `app.state.skill_catalog`. Skills available to runtime.
- **T6** — Dry-run mode + `--prove` flag in CLI. Skill runs end-to-end against stub provider/synthetic inputs, writes `dry_run_results.yaml`.
- **T7** — Install path. Manifest moves to `installed/`, catalog YAML diff applied, daemon picks it up on next boot. Hot-reload deferred to T9.
- **T8** — Frontend "Skills" tab (invoke installed skills) + "Skill Forge" tab (author new ones).
- **T9** — Hot-reload. Daemon watches `data/skills/installed/`, reloads catalog on file change.
- **T10** — Skill-level approval (skills tagged `requires_human_approval: true` in the manifest pause at the agent's first execution; operator approves the whole skill run, like tools). Distinct from per-tool approval inside the skill.

## Trade-offs and rejected alternatives

**Declarative YAML vs. Python.** Declarative. Stated reasons above. The escape hatch is `run_python.v1` as a tool, dispatched through the runtime, audited like any other call. We accept the friction of building a small expression language; the audit + replay properties are worth it.

**Sequential vs. parallel step execution.** Sequential for v1. Parallel `parallel:` blocks are a future tranche; they need careful audit-chain ordering semantics (do parallel branches share a chain? Branch off and rejoin?). Not v1.

**Skill-level constraints vs. inherit-from-agent.** Inherit. Adding a separate constraint layer for skills doubles the constraint resolution machinery for a marginal gain. Keep one source of truth: the agent's constitution.

**Skills calling other skills.** Yes — a step's `tool:` field can be `skill:other_skill.v1`. The skill runtime detects the namespace and recurses. Audit chain captures parent-skill-seq → child-skill-seq the same way it does for `delegate_to_agent.v1`. Recursion limit lives in `max_skill_depth` constitution constraint (default 5).

**LLM emits Python directly for skills.** Considered for the case where YAML is too restrictive. Rejected for v1: the audit + replay + LLM-friendliness wins outweigh the expressivity loss. A Python escape hatch via `run_python.v1` covers the cases YAML can't.

**Why not just use existing workflow engines (Airflow, Temporal, etc.)?** They're optimized for distributed long-running jobs. Skills here are short-lived, inside-an-agent-session compositions. Pulling in a workflow engine is a 10× scope increase for needs that fit in 500 lines.

## Consequences

**Positive.**
- Compositions become first-class artifacts: hash-pinned, audited, replayable.
- LLM agents can invoke a skill instead of orchestrating tool calls themselves — the skill's logic is fixed, the LLM's reasoning is just "which skill?"
- Skill Forge + Tool Forge together close the loop: operator describes a goal in English, gets a skill that calls (possibly newly forged) tools.
- Memory subsystem (ADR-0022) gets a natural granularity: a memory checkpoint per skill invocation.
- Marketplace (Horizon 3) gets two product axes: forged tools and forged skills.

**Negative.**
- Two new authoring surfaces (Tool Forge + Skill Forge) double the surface area to maintain.
- Skill expressivity is bounded by the interpolation language. Some compositions need the Python escape hatch — that's a friction surface.
- Versioning across the layer boundary is tricky: skill v1 references tool v1; if a tool's API changes (v1 → v2), the skill manifest may need a corresponding bump or a tool version pin.

**Neutral.**
- Operators have to learn the manifest schema. The Forge pipeline mostly hides it; direct-authoring operators read the docs once.

## Cross-references

- ADR-0030 — Tool Forge (this ADR's primary collaborator)
- ADR-0019 — tool execution runtime (this ADR's executor)
- ADR-0021 — genres (constraint inheritance)
- ADR-0022 — memory subsystem (skill-granular checkpoints)
- ADR-0024 — horizons (Skill Forge is H1)
- ADR-0026 — provider economics (forge codegen cost)
- docs/notes/skill-and-tool-trees.md — original prior-art note
