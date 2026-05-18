# ADR-0080 — Per-agent capability tree UI

**Status:** Accepted (4/5 tranches shipped; T4 deferred indefinitely)
**Date:** 2026-05-17 proposal, 2026-05-18 close
**Closed in:** B383 (this commit). Tranches:
  - T1 B380 (`972ff9b`) — backend GET endpoint
  - T2 B381 (`0560d0f`) — frontend Capabilities tab
  - T3 B382 (`e767be3`) — toggle endpoint + audit event
  - T4 (DEFERRED) — inferred tool→tool prerequisite edges; not
    needed for daily operator workflow today
  - T5 B383 (this) — runbook (`docs/runbooks/agent-capability-tree.md`)
    + this status update

Plus a queued follow-on: **T3b** lands the per-agent overrides
table + runtime enforcement of toggles. The audit chain is the
durable record in the meantime; T3b only converts intent into
gating.


**Tracks:** Frontend / Operator UX / Governance surface
**Supersedes:** none (augments existing global Skills + Tool
Registry tabs)
**Builds on:** ADR-0001 (constitution as identity), ADR-0019 (tool
catalog + registration), ADR-0021 T8 (genre kit-tier ceilings),
ADR-0036 (per-agent posture), ADR-0057 (skill forge), ADR-0058
(tool forge), ADR-0072 (behavior provenance), ADR-0079 (diagnostic
harness — feeds the "broken/in-progress" coloring)
**Unblocks:** operator-fluent per-agent capability management
without spelunking the constitution YAML

## Context

The frontend currently surfaces capabilities at the **global**
level: `Skills` tab lists every installed skill across the
substrate; `Tool Registry` lists every registered tool. Both are
flat catalogs. Per-agent capabilities live in the agent's
constitution YAML — operator-readable, but not interactively
manipulable from the UI. Genre kit-tier ceilings, hardcoded
allowed_tools lists, per-agent posture overrides, and runtime-
broken capabilities all compose into "what can this specific agent
actually do right now" in a way the current UI doesn't surface.

During the 2026-05-17 wire-readiness sweep (B358-B370), the
operator requested a per-agent sub-page styled like a video-game
character sheet: capabilities laid out as a dependency tree, with
visual coloring that distinguishes:

1. **Hard-wired** capabilities — required by the agent's role +
   genre, non-toggleable (removing would violate the constitution
   contract or genre invariant). Operator visibility only.
2. **Operator-toggleable** capabilities — optional capabilities
   the operator can enable/disable per-agent without rebirth.
   Toggling updates the agent's posture; the underlying
   constitution stays immutable.
3. **Broken / in-progress / unavailable** capabilities — greyed
   out and not actionable. Reasons surface inline (provider
   offline, dependency missing, harness flagged drift, awaiting
   substrate ADR not yet landed).

Dependency-tree shape: child nodes require parent capabilities
(e.g., `code_edit.v1` requires `code_read.v1` per the
SW-track A.5 design; `archive_evidence.v1` skill requires
`audit_chain_verify.v1` tool). The tree makes prerequisites
visible without forcing the operator to cross-reference
manifests by hand.

The current global Skills + Tool Registry tabs remain for
substrate-wide views (forge pipeline, installation, catalog
audit). The new tab is the **per-agent operational view** — "given
this specific agent, what is its actual reach right now?"

## Decision

Land a new frontend tab `Agent Capabilities` (sibling to the
existing 15 tabs) that takes an agent_id and renders a dependency-
shaped tree of that agent's capabilities. Backend support adds two
endpoints; no schema migration.

### Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  Frontend: Agent Capabilities tab                             │
│  ┌────────────────────────────────────────────────────────┐   │
│  │ Agent: [picker dropdown from /agents]                  │   │
│  ├────────────────────────────────────────────────────────┤   │
│  │ ┌─────────────────────┐  ┌────────────────────────┐    │   │
│  │ │ Tree view           │  │ Detail pane             │    │   │
│  │ │ ─ tools             │  │ <selected node>          │    │   │
│  │ │   ├─ code_read.v1  ✓│  │ Status: HARD_WIRED       │    │   │
│  │ │   │  └─ code_edit  ✓│  │ Genre: actuator (writes) │    │   │
│  │ │   ├─ llm_think    ✓│  │ Side_effects: filesystem │    │   │
│  │ │   ├─ text_summari ✓│  │ Reason hard-wired:        │    │   │
│  │ │   └─ web_fetch    ✗ │  │   provider offline        │    │   │
│  │ │ ─ skills            │  │ Required by:              │    │   │
│  │ │   └─ archive_evid  ⏳│  │   ─ commit_message skill  │    │   │
│  │ │ ─ mcp_plugins       │  │   ─ release_notes skill   │    │   │
│  │ │   └─ ...             │  │                          │    │   │
│  │ └─────────────────────┘  └────────────────────────┘    │   │
│  └────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

