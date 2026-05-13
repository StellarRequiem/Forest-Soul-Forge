# ADR-0061 — Agent Passport for Cross-Machine Roaming

- **Status:** **Closed 2026-05-12.** All seven tranches shipped
  across Bursts 246 (T1-T3+T5-partial) + 247 (T4 + K6 integration)
  + 248 (T6 HTTP endpoint + T7 CLI subcommand + audit events).
  Passport substrate is operator-usable end-to-end: programmatic
  mint + verify, trust-list loader, `passport.json`-overrides-K6
  in the dispatcher, daemon `POST /agents/{id}/passport` endpoint,
  `fsf passport {mint,show,fingerprint}` CLI. Audit events
  `agent_passport_minted` + `agent_passport_refused` ship in
  KNOWN_EVENT_TYPES.
- **Date:** 2026-05-12 (drafted, accepted same day during the
  ADR-0046 license-pivot conversation).
- **Related:** ADR-0001 (DNA + content-addressed identity),
  ADR-0049 (per-agent ed25519 keypair + audit-chain signatures),
  ADR-003X K6 (hardware fingerprint binding), ADR-0046
  Amendment 1 (license switch to ELv2 — this ADR makes the
  "agents bound to your hardware at birth + passport for non-
  home systems" business model concrete).

## Context

Alex's 2026-05-12 license review locked the business model:

- Forest Soul Forge runs as a hosted service Alex operates.
- Operators birth agents on the hosted service.
- The agents themselves are downloadable artifacts: soul.md,
  constitution.yaml, private key bytes.
- Each agent is **hardware-bound at birth** to the machine it
  was first downloaded to (ADR-003X K6 — already shipped).
- Operators want their agents to **roam to other machines they
  own** (e.g., laptop ↔ desktop) without breaking the hardware
  binding's protection against unauthorized copies.

The hardware binding alone is too rigid for this. The K6 quarantine
check at agent load refuses any agent whose constitution's
`hardware_binding.fingerprint` doesn't match the current host —
which is correct for the "compromised agent gets exfiltrated to
attacker's box" threat, but blocks the operator's legitimate
"my laptop is also my machine" workflow.

This ADR adds the **agent passport** — a Forge-signed certificate
that authorizes a specific agent to run on a specific set of
hardware fingerprints, optionally with an expiration. The
operator's Forge instance signs the passport; the receiving
machine's Forge daemon verifies the signature against the
operator's published public key and accepts the agent if (a) the
signature is valid, (b) the current host fingerprint is in the
passport's authorized set, (c) the passport hasn't expired.

## Decisions

### Decision 1 — Operator master keypair

Every Forge daemon, on first startup, generates an **ed25519
operator master keypair**:

- Private key stored in the AgentKeyStore (ADR-0049 substrate)
  under the reserved name `forest_operator_master:default`.
  The colon-prefix matches the AgentKeyStore's namespacing
  convention. `default` allows future multi-operator support
  without schema change.
- Public key written to `data/operator_pubkey.txt` as a
  human-shareable base64 string + emitted in daemon startup
  diagnostics. This is what the operator copies to receiving
  machines to establish trust.

The master keypair is **bound to the operator's deployment**,
not to any individual agent. Multiple agents on the same Forge
share the same operator master.

The substrate (AgentKeyStore wrapping the ADR-0052 secret store)
inherits its security posture: macOS default = Keychain;
non-Darwin = file-backed (insecure, banner present per ADR-0052).
Operators serious about passport unforgeability should run on
macOS until the encrypted-file backend (ADR-0050) lands.

### Decision 2 — Passport format

A passport is a **JSON document with a detached signature**:

```json
{
  "version": 1,
  "agent_dna": "abc123",
  "instance_id": "operator_companion_abc123abc123",
  "agent_public_key": "base64-encoded raw 32 bytes",
  "authorized_fingerprints": [
    "fp_birth_machine_hex",
    "fp_laptop_hex"
  ],
  "issued_at": "2026-05-12T22:00:00Z",
  "expires_at": "2026-08-12T22:00:00Z",
  "issuer_public_key": "base64-encoded raw 32 bytes",
  "signature": "ed25519:base64-encoded 64-byte signature"
}
```

The signature is computed over the **canonical JSON
serialization of all fields except `signature`**. Same canonical-
form pattern as the audit chain (ADR-0005 + ADR-0049 D4) —
sort keys, no whitespace, hash before sign.

Field semantics:

- `version` — schema version. Starts at 1; bump if a future
  passport requires fields the v1 verifier can't understand.
- `agent_dna` + `instance_id` + `agent_public_key` — bind the
  passport to the specific agent. A receiver checking the
  passport must also check that the agent's actual public key
  (from soul.md frontmatter or registry) matches
  `agent_public_key`. A passport with a mismatched key is
  refused.
- `authorized_fingerprints` — list of hardware fingerprints
  (per ADR-003X K6 format) where the agent is permitted to
  run. Always includes the birth fingerprint as the first
  entry; additional entries are added when the operator
  explicitly authorizes roaming to new machines.
- `issued_at` / `expires_at` — RFC 3339 timestamps. `expires_at`
  is optional (null = no expiration). Operators using time-
  limited passports (e.g., a 7-day trip passport) set this
  explicitly.
- `issuer_public_key` — the operator master public key that
  signed this passport. Receivers verify the signature against
  this key + check that the key is in their **trusted issuers**
  list (Decision 4).

### Decision 3 — Mint surface

Passports are minted via a function-level API initially; the
HTTP endpoint comes in a follow-up burst. The Python signature:

```python
def mint_passport(
    *,
    agent_dna: str,
    instance_id: str,
    agent_public_key_b64: str,
    authorized_fingerprints: list[str],
    operator_private_key: bytes,
    issuer_public_key_b64: str,
    issued_at: str | None = None,    # defaults to utc_now()
    expires_at: str | None = None,   # None = no expiration
    version: int = 1,
) -> dict[str, Any]:
    """Build + sign a passport. Returns the passport dict
    ready for serialization to disk."""
```

The mint function does NOT consult the registry or AgentKeyStore —
it's a pure cryptographic primitive. The caller (the daemon
endpoint, the CLI subcommand) is responsible for resolving the
operator master keypair and the agent's public key from their
respective stores.

