# ADR-0054 — Procedural Shortcut Dispatch Path

**Status:** Proposed (2026-05-06). Pairs with ADR-0022 (memory
subsystem) and ADR-0019 (tool dispatch + governance pipeline).
Userspace-only delivery — uses existing kernel ABI surfaces; ADDS
one schema table (additive v15→v16 migration) and one new audit-
chain event type.

## Context

Forest's current dispatch shape: every conversation turn fires
`llm_think.v1` end-to-end. A 200-token reply on qwen2.5-coder:7b
takes 2-5 seconds on the M4 baseline. For recurring situations
the operator has already seen the assistant respond to many times
(daily greeting, "what's on my schedule," common refusals,
canned acknowledgements), the LLM round-trip is overhead — the
assistant has already learned how to handle the case but
re-derives the answer from scratch each time.

The 2026-05-06 outside assessment surfaced this directly:

> Reactive, in-the-moment agents that are trainable through real
> experience, repeatable, deeply personable, and stay grounded
> in current reality. Priority: Fast reactions + procedural
> memory over long-chain planning and massive pre-training.
>
> Fast "pattern match → react" pathway (bypass heavy LLM when
> possible).

The substrate to build this is mostly already in place:

- ADR-0022 procedural memory layer exists but is underused (most
  writes go to episodic / semantic).
- ADR-0019 governance pipeline already supports adding new
  pre-execute steps (the existing 8 steps run before tool
  execution).
- Embedding tooling is wired (`nomic-embed-text` available via
  Ollama + the `embed` task_kind in `llm_think.v1`).
- ADR-0005 audit chain accepts new event_type strings without
  schema migration.

What's missing: a step that BEFORE `llm_think` runs, checks
procedural memory for a high-confidence situation→action match
and short-circuits to the recorded action when one exists.

This ADR specifies that step.

**Out of scope for this ADR** (related but separately tractable
items from the 2026-05-06 assessment §5):

- Emotional-state tracker that modulates traits at runtime —
  separate substrate, separate ADR if pursued.
- Trait-slider runtime attention/recall weighting — separate
  ADR; no conflict with this one.
- `soul.md` "gradual evolution" — directly conflicts with
  ADR-0001 D2 (constitution_hash immutable per agent). If pursued,
  needs a doctrine-level decision about identity invariants.
  This ADR explicitly does NOT touch identity.

## Decision

Add a **`ProceduralShortcutStep`** to the dispatch pipeline,
running before `LookupStep` (the step that resolves which tool
to fire). When called for an `llm_think` task in conversation
context with a procedural-memory match above the confidence
threshold, the step short-circuits the dispatch with the recorded
action, emits a `tool_call_shortcut` audit event, and returns
without touching the LLM provider.

### Decision 1 — Schema: new `memory_procedural_shortcuts` table

Sibling table to `memory_entries` rather than column extensions
on the existing schema. Reasons:

- Procedural-shortcut entries have a different access pattern
  (vector search on situation_embedding) than episodic /
  semantic / standard procedural memory (text search on body).
- Keeps the v0.6 ABI surface narrow — episodic/semantic memory
  consumers don't need to learn about shortcut fields.
- Per ADR-0040 trust-surface decomposition rule, a separate
  table = a separate `allowed_paths` grant target.

Schema (v15 → v16, additive only):

```sql
CREATE TABLE memory_procedural_shortcuts (
    shortcut_id        TEXT PRIMARY KEY,         -- uuid4
    instance_id        TEXT NOT NULL,            -- agent owner; FK to agents
    created_at         TEXT NOT NULL,            -- ISO 8601 UTC
    last_matched_at    TEXT,                     -- ISO 8601 UTC; NULL until first hit
    last_matched_seq   INTEGER,                  -- audit chain seq of last match

    -- Situation fingerprint
    situation_text     TEXT NOT NULL,            -- the operator's input that triggered storage
    situation_embedding BLOB NOT NULL,           -- float32 array (typically 768-dim, nomic-embed-text)

    -- Recorded action — what the assistant did/said
    action_kind        TEXT NOT NULL,            -- 'response' | 'tool_call' | 'no_op'
    action_payload     TEXT NOT NULL,            -- JSON: response text OR tool spec OR null

    -- Reinforcement state
    success_count      INTEGER NOT NULL DEFAULT 0,
    failure_count      INTEGER NOT NULL DEFAULT 0,

    -- Provenance
    learned_from_seq   INTEGER NOT NULL,         -- audit chain seq of the source turn
    learned_from_kind  TEXT NOT NULL,            -- 'auto' | 'operator_tagged'

    FOREIGN KEY (instance_id) REFERENCES agents(instance_id)
);

CREATE INDEX ix_psh_instance ON memory_procedural_shortcuts(instance_id);
```

