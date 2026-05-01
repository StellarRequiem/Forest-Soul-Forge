# ADR-0027 amendment — epistemic metadata on memory entries

- **Status:** Accepted (promoted 2026-05-01 — implementation complete across all four tranches T1–T4). The base ADR-0027 stays Accepted; this is an additive amendment, not a supersession. Implementation commits: `fcd8d2c` (T1+T2 schema v10→v11 + MemoryEntry write/read paths), `24ec62b` (T3 memory_recall.v1 epistemic enrichments), `fdef95b` (T4 memory_challenge.v1 tool). T7 (operator-driven memory_reclassify.v1) deferred to v0.3 — quality-of-life follow-up, not blocking.
- **Date:** 2026-05-01
- **Amends:** ADR-0027 (memory privacy contract). The original §1–§6 stay in force. This amendment adds §7 (epistemic metadata) and updates §6 (audit obligations) with three new event types.
- **Related:** ADR-0022 (memory subsystem — substrate), ADR-0021 (role genres), ADR-003X K1 (`memory_verify.v1` — verification-as-consent-grant; this amendment makes verification multi-state without breaking K1), ADR-0038 (companion harm model — H-6 "memory overreach / inferred-preference cementing" is closed by this amendment).
- **External catalyst:** [SarahR1 (Irisviel)](https://github.com/SarahR1) — comparative review of FSF vs. her Nexus/Irkalla project (2026-04-30). Quote: *"FSF's audit chain proves 'this happened' better than it proves 'this belief is true'. For companion/personhood work, that distinction matters enormously. A companion with durable memory needs not only memory privacy, but also memory humility."* The MemoryNode schema reference + Iron Gate framing she cites are her project's prior art; the FSF-specific column shape, schema-bump path, and tool-surface mapping are this amendment's work.

## Context

ADR-0027 v1 governs **information flow** on memory entries — who can
read, who can write, what consent looks like, what disclosure does,
what delete means. K1 (`memory_verify.v1`, ADR-003X tranche K1) added
**one bit** of epistemic state: a memory entry is either verified
(by a sentinel consent grant from an external verifier) or not.

What's missing — and what the catalyst review correctly flags — is
the rest of the epistemic dimension. Today's `MemoryEntry` shape:

```
entry_id, instance_id, agent_dna, layer, scope, content,
content_digest, tags, consented_to, created_at, deleted_at,
disclosed_from_entry, disclosed_summary, disclosed_at
```

Every field above answers a privacy or provenance question. None
answers an epistemic question. Specifically:

1. **What kind of claim is this?** A memory entry might be:
   - An **observation** ("operator typed 'I'm tired' at 14:23")
   - A **user-stated fact** ("operator said 'I work nights'")
   - An **agent inference** ("operator seems to prefer mornings"
     — derived from observation, NOT stated)
   - A **preference** ("operator said they like X")
   - A **promise** ("operator said they'd do X by Y")
   - An **external fact** ("public weather API said it rained
     yesterday")

   These six classes have wildly different reliability and
   wildly different mutation rules. Conflating them in a single
   `content` column means the agent treats inferences as facts and
   facts as inferences, indistinguishably.

2. **How confident is the entry?** Even within one claim class,
   confidence varies. An observation logged a millisecond after the
   event is high-confidence; an inference from sparse signal is low.

3. **Has this been challenged or contradicted?** A preference stated
   in January and contradicted in April should not still surface as
   "operator's stated preference" without flagging the conflict.

4. **When was it last revisited?** Stale memory is a real harm —
   a six-month-old "preference" that was actually a one-time mood is
   indistinguishable from a stable preference under today's schema.

ADR-0038 H-6 ("memory overreach / inferred-preference cementing") is
the harm that arises directly from this gap. This amendment closes it
at the schema layer.

## Decision

### §7.1 — `claim_type` field on memory entries

New required column on `memory_entries`:

```sql
ALTER TABLE memory_entries ADD COLUMN claim_type TEXT NOT NULL
    DEFAULT 'observation' CHECK (claim_type IN (
        'observation',     -- direct event log; high reliability
        'user_statement',  -- operator-stated; reliability bounded by operator's accuracy
        'agent_inference', -- agent-derived; explicitly NOT operator's stated word
        'preference',      -- operator's stated preference (subtype of user_statement, but mutation rules differ)
        'promise',         -- operator's stated commitment with implicit deadline
        'external_fact'    -- claim sourced outside the agent-operator dyad
    ));
```

Rules:

- **Default is `observation`** for back-compat. Existing memory
  entries pre-migration land as `observation`. Operator can run a
  one-time re-classification pass post-migration; not required for
  v0.2.
- **`agent_inference` is the high-friction class.** Tools that
  surface memory to the operator MUST visually distinguish
  inferences from observations + user_statements. UI: italicized,
  prefixed with "I think:". Voice: introduced with "From what I've
  noticed, " not "You said".
- **Mutation rules differ.** `observation` and `external_fact` are
  immutable; correction creates a new entry that supersedes via
  `contradicts:`. `user_statement` and `preference` can be updated
  (new entry supersedes old) — the chain captures the supersession.
- **`promise` carries an implicit `deadline_at` derivative.** Surface-
  level: not a new column on memory_entries (deadline parsing is a
  separate skill); but `promise`-typed entries get queryable through
  `memory_recall.v1` with a "due soon" filter.

### §7.2 — `confidence` field

```sql
ALTER TABLE memory_entries ADD COLUMN confidence TEXT NOT NULL
    DEFAULT 'medium' CHECK (confidence IN ('low', 'medium', 'high'));
```

Three-state, not float. Float confidence is false precision — agents
rationalizing "0.73" mean nothing the operator can interpret.
Three-state is auditable + UI-presentable.

Rules:

- `observation` defaults to `high`.
- `user_statement` defaults to `high` (operator-stated is operator-
  authoritative until contradicted).
- `agent_inference` defaults to `low` and CANNOT be written at `high`
  by the agent itself. An external verifier (via `memory_verify.v1`)
  can promote an inference to `high` once corroborated.
- `preference` and `promise` default to `medium`.
- `external_fact` confidence depends on source — declared at write
  time by the writing tool.

### §7.3 — `contradiction_links` table

Contradictions are 1-to-many. Storing them as a column on
memory_entries either denormalizes (JSON list) or doesn't fit. New
table:

```sql
CREATE TABLE memory_contradictions (
    contradiction_id   TEXT PRIMARY KEY,
    earlier_entry_id   TEXT NOT NULL,
    later_entry_id     TEXT NOT NULL,
    contradiction_kind TEXT NOT NULL CHECK (contradiction_kind IN (
        'direct',      -- statement directly contradicts earlier statement
        'updated',     -- preference/state changed; later supersedes earlier
        'qualified',   -- earlier was true under conditions C; later modifies C
        'retracted'    -- operator explicitly retracted the earlier claim
    )),
    detected_at        TEXT NOT NULL,
    detected_by        TEXT NOT NULL,    -- agent_id or operator
    resolved_at        TEXT,             -- NULL if unresolved
    resolution_summary TEXT,             -- operator-supplied narrative
    FOREIGN KEY (earlier_entry_id) REFERENCES memory_entries(entry_id),
    FOREIGN KEY (later_entry_id)   REFERENCES memory_entries(entry_id)
);

CREATE INDEX idx_contradictions_earlier ON memory_contradictions(earlier_entry_id);
CREATE INDEX idx_contradictions_later   ON memory_contradictions(later_entry_id);
CREATE INDEX idx_contradictions_unresolved ON memory_contradictions(resolved_at)
    WHERE resolved_at IS NULL;
```

`memory_recall.v1` gets a `surface_contradictions: bool` flag — when
true, every recall result includes `unresolved_contradictions: [...]`
linked entries. Default: true for `agent_inference`, false for
`observation` / `external_fact`.

### §7.4 — `last_challenged_at` field

```sql
ALTER TABLE memory_entries ADD COLUMN last_challenged_at TEXT;
```

Updated when:
- A contradiction row references the entry as `earlier_entry_id`.
- An operator explicitly calls a (new) `memory_challenge.v1` tool
  to mark an entry as challenged without writing a contradicting
  entry.
- A `memory_verify.v1` call lands (verification implicitly is a
  challenge that resolved in favor of the entry).

Stale-entry surfacing: `memory_recall.v1` gains a `staleness_threshold_days`
parameter. Entries with `last_challenged_at` older than the threshold
(or NULL with `created_at` older than the threshold) are flagged as
"stale" in the recall output. Default threshold is 90 days for
`preference`, 30 days for `agent_inference`, infinite (no flag) for
`observation` and `external_fact`.

### §7.5 — Schema bump v10 → v11

Migration is additive (ADD COLUMN with DEFAULT, CREATE TABLE IF NOT
EXISTS, CREATE INDEX IF NOT EXISTS). Pre-existing rows land with:
- `claim_type = 'observation'` (the safest default; explicit
  re-classification is operator-driven, not auto-attempted)
- `confidence = 'medium'` (avoids overclaiming on legacy rows)
- `last_challenged_at = NULL`

The migration writes one audit-chain event per agent:
`memory_schema_v11_migrated` with the row count migrated. No bulk
event — per-agent because the chain is per-agent.

### §7.6 — K1 (`memory_verify.v1`) interaction — additive

K1's sentinel-consent-grant verification stays. With multi-state
verification, the relationship is:

| K1 verification state | New `confidence` field |
|---|---|
| Not verified | `low`, `medium`, or `high` (set at write time) |
| Verified (sentinel grant present) | Promotes to `high` regardless of original |
| Verified + revoked (revocation event) | Reverts to `medium` (not back to original — the verification + revocation history matters) |

K1's API surface doesn't change. The promotion-to-`high` is computed
at read time by `memory_recall.v1`, not stored separately. No schema
bump for K1; the existing consent-grant table remains the source of
truth for the verified bit.

### §7.7 — Updates to ADR-0027 §6 audit obligations

Three new event types added to the v1 §6 table:

| Event | Triggered by |
|---|---|
| `memory_claim_type_set` | Write (every memory write — claim_type is required, so this is implicit) — folded into `memory_written` event payload, not a separate event. |
| `memory_contradicted` | Insert into `memory_contradictions`. Records earlier + later entry IDs, kind, detector. |
| `memory_challenged` | `memory_challenge.v1` tool call. Records entry id + challenger (operator vs. agent). Distinct from `memory_contradicted` because a challenge doesn't always have a contradicting entry yet. |
| `memory_contradiction_resolved` | Update to `memory_contradictions.resolved_at`. Records resolution summary. |
| `memory_schema_v11_migrated` | One per agent at first daemon boot post-migration. |

No event is emitted for `last_challenged_at` updates — those are
derived from contradiction / challenge / verify events, and emitting
a redundant event triples chain volume for no audit gain.

## §0 verification

The amendment adds columns + tables + audit events. Nothing is
removed. Pre-existing memory entries keep their semantics under the
DEFAULT migration values. K1 stays in force. ADR-0027 §1–§6 stay in
force.

§0 gate not invoked because no removal is happening. The audit
question is whether each new field carries its weight (§1–§4 each
have a concrete harm closed) and whether the migration is safe (§5
is additive-only).

## Trade-offs and rejected alternatives

**Float confidence vs. three-state.** Three-state. Float invites
agents to rationalize precision they don't have. Three-state aligns
with operator UI ("low / medium / high" is interpretable).

**`claim_type` enum vs. tag-based.** Enum. Tags are too loose — an
inference tagged `preference` is meaningfully different from an
observation tagged `preference`, and the difference matters for
mutation rules. Enum + CHECK constraint catches this at write time.

**Single `contradicts:` column on memory_entries vs. separate table.**
Separate table. Contradictions are 1-to-many (one entry can be
contradicted by multiple later entries) AND 1-to-many in the
opposite direction (one new entry can contradict multiple older
ones). A column doesn't model this without ugly delimited strings.
Separate table with two FKs is the correct shape.

**Auto-detect contradictions at write time.** Rejected for v0.2.
Auto-detection requires semantic comparison of memory contents —
expensive, error-prone, and the false-positive rate is its own harm
(false contradiction events trigger H-6 in reverse). v0.2 ships with
operator-supplied + agent-supplied (via `memory_challenge.v1`)
contradiction detection only. Auto-detection is an ADR-0036
(Verifier Loop, queued for v0.3) candidate.

**Promote to v11 vs. defer to v12 with other planned changes.** Bump
to v11 now. ADR-0038 H-6 mitigation depends on this; deferring means
H-6 stays open. The migration is small and additive; combining with
unknown future v12 changes increases coupling unnecessarily.

**Float `last_challenged_at` to a separate table.** Rejected — the
field is per-entry and updated frequently. Column is the right
shape; the contradictions detail goes to the separate table.

**Why is `agent_inference` confidence floor `low`, not just default
`low`?** Hard cap exists because an agent self-elevating its
inferences to `high` produces H-6 directly. The K1 verification path
is the legitimate way an inference can earn `high` — through
external corroboration. Self-confidence is bounded.

## Consequences

**Positive.**
- ADR-0038 H-6 ("memory overreach / inferred-preference cementing")
  closed at the data layer. Inference-vs-observation distinction
  becomes mandatory at write time, surfaceable at read time.
- `memory_recall.v1` results gain epistemic shape — UI can display
  inferences differently, voice renderer can introduce them
  differently. H-1 (sycophancy) gets a hook: the agent can no longer
  silently treat its own inferences as operator-stated facts.
- Stale-memory pressure becomes auditable. `last_challenged_at` +
  staleness threshold + contradiction surfacing produces an
  operator-visible "this preference might be outdated" signal.
- Future ADR-0036 (Verifier Loop, v0.3) has a concrete schema to
  build on — auto-detected contradictions land in the same table,
  with `detected_by` recording the verifier identity.
- K1 verification stays simple (one bit) but combines cleanly with
  the new confidence field at read time. No K1 rework.

**Negative.**
- Memory entries gain three columns + one new table + two new
  optional tools (`memory_challenge.v1`, indirectly via the
  `surface_contradictions` flag). Surface area grows.
- Migration time is proportional to memory_entries row count. For
  Companion-genre agents with long retention, the migration could
  be observable. Mitigation: migrate per-agent on first daemon
  boot; not all-at-once at startup.
- Operators who don't yet think epistemically about memory will see
  unexpected UI text ("From what I've noticed, ..."). Documentation
  needs to explain the distinction up-front.

**Neutral.**
- One `memory_recall.v1` parameter added (`surface_contradictions`).
  Default behavior stays compatible (default `false` for stable claim
  types means existing recall callers see no change).
- One `memory_recall.v1` parameter added (`staleness_threshold_days`).
  Defaults applied per-claim_type; existing callers see staleness
  flagged in results but no semantic change.

## Cross-references

- ADR-0027 v1 — base privacy contract; this amendment adds §7 + three §6 events.
- ADR-0022 — memory subsystem; schema v10 → v11.
- ADR-003X K1 — `memory_verify.v1`; combines with multi-state confidence at read time.
- ADR-0038 — companion harm model; H-6 closes at the data layer through this amendment.
- ADR-0036 — Verifier Loop (v0.3 queued); auto-detected contradictions ride on this amendment's schema.

## Open questions

1. **Should `claim_type` be settable post-write (re-classification)?**
   Lean no — claim_type at write time is the writer's claim. Re-
   classification creates a new entry with a `supersedes:` link to
   the old one. Operator-driven bulk re-classification (e.g., post-
   migration) goes through a new `memory_reclassify.v1` tool; the
   tool emits one `memory_reclassified` audit event per row, not a
   bulk event.

2. **`contradiction_kind = 'qualified'` — is this distinct enough
   from `'updated'` to keep?** Marginal. Lean keep — `qualified`
   captures cases where the earlier entry was true within scope
   (e.g., "I work nights *during summer*"). `updated` collapses this.
   v0.2 ships with both; revisit if `qualified` rate is < 5% of
   contradictions.

3. **Staleness threshold per genre?** Companion has long-retention
   memory; Observer has short-retention. Threshold defaults from §7.4
   are global. Per-genre thresholds are a reasonable extension; defer
   to v0.3 unless harm surfaces.

4. **`memory_challenge.v1` access — operator-only or also agent?**
   Lean operator-only for v0.2. Agent-self-challenge produces
   ambiguity ("did the agent challenge itself because it's
   uncertain, or because it wants to manipulate the operator's
   trust?"). Agents can record uncertainty via `confidence: low` at
   write time; explicit challenge stays operator-driven.

5. **Migration rollback?** Schema v11 → v10 rollback drops the new
   columns + table. Pre-existing audit events stay. Lean: support
   rollback only if a critical bug surfaces in v11; not a planned
   user-facing operation.

## Implementation tranches

- **T1** — Schema migration v10 → v11. Adds `claim_type`,
  `confidence`, `last_challenged_at` columns + `memory_contradictions`
  table + indexes. Migration test (v10 fixture → v11 + verify).

- **T2** — `MemoryEntry` dataclass gains the three fields. Memory
  write path requires `claim_type` (rejects writes that omit it
  post-migration; pre-migration callers get the default for back-
  compat). Tests for write-path enforcement.

- **T3** — `memory_recall.v1` gains `surface_contradictions` +
  `staleness_threshold_days` parameters. Default behaviors per §7.3
  + §7.4. Unit tests for each parameter.

- **T4** — `memory_challenge.v1` tool. Operator-only for v0.2.
  Audit event `memory_challenged`. Tests.

- **T5** — Voice renderer + UI hooks. Inferences prefixed
  "From what I've noticed,"; UI italicizes inferences. ADR-0017
  voice renderer post-filter step gains the epistemic-formatting
  pass. Tests on voice output.

- **T6** — `memory_recall.v1` K1-confidence fold. Verified entries
  surface as `confidence: high` in recall results regardless of
  stored value. Test that verified-then-revoked entries surface as
  `medium` (not original).

- **T7** — One-time `memory_reclassify.v1` tool for operator-driven
  bulk re-classification. v0.2 candidate; not blocking.

T1+T2 = "schema is in place" milestone — minimum for v0.2.
T3+T4+T5+T6 = "epistemic shape is operator-visible" milestone — full v0.2 close.
T7 = quality-of-life follow-up.

## Attribution

The framing "FSF's audit chain proves 'this happened' better than it
proves 'this belief is true' — a companion with durable memory needs
not only memory privacy, but also memory humility" is verbatim from
[SarahR1 (Irisviel)](https://github.com/SarahR1)'s 2026-04-30 review.
Her project (Nexus / Irkalla; public surface at
[nexus-portfolio](https://github.com/SarahR1/nexus-portfolio)) carries
the prior art for verified-memory schema discipline: MemoryNode
(content + metadata + latent state + graph edges + provenance), Iron
Gate (rule that automated memory starts unverified, only human
authority promotes to ground truth).

The FSF-specific design — three-state confidence, separate
`memory_contradictions` table, per-claim-type defaults, K1 fold,
schema-bump migration plan — is this amendment's work, shaped by FSF's
existing SQLite-discipline + audit-chain-as-source-of-truth
constraints. See `CREDITS.md`.
