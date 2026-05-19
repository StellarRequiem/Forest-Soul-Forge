# ADR-0083 — Lifecycle-aware idempotency replay

**Status:** Accepted (2026-05-19, B426)
**Date:** 2026-05-19
**Tracks:** Kernel / Idempotency contract / Agent lifecycle
**Supersedes:** none (extends ADR-0040, Burst 77 idempotency cache)
**Builds on:** ADR-0082 (kernel freeze posture — invoked under the
"architectural bug discovery" unfreeze trigger)
**Unblocks:** Triune-Main 3-of-3 restoration. Future rebirth flows
on same-trait-profile agents. Any operator-driven archive +
re-birth cycle.

## Context

B416 added `allowed_paths` defaults to the `code_reviewer` template.
B420 shipped `dev-tools/rebirth-reviewer-main.command` to archive
the existing Reviewer-Main and re-birth it under the updated
template so the new defaults would land in the rebirth constitution.

Two live attempts (2026-05-19 14:48 + 2026-05-19 18:48) both
failed to actually rebirth Reviewer-Main. Investigation revealed
two bugs:

1. **B425 (fixed):** rebirth helper POSTed to non-existent
   `/agents/{id}/archive` (404). Silent no-op.
2. **THIS ADR addresses:** even after B425's path fix, the second
   live attempt successfully archived Reviewer-Main (audit seq
   19177) but the subsequent birth POST silently no-op'd. The
   script printed "born: code_reviewer_8808e39f43ac" but no
   `agent_created` event fired and no new constitution file was
   written.

The substrate's sibling-index mechanism (`next_sibling_index()`
in `registry/tables/agents.py:150`) already produces unique
`instance_ids` for fresh births after archive — it counts
`MAX(sibling_index) WHERE dna=?` across **all** agents including
archived. A fresh birth after archive would correctly produce
`code_reviewer_8808e39f43ac_2`. The substrate primitive is sound.

The wedge is **idempotency cache replay** at
`writes/_shared.py:_maybe_replay_cached`. The cache stores
`(idempotency_key, endpoint, request_hash) → (status, response_body)`.
On a subsequent request with the same triple, it returns the
cached response verbatim, bypassing the write path entirely.

That contract is correct for retries within a single agent
lifecycle (network blip, operator double-click). It's **wrong** for
rebirth after archive: the cached response captures state-at-time-
of-original-write (status=active, sibling_index=1), but the world
has changed since (the referenced instance is now archived). The
replay returns a misleading 201 with active-state data describing
an agent that no longer exists in active form. The operator sees
"born" output but no audit event fires.

### Evidence trail

Cached response in `idempotency_keys` table after the original
birth at 07:31:54Z:

```json
{
  "instance_id": "code_reviewer_8808e39f43ac",
  "status": "active",
  "sibling_index": 1,
  "created_at": "2026-05-19 07:31:54Z",
  ...
}
```

Registry state after the archive at 18:48:55Z:

```
agents.status = 'archived' for instance_id = code_reviewer_8808e39f43ac
```

The rebirth helper's birth-triune-main subprocess POSTed to
`/birth` with idempotency key `birth-reviewer-main` (deterministic
from agent_name). The triple matched the cached entry → replay
fired → no fresh birth.

Net operational impact: Reviewer-Main archived without
replacement. Triune-Main degraded to 2-of-3. Daily 7am
wiring_audit_triage scheduled task runs incomplete.

## Decision

**The idempotency cache replay path is lifecycle-aware:** when the
cached response refers to a registry entity whose lifecycle state
implied by the cached response no longer matches reality, the
cache entry is treated as a miss and the request processes fresh.

The check is opt-in per call site via a new keyword-only parameter
on `_maybe_replay_cached`:

```python
def _maybe_replay_cached(
    registry: Registry,
    key: str | None,
    endpoint: str,
    request_hash: str,
    *,
    is_still_valid: Callable[[bytes], bool] | None = None,
) -> Response | None:
    ...
    if is_still_valid is not None and not is_still_valid(cached_json):
        return None
    ...
```

The /birth call site (`writes/birth.py`) passes a validator that:

1. Parses the cached response JSON
2. Extracts the cached `instance_id`
3. Looks up the current registry row for that instance_id
4. Returns `True` only if the row's `status` is `active`

If the validator returns `False`, replay is skipped and the
request proceeds through the normal birth path. The next
`next_sibling_index(dna_s)` call sees the archived predecessor +
returns `sibling_index = 2`. A fresh `instance_id` is minted with
the new template's constitution body. New `agent_created` event
fires. New constitution file written. The operator sees the
expected outcome.

The /archive call site does NOT receive this validator. Archive
idempotency replay continues to work as before — repeated archive
requests for an already-archived agent get the same response,
which is correct behavior (archiving an archive is a no-op
per `writes/archive.py:80`).

### Trade-offs considered

**Alternative 1: Substrate-side cache invalidation on archive.**
When `agent_archived` event fires, walk the `idempotency_keys`
table and mark any `/birth` entries referencing that instance_id
as superseded. Rejected because: (a) schema change for a new
`superseded_at` column, (b) walking the table on every archive
adds write-path cost, (c) the lifecycle check at replay time is
both cheaper and more general (handles edge cases like manual
DB tampering or registry rebuild from chain).

**Alternative 2: Script-level fix (unique idempotency key per
rebirth).** The rebirth helper could append a UUID to its
idempotency key. Rejected because: (a) every future rebirth
helper has to know the gotcha, (b) it doesn't fix the substrate
class of bug for any other future caller, (c) loses idempotency
within a single rebirth attempt (network retries would produce
duplicate writes).

