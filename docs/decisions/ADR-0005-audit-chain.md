# ADR-0005 — Audit Chain (Append-Only Hash Chain, v0.1)

- **Status:** Accepted
- **Date:** 2026-04-21
- **Supersedes:** —
- **Related:** ADR-0002 (DNA/lineage), ADR-0004 (constitution builder)

> **Amendment 2026-04-21 (pre-implementation):** Hash function changed from BLAKE2b-256 to **SHA-256** for codebase consistency with ADR-0002 (DNA) and ADR-0004 (constitution hash). BLAKE2b's per-hash speedup is negligible at the scale this chain operates at, and the cognitive cost of running two hash families across three co-located systems outweighs it. All references below to BLAKE2b should be read as SHA-256. Documented here rather than silently edited so the reasoning stays recoverable.

## Context

The original handoff brief called for a **"tamper-proof audit chain"** but left the phrase undefined. Per the appended open question #4 in the vision doc: *"What tampering are we protecting against — local attacker with root? Remote? Operator error?"* The honest answer determines whether we need simple hash chains, cryptographic signatures, external anchoring, or all three.

We need to pin the **threat model first**, then the mechanism can follow. An under-scoped audit chain that claims more security than it provides is worse than one that states its limits plainly.

## Decision

### Threat model for v0.1: operator-honest-but-forgetful

The system is being operated by the person who stood it up. That person is not malicious toward themselves. The threat model v0.1 protects against:

- **Accidental edits** — the operator opens the audit log in a text editor to grep, autocorrect changes a character, they save. We detect and report.
- **Corruption from partial writes** — a crash mid-append leaves a truncated JSONL line. We detect and stop the walk at the bad line.
- **Out-of-order writes** — two processes write interleaved entries. We detect via `seq` gaps and hash-chain breaks.
- **Silent deletion** — an entry is removed from the middle of the file. We detect via broken hash chain.

The threat model v0.1 **does not** protect against:

- **Local root attacker** — anyone with write access to the file plus the builder's template library can reconstruct a valid chain with fabricated history. Needs signatures (deferred to ADR-00xx, likely Phase 4).
- **Malicious operator** — same class of attack; operator-honest is an assumption, not an enforcement. A later attestation layer (external witness, hardware-anchored signing) is where this would be addressed.
- **Remote network attacker** — N/A in v0.1; the system is local-first and the chain never leaves the machine. When it does (if exported for audit), the threat model changes and this ADR gets superseded.
- **Side-channel leakage** — entries are plaintext JSONL; sensitive content in `event_data` is the caller's responsibility. Not protected by this chain.

**Stating the model loud because understating it is a bug.** The "tamper-proof" phrasing in marketing material must be corrected wherever it appears — v0.1 provides **tamper-evident**, not **tamper-proof**. The UI, CLI, and docs should use the precise term.

### Mechanism

Append-only JSONL file at `audit/chain.jsonl` relative to the repo root. One event per line.

**Entry schema:**

```json
{
  "seq": 42,
  "timestamp": "2026-04-21T20:33:45Z",
  "prev_hash": "<64-hex>" | "GENESIS",
  "entry_hash": "<64-hex>",
  "agent_dna": "<short dna>" | null,
  "event_type": "<one of the enumerated types>",
  "event_data": { "...": "..." }
}
```

**Fields:**

- `seq` — monotonic integer starting at 0. The chain rejects appends where `seq != max(existing_seq) + 1`.
- `timestamp` — ISO 8601 UTC. Informational; *not* hashed into the chain (clock skew would break verification).
- `prev_hash` — BLAKE2b-256 hex of the previous entry's canonical form, or the literal string `"GENESIS"` for `seq == 0`.
- `entry_hash` — BLAKE2b-256 hex of *this* entry's canonical form, excluding the `entry_hash` field itself.
- `agent_dna` — short DNA of the agent involved, or `null` for system events.
- `event_type` — enumerated (see below).
- `event_data` — arbitrary JSON object specific to the event type.

**Canonical serialization** for hashing: `json.dumps(payload, sort_keys=True, separators=(",", ":"))` with the full payload excluding `entry_hash`, UTF-8. Same shape as DNA canonicalization so the two stay consistent.

