# ADR-0053 — Per-Tool Plugin Grants

**Status:** Proposed (2026-05-06). Pairs with ADR-0048 (Computer
Control Allowance) — directly unlocks the per-tool toggles in the
ADR-0048 T4 Advanced disclosure that B165 shipped as a read-only
reference table. Userspace-only delivery — uses existing audit-
chain event family; ADDS a column to one schema table (additive
v15→v16 migration).

## Context

ADR-0043 (plugin protocol + grants) shipped per-(agent, plugin)
grants. The substrate is plugin-scoped — one row per
(instance_id, plugin_name) in `registry.plugin_grants`, augmenting
the agent's constitutional `allowed_mcp_servers` at runtime
without rebirthing the agent.

That worked for ADR-0043's design intent. But ADR-0048 Decision 3
(three-preset allowance UI, B162 amendment) and the soulux-
computer-control plugin's six tools surfaced a granularity gap:
the operator wants to say "let the assistant SEE my screen
(`computer_screenshot.v1`) but never CLICK (`computer_click.v1`)"
— and the substrate doesn't support it. The current grant either
gives the agent the whole plugin or none of it. ADR-0048 T4
shipped (B165) with an Advanced disclosure rendered as a read-
only reference table, with a documented note: "per-tool
granularity in the UI awaits a substrate extension."

This ADR is that substrate extension.

The 2026-05-06 operator session locked the three-preset framing:
Restricted (read-only tools), Specific (per-category toggles),
Full (all tools). Specific's per-category toggles are halfway
down to per-tool granularity — Specific currently issues / revokes
the whole plugin grant, then the per-category UI toggles are
visual-only because the substrate can't enforce the distinction.
This ADR closes the gap.

## Decision

Extend `registry.plugin_grants` to support per-(agent, plugin,
tool) granularity. Plugin-level grants stay valid as the default
("all tools the manifest declares"); per-tool grants narrow the
effective set when present.

### Decision 1 — Schema: optional tool_name column

Add a new nullable column `tool_name` to the `plugin_grants`
table. Schema migration v15 → v16. Semantics:

- `tool_name IS NULL` (existing rows + new plugin-level grants)
  — grant covers ALL tools the manifest declares for this plugin.
  Byte-for-byte compatible with ADR-0043 substrate; no behavior
  change for existing operators.
- `tool_name IS NOT NULL` — grant covers ONLY the named tool.
  Multiple per-tool rows for the same (instance_id, plugin_name)
  represent the operator's curated subset.

The composite key becomes `(instance_id, plugin_name, tool_name)`
with a partial unique index where `tool_name IS NULL` (so the
plugin-level grant is at most one row per (agent, plugin)).

**Why a column on the existing table, not a new join table:**

- Most operators stay at plugin-level granularity — a separate
  table would require LEFT JOIN on every grant lookup
- Forest's audit-chain replay path already touches plugin_grants
  rows; one table to track keeps the replay logic narrow
- Per-tool rows live alongside plugin-level rows; the dispatcher's
  resolution logic (Decision 3 below) consults both with one
  query

### Decision 2 — API: tool_name optional on grant operations

`POST /agents/{instance_id}/plugin-grants` body gains an optional
`tool_name` field:

```json
{
  "plugin_name": "soulux-computer-control",
  "trust_tier": "standard",
  "tool_name": "computer_screenshot",     // optional; omit for plugin-level
  "reason": "operator selected Restricted preset"
}
```

`DELETE /agents/{instance_id}/plugin-grants/{plugin_name}` keeps
its current shape for plugin-level revocation. A new endpoint
`DELETE /agents/{instance_id}/plugin-grants/{plugin_name}/tools/{tool_name}`
handles per-tool revocation. (URL-path style matches the
existing posture / consents shape.)

The existing `GET /agents/{instance_id}/plugin-grants` response
gains `tool_name` in each row (null when plugin-level). No
breaking changes for existing clients — they ignore the new
field.

### Decision 3 — Dispatcher: resolution precedence

When `mcp_call.v1` dispatches a (server, tool) pair, the
governance pipeline's grants-check now resolves in this order:

1. Per-tool grant for `(instance_id, plugin, tool)` — if present,
   use its trust_tier
2. Per-plugin grant for `(instance_id, plugin)` (`tool_name IS NULL`)
   — fallback when no per-tool row exists
3. No grant — refuse (existing behavior; plugin not in agent's
   effective allowlist)

