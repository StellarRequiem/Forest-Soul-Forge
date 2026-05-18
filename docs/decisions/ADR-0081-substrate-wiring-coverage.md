# ADR-0081 — Substrate wiring coverage + wiring_sentinel

**Status:** Proposed
**Date:** 2026-05-18
**Tracks:** Observability / Substrate hygiene
**Supersedes:** none (extends ADR-0079 diagnostic harness)
**Builds on:** ADR-0079 (the 14-section harness), ADR-0080 (the
per-agent capability tree), ADR-0064 (telemetry pipeline)
**Unblocks:** ongoing operator confidence that "wired in catalog"
matches "wired end-to-end"

## Context

B363 wired 6 LLM tools into `config/tool_catalog.yaml` so that
the 9 skills referencing them would resolve their `requires:`
list at parse time. Section-02 of the harness passed (skill
manifests). The catalog had what skills needed.

What was MISSED: those tools were not added to any archetype's
`standard_tools` list. So every agent born after B363 — including
the entire 9-agent Security Swarm — was born with a constitution
that didn't carry the new tools. The Capabilities tab (B381)
surfaced this 2026-05-18 when the operator opened it and saw
9 skills marked "broken" on every old agent.

B392 fixed the immediate gap by adding the missing tools to
archetype kits + renaming the skill-row status to `unavailable`
(grey, "off for this agent") to distinguish from `broken` (red,
"substrate corruption").

**The class of gap is structural**, not a one-off. The substrate
has multiple wiring layers, and a tool/skill/role/handoff/tab can
be "present" in any subset of them:

| Layer | Question |
|---|---|
| Tool implementation | Python class registered into `ToolRegistry`? |
| Catalog | Listed in `config/tool_catalog.yaml` with side_effects? |
| Archetype kit | Listed in any archetype's `standard_tools`? |
| Genre default | Listed in any `genre_default_tools` block? |
| Agent constitution | In an alive agent's `tools:` list? |
| Skill require | In any installed skill's `requires:` list? |
| Handoff route | Capability mapped to a routable skill? |
| Domain entry | Role listed in a domain's `entry_agents`? |
| Frontend tab | Backed by an endpoint Section-13 probes? |

A gap at any layer reads as "wired" at the layer above. **The
14-section harness doesn't catch this** because each section
checks one layer in isolation. Section-02 sees the catalog has
text_summarize; section-05 sees agent A is alive with role X.
Neither asks "does agent A's kit carry text_summarize so it can
run skills that require it?"

The operator currently has two surfaces that almost answer this
question:
- **Capability tab** (B381) — per-agent, but operator has to pick
  each agent + interpret per-skill status manually.
- **Section-14 browser smoke** — confirms each tab renders, but
  doesn't cross-reference layer X vs. layer Y.

What's missing is a **cross-cutting wiring matrix** that scans
every layer for every entity and reports gaps as audit-grade
findings — and an agent that does this automatically on a
schedule, the same way `telemetry_steward` watches its
substrate.

Operator request (2026-05-18):

> "for our own personal diagnostics, let's create a page that
> shows everything working and wired correctly so every aspect I
> wanna see a page we can click on something and see if it's
> working or look at it potentially or is there another way to do
> like a full scan of the entire project or even create a special
> agent, that's doing real work monitoring the project for issues
> like this let's create a schedule for it as well to do its own
> runs and tests"

This ADR proposes the structural answer.

## Decision

Land **substrate wiring coverage** as three layers + a sentinel
agent + a schedule:

### 1. New diagnostic section: section-15-wiring-cross-check

`dev-tools/diagnostic/section-15-wiring-cross-check.command`.
Same shape as the existing 14 sections (writes `report.md` +
contributes to the umbrella summary). Cross-cutting checks:

- **Tool wiring coverage.** For every tool in catalog: is it in
  `/tools/registered`? Is it in at least one archetype kit OR
  one genre_default_tools block? If yes-catalog + no-anywhere-else,
  flag — operator has a tool no agent will ever carry.

- **Skill wiring coverage.** For every installed skill: for each
  required tool, is it in the catalog? If the skill is in any
  archetype's likely-use list (heuristic: roles whose handoff
  capability resolves to that skill), do those archetypes carry
  all the required tools?

- **Handoff resolution.** For every `(domain, capability) ->
  skill_name` mapping in `handoffs.yaml`: does the skill exist
  in the installed catalog? Does at least one alive agent in
  the domain's `entry_agents` carry all the required tools?

- **Archetype-to-skill matrix.** For every archetype: which
  cataloged skills can agents born under that archetype run?
  Counts per archetype + a list of "skills this archetype's
  kit blocks."

