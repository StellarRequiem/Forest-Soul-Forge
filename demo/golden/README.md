# The Golden Demo — a governed, local, cryptographically-auditable agent

> **Provenance, not just credentials.** The funded agent-governance vendors
> (Cisco/Astrix, Oasis, GitGuardian) secure an agent's *secrets*. Forest secures
> what no vault does: **cryptographic proof of every action an agent took, under
> whose approval** — tamper-evident even against an insider who can forge hashes.

## Run it (≈1 second, no cloud, no API key)

```sh
.venv/bin/python demo/golden/golden_demo.py      # or double-click golden-demo.command
```

Everything runs locally in a throwaway temp dir, against Forest's **real**
`core.audit_chain.AuditChain` and the same `cryptography` ed25519 the daemon
itself uses. Nothing is mocked. Add `FSF_DEMO_SLOW=1` to pace it for a live audience.

## What it shows

| Phase | What happens | The primitive |
|------:|---|---|
| 🔨 **Forge** | A constitution compiled from trait sliders is hashed into a **content-addressed identity** (DNA). | `sha256(constitution)` |
| 👶 **Birth** | The agent gets a real **ed25519 keypair**; pubkey + DNA = its passport. | `Ed25519PrivateKey` |
| 🏃 **Run** | The agent requests `file_delete` on customer records. The genre's `risk_profile` **gates it on human approval**; the operator approves; it runs. | `config/genres.yaml` policy |
| 📜 **Audit** | Every step lands in a **hash-chained** log; agent actions are **ed25519-signed at emit time**. | `AuditChain.append(…, agent_dna=…)` |
| 🔍 **Verify** | Links intact, signatures valid. | `AuditChain.verify()` |
| 😈 **Tamper** | An insider rewrites the log — **twice**, both caught (below). | — |

## The kicker — why a hash chain alone isn't enough

The demo runs **two** insider attacks on the audit log:

1. **Lazy edit** — change the deleted target, leave the hash.
   → 🚨 caught by the **hash chain** (`entry_hash mismatch`).
2. **Expert edit** — change the target *and recompute the hash* so the chain
   check passes.
   → 🚨 caught by the **ed25519 signature** — it was made over the *original*
   action, and the attacker has no private key, so they cannot re-sign the lie.

That second case is the whole point: **the signature provides provenance the
hash chain cannot.** An attacker who fully controls the log file still cannot
fabricate or alter what an agent did.

## Why it matters (the market)

- **OWASP Agentic Top-10** (Dec 2025): ASI03 *Identity & Privilege Abuse*, ASI10 *Rogue Agents*.
- **EU AI Act Art. 12**: high-risk systems must keep **automatic**, tamper-evident lifetime logs.
- The agent-governance category is funding fast (Cisco→Astrix ~$400M, Oasis $120M B, GitGuardian $50M C) — almost all through the *credential/identity* lens and assuming *cloud* agents. **Local-first + cryptographic action-provenance is the unclaimed intersection.** This demo is that intersection, running.

## Verify the claims yourself

The underlying tamper-evidence is also exercised as a standalone battery:

```sh
.venv/bin/python scripts/verify_audit_chain.py    # genesis, linkage, 4 tamper classes
```