The embedding is stored as a BLOB (float32 little-endian). Search
is exact-distance against the BLOB at dispatch time — no FAISS /
similar yet. At single-operator scale (hundreds of entries per
agent), brute-force cosine similarity in NumPy completes in
single-digit milliseconds. If an operator's table grows past a
few thousand entries, the index can graduate to FAISS without
schema change.

### Decision 2 — Match algorithm

A two-stage filter:

1. **Embedding similarity:** cosine(situation_embedding,
   query_embedding) ≥ `FSF_PROCEDURAL_COSINE_FLOOR` (default
   `0.92`). Tunable per-operator via env var.
2. **Reinforcement gate:** `success_count - failure_count ≥
   FSF_PROCEDURAL_REINFORCEMENT_FLOOR` (default `2`). Stops a
   single one-off interaction from becoming an automatic
   response.

Both gates must pass. If multiple entries match, the highest
combined score `cosine + 0.05·log(success_count + 1)` wins.
Logarithm not linear so a recently-added entry with 3 successes
doesn't get crushed by an old entry with 100.

The conservative defaults (cosine 0.92, 2+ net successes) are
chosen so the FIRST plausible application of this surface
prefers false-negative (fall through to LLM) over false-positive
(skip LLM with wrong answer). Operators can dial down once
they've calibrated against their own usage patterns.

### Decision 3 — Pipeline integration

Insert `ProceduralShortcutStep` in `governance_pipeline.py`
BEFORE `LookupStep`. The step:

```
def evaluate(self, dctx: DispatchContext) -> StepResult:
    if not self._is_eligible(dctx):
        return StepResult.go()           # not in scope; pass through
    candidate = self._find_match(dctx)
    if candidate is None:
        return StepResult.go()           # no match; let llm_think run
    return StepResult.shortcut(candidate)
```

`StepResult.shortcut(candidate)` is a new verdict (alongside the
existing GO / REFUSE / PENDING). Downstream pipeline steps don't
fire when shortcut wins. The dispatcher returns the candidate's
recorded action as if it came from llm_think.

Eligibility — the step ONLY fires when ALL of these are true:

- `dctx.tool_name == "llm_think"`
- `dctx.args.get("task_kind") == "conversation"`
- The conversation's `domain == "assistant"` (per ADR-0047) —
  multi-agent rooms (Y3) keep the deterministic-LLM-every-turn
  semantic; only the Persistent Assistant uses shortcuts
- The agent's posture is NOT `red` (per ADR-0045: red = full
  cautious path, NO shortcuts)
- `FSF_PROCEDURAL_SHORTCUT_ENABLED=1` (default off in v0.1; flip
  on once the surface is calibrated against real conversations)

Posture interaction:
- **green:** shortcuts fire freely subject to confidence floors
- **yellow:** shortcuts fire but emit an additional
  `tool_call_shortcut_under_caution` audit event so the operator
  can audit them post-hoc. Approval gate not added (would defeat
  the point).
- **red:** shortcuts NEVER fire. Full LLM path. Same global-
  brake semantic as ADR-0045.

### Decision 4 — Audit-chain visibility

New event type: `tool_call_shortcut`. Emitted alongside the
substituted action's normal `tool_call_succeeded` event. Carries:

```json
{
  "event_type": "tool_call_shortcut",
  "event_data": {
    "instance_id": "...",
    "session_id": "conv-...",
    "shortcut_id": "...",
    "matched_situation_text": "...",     // first 200 chars
    "cosine_similarity": 0.964,
    "reinforcement_score": 5,
    "action_kind": "response",
    "tokens_saved_estimate": 200,
    "llm_round_trip_skipped": true
  }
}
```

