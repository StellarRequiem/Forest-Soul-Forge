# Runbook — D10 Multi-Agent Research Lab (ADR-0090)

**Scope.** Operating the D10 Research Lab domain end-to-end:
birth, skill install, first dispatch, observation, recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands D10 Phase A (this runbook will grow as Phases B–D ship).

**Phase context.** D10 ships in four phases per ADR-0090:

| Phase | New agent(s) | New builtin tool | Status |
|---|---|---|---|
| **A** | gatherer + analyst | none — reuses existing | IN FLIGHT |
| **B** | critic + lab_synthesizer | citation_graph_build.v1 + confidence_score.v1 | pending |
| **C** | debate_moderator | claim_provenance.v1 + debate_orchestrate.v1 | pending |
| **D** | (cascade + umbrella + live) | none | pending |

Each phase = one commit + one push, so the operator can verify
phase N before phase N+1 fires.

---

## At a glance

D10's value proposition: **structured multi-agent debate on any
topic with citation-graph reasoning + per-conclusion confidence
scores + dissenting arguments preserved**. The lab gathers
sources, decomposes claims, critiques adversarially, runs
hypotheses (via experimenter, ADR-0056 shipped), and synthesizes
auditable reports with operator-readable citation graphs.

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `gatherer` | researcher | green | `source_gathering.v1` | Pulls source material via allowlisted web_fetch + D1 catalog reads; composes structured source-bundle attestations. NEVER decomposes claims; NEVER synthesizes. |
| `analyst` | researcher | green | `deep_analysis.v1` | Consumes gatherer bundles; composes per-claim decompositions (claim → source spans → verify_claim verdict). NEVER critiques; NEVER synthesizes. |

Both Phase A agents are **operator-birthed via the approval queue**
per ADR-0090 — no auto-birth.

**Why separate gatherer + analyst?** Sourcing and analysis are
different governance surfaces. The gatherer's load-bearing tool
is `web_fetch.v1` (crosses the allowlist boundary). The analyst's
load-bearing tool is `verify_claim.v1` (crosses the Reality Anchor
boundary). Both researcher-genre, both GREEN posture, but
different policy stacks: the gatherer is bound by
`forbid_silent_source_substitution`; the analyst is bound by
`require_verify_claim_per_decomposition`. Combining them would
conflate sourcing with reasoning + lose the per-claim Reality
Anchor cross-check.

**Pacific time everywhere.** Per CLAUDE.md, all D10 timestamps
are Pacific time. The skill manifests explicitly tell the LLM to
use Pacific time. The audit chain itself records UTC for ordering
but every operator-facing surface (bundle attestations,
decompositions, eventual synthesis reports) normalizes to Pacific.

---

## Phase A — birth + first dispatch

### Birth

```bash
./dev-tools/birth-gatherer.command
./dev-tools/birth-analyst.command
```

Each script:
1. Kickstarts the daemon (loads the new role).
2. Checks for an existing agent (by name).
3. POSTs `/birth` with the role + agent_name; the constitution
   templates + tool catalog kits are resolved at birth time.
4. Sets posture to GREEN.

Birth payload uses an idempotency key per agent
(`birth-gatherer-d10`, `birth-analyst-d10`) — re-running the
script is safe; the second run finds the existing agent and
skips birth.

### First dispatch — gatherer

```bash
curl -s --max-time 120 \
  http://localhost:7423/api/v1/agents/${GATHERER_ID}/tools/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "source_gathering",
    "tool_version": "1",
    "session_id": "d10-first-bundle",
    "args": {
      "topic_slug": "adr-0090-pilot-topic",
      "research_question": "What does the D10 lab actually look like at Phase A?",
      "source_url": "",
      "operator_reason": "smoke test of source_gathering.v1"
    }
  }' | python3 -m json.tool
```

Expected: a `bundle_text` field containing the structured bundle
+ a `bundle_entry_id` pointing at the memory attestation. The
attestation is tagged `source_bundle:adr-0090-pilot-topic` so the
analyst's downstream `memory_recall` finds it.

### First dispatch — analyst

```bash
curl -s --max-time 120 \
  http://localhost:7423/api/v1/agents/${ANALYST_ID}/tools/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "deep_analysis",
    "tool_version": "1",
    "session_id": "d10-first-decomposition",
    "args": {
      "topic_slug": "adr-0090-pilot-topic",
      "primary_claim": "The D10 lab can render decompositions at Phase A.",
      "operator_reason": "smoke test of deep_analysis.v1"
    }
  }' | python3 -m json.tool
```

Expected: a `decomposition_text` field + a
`primary_claim_verdict` (CONFIRMED / REFUTED / INCONCLUSIVE) +
a `decomposition_entry_id`. The Reality Anchor verdict is the
authoritative cross-check; the LLM-rendered prose is the
operator-facing narrative.

---

## Observation

After each dispatch:

```bash
tail -5 examples/audit_chain.jsonl | python3 -m json.tool
```

Look for `tool_executed` events with `tool_name` ∈
{`source_gathering`, `deep_analysis`}. Each event's `outputs_hash`
is the SHA-256 of the JSON-canonical output; the attestation's
`entry_id` is its memory-store key.

For bundle / decomposition discovery:

```bash
sqlite3 data/registry.sqlite \
  "SELECT entry_id, tags FROM memory_entries WHERE tags LIKE '%d10_source_bundle%' OR tags LIKE '%d10_decomposition%' ORDER BY ts DESC LIMIT 10;"
```

---

## Recovery

| Symptom | Cause | Fix |
|---|---|---|
| Birth returns 409 | Agent already exists | Use the existing `instance_id`; the script handles this idempotently. |
| `verify_claim` returns INCONCLUSIVE | Corpus lacks ground-truth | Operator action: extend `data/anchor_corpus/` with the relevant claim attestations. INCONCLUSIVE is not a failure — it's a signal the corpus needs work. |
| `web_fetch` rejects URL | URL outside operator allowlist | Operator action: extend `data/operator/allowlist.yaml` with the host. Re-dispatch; `forbid_silent_source_substitution` policy ensures the gatherer respects the allowlist. |
| Skill dispatch fails with "skill not found" | Skill manifest not installed | Re-run birth or use `POST /api/v1/skills/install` with the path to the manifest. |
| Chain integrity halts dispatch | `audit_chain_verify` returned ≠ "ok" | Investigate the chain via `dev-tools/check-drift.sh`. Do NOT bypass — both skills' policies require chain integrity before publishing attestations. |

---

## Phase B preview (not yet shipped)

Phase B will add:

- **critic** (guardian, GREEN) — adversarial counter-argument
  role. Reads analyst decompositions + composes per-claim
  counter-evidence. The two-role split (analyst + critic) is the
  load-bearing governance separation; neither can short-circuit
  the other's lane.
- **lab_synthesizer** (researcher, GREEN) — aggregates across
  decompositions + critiques into a final report with citation
  graph + per-conclusion confidence band. Renamed from manifest's
  bare "synthesizer" to avoid collision with D1's synthesizer
  (same disambiguation pattern as D1's `knowledge_verifier` vs
  `verifier_loop`).
- Two new builtin tools:
  - `citation_graph_build.v1` — directed graph (nodes=claims,
    edges=claim→source); read-only.
  - `confidence_score.v1` — per-claim aggregation: source count +
    verify_claim verdicts + critic-counter density → calibrated
    band (low / medium / high); read-only.
