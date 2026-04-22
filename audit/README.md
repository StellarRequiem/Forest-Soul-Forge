# Audit Chain

This directory holds the **append-only, hash-linked audit log** for this
Forest Soul Forge instance. Every agent creation, constitution regeneration,
manual override, finding emission, and policy-violation detection is recorded
as one line in `chain.jsonl`.

Full design rationale and threat model:
[`docs/decisions/ADR-0005-audit-chain.md`](../docs/decisions/ADR-0005-audit-chain.md).

---

## What each line looks like

One JSON object per line, keys sorted, no whitespace:

```json
{"seq":0,"timestamp":"2026-04-21T15:30:00Z","prev_hash":"GENESIS","entry_hash":"9f3…","agent_dna":null,"event_type":"chain_created","event_data":{"schema_version":1}}
```

Fields:

| Field          | Meaning                                                                 |
| -------------- | ----------------------------------------------------------------------- |
| `seq`          | Monotonic counter. Genesis is 0; every append increments by 1.          |
| `timestamp`    | UTC ISO-8601. **Not hashed** — clock skew would corrupt verification.   |
| `prev_hash`    | The previous entry's `entry_hash`. Genesis stores the literal `GENESIS`. |
| `entry_hash`   | SHA-256 over canonical JSON of `{seq, prev_hash, agent_dna, event_type, event_data}`. |
| `agent_dna`    | 12-char short DNA of the agent this event concerns, or `null` for system events. |
| `event_type`   | One of the known types (see below) or a forward-compat extension.        |
| `event_data`   | Payload object. Schema depends on `event_type`.                         |

## Known event types (v0.1)

- `chain_created` — genesis entry, written once when the file is first created.
- `agent_created` — a new soul+constitution was generated from a trait profile.
- `agent_spawned` — an already-defined agent started a run.
- `constitution_regenerated` — an existing agent's constitution was rebuilt (template or trait change).
- `manual_override` — operator bypassed a policy; should be rare and carry justification in `event_data`.
- `drift_detected` — runtime detected trait or policy drift from the recorded DNA/hash.
- `finding_emitted` — agent produced a security finding.
- `policy_violation_detected` — an agent attempted an action its constitution forbade.

Unknown `event_type` values do not fail verification — they produce a warning.
This is deliberate forward-compat: a newer runtime can write new event shapes
without requiring every operator to simultaneously update their verifier.

---

## Threat model (read this)

**v0.1 is tamper-EVIDENT, not tamper-proof.**

- A root attacker with write access to `chain.jsonl` *and* access to the
  builder code can forge a complete, internally consistent chain. SHA-256
  with no external anchor is not a defence against that attacker class.
- What the chain *does* guarantee: if the operator or any tool modifies a
  single line in place, `audit_chain.verify()` will report the seq where
  the break occurs. This is the "operator-honest-but-forgetful" threat
  model — the tool tells you when something got mangled.
- Single-writer assumption. Concurrent appends from separate processes are
  undefined behavior. Don't do that.

If you need genuine tamper-proof audit logs, that's out of scope for v0.1.
Options on the table for later phases: Merkle anchoring to an external
timestamp service, append-only filesystem features, or off-box replication.

---

## Operator rules

1. **Do not edit `chain.jsonl` by hand.** Any edit will break verification
   at the seq you touched and all seqs after it. The file is meant to be
   read, not mutated. If you genuinely need to record something that
   doesn't fit an existing event type, add a new event type — don't
   retroactively rewrite history.
2. **Do not delete lines.** Truncating the chain is itself a tamper event
   and leaves the chain in a state where the last-known-good entry cannot
   be recovered from the file alone.
3. **Back up before running any tool that touches `audit/`.** A simple
   `cp chain.jsonl chain.jsonl.bak` before an experiment is enough to
   survive accidents during development.
4. **Run verify regularly.** Check the chain before and after anything
   interesting — before a deploy, after a crash, before filing a report
   based on the logs.

---

## Verifying the chain

From the repo root, with `src/` on the path:

```python
from pathlib import Path
from forest_soul_forge.core.audit_chain import AuditChain

result = AuditChain(Path("audit/chain.jsonl")).verify()
print(result)
```

`result.ok` is `False` on the first structural break; `result.broken_at_seq`
and `result.reason` point at the offending entry. `result.unknown_event_types`
lists any forward-compat event types encountered (these do not flip `ok`).

For a smoke test against a synthetic chain, run:

```
python3 scripts/verify_audit_chain.py
```

---

## Storage & retention

- Unbounded growth is fine for v0.1 — the chain is text and grows slowly.
- Do not rotate or compress live chains. If you want archival, copy the
  whole file to immutable storage; don't modify in place.
- `.gitignore` the chain file if your instance is private; the schema and
  tooling are the only things that belong in version control.
