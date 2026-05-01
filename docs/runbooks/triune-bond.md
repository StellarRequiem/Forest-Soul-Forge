# Triune bond — operator runbook

**ADR:** [ADR-003X](../decisions/ADR-003X-open-web-tool-family.md) §K4
**Status:** Accepted (K4 shipped)

A triune is three peer-rooted agents bonded under a shared name with
sealed cross-bond delegation. The bond ceremony is a one-shot
operator action that patches each agent's constitution and emits a
single `triune.bonded` event in the audit chain.

This runbook covers the ceremony itself. For SW-track-specific
triune workflow (Atlas/Forge/Sentinel handoff), see
`sw-track-triune.md`.

## What "bonded" means at runtime

After the ceremony:

1. Each agent's constitution YAML carries `triune_bond: <name>` plus
   the two sister instance_ids (so any sister can identify the others
   without going through the registry).
2. `delegate.v1` checks the bond at every cross-agent call. If
   `restrict_delegations=true` (the default), out-of-bond delegations
   are refused with `out_of_triune_attempt` audit event.
3. `delegate.v1` calls between sisters carry `triune_internal: true`
   in the event_data, so an auditor can filter for bond-internal
   chains.
4. Spawning out of triune is allowed (a triune can have descendants);
   the bond is on the three peer roots.

## Bond three agents

```bash
fsf triune bond --name aurora \
  --instances <id_1> <id_2> <id_3>
```

Required:
- `--name`: bond name (free-form; e.g. `aurora`, `helios`,
  `sw_main`). Used in audit events + the constitution patch.
- `--instances`: exactly 3 distinct instance_ids of already-birthed
  agents.

Optional:
- `--operator`: operator id recorded in the ceremony event.
  Defaults to `$USER` (or `$USERNAME` on Windows, or `operator`).
- `--no-restrict`: opt out of the safety default. When set,
  `restrict_delegations=false` so `delegate.v1` will NOT refuse
  cross-triune calls. **Use only when the operator deliberately
  wants a porous triune** (default is sealed).

## What the daemon does on bond

```
POST /triune/bond
  ↓
1. Verify all 3 instance_ids exist in the registry
2. Verify all 3 are distinct
3. Verify none of them is already in another triune (one bond max
   per agent at v0.1)
4. Patch each agent's constitution.yaml with:
     triune_bond_name: aurora
     triune_sisters: [<id_1>, <id_2>, <id_3>]
     restrict_delegations: true   # or false if --no-restrict
5. Recompute each agent's constitution_hash with the patched bytes
6. Update the registry's constitution_hash for each agent
7. Append ONE ceremony event: { event_type: "ceremony",
     ceremony_name: "triune.bonded", bond_name, sisters, operator }
   to the audit chain
8. Return ceremony_seq + ceremony_timestamp + sealed flag
```

The whole sequence runs under the daemon's write lock. If any step
after #4 fails, the constitution patches stay (they're committed to
disk first); the daemon's response surfaces what step failed and
the operator decides whether to retry the bond or unbond manually.

## Bond invariants

- One bond per agent. Re-bonding a sister into a different triune
  requires unbonding first (no `unbond` endpoint at v0.1; archive +
  re-birth is the path).
- Bond name is unique per realm. Two triunes can't share a name in
  the same daemon.
- Bond-internal `delegate.v1` does NOT consume the lineage gate.
  Sisters can call each other regardless of who's whose ancestor.
- Out-of-bond `delegate.v1` STILL respects lineage. A bonded sister
  trying to call a non-sister still has to be in lineage.

## Audit events worth filtering for

| Event | Meaning |
|---|---|
| `ceremony` (with `ceremony_name: triune.bonded`) | bond formed |
| `agent_delegated` (with `triune_internal: true`) | bond-internal call |
| `agent_delegated` (with `triune_bond_name: <name>`) | call by a bonded sister, regardless of internal/external |
| `out_of_triune_attempt` | bonded sister tried to call out-of-bond and was refused (or audit-only if `--no-restrict`) |

The chronicle filter `--bond <name>` walks the chain for
`ceremony` + `agent_delegated.triune_bond_name` + `out_of_triune_attempt`
events for a given bond name. Useful for postmortem on a triune's
behavior:

```bash
fsf chronicle --bond aurora --md
```

## What the bond is NOT

- **Not a single agent.** Three agents stay distinct — distinct
  DNAs, distinct constitution_hashes, distinct memory stores. The
  bond is a relationship, not a merger.
- **Not a guarantee of cooperation.** Three bonded sisters can still
  refuse each other's `delegate.v1` if their genres' approval rules
  fire. The bond just lifts the lineage gate.
- **Not a permission elevation.** Each sister's tool-side-effect
  ceiling stays. Atlas in researcher genre still can't fire
  external-side-effect tools just because Forge can.

## Where to dig deeper

- **ADR-003X §K4**: triune spec
- **HTTP**: `daemon/routers/triune.py` — the bond endpoint
- **CLI**: `cli/triune.py` (`fsf triune bond`)
- **Delegator**: `tools/delegator.py` — bond enforcement at delegate.v1
- **Chronicle filter**: `chronicle/render.py::filter_by_bond_name`
- **SW-track usage**: `docs/runbooks/sw-track-triune.md`
