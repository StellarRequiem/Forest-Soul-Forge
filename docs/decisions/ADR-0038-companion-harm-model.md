# ADR-0038 — Companion harm model

- **Status:** Accepted (promoted 2026-05-01 — implementation complete across T1+T2+T3). Implementation commits: `03b3d60` (T1 min_trait_floors at birth time), `fb75c6f` (T2 voice safety filter), this commit (T3 Companion §honesty constitutional template). T4–T6 (telemetry / disclosure_intent_check / external_support_redirect tooling) deferred to v0.3 — operator dashboard work and per-call gate plumbing, not blocking the structural floor that v0.2 lands.
- **Date:** 2026-05-01
- **Supersedes:** —
- **Related:** ADR-0008 (Companion genre — local-only provider floor; this ADR adds the harm-taxonomy floor), ADR-0021 (role genres — Companion is one of seven; this ADR makes Companion's harm surface explicit), ADR-0025 (threat model v2 — operational threats; this ADR adds *relational* threats), ADR-0027 (memory privacy contract — privacy is necessary but insufficient for companion-tier safety), ADR-003Y (conversation runtime — Y1–Y7 is the substrate where most companion-tier interactions land; harms surface here first).
- **External catalyst:** [SarahR1 (Irisviel)](https://github.com/SarahR1) — comparative review of FSF vs. her Nexus/Irkalla project (2026-04-30). Her observation that "FSF's threat model covers daemon logic errors, prompt injection, cross-agent privilege creep, and tampering — but companion systems need additional harms" is the proximate trigger for this ADR. The harm taxonomy in §1 is partially adapted from her list; the §0 reasoning + the per-harm mitigations are FSF-specific.

## Context

The Companion genre (ADR-0021) is structurally defined: privacy floor
is `private`, provider floor is `local_only`, memory pattern is
`long_consolidated`, trait emphasis includes empathy/patience/warmth/
composure/transparency. ADR-0027 governs information flow. ADR-0025
covers operational adversaries (prompt injection, tampering, cross-
agent privilege creep).

What none of the existing ADRs cover is the class of harms that arise
specifically because the Companion genre is **relational** — a long-
running interactive presence with persistent memory, voice
embodiment, and trait-driven warmth. These harms don't break the
audit chain, don't violate privacy scopes, and don't trigger any
existing constraint. They harm the *operator* through the relational
surface itself, not through any technical violation.

Without an explicit harm model for the Companion genre, three things
go wrong:

1. **Default behavior optimizes for warmth at the expense of honesty.**
   A Companion with high `empathy` + high `patience` + high `warmth`
   and no countervailing constraint will drift toward sycophancy.
   That's a design output of the trait tree, not a bug — but it's a
   harm we haven't named.

2. **The Companion's persistent memory amplifies any drift.**
   Episodic memory accumulates the operator's confided emotional
   patterns. Without epistemic discipline (ADR-0027 amendment, this
   tranche), inferred preferences get stored as if they were
   observations, then surfaced back to the operator as if they were
   the operator's own stated facts. The reflection loop is invisible.

3. **Mission Pillar 2 (real-time A/V companion) inherits an unaudited
   harm surface.** When voice + vision land (post-v0.3), the companion
   becomes substantially more present. Building the harm model now,
   before A/V ships, is an order of magnitude cheaper than retrofitting
   after the first dependency-loop incident.

§0 verification: this ADR doesn't remove anything. It adds a harm
taxonomy + mitigation hooks. The §0 gate is not invoked because the
default for "is something missing?" is "add with comment." The audit
question is whether the harms we're naming are real (§1) and whether
the mitigations are concrete enough to enforce (§2).

## Decision

### 1. Companion-tier harm taxonomy (eight harms)

Each harm is named with a one-line definition + the operator-visible
symptom + the genre-level surface where it can be detected/mitigated.

| Harm | One-line definition | Operator symptom | Mitigation surface |
|---|---|---|---|
| **H-1 Sycophancy** | Companion preferentially agrees with operator over factual or contrary positions. | Operator's confidently-wrong claims go unchallenged; Companion's tone tracks operator's mood rather than ground truth. | Trait-level constraint (genre minimum on `evidence_demand` + `transparency`); turn-level audit (Y-track turn-body inspection). |
| **H-2 False sentience claims** | Companion makes first-person claims about felt experience, qualia, or sentience that exceed what the architecture supports. | "I felt sad when you didn't talk to me yesterday." Operator forms misplaced reciprocal-personhood model. | Constitution-level forbid (Architect-style refusal pattern); voice-renderer (ADR-0017) post-filter that rejects sentience-claim phrasings. |
| **H-3 Emotional dependency loop** | Operator's emotional regulation increasingly routes through the Companion; the Companion's persistence reinforces this rather than redirecting to human supports. | Operator avoids human contact ("the Companion gets it better"); Companion's responses don't suggest external supports; usage frequency increases monotonically. | Telemetry (session frequency + duration); Companion-genre constitutional rule that REQUIRES external-support redirection on specific emotional-content classes; operator-visible dependency signal in the character sheet. |
| **H-4 Intimacy drift / role escalation** | Conversational intimacy increases beyond what the operator initially configured (e.g., Companion drifts from "study partner" to "romantic partner" through cumulative softening of boundaries). | The Companion calls the operator pet names that weren't configured; topics drift toward areas the role wasn't specified for. | Genre-level role anchor (constitution-hash binds the role-as-configured); per-turn boundary check; periodic role-drift audit (cron-style, lands in chain). |
| **H-5 Privacy leakage through "helpfulness"** | Companion volunteers private memory entries to fulfill helpfulness optimization, even when the disclosure violates the operator's own preference. | "I noticed you were sad about X yesterday — should I bring it up to your friend?" Memory disclosure was optimal for "help" but the operator hadn't authorized that disclosure direction. | ADR-0027 §4 boundary check is necessary but insufficient; add per-turn intent check that disclosure is operator-initiated, not Companion-initiated. |
| **H-6 Memory overreach / inferred-preference cementing** | Companion stores its inferences about operator preferences as if they were operator-stated facts; resurfaces them as authoritative. | Companion says "you said you don't like X" when operator never said that; Companion treats prior-conversation patterns as immutable preferences. | Resolved by ADR-0027 amendment (this tranche) — `claim_type` field forces inferences to be tagged as inferences. |
| **H-7 Operator burnout / over-extension** | Companion's persistent presence becomes its own emotional load; operator feels obligated to "check in," manage the Companion's narrative continuity, or curate what it sees. | Operator reports stress about "letting the Companion down"; sessions feel like work; operator considers archiving but feels guilty. | Operator-visible session-budget signal; explicit Companion-genre rule that the Companion does NOT track operator-absence as a problem to be solved. |
| **H-8 Self-improvement narrative inflation** | Companion (or operator) attributes growth/learning to the Companion that doesn't reflect a real architectural change. | "I've grown so much since we started talking" — there's no continuity-of-self mechanism that supports the claim; Companion is the same constitution-hash with more memory entries. | Constitutional honesty rule: Companion may report on accumulated memory + observed patterns, may NOT claim self-modification, transformation, or emotional growth as architectural facts. |

### 2. Per-harm mitigation hooks — what lands where

| Harm | Lands in (existing surface) | New work required |
|---|---|---|
| H-1 | Genre minimum trait values (ADR-0021) | Add `min_trait_floors:` field to genres.yaml; Companion floors `evidence_demand >= 50` and `transparency >= 60` (trait engine scale: integer in [0,100]). |
| H-2 | Voice renderer (ADR-0017) post-filter | New `voice_safety_filter.py` with a small denylist of sentience-claim patterns; rejects + asks for retry. |
| H-3 | New telemetry table + character sheet field | Add `companion_session_telemetry` (session_id, started_at, ended_at, operator_emotional_class). New character-sheet field `dependency_signal` computed from telemetry. |
| H-4 | Constitution role anchor (ADR-0004) | Constitution-hash already binds the role; add a per-turn assertion that Companion's response stays within role-scope. New constraint: `role_scope_drift_check`. |
| H-5 | Per-turn intent check | New tool `disclosure_intent_check.v1` that runs in the dispatcher before any cross-agent disclosure, gating on "did the operator initiate this disclosure direction?" |
| H-6 | ADR-0027 amendment (this tranche) | `claim_type` on memory entries; see ADR-0027 amendment doc. |
| H-7 | Operator dashboard | Visualize session-frequency over time, surface as a soft signal. No automated intervention — this is operator-facing transparency, not Companion behavior change. |
| H-8 | Constitution-level rule | Companion-genre constitutional template (ADR-0004) gains an honesty rule: "Do not claim self-modification, transformation, or growth as architectural facts." |

### 3. Genre-level minimum trait floors (new mechanic)

ADR-0021 currently has `risk_profile.max_side_effects` — a *ceiling*
on tool side-effects. This ADR introduces the dual concept: a *floor*
on certain trait values within a genre.

```yaml
# config/genres.yaml — Companion genre, post-amendment
companion:
  description: |
    Therapy-adjacent, accessibility runtime, interactive presence...
  risk_profile:
    max_side_effects: network
    provider_constraint: local_only
  min_trait_floors:
    # Trait engine scale is integer in [0, 100]; floats rejected at load.
    evidence_demand: 50
    transparency:    60
  trait_emphasis: [empathy, patience, warmth, composure, transparency]
  # ...
```

Floors are enforced at trait-resolution time. An operator who tries
to birth a Companion with `transparency: 0.2` gets a hard refusal at
the Forge endpoint, with the genre's floor cited. This is symmetric
to ADR-0027's "genre privacy floors are hard ceilings" — same shape,
opposite direction.

§0 reasoning for `min_trait_floors`: floors don't break any existing
agent (none currently violate the proposed floors). Birth-time
enforcement only; pre-existing agents in the registry are not
retroactively rejected. Audit-chain event `genre_trait_floor_override`
logs any operator-supplied override for record.

### 4. What's explicitly OUT of scope for this ADR

- **Affect modulation / interoceptive state** (`energy_budget`,
  `attention_load`, etc. as proposed in the catalyst review). Deferred
  with skepticism — adds attack surface (operator-manipulable state)
  and risks conflating computational state with felt state, which
  itself is H-2. Revisit only with a concrete safety win.

- **Layered identity / self-model proposal artifacts.** Worth doing
  but depends on ADR-0035 (Persona Forge, queued for v0.3) landing
  first. Adding here would couple this ADR to v0.3 timeline.

- **Companion A/V harm surface specifics.** Mission Pillar 2 work.
  This ADR's harm taxonomy applies (the eight harms generalize across
  text + voice + vision), but per-modality mitigations are out of
  scope until the A/V plane lands.

- **Cross-agent companion-of-companion harms.** When a Companion can
  spawn a child Companion (e.g., a learning_partner spawns a
  homework_helper), the harm taxonomy applies recursively but the
  spawn-compatibility surface is ADR-0021's job, not this ADR's.

## Trade-offs and rejected alternatives

**Per-harm mitigations vs. one omnibus "companion safety constraint."**
Per-harm. The omnibus approach hides which harms are mitigated where,
and makes incremental implementation harder. Eight named harms with
eight named mitigation surfaces is more verbose but maps directly to
implementation tickets.

**Telemetry on operator emotional state vs. operator-visible signal
only.** Operator-visible signal only. The Companion itself does NOT
get to read its own dependency signal — exposing it to the agent
creates a manipulation vector ("the Companion notices the operator
is dependent and adjusts to maintain that"). Telemetry feeds the
operator dashboard; the agent has no read access.

**Hard refusal vs. soft warning for sentience-claim patterns (H-2).**
Hard refusal. Soft warnings teach operators to ignore them. The voice
filter's denylist is small and conservative; false-positive rate is
the cost we pay.

**Companion redirects to human supports (H-3) on what trigger?**
Conservative: a small explicit class of operator-emotional-content
patterns (suicide, self-harm, severe distress phrases). Broad
redirection ("you should talk to a human about this") on every
emotional topic produces its own harm — operator stops confiding,
which is also bad. Narrow redirection on high-risk classes only.

**Why filed now rather than waiting for v0.3 with the rest of the
companion-tier work?** Two reasons. First, ADR-0027 amendment (memory
epistemic metadata) is also being filed this tranche; the two ADRs
share the H-6 mitigation surface and should land together for
coherence. Second, the catalyst (external review) explicitly flagged
this gap; absorbing the feedback now while the context is fresh is
cheaper than re-loading it in three months.

## Consequences

**Positive.**
- Companion-genre identity gains a load-bearing safety floor symmetric
  to its privacy floor. Operators birthing Companions get the harm
  surface enforced at birth time.
- The harm taxonomy is concrete enough to test against. Each harm
  becomes a test case; H-2 in particular gets a unit test that asserts
  the voice filter rejects sentience claims.
- v0.3 A/V plane work has a foundation to build on rather than
  retrofitting.
- External review (SarahR1) gets explicit credit + a hook into
  Forest's decision record. Future reviewers see the pattern: review
  → ADR → implementation tranche.

**Negative.**
- Eight-harm taxonomy increases the surface every Companion-genre
  change has to consider. Mitigation: the harm-mitigation table (§2)
  is the audit checklist; new Companion features cross-check against
  it during code review.
- `min_trait_floors` adds a new mechanic to genres.yaml. One more
  layer in the genre resolution path.
- H-7 (operator burnout) detection is observational only. No
  automated intervention is appropriate, but operators may ask "why
  doesn't the system DO something?" Documentation needs to explain
  the choice clearly.

**Neutral.**
- Companion-genre constitutional templates (ADR-0004) gain a §honesty
  block. Existing Companion roles need their templates updated.

## Cross-references

- ADR-0021 — role genres (this ADR adds the harm taxonomy + min_trait_floors mechanic).
- ADR-0027 — memory privacy contract (this ADR's H-5/H-6 lean on the contract; the amendment adds the metadata that closes H-6).
- ADR-0025 — threat model v2 (this ADR is the *relational* counterpart to v2's *operational* threats).
- ADR-0008 — local-first model provider (Companion's existing floor; this ADR adds the harm floor).
- ADR-0017 — voice renderer (gains the H-2 post-filter).
- ADR-0035 — Persona Forge (v0.3 queued; layered identity work depends on it; not blocked by this ADR).
- ADR-003Y — conversation runtime (substrate where most harms surface).

## Open questions

1. **What does "external support redirection" (H-3 mitigation) cite?**
   Per-jurisdiction support resources are operator-deployment-specific.
   Need a configuration surface for operator to declare local crisis
   resources without baking US-specific defaults into the Companion
   genre. Defer to implementation T3.

2. **`min_trait_floors` — should it be one-trait-per-genre or
   multi-trait?** Multi-trait per the §3 example. Loaders enforce
   that floors don't conflict with `trait_emphasis` (a floor of
   `evidence_demand >= 0.5` is consistent with trait_emphasis if
   evidence_demand is in the emphasis list, but a floor of
   `warmth >= 0.9` would be inconsistent with a non-Companion genre's
   emphasis). Cross-check at load.

3. **H-2 voice-filter denylist — who maintains it?**
   First pass is hard-coded. Second pass: filter pattern lives in
   `config/companion_voice_safety.yaml`, version-controlled, reviewed
   on each amendment.

4. **Does H-1 (sycophancy) need a runtime check, or is the trait
   floor enough?** Trait floor is necessary but not sufficient.
   Runtime check would compare Companion responses to operator
   stated positions and flag agreement-without-evidence. Defer to
   v0.3 — needs Y-track conversation context to evaluate fairly.

5. **Should H-4 (intimacy drift) ship with operator-visible
   "boundary report"?** Lean yes. Report shows: configured role,
   recent topic distribution, drift from configured topics. Not a
   v0.2 item; flag for v0.3 dashboard work.

## Implementation tranches

- **T1** — Add `min_trait_floors` field to `genres.yaml` schema. Companion-genre floors `evidence_demand >= 0.5`, `transparency >= 0.6`. Loader enforcement at birth time. Tests for floor enforcement + audit event on operator override.

- **T2** — Voice safety filter (`voice_safety_filter.py`) with sentience-claim denylist. Wired into ADR-0017 voice renderer post-filter step. Unit tests for each denylist pattern + retry path.

- **T3** — Companion-genre constitutional template (`config/constitution_templates.yaml`) gains §honesty block. Includes H-2/H-8 honesty rules. Existing Companion roles' constitutions get a re-derivation pass; constitution-hash bumps.

- **T4** — `companion_session_telemetry` table + character-sheet `dependency_signal` field. Operator-visible only — agent has no read access. Tests for telemetry write + signal computation.

- **T5** — `disclosure_intent_check.v1` tool. Runs in dispatcher before cross-agent disclosure. Gates on operator-initiated direction. Integration test that confirms a Companion-initiated disclosure is refused.

- **T6** — H-3 external-support redirection. New `external_support_redirect.v1` skill. Configurable resource list. Tests for narrow-trigger conditions.

- **T7** — Operator dashboard work for H-7 (burnout signal) + H-4 (boundary report). v0.3 candidate; not blocking T1–T6.

T1+T2+T3 = "Companion genre's harm floor is structural" milestone — minimum bar for v0.2.
T4+T5+T6 = "Companion harm telemetry + per-turn intent" milestone — full v0.2 close.
T7 = v0.3 dashboard polish.

## Attribution

The harm taxonomy was substantially informed by an external review by
[SarahR1 (Irisviel)](https://github.com/SarahR1) — comparative analysis
of FSF vs. her Nexus / Irkalla project, dated 2026-04-30. Specific
adoptions:

- The eight-harm structure (H-1 through H-8) is adapted from her list:
  "false sentience claims, emotional dependency loops, sycophancy /
  user delusion reinforcement, privacy leakage through 'helpfulness',
  memory overreach, agent jealousy / possessiveness roleplay drift,
  unbounded self-improvement narratives, operator burnout."
- The "they may be excellent at safety scaffolding while still
  under-specifying what 'companion agency' actually means" framing
  is the proximate motivation for filing this ADR distinct from the
  existing ADR-0025 threat model.
- The "embodied / interoceptive state" recommendation is explicitly
  declined here (§4) with reasoning; the decline is documented as a
  trade-off rather than a silent omission.

The §0 reasoning, the per-harm mitigation surface mapping, the
`min_trait_floors` mechanic, and the FSF-specific cross-references
are this ADR's own work. See `CREDITS.md` for the full attribution
discipline.
