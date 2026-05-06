# ADR-0050 — Encryption at Rest (Registry + Audit Chain + Memory)

**Status:** Proposed (2026-05-05). Phase 4 of the security-hardening
arc. Pairs with ADR-0049 (per-event signatures). ADR-0049 stops
forgery; ADR-0050 stops disclosure. Both close the audit-chain-on-
disk concern from the 2026-05-05 outside review.

## Context

Forest stores everything in plaintext on disk:

- `examples/audit_chain.jsonl` — canonical event log (the live default
  per `daemon/config.py`). 1,200+ entries on the live install.
- `data/registry.sqlite` — derived index over the audit chain (agents,
  conversations, turns, memory entries, scheduled task state, plugin
  grants, idempotency, etc.).
- `data/soul_generated/` — birthed agent artifacts: `soul.md` and
  `constitution.yaml` per agent. Contains the agent's voice + the
  operator's trait choices.
- Per-agent memory store within `registry.sqlite` (memory_entries
  table) — agent-authored content with operator/lineage scope.
- `data/forge/skills/installed/` + `data/plugins/` — operator-installed
  skill manifests + plugin binaries.

The 2026-05-05 outside security review flagged this as the biggest
practical hole:

> No encryption at rest (biggest practical hole). Audit chain
> (data/audit_chain.jsonl), memory, SQLite registry, skills,
> everything is plaintext. It's tamper-evident via SHA-256 hash
> chaining + prev_entry_hash/entry_hash, and verify_audit_chain.py
> walks it cleanly. But any process with read access to ~/.forest/
> or the Docker volume can just cat your entire agent history,
> memories, constitutions, and tool calls. The project explicitly
> says "No Encryption by Default" and defers encrypted secrets store.
> This is by design for simplicity, but it means disk-level compromise
> = game over.

The reviewer's diagnosis is right. ADR-0049 (per-event signatures)
addresses **integrity** (forgery resistance); ADR-0050 addresses
**confidentiality** (disclosure resistance). The two are orthogonal
mitigations and ship in parallel.

This ADR specifies a **mixed-encryption posture** that balances
confidentiality with the existing operational property of "the chain
is readable for self-verification without operator action." Sensitive
content gets encrypted; structural metadata stays clear so the chain
can hash-verify on startup without unlocking the master key first.

## Decision

This ADR locks **seven** decisions:

### Decision 1 — Three-tier classification

Forest's on-disk state splits into three tiers based on sensitivity:

