# Runbook — substrate wiring audit (ADR-0081)

**Scope.** Operating the 4-hour scheduled wiring audit and reading
its outcomes. Pairs with the diagnostic-harness runbook
(`diagnostic-harness.md`) which covers the broader 15-section
diagnostic-all pipeline.

**Audience.** Operator on a running daemon with WiringSentinel
born and the launchd plist active.

**Why it exists.** B363 (2026-05-17) shipped 6 LLM tools into
the tool_catalog but missed wiring them into archetype kits —
the gap surfaced months later when Alex opened the Capabilities
tab and saw 9 skills marked broken on every Security Swarm agent.
The existing 14 diagnostic sections check each substrate layer
(catalog, agents, handoffs, frontend, etc.) in isolation;
nothing was asking the cross-cutting questions. ADR-0081
introduced section-15 (cross-cutting check) + WiringSentinel
(scheduled monitor) + this runbook (operator playbook).

---

## At a glance

Three moving parts:

1. **`section-15-wiring-cross-check`** — disk-only harness section.
   Reads `config/tool_catalog.yaml` + `config/handoffs.yaml` +
   `examples/skills/` + `soul_generated/*.constitution.yaml` +
   `config/domains/`. Emits `report.md` + `coverage.json`.
2. **`WiringSentinel`** — guardian-genre singleton agent. Runs
   `wiring_audit.v1` against `coverage.json` and records the
   outcome to lineage memory + audit chain.
3. **`dev.forest.wiring-audit` launchd job** — fires
   `dev-tools/run-wiring-audit.command` every 4 hours
   (ADR-0081 D7). The wrapper regenerates `coverage.json`,
   resolves the sentinel's `instance_id`, and dispatches
   `wiring_audit.v1` via curl.

---

## What section-15 catches

Four cross-cutting questions the other 14 sections don't ask:

1. **Tool wiring coverage.** Every tool in `tool_catalog.yaml`
   should be carried by at least one archetype kit OR alive
   agent constitution. Orphan tools = cataloged-but-unreachable;
   either retire or assign to a kit.
2. **Skill requires resolve in catalog.** Every installed skill's
   `requires` list should reference tools that exist in the
   catalog. An unresolvable require is a dead skill (will fail
   on first dispatch).
3. **Skills have a carrier archetype.** Every installed skill
   should have at least one archetype whose kit carries all the
   skill's required tools. A skill with no carrier is wired in
   manifests but no role can actually run it.
4. **Handoff routes resolve end-to-end.** Every
   `(domain, capability) → skill` route in `handoffs.yaml` should
   (a) name a skill that exists in `examples/skills/`, and
   (b) have at least one entry_agent role in the domain whose
   archetype kit carries all the skill's requires.

Each FAIL is operator-actionable. ADR-0081 D5 — the sentinel
finds; the operator owns the substrate mutation that fixes the
gap.

---

## Severity scale

| Severity | Triggers | Operator action |
|---|---|---|
| **high** | Chain break, constitution parse failure, handoff with no entry agent at all | Immediate — chain integrity or domain definition is broken |
| **medium** | Orphan tools > 0, skills_unresolvable > 0, broken handoff routes > 0 | Triage within ~1 week — substrate inconsistency, not yet observable as user-facing failure |
| **low** | Tools in kits but no alive agent yet | Normal during rollouts — rebirth specific agents OR let the gap close naturally as the operator rebirths into new kits |
| **info** | All green, freshness notes only | Acknowledge and move on |

The 4-hour cadence means medium+ gaps surface within a quarter-
day of introduction. The runbook recommends a weekly review of
delegate-queued sentinel events.

---

## How to install (one-time)

```bash
# 1. Birth the sentinel
dev-tools/birth-wiring-sentinel.command

# 2. Install the launchd plist
cp dev-tools/launchd/dev.forest.wiring-audit.plist.template \
   ~/Library/LaunchAgents/dev.forest.wiring-audit.plist
launchctl bootstrap gui/$(id -u) \
   ~/Library/LaunchAgents/dev.forest.wiring-audit.plist

# 3. (Optional) Trigger first run immediately (don't wait 4 hours)
bash /Users/llm01/Forest-Soul-Forge/dev-tools/run-wiring-audit.command
```

---

## How to read the output

### Live HTML view

After any `diagnostic-all.command` run:

```
data/test-runs/diagnostic-all-<timestamp>/wiring-coverage.html
```

Single self-contained HTML page. Top has the verdict chip
(GAPS DETECTED / ALL WIRED) + per-category status chips
(tools, orphans, kit-only, skills, unresolvable, no-carrier,
handoffs, broken). Below: tables per check + per-tool carrier
matrix with color-coded rows (red orphan / orange kit-only /
white healthy).

