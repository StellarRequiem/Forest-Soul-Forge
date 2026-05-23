# Runbook — D9 Learning Coach (ADR-0089)

**Scope.** Operating the D9 Learning Coach domain end-to-end:
birth, skill install, first dispatch, observation, recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands D9 Phase A (this runbook will grow as Phases B–D ship).

**Phase context.** D9 ships in four phases per ADR-0089:

| Phase | New agent(s) | New builtin tool | Status |
|---|---|---|---|
| **A** | mentor + curriculum_designer | curriculum_design.v1 | CLOSED |
| **B** | assessor (YELLOW) | knowledge_assessment.v1 + assessment_score.v1 + misconception_log.v1 | CLOSED |
| **C** | socratic_partner | none — reuses existing | queued |
| **D** | spaced_repetition_pilot (YELLOW) | spaced_repetition_schedule.v1 | queued |

Each phase = one commit + one push, so the operator can verify
phase N before phase N+1 fires.

---

## At a glance

D9's value proposition: **backward design from operator goals**.
"I want to read papers on diffusion models comfortably in 3 months."
The curriculum_designer reverse-engineers the prereqs from D1's
catalog state; the mentor frames + coaches; the assessor measures
understanding (YELLOW posture, operator-gated); the socratic_partner
runs dialogue sessions; the spaced_repetition_pilot schedules
reviews (YELLOW posture, composes with D2). **NEVER gates
progression silently** — every mastery sign-off is operator-
approved.

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `mentor` | researcher | green | `coaching.v1` | Composes operator-facing coaching briefs (framing + encouragement + correction). NEVER gates progression; NEVER mutates the curriculum DAG. |
| `curriculum_designer` | researcher | green | `curriculum_design.v1` | Composes a topic-prereq DAG + deterministic ordered learning path via curriculum_design.v1 from an operator goal + operator-curated catalog. NEVER assesses understanding. |

Both Phase A agents are **operator-birthed via the approval queue**
per ADR-0089 — no auto-birth.

**Why deterministic curriculum?** Curriculum design is a long-lived
artifact the operator consults repeatedly over a multi-week or
multi-month arc. An LLM-generated path is opaque and unrepeatable;
small prompt variations would shuffle the order or drop topics,
which destroys the trust contract with the operator pacing their
study schedule against it. A deterministic topo-walk over an
operator-curated catalog keeps the path replayable + auditable +
diff-able when the catalog changes.

**Why two intake roles, not one?** Coaching and planning are
different governance surfaces. The mentor narrates (read_only;
operator-facing prose); the curriculum_designer plans (read_only;
deterministic DAG composition). Different traits, different
policies; one role would conflate them + risk the planner
hallucinating encouragement that grades the operator.

**Pacific time everywhere.** Per CLAUDE.md, all D9 timestamps are
Pacific time. The skill manifests explicitly tell the LLM to use
Pacific time so coaching briefs + curriculum narratives don't
drift into UTC framing.

---

## Phase A — coaching foundation

### 1. Restart the daemon

The new role definitions land in `trait_tree.yaml` +
`genres.yaml` + `constitution_templates.yaml`; the per-role kits
land in `tool_catalog.yaml`. The daemon loads these at lifespan
boot, so a restart is required before the births can pick them up.

```bash
./dev-tools/force-restart-daemon.command
```

Verify in `/healthz`'s `startup_diagnostics` that the genre engine
reports `status: ok` and that `mentor` + `curriculum_designer`
both appear in `/genres` under the `researcher` genre's `roles`
list.

### 2. Birth the agents

```bash
./dev-tools/birth-mentor.command
./dev-tools/birth-curriculum-designer.command
```

Each script is idempotent — re-running it skips the birth if the
agent already exists. Both set posture GREEN as the default per
ADR-0089 Decision 1 (coaching-narrative composition + deterministic
DAG composition are non-acting).

### 3. First dispatch — compose a curriculum

