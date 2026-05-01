# ADR-0036 — Verifier Loop (auto-detected memory contradictions)

- **Status:** Proposed (filed 2026-05-01; v0.3 candidate). Awaiting orchestrator promotion.
- **Date:** 2026-05-01
- **Supersedes:** —
- **Related:** ADR-0027 + amendment (memory privacy contract + epistemic metadata — this ADR builds on the `memory_contradictions` table from §7.3 + the `memory_challenge.v1` tool from §7.4); ADR-003X K1 (`memory_verify.v1` — the existing single-bit verification primitive this ADR layers a continuous loop on top of); ADR-0035 (Persona Forge — auto-detected contradictions feed into external_correction proposals); ADR-0038 (companion harm model — H-6 "memory overreach" is partially closed by ADR-0027-am at the data layer; this ADR closes the runtime-detection gap); ADR-0033 (Security Swarm — same architectural pattern: small focused agents that operate across the substrate without owning it).
- **External catalyst:** [SarahR1 (Irisviel)](https://github.com/SarahR1) — comparative review 2026-04-30. The MemoryNode + Iron Gate framing she cited is the prior art that informs this ADR's design. Her review surfaced that ADR-0027-am closes the *data-layer* gap (claim_type / confidence / contradictions table), but the *runtime-detection* gap remained — entries don't auto-flag as contradictory, contradictions are operator/agent-supplied at v0.2.

## Context

ADR-0027-amendment §7.3 ships the `memory_contradictions` table at
v0.2. The table is populated by:

- Operator scrutiny via `memory_challenge.v1` (ADR-0027-am T4).
- Agent self-flagging at write time (an agent can `claim_type='preference'`
  + flag a prior `preference` as contradicted).
- Manual operator INSERTs through admin tools (deferred to ADR-0037).

What's NOT populated automatically: contradictions detected by an
external scan. Per the v0.2 amendment §7.3 design notes:

> Auto-detect contradictions at write time. Rejected for v0.2.
> Auto-detection requires semantic comparison of memory contents —
> expensive, error-prone, and the false-positive rate is its own
> harm (false contradiction events trigger H-6 in reverse).
> v0.2 ships with operator-supplied + agent-supplied contradiction
> detection only. **Auto-detection is an ADR-0036 (Verifier Loop,
> queued for v0.3) candidate.**

This ADR is that candidate.

The catalyst review's prior-art framing: SarahR1's Nexus / Irkalla
project carries a MemoryNode schema with provenance + verification
metadata + Iron Gate (the rule that automated memory starts
unverified, only human authority promotes to ground truth). FSF's
K1 + ADR-0027-am v11 schema is structurally compatible. What FSF
hasn't built yet is the **continuous scan loop** — an agent (or
daemon-side process) that walks the memory log periodically,
classifies entries against each other, and stamps contradictions
when it finds them. That's this ADR.

The memory-humility framing applies: "FSF's audit chain proves
'this happened' but not 'this belief is true' — a companion needs
memory humility." A scan loop that auto-detects contradictions is
the operational shape of memory humility — the agent's inferences
get checked against its observations, and disagreements surface.

## Decision

### §1 — The Verifier as a small dedicated agent

The Verifier is **not** a daemon-side cron. It's a Guardian-genre
agent in the Forest swarm, born from a `verifier_loop` role. The
operator births one Verifier per per-agent-cluster (or one per
operator) at choice. The agent has its own constitution + audit
trail + memory + tool kit. This makes the Verifier visible in the
same agent listing, gives it the same governance posture as every
other agent, and lets operators birth-multiple for cross-checking.

Genre claim: **Guardian** (read_only ceiling, L3 default initiative,
private memory for its own findings). The Verifier doesn't *write*
to other agents' memory — it stamps `memory_contradictions` rows
through a new `memory_flag_contradiction.v1` tool that has the same
operator-gating shape as `memory_challenge.v1`.

### §2 — Scan strategy

Per-scan, the Verifier:

1. **Pulls candidate pairs** from the target agent's memory. Pairs are
   eligible when:
   - Same `instance_id` (intra-agent scan; cross-agent scan is v0.4
     work — needs ADR-0027 §1 read-scope routing).
   - Both `claim_type IN ('preference', 'user_statement', 'agent_inference')`.
   - Same content-derived "topic key" (cheap LIKE match on tags +
     word-overlap heuristic at v0.3; embedding-cosine at v0.4 if
     concrete need surfaces).
   - Not already in `memory_contradictions` (avoid re-flagging
     resolved cases).

2. **Classifies each pair** via `llm_think.v1` with a constrained
   prompt: "Are these two statements about the same topic?
   Contradictory? If so, which kind: direct / updated / qualified /
   retracted?" (The four kinds match ADR-0027-am §7.3's CHECK enum.)

3. **Acts on classifications** when confidence exceeds threshold:
   - Same topic + contradictory → `memory_flag_contradiction.v1`
     INSERT into `memory_contradictions`. `detected_by` is the
     Verifier's instance_id. `contradiction_kind` per §7.3 enum.
   - Same topic + non-contradictory → noop.
   - Different topics → noop.