### Coverage JSON

```
data/test-runs/diagnostic-15-wiring-cross-check/coverage.json
```

Machine-readable. Schema:

```json
{
  "timestamp": "...",
  "summary": {
    "tools_total": 67,
    "tools_orphan": 6,
    "tools_kit_no_agent": 6,
    "skills_total": 43,
    "skills_unresolvable": 0,
    "skills_no_carrier": 0,
    "handoffs_total": 14,
    "handoffs_broken": 7,
    "broken_constitutions": 3
  },
  "orphan_tools": ["decompose_intent.v1", ...],
  "kit_only_tools": [...],
  "skills_unresolvable": [{"skill": "...", "missing_from_catalog": [...]}],
  "skills_no_carrier": [{"skill": "...", "requires": [...]}],
  "handoffs_broken": [{"domain": "...", "capability": "...", "reason": "..."}],
  "tool_carriers": { "<tool_key>": {"archetypes": [...], "agents": [...]}, ... }
}
```

### Sentinel's audit memory

```bash
# Read recent wiring_audit outcomes from the sentinel's lineage memory
curl -s "http://127.0.0.1:7423/agents/<sentinel_instance_id>/memory?tags=wiring_audit&limit=10" \
  -H "X-FSF-Token: $FSF_API_TOKEN" | jq
```

Each entry has `kind=wiring_audit_outcome`, `chain_ok`,
`coverage_summary`, and an LLM-summarized `summary_text`.

### Launchd logs

```bash
tail -50 /tmp/forest-wiring-audit.out.log
tail -50 /tmp/forest-wiring-audit.err.log
```

---

## Recovery — common failure modes

### "no active WiringSentinel found"

The sentinel was never born or has been archived. Run
`dev-tools/birth-wiring-sentinel.command`. The launchd job
will pick up on the next 4-hour tick (or trigger manually
with the same command above).

### Section-15 crashed (rc >= 2)

Usually YAML parse failure in one of the inputs. The
`/tmp/forest-wiring-audit-section15.log` file has the
traceback. Most common cause: a hand-edit to
`tool_catalog.yaml` or `constitution_templates.yaml` produced
invalid YAML. Fix the YAML and re-run.

### Skill dispatch returns ok=false

Look at the skill response body for the error. Common shapes:
- `tool_call_refused`: a tool in the wiring_audit's step list
  has been removed from the sentinel's kit. Re-birth the
  sentinel.
- `audit_chain_verify failed`: the chain has an integrity
  break. The `require_chain_verify_before_audit` policy aborts
  the audit. See `diagnostic-harness.md` § Section 08 recovery.
- `tool_runtime not wired`: dispatcher wiring regression. See
  CLAUDE.md §2 (dispatcher wiring discipline).

### Sentinel false positives > 5%

Per the constitution's operator_duties: re-birth the sentinel.
The wrapping `bash dev-tools/birth-wiring-sentinel.command`
is idempotent only if no active sentinel exists, so first:

```bash
curl -X POST "http://127.0.0.1:7423/agents/<sentinel_instance_id>/archive" \
  -H "X-FSF-Token: $FSF_API_TOKEN"
# then re-run birth
```

### Coverage detects expected-but-unsolved gaps

Some orphan tools or broken handoffs are intentional during a
rollout. Per ADR-0081 D5, the sentinel does NOT suppress
findings — it surfaces them. Operator options:
1. Fix the substrate (assign tool to kit, build the skill, etc.).
2. Re-route the handoff to an existing skill or remove the
   handoff entry.
3. Retire the tool from the catalog.

Document the choice in the next commit burst's hippocratic gate
so future audits see why the gap persisted.

---

## Extending the audit

A new cross-cutting check is one new block in
`dev-tools/diagnostic/section-15-wiring-cross-check.command`'s
Python body, plus one new field in `coverage.json` and one new
section in `render-wiring-coverage.py`. The sentinel skill
(`wiring_audit.v1`) is generic over the coverage shape — no
skill change needed unless the new check carries severity that
should affect the punch-list summary.

---

## Reference

- `ADR-0081` — substrate wiring coverage decision doc
- `B393-B399` — implementation tranches T1-T6
- `B392` — archetype-kit gap fix that motivated the ADR
- `B363` — original missed-wiring incident
- `dev-tools/diagnostic/section-15-wiring-cross-check.command` — T1
- `dev-tools/diagnostic/render-wiring-coverage.py` — T2
- `examples/skills/wiring_audit.v1.yaml` — T4 skill manifest
- `dev-tools/run-wiring-audit.command` — T5 launchd wrapper
- `dev-tools/launchd/dev.forest.wiring-audit.plist.template` — T5 plist