### Decision 4 — Trust configuration

A receiving Forge daemon validates passports against its
**trusted issuers** list. The list is operator-supplied via:

- `FSF_TRUSTED_OPERATOR_KEYS` env var pointing at a file
  containing one base64 public key per line, with optional
  `# comment` lines.
- Default location if env unset: `data/trusted_operators.txt`.
- Auto-trust the LOCAL operator's master key (the one in this
  daemon's AgentKeyStore) so passports for self-hosted agents
  validate without operator intervention.

A passport whose `issuer_public_key` isn't in the trusted list
is **rejected**, even if the signature is cryptographically
valid. This is the same trust model as TLS root CAs: signature
correctness is necessary but not sufficient — the operator must
explicitly trust the issuer.

T4 ships the trust config + the runtime quarantine integration
in a follow-up burst. T1-T3 ship the cryptographic primitives.

### Decision 5 — Quarantine integration

The existing ADR-003X K6 quarantine check fires at agent load
when constitution.hardware_binding.fingerprint doesn't match
current host. Post-ADR-0061 the check becomes:

1. If constitution has no `hardware_binding` block → pass
   (legacy pre-K6 agents).
2. If `hardware_binding.fingerprint` matches current host → pass
   (agent on its home machine).
3. If a passport.json exists alongside the agent's artifacts AND
   the passport validates (signature + trust + not-expired +
   current host in authorized_fingerprints) → pass.
4. Otherwise → quarantine. Daemon logs the reason + the agent
   stays unloaded.

This preserves the K6 protection (no unauthorized copies) while
enabling the operator's "my laptop is also my machine" workflow
via explicit passport.

Quarantine integration is T4 territory and ships in a follow-up
burst.

## Failure modes

- **Operator master key loss.** Without the private key, no new
  passports can be minted. Existing passports remain valid (their
  signature is preserved on disk). Operator recovers by
  regenerating the master keypair — but the new public key must
  be re-added to every receiving machine's trusted list, OR
  every agent re-minted with a new passport. This is
  intentionally painful; the master key is the root of the
  trust chain.
- **Passport tampering.** Modifying any field invalidates the
  signature. Receiver refuses.
- **Expired passport.** Operator re-mints with a new
  `expires_at`. Old passport stays on disk for audit but
  doesn't pass validation.
