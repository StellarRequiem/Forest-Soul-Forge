# ADR-0027 — Memory privacy contract (information flow control)

- **Status:** Accepted (promoted 2026-04-30 — Phase A audit §A-2; see docs/audits/2026-04-30-comprehensive-repo-audit.md). Memory privacy contract — T14–T17 shipped with ADR-0033 A2.
- **Date:** 2026-04-27 (promoted from Placeholder)
- **Related:** ADR-0022 (memory subsystem — the substrate this contract governs), ADR-0021 (role genres — Companion's privacy posture is the strict ceiling), ADR-0024 (project horizons — social anchoring in H3 needs this contract first), ADR-0025 (threat model v2 — what an attacker can see), ADR-0029 (regulatory map — GDPR / COPPA constraints feed in here).

## Context

ADR-0022 is filed as Proposed: agents have memory layers (episodic, semantic, procedural). ADR-0021 gives each genre a privacy posture (Companion = strict, Observer = permissive). What's still missing is a **formal information-flow contract** that answers:

- When can agent A's memory be referenced by agent B?
- Under what consent — operator-given, agent-given, both?
- With what audit trail?
- What does "delete" mean, and what propagates?

Without this contract, ADR-0022 v0.1 ships a per-agent-only memory store and that's the right answer for now. But the moment two scenarios materialize — multi-agent coordination (Horizon 2) or social anchoring (Horizon 3, "your Guardian tells your friend's agent about a life event you opted into sharing") — the contract becomes load-bearing and we don't want to invent it under deadline pressure.

This ADR exists to **make the decisions now**, before the implementations that depend on them, while none of the H2/H3 work has been started. The implementations (ADR-0022 v0.2+) reference the rules established here.

## Decision

The contract has six pieces. Each is a concrete rule with a default and an exception path.

### 1. Read scopes — who can read what

A memory entry has exactly one **scope** at write time. The scope determines who can read it:

| Scope          | Who can read                                          | Default for |
|----------------|-------------------------------------------------------|-------------|
| `private`      | The owning agent only.                                | Companion-genre default. Default for any memory written without an explicit scope. |
| `lineage`      | The owning agent + its parent + descendants.          | Investigator / Researcher genres' default for working notes. |
| `realm`        | Any agent in the same realm (Horizon 3; meaningless before federation lands). | Observer / Communicator default once realms exist. |
| `consented`    | An explicit allowlist of agent IDs the owner approved. | The default for any cross-agent disclosure. |

Genre privacy postures (ADR-0021) become **hard ceilings**. A Companion-genre agent cannot tag any memory as anything wider than `private`, even if the operator clicks through a UI saying so. The runtime refuses the write at the memory API.

Cross-scope reads through a tool (e.g. `delegate_to_agent.v1` carrying a memory excerpt as part of its prompt) are evaluated against the **read scope of every entry the parent passes**. Insufficient scope = refusal, not silent truncation.

### 2. Consent model — how `consented` scope gets populated

Three levels:

- **Per-event consent.** The owner clicks "share with friend X" on a specific memory entry. Encoded as `consented_to: [agent_id, ...]` on the entry. Default for v0.1.
- **Per-relationship consent.** The owner declares "agent X has a Companion-class friendship; share life events with them by default" once. Encoded as a per-pair consent object stored in the registry. Each disclosure under a relationship still emits an audit event.
- **Tiered consent.** Different scopes for different friends — close friends see emotional state, acquaintances see calendar availability, strangers see nothing. Modeled as named tiers with declared scopes; relationships pin to a tier.

For ADR-0022 v0.1, only **per-event** consent is supported. Per-relationship + tiered land in a later tranche driven by Horizon 3 social-anchoring needs.

Consent is **withdrawable**. Withdrawal:

- Removes the agent from `consented_to:` on every entry (`memory_revoke_consent` event).
- Does NOT retroactively remove entries the consented agent already saw — that's the deletion contract's job.
- Audit chain records both the withdrawal and a count of which entries were affected, without leaking entry content.

### 3. Deletion semantics — what "delete" means

Two delete operations:

- **Soft delete (tombstone).** Default. Entry stays in the SQLite table with `deleted_at` set; reads filter it out by default. Audit chain entry preserved (deletion does not break the hash chain). The tombstone retains scope + consent metadata for diff purposes; content is wiped.
- **Hard delete (purge).** Explicit operator action. Entry row removed from the table; the audit chain entry that wrote it gets a `memory_purged` companion entry (chain stays intact because we add, never remove). Disclosed copies on consented agents stay where they are — the owner's purge doesn't reach into other agents' stores. We document this as a bedrock honesty: "delete" means "from your store"; getting it back from someone you shared with is a social problem, not a technical one.

Both operations write `memory_deleted` (soft) / `memory_purged` (hard) events including the entry id + the count of consented copies that were not reachable.

### 4. Data minimization at the boundary

When agent A's memory crosses to agent B (via an explicit disclosure tool call), only the **minimum** the disclosure declares passes:

- A summary string (operator/agent-supplied at disclosure time), not the full entry.
- The entry's id (for back-reference + future revocation propagation).
- The scope under which it was disclosed (`consented` always; per-event with the recipient agent_id).

Full content stays in the originating agent's store. The receiving agent gets a "I was told about X" reference, not a copy of the original entry.

Tools that wrap memory disclosure (e.g. a future `share_memory.v1`) must declare this contract; they can't bypass it.

### 5. Genre privacy floors as hard ceilings

Repeating from §1 because this is the load-bearing rule:

- **Companion** → all memory writes default to `private`; cannot be widened.
- **Observer / Investigator** → default `lineage`; can be narrowed to `private`, never widened past `lineage` without operator override.
- **Researcher** → default `consented`; can be narrowed.
- **Communicator** → default `realm` (Horizon 3) or `consented` (pre-federation).
- **Actuator / Guardian** → operator-defined per-deployment.

Floors are enforced at the memory API write path. Exceptions require an audit-chain `memory_scope_override` event with operator id + reason. There is no override that lets a Companion write to `realm` — that's the genre's identity, not a tunable.

### 6. Audit obligations

Every memory operation that crosses an information-flow boundary emits an audit event. The chain is the source of truth for "who saw what":

| Event                       | Triggered by                                                |
|-----------------------------|-------------------------------------------------------------|
| `memory_written`            | Any new entry. Records scope, content_digest (not content). |
| `memory_read`               | Cross-agent read. Skipped for an agent reading its own private memory — too noisy and the memory is already in scope. |
| `memory_disclosed`          | Disclosure to another agent's store. Records summary + scope + recipient. |
| `memory_consent_granted`    | Per-event or per-relationship consent. |
| `memory_consent_revoked`    | Withdrawal. Includes affected_count. |
| `memory_deleted`            | Soft delete (tombstone). |
| `memory_purged`             | Hard delete. |
| `memory_scope_override`     | Operator widened a memory beyond the genre floor. |

Cross-agent reads + disclosures + scope overrides are **not auto-bulk-able** — each one is its own audit entry. Bulk operations decompose to per-entry events. This is intentional: an attacker who got operator approval to "consolidate memories" should not be able to disclose a thousand entries inside a single audit line.

## Trade-offs and rejected alternatives

**Genre as a hard ceiling vs. a default.** Hard ceiling. A "default" genre privacy posture that the operator can override in UI is a foot-gun: the next operator who clicks past a warning at 3am ends up with a Companion that disclosed therapy notes. Hard ceilings + an explicit `scope_override` event with reason + operator id is the right balance.

**Auto-revoke disclosed copies on consent withdrawal.** Rejected. Cryptographically impossible — once data has left, it's gone. Pretending we can call it back is a worse user experience than telling the user the truth: "this revokes your future shares; it can't reach what already happened. Talk to your friend." Document it in the consent UI.

**Encrypted memory at rest.** Out of scope for v0.1. ADR-0025 (threat model v2) handles encrypted-at-rest in the federation context. Today's threat model is "your local disk is yours"; encryption adds key-management complexity without addressing a current threat.

**Memory entries as content-addressed.** Tempting (every memory is a hash), but mutable summaries, soft deletes, and consent scope changes break content-addressing's "same hash → same content" property. Memory entries are **identified by UUID**, not hash. The entry's content_digest is recorded for tamper detection but the entry itself is mutable.

**One contract for all memory layers (episodic, semantic, procedural)?** Yes. The privacy contract operates at the entry level; the layer (episodic vs semantic) is metadata on the entry, not a separate contract. Simpler.

**Per-realm memory.** Horizon 3 federation introduces realm-scoped memory. The `realm` scope already exists in §1 for forward compat; the federation protocol design (ADR-0025) decides how realm membership is verified. Until then `realm` is unreachable.

**ID forging — what stops agent A from claiming to be agent B?** Out of this ADR's scope. Identity forging is ADR-0025's problem. This contract assumes identities are correctly attributed; the threat model handles the assumption.

## Consequences

**Positive.**
- Memory subsystem v0.1 (ADR-0022) can ship with a clear, narrow scope — `private` only — and not back-paint itself into a corner.
- Genre identity becomes more meaningful: Companion's privacy posture is enforced at the data layer, not just trait-derived prompt behavior.
- Audit chain records every information-flow boundary crossing → operators can answer "who saw X?" by walking the chain.
- Future regulatory work (ADR-0029) has a clear hook: GDPR Article 17 (right to erasure) maps to soft delete + tombstone retention; data portability (ADR-0028) reads `private` + `consented` entries the same way.

**Negative.**
- Genre ceilings are operator-non-overridable, which will frustrate operators who want fine-grained control. The `scope_override` audit event is the escape valve; it's deliberately heavy.
- "Delete from disclosed agents" is impossible by design and we have to communicate that clearly. Some users will misunderstand and feel surprised.
- Per-event consent doesn't scale to large social graphs — every disclosure is a click. Per-relationship + tiered consent (later tranches) reduce friction but are themselves complex. Phased delivery accepts the friction of v0.1.

**Neutral.**
- Memory entry shape gains five required fields beyond content (scope, consented_to, deleted_at, content_digest, source_agent_id). Adds row width but not query complexity.

## Cross-references

- ADR-0022 — memory subsystem (substrate); v0.1 ships private-only per this ADR.
- ADR-0021 — genres (define the ceilings).
- ADR-0024 — horizons (social anchoring in H3 unblocks the per-relationship + tiered tranches).
- ADR-0025 — threat model v2 (federated identity verification + adversarial operators).
- ADR-0028 — data portability (GDPR Article 20 read path).
- ADR-0029 — regulatory map (which jurisdictions care about which §).
