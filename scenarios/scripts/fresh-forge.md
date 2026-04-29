# Presenter script — `fresh-forge`

A 3-to-5-minute hands-on demo focused on **forging an agent from
scratch**. The opposite of `synthetic-incident` — empty registry,
genesis-only chain. Best for "let me show you how to build one of these
things" with an audience that wants to drag the sliders themselves.

> Prep:
> ```
> ./scenarios/load-scenario.command fresh-forge
> ./start.command
> ```
> The browser opens to the Forge tab. Welcome banner shows on first
> visit — leave it visible for an evaluator-led demo (the explainer
> text is the script for them).

---

## 0:00 — The pitch (30 seconds)

> "We're going to build an agent in under two minutes. Every slider you
> drag changes its DNA. Every choice we make goes into a hash-chained
> log. Watch the right side of the screen — that's the live preview."

---

## 0:30 — Pick the genre (30 seconds)

**Click:** the `genre` dropdown at the top of PROFILE.

> "Ten genres. Each one carries a personality — what kinds of risks
> it can take, what kind of memory it has, who it can spawn into. Let's
> pick `investigator`. It can reach the network, it remembers up to
> the lineage scope, it's structurally cautious."

**Select:** `investigator`. The role dropdown narrows to investigator-
compatible roles.

---

## 1:00 — Pick the role (15 seconds)

**Select role:** `anomaly_investigator`.

> "Now it knows what kind of work it does. Sliders below adjust how
> aggressively it does it."

---

## 1:15 — Drag a slider (1 minute)

**Drag** `caution` from 70 → 95. **Drag** `evidence_demand` from 85 → 90.

> "Look at the preview. The DNA changed — different content, different
> hash. The constitution hash changed too. The radar chart shifts as
> the trait emphasis moves toward audit and security domains."

**Point at the warnings panel** if any flagged combinations appear.

> "If two sliders combine into something the operator should know
> about — like high suspicion + low evidence_demand — the engine
> flags it before birth. You can override, but the override itself
> becomes an audit event."

---

## 2:15 — Birth (45 seconds)

**Type** an agent name in the Identity panel: `Sentinel-01`.

**Click:** `Birth`.

**Watch:**
- A toast appears: "Birthed Sentinel-01"
- The Agents tab gets a new entry
- The Audit tab gets a new `agent_created` event

> "That's it. The agent now exists. The soul.md narrative is rendered.
> The constitution.yaml is on disk. The audit chain has its hash. From
> here you'd assign skills, watch it work in the runtime, hand it
> incidents to investigate."

---

## 3:00 — The closer (1 minute)

**Click** `Audit` tab.

> "There's the entire history of what we just did. One event because
> we only birthed one agent. If we kept going — spawn a child, dispatch
> a tool, run a skill — every step lands here. The chain is the source
> of truth; the SQLite registry is just an indexed view rebuildable
> from the chain."

**Click** the agent in the Agents tab.

> "And here's the agent's full identity card. Soul, constitution,
> lineage. Click Archive to take it out of active rotation — that
> also lands in the chain."

---

## Things to highlight if asked

- **"What's the DNA actually?"** SHA-256 of the canonical trait
  profile (sorted, stripped, JSON-serialized). Same input always
  produces same output — try birthing the same profile twice, you
  get the same agent.
- **"What's the constitution actually?"** Three layers: role base
  template, trait modifiers (rule-driven), flagged combinations
  (operator-flagged combos become forbid policies). All composed
  deterministically and hashed. Two agents differing only in genre
  have different hashes — the genre's risk profile is in the hash.
- **"Can I edit the soul.md by hand?"** You can read it anytime. If
  you edit it, the agent's identity is no longer reproducible from
  the inputs — explicit divergence. The chain still records the
  original.

## Things to skip in this scenario

- **Skills tab** — it's empty in fresh-forge by default (no skills
  installed). The synthetic-incident scenario is the right one for
  showing skills.
- **Memory tab** — same; nothing yet to display.
- **Approvals tab** — empty until the agent dispatches a gated tool.

If the audience wants to see skills + memory + the cross-agent chain,
that's the cue to load the synthetic-incident scenario:
`./scenarios/load-scenario.command synthetic-incident`.
