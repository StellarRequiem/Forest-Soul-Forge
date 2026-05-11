# Procedural-Shortcut Dispatch — operator safety guide

**Reference ADR:** [ADR-0054](../decisions/ADR-0054-procedural-shortcut-dispatch.md).
**Status:** substrate complete (T1–T5b shipped); operator surface is this document plus, when wired, a Chat-tab review card. **DEFAULT OFF.**

## What this is

The procedural-shortcut substrate teaches the daemon to replace a slow `llm_think.v1` reasoning step with a fast lookup of a *previously-validated outcome* for a sufficiently-similar situation. Concretely: when an agent has done the same kind of thinking before and the result was reinforced (operator thumbs-up, downstream success), the daemon can short-circuit the next equivalent request and dispatch the stored answer.

Think of it as muscle memory for the agent: the first time you tie your shoes, you reason about loops; the thousandth time, you just do it.

## Why this is opt-in

Three failure modes make this dangerous if enabled blindly:

1. **False matches.** Cosine similarity over `nomic-embed-text` embeddings is a heuristic. A request that *looks* like a prior reasoning task in vector space but differs in a load-bearing detail (different agent, different inputs, different goal) will get the wrong cached answer. Operator-facing assistants are the worst case — a user asks "should I send this email?" and gets the answer to a *different* "should I send this email?".

2. **Stale reinforcement.** A shortcut that was correct three weeks ago may be wrong today (model changed, environment changed, threat model changed). The reinforcement counter ages but doesn't expire; operators who don't review the table will accumulate drift.

3. **Out-of-band emergent behavior.** Once enabled, the substrate participates in *every* `llm_think.v1` dispatch. A bug in `ProceduralShortcutStep` (the pipeline gate) can silently change the runtime behavior of *every agent in the system at once*. The default-off posture means operators turn this on *after* reading this guide and explicitly accept the failure modes.

## Master switch

The substrate is gated by a single config flag:

```
FSF_PROCEDURAL_SHORTCUT_ENABLED=false  # default
```

Set this in `.env` (or as an OS env var) and restart the daemon. With it off, the `ProceduralShortcutStep` short-circuits to "no match" on every call — no embedding lookups, no audit emissions, no behavioral change vs. pre-ADR-0054 dispatch.

## Sub-knobs

If you enable the master switch, three more settings shape behavior:

| Setting | Default | Meaning |
|---|---|---|
| `FSF_PROCEDURAL_MATCH_THRESHOLD` | `0.93` | Cosine-similarity floor for a shortcut to fire. **0.93 is conservative.** Lowering to 0.85 nearly doubles match rate but increases false-match risk. For high-trust operator assistants where false matches are catastrophic (security review, irreversible actions), consider 0.96+. |
| `FSF_PROCEDURAL_REINFORCEMENT_FLOOR` | `2` | Net-positive reinforcements required for a row to participate in matches. Default 2 means an outcome must be validated twice before becoming a shortcut candidate. Raise to 3+ for operator assistants. |
| `FSF_PROCEDURAL_AUDIT_VERBOSE` | `false` | When true, every shortcut-considered call emits a chain event (not just shortcut-fired). Useful for debugging the gate; very noisy in steady state. |

## What lands in the audit chain

When a shortcut fires, the daemon emits:

```
tool_call_shortcut
  agent_instance: ...
  situation_digest: sha256:...  # the embedded request
  shortcut_id: ...               # row from memory_procedural_shortcuts
  matched_similarity: 0.97       # the actual cosine
  reinforcement_count: 5         # how reinforced this row is
  substituted_for_tool: llm_think.v1
```

This is in *addition* to the substituted action's normal success event. The audit chain records both "we considered a shortcut for this request" and "we ran the substituted action and it produced this output," with the shortcut row's identity in the first event so an auditor can trace every short-circuit decision back to the row that drove it.

## Reinforcement: how rows get strong

Two tools shape the shortcut table at runtime:

- **`memory_tag_outcome.v1`** — operator-driven; called by the Chat-tab thumbs surface when a user thumbs-up's an agent response. Increments the matching row's `reinforcement_count` by +1.
- **`memory_forget_shortcut.v1`** — operator-driven; called by the Chat-tab review card when a user thumbs-down's a response or explicitly deletes a row. Soft-deletes the row (sets `forgotten_at`) so it stops participating in matches but remains in the audit trail.

A thumbs-down does NOT just decrement the counter — it forgets the row entirely. Rationale: a row that produced a wrong answer once is suspect; let the operator's positive reinforcement on the *correct* response build a different row up.

## Inspecting the table

Until the Chat-tab review card lands (T6 second half), inspect the table directly:

```bash
sqlite3 data/registry.sqlite \
  "SELECT id, agent_instance, situation_text, reinforcement_count,
   matched_count, last_used_at FROM memory_procedural_shortcuts
   WHERE forgotten_at IS NULL ORDER BY matched_count DESC LIMIT 20"
```

Look for:

- **Rows with `matched_count` > 100 but no recent `last_used_at`** — drift candidates. The pattern was hot but stopped firing, suggesting the agent's prompts shifted.
- **Rows with `reinforcement_count` close to `forgotten_count`** — contentious. Operator is split on whether the cached answer is correct.
- **Rows from agents you no longer trust at this confidence level** — for example, an agent that was operator_companion but is now archived. Its shortcuts should be cleared.

## When to disable

Turn the master switch back off (`FSF_PROCEDURAL_SHORTCUT_ENABLED=false` + daemon restart) if:

- You're investigating an agent behavior change and want to rule out cached answers.
- You're updating the LLM model. Embeddings shift with model swaps; stale rows will mismatch on the new model.
- You're running a compliance audit where every action must trace to a fresh `llm_think.v1` call (no cache-hit shortcuts).
- The daemon is showing unexplained behavioral drift and you want to bisect.

Disabling is reversible. The table stays intact; subsequent re-enables pick up where they left off.

## Failure-mode escalation

If a shortcut fires that's clearly wrong:

1. **Stop the master switch immediately** (operator override; this is what the kill switch is for).
2. **Find the row in the chain** via the `tool_call_shortcut` event's `shortcut_id`.
3. **Forget the row** via `memory_forget_shortcut.v1` or direct DB update.
4. **Audit the rows that share an `agent_instance` or close `situation_digest`** — one bad row often signals a class of bad rows.
5. **File an audit doc** under `docs/audits/` recording the false-match incident with the row hash, the matched_similarity, and what the correct answer should have been.

## What's still queued

- **Chat-tab review card** (T6 second half) — a table widget on the Chat tab showing the shortcut rows with per-row delete buttons. Until shipped, use the sqlite3 query above.
- **Per-agent threshold overrides** — currently the threshold is global. A future tranche may allow per-agent overrides so a `vault_warden` shortcut needs 0.98 while a `dashboard_watcher` shortcut is fine at 0.90.
- **Row aging** — currently rows live forever. A future tranche may auto-forget rows older than N days unmatched.

## TL;DR

Default off. Read the failure modes. Enable per-environment (probably not on operator assistants without the chat-tab review surface). Monitor the chain for `tool_call_shortcut` events. Forget rows that produce wrong answers; the chain remembers what they did.
