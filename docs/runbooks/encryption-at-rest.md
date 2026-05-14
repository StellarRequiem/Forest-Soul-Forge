# Encryption-at-Rest (ADR-0050) — Operator Runbook

What ADR-0050 ships, how to turn it on, how to back up the master
key, and what to do when something fires.

---

## What this is

Forest's persistence layers historically wrote everything plaintext:
audit chain JSONL, registry SQLite, memory bodies, soul + constitution
files. The 2026-05-05 outside security review flagged this as the
biggest practical hole — any process with read access to the daemon's
data directory could `cat` every agent's history, memories,
constitutions, and tool calls without lifting a finger.

ADR-0049 (per-event signatures, shipped Bursts 242-244) closed the
**integrity** half — the audit chain is now tamper-PROOF for
agent-emitted events. ADR-0050 (shipped Bursts 266-273) closes the
**confidentiality** half — every sensitive surface gets AES-256-GCM
encryption at rest under a single 32-byte master key.

When this is on, disk-level compromise stops being game over.

---

## What gets encrypted

Four persistence layers, one master key:

| Surface | Cipher | Burst |
|---|---|---|
| `data/registry.sqlite` | SQLCipher AES-256-CBC page-level | B267 (T2) |
| Each line of `examples/audit_chain.jsonl` (the `event_data` body) | AES-256-GCM per-event envelope | B268 (T3) |
| `memory_entries.content` rows inside the registry | AES-256-GCM application-layer | B269 (T4) |
| Soul + constitution files (`<agent>.soul.md.enc`, `<agent>.constitution.yaml.enc`) | AES-256-GCM file-level | B271-B272 (T5a + T5b) |

Three surfaces stay **plaintext by design** because the verifier and
the registry rebuild need to walk them without unlocking the key:

- Audit chain envelope fields: `seq`, `timestamp`, `agent_dna`,
  `event_type`, `prev_hash`, `entry_hash`, `signature`
- `agents` table columns: `instance_id`, `dna`, `role`, `genre`,
  `status`, `posture`, `public_key`
- `examples/plugins/`, `examples/skills/`, `config/`, `docs/`,
  `src/` — code and operator-asserted config, not data

This is the same separation JWE and Sigstore use: ciphertext for the
body, cleartext for the envelope. The hash-chain verifier checks
integrity without decrypting.

---

## Quick start — enable encryption

Two env vars in the daemon's environment. Pick a backend, set the
master env var, restart the daemon.

### macOS (Keychain — default)

```bash
export FSF_AT_REST_ENCRYPTION=true
# FSF_MASTER_KEY_BACKEND=keychain is the default on darwin
./start.command
```

First boot generates a 32-byte master key, stores it in the macOS
Keychain under `forest_master_key:default`, and encrypts everything
written going forward. Subsequent boots find the key, unlock the
encrypted surfaces, and the daemon comes up identical to plaintext
mode from the operator's perspective.

### Linux (file backend — default on non-darwin)

```bash
export FSF_AT_REST_ENCRYPTION=true
# FSF_MASTER_KEY_BACKEND=file is the default off-darwin
./start.command
```

The master key lives at `~/.forest/secrets/secrets.yaml` with
`chmod 600` permissions. **This is less secure than the Keychain**
— the raw key is on disk. Acceptable for single-operator boxes
with full-disk encryption (LUKS, etc.); not appropriate for
multi-tenant hosts.

### Headless / CI / hardened (passphrase backend)

```bash
export FSF_AT_REST_ENCRYPTION=true
export FSF_MASTER_KEY_BACKEND=passphrase
export FSF_MASTER_PASSPHRASE='your-operator-passphrase-here'
./start.command
```

The key is derived from the passphrase via Scrypt (`N=2^16, r=8, p=1`,
~250ms-1s wall time on modern hardware). Only a 16-byte salt persists
at `~/.forest/master_salt`. **The passphrase itself never touches
disk** — if the env var isn't supplied and stdin isn't a TTY, the
daemon refuses to boot rather than silently falling back to a
different backend.

