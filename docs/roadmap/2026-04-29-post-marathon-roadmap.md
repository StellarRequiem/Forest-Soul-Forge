# 2026-04-29 — Post-marathon roadmap

Filed after the G-track + K-track marathon (see
[`docs/audits/2026-04-29-grtrack-ktrack-marathon.md`](../audits/2026-04-29-grtrack-ktrack-marathon.md))
to capture the agreed sequence of work going forward. Items are ordered
by **what enables the next thing**, not just feature size — some items
unblock or de-risk others, and a few are cheap-but-important cleanups
that should land before bigger work piles on top.

This is a living document. If priority shifts, edit it; if items are
added or dropped, edit it. The point is that anyone (including future-
self in two weeks) can open this and see what we agreed.

---

## Tier 0 — Foundation cleanup (~30 minutes)

These need to land before anything else; the next big chunks build on
either an unpushed branch or a known-broken arg-passing path.

| #   | Item                                                    | Notes                                                                                                       |
|-----|---------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| 0.1 | Push the 2 unpushed commits                             | K6 live-test + audit doc; ADR-003Y + verify-end-to-end harness. Origin/main should reflect actual state.    |
| 0.2 | Fix `skill_manifest` dict/list-arg stringification bug  | Memory flagged it. Blocks `delegate.v1` cross-skill chains AND will trip Y3+ when agents pass structured args between turns. |

## Tier 1 — Foundation visibility (~1-2 days)

Before adding the conversation runtime on top of today's stack, see
what the stack actually looks like.

| #   | Item                                              | Why now                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
|-----|---------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1.1 | Heavy/light codebase audit                        | We stacked 6 K-track features on top of the existing dispatcher in one session. Each was small individually; collectively they shifted the load. Need to know where the dispatcher is shimmed vs. load-bearing before adding the conversation runtime, which puts an entirely new workload class through it. **Output:** `docs/audits/<date>-load-bearing-survey.md` + 3-5 specific refactor recommendations + classification of every module by load category. |

## Tier 2 — Hardening (~1 week)

Three items that each close a real failure mode the codebase currently
has. Doing these BEFORE the conversation runtime means the runtime
inherits the hardening, not retrofits it.

| #   | Item                                       | Why now                                                                                                                                                                                                                                                                                                                                                | Effort |
|-----|--------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------|
| 2.1 | Governance-relaxed audit event + TTL      | Direct from Discord cross-check (Kazara: "everyone ends up with YOLO mode toggled on"). Today operator constraint-relaxations blend into normal events. Conversation runtime multiplies the surface where operators could relax constraints (allow_out_of_lineage on cross-domain bridges, etc.) — better to have visible governance events first.    | ~1 day |
| 2.2 | Per-model trait floors                    | Ryan Fav's "qwen3.6 is too eager → VM" wisdom. Conversation mode multiplies provider calls dramatically — codifying per-model posture overrides BEFORE Y means each turn already routes through the right safety floor for whichever model is active.                                                                                                  | ~2 days |
| 2.3 | C8 — open-web synthetic-incident demo     | Closes ADR-003X. Mirror of Phase E1 for the open-web plane. Real proof-of-concept (web_researcher fetches RFC → web_actuator opens a Linear ticket via mcp_call). Without this the entire G-track is "shipped but undemonstrated."                                                                                                                     | ~3 days |

**Parallelism note:** 2.1 and 2.2 could run in parallel with the Tier 1
audit if convenient — they're small, independent, and don't conflict
with the audit's investigation.

## Tier 3 — Major unlock (~2-3 weeks)

The conversation runtime. Y1-Y7 as scoped in
[`ADR-003Y`](../decisions/ADR-003Y-conversation-runtime.md).