```bash
DESIGNER_ID=...   # CurriculumDesigner-D9 instance_id
curl -s --max-time 60 -X POST \
  "http://127.0.0.1:7423/agents/${DESIGNER_ID}/tools/call" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "tool_name": "curriculum_design",
  "tool_version": "1",
  "session_id": "d9-bootstrap-$(date +%s)",
  "args": {
    "goal_topic": "diffusion models",
    "catalog": [
      {"slug": "linear-algebra", "title": "Linear algebra",
       "prereq_slugs": []},
      {"slug": "calculus", "title": "Calculus",
       "prereq_slugs": []},
      {"slug": "probability", "title": "Probability",
       "prereq_slugs": ["calculus"]},
      {"slug": "deep-learning", "title": "Deep learning basics",
       "prereq_slugs": ["linear-algebra", "calculus"]},
      {"slug": "diffusion", "title": "Diffusion models",
       "prereq_slugs": ["deep-learning", "probability"]}
    ],
    "expertise_level": "intermediate",
    "target_weeks": 12
  }
}
JSON
```

The response carries `ordered_path` (the deterministic learning
sequence), `dag` (nodes + edges), `has_cycles` + `cycle_members`
(catalog-quality signals), `already_known` (topics the operator's
familiarity ≥ 7), and `orphan_prereqs` (prereq slugs the catalog
references but doesn't define — actionable feedback for the D1
librarian).

### 4. First dispatch — compose a coaching brief

```bash
MENTOR_ID=...     # Mentor-D9 instance_id
curl -s --max-time 60 -X POST \
  "http://127.0.0.1:7423/agents/${MENTOR_ID}/skills/dispatch" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "coaching",
    "skill_version": "1",
    "inputs": {
      "topic_slug": "diffusion",
      "session_focus": "review yesterday\'s notes on the forward process",
      "operator_reason": "weekly check-in"
    }
  }'
```

The response carries `brief_text` (the operator-facing coaching
narrative), `chain_status` (audit chain integrity at compose time),
and `brief_entry_id` (the memory_write attestation row).

### 5. Recovery — what to do when something refuses

| Symptom | Cause | Fix |
|---|---|---|
| `curriculum_design.v1` returns `has_cycles: true` | Catalog has A→B→A | Operator decides which dependency to drop; re-dispatch with corrected catalog. |
| `orphan_prereqs` is non-empty | Catalog references slugs the librarian hasn't cataloged yet | Operator dispatches D1 librarian's `knowledge_curation.v1` for the missing slugs, then re-runs curriculum_design. |
| `coaching.v1` skill halts on chain status != ok | Chain has a tampered or unreachable segment | Inspect via `audit_chain_verify.v1`; if chain is genuinely broken, escalate to the chain-fork recovery runbook. |
| Mentor brief tries to "certify mastery" | LLM hallucinated certification language | The `forbid_progression_gating` policy refuses at governance layer; if it leaks through, file as a content-quality bug and tune the skill's prompt. |

---

## Phase B — assessment + misconception ledger

### 1. Restart the daemon + birth the assessor

```bash
./dev-tools/force-restart-daemon.command
./dev-tools/birth-assessor.command
```

The assessor is `assessor` role / `guardian` genre / **YELLOW**
posture default. Three new builtin tools land in this phase:
`knowledge_assessment.v1` (read_only), `assessment_score.v1`
(read_only), `misconception_log.v1` (filesystem).

### 2. Why misconception_log.v1 is NOT in the assessor's kit

Guardian-genre's `max_side_effects=read_only` ceiling rejects
filesystem-class tools. Same pattern as D1's `knowledge_verifier`
which deliberately omits `memory_flag_contradiction.v1`: the
assessor produces a **misconception_proposal** memory_write entry
(via the `misconception_tracking.v1` skill); the operator picks
it up + dispatches `misconception_log.v1` directly to commit to
the persistent ledger.

This keeps the assessor's surface aligned with its flag-not-
write invariant at the kit-composition layer, not just the
constitution-policy layer.

