# 2026-05-17 quarantine rebirth — lineage record

**Date:** 2026-05-17 evening
**Driver:** B376
**Operator decision:** rebirth (operator's choice from
{archive-only, rebirth, repair-yaml}); see also `agent_quarantine.yaml`
which captured the pre-rebirth state.

## Pre-rebirth state

Three active agents born 2026-05-07 carried a manually-appended
free-text "override" block at EOF of their constitution YAML:

```
# --- override ---
you are the first version of a personal companion and i want you to help me build a great system for agents to grow
```

The text is bare prose, not a YAML key:value pair. The parser
raised "while scanning a simple key... could not find expected
':'". Diagnostic harness section 05 (agent-inventory) reported
each as FAIL.

Per CLAUDE.md architectural invariant — "Constitution hash is
immutable per agent" — the YAML could NOT be rewritten to fix
the parse. Doing so would change constitution_hash and break
audit chain integrity for every entry referencing the old hash.

Pre-fix mitigation: B369 introduced `config/agent_quarantine.
yaml` to surface broken-constitution agents as INFO (not FAIL)
in the harness while awaiting operator decision. Quarantine
manifest was the paper trail. The three quarantined entries:

| Agent | Role | Instance ID |
|---|---|---|
| Kraine | system_architect | system_architect_054edc592917 |
| Victor | knowledge_consolidator | knowledge_consolidator_9dd33078e7bd |
| chaz | software_engineer | software_engineer_871a237714a1 |

## Operator decision

Alex chose **rebirth** (not archive-only, not repair-yaml). New
instance_ids minted via the proper birth pipeline so each agent
gets a clean role-derived constitution; the old instance_ids are
archived with a reason recording the lineage decision.

## Rebirth lineage

Driver: `dev-tools/rebirth-quarantined-agents.command`. For each:

1. `POST /archive` with `instance_id=<old>`, `reason=<lineage
   string>`, `archived_by=alex`. Audit chain records the
   `agent_archived` event.
2. `POST /birth` with `role=<role>`, `agent_name=<name>`,
   `agent_version=v1`, `owner_id=alex`. Daemon mints a new
   `instance_id` with a clean role-derived constitution; audit
   chain records the `agent_created` event with a fresh
   `constitution_hash`.

Result (from `data/test-runs/rebirth-2026-05-17.json`):

| Agent | Role | Old instance | New instance |
|---|---|---|---|
| Kraine | system_architect | `system_architect_054edc592917` | `system_architect_946d6c0cad98` |
| Victor | knowledge_consolidator | `knowledge_consolidator_9dd33078e7bd` | `knowledge_consolidator_13ff42f35f82` |
| chaz | software_engineer | `software_engineer_871a237714a1` | `software_engineer_c1be854eadef` |

Both event pairs (archive of old + birth of new) land in the
audit chain at their respective seq numbers. The chain itself is
the canonical lineage record; this doc cross-references the two
sides for operator-readable narrative.

## What the rebirth dropped

The manually-appended override prose from the old constitutions
did NOT carry over. That text was operator-authored guidance
that lived outside any schema-defined constitution field, and
the schema doesn't have a "personal preamble" element today.

If the operator wants comparable agent-specific guidance on the
new instances, the proper substrate is:
- **ADR-0036 per-agent posture** — operator-authored notes/policies
  that the daemon resolves at dispatch time without changing the
  constitution hash.
- **ADR-0072 behavior provenance preferences** — operator
  preferences resolved through the precedence ladder.

Adding free text directly to the constitution YAML again would
recreate the parse-failure pattern; the rebirth path was chosen
specifically to avoid that recurrence.

## Quarantine manifest disposition

After this commit, `config/agent_quarantine.yaml` has its three
entries removed. Schema_version + the comment header remain so
the manifest is ready for future quarantine events.

## Verification

After the daemon next loads:

1. Diagnostic harness section 05 (agent-inventory) — the three
   old instance_ids no longer surface (archived); the three new
   instance_ids appear as PASS with clean constitution parse.
2. `examples/audit_chain.jsonl` contains six new entries (three
   `agent_archived` + three `agent_created`) with the timestamps
   from the rebirth run.
3. `data/test-runs/rebirth-2026-05-17.json` retains the lineage
   pairs for any future reference.

## Cross-references

- B369 (`d6937ab`) — agent quarantine manifest (introduced the
  paper trail).
- ADR-0001 — constitution as identity (the invariant that
  forbade YAML repair).
- ADR-0036 — per-agent posture (substrate for future agent-
  specific guidance).
- `data/test-runs/rebirth-2026-05-17.json` — machine-readable
  lineage log.