Three visual states per node:
- **`✓ green`** — capability is live + currently exercisable.
- **`✗ greyed`** — capability is unavailable (broken, missing
  dep, provider offline). Tooltip shows reason. Not actionable.
- **`⏳ amber`** — capability is queued / in-progress (e.g., skill
  forge has staged it but not installed; tool catalog has it but
  the implementation class is missing).

Toggle state independent of color:
- **`🔒 lock icon`** — operator cannot toggle (hard-wired by role
  + genre + constitution). Hover shows which invariant forbids
  the toggle.
- **`☐ checkbox`** — operator can toggle. Click flips the posture;
  the daemon validates against the genre ceiling and rejects if
  the toggle would violate the kit-tier contract.

### Tree composition rules

Each agent's capability tree composes from four sources, in
strict precedence order (highest to lowest):

1. **Constitution `allowed_tools` list** — birth-bound; immutable.
   Any tool listed here is hard-wired (lock icon).
2. **Genre `risk_profile.max_side_effects` ceiling** — capabilities
   above the ceiling are forbidden regardless of constitution
   request (genre is the contract; constitution requests beyond it
   were already filtered at birth).
3. **Per-agent posture overrides** (ADR-0036) — operator-toggled
   yellow/green/red postures that gate optional capabilities.
4. **Runtime availability** — tool_runtime startup_diagnostics for
   each tool; provider liveness for llm_* tools; section-04 +
   section-14 harness output for what's actually exercisable.

The tree's PARENT-CHILD edges come from the dependency declarations:
- Skills declare `requires` (list of tool keys).
- Tools may declare implicit prerequisites in their docstrings
  (code_edit requires code_read in practice). Phase 1 only
  surfaces skill→tool dependencies; phase 2 can add inferred
  tool→tool edges.
- MCP plugins (ADR-0043) are leaf nodes attached under a synthetic
  "MCP" parent.

### Backend endpoints

Two new endpoints, both read-only, both per-agent. Existing
substrate populates them — no new tables.

**`GET /agents/{instance_id}/capability-tree`**

Returns the per-agent tree. Composes from constitution +
genre engine + tool_catalog + tool_runtime startup diagnostics +
skill_runtime installed list + plugin grants. Response shape:

```json
{
  "agent": {
    "instance_id": "test_author_5d2af4c4",
    "role": "test_author",
    "genre": "researcher",
    "posture": "yellow"
  },
  "tree": {
    "tools": [
      {
        "key": "code_read.v1",
        "side_effects": "read_only",
        "status": "live",
        "binding": "hard_wired",
        "reason": "in constitution allowed_tools",
        "required_by": ["code_edit.v1"],
        "constraints": {"allowed_paths": ["src/", "tests/"]}
      },
      {
        "key": "web_fetch.v1",
        "side_effects": "network",
        "status": "broken",
        "binding": "hard_wired",
        "reason": "provider offline (last_check=2026-05-17T20:13:00Z)",
        "required_by": []
      }
    ],
    "skills": [
      {
        "name": "archive_evidence",
        "version": "1",
        "status": "in_progress",
        "binding": "operator_toggleable",
        "reason": "staged via forge pipeline; not installed",
        "requires_tools": ["audit_chain_verify.v1", "memory_recall.v1", "memory_write.v1", "file_integrity.v1", "llm_think.v1"]
      }
    ],
    "mcp_plugins": [
      {
        "name": "ms-office-suite",
        "status": "live",
        "binding": "operator_toggleable",
        "tools": ["docx_open.v1", "docx_write.v1"]
      }
    ]
  },
  "version": 1
}
```

**`POST /agents/{instance_id}/capability-toggle`**

Operator toggles an operator_toggleable capability on/off. Validates
against the genre ceiling + constitution allowlist; rejects with
409 if the toggle would violate either. Body:

```json
{
  "capability_key": "web_fetch.v1",
  "enabled": false
}
```

Wires through the existing `app.state.write_lock` for single-writer
SQLite discipline. Emits a `capability_toggled` audit chain event
with `{agent_dna, capability_key, prior_state, new_state}`.

### Frontend module

`frontend/js/capability-tree.js` — new module, ~300 LoC, mirrors
the pattern of `agents.js` + `tool-registry.js`:

1. Subscribe to `state.agents` for the picker dropdown.
2. On agent selection, fetch `/agents/{id}/capability-tree`.
3. Render tree-view in the left half; detail pane in the right.
4. Listen for clicks on toggleable nodes; dispatch
   `/agents/{id}/capability-toggle`; on 200, refresh.

Visual rendering: SVG tree edges between parent-child nodes, color
classes for status (green/grey/amber), lock-icon overlay on
hard-wired nodes. CSS variables in `frontend/css/` follow the
existing pattern; no Tailwind / no React.

## Tranches

**T1 — Backend endpoint substrate (1 burst).**
Two routes (`/agents/{id}/capability-tree` GET +
`/agents/{id}/capability-toggle` POST). Read-only path lands first;
toggle path next. Unit tests over the composition rules.

