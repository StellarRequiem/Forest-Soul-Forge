# ADR-0016 — Session Modes and Self-Spawning Cipher

- **Status:** Proposed
- **Date:** 2026-04-24
- **Supersedes:** —
- **Related:** ADR-0005 (audit chain), ADR-0007 (FastAPI daemon), ADR-0008 (local-first model provider), ADR-0009 (provenance bundle, pending), ADR-0011 (continuity protocol, pending), ADR-0014 (accessibility-adaptive interaction layer, pending)

## Context

"Session" has been a fuzzy word in the codebase up to now — sometimes it means a single HTTP exchange, sometimes a conversation turn, sometimes the wall-clock lifetime of a chat window. That ambiguity is fine when everything the agent does is stateless, but two things on the near roadmap break the stateless assumption:

1. **Agent memory.** The accessibility / therapeutic tier (ADR-0014) requires the agent to remember the user across turns and days — food sensitivities, communication preferences, declared accommodations, the shape of the last hard conversation. Memory is persistent by default for that tier.

2. **Secure / therapeutic use cases.** When the user discloses health information, trauma, or legal matters, they need guarantees that *this* session's contents can't be mined for training, can't be replayed by a third party who later compromises the daemon, and can't be impersonated by a future attacker who captures disk state.

Those two pressures pull in opposite directions. Persistent memory is the feature; but persistent memory is also the surface a compromised daemon can harvest. The answer can't be "always persistent" or "never persistent" — it has to be a per-session contract the user can see and choose.

This ADR names two session modes and the crypto primitive (deferred) that makes the secure mode real. Both need real-use experience before the details lock; status is Proposed until a concrete caller (the Phase 4 runtime, the Phase 5 medical tier) exercises them.

## Decision

### Sessions are first-class

A **session** is a scoped interaction between one user and one agent instance with:

- a `session_id` (UUID v4, minted at open),
- an explicit `mode` (see below),
- an opening audit-chain entry (`session_opened`) that records mode + policy,
- a closing audit-chain entry (`session_closed`) that records duration + outcome.

`session_id` is *not* a secret — it's an audit key. Every model call, every tool use, every memory read/write inside the session carries the `session_id` so the audit chain can be sliced by session after the fact.

### Two session modes in v1

**Ephemeral mode.** Memory accumulated during the session lives in an in-process buffer only. On `session_closed` the buffer is flushed. Nothing is written to the agent's persistent memory store. The audit chain still records that the session happened and what *kind* of interaction it was (tool uses, model calls) but not the content. This is the default for the secure/therapeutic tier and the right choice for a one-off task.

**Persistent-fork mode.** Opening a persistent-fork session branches the agent's memory store into a dated fork named after the `session_id`. All reads go through the fork (so the session sees everything the agent already knew); all writes land only in the fork. On `session_closed` the fork is diffed against the base. The user is shown the diff (adds / changes / deletes) and either:

- accepts the merge → fork is applied to the base, session contributions become permanent;
- rejects the merge → fork is discarded, base is unchanged;
- saves the fork → fork persists as a named branch the user can reopen later or merge manually.

Rejecting is the point: the user gets to curate what the agent learns about them instead of living with whatever inferences the session produced. This is also what makes the therapeutic tier survivable — a bad session doesn't rewrite the agent.

Both modes emit the same audit-chain events; the difference is which backing store the memory-layer writes hit.

### The self-spawning cipher (deferred primitive)

For the secure/therapeutic tier, even an ephemeral session is insufficient if a later attacker compromises the daemon and replays *this session's* messages against the model, or fabricates messages claiming to be from this session. We want a key the attacker cannot forge because they do not possess one of the two halves that produced it, and which neither party alone could have generated.

The **self-spawning cipher** is a joint-spawn protocol run at session open:

1. User half. A per-user device secret (stored in the user's OS keychain / secure enclave, generated at first install) contributes a fresh scalar derived from (device_secret, session_nonce).
2. Agent half. The agent contributes a fresh scalar derived from (agent_instance_secret, session_nonce).
3. Joint spawn. The two halves combine via a two-party computation (target: Diffie-Hellman-style contribution over Curve25519 — details pending crypto review) into a session keypair. Neither party sees the other's half in the clear; neither party's half alone determines the output.
4. Binding. The session public key is committed to the audit chain in the `session_opened` event along with both contribution commitments. Future verifiers (user, auditor, replay defender) can confirm that both halves participated without replaying either.
5. Forward secrecy. Private-key material is kept in-memory only and zeroized at `session_closed`. After close, no party — including a later-compromised daemon or a later-compromised user device — can reconstruct the key.

The result: message integrity inside the session is anchored to a key that requires both device and agent to produce, and the proof that both participated is in the tamper-evident audit chain. An attacker who compromises the daemon after the fact cannot forge "a turn that happened earlier"; an attacker who compromises the user device cannot forge "what the agent said" without also retroactively forging the audit chain.

The cipher is **deferred** past v1 of session modes. v1 ships with mode selection + fork/merge semantics + session events; the cipher lands when the Phase 5 medical/therapeutic tier has a concrete caller that needs it. Documenting the shape now prevents the session-mode schema from closing off the eventual key-bound fields.

### UI contract (summary)

- Session-open dialog shows the mode explicitly. No "invisible" persistent session — the user always knows whether what they say will be remembered.
- Mode is selectable per session; a default per-agent mode lives in the agent's constitution.
- The therapeutic/secure tier forces ephemeral or persistent-fork-only (not a direct persistent write) at the constitution layer.
- Close-of-session diff for persistent-fork mode is modal: the session is not considered closed until the user has chosen accept / reject / save-fork.

## Consequences

**Upside:**

- The privacy property is visible at session open, not buried three layers deep in a settings panel. "Your agent will remember this" vs "Your agent will forget this when we hang up" is a thing the user can see and choose every time.
- Persistent-fork gives the user a review gate between what the agent experiences and what the agent retains. Bad or mistaken inferences are discardable without losing the baseline.
- The cipher's joint-spawn property means that even a total post-hoc daemon compromise cannot forge session history. That is the property the therapeutic tier needs.
- Session as a first-class audit dimension means every investigation ("what did the agent do between 10:00 and 11:30 on 2026-06-12?") can be answered by slicing the chain by `session_id`.

**Downside:**

- Fork/merge semantics are genuinely novel for an agent memory store. First implementation will have edge cases (concurrent sessions, crashed-without-close sessions, forks that diverge for weeks before merge). Continuity protocol (ADR-0011) has to cover the crashed-session case.
- The cipher is real crypto and needs a real crypto review before any therapeutic-tier caller depends on it. Proposed status reflects that — we aren't shipping unreviewed crypto.
- Persistent-fork mode's diff UX is a product design problem, not just a backend one. Showing the user "here is what your agent learned about you today" in a way that is reviewable but not overwhelming is going to take iteration.

**Neutral:**

- Ephemeral mode is cheap and well-understood; shipping it first lets the Phase 4 runtime land without waiting on persistent-fork's UX.
- `session_opened` / `session_closed` event types need to be added to `audit_chain.KNOWN_EVENT_TYPES` when v1 of this ADR goes from Proposed to Accepted. ADR-0005's forward-compat warning-not-failure behavior means pre-upgrade chains can carry the events without breaking verification.

## Open questions

- **Default mode per agent role.** Should investigative agents (log_analyst, network_watcher) default to ephemeral because their sessions are one-off; should companion-tier agents default to persistent-fork? Answer probably lives in role defaults in `constitution_templates.yaml` — TBD.
- **Concurrent sessions.** Can one agent instance have two persistent-fork sessions open at once? Simplest answer: no (one fork at a time, serialized). More capable answer: yes, with a well-defined merge-of-merges. Pick after we see a real use case.
- **Fork storage.** SQLite row-level copy? Content-addressed memory store with immutable snapshots? Postponed until the memory store itself (Phase 4) exists.
- **Cipher primitive choice.** Curve25519 + HKDF is the default I'd propose, but the two-party spawn's exact protocol (commitment scheme, non-malleability guarantees) wants a real crypto-engineer pass before lock-in. Noise Protocol framework is worth evaluating as prior art.
- **Key custody at the user end.** Per-device secret lives in OS keychain on consumer hardware, but the VIP / air-gapped tier might want a hardware token. The shape above doesn't preclude that; concrete handshake needs to.
- **Revocation.** What does "revoke this past session's trust" mean when the key material is already zeroized? Possibly: publish a `session_revoked` audit entry that marks the session's chain range as untrusted for any future decision. Needs design.

## Alternatives considered

- **One session = one persistent memory write, no fork.** Simplest. Rejected: no review gate, a bad session permanently shifts the agent's understanding of the user. That's the exact failure mode the therapeutic tier can't survive.
- **Always ephemeral, no persistent option.** Also simple. Rejected because the accessibility pillar *needs* persistent memory — forgetting a user's declared accommodation every session is a worse failure than remembering it badly.
- **Mode set per-agent instead of per-session.** Rejected: a user may want the same agent to remember the weekly product planning conversation but forget the one-off question about their medical result. Per-session is the right granularity.
- **Out-of-band session keys (pre-shared keys rotated on a schedule).** Strictly easier than a joint-spawn cipher. Rejected for the therapeutic tier because it does not defeat the "post-hoc daemon compromise replaying a past session" attack — the pre-shared key existed on the daemon and is now the attacker's. Joint-spawn specifically closes that hole.
