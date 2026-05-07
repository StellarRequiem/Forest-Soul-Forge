# Experimenter Cycle Runbook (Smith)

**Status:** Operational. Pairs with [ADR-0056](../decisions/ADR-0056-experimenter-agent.md).
Last updated 2026-05-07 (B192 — E6).

This runbook covers the day-to-day operator workflow for
running Smith — the experimenter agent — through cycles of
self-recursive improvement on Forest-Soul-Forge. Read this
before you flip Smith from YELLOW posture to GREEN, before
you enable the explore-mode scheduled tasks, and before you
approve your first cycle's merge.

The substrate ships with safety defaults. Most of what's
below is "what to watch for" and "when to reach for the brake
pedal" rather than "how to install."

## Identity check

Smith was born 2026-05-07 03:22:40Z with these immutable
identity fields (per ADR-0001 D2):

- **instance_id:** `experimenter_1de20e0840a2` (your install
  may differ — check the Agents tab if unsure)
- **DNA:** `1de20e0840a2`
- **Role:** `experimenter`
- **Genre:** `actuator` (max_side_effects=external,
  default_initiative_level=L5)

If any of these change, you have a different agent — Smith
the original is gone, replaced by a new instance with the same
name. The audit chain captures the transition; the dashboard's
Agents tab shows the latest.

## Posture — the brake pedal

Smith births at YELLOW. Three states, switchable via the
Cycles pane's posture toggle (B192) or via direct API:

```bash
curl -X POST http://127.0.0.1:7423/agents/experimenter_1de20e0840a2/posture \
     -H "X-FSF-Token: $FSF_API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"posture": "yellow", "reason": "your reason here"}'
```

| Posture | Read-only tools | Mutating tools | When to use |
|:---|:---:|:---|:---|
| **GREEN** | auto-fire | auto-fire (within genre cap) | After 5+ clean cycles. You trust Smith. |
| **YELLOW** (default) | auto-fire | queue for operator approval | Daily default. Every action is one-click confirm. |
| **RED** | auto-fire | refuse outright | Overnight. While you're traveling. After a bad cycle. After installing a new tool you haven't validated. |

**Important:** RED does NOT silence explore-mode dispatches.
Explore-mode tools are read-only; PostureGateStep passes
read-only tools regardless of posture. To stop Smith fully
overnight, **disable the scheduled tasks** in
`config/scheduled_tasks.yaml` (`enabled: false`) AND flip RED.
Or just disable the tasks — explore mode is the only
auto-firing path.

## When to flip RED — checklist

Flip Smith to RED if ANY of these are true:

1. You're stepping away for >2h and don't want surprise
   approval queues building up.
2. The last cycle's diff did something you didn't expect
   (touched files outside your mental model).
3. You added a new tool to Smith's kit (tools_add) and
   haven't validated its behavior.
4. The system clock or audit chain is showing anomalies
   (`dev-tools/check-drift.sh`, `audit_chain_verify.v1`).
5. You're about to perform any operation on Smith's
   workspace from your own work tree — clean state is
   the easier-to-reason-about state.
6. You suspect the explore-mode prompt has been compromised
   (a memory entry, a counter-propose note, or a previous
   cycle's report contains content you don't recognize as
   yours).

Flip back to YELLOW or GREEN after the situation clears + a
fresh `audit_chain_verify.v1` passes.

## Cycle review checklist

When a new cycle appears in the Cycles pane:

1. **Read the commit message** (collapsible at top of expand
   view). Smith should explain what it changed, why, what
   tests it ran, what it deliberately didn't change.
2. **Read the cycle report** if present (`CYCLE_REPORT.md`
   on the branch, or `docs/cycles/cycle-N.md`). Should
   include: target, plan, what it tried, what worked, what
   failed, and any `requested_tools:` block (E5 / E6
   self-augmentation surface).