- **Cataloged-but-orphan tools.** Tools in catalog that no
  archetype kit + no genre_default + no agent constitution
  carries. These are operator-visible candidates for retirement
  OR for archetype-kit assignment.

Section-15 reports the matrix as a markdown table + the
operator-actionable punch list. Pass criteria: zero unintended
orphans, every handoff resolves end-to-end.

### 2. New diagnostic output: `wiring-coverage.html`

`data/test-runs/diagnostic-all-<ts>/wiring-coverage.html`
(or `wiring-coverage.html` standalone if regen on demand). A
single browsable page rendered by the umbrella that:

- Tally banner — total entities by layer + gap count.
- **Tool table** — one row per cataloged tool. Columns:
  registered? in any archetype? agent count carrying it?
  skill count requiring it? Click row → drilldown showing which
  archetypes/agents/skills.
- **Skill table** — one row per installed skill. Columns:
  required tools in catalog? archetype coverage (which
  archetypes can run it)? agent count that can actually run it?
  handoff backing it? Click row → drilldown.
- **Role table** — one row per role in trait_tree. Columns:
  in any genre? archetype kit present? alive agents? capability
  count routable? Click row → drilldown.
- **Handoff table** — one row per (domain, capability). Columns:
  skill_name + version exists? entry_agents carry required
  tools? matched alive agent count?
- **Frontend table** — one row per of the 16 tabs. Columns:
  endpoint live? section-13 probe status? section-14 smoke
  status?

The page is the operator's "everything works and is wired"
single read.

### 3. New role: `wiring_sentinel`

Genre: **guardian** (read_only). Job: run the cross-check skill
on a schedule, summarize findings, escalate operator-actionable
gaps via memory_write + `delegate.v1` (for severity ≥ medium).
Sibling to `telemetry_steward` (pipeline hygiene) and
`detection_engineer` (rule authoring). Same discipline pattern:
guardian-genre observer that synthesizes findings; never acts on
them.

Signature skill `wiring_audit.v1`:
1. `prior_audits` — memory_recall scope=private for prior briefs.
2. `verify_chain_integrity` — audit_chain_verify before drawing
   conclusions.
3. `cross_check` — dispatches section-15 via subprocess OR
   invokes the cross-check directly via a daemon endpoint
   (T-decide which).
4. `summarize_findings` — llm_think classifies findings:
   `gap_severity` in {info, low, medium, high}, recommended
   operator action.
5. `escalate_critical` — for severity ≥ medium, `delegate.v1`
   to the operator's pending queue with the punch list.
6. `record_audit` — memory_write scope=private with tags.

Constitution policies enforce:
- `forbid_substrate_mutation` — the sentinel reads; never
  modifies catalog/kits/constitutions.
- `forbid_silent_audit` — the chain MUST record a
  `wiring_audit_completed` event with the findings count per
  severity. No "scanned but found nothing" without an audit
  entry.
- `require_chain_verify_before_audit` — same posture as
  telemetry_steward.

### 4. Scheduled cadence

The existing daily scheduled task
(`forest-soul-forge-daily-config-check`, 8am Pacific) runs
`diagnostic-all.command` which already aggregates all 14
sections. After section-15 lands, the daily run picks it up
automatically — no new cron entry needed.

A SEPARATE scheduled task `forest-soul-forge-wiring-audit` runs
the wiring_sentinel's `wiring_audit.v1` skill on a 4-hour cadence
(`0 */4 * * *` Pacific) so substrate changes within a day
surface within 4 hours rather than the next morning.

## Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Section-15 cross-check command + reports.md output + tests | 1 burst (long) |
| T2 | wiring-coverage.html generator + umbrella integration + drilldown links | 1 burst (long) |
| T3 | wiring_sentinel role (trait_tree + genre + constitution + tool_catalog + handoffs + d3? OR daemon-wide? — see D5 below) | 1 burst (long) |
| T4 | wiring_audit.v1 signature skill | 1 burst |
| T5 | Scheduled task: forest-soul-forge-wiring-audit (4h cadence). Operator runbook addendum to docs/runbooks/diagnostic-harness.md. | 1 burst |
| T6 | CLOSE — live verify end-to-end + north-star update + status: Accepted | 1 burst |

Total: ~6 bursts. ADR-0081 closes when the sentinel's audit
runs cleanly + the coverage page renders with operator-readable
gaps.

## Decisions

**D1 — Section-15 is part of the harness, not a separate runner.**

The 14-section harness has the operator's mental model already.
Adding section-15 to the umbrella means the daily 8am run picks
it up + the index.html generator (B367) automatically renders it.
No new tooling.

**D2 — wiring-coverage.html is rendered by the umbrella, not a
separate command.**