### 3. First dispatch — score a response

```bash
ASSESSOR_ID=...   # Assessor-D9 instance_id
curl -s --max-time 60 -X POST \
  "http://127.0.0.1:7423/agents/${ASSESSOR_ID}/skills/dispatch" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "knowledge_assessment",
    "skill_version": "1",
    "inputs": {
      "topic_slug": "diffusion-forward",
      "response": "Forward diffusion adds Gaussian noise step by step",
      "ground_truth_answers": [
        "Forward diffusion adds Gaussian noise step by step",
        "noise is added gradually in T discrete steps"
      ],
      "difficulty": "medium",
      "kind": "short_answer"
    }
  }'
```

The response carries `verdict` ∈ {correct, partial, incorrect,
deferred}, `score` (0.0..1.0), `reality_anchor_verdict` (from
verify_claim.v1), and `score_entry_id` (the memory_write
attestation). On `incorrect` or `partial`, follow with
`misconception_tracking.v1`.

### 4. Second dispatch — propose a misconception (incorrect verdict)

```bash
curl -s --max-time 60 -X POST \
  "http://127.0.0.1:7423/agents/${ASSESSOR_ID}/skills/dispatch" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "misconception_tracking",
    "skill_version": "1",
    "inputs": {
      "topic_slug": "diffusion-forward",
      "source_item_id": "item_abc...",
      "claim_summary": "Forward diffusion is reversible",
      "correction": "Forward diffusion is designed to be irreversible; reverse process learns to invert it",
      "severity": "moderate"
    }
  }'
```

The skill writes a `misconception_proposal` memory_write entry +
returns `next_step` describing the operator's commit path.

### 5. Third dispatch — commit the proposal to the ledger (operator)

Operators dispatch `misconception_log.v1` directly (not via the
assessor's skill kit):

```bash
curl -s --max-time 30 -X POST \
  "http://127.0.0.1:7423/tools/dispatch" \
  -H "X-FSF-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "misconception_log",
    "tool_version": "1",
    "args": {
      "topic_slug": "diffusion-forward",
      "claim_summary": "Forward diffusion is reversible",
      "correction": "Forward diffusion is designed to be irreversible; reverse process learns to invert it",
      "severity": "moderate",
      "source_item_id": "item_abc..."
    }
  }'
```

Per `filesystem_always_human_approval`, this dispatch queues for
approval. After confirmation, the entry lands in
`data/d9/misconceptions.jsonl`. The next assessment session reads
recent ledger entries to target the gap.

## Phase C / D — TBD

Sections expand as Phases C, D ship.

### YELLOW posture promotion criteria (Phases B + D)

`assessor` and `spaced_repetition_pilot` default YELLOW per
ADR-0089 Decision 3. Promote to GREEN only after:

1. **Quality bar** — at least two weeks of YELLOW-posture
   operation with operator-approved scores + queue updates
   matching what the operator would have approved manually
   anyway.
2. **Misconception calibration** — review the misconception
   ledger's false-positive rate (operator overrides) and
   tune the rubric before promotion.
3. **Per-call gates remain** — even at GREEN, the per-tool
   `requires_human_approval` on misconception_log.v1 +
   spaced_repetition_schedule.v1 still fires. Posture is
   secondary discipline; per-call is the load-bearing safety.

---

## Cross-references

- ADR-0089 — D9 Learning Coach rollout (this domain)
- ADR-0086 — D1 Knowledge Forge (upstream — catalog source)
- ADR-0087 — D2 Daily Life OS (downstream — spaced repetition
  composes with schedule_reminder.v1)
- ADR-0088 — D7 Content Pipeline (precedent — same four-phase
  rollout shape)
- ADR-0063 — Reality Anchor (Phase B's assessment_score.v1
  composes verify_claim.v1)
- ADR-0068 — Operator profile (expertise_level + areas_of_focus
  drive every D9 dispatch)
- `config/domains/d9_learning_coach.yaml` — domain manifest