| Tier | What | Treatment |
|---|---|---|
| **Sensitive** | event_data payloads in audit chain entries; memory_entries.body in registry; soul.md narrative; constitution.yaml policies | **Encrypted** at rest |
| **Structural** | audit chain seq, timestamp, agent_dna, event_type, prev_hash, entry_hash, signature; agent rows (instance_id, dna, role, genre, status, posture, public_key); conversation rows (id, domain, status); idempotency keys | **Plaintext** — required for verification + lookup without master-key unlock |
| **Public** | examples/plugins/*/plugin.yaml; examples/skills/*.yaml; config/*.yaml; docs/; src/ | **Plaintext** — intended to be readable; not data, just code/config |

This is the same separation that JWE / Sigstore / etc. use:
**ciphertext for the body, cleartext for the envelope.** The
operational property "verifier can check chain integrity without
decrypting" is preserved.

### Decision 2 — SQLCipher for registry.sqlite

The registry SQLite database (`data/registry.sqlite`) gets full
encryption via **SQLCipher** — a drop-in encrypted SQLite that
implements page-level AES-256-CBC. The Python `sqlcipher3` package
is API-compatible with stdlib `sqlite3`; the registry's per-thread
connection proxy (B143) just opens via `sqlcipher3.connect()` instead
of `sqlite3.connect()` and runs `PRAGMA key='...'` on each connection.

Why SQLCipher (not at-application-layer encryption):

- **Whole-file encryption** — including indices, journals, WAL.
  Application-layer encryption leaves indexes plaintext.
- **Mature** — used by Signal, 1Password, etc.
- **API-compatible** — minimal change to registry/registry.py.
- **Performance** — ~5-15% overhead for most workloads. Negligible
  at Forest's tick rate.

The cost: cross-platform builds. SQLCipher needs to be compiled
separately from stdlib sqlite3. macOS/Linux operators install via
`pip install sqlcipher3-binary` (pre-built wheels available); Windows
operators may need to compile.

### Decision 3 — Per-event encryption for audit chain

The audit chain JSONL stays one-line-per-event but each line splits
into a clear envelope + encrypted body:

```json
{
  "seq": 1234,
  "timestamp": "2026-05-06T03:58:25Z",
  "agent_dna": "abc123",
  "event_type": "tool_call_dispatched",
  "prev_hash": "f00...",
  "entry_hash": "ba1...",
  "signature": "ed25519:...",
  "encryption": {
    "alg": "AES-256-GCM",
    "kid": "master-2026-05",
    "nonce": "base64-12-bytes",
    "ct": "base64-ciphertext-of-event_data"
  }
}
```

When encryption is enabled:
- `event_data` (the field that today carries the JSON payload of the
  event — e.g., tool_call args + result_digest, memory body, etc.)
  is REPLACED by `encryption.ct` (the encrypted form)
- The plaintext is still part of the canonical-JSON form used for
  `entry_hash` (so hash-chain verification doesn't change), but the
  ciphertext is what's persisted. Verifier decrypts before re-hashing.
- `kid` (key id) lets us rotate master keys without re-encrypting
  the historical chain — old entries still decrypt with the
  master-key generation they were encrypted under (Decision 6).

When encryption is disabled (operator opt-out OR pre-ADR-0050 legacy
entries), `event_data` stays clear and `encryption` is absent. Verifier
handles both.

**Why per-event, not whole-file:**

- **Append-only preserved** — appending a new encrypted line is
  identical to appending a plaintext line. No file-level rewrites.
- **Streaming verify still works** — verifier reads one line at a
  time; doesn't need the whole file in memory.
- **Per-key rotation** — old entries with old key, new entries with
  new key, naturally coexist.
- **Selective decryption** — operator can decrypt a single line for
  inspection without decrypting the whole chain.

### Decision 4 — Memory / soul / constitution encrypted at write

`registry.conversations.memory_entries.body` (the memory content
itself) is encrypted at the application layer before insertion. The
SQLCipher layer (Decision 2) is whole-file; this is per-cell within
that — additional protection so a hypothetical SQLCipher break still
exposes only the row-level structure, not the memory content.

`data/soul_generated/<agent>.soul.md` and `<agent>.constitution.yaml`
files written via the soul renderer are encrypted at write time.
File extension stays `.md.enc` and `.yaml.enc` so the operator can
see what's encrypted vs. plaintext at a glance.

This is "defense in depth" — sensitive data has TWO encryption layers
(SQLCipher + per-cell, or per-file). Operator opt-out is single-knob:
`FSF_AT_REST_ENCRYPTION=false` disables everything (DB stays
SQLCipher-without-key — operator can opt out of file encryption
specifically via `FSF_ENCRYPT_FILES=false`).

### Decision 5 — Master key in OS keychain (KeyStore Protocol reuse)

The master key for at-rest encryption uses the SAME `KeyStore`
Protocol abstraction defined in ADR-0049. SoulUX default:
**macOS Keychain entry** at `dev.forest.master_key`. Cross-platform
fallback: operator-supplied passphrase at daemon startup, derived
to a master key via Argon2id (memory-hard KDF resistant to GPU
brute-force).

Master-key options ranked:

| Option | Security | UX | Notes |
|---|---|---|---|
| macOS Keychain (Secure Enclave-backed) | Best | Frictionless | SoulUX default on M-series; auto-unlock on operator login |
| Operator passphrase (Argon2id) | Strong | Friction at every boot | Cross-platform fallback |
| HSM-backed | Best | Friction (operator buys hardware) | Future, T16 |
| File on disk (operator-managed) | Weak | Frictionless | Defeats the purpose unless the file lives on a different volume |

Operator picks via `FSF_MASTER_KEY_BACKEND={keychain,passphrase,
hsm,file}` (default `keychain` on macOS, `passphrase` elsewhere).

**Why reuse ADR-0049's KeyStore:** the per-agent private keys (ADR-
0049) and the master encryption key (this ADR) are different
concerns but use the same abstraction. One KeyStore surface for
both keeps the substrate small.

### Decision 6 — Mixed legacy / encrypted chain (no rewrites)

Existing audit chain entries stay plaintext. New entries (post-ADR-
0050) are encrypted. The chain is read with a transparent
"decrypt-if-encrypted" wrapper: lines with `encryption` field
decrypt; lines without stay as-is.

**Why not re-encrypt legacy entries:** rewriting the chain to encrypt
old entries breaks the append-only invariant (every line's
entry_hash would change). Append-only IS the integrity primitive;
breaking it to add a confidentiality property is a worse tradeoff
than accepting the legacy plaintext window.

**Operator path forward:** operators concerned about the legacy
plaintext window can:
1. Archive the existing chain (rename to `chain.legacy.jsonl`)
2. Start a fresh chain on the next agent birth
3. Keep the legacy chain encrypted at the filesystem level (FileVault,
   LUKS, etc.) — out of Forest's scope but a real mitigation

### Decision 7 — Schema is additive (kernel ABI compatible)

Per ADR-0044 Decision 3 the audit chain schema is a v1.0 ABI surface.
This ADR's changes are ADDITIVE:

- New OPTIONAL `encryption` object on audit chain entries
- New OPTIONAL columns on memory_entries / agents / conversations to
  flag "encrypted body"
- Registry schema migration v16 → v17 adds the encryption-flag columns

No breaking change to:
- Tool dispatch protocol
- Plugin manifest schema v1
- Constitution.yaml schema (the file format on disk gets `.enc`
  variants but the semantic schema is unchanged)
- HTTP API contract (operator unlocks at startup via a single
  knob; the API surface doesn't expose master keys)
- CLI surface (one new `fsf encrypt` family; existing commands
  behave identically)

External integrators reading the chain at v1.0:
- Pre-ADR-0050 reader → ignores `encryption` field, reads legacy
  plaintext entries fine
- Post-ADR-0050 reader → handles both legacy plaintext and new
  encrypted entries

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | KeyStore master-key extension | Reuse ADR-0049's KeyStore Protocol; add `master_key` slot. Wire in lifespan startup. | 1 burst |
| T2 | sqlcipher3 integration | Add sqlcipher3-binary to daemon extras. Switch registry/registry.py from sqlite3 to sqlcipher3 (per-thread connection proxy already abstracts this). PRAGMA key on each connect. | 1-2 bursts |
| T3 | Per-event encryption in audit_chain.py | New AES-256-GCM wrapper. AppendEvent gains an `encrypt: bool` flag (default True post-ADR-0050). Verifier extends to handle `encryption` field. | 1-2 bursts |
| T4 | Memory body encryption | Per-cell encryption at memory_write.v1 / memory_recall.v1 boundaries. Schema v17 adds `body_encrypted: bool` column. | 1 burst |
| T5 | Soul + constitution file encryption | Write to .enc files; reader decrypts transparently. Soul renderer + constitution loader both pass through the encryption layer. | 1 burst |
| T6 | Operator UX: passphrase prompt + Keychain integration | If KeyStore says no master key on first boot, prompt for passphrase (interactive) OR refuse if non-interactive. macOS Keychain entry creation for first-time operators. | 1 burst |
| T7 | Migration runbook + key-backup workflow | docs/runbooks/encryption-at-rest.md: how to enable, how to back up the master key, what to do if the key is lost (the answer is "the data is gone — design for it"), legacy chain handling. | 0.5 burst |
| T8 | CLI: `fsf encrypt` family | Subcommands: `fsf encrypt status` (what's encrypted), `fsf encrypt rotate-key` (key rotation via SQLCipher rekey + new audit chain kid), `fsf encrypt decrypt-event <seq>` (operator-debug single event). | 1 burst |

Total estimate: 7-9 bursts. Comparable to ADR-0049's effort.

## Consequences

**Positive:**

- Closes the highest-impact disclosure hole the outside review flagged.
  Disk compromise no longer = full agent history exposed.
- Pairs with ADR-0049: integrity + confidentiality together for the
  audit chain.
- Defense-in-depth: SQLCipher (file level) + per-cell memory
  encryption + per-event audit chain encryption. Layered failures
  mean an attacker who breaks one layer still hits the next.
- Aligns with industry patterns (JWE envelope, Sigstore-style
  separation of metadata and payload).
- Reuses ADR-0049's KeyStore — single key-management abstraction
  for both agent private keys and master encryption key.

**Negative:**

- Adds substantial implementation complexity (8 tranches, 7-9
  bursts). One of the larger ADRs in the security-hardening arc.
- Master-key loss = data loss. Operator MUST back up the master
  key (or accept the risk). Documented in T7 runbook.
- SQLCipher cross-platform build complexity. Pre-built wheels
  cover macOS + Linux x86_64 / arm64; Windows may need manual
  build. Acceptable given Forest's macOS-first posture.
- Per-event encryption costs ~50-200µs per emit. Negligible at
  agent-tick rates but noticeable when bulk-replaying a long chain
  for verification.
- Legacy plaintext window — pre-ADR-0050 entries stay unencrypted.
  Operators concerned about that window get the recommendation in
  Decision 6 (filesystem-level encryption like FileVault).

**Neutral:**

- Doesn't change Forest's threat model. Trusted-host assumption
  remains. This ADR makes the "what if the host gets compromised"
  scenario have a softer landing — secrets stay sealed if the
  master key is sealed (Keychain / Secure Enclave), but a compromised
  host that ALSO captures the master key is fully exposed.
- Doesn't address network exposure. ADR-0050 is on-disk; HTTP
  API auth (T25 / B148) is the corresponding network-layer fix
  already shipped.
- Doesn't address process-memory exposure. A live daemon with
  master key in RAM is exposed to ptrace / dump-attach attacks.
  Acceptable per the trusted-host model; mitigations (PTRACE_DENY,
  hardened runtime) are platform-level concerns.

## What this ADR does NOT do

- **Does not encrypt config/*.yaml.** Trait tree, genres, tool
  catalog, constitution templates — operator-customizable text
  configuration. Not data. Not encrypted.
- **Does not encrypt examples/.** Plugins + skills shipped as
  canonical authored content. Public.
- **Does not encrypt source code or docs/.** Code is open source;
  docs are public.
- **Does not promise zero-knowledge** — daemon must hold the master
  key in memory to read its own state. Anyone with debugger access
  to the daemon's process gets the master key.
- **Does not address backup encryption.** Operator's backup tool
  (Time Machine, rsync, etc.) gets a snapshot of encrypted-at-rest
  files. Backup unencryption (operator's choice) is out of scope.
- **Does not bundle filesystem-level encryption.** macOS FileVault,
  Linux LUKS, BitLocker — those are operator/OS concerns. ADR-0050
  adds an additional layer beneath them.
- **Does not address post-quantum cryptography.** AES-256-GCM is
  classically secure; quantum attacks against it require ~2^85
  operations even with Grover's algorithm. The `alg` field in the
  encryption envelope leaves room for future PQC migration.
- **Does not break the kernel/userspace boundary.** Per ADR-0044
  Decision 3, audit chain schema is a v1.0 ABI surface; this is an
  additive change (allowed). Schema migration v16 → v17 follows
  the established additive pattern.

## References

- ADR-0005 — Audit chain (the JSONL format this ADR extends)
- ADR-0006 — Registry as derived index (what gets SQLCipher'd)
- ADR-0007 — FastAPI daemon (where master key gets loaded at startup)
- ADR-0022 — Memory subsystem (where memory body encryption happens)
- ADR-0025 — Threat model v2 (the trusted-host assumption this
  ADR doesn't break)
- ADR-0027 — Memory privacy contract (the existing scope-based
  privacy model — encryption is a different layer beneath it)
- ADR-0033 — Security Swarm (parallel multi-tier security model)
- ADR-0042 — v0.5 product direction (the SoulUX flagship surface
  this ADR's UX targets)
- ADR-0043 — MCP plugin protocol (plugins land in plaintext under
  data/plugins/ — out of scope here, future ADR could encrypt
  installed plugin binaries)
- ADR-0044 — Kernel positioning + SoulUX (the kernel/userspace
  boundary this ADR respects)
- ADR-0049 — Per-event digital signatures (companion ADR;
  ADR-0049 stops forgery, ADR-0050 stops disclosure)
- 2026-05-05 outside security review (Cowork session 87fd4f13) —
  the "no encryption at rest = biggest practical hole" finding

## Credit

The "biggest practical hole" framing came from the 2026-05-05 outside
security review. The mixed-encryption posture (envelope clear,
payload encrypted) came from the plan-before-act discussion in the
same Cowork session — it lets ADR-0049's chain-verifier work without
master-key access. The KeyStore reuse came from ADR-0049's
abstraction, deliberately designed for this.
