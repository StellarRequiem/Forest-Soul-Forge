# Per-Event Signatures (ADR-0049) — Operator Runbook

What changed in v0.6 with ADR-0049 + how to operate around it.

This is the §0 Hippocratic-gate companion to the technical ADR —
it spells out what to do when the keystore breaks, how to read
the signature surface, and what the strict-mode verifier is for.

---

## What changed

Forest's audit chain (ADR-0005) was tamper-EVIDENT — sha256 hash
linkage detects rewrites, but doesn't prove WHO emitted each
event. With root access an attacker could rewrite the whole chain
with self-consistent hashes and claim any agent did anything.

ADR-0049 closes that gap. Every agent-emitted event now carries
an ed25519 signature over its `entry_hash`. The signature is
verifiable against the agent's public key (stored in the
registry's `agents.public_key` column and in the agent's
`soul.md` frontmatter). No private key ever leaves the
`AgentKeyStore` — verification works with public material alone.

Result: the chain is now tamper-PROOF for agent-emitted events.
An attacker without the agent's private key cannot forge entries
that pass verification, even with full disk access.

---

## What's where

| Surface | Lives in | Notes |
|---|---|---|
| Per-agent private key | `AgentKeyStore` (backed by ADR-0052 secret store) | macOS default: Keychain; non-Darwin default: `~/.forest/secrets/secrets.yaml` (INSECURE). |
| Per-agent public key (canonical) | Agent's `soul.md` frontmatter (`public_key:`) | Base64-encoded raw 32-byte ed25519 public key. |
| Per-agent public key (lookup) | `agents.public_key` column | Same value. Rebuilt from soul.md frontmatter at registry reingest time. |
| Per-event signature | New `signature` field on each audit-chain entry | Format: `"ed25519:" + base64(64 bytes)`. Outside `entry_hash`. |
| Sign-on-emit | `AuditChain.append` → `self._signer(...)` | Daemon lifespan wires the closure; resolves agent_dna → instance_id → AgentKeyStore.fetch → sign. |
| Verify-on-replay | `AuditChain.verify` → `self._verifier(...)` | Daemon lifespan wires the closure; resolves agent_dna → agents.public_key → ed25519.verify. |

---

## Operating

### Run a verify

The `audit_chain_verify.v1` builtin tool walks the live chain
and runs both hash-chain + signature verification:

```bash
# Tolerant mode (default) — legacy pre-ADR-0049 entries pass with
# hash check only.
curl -s -H "X-FSF-Token: $(grep FSF_API_TOKEN .env | cut -d= -f2)" \
     http://127.0.0.1:7423/agents/<some_agent>/tools/call \
     -H "Content-Type: application/json" \
     -d '{
       "tool_name": "audit_chain_verify",
       "tool_version": "1",
       "tool_args": {},
       "tool_version_for_audit": "1",
       "session_id": "verify-$(date +%s)"
     }'

# Strict mode — every agent-emitted entry MUST be signed.
# Use this for compliance snapshots + tamper-proof archival.
curl ... -d '{
       ...
       "tool_args": { "strict": true },
       ...
     }'
```

The response shape:

```json
{
  "output": {
    "ok": true,
    "entries_verified": 8870,
    "broken_at_seq": null,
    "reason": null,
    "unknown_event_types": [],
    "unknown_event_types_count": 0
  }
}
```

On a strict-mode failure for a legacy entry:

```json
{
  "output": {
    "ok": false,
    "broken_at_seq": 4321,
    "reason": "strict mode: agent-emitted entry has no signature"
  }
}
```

### Read a signed entry

Chain entries with signatures look like:

```json
{
  "seq": 8543,
  "timestamp": "2026-05-12T15:30:42Z",
  "prev_hash": "abc...",
  "entry_hash": "def...",
  "agent_dna": "1f2e3d4c5b6a",
  "event_type": "tool_call_dispatched",
  "event_data": {...},
  "signature": "ed25519:MEUCIQ..."
}
```

The `signature` field is OUTSIDE `entry_hash` per ADR-0049 D4 —
the hash is computed first, then signed, then the signature is
attached. So `entry_hash` is stable whether or not a signature is
present.

### Filter signed vs unsigned events

```bash
# Every signed entry:
jq 'select(.signature != null)' examples/audit_chain.jsonl

# Every legacy unsigned agent-emitted entry (pre-ADR-0049 +
# legacy-keypair-less agents):
jq 'select(.agent_dna != null and .signature == null)' \
   examples/audit_chain.jsonl

# Count signed vs unsigned per agent:
jq -r 'select(.agent_dna != null) |
       [.agent_dna, (if .signature then "signed" else "unsigned" end)] |
       @tsv' examples/audit_chain.jsonl |
   sort | uniq -c | sort -rn
```