4. **Records its own audit trail** of the scan: how many pairs
   considered, how many flagged, how long the scan took. Per-scan
   audit-chain event `verifier_scan_completed`.

### §3 — Cadence + scope

The Verifier runs on a **schedule + on-demand** model:

- **Schedule.** Per-target-agent cron-style: every 24 hours (default;
  operator-tunable per Verifier birth), scan target agent's memory
  added since last scan. Agents who write little memory get
  inexpensive scans; verbose agents pay proportionally.

- **On-demand.** Operator can trigger an immediate scan via the
  `/verifier/scan` endpoint, optionally scoped to (target_instance,
  time_window). Useful for "did this agent contradict itself
  yesterday?" investigations.

Resource posture: the LLM token cost is non-trivial. v0.3 ships with
conservative defaults — top-N=20 candidate pairs per scan, no
embedding pre-filter. Operators who run many agents may need to
tune via per-Verifier `task_caps_set` budgets.

### §4 — False-positive handling

False positives are this ADR's primary risk. ADR-0027-am §7.3
explicitly noted: *"the false-positive rate is its own harm (false
contradiction events trigger H-6 in reverse)."* If the Verifier
flags A and B as contradictory when they're actually about
different topics, the recall-time UI flag misleads the operator.

Mitigations:

1. **Confidence threshold.** Only flag at high LLM confidence (Verifier
   constitution requires `llm_think.v1` confidence ≥ 0.8 — the
   verifier_loop role's `min_confidence_to_act` floor). Low-
   confidence cases are skipped, not auto-flagged.

2. **Operator review surface.** Every flag has `detected_by =
   <verifier_instance_id>`. Operators reviewing memory_contradictions
   in the dashboard (ADR-0037) can filter by detector and audit
   the Verifier's track record. A noisy Verifier surfaces in the
   review log; the operator can re-birth with stricter thresholds
   or archive it.

3. **Ratification dial.** New `memory_contradictions.flagged_state`
   column (schema v12 candidate): values `flagged_unreviewed` /
   `flagged_confirmed` / `flagged_rejected` / `auto_resolved`. The
   Verifier's flags land at `flagged_unreviewed`. Operators move
   them through the lifecycle. Recall surfaces (ADR-0027-am T3)
   default to filtering out `flagged_rejected` so a known-false
   flag stops surfacing on every recall.

### §5 — Iron Gate alignment (ADR-003X K1)

K1 verification (`memory_verify.v1`) stamps a one-bit "verified"
flag on an entry — the Iron Gate primitive. The Verifier Loop
adds the **inverse** signal: stamps "potentially-contradicted" via
the contradictions table. Together they give:

- Verified + no contradiction → confidence='high' at recall
- Verified + contradiction → schema bug (flag as contradiction
  resolution path; verifier shouldn't flag verified entries unless
  there's external corroboration)
- Unverified + no contradiction → stored confidence at recall
- Unverified + contradiction → recall surfaces both stored
  confidence AND the contradiction reference (operator decides)

The combination is the full Iron Gate semantic SarahR1's review
described — "automated memory starts unverified, only human
authority promotes to ground truth." K1 is the promotion path;
Verifier Loop is the demotion-toward-skepticism path.

### §6 — Cross-agent scan (v0.4 candidate, NOT in this ADR)

Out of scope. ADR-0027 §1 read-scope routing means a Verifier
scanning agent A's `private` memory would need to be in A's
lineage chain. Cross-agent scan policy is its own ADR. This ADR
ships intra-agent scan only — sufficient for the H-6 / memory
humility use case the catalyst review surfaced.

## Trade-offs and rejected alternatives

**Verifier as daemon-side process.** Rejected. Daemon-side processes
are invisible in the agent listing, don't have constitutions, don't
have governance posture. Making the Verifier an agent gives it the
same accountability surface as every other dispatching entity in
the system. ADR-0033 Security Swarm pattern is the precedent.

**Auto-detection at write time.** Rejected (deferred from
ADR-0027-am §7.3). Write-time detection runs on every memory write,
which is expensive + amplifies false positives. Continuous scan
in batches is cheaper + more reviewable.

**Embedding-similarity matching (instead of LLM classification).**
Deferred to v0.4. Embedding cosine is cheaper per pair but requires
a model + storage for embeddings. v0.3 ships with LLM classification;
the candidate-pair pre-filter (§2.1 word-overlap) keeps the LLM
load bounded.

**Auto-resolve "obviously" resolved contradictions.** Rejected. An
auto-resolved contradiction is a Verifier deciding which side
wins — that's the operator's call, not the Verifier's. The
Verifier flags + records confidence; operator ratifies / rejects
/ resolves.

**Operator-only Verifier (no agent role).** Rejected. The whole
point is automated continuous scan. Operator-only collapses to
"the operator runs the scan tool manually" — no different from
ADR-0027-am T4's `memory_challenge.v1` semantic.

## Consequences

**Positive.**
- Memory humility becomes operational: agents' inferences get
  checked against their observations + each other's statements over
  time, contradictions surface, operator decides.
- ADR-0038 H-6 ("memory overreach / inferred-preference cementing")
  closed at the runtime-detection layer (data layer was closed by
  ADR-0027-am). The full H-6 mitigation chain is now: claim_type
  distinguishes inference from observation (data); confidence floor
  prevents agent self-elevation (data); contradictions surface
  through recall (data); Verifier Loop proactively detects
  contradictions (runtime).
- ADR-0035 Persona Forge gets a high-quality input: persistent
  unresolved contradictions on the same axis trigger
  `external_correction` proposals to the operator.
- New Verifier role + constitution template + tool surface
  (`memory_flag_contradiction.v1`).

**Negative.**
- LLM token cost per scan. Operators running many agents may need
  to budget. Mitigation: scan cadence is operator-tunable.
- Schema v11 → v12 migration for the `flagged_state` column on
  `memory_contradictions` (§4). Pure addition; same shape as the
  v10→v11 migration.
- False-positive operator burden, even with high-confidence
  threshold. Mitigation: review surface (§4.2) lets operators
  audit the Verifier's track record + tune.

**Neutral.**
- `audit_events` gains `verifier_scan_completed` event type. Volume
  is one event per scan run per Verifier — modest.

## Cross-references

- ADR-0027-amendment §7.3 — `memory_contradictions` table this ADR populates.
- ADR-0027-amendment §7.4 — `memory_challenge.v1` shape this ADR's `memory_flag_contradiction.v1` mirrors.
- ADR-0035 — Persona Forge consumes auto-detected contradictions for `external_correction` proposals.
- ADR-0037 — Observability dashboard surfaces the Verifier's track record (§4.2).
- ADR-0033 — Security Swarm precedent for "small focused agent that operates across substrate without owning it."

## Open questions

1. **Per-target Verifier or one-Verifier-watches-many?** Lean
   per-target for v0.3 (one Verifier per Companion, sharing across
   many security_swarm agents that don't have epistemic memory
   needs). Operator decides at birth time.

2. **Should Verifier scan its own memory?** Recursive scan is
   philosophically interesting but operationally risky (Verifier
   might flag its own flags as contradictory). Lean no for v0.3 —
   Verifier's own memory is private to itself, not subject to
   Verifier scan. v0.4 may revisit if a concrete need surfaces.

3. **What's the minimum LLM provider posture?** `llm_think.v1` runs
   through the active provider; for a Companion's Verifier the
   Companion-genre `local_only` provider constraint applies. Means
   local Ollama models do the classification. Quality-of-detection
   is bounded by local model quality. Frontier-provider Verifiers
   (for non-Companion targets) can use larger models. Document
   this honestly in the operator UI.

4. **How does Verifier interact with Y7 lazy summarization?**
   Conversation turn bodies purge after retention; the Verifier's
   scan can use turn `summary` as a fallback. Body_hash + summary
   are sufficient to flag contradictions even after purge.

5. **Cross-Verifier consensus.** If two Verifiers disagree on a
   contradiction, what wins? Lean: both record their own flags
   independently; operator sees both `detected_by` values and
   decides. No automatic consensus.

## Implementation tranches

- **T1** — `verifier_loop` role in `config/trait_tree.yaml`
  + Guardian-genre claim + constitutional template policies.
  Birth a Verifier via standard `/birth` flow.

- **T2** — `memory_flag_contradiction.v1` tool (ADR-0027-am-pattern,
  filesystem-tier, operator-only via constitutional kit gate). Adds
  rows to `memory_contradictions` with `detected_by =
  ctx.instance_id`.

- **T3** — Scan implementation: candidate-pair pre-filter (§2.1)
  + LLM classification (§2.2) + acts on classification (§2.3)
  + audit event emission (§2.4). New skill `verifier_scan.yaml`.

- **T4** — Scheduler: per-Verifier cron via the existing scheduled-
  task surface. Defaults to 24-hour cadence. Operator-tunable per
  Verifier.

- **T5** — `/verifier/scan` daemon endpoint for on-demand scans.

- **T6** — Schema v11 → v12: add `flagged_state` column to
  `memory_contradictions`. Default `flagged_unreviewed` for
  Verifier-flagged rows; `flagged_unreviewed` -> `flagged_confirmed`
  on first operator review (manual or auto in v0.4).

- **T7** — `memory_recall.v1` extension (`surface_contradictions`
  output extended): flagged_state surfaces alongside the
  contradiction details. Operators can filter
  `flagged_state == 'flagged_rejected'` from recall output.

T1+T2 = "Verifier exists, can flag manually" — minimum bar.
T3+T4+T5 = "Verifier auto-detects on schedule + on-demand" — full v0.3 close.
T6+T7 = lifecycle + recall integration.

## Attribution

The MemoryNode + Iron Gate prior art is from
[SarahR1 (Irisviel)](https://github.com/SarahR1)'s Nexus / Irkalla
project — referenced verbatim in the catalyst review of 2026-04-30.
This ADR's specific framing (Verifier as Guardian-genre agent;
intra-agent at v0.3 with cross-agent deferred; LLM-classification
at high confidence threshold; operator review surface for false-
positive handling) is FSF-specific work shaped by the SarahR1
review's emphasis on memory humility. See `CREDITS.md`.