3. **Read the diff.** Look for:
   - Files touched outside the constitutionally allowed_paths
     (should be impossible — the dispatcher refuses — but
     verify).
   - New files in `tools/builtin/` or `skills/` (means Smith
     is asking for a tools_add — see Self-augmentation
     section below).
   - Any deletion of files in `examples/`, `data/`,
     `~/.fsf/secrets/` (also impossible by constraint, but
     verify).
   - Any change to `examples/audit_chain.jsonl` directly
     (forbidden — the chain is append-only by the kernel).
4. **Check tests.** The cycle report's `test_outcome` field
   should be `passed`. If `failed`, deny the cycle and use
   counter-propose to give Smith specific feedback on what
   to fix.
5. **Check requested_tools.** If Smith asks to add new
   tools/skills to its kit, read each request's `reason`
   field. Approve via tools_add only if you understand the
   tool + agree with the reason. See Self-augmentation.

## How to abandon a cycle

Three paths:

1. **Deny via the Cycles pane.** Click "deny" — the audit
   event lands; the branch stays for forensics. Add a note
   for the audit if helpful.
2. **Deny + delete branch.** Same flow, with the
   "delete branch on deny" checkbox ticked. Branch is gone
   from the workspace; Smith won't see it on next explore.
3. **Manual nuke (escape hatch).** From the workspace:
   ```bash
   cd ~/.fsf/experimenter-workspace/Forest-Soul-Forge
   git branch -D experimenter/cycle-N
   ```
   No audit event; useful when the cycle never reached the
   chat pane (e.g. dispatcher crashed mid-cycle).

After abandoning, fire `audit_chain_verify.v1` to confirm the
chain integrity is intact. Smith's procedural-shortcut row
(if any matched) will weaken on the deny via
`memory_tag_outcome.v1` — this is the reinforcement signal
that prevents Smith from re-proposing the same thing next
cycle.

## Self-augmentation — when Smith asks for a new tool

Per ADR-0056 D6, Smith CAN draft new tools/skills/plugins in
work-mode cycles, but it CANNOT grant itself access — that's
an operator action via `tools_add` (or constitutional re-birth
for skills).

Smith expresses requests via a yaml fence in the cycle report:

````markdown
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
````

When you approve a cycle that includes requested_tools:

1. Approve the merge first (via the Cycles pane button or
   manually). The new tool's source lands on workspace's main.
2. Push the workspace to upstream main if you want it
   published:
   ```bash
   cd ~/.fsf/experimenter-workspace/Forest-Soul-Forge
   git push origin main
   ```
3. Pull into your operator work tree:
   ```bash
   cd ~/Forest-Soul-Forge
   git pull
   ```
4. Reload the daemon's tool catalog so it sees the new tool:
   ```bash
   curl -X POST http://127.0.0.1:7423/tools/reload \
        -H "X-FSF-Token: $FSF_API_TOKEN"
   ```
5. Grant Smith access to the new tool:
   ```bash
   curl -X POST http://127.0.0.1:7423/agents/experimenter_1de20e0840a2/tools/add \
        -H "X-FSF-Token: $FSF_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"tool_name": "log_correlation_window", "tool_version": "1"}'
   ```