---

## Failure modes + recovery

### Operator loses the keystore (or it gets corrupted)

**The agent's identity is permanent.** An ed25519 keypair is
bound to the agent at birth (ADR-0049 D1) — alongside DNA +
constitution_hash — and cannot be regenerated without effectively
killing the agent.

If the keystore is lost or unrecoverable:

- Past chain entries signed by that agent still verify against
  the public key (which lives in soul.md + agents.public_key,
  independently of the keystore).
- Future entries from that agent will land **unsigned** because
  the signer closure returns None when AgentKeyStore.fetch returns
  None. Strict-mode verifier will refuse the chain from that
  point forward for that agent.
- Recovery option: archive the agent (sets status='archived',
  emits agent_archived event). Future operator-emitted events on
  that instance_id are unaffected. The agent itself can no longer
  meaningfully act.
- Recovery option (last resort): operator can **re-birth** with
  the same trait profile to get a fresh keypair, but the new
  agent has a NEW instance_id, NEW public_key, and NEW chain of
  signed events. There is no continuity with the lost agent;
  past signed events from the old instance_id stay verifiable
  but no new ones can sign as that old identity.

**Operationally:** back up the keystore the same way you back up
the audit chain. On macOS, Keychain has its own backup integration;
on non-Darwin the file-backed store at `~/.forest/secrets/secrets.yaml`
needs to be in your normal backup rotation.

### "verifier raised: ..." in a verify result

The verifier closure threw an exception (registry read failure,
malformed public key in agents column, etc.). The chain is refused
on that entry. Read the daemon's startup_diagnostics to confirm
the signer/verifier closures wired correctly at lifespan:

```bash
curl -s http://127.0.0.1:7423/healthz | jq '.startup_diagnostics[]
  | select(.component == "audit_chain_signer")'
```

A `"status": "failed"` here means signing IS NOT happening — new
events land unsigned. Fix the underlying cause (registry
unavailability, ImportError on the cryptography package, etc.)
and restart the daemon.

### Chain has both signed and unsigned entries for the same agent

Expected and normal:
- Births / archive events are operator-emitted (agent_dna=None)
  and never signed.
- An agent born before ADR-0049 had no keypair → all its events
  stay unsigned forever.
- An agent born after ADR-0049 with a temporarily-missing
  keystore (during the keystore-corruption recovery window) will
  have a gap of unsigned events.

The audit chain is **append-only** (per ADR-0005). Past entries
are never re-signed retroactively — re-signing rewrites history.
Operators DO NOT need to "fix up" old unsigned entries; they
stay unsigned forever, and the verifier's tolerant mode
acknowledges that legacy reality.

---

## Strict mode — when to use it

The tolerant verifier (default) is the right posture for ongoing
operation: a fresh daemon comes up, walks the chain, accepts
both legacy unsigned and post-ADR-0049 signed entries.

**Strict mode** is for **point-in-time compliance snapshots**:

- Archival: "verify the chain from a fresh-install agent's birth
  to today is 100% signed." Use strict + verify the response
  shows `"ok": true`. Save the response + chain hash as your
  compliance artifact.
- Tamper-proof archival: bundle the chain + the strict-mode
  verify response + the agents.public_key column dump. Any
  future tampering with the chain will fail strict verification.
- Trust-but-verify external integration: an external integrator
  who runs Forest in production and pipes the chain into their
  SIEM can validate every event is properly signed before
  ingesting.

**Don't use strict on a chain that contains legacy entries** —
it will refuse at the first one. Use it only on chains where
every agent was born after ADR-0049 + had its keypair the entire
time.

---

## References

- ADR-0049 (Per-Event Digital Signatures)
- ADR-0052 (Pluggable Secrets Storage — the substrate
  AgentKeyStore wraps)
- ADR-0005 (Audit Chain — the hash-chain layer signatures add to)
- ADR-0001 D2 (Identity Invariance — public_key is part of the
  agent's content-addressed identity post-ADR-0049)
- `src/forest_soul_forge/security/keys/agent_key_store.py`
- `src/forest_soul_forge/core/audit_chain.py`
  (`set_signer` / `set_verifier`)
- `src/forest_soul_forge/daemon/app.py` (lifespan wiring of both
  closures)