**T2 — Frontend module (1-2 bursts).**
`capability-tree.js` + `index.html` panel + CSS for tree-view.
Section-13 harness probe added for the new endpoints.

**T3 — Posture wiring for toggles (1 burst).**
The toggle endpoint mutates per-agent posture. Wire the audit
chain `capability_toggled` event. Section-08 (audit chain) gains
awareness of the new event type.

**T4 — Inferred tool→tool edges (1 burst).**
Parse tool docstrings for "requires X" hints; surface as
prerequisite edges in the tree. Phase 1 only had skill→tool;
phase 2 adds the inferred tool→tool layer for visual clarity.

**T5 — Operator runbook + closing CHANGELOG entry (1 burst).**
`docs/runbooks/agent-capability-tree.md`. Walk through reading
the tree, interpreting the three states, when to toggle vs.
rebirth, audit-chain readback of toggle events.

**Total: 5-6 bursts to close.**

## Decisions

**D1 — Why not just extend Tool Registry / Skills tabs.**
The global tabs answer "what does the substrate have?" The per-
agent tab answers "what does THIS agent actually have RIGHT NOW?"
Those are different questions. Operators need both views.
Conflating them would clutter the global tab with per-agent state
or hide the global picture behind a picker.

**D2 — Why per-agent, not per-role.**
Per-role would miss: per-agent posture, per-agent
constitution_hash (which constraints the agent at birth time),
per-agent allowed_paths overrides, the fact that two agents in the
same role can have different effective reaches if the operator
toggled differently.

**D3 — Why dependency tree, not flat list.**
The dependency shape IS the operator's mental model. "Can this
agent commit a message?" requires "can it use commit_message.v1?"
which requires "is llm_think.v1 live + provider reachable?" The
flat list forces the operator to reconstruct that chain mentally;
the tree shows it.

**D4 — Why three states (live / broken / in-progress) not just
two.**
"In-progress" surfaces staged-but-not-installed capabilities the
operator might be unaware of (forge pipeline output, ADR work in
mid-arc, etc.). Without the third state these would look broken,
which loses information.

**D5 — Toggles update posture, not constitution.**
Per CLAUDE.md "Constitution hash is immutable per agent." The
toggle surface CANNOT mutate constitution. It mutates per-agent
posture (yellow→green to enable an optional capability;
green→yellow to disable). Hard-wired capabilities are
non-toggleable by definition (lock icon).

**D6 — No real-time tree (poll, don't WebSocket).**
The tree state changes when:
- Operator toggles a capability (immediate refetch).
- Substrate restarts (operator reloads tab).
- A capability's status flips (tool provider goes offline, skill
  forge completes an install).
The first two are operator-driven; the third is rare. Polling
every 30s is sufficient; WebSocket complexity isn't earned yet.

**D7 — Use existing `/agents/{id}` agent detail endpoint as the
single source of constitution + posture; don't duplicate
read paths.**
The backend composes the tree by fetching the existing agent
detail, joining against tool_catalog + skill_runtime + plugin
grants. No new SQL queries; no new tables.

## Open questions

- **Tool→tool prerequisite inference**: docstring-parsed or
  manifest-declared? Lean manifest-declared (explicit > inferred)
  if T4 happens at all; T4 may stay deferred indefinitely if
  skill→tool edges prove sufficient.
- **MCP plugin grouping**: should each plugin appear under a
  synthetic "MCP" parent, or interleaved with native tools?
  Likely synthetic parent for visual scanability — MCPs have
  different governance (operator install gate vs. catalog).
- **Forge pipeline integration**: staged-but-not-installed
  skills/tools render as `in_progress` (amber). Should the
  operator be able to install from the capability tree itself,
  or stay routed through the Marketplace tab? Keep separation:
  capability tree is read + toggle; install lives in Marketplace.

## Risk

Per CLAUDE.md §0:
1. **Prove harm of NOT building this**: operators currently
   reconstruct per-agent reach from raw constitution YAML
   reads. Errors and ambiguity show up only when an agent
   refuses a request the operator expected to succeed (or
   succeeds at one they expected to refuse). The cost is
   measured in operator-confusion events, not in code.
2. **Prove non-load-bearing of additions**: pure additive
   surface — new tab, new endpoints, new module. No existing
   substrate behavior changes. The capability-toggle endpoint
   wires through existing write_lock + posture pipeline.
3. **Prove alternative is strictly better**: alternatives are
   (a) extend Tool Registry tab — clutters the global view,
   (b) static dump in agent detail panel — loses interactivity,
   (c) leave operator to YAML reads — current state, accepted
   cost. The dedicated tab delivers operator fluency the
   alternatives don't.

## Status tracking

This ADR lands at Proposed status. Operator opens the arc by
green-lighting T1 (backend substrate). Each tranche lands as
its own commit-burst; the umbrella runbook + the operator's
acceptance of T2's UI close the arc.

The diagnostic-all section-13 + section-14 (B366) will catch
boot-level regressions on the new tab as soon as T2 lands.