Per ADR-0001 D2 invariance: `tools_add` mutates Smith's
per-instance state (the constitution's `tools[]` list grows)
but does NOT change `constitution_hash` or DNA. Smith retains
its identity across additions.

**Things to refuse on requested_tools:**

- Tools that bypass identity surfaces (audit chain, registry
  DB, secrets dir).
- Tools whose `reason` field doesn't actually justify the
  scope. "I might need this someday" is not a reason.
- Tools whose `side_effects` declaration doesn't match the
  implementation. If the tool spawns subprocess but declares
  `read_only`, refuse and counter-propose with the correct
  declaration.
- Tools that wrap a third-party API without a verified
  authentication path through ADR-0052 secrets store.

## Frontier cost guardrails

Smith routes `llm_think` to the Anthropic frontier provider
(claude-sonnet-4-6) when the dispatch's task_kind defaults
align. Per ADR-0056 D4:

- **Default task_caps.usage_cap_tokens:** 100,000 per session.
  Hits cap → dispatcher refuses with `task_usage_cap_exceeded`.
- **Cost ceiling at full enablement:** ~$1/day with the two
  default explore-mode scheduled tasks (4h + 6h cadences).
  Work-mode cycles add ad-hoc spend per operator-fired
  dispatch.
- **No daily budget cap currently enforced.** Watch the
  Audit tab for `tool_call_succeeded` events tagged with
  `model: "claude-sonnet-4-6"` if you want a running tally.
  Per-day caps land in a future tranche if usage warrants.

To stop frontier spend immediately:
```bash
# Edit .env, flip FSF_FRONTIER_ENABLED to false, restart daemon
sed -i.bak 's/^FSF_FRONTIER_ENABLED=true/FSF_FRONTIER_ENABLED=false/' .env
launchctl kickstart -k gui/$(id -u)/dev.forest.daemon
```

This forces every dispatch to local provider (qwen2.5-coder:7b).
Smith continues to function but with reduced capability.

## What success looks like

After 10 clean cycles you should observe:

- Smith's procedural-memory shortcuts table (`memory_procedural_shortcuts`)
  has a few high-confidence rows for recurring situations.
- The Cycles pane shows a steady cadence: explore tick → cycle
  proposal → work-mode dispatch → review → approve/deny.
- Smith's requested_tools list has merged a few utility tools
  that you actually use; some have been refused (rightly).
- The audit chain has 0 verification warnings.
- Smith hasn't tried to push to main directly even once. (If
  it did, the constraint refused it; verify via grep on the
  chain for `mode_kit_clamp` or `tool_call_refused`.)

## Things that should NOT happen — escalate immediately

If you see ANY of these in the audit chain or workspace:

- Smith committed directly to main (impossible by branch-
  isolation constraint, but verify after each cycle by
  checking the workspace's `git log main` for non-merge
  commits authored by Smith).
- Files outside Smith's `allowed_paths` got modified.
- The constitution_hash for Smith changed without an
  operator action you remember.
- A `tool_call_dispatched` event for Smith fired a tool not
  in its kit.
- An `experimenter_cycle_decision` event with `action=approve`
  appears for a cycle you didn't approve.

→ Flip Smith to RED immediately, stop all scheduled tasks,
   run `audit_chain_verify.v1`, run `dev-tools/check-drift.sh`,
   and post the findings to your audit log. The substrate is
   designed to make these impossible; if one happens, the
   substrate has a bug + you have an integrity question.

## Quick reference

| Action | Path |
|:---|:---|
| Open cycles pane | Chat tab → "Cycles" button |
| Flip posture | Cycles pane → posture toggle (top of pane) |
| Fire work-mode cycle | Tools tab → llm_think with `task_caps.mode=work` against Smith |
| Disable explore tasks | `config/scheduled_tasks.yaml` → set `enabled: false` |
| Manual cycle abandon | `cd ~/.fsf/experimenter-workspace/... && git branch -D experimenter/cycle-N` |
| Verify chain | Tools tab → `audit_chain_verify.v1` |
| Stop frontier spend | `FSF_FRONTIER_ENABLED=false` in `.env` + daemon restart |
| Identity check | Agents tab → look up `experimenter_1de20e0840a2` |

## See also

- [ADR-0056](../decisions/ADR-0056-experimenter-agent.md) —
  full design rationale + tranche map
- [ADR-0045](../decisions/ADR-0045-agent-posture-traffic-light.md) —
  posture semantics
- [ADR-0001](../decisions/ADR-0001-agent-identity-substrate.md) D2 —
  identity invariance
- [ADR-0019](../decisions/ADR-0019-tool-execution-runtime.md) —
  governance pipeline that ModeKitClampStep slots into
- `dev-tools/birth-smith.command` — the script that provisioned
  Smith's identity + workspace
