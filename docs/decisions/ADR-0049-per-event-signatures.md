# ADR-0049 — Per-Event Digital Signatures for Agent Events

**Status:** Accepted 2026-05-12. **T1 + T4 shipped (B242 + B243).**
T1 wraps ADR-0052 `SecretStoreProtocol`; T4 wires birth-time
ed25519 keypair generation through the soul.md frontmatter + the
new `agents.public_key` column (schema v18→v19). T5 (sign-on-emit)
+ T6 (verifier extension) + T7 (strict mode) + T8 (runbook)
queued.

T1 ships as a thin wrapper over ADR-0052's secret-store substrate
(T1-T3 collapsed because ADR-0052 already provides the three
backends file / keychain / vaultwarden this ADR called for). The
wrapper lives at `src/forest_soul_forge/security/keys/` and
exposes `AgentKeyStore.store/fetch/delete/list_agent_ids` with
bytes in/out, base64-encoded internally so the underlying string-
valued secret store handles the transport. Agent private keys
land under the secret name `forest_agent_key:<instance_id>`.

T4 wires the birth pipeline (`daemon/routers/writes/birth.py
::_perform_create`) to generate an `Ed25519PrivateKey` inside the
write lock, store the private bytes via `AgentKeyStore.store`,
then thread the base64-encoded public key into both
`SoulGenerator.generate(public_key=...)` (lands in soul.md
frontmatter) and through `parse_soul_file → ParsedSoul.public_key
→ _insert_agent_row` (lands in the `agents.public_key` column).
Schema migration v18→v19 adds the column.

Phase 4 of the security-hardening arc opened by the 2026-05-05
outside review. Pairs with ADR-0050 (encryption at rest) — both
close the "audit chain is tamper-evident, not tamper-proof" gap
the review surfaced.

## Design adjustment (post-drafting): leverage ADR-0052

Original Decision 2 specified a fresh ``KeyStore`` Protocol with
three new backends (memory_only / encrypted_file / keychain).
Between drafting and acceptance, ADR-0052 shipped a
``SecretStoreProtocol`` with the same backend coverage
(file / keychain / vaultwarden) for plugin secrets. The B242
implementation collapses ADR-0049 T1-T3 into a thin wrapper that
leans on that substrate:

- One operator-facing surface for "where do secrets live" (the
  ADR-0052 ``FSF_SECRET_STORE`` env var governs both plugin
  secrets and agent keys)
- One ``fsf secret`` CLI for both surfaces (with the secret name
  conventions making the namespaces visible)
- One set of backend conformance tests
- ed25519 bytes are base64-encoded at the wrapper boundary so the
  underlying string-valued store can hold them

The trade-off: the ``memory_only`` backend the ADR named for tests
is gone (FileStore in a tmpdir replaces it — equivalent on-the-
wire, slightly more I/O during test runs but still <0.1s for the
full suite). The encrypted_file backend the ADR named is a future
ADR-0052 extension, not a separate ADR-0049 deliverable.

Decisions 1, 3, 4, 5, 6 below are unchanged.

## Context

Forest's audit chain (ADR-0005) is currently **tamper-evident** via
sha256 hash-linking: each entry's `entry_hash` is computed over
the canonical-JSON of (seq, agent_dna, event_type, event_data,
prev_hash). Modifying any field invalidates the hash; modifying any
ENTRY invalidates every following hash; the chain refuses to verify
on the next `audit_chain_verify.v1` run.

The 2026-05-05 outside security review put the gap precisely:

> Hash chaining is solid on paper and the daemon refuses to start
> if verification fails. But the chain lives on disk. Root/malware
> can truncate, rewrite the entire chain, or replay events before
> the daemon notices. No per-event digital signatures with agent
> private keys (only content-addressed SHA-256 DNA hashes). No
> timestamps in some places to avoid clock skew.

The reviewer's diagnosis is right. With root access, an attacker can:

1. **Truncate** — chop the chain at some old seq, accept a small
   sliver of "missing recent events" as the cost