**Critical**: the substituted action ALSO emits the standard
`tool_call_succeeded` event with `metadata.shortcut_id` set.
Operators querying the chain by `tool_call_succeeded` see all
turns; querying by `tool_call_shortcut` shows only the shortcut
hits. Both event_types use the existing event_data shape per
ADR-0005 canonical-form contract — additive only.

### Decision 5 — Reinforcement (operator feedback loop)

Two paths to populate / strengthen the table:

**Auto-capture (default, opt-in):** After every llm_think reply,
the daemon writes a `memory_procedural_shortcuts` row with
`success_count=0, failure_count=0, learned_from_kind='auto'`.
The entry is INELIGIBLE for matching until it accumulates
`success_count ≥ FSF_PROCEDURAL_REINFORCEMENT_FLOOR` matches.
Reinforcement comes from operator feedback (below). Auto-capture
is gated by `FSF_PROCEDURAL_AUTO_CAPTURE=1` so an operator who
doesn't want their assistant building a shortcut table can
disable it.

**Operator-tagged (always):** A new tool `memory_tag_outcome.v1`
takes a `turn_id` + `outcome ∈ {good, bad}` and updates the
matching shortcut's success/failure counters. Surfaced in the
chat UI as thumbs-up/thumbs-down on each agent turn.

Counter semantics:
- `good` tag: `success_count += 1`. If the entry was created via
  auto-capture and crosses the reinforcement floor, it becomes
  eligible for matching.
- `bad` tag: `failure_count += 1`. If `failure_count >
  success_count`, the entry is soft-deleted (matching skips it).
  Hard delete is operator-driven via a `memory_forget_shortcut.v1`
  tool.

### Decision 6 — Operator overrides

Three env-var knobs:

| Variable | Default | Effect |
|---|---|---|
| `FSF_PROCEDURAL_SHORTCUT_ENABLED` | `0` | Master switch. v0.1 ships off; flip on after the operator has tagged some interactions. |
| `FSF_PROCEDURAL_AUTO_CAPTURE` | `0` | Whether `llm_think` replies auto-store shortcuts. Off by default — assistant doesn't build a behavior table without operator opt-in. |
| `FSF_PROCEDURAL_COSINE_FLOOR` | `0.92` | Cosine similarity threshold. |
| `FSF_PROCEDURAL_REINFORCEMENT_FLOOR` | `2` | Net positive reinforcements required for matching eligibility. |