For interactive boots (you're sitting at a terminal), unset
`FSF_MASTER_PASSPHRASE` and the daemon will prompt via
`getpass.getpass()` on startup.

---

## Verifying encryption is actually on

The lifespan emits `startup_diagnostics` entries you can grep for:

```bash
./start.command 2>&1 | grep encryption_at_rest
```

Expected on a happy-path encrypted boot:

```
encryption_at_rest: ok — master key loaded from keychain backend;
consumers (registry SQLCipher / audit-chain / memory body) wire up
via the same env var.
```

Plaintext boot (default — env var not set):

```
encryption_at_rest: off — FSF_AT_REST_ENCRYPTION not set; legacy
plaintext posture (registry, audit chain, memory bodies).
```

Degraded — env var on, key resolution failed (Keychain access denied,
salt file corrupted, etc.):

```
encryption_at_rest: degraded — FSF_AT_REST_ENCRYPTION=true but
master key unavailable: <error>. Daemon will run with the legacy
plaintext posture.
```

The degraded path lets the daemon stay up rather than refusing to
boot. Operators who want stricter behavior can grep the diagnostic
in their orchestration and refuse to mark the daemon healthy.

Sanity check at the data layer — `data/registry.sqlite` is no longer
a valid SQLite file under encryption:

```bash
# Plaintext (encryption off):
sqlite3 data/registry.sqlite "SELECT COUNT(*) FROM agents;"
# Encrypted (encryption on):
sqlite3 data/registry.sqlite "SELECT COUNT(*) FROM agents;"
# → Error: file is not a database
```

Audit chain — encrypted lines have an `encryption` envelope field:

```bash
head -1 examples/audit_chain.jsonl | python3 -m json.tool
# Plaintext: shows "event_data": {...}
# Encrypted: shows "encryption": {"alg": "AES-256-GCM", "kid": ..., ...}
```

---

## Backing up the master key

**The master key is unrecoverable if lost.** Encrypted data is
unrecoverable if the key is lost. Design for both.

### Keychain backend

```bash
# Get the raw base64 key out of Keychain:
security find-generic-password -s 'forest_master_key:default' -w

# Store the output in your password manager / fire safe / wherever
# you keep things you can't afford to lose.
```

### File backend

```bash
# Back up the secrets file:
cp -p ~/.forest/secrets/secrets.yaml ~/forest-master-key.backup
# Move that backup somewhere off the daemon's machine.
```

### Passphrase backend

The passphrase IS the backup. Store it in your password manager.
The salt at `~/.forest/master_salt` is NOT secret (it's an
anti-precomputation primitive), but it does need to be present
on every machine that will derive the same key — back it up
alongside the passphrase if you'll restore on a different host.

### Verifying a backup works

Stop the daemon, rename the live store, restore from backup, start
the daemon, verify encryption_at_rest reports `ok`. Spot-check a
character sheet for an encrypted agent — if the soul renders the
key restored cleanly.

---

## Key loss — the unrecoverable case

If you lose the master key (Keychain wiped, secrets file deleted,
passphrase forgotten), the encrypted data is gone. There is no
recovery. This is the design — the alternative is a key-escrow
mechanism that defeats the encryption.

What recovery looks like:

1. Stop the daemon
2. Move the encrypted artifacts somewhere out of the way (don't
   delete them in case the key resurfaces): `mv data/ data.lost/`
3. Move the encrypted audit chain: `mv examples/audit_chain.jsonl{,.lost}`
4. Move the encrypted soul artifacts: `mv data/soul_generated{,.lost}`
5. Decide whether to start over plaintext or re-enable encryption
   with a fresh master key — the existing agents are gone either
   way
6. Start the daemon. It bootstraps a fresh registry + empty audit
   chain

Operators concerned about this scenario should:

- Back up the master key as described above
- Test the backup restore on a sacrificial machine before relying
  on it
- For long-running production deployments, consider a key-rotation
  cadence (T8 — `fsf encrypt rotate-key`, queued) that lets you
  re-encrypt under a new key without losing the data

---

## Mixed deployments

Forest supports operators flipping `FSF_AT_REST_ENCRYPTION` at any
agent boundary. Pre-T5 plaintext agents and post-T5 encrypted
agents coexist in the same registry per ADR Decision 6:

- The audit chain reads handle both legacy plaintext and post-T3
  encrypted entries on the same file — see ADR Decision 6
- The soul + constitution file readers detect `.enc` variants at
  read time and decrypt; legacy `.soul.md` / `.constitution.yaml`
  files keep working unchanged
- The memory recall path detects the `content_encrypted` column
  flag per row — same table holds both shapes
- The registry's SQLCipher mode is whole-file; flipping the env
  on against a plaintext registry refuses with a clear error
  (T8 will ship the migration tool that re-encrypts in place)

If you turn encryption ON against a plaintext registry, the
daemon will refuse to start with a clear error:

```
RegistryError: registry file is plaintext but master_key was
supplied; refusing to overwrite. Run `fsf encrypt migrate` (T8)
to re-encrypt in place, or unset FSF_AT_REST_ENCRYPTION.
```

For now (pre-T8), the workflow is:

1. Stop the daemon
2. Move the plaintext data dir aside: `mv data/ data.plaintext/`
3. Set `FSF_AT_REST_ENCRYPTION=true`
4. Start the daemon — fresh encrypted registry boots
5. Manually re-import what you need from `data.plaintext/`

---

## Backend selection details

`FSF_MASTER_KEY_BACKEND` controls where the master key comes from.
Resolution priority: env var (if set to a recognized value) → platform
default. Unrecognized values fall back to the platform default rather
than failing — a typo doesn't silently route the daemon through an
unintended backend.

| Backend | Default on | Stores | Security |
|---|---|---|---|
| `keychain` | macOS | macOS Keychain (`security` cli surface) | Best — Secure Enclave-backed where available |
| `file` | Linux | `~/.forest/secrets/secrets.yaml` (chmod 600) | Adequate with FDE; weak without |
| `passphrase` | (explicit opt-in) | Operator memory or `FSF_MASTER_PASSPHRASE` env; salt at `~/.forest/master_salt` | Strong if passphrase is strong; depends on operator practice |
| `hsm` | (reserved T16) | — | Not implemented; raises `NotImplementedError` |

---

## Argon2id → Scrypt amendment

ADR-0050 Decision 5 named **Argon2id** for the passphrase KDF. The
shipped substrate (T6, B273) uses **Scrypt** instead. Reasons:

1. The `cryptography` library Forest already depends on ships
   `cryptography.hazmat.primitives.kdf.scrypt.Scrypt`. Adding
   `argon2-cffi` would mean a new transitive dep + native build
   tooling. Forest's pyproject deps audit is already open as a
   queued tech-debt item; minimizing new transitive deps in T6
   is the safer call.
2. Scrypt is memory-hard, well-vetted (used in Litecoin, Tarsnap),
   and adequate for the operator-passphrase threat model.
3. The KDF surface is intentionally small. An operator who needs
   Argon2id specifically for regulated infrastructure can swap by
   editing `security/passphrase_kdf.py` — the surgery is contained.

This is a substrate decision. ADR-0050 text will be amended at the
T8 close-out so the decision record matches the shipped code.

---

## Performance notes

Encryption adds measurable but acceptable overhead:

| Surface | Overhead | Notes |
|---|---|---|
| SQLCipher registry | ~5-15% on typical workloads | Page-level encryption is fast; negligible at Forest's tick rate |
| Audit chain append | ~50-200μs per event | AES-256-GCM is hardware-accelerated on Apple Silicon + modern x86 |
| Audit chain read (verify) | ~50-200μs per event | Verifier walks one line at a time; cumulative cost only matters on full-chain rebuilds |
| Memory recall | 1 decrypt per row | Hits the memory hot path; cache layer queued for v0.3 |
| Soul + constitution file reads | 1 decrypt per dispatch | Constitution is read multiple times per dispatch (initiative, posture, quarantine, etc.); same caching plan applies |
| Passphrase KDF (first boot only) | ~250ms-1s | Scrypt is intentionally slow; not a hot path |

If you measure your daemon at the limit of what your hardware can
sustain, the most impactful optimization is constitution caching
(queued). Encryption itself is rarely the bottleneck.

---

## Failure modes and what to do

### `RegistryEncryptionError: cipher_version probe failed`

The `sqlcipher3` Python wheel installed but linked against vanilla
SQLite. Reinstall:

```bash
pip uninstall sqlcipher3 sqlcipher3-binary
pip install sqlcipher3-binary  # ships pre-built wheels with bundled SQLCipher
```

### `DecryptError: entry encrypted under kid='...' but only kid='...' is loaded`

The chain has entries encrypted under an OLDER master key (key
rotation history). The current daemon was bootstrapped under a
newer key. Two options:

1. Restore the older master key from backup, then run
   `fsf encrypt rotate-key` (T8, queued) to re-encrypt the older
   entries under the current key
2. Accept the old chain as an archived artifact (operator decision
   per ADR-0050 Decision 6)

### `RuntimeError: FSF_AT_REST_ENCRYPTION=true but stdin is not a TTY and FSF_MASTER_PASSPHRASE is not set`

Non-interactive daemon under passphrase backend with no env supply.
Either:
- Switch to `FSF_MASTER_KEY_BACKEND=keychain` (macOS) or `file`
  (Linux), which don't need interactive input
- Supply the passphrase via env: `FSF_MASTER_PASSPHRASE='...'`
- Start the daemon interactively the first time so the prompt
  fires, then switch to non-interactive once the daemon is running

### `PassphraseKDFError: salt at <path> has wrong length`

The salt file is corrupted or was written by an incompatible
Forest version. Silently regenerating would orphan all data
encrypted under the old key, so the error is explicit. Decide:

- Restore the salt from backup (if you have one)
- Wipe and start over: `rm ~/.forest/master_salt` then restart
  — the encrypted data is unrecoverable but a fresh deployment
  comes up clean

### Daemon refuses to boot with `RegistryEncryptionError: plaintext database with key supplied`

The registry on disk is plaintext but you turned on encryption.
This is the migration case — `fsf encrypt migrate` (T8) will ship
the in-place rewrite. For now, follow the "Mixed deployments"
section above to start fresh under encryption.

---

## Threat model — what this covers and what it doesn't

**Covered:**

- Disk theft / cold-boot attack on a powered-down host
- Read access by another user on the same machine (with FDE
  protecting the system at large, or with `chmod` on the secrets
  file)
- Backup tape / cloud snapshot exposure
- Casual `cat` / `grep` access by a process running on the box
- Supply-chain artifact extraction (the encrypted data is opaque
  to anyone who exfiltrates it without the key)

**Not covered:**

- Debugger access to the running daemon process (the master key
  is in process memory by design — ADR-0025 trusted-host model)
- Compromised Keychain (Keychain trust model is the OS's; if a
  process running as the daemon user can read Keychain entries,
  it can read the master key)
- Compromised env var (operators using passphrase backend with
  `FSF_MASTER_PASSPHRASE` in a shell history or visible-to-other-
  users env are exposed)
- Side-channel attacks on the KDF / cipher (in scope for higher
  threat tiers, out of scope for the trusted-host model)
- Key rotation correctness across a partial-write window (T8
  ships the atomic rotation tool)

If your threat model includes any of the "not covered" items, the
trusted-host model alone isn't sufficient — you need OS-level
hardening (ptrace_scope=3, hardened-runtime entitlements) or HSM-
backed key sealing (ADR-0050 T16, reserved).

---

## See also

- [`docs/decisions/ADR-0050-encryption-at-rest.md`](../decisions/ADR-0050-encryption-at-rest.md)
  — the architectural decision record
- [`docs/runbooks/per-event-signatures.md`](per-event-signatures.md)
  — the integrity half (ADR-0049). Signatures + encryption are
  orthogonal mitigations and ship in parallel
- [`docs/runbooks/tool-sandbox.md`](tool-sandbox.md) — Phase-4
  security-hardening sibling (ADR-0051)