This means an operator can issue a plugin-level grant ("the agent
can use this plugin") AND THEN narrow it with per-tool revocations
("…except for `computer_click.v1`"). Or skip the plugin-level
grant and issue ONLY per-tool grants for the tools they want
(matches the Specific preset's per-category toggle UX).

**Why precedence puts per-tool first:**

- Operator intent is "more specific wins." A per-tool grant
  represents an explicit choice for THAT tool; the plugin-level
  grant is the default fallback.
- Forest's existing patterns (constitutional allow_paths,
  filesystem grants in ADR-0033) all use specificity-wins
  resolution.

### Decision 4 — Audit-chain events — additive event_data

The existing `agent_plugin_granted` and `agent_plugin_revoked`
events gain an optional `tool_name` field in event_data:

```json
{
  "event_type": "agent_plugin_granted",
  "event_data": {
    "instance_id": "...",
    "plugin_name": "soulux-computer-control",
    "tool_name": "computer_screenshot",     // null when plugin-level
    "trust_tier": "standard",
    "granted_by": "alex",
    "reason": "Specific preset → Read screen on"
  }
}
```

Per-tool grant events use the same event_type as plugin-level
grants — distinguishing per-tool from plugin-level happens via
the `tool_name` field, not a new event_type. Reason: an auditor
querying `event_type = 'agent_plugin_granted'` should see ALL
grant operations chronologically; filtering by tool_name is a
secondary concern.

Per ADR-0005 audit-chain canonical-form contract, adding optional
event_data fields is additive — old replay logic ignores the new
field; new replay logic uses it.

### Decision 5 — UI: ADR-0048 T4 Advanced disclosure becomes interactive

The B165 read-only per-tool reference table becomes a per-tool
toggle grid:

```
☑ computer_screenshot      read_only        none
☑ computer_read_clipboard  read_only        none
☐ computer_click           external         per-call
☐ computer_type            external         per-call
☐ computer_run_app         external         per-call
☐ computer_launch_url      network          per-call
```

Toggling a per-tool checkbox issues / revokes a per-(agent,
plugin, tool) grant via the new endpoint shape. Plugin-level
grant stays the bulk-toggle "all on / all off" surface in the
preset row above.

The three preset buttons in the preset row map to:

- **Restricted**: revoke plugin-level grant + revoke ALL per-tool
  grants. Clean state.
- **Specific**: issue per-tool grants for the tools whose
  per-category toggle is on (or for individual checkboxes the
  operator picks in Advanced). NO plugin-level grant — the
  per-tool rows are the entire effective set.
- **Full**: issue plugin-level grant at `elevated` tier + revoke
  any conflicting per-tool grants. Equivalent to ADR-0043's
  current grant shape.

### Decision 6 — Migration safety

The v15→v16 migration:

1. ADD COLUMN `tool_name TEXT NULL` to `plugin_grants`
2. CREATE UNIQUE INDEX `ux_plugin_grants_per_tool`
   ON `plugin_grants` (instance_id, plugin_name, tool_name)
3. CREATE UNIQUE INDEX `ux_plugin_grants_plugin_level`
   ON `plugin_grants` (instance_id, plugin_name)
   WHERE `tool_name IS NULL`

Existing rows stay valid (their `tool_name` is NULL post-migration
→ they're plugin-level grants by the new semantics). The pre-
migration rebuild path (replay the audit chain) still works
because the existing `agent_plugin_granted` events lack
`tool_name`, and absent → NULL → plugin-level. Defense in depth:
a v16 daemon reading a v15-shape audit event treats it as
plugin-level via the NULL-coalesce.

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Schema migration v15→v16 | ADD COLUMN + indexes + audit-replay path coalesces to plugin-level | 1 burst |
| T2 | Registry surface | `plugin_grants.grant(instance_id, plugin, tool=None, ...)` + `revoke(instance_id, plugin, tool=None, ...)` + `list_active` returns the new column | 0.5 burst |
| T3 | HTTP API | POST/DELETE accept `tool_name` field; new path `/tools/{tool_name}` for per-tool revocation; GET response includes `tool_name` per row | 0.5 burst |
| T4 | Dispatcher resolution | Specificity-wins lookup in the grants step of the governance pipeline | 0.5 burst |
| T5 | Frontend UI | ADR-0048 T4 Advanced disclosure: read-only table → interactive toggle grid wired to the new endpoints | 1 burst |
| T6 | Documentation | Update ADR-0048 + ADR-0043 + the operator safety guide (`docs/runbooks/computer-control-safety.md`) to reflect per-tool granularity | 0.5 burst |

Total estimate: 4 bursts.

## Consequences

**Positive:**

- ADR-0048 T4 Advanced disclosure becomes functional, closing the
  one TODO from the assistant arc that's documented as "awaits
  substrate"
- Operator gets the granularity the soulux-computer-control plugin
  was always designed for ("let it see, don't let it click")
- Existing operators' grants stay valid byte-for-byte; the
  migration is purely additive
- Specific preset semantics finally match what the UI shows (per-
  category checkboxes that actually issue per-tool grants
  instead of being decorative)
- Same pattern can extend to OTHER plugins later (a Slack plugin
  could grant `read_messages` but not `send_message`; a GitHub
  plugin could grant `list_issues` but not `merge_pr`) without
  additional substrate work

**Negative:**

- Schema migration. Forest's migration discipline catches drift,
  but operators who skip the v16 daemon update will have grants
  that don't migrate cleanly (v15 client + v16 daemon ↔ v16
  client + v15 daemon both work; v15 client + v16 client against
  the same v15 daemon is the ambiguous one — but Forest's single-
  daemon-per-host model makes this concrete only in a multi-
  install setup).
- The dispatcher's grants-check has one extra row to consult per
  call. Negligible at single-operator scale (the table will have
  tens of rows in practice); flag for re-examination if some
  operator grows it to thousands.
- "What grant am I under right now?" gets harder to reason about
  — the answer is now "plugin-level UNLESS overridden per-tool."
  Mitigation: the GET endpoint surfaces both rows; the UI shows
  the resolved state with a "(via per-tool grant)" annotation
  when relevant.

**Neutral:**

- Per-tool grants don't change posture clamp behavior. The
  ADR-0048 Decision 4 matrix (red refuses non-read-only, yellow
  PENDING, green grants-decide) operates on the EFFECTIVE grant
  resolved by Decision 3 above. So a per-tool grant for
  `computer_click` at the standard tier still gets refused under
  red posture; that's the intended global-brake semantic.
- No change to ADR-0049 / ADR-0050 / ADR-0051 (security hardening
  arc). Per-tool grants ride the same audit chain + same posture
  + same dispatcher pipeline; the security designs treat the
  augmented grants table as "still the same per-(agent, plugin)
  shape" because that's what NULL-tool_name rows are.

## What this ADR does NOT do

- Does NOT change ADR-0019 governance pipeline structure. The
  resolution change in Decision 3 is a one-line update to the
  grants-check step (the lookup query), not a new step.
- Does NOT change the constitution schema. Per-tool restrictions
  in the constitution itself are out of scope (the constitution
  declares `allowed_mcp_servers`, not per-tool sub-allowances —
  that level of granularity stays the operator's runtime choice
  via grants).
- Does NOT add new audit-chain event types. `agent_plugin_granted`
  + `agent_plugin_revoked` keep their existing shape with an
  optional event_data.tool_name field.
- Does NOT specify per-tool trust tier semantics. The grant's
  trust_tier applies uniformly to every tool the grant covers.
  Different tools needing different tiers means issuing different
  grants for them — that's the per-tool granularity working.
- Does NOT migrate existing rows to per-tool. Pre-v16 rows stay
  plugin-level (NULL tool_name) until an operator explicitly
  re-issues them at per-tool granularity through the UI / CLI.

## References

- ADR-0019 — Tool dispatch + governance pipeline (the grants
  resolution lives in the GrantsStep)
- ADR-0043 — Plugin protocol + grants (this ADR extends the
  substrate)
- ADR-0044 — Kernel/userspace boundary (this ADR is userspace-only;
  the schema migration is part of the userspace contract)
- ADR-0045 — Agent posture (orthogonal; per-tool grants ride the
  same posture clamps)
- ADR-0048 — Computer control allowance (Decision 3 amendment in
  B162 ships the three-preset UI; this ADR closes the per-tool
  gap in T4 Advanced disclosure)
- ADR-0005 — Audit chain canonical-form contract (this ADR's
  event_data additions are additive per the contract)

## Credit

The per-tool gap was flagged in B158 (ADR-0047 T4 partial settings
panel) and again in B165 (ADR-0048 T4 partial allowance UI), both
shipping the per-tool reference as read-only with a documented
"awaits substrate" note. The 2026-05-06 operator framing of three
preset tiers + Advanced disclosure (B162 ADR-0048 D3 amendment)
was where Specific's per-category UX made the substrate gap
concretely visible. This ADR ships the substrate that the UI was
designed against.