Plus a Chat-tab settings card surface (queued for UI tranche T6
of this ADR's implementation) that exposes the current shortcut
table for review + per-row delete.

## Implementation tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Schema + table accessor | v15→v16 migration; `MemoryProceduralShortcutsTable` with put/get/list/search/cosine helpers | 1 burst |
| T2 | Embedding adapter | Wire `nomic-embed-text` calls (via the existing llm_think `embed` task_kind) into the shortcut path; NumPy cosine + reinforcement gate | 0.5 burst |
| T3 | `ProceduralShortcutStep` | Pipeline step + StepResult.shortcut verdict + dispatcher branch | 1 burst |
| T4 | Audit emission + tool_call_shortcut event | Emit alongside the substituted action's success event; extend the audit chain canonical-form tests | 0.5 burst |
| T5 | Reinforcement tools | `memory_tag_outcome.v1` + `memory_forget_shortcut.v1` + Chat-tab thumbs surface | 1 burst |
| T6 | Settings UI + safety guide | Chat-tab card to review/delete shortcuts; `docs/runbooks/procedural-shortcuts.md` operator guide | 0.5 burst |

Total estimate: 4-5 bursts.

T1 + T2 + T3 land the substrate; T4 closes the audit-chain
visibility; T5 closes the operator-feedback loop; T6 is the
operator-facing surface. All six tranches are independently
shippable; the master switch defaults off so partial implementation
doesn't accidentally enable shortcuts on a half-built path.

## Consequences

**Positive:**

- Closes the assessment's core "fast reactions" gap. Recurring
  interactions skip the 2-5s LLM round-trip and resolve in
  <50ms. Operator-visible latency wins on common interactions.
- Trainability: operators can shape the assistant's behavior by
  tagging turns good/bad without retraining a model. Procedural
  memory grows from real use, not pre-training.
- Operator-controlled: master switch off by default; auto-capture
  off by default; reinforcement floor blocks single-shot bad
  patterns from becoming automatic.
- Audit chain captures every shortcut by name + similarity score
  + reinforcement state, so an operator can always reconstruct
  why a shortcut fired.
- Posture-aware: red posture disables all shortcuts; yellow
  emits a caution-event for review.
- Doesn't conflict with ADR-0001 identity immutability:
  procedural memory is per-instance state, not identity.
  Constitution_hash + DNA stay stable; only what the agent KNOWS
  evolves, not what it IS.

**Negative:**

- Schema migration. v15→v16 adds the new table; pre-v16 daemons
  obviously don't have shortcuts available. Existing rows in
  `memory_entries` are unaffected.
- Memory budget: each entry stores a 768-dim float32 embedding
  (~3 KB per row). 1000 entries per agent = ~3 MB. Tractable
  but not free.
- "Stale shortcut" risk: if the operator's situation changes
  (new project, new role) but old shortcuts still match, the
  assistant gives outdated answers. Mitigation: reinforcement
  floor + operator's bad-tag drops failure_count quickly; in
  practice operators flip to red posture during onboarding to a
  new context, which disables shortcuts entirely.
- Embedding requires Ollama's `nomic-embed-text` to be
  available. Already a Forest dependency for other paths, but
  if Ollama is down, shortcuts fall through to llm_think (which
  also needs Ollama, so no degradation beyond the existing
  Ollama-down failure mode).
- Cross-conversation shortcut leak: a shortcut learned in
  conversation A could fire in conversation B (same agent). The
  embedding match is what gates this — different conversation
  contexts produce different embeddings. Acceptable; the test
  suite should cover the case explicitly.

**Neutral:**

- Posture interaction is the same model the operator already
  knows — green permissive, yellow audited, red blocked.
- The `memory_tag_outcome.v1` tool adds one more thumbs-up/down
  surface to the chat UI. Voluntary; no blocking change.
- Future extension: when ADR-0049 per-event signatures lands,
  `tool_call_shortcut` events get signed same as any other event.
  No special handling needed.

## What this ADR does NOT do

- Does NOT bypass the constitution. The recorded action was
  ITSELF generated through the full constitution + governance
  path the first time (auto-capture happens AFTER llm_think
  succeeded under all gates). Replaying that action is the same
  as re-running llm_think and getting the same answer — except
  faster.
- Does NOT bypass posture clamps. Red posture disables
  shortcuts entirely; yellow emits a caution event; green is
  permissive.
- Does NOT change the constitution_hash on shortcut hit.
  Procedural memory is per-instance state, not identity.
- Does NOT introduce a new LLM model dependency. Uses existing
  `nomic-embed-text` via the existing `llm_think` embed task.
- Does NOT specify a UI for browsing the shortcut table — that's
  a tranche T6 deliverable, scoped after the substrate lands.
- Does NOT give the assistant the ability to learn malicious
  behaviors autonomously. Auto-capture only stores what the
  assistant ALREADY produced through the constitution-checked
  llm_think path; reinforcement requires operator tagging
  (default) or accumulated successful matches (which themselves
  require ≥ reinforcement_floor prior tags).

## References

- ADR-0001 — Hierarchical trait tree (identity invariants this
  ADR doesn't touch)
- ADR-0005 — Audit chain canonical-form contract (additive
  event_type)
- ADR-0019 — Tool dispatch + governance pipeline (where the
  step inserts)
- ADR-0022 — Memory subsystem (procedural layer this ADR
  builds on)
- ADR-0040 — Trust-surface decomposition rule (why the new
  table is sibling, not column-extension)
- ADR-0044 — Kernel/userspace boundary (this ADR is userspace
  with one additive schema migration)
- ADR-0045 — Agent posture (red/yellow/green interaction)
- ADR-0047 — Persistent Assistant Chat (where shortcuts fire)
- 2026-05-06 outside assessment §5 — the framing this ADR
  responds to

## Credit

The "fast pattern-match → react" framing came from the
2026-05-06 outside assessment. The substrate compatibility work
— specifically the recognition that procedural memory + the
existing pipeline + the existing audit chain are enough — was
the architecture review against Forest's existing primitives.
The doctrine point that shortcuts ride per-instance state, not
identity, is what keeps this compatible with ADR-0001.
