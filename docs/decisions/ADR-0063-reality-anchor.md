# ADR-0063 — Reality Anchor (persistent ground-truth verification)

- **Status:** Accepted 2026-05-12. **T1 + T2 + T3 + T4
  shipped** across Bursts 251 (ground_truth.yaml +
  verify_claim.v1) + 252 (`RealityAnchorStep` in the
  governance pipeline + audit events + per-agent
  constitutional opt-out) + 253 (`reality_anchor` role in
  trait_tree / genres / tool_catalog / constitution_templates
  + singleton-per-forest enforcement at /birth). T5
  (conversation runtime pre-turn hook), T6 (correction
  memory), T7 (SoulUX pane) queued.
- **Date:** 2026-05-12.
- **Related:** ADR-0033 Security Swarm, ADR-0036 Verifier
  Loop (memory contradiction scanner), ADR-0049 K1 verified-
  memory tier, ADR-0049 audit signatures, ADR-003Y
  conversation runtime, ADR-0019 dispatcher + governance
  pipeline, ADR-0062 install-time gate (same refuse/warn
  pattern).

## Context

Forest is now at v1.0 license + 25 live agents + an audit
chain past 9400 entries. As the agent population grows and
the operator's reliance on agent outputs grows with it, two
failure modes get more expensive:

1. **Hallucination.** An agent asserts something that contradicts
   established reality — wrong file path, wrong schema version,
   wrong API URL, wrong license, wrong operator email. Small
   ones waste time; big ones (an agent about to write to a
   `prod_db` that doesn't exist or that has a different name)
   cause real harm.

2. **Drift.** An agent's stated context diverges from the
   conversation context the operator has been building over
   the last N turns. The operator caught it 5 minutes ago,
   but two more turns of agent output now build on the wrong
   premise.

ADR-0036's Verifier Loop already scans **memory entries** for
contradictions post-hoc. ADR-0049 K1 has a "verified" memory
tier promotable by external verification. What's missing is
**pre-action** verification — catching the contradiction
BEFORE the agent acts on it, against an explicit operator-
asserted ground truth.

The right shape is a layered **Reality Anchor**:

- **Substrate-layer step** in the governance pipeline that
  runs on every gated tool dispatch (~5ms overhead per call).
  Pattern-match-only. Reuses the same refuse-on-CRITICAL
  posture as ADR-0062 T4 install gate.
- **Agent-layer role** (`reality_anchor`) that other agents
  can delegate to via `delegate.v1` when they want a deeper
  LLM-grade pass. Pulls verified memory + ground truth +
  recent conversation context.
- **Persistent corrections memory** that recognizes when an
  agent has been corrected on the same claim multiple times,
  so repeated hallucinations escalate.

This ADR is also a public-facing differentiator for the ELv2
business model: "Forest agents run with a Reality Anchor —
your agent can't silently drift past your facts" is a real
sales-grade promise.

## Decisions

### Decision 1 — Refuse vs. warn matrix

| Finding | Default action |
|---|---|
| Direct contradiction with operator-asserted ground truth | **REFUSE** the tool call. 409 with the contradicting fact + the agent's claim. Same shape as ADR-0062 install-gate refusal. |
| Drift from recent conversation context | **WARN** with `reality_anchor_flagged` audit event. Tool call proceeds; operator sees the warning surfaced in approval queue / chat UI. |
| Unverified factual claim (no ground truth covers it, no recent context covers it) | **PASS** with `reality_anchor_unknown` informational event. The substrate is silent unless something can ANTI-confirm. |
| Strict mode (`?strict=true` on tool dispatch) | Drift escalates to REFUSE. Unverified factual claims escalate to WARN. |

Refusing direct contradictions matches the §0 Hippocratic
gate posture: we only block on things with zero false-positive
risk. Drift detection has wider false-positive surface (the
operator may have legitimately changed context) so it warns.

### Decision 2 — On by default + per-agent constitutional opt-out

`RealityAnchorStep` runs on every dispatch by default, same
posture as K6 hardware-binding (always on, opt-out via
constitution). A constitution can declare:

```yaml
reality_anchor:
  enabled: false
  reason: "creative-writing role; ground-truth pinning is counterproductive"
```

The opt-out is recorded in `agent_created` audit event so
operators auditing "which agents skip reality anchor?" can
filter the chain.

### Decision 3 — Layered ground-truth scope

Three sources, evaluated in priority order:

1. **Operator-global** (`config/ground_truth.yaml`) — the
   canonical "this is reality" source. Only the operator can
   edit. Loaded at daemon lifespan, hot-reloaded on
   `POST /reality-anchor/reload` (T7).

2. **Per-agent additions** in the agent's constitution YAML
   under a `ground_truth_additions` block. Agents may ADD
   facts ("this agent works on the v2 schema only") but
   **MAY NOT** override operator-global. A per-agent entry
   whose `id` collides with an operator-global entry is
   logged as a config error + ignored.

3. **Recent conversation context** (last N turns of the
   active conversation, per ADR-003Y). Used only for **drift**
   detection (D1 row 2), never for refusal. Conversation
   context is mutable + low-trust; we don't let it block
   actions.

The operator-global → per-agent-add direction means a
compromised agent can't rewrite its own reality.

### Decision 4 — Bootstrap ground-truth set

The initial `config/ground_truth.yaml` covers obvious operator
+ environmental facts the operator can confirm at a glance:

- operator identity (id, email)
- license (ELv2, post-B245 effective)
- repo url + path
- daemon URL + frontend URL
- platform (macOS, Mac mini M4)
- python version requirement (3.11+)
- SQLite schema version (current = v19, post-B243)
- audit chain canonical path (`examples/audit_chain.jsonl`
  per CLAUDE.md)
- write-lock pattern (`app.state.write_lock` RLock)

The set is operator-mutable; expansion as needed is expected
per Alex's 2026-05-12 ruling. No formal "this is the
canonical set" — the operator owns the truth.

### Decision 5 — Verification primitive: pattern-match in v1, LLM in v2

v1 (B251 ships): `verify_claim.v1` is **pure pattern
matching**. Each ground-truth fact has:

- `domain_keywords` — claim must mention at least one to be
  "in domain" for this fact
- `canonical_terms` — presence in the claim = confirmation
- `forbidden_terms` — presence in the claim WITHOUT a
  canonical term = contradiction
- `severity` — CRITICAL / HIGH / MEDIUM / LOW / INFO

Pros: fast (~1µs per fact × ~20 facts = bounded latency
under 1ms), deterministic, auditable diff in `git log` when
the catalog changes, no token cost.

Cons: misses semantically-equivalent paraphrases the
operator didn't enumerate. Acceptable tradeoff in v1; the
agent layer (D6) handles the deep semantic cases.

v2 (future ADR): LLM-grade pass invoked only when the
pattern pass returns `unknown` AND strict mode is set. Per
Alex's request the lightweight pass stays substrate; LLM
pass is opt-in.

### Decision 6 — Reality Anchor as both substrate AND agent

**Substrate layer** (ships T3, queued):
`RealityAnchorStep` in `tools/governance_pipeline.py` runs
on every tool dispatch with `side_effects ∈
{filesystem, external, network}`. Pure-pattern verification
against ground_truth + recent context. Refuse/warn per D1.

**Agent layer** (ships T4, queued): A `reality_anchor` role
+ constitution + genre tag. Other agents can `delegate.v1`
to it for a deliberate verification pass. The agent has:

- Tool kit: `verify_claim.v1`, `memory_recall.v1`,
  `llm_think.v1`
- Memory scope: `forest_wide` read access to verified-tier
  entries
- Genre: `verifier` (existing per ADR-0036)
- Single-instance singleton at the operator level (one
  `reality_anchor` per forest, like a Forge or Verifier
  Loop)

The substrate ALWAYS runs; the agent is OPTIONAL deep-check
on demand.

### Decision 7 — Correction memory + recurrence detection

A new registry table `reality_anchor_corrections`:

```sql
CREATE TABLE reality_anchor_corrections (
    claim_hash       TEXT PRIMARY KEY,   -- sha256 of normalized claim
    canonical_claim  TEXT NOT NULL,      -- the normalized form
    contradicts_fact_id TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    repetition_count INTEGER NOT NULL,
    last_agent_dna   TEXT,
    last_corrected_action TEXT  -- 'refused' / 'warned'
);
```

When the same claim hash reappears, `repetition_count` is
bumped and the `reality_anchor_repeat_offender` audit event
fires. Operators querying "this agent keeps hallucinating X"
get one event per repeat instead of N events to correlate.

## Tranches

| # | Tranche | Description | Status |
|---|---|---|---|
| T1 | `ground_truth.yaml` + loader | Operator-global catalog with bootstrap entries (D4). Loader handles operator-global + per-agent ADD layering (D3); rejects per-agent overrides. Pure-Python loader; no daemon dependency so tests don't need a running daemon. | shipping B251 |
| T2 | `verify_claim.v1` builtin | Pattern-match verifier (D5). Args: claim_text + optional fact_ids filter + agent_dna (for per-agent additions). Output: verdict, per-fact verdicts, citations, severity. side_effects=read_only. Tests cover each branch of the verdict matrix. | shipping B251 |
| T3 | `RealityAnchorStep` in governance pipeline | **DONE B252** — new step inserted between `HardwareQuarantineStep` and `TaskUsageCapStep` in `dispatcher.py`. Args are flattened to a "claim" via `_flatten_args_to_claim` (one nesting level deep + lists of strings) and run through `_reality_anchor_verify` (a substrate-cost inline of `verify_claim.v1` semantics). CRITICAL contradictions REFUSE with reason `reality_anchor_contradiction`; HIGH/MEDIUM/LOW contradictions WARN via `reality_anchor_flagged` but proceed. Per-agent opt-out via `reality_anchor: {enabled: false}` in the constitution YAML. Catalog load errors + verifier exceptions degrade to GO so a broken Reality Anchor never blocks legitimate work. KNOWN_EVENT_TYPES updated with both new event types. Note on ADR-T3 deviation: original spec said "skip for `side_effects=read_only`"; final implementation runs on ALL tools (read-only emissions are still worth flagging in the chain) — the skip-read-only guidance applies instead to the future T5 conversation-runtime hook. 20+ unit tests cover every branch. | shipped |
| T4 | `reality_anchor` role | **DONE B253** — role added to `config/trait_tree.yaml` (with full domain_weights) + `config/genres.yaml` (under guardian's roles list) + `config/tool_catalog.yaml` (kit: verify_claim.v1 + memory_recall.v1 + audit_chain_verify.v1 + llm_think.v1 + delegate.v1) + `config/constitution_templates.yaml` (4 policies: forbid_action_taking, forbid_ground_truth_mutation, require_citation, forbid_low_confidence_contradicted; risk_thresholds tighter than verifier_loop). Singleton-per-forest structurally enforced in `daemon/routers/writes/birth.py::_perform_create` — second active reality_anchor returns 409 with the existing agent's instance_id in the detail. Archive-then-rebirth path works. Plus diagnostic helpers (`diagnose-import.command` + `fix-cryptography-dep.command`) shipped after a cryptography-dep diagnosis incident at the start of this burst. | shipped |
| T5 | Conversation runtime pre-turn hook | ADR-003Y `pre_turn_emit` callback wired so the reality anchor inspects a final assistant turn's content before it lands. Drift detection compares to recent operator turns. Hooks into the same audit event family as T3. | queued B254 |
| T6 | Correction memory + recurrence | New table + registry accessor + `reality_anchor_repeat_offender` event. Bumps `repetition_count` on claim_hash collision. Operator query: "show me repeat offenders by agent." | queued B255 |
| T7 | SoulUX Reality Anchor pane | Ground-truth editor (read + add + edit, no delete without confirmation), recent flags timeline, correction-history table by agent. New tab in the SoulUX frontend. | queued B256 |

Total estimate: ~5.5 bursts. B251 lands the foundation; B252
delivers the substrate-layer enforcement; B253-B255 round out
the agent layer + persistence; B256 closes the operator UI.

## Consequences

**Positive:**

- Concrete defense against agent hallucination at the gate,
  not after the fact. Matches Forest's ADR-0049 K1
  philosophy of catching errors at the point of action.
- Operator-asserted ground truth is the operator's
  responsibility to keep current — Forest doesn't pretend to
  know the world better than the human.
- Lightweight default + opt-in deep pass means baseline cost
  is near-zero. An agent that never hits a ground-truth
  domain pays no token cost.
- Per-agent ADD layering supports specialized agents
  (security-domain, dev-domain) with their own micro-ground-
  truths.
- Persistent correction memory turns "agent X keeps making
  this same mistake" from anecdote into queryable signal.

**Negative:**

- Pattern-match in v1 misses semantic paraphrases. Operators
  who care must extend the catalog OR opt into v2's LLM
  pass when it ships.
- A creative-writing agent will need the
  `reality_anchor.enabled: false` opt-out or it'll fire on
  every fictional assertion. Documented in T4's runbook.
- Ground-truth catalog drift: if the operator forgets to
  update a fact (e.g., daemon URL changes), the anchor
  refuses legitimate actions. Mitigation: T7's editor +
  staleness warnings on facts not confirmed in N days.

**Risks accepted:**

- The substrate-layer step adds ~5ms per gated dispatch. At
  Forest's current ~1Hz dispatch rate that's negligible;
  high-throughput scenarios would need pattern compilation
  caching (already in v1).
- A determined attacker who compromises an agent can submit
  claims crafted to evade the pattern matcher. Real defense
  against that lives in ADR-0049 signed audit chain + ADR-
  0051 sandboxing. Reality Anchor is one layer in the
  defense-in-depth stack, not the whole stack.

## Out of scope

- Real-time fact lookup (web search, knowledge graph). The
  operator owns ground truth; this ADR doesn't make Forest
  go look things up.
- Cross-operator fact federation. Each operator's ground
  truth is private and local.
- Probabilistic confidence scoring beyond the four-level
  severity tier. v1 is binary-per-fact.

## References

- ADR-0036 Verifier Loop: post-hoc memory contradiction
  detection (the temporal complement to this pre-action
  detector)
- ADR-0049 K1 verified-memory tier
- ADR-0019 governance pipeline (the substrate this hooks
  into)
- ADR-003Y conversation runtime (T5 hook target)
- ADR-0062 install-time scanner gate (same refuse/warn
  pattern; ADR-0063 generalizes it from install paths to
  every gated dispatch)