**Alternative 3: Per-agent cache invalidation in the archive
endpoint itself.** When archiving, delete any `idempotency_keys`
rows referencing the archived instance_id. Rejected because: it
mixes write paths (archive shouldn't have side effects on
idempotency cache state) and creates a coupling that the
boundary docs (ADR-0040) discourage.

The chosen approach (lifecycle-aware replay) is the smallest
change that closes the class of bug. The check fires only when a
caller opts in (so /archive replay isn't slowed down). The check
is local to the call site (so the validation logic doesn't leak
into _shared.py's general-purpose surface).

## Implementation surface

Three files touched. Total LoC delta: ~30 lines.

1. **`src/forest_soul_forge/daemon/routers/writes/_shared.py`**
   - Add `is_still_valid: Callable[[bytes], bool] | None = None`
     keyword-only parameter to `_maybe_replay_cached`
   - When parameter provided + returns False, return None (miss)
   - ~5 LoC

2. **`src/forest_soul_forge/daemon/routers/writes/birth.py`**
   - Define `_birth_cache_still_valid(cached_json: bytes) -> bool`
     closure inside the birth handler (it captures `registry`)
   - Parses cached_json, looks up agent, returns
     `row.status == "active"`
   - Pass `is_still_valid=_birth_cache_still_valid` to the
     `_maybe_replay_cached` call at line 549
   - ~15 LoC including audit-grade comment

3. **`tests/unit/test_birth_idempotency.py`** (NEW file or
   extension to existing)
   - Test: birth with idempotency key → success
   - Test: re-birth with same key (no archive in between) →
     cached replay (existing behavior preserved)
   - Test: birth + archive + re-birth with same key →
     fresh birth, new sibling_index, new instance_id
     (new behavior)
   - ~50 LoC

## What's NOT changing

- The seven ABI surfaces from KERNEL.md are unchanged. POST /birth
  with the same request and a fresh idempotency cache state
  behaves identically.
- The seven frozen abstractions from ADR-0082 are unchanged.
  `instance_id` derivation, DNA derivation, constitution hash
  derivation, additive-only schema migrations all preserved.
- The `idempotency_keys` table schema is unchanged.
- /archive replay behavior is unchanged.
- /regenerate-voice replay behavior is unchanged.
- Wire format of the cached response is unchanged.
- The "same key + same body = same response" contract is
  PRESERVED for within-lifecycle retries. Only cross-lifecycle
  replays (where the referenced entity has been archived since)
  trigger fresh processing.

## ADR-0082 compliance check

Per ADR-0082, kernel additions require justification under one of
three triggers:

- ✓ **Architectural bug discovery.** B416/B420/B425 chain
  surfaced this; investigation revealed the lifecycle gap in the
  idempotency contract. The cache replay was correct for retries
  but undefined for lifecycle transitions.

The change is scoped to a single internal helper (`_maybe_replay_cached`)
and a single call site (`/birth`). It does not expand the kernel
surface area, add a new top-level subsystem, or modify any of the
seven frozen abstractions. Under ADR-0082's classification
(KERNEL.md "What the kernel does NOT commit to"), this is an
internal refactor with no ABI implication.

## Consequences

**Positive:**

- Rebirth flows on same-trait-profile agents work correctly.
  Operators can archive + re-birth to pick up template changes
  without script gymnastics or idempotency-key hacks.
- The class of bug is closed at the substrate level. Future
  rebirth scripts inherit the fix.
- Audit trail integrity preserved: every successful birth lands
  exactly one `agent_created` event in the chain.
- Triune-Main 3-of-3 restoration becomes a single command run.

**Negative:**

- Idempotency replay has one more hop (the validator callback)
  on the hot path. Cost: one SQLite SELECT per cache hit, only
  when caller opts in. Negligible.
- The contract becomes slightly more nuanced: "idempotent within
  a single lifecycle window" instead of "idempotent unconditionally."
  This is documented in the ADR and in the code comment at the
  call site.

**Mitigations:**

- Comment at the _shared.py signature explains the validator
  contract clearly.
- The /archive call site explicitly does NOT pass a validator,
  documenting that archive idempotency is not lifecycle-aware
  (it doesn't need to be; archiving an archive is a no-op by
  design).

## Open questions

- **Q1: Should /regenerate-voice gain a similar lifecycle check?**
  No — voice regeneration is content-stable. If the same key +
  same body yields the same voice text, replaying the cached
  response is correct regardless of agent lifecycle state.
- **Q2: Should the validator be promoted to a default behavior
  rather than opt-in?** No — keeping it opt-in preserves the
  general-purpose nature of _shared.py and lets per-endpoint
  semantics drive when the check makes sense.

## References

- ADR-0082 — Kernel Freeze Posture (parent ADR; this addition
  invokes the architectural-bug-discovery trigger)
- ADR-0040 — Trust-Surface Decomposition Rule (the file-grained
  governance pattern that makes _shared.py a separate trust
  surface from per-endpoint sub-routers)
- ADR-0007 — Constitution as immutable hash (preserved; new
  rebirth flow produces NEW constitution hash for the NEW
  instance_id; the OLD agent's constitution_hash remains its
  identity-defining record)
- Burst 77 (2026-05-02) — original `_maybe_replay_cached`
  implementation in writes/_shared.py
- `docs/audits/2026-05-17-quarantine-rebirth.md` — B376 Kraine/
  Victor/chaz precedent for archive + re-birth pattern (though
  those agents got new DNAs due to constitution edits, not new
  sibling_indices)
- B416 — code_reviewer allowed_paths template defaults
- B420 — rebirth-reviewer-main.command (original, with bug 1)
- B425 — rebirth-reviewer-main.command archive endpoint hotfix
  (bug 1 fixed; this ADR closes bug 2)
- Memory [[project_2026_05_19_b422_b425_discipline_arc]] — the
  arc that surfaced the architectural bug