Same rationale. Operator opens
`data/test-runs/diagnostic-all-<latest>/index.html` and clicks
through to wiring-coverage. One link, not a separate command.

**D3 — wiring_sentinel is a daemon-wide singleton, not domain-
scoped.**

The substrate spans all domains; a sentinel scoped to one domain
would miss cross-domain gaps. Single sentinel per-forest, similar
to reality_anchor / verifier_loop / forge.

**D4 — wiring_sentinel writes to memory + audit chain, never to
substrate config.**

Hard invariant. The sentinel proposes operator action; it never
takes action. Same posture as detection_engineer / telemetry_steward:
guardian-genre, read_only ceiling, advisory by policy.

**D5 — The sentinel does NOT auto-fix gaps.**

Operator-decision territory. The sentinel surfaces findings;
the operator decides whether to:
  (a) Add a tool to an archetype kit (config edit + rebirth).
  (b) Rebirth an agent to refresh its kit.
  (c) Retire an orphaned tool/skill.
  (d) Document the gap as intentional (e.g. "this tool is for
      future role X; not in any kit yet").

The auto-fix surface would invert the consequence: a sentinel
that auto-adds tools to kits would invalidate every other
constitution-immutability invariant. Operator owns substrate
mutation; sentinel owns finding.

**D6 — Severity classification is operator-readable, not
mechanical.**

Per ADR-0079's status taxonomy:
- `info` — gap is intentional (orphan tool with documented "for
  v0.4 role X" comment). No action.
- `low` — gap exists but no consumer impacted (cataloged tool
  in zero kits + zero skills require it). Retirement candidate.
- `medium` — gap blocks at least one skill from running on at
  least one agent. Operator-actionable.
- `high` — gap blocks a handoff from resolving (skill in
  handoff yaml but not in catalog OR no agent in domain carries
  the required tools). Operator must fix.

The LLM-think step in `wiring_audit.v1` classifies; the
escalation policy (`require_human_approval` on `delegate.v1`)
fires for medium + high.

**D7 — Schedule cadence is 4h, not hourly, not daily.**

Hourly would generate too much noise (substrate doesn't change
that often). Daily misses gaps for up to 23h. 4h hits the
"surface within a quarter day" sweet spot the operator hit when
B363 shipped at noon and the gap surfaced at the next operator
session.

## Consequences

**Positive:**

- The B363-class gap (catalog yes, kit no) surfaces automatically.
- The operator gets a single page (`wiring-coverage.html`) to
  scan every wiring layer.
- The sentinel produces a queryable audit-chain history of
  substrate health — operator can ask "when did this gap
  appear?" via memory_recall.
- Pattern reuse: wiring_sentinel shape mirrors telemetry_steward,
  threat_intel_curator, detection_engineer. Familiar discipline.

**Negative:**

- Adds substrate surface. Daily harness gains a new section;
  daily scheduled-task output gets longer; operator has another
  agent's brief to read.
- LLM-think classification has model risk — a borderline gap
  could be miscategorized. Mitigation: the sentinel's
  classification is advisory; the operator owns the call.
- Cross-cutting checks have correctness risk — if the matrix
  has a bug, false positives flood the operator. T1's tests
  + T6's live verify mitigate.

**Open questions:**

- Should section-15 also surface frontend wiring (tab → endpoint
  → renderer)? Section-13/14 already do this; the cross-check
  matrix could just reference their results rather than
  re-implementing.
- MCP plugin coverage: plugins are per-agent grants today
  (ADR-0043). Section-15 should include "agents with plugin X
  grants" as a column once per-agent grants surface in the
  capability tree (ADR-0080 placeholder right now).
- Should the wiring_sentinel skill also propose the fix (e.g.
  "add text_summarize.v1 to log_lurker's archetype kit")?
  Tempting but inverts D5. Defer to a future
  `propose_kit_addition.v1` skill that's clearly operator-
  reviewed-before-apply.

## See Also

- ADR-0079 — diagnostic harness (the 14-section parent)
- ADR-0080 — per-agent capability tree (the per-agent view this
  ADR provides cross-cutting matrix to)
- ADR-0064 — telemetry pipeline (sibling pattern of substrate
  oversight via guardian-genre agent)
- `docs/runbooks/diagnostic-harness.md` — operator runbook;
  extends to cover section-15 + wiring-coverage in T5
- `feedback_complete_over_narrow.md` — the principle this ADR
  systematizes: the B363 gap traced to a narrow interpretation;
  the sentinel + coverage page catches that class going forward
- B392 — the immediate fix for the B363 gap; this ADR's first
  payload is "never let this class of gap survive again"
