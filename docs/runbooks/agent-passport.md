# Agent Passport (ADR-0061) — Operator Roaming Runbook

Operator workflow for letting an agent run on a machine other
than its birth machine without weakening the K6 hardware-binding
protection.

## What a passport is

A passport is a Forge-signed certificate authorizing a specific
agent to run on a specific set of hardware fingerprints, with an
optional expiration. The receiving Forge daemon validates the
passport before bypassing K6 quarantine.

Cryptographically: an ed25519 signature by the operator's master
keypair over a canonical-JSON body containing the agent's
identity (dna + instance_id + public_key) + the authorized
fingerprints + the issued/expires timestamps.

## When you need one

- **Same operator, multiple machines.** You birth an agent on
  your desktop and want to run it on your laptop too. Mint a
  passport authorizing both fingerprints.
- **Roaming with expiration.** Going on a trip, want the agent
  on the laptop for 7 days, then back to desktop-only. Mint a
  passport with `expires_at` 7 days out.
- **Sharing an agent with another operator.** Another operator
  copies your `operator_pubkey.txt` content into their trust
  list; you mint a passport authorizing their machine's
  fingerprint; they receive both the agent artifacts + the
  passport.

## What you don't need a passport for

- Running the agent on its birth machine. K6 hardware binding
  matches automatically.
- Pre-ADR-0061 agents that have no `hardware_binding` block in
  their constitution. They run anywhere (the K6 quarantine
  doesn't apply).

## How the substrate works

1. Every Forge daemon, on first startup, generates an
   **operator master keypair** stored in the AgentKeyStore.
   Public key written to `data/operator_pubkey.txt`.
2. The daemon trusts its own operator master by default
   (auto-included in the trust list).
3. Additional trusted operators are added by editing
   `data/trusted_operators.txt` (one base64 pubkey per line,
   `#` comments allowed) — or via the `FSF_TRUSTED_OPERATOR_KEYS`
   env var pointing at a different file.
4. When an agent loads, K6 fires:
   - Constitution has no `hardware_binding` → load.
   - Binding matches current host → load.
   - Binding mismatch + valid passport authorizes current host
     → load.
   - Binding mismatch + no/invalid passport → quarantine.

## Operator workflow today (B247)

The substrate is in place; the operator-facing CLI + HTTP
endpoints are queued for B248+. For now, mint passports
programmatically:

```python
from forest_soul_forge.security.operator_key import resolve_operator_keypair
from forest_soul_forge.security.passport import mint_passport
import json
from pathlib import Path

priv, pub_b64 = resolve_operator_keypair()
passport = mint_passport(
    agent_dna="abc123",                # from agents table
    instance_id="operator_companion_abc123abc123",
    agent_public_key_b64="<base64 from agents.public_key>",
    authorized_fingerprints=[
        "<birth machine fp>",
        "<other machine fp>",          # the roaming target
    ],
    operator_private_key=priv,
    issuer_public_key_b64=pub_b64,
    expires_at="2026-08-12T00:00:00Z", # optional
)

agent_dir = Path("data/souls/operator_companion_abc123abc123/")
(agent_dir / "passport.json").write_text(json.dumps(passport, indent=2))
```

Then copy the entire agent directory (soul.md, constitution.yaml,
passport.json) to the target machine + place it under that
machine's `data/souls/` so the daemon picks it up.

Receiving machine prerequisites:
- Its `data/trusted_operators.txt` must contain the issuing
  operator's master public key (the `pub_b64` value above).
- The agent's private key must also be transported via a side
  channel — passport authorizes WHERE the agent can run; it
  doesn't transport the key. Per ADR-0049 the private key
  lives in the AgentKeyStore; cross-machine private-key
  transfer is the operator's responsibility and is a separate
  threat-model conversation.

## Trust establishment

To trust another operator's signed passports:

1. Get their `data/operator_pubkey.txt` content (44-character
   base64 string).
2. Append a line to your `data/trusted_operators.txt`:

   ```text
   # operator_alex (~AP~)
   abc123-base64-string-here
   ```

3. Restart the daemon (the trust list is cached per-process)
   OR call `forest_soul_forge.security.trust_list.reset_cache()`
   programmatically.

## Recovery scenarios

- **Lost operator master key.** Existing passports stay valid
  (their signature is preserved on disk). You can't mint new
  passports. Recovery: regenerate a master keypair; every
  receiving machine has to re-add the new pubkey to their
  trust list, and you have to re-mint every passport you want
  to keep current. This is intentionally painful — the master
  key is the root of trust.
- **Passport expired.** Mint a new one with a later
  `expires_at`. The old passport stays on disk for audit
  history but doesn't pass validation.
- **Hardware fingerprint changed** (e.g., Linux disk swap
  changing the machine-id). Re-mint the passport with the new
  fingerprint added.

## Failure modes the quarantine surfaces

When passport validation fails, the K6 quarantine descriptor
gains two extra fields visible to the operator via dispatcher
diagnostics + the eventual `agent_passport_refused` audit event:

| `passport_reason` | What to do |
|---|---|
| `passport.json parse failed: ...` | The file isn't valid JSON. Re-mint or recover from backup. |
| `signature verification failed` | The passport was tampered with OR the issuer's private key has changed. Re-mint with the current operator master. |
| `issuer public key not in trusted list` | Add the issuer's pubkey to `data/trusted_operators.txt`. |
| `passport expired at ...` | Re-mint with a later `expires_at`. |
| `current hardware fingerprint not in authorized list` | Re-mint with the current fingerprint added to `authorized_fingerprints`. |

## References

- ADR-0061 — Agent Passport for Cross-Machine Roaming
- ADR-0049 — Per-Event Signatures (the ed25519 substrate)
- ADR-003X K6 — Hardware fingerprint binding
- `src/forest_soul_forge/security/passport.py` — mint + verify
- `src/forest_soul_forge/security/operator_key.py` — master keypair
- `src/forest_soul_forge/security/trust_list.py` — trust loader
- `src/forest_soul_forge/tools/dispatcher.py::_hardware_quarantine_reason` — K6 quarantine integration
