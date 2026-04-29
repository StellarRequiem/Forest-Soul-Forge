# Triune constitution seeds — ADR-003X K4

A **triune** is a sealed bond of three peer-root agents with
complementary roles. The default Forest triune ships as
**Heartwood / Branch / Leaf** — synthesizer, proposer, critic — but
the role names are aliases over a small set of cognitive functions
and operators are free to swap names without touching the
mechanics.

| File                              | Role          | Function                      |
|-----------------------------------|---------------|-------------------------------|
| `heartwood.constitution.yaml`     | synthesizer   | reconciles, ships final answer|
| `branch.constitution.yaml`        | proposer      | generates options             |
| `leaf.constitution.yaml`          | critic        | dissents, surfaces objections |

## Bond mechanics

Each constitution carries a `triune` block:

```yaml
triune:
  bond_name: aurora              # shared by all three sisters
  partners: [<id_a>, <id_b>]     # the OTHER two instance_ids
  restrict_delegations: true     # SAFETY DEFAULT
```

When `restrict_delegations: true`, `delegate.v1` refuses any
`target_instance_id` not in `partners`. **`allow_out_of_lineage=True`
does NOT bypass this** — the bond is sealed by the constitution, not
the call. Out-of-bond attempts emit an `out_of_triune_attempt`
audit event so the operator sees them.

In-triune calls auto-bypass the lineage gate (sisters are peer
roots; requiring `allow_out_of_lineage=True` on every call would be
ceremony without value). The corresponding `agent_delegated` event
carries `triune_internal: true` and `triune_bond_name: <name>` so
the audit chain shows which calls were bond-internal.

## Birth flow

These files are **seeds**. The fields filled at `/birth` time:

- `agent.dna` / `agent.dna_full` — generated per-agent
- `constitution_hash` — computed after all fields are settled
- `triune.bond_name` — operator-supplied (e.g. `aurora`)
- `triune.partners` — the other two `instance_id`s

The `fsf triune <bond_name>` CLI (ADR-003X K4) wraps this:

1. `/birth` Heartwood, Branch, Leaf in sequence
2. write `triune.partners` into each constitution
3. emit one `triune.bonded` ceremony event recording the bond

If any `/birth` fails, the partial births are rolled back via
`/archive` so the operator never sees a half-formed triune.

## Swapping the names

If your operator wants Sage / Maker / Skeptic instead of
Heartwood / Branch / Leaf:

1. Copy these three files; rename them; rename `agent.agent_name`.
2. Keep `agent.role` mapped to the canonical functions
   (`synthesizer` / `proposer` / `critic`) — `triune_consult.v1`
   dispatches off role, not name.
3. The `fsf triune` CLI accepts a `--seed-dir` flag pointing at
   your renamed files.

## Why these three roles

Three is the minimum set that preserves both **dissent** (Leaf vs.
Branch) and **resolution** (Heartwood between them). Two agents
can disagree but not arbitrate; four+ adds coordination cost
without adding cognitive function.