**Why BLAKE2b over SHA-256:** BLAKE2b is faster, is keyed-capable (useful if we add HMAC later without changing the chain), and has no meaningful security gap. The existing DNA code uses SHA-256; the choice to diverge here is deliberate and noted. If consistency across the codebase matters more than the per-hash speedup, we can flip this to SHA-256 in review — see open question.

### Event types (v0.1 enumeration)

```
chain_created              # seq=0, genesis entry
agent_created              # a root agent minted via the factory
agent_spawned              # an agent spawned another; event_data has parent_dna and child_dna
constitution_regenerated   # a constitution file was rebuilt
manual_override            # operator overrode a policy (Phase 3+, schema reserved now)
drift_detected             # profile hash mismatch at runtime (Phase 3+)
finding_emitted            # agent produced a finding (Phase 3+)
policy_violation_detected  # enforcement tripped (Phase 3+)
```

Only the first four can actually be emitted in v0.1. The rest are reserved so the schema doesn't need to change when the runtime lands — a consumer reading today's chain can be written to handle all types already. **Unknown event types are a verification warning, not an error** — allows forward compatibility.

### Public API

```python
class AuditChain:
    def __init__(self, path: Path): ...
    def append(self, event_type: str, event_data: dict, agent_dna: str | None = None) -> ChainEntry: ...
    def read_all(self) -> list[ChainEntry]: ...
    def verify(self) -> VerificationResult: ...
    @property
    def head(self) -> ChainEntry | None: ...


@dataclass(frozen=True)
class ChainEntry:
    seq: int
    timestamp: str
    prev_hash: str
    entry_hash: str
    agent_dna: str | None
    event_type: str
    event_data: dict


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    entries_verified: int
    broken_at_seq: int | None
    reason: str | None        # e.g. "prev_hash mismatch", "entry_hash mismatch", "seq gap", "invalid JSON"
    unknown_event_types: tuple[str, ...]  # forward-compat warnings
```

`append()` is the only mutator. There is no `delete`, `edit`, `truncate`, or `replace`. The chain object does not expose raw file handles — callers that want to inspect the file go through `read_all()` or open it themselves for read-only use.

### Concurrency (v0.1)

Single-writer assumption. The chain does not attempt OS-level locking in v0.1. Calling `append()` from multiple processes concurrently is **undefined behavior** — documented as a known limitation, not a silent corruption waiting to happen. A process-level lock (e.g., `fcntl.flock` on the file) is a candidate addition, but buckets under "things that require a threat-model upgrade to make meaningful".

### Verification semantics

`verify()` walks from seq=0 forward, recomputing each `entry_hash` and `prev_hash`. Stops at first failure and reports:

- **`ok=True`** — all entries are consistent, hashes link back to genesis, no seq gaps.
- **`ok=False` with `reason`** — first break. Callers can decide whether to halt, alert, or quarantine.

Verification is **pure** — it does not modify the chain. A corrupted chain is *reported*, not *repaired*; any repair path is a separate deliberate action (e.g., `audit/rotate.py`, which does not exist in v0.1).

### Storage location and rotation

- Single global chain at `<repo_root>/audit/chain.jsonl`. Per-agent sharding is rejected for v0.1 — cross-agent event ordering (spawns, swarm interactions) is the whole point of an audit log.
- No rotation in v0.1. The file grows forever. Acceptable for a local-first personal tool; flag as future work once it ceases being acceptable.
- A tiny `audit/README.md` explains the schema, the threat model limits, and the "don't edit by hand" rule to the operator.

### Polymorphism pressure (carried from ADR-0004)

The chain is event-typed with freeform `event_data`, which keeps it open-ended. As the agent ecosystem gets weirder, new event types can land without schema changes — only the enumeration and the verification's unknown-type tolerance need updates. This is a deliberate choice to avoid locking in v0.1.

## Consequences

### Positive

- A single canonical record of every agent-affecting event, tamper-evident under the stated threat model.
- Hash chain makes "something changed" instantly detectable.
- Forward-compatible event schema — Phase 3 runtime can start emitting `finding_emitted` entries without any chain format change.
- Decouples enforcement (runtime) from evidence (chain) — the chain exists and works whether or not the runtime is live.
- The stated threat model limits become a reviewable decision, not an oversight. If someone later says "but this doesn't protect against a root attacker!" the answer is "correct, and we said so in ADR-0005; here's the later ADR that addresses that."

