# Presenter script — `synthetic-incident`

A 5-to-15-minute live demo. Scales by depth — at the lower bound you
hit the headline beats; at the upper bound you can drill into any link
in the chain or any tab. The same scenario serves both modes.

> Prep: load the scenario and start the stack ahead of time.
> ```
> ./scenarios/load-scenario.command synthetic-incident
> ./start.command
> ```
> The browser opens to the Forge tab with the welcome banner. Dismiss
> it before the audience walks in unless you want them to see the
> "what is this?" framing inline.

---

## 0:00 — The hook (30 seconds)

> "This is Forest Soul Forge. Local-first agent foundry. Every agent
> has a cryptographic identity, a constitutional rulebook compiled
> from sliders you set yourself, and a tamper-evident log of every
> action it takes. Nothing leaves the machine. Watch."

**On screen:** Forge tab. Sliders visible. Top-right shows the neutral
"local · offline (LLM features off)" indicator — point this out only
if asked: "we're not running an LLM in this demo, but the same flows
work offline."

---

## 0:30 — Click `Agents` (2 minutes)

> "199 agents in this registry. Each one is content-addressed — same
> sliders always produce the same DNA. Click any one."

**Click:** `NetworkWatcher` (5937afd40a51) — middle of the list.

**Detail panel shows:**
- INSTANCE: `0104253c-f76a-59d3-9dbb-3c39990ea896` (the registry's UUID)
- DNA short / full
- ROLE: `network_watcher`
- Soul + constitution paths
- CONSTITUTION HASH (the long hex)

> "The constitution is built from three layers — the role's base policy,
> trait-driven modifiers (caution ≥ 80 → require approval on writes),
> and any operator-flagged trait combos. The hash is over all of it.
> Two agents with identical sliders but different *genres* have
> different constitution hashes — by design."

**Optional drill:** click `OperatorCompanion` to show how the role
matters. Same sliders, different role → different DNA, different
hash.

---

## 2:30 — Click `Skills` (1 minute)

> "21 skill manifests installed. Skills are YAML — they orchestrate
> tool calls into reusable procedures. Look at this one."

**Scroll to:** `morning_sweep.v1` (or any chain-skill at the top).

> "This is what the LogLurker agent runs every morning. Five steps:
> capture timestamp window, scan logs for canaries, write findings to
> lineage memory, then *delegate* to AnomalyAce if any matches. The
> delegate call is itself a tool — `delegate.v1` — so the cross-agent
> handoff lands in the audit chain alongside everything else."

---

## 3:30 — Click `Audit` (3 minutes — **this is the headline**)

> "This is the canonical hash-chained log. Append-only. Every state
> change. Look at the most recent events."

**On screen:** chain tail starting at #499 going backwards. Point at
the `tool_call_succeeded` rows with instance_ids visible.

```
#499  skill_completed                executed_steps:4, outcome:succeeded
#498  skill_step_completed           skill_invoked_seq:453, step_id:escalate
#497  tool_call_succeeded            instance_id:log_lurker_98c6841d68e5
#496  skill_completed                outcome:succeeded
#495  skill_step_completed           step_id:contain
#494  tool_call_succeeded            instance_id:anomaly_ace_fea769557fef
#493  skill_completed
#492  skill_step_completed           step_id:key_audit_handoff
#491  tool_call_succeeded            instance_id:response_rogue_f3adec6a5
#490  skill_completed
#489  skill_step_completed           step_id:inventory
#488  tool_call_succeeded            instance_id:vault_warden_abb276339f2
```

> "Read this from bottom up. VaultWarden runs a key inventory — the
> last hop. Above it, ResponseRogue completed its containment step.
> Above that, AnomalyAce wrapped its investigation. At the top,
> LogLurker closed the morning sweep that kicked all this off. Four
> agents, three cross-tier delegations, twelve tool calls,
> forty-seven audit events — the entire chain visible right here.
> Nothing happened off-the-record."

**Optional drill:** scroll to seq #453 and walk forward. "Here's
where it started — `morning_sweep` invoked. By #468 the first
delegation. The whole story plays out in the chain itself."

---

## 6:30 — Click `Tools` (1 minute)

> "31 tools registered. Every tool declares its side effects:
> `read_only`, `network`, `filesystem`, `external`. The runtime gates
> on this. Companion-genre agents can't fire network tools. Security
> high-tier agents need explicit operator approval per call for anything
> beyond read_only."

**Point to:** the `external` pill on `dynamic_policy.v1` and the
`filesystem` pill on `canary_token.v1`.

> "If a tool author marks something `read_only` to bypass the queue,
> that's a safety bug — the constraint resolver enforces this at
> dispatch."

---

## 7:30 — Click `Memory` (1 minute, optional)

> "Per-agent memory store with four privacy scopes — private, lineage,
> consented, realm. Lineage means parent + descendants can read.
> Cross-agent disclosure is summary-only — the recipient gets a hash
> + summary, never the original content."

**On screen:** empty state — "No entries visible in mode='private'".

> "Empty here because we haven't picked an agent. The point is the
> tab exists and the privacy contract is enforced at the engine level,
> not bolted on."

---

## 8:30 — Click back to `Forge` (the closer, 1-2 minutes)

> "And the way you get an agent like LogLurker is right here. Drag
> sliders. Pick a role. Pick a genre. Click Birth. The DNA, the
> constitution, the soul.md narrative — all generated from your input,
> all hashed, all logged."

**Drag:** `caution` slider down 20 points.

**Watch:** preview panel updates instantly. DNA changes. Constitution
hash changes. Radar chart shifts.

> "Same sliders, deterministic output. Two evaluators with the same
> profile birth identical agents."

---

## 9:30 — Wrap (30 seconds)

> "Local. Audited. Reversible. Every action gated. No telemetry, no
> phone-home, no cloud lock-in. The audit chain you just saw is the
> operator's evidence — not evidence FOR the daemon to anyone else.
> The threat model is published; the source is open. That's the shape."

---

## Recovery cards (if something glitches)

- **Browser shows stale UI** → `cmd+shift+r` to hard reload. The
  no-cache shim should prevent this but Safari sometimes ignores it.
- **Daemon stops responding** → `./stop.command` then `./start.command`.
  Browser auto-opens after ~5s.
- **Want to start over from scratch** → `./scenarios/load-scenario.command synthetic-incident`
  re-installs the canonical state.
- **Audience asks "but what if it crashes mid-skill?"** → point at
  the dispatch event types: `tool_call_dispatched` happens BEFORE
  execute; if the tool crashes you see `tool_call_failed` paired
  with the dispatched event. A crash mid-execute leaves a dispatched
  without a success — explicitly diagnostic.

## Things NOT to do live

- **Don't run swarm-bringup.command** during the demo — it's a 2-3 min
  bring-up. The whole point of this scenario is that the chain is
  pre-populated.
- **Don't click `reload from disk`** in the Tools tab unless asked —
  it's idempotent but takes a second and there's nothing visible to
  show for it.
- **Don't promise anything in the Companion tier** — that's mission
  pillar 2, designed but not implemented (per ADR-0008 + 0021).
  If pressed, call it explicitly: "designed, not yet shipped."
