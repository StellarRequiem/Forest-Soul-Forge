# Runbook — D7 Content Pipeline (ADR-0088)

**Scope.** Operating the D7 Content Studio domain end-to-end:
birth, skill install, first dispatch, observation, recovery.

**Audience.** Operator on a running daemon at HEAD ≥ the commit
that lands D7 Phase A (this runbook will grow as Phases B–D ship).

**Phase context.** D7 ships in four phases per ADR-0088:

| Phase | New agent(s) | New builtin tool | Status |
|---|---|---|---|
| **A** | writer + content_researcher | none — reuses existing | IN PROGRESS |
| **B** | style_steward (GREEN) | voice_profile_build.v1 + voice_match_check.v1 | PENDING |
| **C** | editor (GREEN) | format_adapt.v1 | PENDING |
| **D** | distribution_pilot (YELLOW) | publish_schedule.v1 | PENDING |

Each phase = one commit + one push, so the operator can verify
phase N before phase N+1 fires.

---

## At a glance

D7's value proposition: **end-to-end content pipeline** — idea →
researched → drafted → edited → fact-checked → ready-to-publish.
**NEVER auto-publishes** — distribution is always operator-gated.

| Role | Genre | Posture | Skill | What it does |
|---|---|---|---|---|
| `writer` | researcher | green | `draft_writing.v1` | Composes long-form drafts (blog / newsletter / technical article) from research briefs + outlines. NEVER publishes; NEVER adapts to non-primary formats. |
| `content_researcher` | researcher | green | `content_research.v1` | Pulls source material via web_fetch + lineage memory (D1 catalog), produces structured research briefs the writer + editor + style_steward consume. NEVER drafts final articles. |

Both Phase A agents are **operator-birthed via the approval queue**
per ADR-0088 — no auto-birth.

**Why the disambiguation rename?** The domain manifest's bare
`researcher` collides with the researcher *genre* name. Renaming
to `content_researcher` matches the D1 precedent (manifest's
bare `verifier` → `knowledge_verifier` to avoid collision with
`verifier_loop` + `reality_anchor`) and keeps the role list
unambiguous at trait-engine + genre-loader time.

**Why two intake roles, not one?** Sourcing and composition are
different governance surfaces. The content_researcher pulls
material from external sources (network ceiling; allowlist-gated);
the writer composes long-form prose (read_only ceiling; no
external fetches). Different traits, different policies; one
role would conflate them + raise the drafting role's network
blast radius unnecessarily. Same pattern as D1's
prospector / librarian split.

**Pacific time everywhere.** Per CLAUDE.md, all D7 timestamps
are Pacific time. The skill manifests explicitly tell the LLM
to use Pacific time so drafts don't drift into UTC framing.

---

## Phase A — drafting foundation

### 1. Restart the daemon

The new role definitions land in `trait_tree.yaml` +
`genres.yaml` + `constitution_templates.yaml`; the per-role kits
land in `tool_catalog.yaml`. The daemon loads these at lifespan
boot, so a restart is required before the births can pick them
up.

```bash
./dev-tools/force-restart-daemon.command
```

Verify in `/healthz`'s `startup_diagnostics` that the genre
engine reports `status: ok` and that `writer` + `content_researcher`
both appear in `/genres` under the `researcher` genre's `roles`
list.

### 2. Birth the agents

```bash
./dev-tools/birth-writer.command
./dev-tools/birth-content-researcher.command
```

Each script is idempotent — re-running it skips the birth if
the agent already exists. Both set posture GREEN as the default
per ADR-0088 Decision 1 (drafts-to-private-memory + read-from-
allowlisted-network are non-acting).

The `content_researcher` birth script also patches the agent's
constitution with a default web_fetch allowed_hosts list
(arxiv, github, wikipedia, RFC editor) so the first content_research
dispatch works out of the box; widen via the per-(agent, tool)
grant surface as the operator's source-allowlist grows.

### 3. First dispatch — research a topic

```bash
RESEARCHER_ID=...   # ContentResearcher-D7 instance_id
curl -s --max-time 60 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${RESEARCHER_ID}/skills/run" \
  -H "X-FSF-Token: $FSF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "content_research",
    "skill_version": "1",
    "tool_version": "1",
    "session_id": "research-trial-001",
    "args": {
      "topic_slug": "multi-agent-governance",
      "source_url": "https://arxiv.org/abs/...",
      "operator_reason": "Sourcing for a blog post on this week."
    }
  }'
```

The brief lands in private memory tagged
`content_research:multi-agent-governance` and is now ready for
the writer's `draft_writing` skill to pick up.

### 4. Second dispatch — compose a draft

```bash
WRITER_ID=...   # Writer-D7 instance_id
curl -s --max-time 120 -X POST \
  "http://127.0.0.1:7423/api/v1/agents/${WRITER_ID}/skills/run" \
  -H "X-FSF-Token: $FSF_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "draft_writing",
    "skill_version": "1",
    "tool_version": "1",
    "session_id": "draft-trial-001",
    "args": {
      "topic_slug": "multi-agent-governance",
      "outline": "1. Why governance matters\n2. Posture model\n3. Audit chain spine",
      "format": "blog_post",
      "target_word_count": 1200,
      "operator_reason": "Drafting from research brief."
    }
  }'
```

The draft lands in private memory tagged
`draft:topic:multi-agent-governance` for downstream Phase B+C
editor / style_steward review.

### 5. Recovery

- **Birth fails with role-not-found** → daemon didn't reload
  after trait_tree edit. Re-run `force-restart-daemon.command`
  and check `/healthz`.
- **content_research returns 0 source body** → check the
  agent's `allowed_hosts` constraint patch; the source URL's
  host must be in the allowlist OR the operator must grant via
  the per-(agent, tool) grant surface.
- **draft_writing refuses with "no research brief found"** →
  the writer requires a prior `content_research:${topic_slug}`
  brief in lineage memory. Run the researcher first OR pass a
  different topic_slug that has briefs.
- **Chain integrity halt** → both skills refuse on
  `chain_status != "ok"`. Investigate via
  `audit_chain_verify` directly + check
  `examples/audit_chain.jsonl` for the broken segment before
  retrying.

---

## Phase B — voice profiling (PENDING)

Will document style_steward birth + voice_profile_build /
voice_match_check dispatch + the operator voice-sample curation
workflow.

## Phase C — editing + format adaptation (PENDING)

Will document editor birth + the editing skill (composes
verify_claim + voice_match_check + the source-claim fact-check
loop) + the format_adapt dispatch (one draft → twitter_thread /
linkedin / newsletter / blog variants).

## Phase D — distribution + cascade + umbrella (PENDING)

Will document distribution_pilot birth (YELLOW posture by
design — every queued publish gates on operator approval), the
publish_schedule dispatch, and the cascade wiring
(d1.knowledge_curation → d7.content_drafting active;
d2.daily_reflection → d7.content_seed active).