2. **Rewrite end-to-end** — replace every `prev_hash` and
   `entry_hash` to construct a self-consistent fake chain. The hash
   primitive is public; nothing prevents this.
3. **Replay** — re-emit a past event with current timestamp. The
   chain still verifies (entry_hash doesn't include timestamp per
   B134's canonical-form fix), but the agent that "did" the event
   wasn't actually involved this time.

What the chain CAN'T currently prove: **that the agent actually
authored the event**. The agent_dna in event_data names which agent
the daemon SAID emitted it. Without a signature, anyone with write
access to the file can claim any agent did anything.

This ADR closes that gap by adding **ed25519 digital signatures**
per event, signed by the agent's private key. The signature is
verifiable against the agent's public key (stored in the registry +
in the agent's soul.md frontmatter) without any secret being shared.

## Decision

This ADR locks **six** decisions:

### Decision 1 — ed25519 keypair per agent, generated at birth

When an agent is born (POST /birth), in addition to DNA derivation
(ADR-0002) and constitution composition (ADR-0004), the daemon
generates an **ed25519 keypair**:

- **Private key** stored in a per-agent KeyStore (Decision 2)
- **Public key** stored:
  - In the agent's registry row (new column `agents.public_key`,
    schema migration v15 → v16)
  - In the soul.md frontmatter (canonical artifact, content-
    addressable)

The keypair is bound to the agent's identity at birth — it's
immutable for the agent's lifetime, just like the constitution_hash
(per ADR-0007). An agent's identity is therefore the triple
(DNA, constitution_hash, public_key); all three together prove
"this is the same agent across time."

**Algorithm choice — ed25519:**
- Modern, well-audited (RFC 8032)
- Fast signing + verification
- Small key size (32 bytes private, 32 bytes public)
- Small signature size (64 bytes)
- Available in Python stdlib via `cryptography.hazmat.primitives.
  asymmetric.ed25519`
- Cross-platform, no special hardware needed

### Decision 2 — KeyStore abstraction with pluggable backend

The daemon uses a **`KeyStore` Protocol** abstraction. Concrete
backends ship as plugins (or built-in for SoulUX).

```python
class KeyStore(Protocol):
    def store(self, instance_id: str, private_key_bytes: bytes) -> None: ...
    def fetch(self, instance_id: str) -> bytes | None: ...
    def delete(self, instance_id: str) -> bool: ...
```

**Default backends:**

| Backend | Storage | Platform | Notes |
|---|---|---|---|
| `keychain` | macOS Keychain | macOS only | SoulUX default. Hardware-backed if Secure Enclave available. |
| `encrypted_file` | `data/agent_keys.db` (encrypted SQLite) | All | Master key from `FSF_KEYSTORE_PASSPHRASE` env or interactive prompt at lifespan startup. Fallback if Keychain unavailable. |
| `memory_only` | In-memory dict | All (test/dev) | Lost on restart. Test/CI only. |

Operator picks via `FSF_KEYSTORE_BACKEND={keychain,encrypted_file,
memory_only}` (default `keychain` on macOS, `encrypted_file`
elsewhere).

A future backend (T16 / ADR-0050) could be `hsm` for HSM-backed
keys. The Protocol abstraction means that's an additional plugin,
not a kernel surgery.

### Decision 3 — Sign every event with `agent_dna != None`

The audit chain has two kinds of events:

- **Operator-emitted** — births, archives, status changes initiated
  by the human operator. `agent_dna` is None.
- **Agent-emitted** — every dispatch, memory write, conversation
  turn, etc. that an agent performed. `agent_dna` is the actor.

ADR-0049 signs **only agent-emitted events**. Operator-emitted
events stay unsigned (the operator's signature would be a different
substrate — out of scope here).

This means:
- Every `tool_call_dispatched` / `_succeeded` / `_failed` carries
  a signature