- **Hardware fingerprint changes** (e.g., disk replacement on
  Linux changing machine-id). Operator re-mints with the new
  fingerprint added to `authorized_fingerprints`.

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Operator master keypair bootstrap | New module `security/operator_key.py` with `resolve_operator_keypair() -> (priv_bytes, pub_b64)`. AgentKeyStore reserved name `forest_operator_master:default`. Lifespan calls it once at startup. Writes public to `data/operator_pubkey.txt`. Diagnostic line for visibility. | 0.5 burst (shipping B246) |
| T2 | Passport mint primitive | `security/passport.py::mint_passport(...)` — canonical-form serialize + ed25519 sign. Returns dict ready for json.dump. | 0.5 burst (shipping B246) |
| T3 | Passport verify primitive | `security/passport.py::verify_passport(passport_dict, trusted_pubkeys, current_hw_fp) -> tuple[bool, str]`. Returns (valid, reason). Refuses on: malformed, wrong signature, untrusted issuer, expired, current host not in authorized. | 0.5 burst (shipping B246) |
| T4 | Trust config + quarantine integration | **DONE B247** — `security/trust_list.py::load_trusted_operator_pubkeys()` loads from `FSF_TRUSTED_OPERATOR_KEYS` env var (default `data/trusted_operators.txt`); always includes the local operator master via `resolve_operator_keypair`. Comments + dedupe + missing-file-is-fine semantics. `_hardware_quarantine_reason` in dispatcher.py extended: on binding mismatch, looks for `passport.json` next to constitution; valid passport → bypass quarantine; invalid passport → quarantine descriptor carries `passport_reason` so operator can fix. 11 new tests cover the integration (5 quarantine scenarios + 4 trust-list loader cases). | shipped |
| T5 | Tests + runbook | **DONE B246+B247** — 19 passport tests + 7 operator-key tests in B246; 11 passport-quarantine + trust-list tests in B247. `docs/runbooks/agent-passport.md` covers operator workflow, recovery scenarios, trust establishment, and the failure-mode → action mapping. | shipped |
| T6 | Daemon HTTP endpoint | **DONE B248** — `POST /agents/{id}/passport` in `routers/passport.py`. Body: `authorized_fingerprints` (required, min 1) + optional `expires_at` + `operator_id` + `reason`. Resolves operator master via `resolve_operator_keypair`, agent public_key from registry, mints via `security/passport.py`, persists `passport.json` next to constitution under the write lock, emits `agent_passport_minted` audit event. Gated by `require_writes_enabled` + `require_api_token`. 404 on unknown agent, 409 if agent lacks public_key (pre-ADR-0049 legacy), 422 on empty fingerprint list. | shipped |
| T7 | CLI subcommand | **DONE B248** — `fsf passport` registered in `cli/main.py` via `cli/passport_cmd.py`. Three subcommands: `mint <instance_id> -f <fp> [-f <fp> ...]` posts to the HTTP endpoint; `show <instance_id> [--souls-dir DIR]` reads passport.json directly off disk + pretty-prints; `fingerprint` prints the local machine's hardware fingerprint (script-friendly: fp on stdout, source on stderr). Uses urllib + X-FSF-Token header matching the daemon's require_api_token contract. | shipped |

Audit events added to `core/audit_chain.py::KNOWN_EVENT_TYPES`: `agent_passport_minted` (emitted by the router on success — carries instance_id, issuer_public_key, authorized_fingerprint_count, issued_at, expires_at, operator_id, reason, passport_path) and `agent_passport_refused` (emitted by `HardwareQuarantineStep` in `tools/governance_pipeline.py` when the K6 quarantine descriptor surfaces a `passport_reason` — operator tried to roam but the passport didn't validate). The quarantine refusal message also updates to mention both `/hardware/unbind` AND `/passport` as remediation paths.

Total estimate: 3.5 bursts. **Shipped in 3.0 bursts** — T1-T3+T5(partial) in B246; T4 + K6 integration in B247; T6 + T7 + final T5 + audit events in B248.

## Consequences

**Positive:**

- Operator workflow: laptop ↔ desktop roaming without
  weakening the K6 hardware-binding protection.
- Lays the cryptographic foundation for the "agents are
  property of the operator" business model — the operator's
  master key is the root of trust; nobody can mint a valid
  passport without it.
- Time-limited passports become a real safety mechanism for
  travel scenarios ("7-day passport, then this agent is
  quarantined again").
- Builds cleanly on ADR-0049's ed25519 substrate. No new
  cryptographic primitives.

**Negative:**

- Operator master key is a new single point of failure. Lose
  it and roaming is broken until every agent is re-minted.
  Mitigation: the daemon's AgentKeyStore (Keychain default on
  macOS) is already a hardened storage layer.
- Adds a verification step at agent load. Cheap (single
  ed25519 verify + a few field checks) but non-zero. Cached
  per-process after first successful load to amortize.
- Trust-list distribution is operator-manual today. Future ADR
  could auto-distribute via a known well-known URL or
  TOFU-like first-contact, but v1 is "paste this string into
  the receiving machine's config."

**Neutral:**

- Doesn't change the agent's own keypair (ADR-0049). The
  agent still signs its own audit events with its own private
  key. The operator master signs the passport, which
  authorizes WHERE the agent can run; it doesn't sign on the
  agent's behalf.
- Doesn't change the audit chain shape. Passport mint + verify
  events emit as new event types (`agent_passport_minted`,
  `agent_passport_verified`, `agent_passport_refused`) but
  follow the same canonical-form contract.

## References

- ADR-0001 — DNA + content-addressed identity
- ADR-0046 Amendment 1 — license switch to ELv2 (motivates the
  business model this ADR makes concrete)
- ADR-0049 — Per-event signatures (ed25519 substrate this ADR
  reuses)
- ADR-003X K6 — Hardware fingerprint binding (the protection
  this ADR carefully extends, not replaces)
- ADR-0052 — Pluggable secrets storage (where the operator
  master private key lives via AgentKeyStore)
- `src/forest_soul_forge/security/operator_key.py` — T1
- `src/forest_soul_forge/security/passport.py` — T2 + T3
- `tests/unit/test_passport.py` — T5
- `docs/runbooks/agent-passport.md` — operator workflow
  (future)
