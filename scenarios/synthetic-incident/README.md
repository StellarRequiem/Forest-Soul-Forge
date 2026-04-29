# Scenario: synthetic-incident

The full ADR-0033 Phase D+E run frozen at the moment of completion.

## What's in here

| File | Size | Contents |
|---|---|---|
| `audit_chain.jsonl` | ~570 KB | 500 events ending in the canonical 47-event chain (seqs 453-499) |
| `registry.sqlite` | ~990 KB | 199 agents across every role family |
| `data/soul_generated/` | ~50 KB | Birthed agents' soul.md + constitution.yaml |

## The headline beat

The chain culminates in a four-level cross-tier delegation through
the Security Swarm:

```
LogLurker (security_low)
  └─ morning_sweep skill triggers a finding
     └─ delegate.v1 → AnomalyAce (security_mid)
          └─ investigate_finding skill correlates
             └─ delegate.v1 → ResponseRogue (security_mid)
                  └─ contain_incident skill quarantines
                     └─ delegate.v1 → VaultWarden (security_high)
                          └─ key_audit skill runs key_inventory
```

Forty-seven audit events captured this end-to-end. Every link has its
own `agent_delegated` event. The whole story plays out in the chain
itself — see the Audit tab to walk it.

## Use it

```bash
./scenarios/load-scenario.command synthetic-incident
./start.command
```

Presenter script: [`../scripts/synthetic-incident.md`](../scripts/synthetic-incident.md)

## Refresh from live state

If you want to re-snapshot this scenario from your current daemon state
(after a fresh swarm-bringup or other meaningful run):

```bash
cp audit_chain.jsonl scenarios/synthetic-incident/audit_chain.jsonl
cp registry.sqlite scenarios/synthetic-incident/registry.sqlite
rm -rf scenarios/synthetic-incident/data/soul_generated
mkdir -p scenarios/synthetic-incident/data
cp -r data/soul_generated scenarios/synthetic-incident/data/
```
