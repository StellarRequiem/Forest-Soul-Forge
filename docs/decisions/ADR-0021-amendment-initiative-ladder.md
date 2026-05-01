# ADR-0021 amendment — initiative ladder + `max_initiative_level`

- **Status:** Accepted (promoted 2026-05-01 — implementation complete across T1+T2+T3). The base ADR-0021 stays Accepted; this is an additive amendment. Implementation commits: `03b3d60` (T1 genres.yaml fields + loader), `823e69c` (T2 constitution derived fields), `4e9b8cf` (T3 InitiativeFloorStep dispatcher; opt-in per tool — per-tool annotation deferred for catalog audit).
- **Date:** 2026-05-01
- **Amends:** ADR-0021 (role genres). Adds `max_initiative_level` field alongside the existing `risk_profile.max_side_effects`. The original taxonomy + spawn rules + storage shape stay in force.
- **Related:** ADR-0019 (tool execution runtime — initiative landings go through the dispatcher with audit), ADR-0027 (memory privacy contract — memory writes are an L1 operation), ADR-0033 (Security Swarm — swarm roles need higher initiative ceilings than Companions), ADR-0034 (SW-track triune — Architect/Engineer/Reviewer initiative tiers differ; this amendment makes that explicit), ADR-0038 (companion harm model — H-3/H-4 mitigation depends on Companion's initiative ceiling being declared, not implicit).
- **External catalyst:** [SarahR1 (Irisviel)](https://github.com/SarahR1) — comparative review of FSF vs. her Nexus / Irkalla project (2026-04-30). Her observation that *"FSF has a side-effect ladder but it's about effect, not initiative"* identified the orthogonality gap. The five-step ladder shape (L0–L5) is adapted from her review's table; the FSF-specific genre-level enforcement, audit-event names, and §0 reasoning are this amendment's work.

## Context

ADR-0021 v1 gives each genre a `risk_profile.max_side_effects`
ceiling — the highest tier of tool side-effects (`read_only` →
`network` → `filesystem` → `external`) the genre's standard kit can
reach. That ceiling answers one question well: **"How destructive
can this agent's actions be?"**

What the ceiling does NOT answer is the orthogonal question:
**"How autonomous is this agent allowed to be in *deciding* to
act?"**

Two examples:

1. A Companion-genre agent and a Guardian-genre agent both have
   `read_only` tool surfaces — they can both read memory, recall
   conversations, and log observations. Same `max_side_effects`
   ceiling. But:
   - Companion is *reactive*: the operator says something, the
     Companion responds. It doesn't initiate read-only operations
     unprompted.
   - Guardian is *autonomous*: it watches other agents' output and
     blesses or blocks without operator prompting per turn.

   Same effect ceiling, drastically different initiative posture.
   Today's ADR-0021 doesn't capture the difference.

2. A SW-track Engineer (ADR-0034) and an Actuator both have
   `external` ceilings — both can mutate state outside the
   operator's environment. But:
   - Actuator: every non-read-only call requires explicit operator
     approval per call. High effect, low initiative.
   - Engineer: `code_edit.v1` with `--auto-approve` (tier-gated)
     can run a whole patch series autonomously. High effect, high
     initiative.

   The dispatcher already knows about the approval gate. What it
   doesn't have is a structured way to ask "which agents may run
   without per-call approval at all, vs. only with operator
   ratification, vs. never?"

The catalyst review's L0–L5 framing makes this explicit: initiative
is a property of the role-as-deployed, not derivable from the
side-effects ladder. Companions max at L2 (suggesting); SW-track
Engineers can earn L4 (reversible side-effects with policy);
Actuators stay at L5 (destructive only with friction).

§0 verification: this amendment doesn't remove anything. It adds a
field + enforcement points + audit events. The old `max_side_effects`
field stays in force unchanged. No agent currently in the registry
is broken by the addition; pre-existing agents get a default
initiative ceiling computed from their genre at first daemon boot
post-migration.

## Decision

### §1 — Six-level initiative ladder

```
L0  Reactive — responds only to direct operator input. No memory writes,
    no autonomous reads, no observation logging, no prompting. The
    agent is a turn-by-turn responder with no persistence beyond the
    constitution + the conversation context.

L1  Memory — autonomous private-scope memory writes (operator-visible
    via the chain + character sheet, but not gated per write). Reads
    own memory autonomously. No cross-agent reads, no tool calls
    beyond memory + reflection.

L2  Suggestion — initiates *suggestions* without acting on them.
    May say "I notice X — would you like me to do Y?" without
    proceeding to Y. Can call read_only tools to investigate
    something it noticed; cannot call any side-effect-bearing tool
    autonomously.

L3  Read-only autonomous — runs read_only tools without per-call
    approval. May initiate observation cycles, run read-only
    investigations, schedule its own reads. State changes still gate;
    only reads are autonomous.

L4  Reversible side-effects with policy — runs `network` and
    `filesystem` tools autonomously *under declared policy*. Policy
    declares: which tools, which targets, which budget (per hour /
    per session), which kill-switches. Tools whose effects are
    cleanly reversible (file writes inside an allowlisted dir, HTTP
    fetches without state-mutation verbs) qualify. Operator gets
    after-the-fact summary, not per-call approval.

L5  Destructive with friction — `external` tools that mutate state
    outside reversible scope (deploys, sends, deletes external).
    Always operator-gated per call. Friction is the feature; this
    is the existing approval-queue path.
```

### §2 — `max_initiative_level` field on genres

```yaml
# config/genres.yaml — example shape post-amendment
companion:
  description: |
    Therapy-adjacent, accessibility runtime, interactive presence...
  risk_profile:
    max_side_effects: network
    provider_constraint: local_only
  min_trait_floors:                       # from ADR-0038
    evidence_demand: 0.5
    transparency: 0.6
  max_initiative_level: L2                # NEW
  default_initiative_level: L1            # NEW
  trait_emphasis: [empathy, patience, warmth, composure, transparency]
  # ...
```

Two fields:

- **`max_initiative_level`** — the *ceiling*. Genre-level hard cap.
  Operator cannot birth a Companion at L3 even with override.
  Ceiling violations refuse at birth time, audit event
  `genre_initiative_ceiling_refused`.

- **`default_initiative_level`** — the genre's default at birth. Roles
  inside the genre may override downward (more conservative) but not
  upward. Per-role overrides land in role definitions, not in
  `genres.yaml`.

### §3 — Per-genre defaults

| Genre | `max_initiative_level` | `default_initiative_level` | Rationale |
|---|---|---|---|
| **Observer** | L3 | L3 | Defined by autonomous read-only observation; that's the genre's job. |
| **Investigator** | L4 | L3 | Read-only by default; reversible network calls under declared policy. |
| **Communicator** | L3 | L2 | Output goes to humans; suggestion-class default. L3 ceiling allows autonomous summarization passes. |
| **Actuator** | L5 | L5 | Action-class genre — its ceiling is the existing approval gate. Default = ceiling because Actuators don't have a "lower" useful state. |
| **Guardian** | L3 | L3 | Watches other agents autonomously; reads only. Refusal/approval is its action surface, gated separately by ADR-0019. |
| **Researcher** | L4 | L3 | Long-running read-heavy by default; reversible network for paper fetches under policy. |
| **Companion** | L2 | L1 | Reactive default; suggestion ceiling. Higher levels would require crossing into autonomous emotional regulation, which is exactly the H-3/H-4 surface ADR-0038 names as harm. |

### §4 — SW-track interaction (ADR-0034)

ADR-0034 introduces three SW-track roles: Architect, Engineer,
Reviewer. They claim three existing genres (per ADR-0034 §note).
With this amendment:

| SW role | Genre claimed | `max_initiative_level` | Notes |
|---|---|---|---|
| Architect | Researcher | L3 | Designs; doesn't implement. Reads broadly, writes design docs. Engineer carries the implementation initiative. |
| Engineer | Actuator | L4 | Implements under declared policy. `--auto-approve` for code_edit within allowlist is the L4 surface. Out-of-allowlist edits stay L5. |
| Reviewer | Guardian | L3 | Reads code + Architect's design + Engineer's diff; approves or refuses. Refusal is its action; covered by Guardian default. |

This makes the SW-track triune's privilege ceiling structural: the
genre claim *is* the initiative declaration. No code asks for
permission; the genre + role enforces it at dispatch time.

### §5 — Per-call dispatcher enforcement

`dispatcher.py` (R3 governance pipeline) gains a step:

```python
class InitiativeFloorStep(GovernanceStep):
    """Refuses calls where the call's initiative-class exceeds the
    agent's max_initiative_level.

    Initiative class is computed from:
    - Was this call operator-initiated (per-turn input) or agent-
      initiated (autonomous timer / agent's own decision)?
    - Does the tool side-effect class exceed the agent's reversibility
      contract?

    A Companion (L2) may run a `memory_recall` autonomously (L1
    operation) but may NOT run a `web_fetch` autonomously (L3+
    operation, agent-initiated). The same `web_fetch` is allowed
    when operator-initiated.
    """
```

The step lives next to `GenreFloorStep` in the governance pipeline.
Refusals emit `initiative_floor_refused` audit events with the
agent's level, the call's class, and the gap.

### §6 — Schema impact

No schema bump. `max_initiative_level` is genre-config (lives in
`genres.yaml`); per-agent levels are computed at birth from the
genre default + role override and recorded in the constitution
(constitution-hash bumps for any agent re-derived post-amendment).

The constitution gains:

```yaml
# constitution.yaml post-amendment
agent: ...
role: ...
genre: ...
genre_description: ...
initiative_level: L1                     # NEW — derived at birth
initiative_ceiling: L2                   # NEW — from genre.max_initiative_level
# ...
```

### §7 — Audit obligations (additions to ADR-0021's existing chain coverage)

Three new event types:

| Event | Triggered by |
|---|---|
| `genre_initiative_ceiling_refused` | Birth-time attempt to set initiative_level > genre.max_initiative_level. |
| `initiative_floor_refused` | Dispatcher refusal — call's initiative-class exceeds agent's level. |
| `initiative_level_override` | Operator explicit override (audit-chain visible). Symmetric to `spawn_genre_override` from ADR-0021 v1. |

## §0 verification

Additive only. No removal. Existing agents continue to function;
they receive a default initiative_level at first daemon boot post-
amendment, computed from their genre. The constitution-hash bumps for
re-derived agents — but the existing in-flight agents keep their
original constitutions in the chain. New births adopt the new shape.

§0 gate not invoked.

## Trade-offs and rejected alternatives

**Why six levels (L0–L5) instead of three or ten?** Six is the
fewest that distinguishes the cases that matter:
- L0/L1 distinguishes "no memory" from "private memory writes" —
  important because L1 changes accountability (private memory is
  agent-derived state).
- L1/L2 distinguishes "writes to self" from "voices opinions" —
  important because L2 produces operator-visible suggestions.
- L2/L3 distinguishes "reactive only" from "autonomous reads" —
  important for Observer/Guardian semantics.
- L3/L4 distinguishes "reads" from "reversible writes" — the
  side-effect-vs-initiative axis.
- L4/L5 distinguishes "reversible" from "destructive" — maps to
  the existing approval gate.

Three levels collapse important cases. Ten levels invent
distinctions that don't pay rent.

**Initiative as a property of the agent vs. of the call.** Both.
The agent declares a ceiling (`max_initiative_level`); each call
gets classified at dispatch and checked against the ceiling. This is
symmetric to side-effects: tool declares its class, agent has a
ceiling.

**Per-role override vs. per-agent override.** Per-role downward only;
per-agent only via explicit `initiative_level_override` event. An
operator who wants a specific Companion to never write memory ever
sets the role's `initiative_level: L0` override; they cannot set a
Companion to L4 even with override (the genre ceiling is hard).

**Why is Companion `default_initiative_level: L1` rather than L0?**
L1 (private memory) is what makes a Companion a Companion. L0
collapses to a stateless prompt-response agent, which is the
opposite of the genre's defining property. Operators who want L0 are
asking for a Communicator, not a Companion.

**Why is Researcher `max: L4`?** Reversible network calls (paper
fetches under allowlist policy) is the genre's load-bearing use
case. L5 deploys / sends / external mutations don't fit Research.

**Combining `max_side_effects` and `max_initiative_level` into one
field.** Rejected. They're orthogonal — Companion (low side-effects,
low initiative) and Guardian (low side-effects, high initiative)
collapse to the same combined value despite different real
behaviors. Two fields preserve the distinction.

**Per-deployment initiative override (operator-set).** Out of scope.
Per-deployment overrides go through the existing
`initiative_level_override` audit event surface; no new mechanism
needed. If repeated per-deployment override patterns emerge, file
follow-up.

## Consequences

**Positive.**
- Companion genre's reactive posture becomes structural, not
  trait-derived. ADR-0038 H-3 and H-4 mitigations rest on the
  initiative ceiling being known at dispatch time.
- SW-track triune's privilege gradient becomes legible.
  Architect/Engineer/Reviewer roles inherit appropriate ceilings
  through their genre claims.
- Operators birthing agents see two ceilings (effect + initiative),
  which is the right amount of detail. The shape "this agent can do
  X, and decides to do X under conditions Y" is more honest than
  "this agent has X tools."
- Future ADRs (Persona Forge ADR-0035, Verifier Loop ADR-0036) have a
  clean field to specialize. Self-modification proposal artifacts
  (ADR-0035) ride at L4 with policy; verifier loops (ADR-0036) ride
  at L3.

**Negative.**
- Two ceilings to reason about at birth time instead of one.
  Documentation needs to explain orthogonality clearly.
- Dispatcher gains another governance step. R3 pipeline lengthens
  by one — the `InitiativeFloorStep`. Acceptable given pipeline is
  already stage-decomposed.
- `genres.yaml` carries two new fields per genre; some operators
  will see this as configuration sprawl.

**Neutral.**
- `constitution.yaml` gains two derived fields. Constitution-hash
  semantics unchanged (still content-addressed; new fields land in
  the hash input).

## Cross-references

- ADR-0021 v1 — base genre taxonomy; this amendment adds §1–§7 layered on top.
- ADR-0019 — dispatcher; gains `InitiativeFloorStep` as a new pipeline step.
- ADR-0027 — memory; L1 captures the autonomous-private-write surface this contract governs.
- ADR-0033 — Security Swarm; swarm roles can earn L3/L4; Sentinel watcher pattern is L3.
- ADR-0034 — SW-track triune; Architect/Engineer/Reviewer claim Researcher/Actuator/Guardian, respectively, with the ceilings declared in §4 above.
- ADR-0038 — companion harm model; H-3/H-4 mitigations rest on Companion's L2 ceiling being structural.

## Open questions

1. **"Initiative class" classification logic — where does it live?**
   In dispatcher (`InitiativeFloorStep`). The step inspects the
   call's metadata: `operator_initiated: bool`, tool's
   side-effects class. Lookup table from those two → initiative
   class. Lives in `dispatcher/policy_initiative.py` or similar.

2. **Should L4 require a policy declaration to be syntactically
   valid?** Lean yes. An agent at L4 without a declared policy is
   ambiguous — what's the budget? what tools are in scope? L4
   without policy refuses at birth time. Policy is a YAML stanza
   on the agent's character sheet.

3. **L3 autonomous reads — rate-limited?** Probably. Policy
   declares budget (`reads_per_session`, `tool_call_budget`). v0.2
   ships with conservative defaults; v0.3 might add per-tool
   budgets.

4. **Initiative downgrade at runtime?** An L4 agent that exceeds its
   budget — does it drop to L3 for the rest of the session? Leans
   yes, but adds complexity. Defer to v0.3; v0.2 ships with hard
   refusal at budget exhaustion (chain event, no auto-downgrade).

5. **How does ambient mode (Y5) interact?** Ambient mode lets
   agents run during operator-absence. Ambient activity is
   agent-initiated, not operator-initiated. An ambient Companion
   can still only operate at L2 (suggestion-class) and so produces
   no autonomous actions in ambient mode beyond logging. This is
   correct by construction.

## Implementation tranches

- **T1** — `genres.yaml` gains `max_initiative_level` +
  `default_initiative_level` for all seven genres. Loader enforces
  per-genre ceilings. Tests for floor enforcement at birth.

- **T2** — `constitution.yaml` template gains derived fields.
  Constitution-hash recomputation pass for all existing agents post-
  amendment (first-boot migration).

- **T3** — `dispatcher.py` `InitiativeFloorStep`. Lives in the R3
  governance pipeline alongside `GenreFloorStep`. Audit event
  emission. Tests for refusal + override paths.

- **T4** — Birth API gains `initiative_level` parameter (defaults to
  genre default; can be set lower; refuses upward beyond ceiling).
  Tests for each refusal path.

- **T5** — Character sheet (ADR-0020) `capabilities.initiative_level`
  + `capabilities.initiative_ceiling` fields populate.

- **T6** — Frontend agent-detail view shows the initiative ceiling
  alongside the side-effects ceiling. Document the orthogonality on
  the page.

- **T7** — SW-track agents (Architect/Engineer/Reviewer) re-derived
  with the new fields. Constitution-hash bumps logged in the chain.

T1+T2+T3 = "ladder is enforced at birth + dispatch" milestone — minimum for v0.2.
T4+T5 = "ladder is operator-visible" milestone — full v0.2 close.
T6+T7 = polish + SW-track migration.

## Attribution

The L0–L5 ladder framing is adapted from
[SarahR1 (Irisviel)](https://github.com/SarahR1)'s 2026-04-30 review.
Her table:

> | Level | Meaning |
> | L0 | Responds only; no memory writes. |
> | L1 | Writes private memory with user-visible audit. |
> | L2 | Initiates low-risk suggestions. |
> | L3 | Runs read-only tools autonomously. |
> | L4 | Performs reversible side-effect actions after policy checks. |
> | L5 | Performs durable/destructive/external actions only through explicit human approval/friction. |

This amendment adopts the level shape with one addition (L0 split is
unchanged; L1's "memory writes with user-visible audit" gets named
explicitly as private-scope-only per ADR-0027), and embeds the
ladder in FSF's existing genre + dispatcher + constitution machinery
rather than as a free-standing concept.

The mapping to genres (§3), the SW-track interaction (§4), the
dispatcher step (§5), the schema impact (§6), and the audit events
(§7) are this amendment's own work. See `CREDITS.md` for the
attribution discipline.