- Every `memory_appended` / `_disclosed` carries one
- Every `conversation_turn` (when speaker is the agent) carries one
- Every `agent_delegated` carries one (signed by the delegator)
- `agent_birthed` does NOT — signed by the operator concept, not
  the agent (which doesn't exist yet at the moment of the event)

### Decision 4 — Signature is over `entry_hash`, stored as separate field

Keep `entry_hash` computation IDENTICAL to today (per B134 canonical
form: `sha256(canonical_json({seq, agent_dna, event_type, event_data,
prev_hash}))`). The signature is computed AFTER:

```
signature_bytes = ed25519_sign(agent_private_key, entry_hash_bytes)
```

The audit chain JSONL line shape becomes:

```json
{
  "seq": 1234,
  "timestamp": "2026-05-06T03:58:25Z",
  "agent_dna": "abc123",
  "event_type": "tool_call_dispatched",
  "event_data": { ... },
  "prev_hash": "f00...",
  "entry_hash": "ba1...",
  "signature": "ed25519:base64-encoded-64-bytes"   ← NEW (optional)
}
```

**Field semantics:**

- `signature` is OMITTED for unsigned (legacy / operator-emitted) events
- When present, format is `"ed25519:" + base64(signature_bytes)` —
  prefix lets future ADRs add other algorithms (e.g.,
  `"sphincs+:..."` for post-quantum)
- Verification: parse algorithm prefix, look up the agent's public
  key in registry, run algorithm-specific verify on
  `(public_key, entry_hash_bytes, signature_bytes)`

**Why not include signature in entry_hash:** if the signature were
part of entry_hash, the agent would need to compute its own signature
THEN re-hash including it, creating a chicken-and-egg situation.
Standard pattern (used by JWS, X.509, sigstore) is hash-then-sign
with separate signature field.

### Decision 5 — Verifier extension; legacy entries treated as unsigned

`audit_chain_verify.v1` extends to:

1. **Hash-chain verify** (existing) — compute expected entry_hash,
   compare to stored, refuse on mismatch
2. **Signature verify** (NEW) — for entries with `signature` field:
   - Parse algorithm prefix
   - Look up agent's public key from registry (via agent_dna)
   - Run algorithm-specific verify
   - Refuse on mismatch

**Legacy entries (unsigned, pre-ADR-0049):**

- Verifier treats them as "legacy unsigned" — passes hash-chain
  check, no signature check attempted
- A new audit event `legacy_unsigned_event` flag in
  startup_diagnostics surfaces the count so operator knows the
  pre-ADR-0049 portion of their chain
- **Operators do NOT re-sign legacy entries.** Re-signing rewrites
  history; the whole point of an append-only audit chain is that
  history is fixed. Legacy entries stay unsigned forever.
- Future event verifiers can reject unsigned entries when the
  threat model demands it (e.g., a "strict mode" verifier that
  refuses any unsigned post-ADR-0049 entry). Default: tolerant.

### Decision 6 — Schema is additive (kernel ABI compatible)

This ADR adds:
- New OPTIONAL field `signature` on audit chain entries
- New OPTIONAL column `agents.public_key` (registry schema v15 → v16)

Both are ADDITIVE changes — readers seeing them ignore unknown fields
gracefully, writers omitting them produce valid pre-v16 entries.

Per ADR-0044 Decision 3:
> Schema migrations — registry SQLite schema migrations are strictly
> additive; downgrade is allowed via rebuild_from_artifacts.

This is exactly the pattern. The audit chain v1 schema gets one
optional field; the registry schema gets one optional column. No
breaking change to any of the seven v1.0 ABI surfaces.

**External integrators reading the chain at v1.0:**
- Pre-ADR-0049 reader → ignores `signature` field, hash-chain
  verifies cleanly
- Post-ADR-0049 reader → uses signature when present, falls back
  to hash-chain-only on legacy entries

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | KeyStore Protocol + memory_only impl | **DONE B242 (T1+T2+T3 collapsed)** — `AgentKeyStore` thin wrapper at `src/forest_soul_forge/security/keys/` over the ADR-0052 `SecretStoreProtocol`. Inherits all three backends (file / keychain / vaultwarden) without duplicating substrate. ed25519 bytes base64-encoded at the wrapper boundary; agent keys stored under `forest_agent_key:<instance_id>`. 19 tests cover the wrapper (round-trip, overwrite, multi-agent isolation, namespace prefix lock, base64-corruption error path). | shipped |
| T2 | encrypted_file backend | **Subsumed by T1** — the ADR-0052 secrets store provides file + keychain + vaultwarden backends. The encrypted_file variant the original draft proposed becomes a future ADR-0052 extension if needed (the unencrypted FileStore today is `chmod 600`; OS-Keychain backend is the production path on Darwin). | superseded |
| T3 | keychain backend (macOS) | **Subsumed by T1** — ADR-0052 `KeychainStore` already covers this. AgentKeyStore inherits via the resolver. | superseded |
| T4 | Birth-time keypair generation | **DONE B243** — `_perform_create` (in `daemon/routers/writes/birth.py`, shared by /birth + /spawn) generates an `Ed25519PrivateKey` inside the write lock right after `instance_id` is computed. Private bytes go to `AgentKeyStore.store(instance_id, ...)`; public bytes are base64-encoded and threaded into both (a) `SoulGenerator.generate(public_key=...)` so the canonical frontmatter carries the key, and (b) through the registry's ingest path (`ParsedSoul.public_key` populated by `parse_soul_file`, written into the new `agents.public_key` column by `_insert_agent_row`). Schema migration v18→v19 adds the nullable column; the migration is `ALTER TABLE agents ADD COLUMN public_key TEXT` with no default — legacy pre-v19 agents stay NULL (verifier treats them as 'legacy unsigned' per ADR-0049 D5). The two surfaces (frontmatter + agents column) must agree at rebuild-from-artifacts time; a test asserts this. 6 new tests cover the path (frontmatter write, agents column write, agreement check, key-store fetch, distinct keypairs per agent, ed25519 round-trip validity). | shipped |
| T5 | Sign-on-emit | core/audit_chain.py extension: when emitting an event with agent_dna, look up the agent's private key, sign entry_hash, attach signature. | 1 burst |
| T6 | Verifier extension | audit_chain_verify.v1 + scripts/verify_audit_chain.py: parse signature, look up public key, verify ed25519. Legacy entries skipped gracefully. | 1 burst |
| T7 | Strict-mode verifier flag | Optional `--strict` flag for the CLI verifier that rejects ANY unsigned post-v16 event. | 0.5 burst |
| T8 | Documentation + migration runbook | docs/runbooks/per-event-signatures.md: what changed, key-rotation thoughts (deferred), key-loss recovery (no recovery — agent identity is permanent). | 0.5 burst |

Total estimate: 6-8 bursts. Largest single ADR implementation
in the security-hardening arc.

## Consequences

**Positive:**

- Closes the "audit chain is tamper-evident not tamper-proof" gap.
  An attacker with write access to the chain file can truncate or
  rewrite, but CANNOT forge new signed events without the agent
  private keys.
- Per-agent provenance: now there's cryptographic evidence the
  named agent did the named event, not just the daemon's claim.
- Compatible with the existing audit chain — additive schema field,
  no breaking change to existing readers / verifiers.
- Sets up future trust-extension primitives: cross-agent signed
  delegations, signed memory disclosures, signed handoffs. All
  build on the same per-agent ed25519 keypair.
- macOS Keychain integration uses Secure Enclave when available —
  hardware-backed key protection for free on M-series Macs.

**Negative:**

- Adds substantial implementation surface (8 tranches, 6-8 bursts).
  Not a one-burst close.
- Key loss = agent loss. If the operator's KeyStore is destroyed
  (e.g., reset macOS Keychain), the agent's private key is gone;
  it can never sign again. Existing signed events remain verifiable
  (their signatures predate the loss), but the agent can no longer
  emit verifiable events. Acceptable given Forest's "agent identity
  is permanent" model — this is consistent with constitution_hash
  immutability (ADR-0007). Operators who need rotation should
  archive the agent + birth a successor.
- Performance: ~50µs per signature (ed25519 is fast), ~150µs per
  verify. Negligible at agent-tick rates (single dispatch per
  several seconds). Bulk-verify of a 100k-entry chain would take
  ~15s — acceptable for a startup self-check, slow for interactive
  inspection. Future optimization: parallel verify, only verify
  recent entries on startup.
- Cross-platform variance: Keychain on macOS, encrypted file
  elsewhere. Operators moving between platforms experience different
  key-loss risk profiles. Documented in T8 runbook.

**Neutral:**

- Doesn't change the threat model documented in ADR-0025. Forest
  still assumes a trusted host. This ADR adds a layer of evidence
  IN-CASE the host turns hostile, without claiming defense against
  rooted compromise.
- Doesn't enable cross-agent verification of behavior across
  different Forest installations — that requires a public-key
  exchange protocol (deferred; T27.b possibly).
- Doesn't address signature rotation. Once the keypair is generated
  at birth, it's the agent's keypair forever. This matches Forest's
  identity model; if rotation becomes necessary in v0.7+, that's
  an amendment.

## What this ADR does NOT do

- **Does not encrypt the audit chain at rest.** That's ADR-0050
  (encryption at rest). The chain remains plaintext + hash-linked
  + signed. An attacker with read access can still see what events
  occurred; they just can't FORGE new ones convincingly.
- **Does not add operator signatures.** Birth, archive, and other
  operator-initiated events stay unsigned by the agent (because no
  agent existed yet, or the operator is the actor). A future ADR
  can add operator-side signatures (e.g., requiring a hardware key
  for ops actions).
- **Does not address key revocation.** Once a public key is
  registered for an agent, it can't be revoked or replaced. An
  agent whose private key is compromised has to be archived; future
  events from that agent's instance_id can't be trusted. The
  registry can record an `agent_compromised` event but the existing
  audit chain entries from that agent stay technically verifiable.
- **Does not implement signature aggregation / Merkle proofs.** Each
  event's signature is independent. Future ADRs could add Merkle
  trees for batch verification but that's out of scope.
- **Does not specify post-quantum migration.** ed25519 is
  classically secure. NIST PQC standards (e.g., SPHINCS+, Dilithium)
  are still maturing. The `"ed25519:"` algorithm prefix on the
  signature field leaves room — future ADR can add a parallel
  PQC algorithm and cross-sign during transition.
- **Does not break the kernel/userspace boundary.** Per ADR-0044
  Decision 3, audit chain schema is a v1.0 ABI surface; this is
  an additive change (allowed). Readers without ADR-0049 awareness
  ignore the new field gracefully.

## References

- ADR-0002 — Agent DNA + lineage (the existing per-agent identity
  primitive that signatures complement)
- ADR-0004 — Constitution builder (the immutable constitution_hash
  identity binding)
- ADR-0005 — Audit chain (the JSONL hash-chain this ADR extends)
- ADR-0007 — FastAPI daemon (signature emit happens in the daemon's
  audit-chain emit path)
- ADR-0025 — Threat model v2 (the trusted-host assumption this ADR
  doesn't break)
- ADR-0033 — Security Swarm (a parallel multi-tier security model)
- ADR-0044 — Kernel positioning + SoulUX (the kernel/userspace
  boundary this ADR respects via additive schema)
- ADR-0050 — Encryption at rest (companion ADR; closes the
  read-side of the audit-chain disk-exposure gap that this ADR
  doesn't address)
- B134 — audit chain canonical-form fix (the existing canonical-JSON
  shape this ADR builds on)
- 2026-05-05 outside security review (Cowork session 87fd4f13) —
  the "tamper-evident not tamper-proof" finding that triggered this
  ADR

## Credit

The "no per-event digital signatures with agent private keys" framing
came from the 2026-05-05 outside security review. The ed25519 +
KeyStore-Protocol shape came from the plan-before-act discussion
2026-05-06 in the Cowork session that opened Phase 4. The "additive
schema, kernel-compatible" framing matches ADR-0044's discipline.
