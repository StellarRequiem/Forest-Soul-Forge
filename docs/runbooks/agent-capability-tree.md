# Runbook — Agent Capabilities tab

**ADR:** ADR-0080
**Bursts:** B380 (T1 backend), B381 (T2 frontend), B382 (T3 toggle)
**Status:** Operator-ready. T3b (runtime enforcement of toggles)
queued; until then the audit chain is the durable record of toggle
intent.

## What this tab answers

The global **Tool Registry** + **Skills** tabs answer "what does
the substrate have?" The **Capabilities** tab answers "what does
THIS specific agent actually have RIGHT NOW?" — a different
question with a different audience.

For each selected agent, the tree shows:

- **Tools (constitution-bound, hard-wired 🔒)** — the agent's
  `allowed_tools` list from its constitution. Immutable at this
  layer; rebirth is the only path to remove.
- **Skills (operator-toggleable ☐)** — every installed skill in
  the catalog, with a `missing` annotation listing any required
  tool the agent doesn't have.
- **MCP Plugins** — placeholder for ADR-0043 per-agent plugin
  grants. Empty today; populated when those grants surface in
  the substrate.

## Reading the three states

| Glyph | State | Meaning |
|:---:|---|---|
| ✓ | **live** | callable right now |
| ✗ | **broken** | known by the substrate but missing a dep (skill's required tool absent from agent's kit; constitution tool absent from `/tools/registered`) |
| ⏳ | **in_progress** | staged via the forge pipeline but not yet installed (skills only) |

## Reading the two binding modes

| Glyph | Binding | Operator action |
|:---:|---|---|
| 🔒 | **hard_wired** | constitution-bound; toggle endpoint returns **409**. To remove, rebirth the agent via the proper pipeline. |
| ☐ | **operator_toggleable** | a toggle button appears next to the node. Click to enable/disable. |

## How the toggle works (B382)

Clicking **enable** or **disable** POSTs to
`/agents/{instance_id}/capability-toggle` with
`{capability_key, enabled}`. The endpoint:

1. Verifies the agent exists (404 otherwise).
2. Verifies the `capability_key` is in the agent's tree
   (404 otherwise).
3. Rejects hard-wired tools with **409 Conflict** (rebirth is the
   path).
4. Emits a `capability_toggled` audit chain event under the
   single write lock:
   ```json
   {
     "instance_id": "...",
     "capability_key": "name.vN",
     "kind": "skill",
     "binding": "operator_toggleable",
     "requested_enabled": false,
     "set_by": "<operator_id or null>",
     "prior_state": "unknown"
   }
   ```
5. Returns the new state echoed back + the audit event's seq.

**Audit-first / enforcement-later** (T3b is queued): the chain is
the durable record today. Runtime gating of disabled skills lands
in T3b alongside a small `agent_capability_overrides` table that
will let prior_state surface concretely. Until T3b ships, an
operator's toggle is operationally a *recorded intent*: the
audit trail names what should happen; the dispatcher doesn't yet
gate on it.

## When to use this tab

| Question | Tab |
|---|---|
| "Does this specific agent have everything it needs to run skill X?" | **Capabilities** (look for ✗ + `missing` annotation) |
| "Is the substrate generally healthy?" | **Tools** / **Skills** / **Tool Registry** |
| "Why can't this agent be reached for capability Y?" | **Capabilities** + **Audit** (cross-ref `capability_toggled` history) |
| "Which agents are eligible for skill Z?" | not yet — that's a reverse query the substrate doesn't expose. T4 candidate. |

## What this tab does NOT do

- **Does not** modify constitutions. Identity-hash invariant
  per CLAUDE.md.
- **Does not** affect runtime dispatch yet for toggled-off
  skills — that's T3b.
- **Does not** surface MCP plugin grants today; that arrives
  with the ADR-0043 grant-table reach.
- **Does not** display inferred tool→tool edges (e.g.
  `code_edit` requires `code_read`); T4 is the optional bracket
  for that.

## Recovery paths

- **A hard-wired tool needs to leave an agent's kit:**
  the toggle endpoint won't help — rebirth the agent. Use
  `/archive` on the old `instance_id`, then `/birth` with the
  updated role definition. Pattern is the same as the
  Kraine/Victor/chaz rebirth (B376), audit doc at
  `docs/audits/2026-05-17-quarantine-rebirth.md`.

- **An operator-toggleable skill has been toggled and you want
  to see history:** query `/audit/tail` (or the Audit tab) for
  events where `event_type=capability_toggled` and
  `event_data.instance_id=<agent>`. The chain is append-only;
  every toggle is recoverable.

- **A skill shows `✗ broken: missing [tool.v1]`:** the agent's
  constitution doesn't include that tool. Either rebirth with a
  kit that includes it, OR uninstall the skill (which is the
  cheaper move if the agent isn't supposed to run it).

- **The tab shows "Failed to load" for an agent:** either the
  agent's `constitution_path` is missing/broken, or the daemon
  isn't reachable from the frontend. Check `/agents/{id}` first
  — if the basic endpoint 404s, the agent is gone; if it
  responds but the tree fails, look at the daemon log for the
  capability-tree handler's traceback.

## Cross-references

- **ADR-0080** — the design doc this runbook follows.
- **CLAUDE.md §sec0** — Hippocratic gate (why hard-wired stays
  hard-wired).
- **B380** `972ff9b` — T1 backend (GET endpoint).
- **B381** `0560d0f` — T2 frontend (Capabilities tab).
- **B382** `e767be3` — T3 toggle endpoint + audit event.
- **B376** `8b723bf` — quarantine rebirth (template for "rebirth
  to change a hard-wired kit").

## Verification

After B380+B381+B382 land + a daemon restart:

1. `PYTHONPATH=src python3 -m pytest tests/unit/test_b380_capability_tree.py`
   → 12 passed.
2. Open the frontend at `?api=...`; click **Capabilities** tab.
3. Pick TelemetryStreward-D3 (or any active agent). See tree
   render with tools live, skills classified, MCP empty.
4. Click a skill's enable/disable button. Toast surfaces
   "audit seq N".
5. Open Audit tab; the most recent entry is
   `capability_toggled` with the toggle's full payload.
6. `dev-tools/diagnostic/diagnostic-all.command` reports
   **15/15** PASS (14 sections + section-14's 16 tabs).