| #   | Phase | Deliverable                                                                                       |
|-----|-------|---------------------------------------------------------------------------------------------------|
| 3.1 | Y1    | Schema v10 + Conversation/Turn dataclasses + read-only `/conversations` router                    |
| 3.2 | Y2    | Single-agent conversation: addressed-only turns, retention-window bodies                          |
| 3.3 | Y3    | Multi-agent rooms within one domain: addressing + @mention pass + suggest_agent fallback          |
| 3.4 | Y4    | Cross-domain bridge endpoint + audit trail                                                        |
| 3.5 | Y5    | Ambient mode: opt-in flag + rate slider + quota enforcement                                       |
| 3.6 | Y6    | Frontend Chat tab + localStorage restart-stickiness                                               |
| 3.7 | Y7    | Lazy summarization background task + retention policy endpoint                                    |

## Tier 4 — Companion polish (~1 week)

Only meaningful AFTER conversation runtime exists. These complete the
Companion experience.

| #   | Item                                                                                          | Effort |
|-----|-----------------------------------------------------------------------------------------------|--------|
| 4.1 | Attachment health checks — weekly Companion ceremony surfacing usage patterns                | ~1 day |
| 4.2 | Defensive linguistic priming per genre (Ryan Fav's "abusing english" trick codified)         | ~1 day |
| 4.3 | Growth reflection loops — scheduled task summarizing "what changed about this agent in N days" | ~1 day |

## Tier 5 — Strategic / horizon (multi-week, mostly own ADRs)

Bigger commitments. Each deserves its own ADR before code.

| #   | Item                                                          | Notes                                                                                                                                                                              |
|-----|---------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 5.1 | Agent-initiated Tool Forge (`forge_tool.v1` agent-callable)   | ADR + ~3 days impl                                                                                                                                                                 |
| 5.2 | I — Catalog expansion (~30 new role types from Phase I seed)  | Mostly trait_tree.yaml + archetype kits + tests; ~1 week                                                                                                                            |
| 5.3 | Inter-realm handshake protocol                                | Identity + federation territory; own ADR; 1-2 weeks impl                                                                                                                            |
| 5.4 | Life-event schemas                                            | Own ADR; touches realm scope + consent contract; ~1 week impl                                                                                                                       |
| 5.5 | C5 — Sigstore-style provenance for MCP servers                | Follow-up to ADR-003X C4; defer until typosquat-via-pinned-binary becomes a real concern                                                                                            |
| 5.6 | J — Installer polish (.dmg, codesign, bundled venv)           | Operator-experience polish; valuable when going wider than current operator                                                                                                         |
| 5.7 | H — Working agents loop ADR                                   | Vague placeholder in TodoList; probably should be folded into Y (conversation runtime IS a working agents loop) — propose dropping when we get there                              |

---

## Suggested timeline

```
Today / next session:
  T0.1 push commits
  T0.2 fix skill_manifest bug

Day 1-2:
  T1.1 heavy/light audit         ────┐
                                     ├─ in parallel
  T2.1 governance-relaxed audit   ───┘

Day 3-5:
  T2.2 per-model trait floors
  T2.3 C8 open-web demo

Week 2-3:
  Y1 → Y2 → Y3 → Y4 → Y5 → Y6 → Y7

Week 4:
  T4.1 → T4.2 → T4.3

Beyond:
  Tier 5 — pick by then-current need
```

## How this doc works going forward

- **As items complete**, mark them `[done]` inline and move the line
  under a new "Completed" section at the bottom (don't delete; the
  sequence is itself a record).
- **As priority shifts**, re-edit. The order is descriptive of what
  we agreed at filing time, not prescriptive. Re-deciding mid-stream
  is fine; just update the doc so the next-session view is accurate.
- **As new items emerge**, add them to whichever tier fits. New tiers
  can be added; we're not forced into 0-5.

## What this doc deliberately doesn't do

- It doesn't capture *all* possible work. ADRs 0001-0033 still have
  open items not surfaced here; they're not on the next-quarter
  critical path so they didn't make it.
- It doesn't replace the per-ADR phase tables. Y1-Y7 details live in
  ADR-003Y. C5-C8 details live in ADR-003X. This doc is the *order*,
  not the *specifications*.
- It doesn't bind the timeline. "~1 day" estimates are calibrated
  against today's pace and could be off by 2x in either direction
  depending on what surprises lurk in the audit.