### Negative

- Tamper-**evident**, not tamper-proof. If marketing material has claimed the stronger property, it needs correction. This is a feature of being honest, not a weakness of the design.
- Unbounded file growth until a rotation ADR lands.
- Single-writer assumption means any multi-process agent runtime must serialize writes — a constraint the runtime ADR inherits.

### Neutral

- Timestamps are informational and not hashed. Clocks drift; the chain does not care. The cost is that two entries with the same seq cannot be ordered by timestamp alone — but seq is the canonical order, which is as it should be.
- BLAKE2b vs SHA-256 is a coin-flip-level choice. Either works. ADR picks BLAKE2b for reasons given; reviewers can flip.

## Alternatives considered

**No chain, just plain JSONL log.** Simpler. Rejected: loses the ability to detect out-of-order edits or silent deletions, which is half the point.

**Full Merkle tree rather than linear chain.** Gives us partial-audit efficiency (proof of inclusion without reading the whole log). Rejected for v0.1 as overkill — the log will be small for a long time, and the linear chain is simpler to reason about.

**Signatures per entry (Ed25519) now.** Addresses the malicious-operator threat model. Rejected for v0.1 because (a) we don't have a key-management story, and (b) signing with a key stored in the same filesystem the attacker controls is security theater. When signing lands, it lands with an external signing root — that's a separate project.

**External anchoring** (commit chain root periodically to a public ledger). Powerful but wildly outside v0.1 scope. Noted for a hypothetical Phase 6+ when the operator has third-party audit requirements.

**SQLite instead of JSONL.** Queryability, transactional appends, easier rotation. Rejected for v0.1: plaintext JSONL is human-greppable and survives disaster recovery trivially. A binary database would make the "don't edit by hand" guarantee stronger but would also make "I opened it in a text editor and it was fine" impossible. The threat model doesn't require the stronger guarantee yet.

**Per-agent chains.** Simpler to verify in isolation. Rejected because swarm and lineage events cross agents by nature — we'd have to replicate events across chains, which is worse than just one chain.

## Open questions

These are places we might be wrong and should watch for:

- **BLAKE2b vs SHA-256 consistency.** The DNA code uses SHA-256. Mixing is fine but adds cognitive load. Call it in review: keep BLAKE2b for chain (fast), or flip to SHA-256 for codebase uniformity?
- **Rotation signal.** At what chain size does growth become an operational concern? 10MB? 100MB? Needs a measured answer once a real agent population is generating events.
- **Unknown event type tolerance.** Forward-compat is a feature; it's also a vector for "chain is considered valid but contains events the consumer doesn't understand." A stricter mode (`verify_strict=True`) that rejects unknown types is probably worth adding once agent runtime types stabilize.
- **Lineage event verification.** An `agent_spawned` entry carries `parent_dna` and `child_dna`. Should `verify()` cross-check that the parent actually exists earlier in the chain? Doing so is O(n²) naive, O(n) with an index. Worth it if we want to detect "spawn event with no parent record" — probably yes, marked as a verify-mode option.
- **Per-entry `event_data` schema validation.** Currently `event_data` is arbitrary. We could register JSON schemas per event_type. Overkill for v0.1; probably warranted by the end of Phase 3 when event shapes stabilize.
- **Disaster recovery.** If the chain file is deleted or catastrophically corrupted, the operator loses history. Should the builder emit periodic compressed archives? Proposed for a rotation/archive ADR once we have a real need.
- **Concurrent writers.** Single-writer is a hard constraint. When the runtime lands (Phase 3), we likely have multiple agents emitting events. They'll need to route through a single append-serialization point (a small daemon or a file lock). Whichever way it goes, it's the runtime's ADR, not this one.
- **Polymorphism.** Same pressure as constitution: as agent kinds diversify, event shapes will diversify. The `event_data` freeform is the escape hatch; we should *not* prematurely lock it into structured sub-schemas.

## Scope explicitly out

- Cryptographic signatures on entries.
- External anchoring / third-party audit.
- Rotation, sharding, archiving.
- Multi-process concurrent writer coordination.
- Encryption of entries at rest.
- Cross-host replication.
- Anti-forensics resistance (chain stored outside the operator's filesystem).
