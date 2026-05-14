# ADR-0068 — Personal Context Store (Operator Profile + Cross-Domain Memory)

**Status:** Accepted (2026-05-14). Phase α of the ten-domain
platform arc — the substrate that every domain reads to know
who the operator is, what their preferences are, and what
context to honor across handoffs.

## Context

Forest's kernel today carries no notion of WHO the operator is.
The audit chain knows `agent_dna` per entry. The frontend has a
"change token" affordance for API auth. There's no canonical
place where any agent can read "the operator is Alex; her work
hours are 09:00-17:00; her timezone is America/New_York" without
either re-inferring from context every time or asking the
operator afresh.

This was tolerable when Forest was substrate-only. It breaks the
moment ten functional domains need to coordinate over shared
operator state:

- The Daily Life OS's Morning Coordinator needs to know "work
  hours start at 09:00 in America/New_York" to schedule the
  briefing.
- The Knowledge Forge's Librarian needs to know the operator's
  preferred name to attribute notes.
- The Smart Home Brain needs to know "operator is heading home"
  signals — which require knowing where home is.
- The Content Studio's Writer needs the operator's voice samples
  to match style.
- The Finance Guardian needs to know currency + tax jurisdiction.
- The Learning Coach needs to know the operator's existing
  expertise level + goals.

Every domain re-asking these questions every conversation
becomes intolerable. Worse: the answers drift between domains.

Separately, Forest's four-scope memory model (ADR-0027:
private / lineage / consented / realm) covers AGENT-OWNED
memory. There's no scope for operator-owned facts that span
agents and domains. The lineage scope is the closest, but it's
inherited via agent ancestry, not authored by the operator.

ADR-0068 closes both gaps.

## Decision

This ADR locks **four** decisions:

### Decision 1 — Operator profile is a versioned YAML file

The operator profile lives at `data/operator/profile.yaml`
with a fixed schema:

```yaml
schema_version: 1
operator:
  operator_id: <human-readable stable identifier>
  name: <full name>
  preferred_name: <name to use when addressing the operator>
  email: <primary contact email>
  timezone: <IANA timezone name, e.g. America/New_York>
  locale: <BCP-47 locale, e.g. en-US>
  work_hours:
    start: <HH:MM 24-hour>
    end: <HH:MM 24-hour>
created_at: <RFC 3339 UTC timestamp>
updated_at: <RFC 3339 UTC timestamp>
```

Why YAML, not a registry table:

- Operator-editable directly with any text editor. Matches the
  existing pattern of `config/genres.yaml`, `config/ground_truth.yaml`,
  `config/security_iocs.yaml`.
- Survives a registry rebuild — the operator's identity doesn't
  depend on SQLite state.
- Encrypted-at-rest via ADR-0050 T5 when `FSF_AT_REST_ENCRYPTION=true`.
  Filename suffix `.enc` makes confidentiality posture visible.
- Hot-reloadable via `POST /operator/profile/reload`.

Extensibility is additive: future tranches add optional fields
(trust circle, allergies, dietary preferences, content samples,
financial jurisdiction) under `operator.*` with schema_version
bumps gated by a migration helper.

### Decision 2 — Operator profile seeds the Reality Anchor

The profile's facts become Reality Anchor ground-truth entries
(ADR-0063) at daemon boot. So if an agent later asserts "the
operator's timezone is America/Los_Angeles," the Reality Anchor
pre-turn hook catches the contradiction.

This wires personal-truth into the existing tamper-evident
verification substrate. The Reality Anchor was built for system
invariants (audit chain canonical path, schema version, license
identity); seeding it with operator facts extends the same
discipline to personal-domain truths.

Seeded facts at T1:

- "The operator's name is `<name>`."
- "The operator's preferred name is `<preferred_name>`."
- "The operator's primary email is `<email>`."
- "The operator's timezone is `<timezone>`."
- "The operator's locale is `<locale>`."
- "The operator's work hours are `<start>` to `<end>` local time."

Future tranches expand the seed set as profile fields grow.

### Decision 3 — A fifth memory scope: `personal`

ADR-0027's four scopes (private / lineage / consented / realm)
cover agent-owned memory. Operator-owned facts that all domains
should read need a distinct scope:

- **`personal`** — owned by the operator, readable by any agent
  with `read:personal` constitution permission. Default-readable
  for agents in companion + researcher genres; gated for others.
  Writes always require operator approval (same surface as
  `consented`).

Why a new scope rather than overloading `realm`:

- `realm` is "this Forest deployment" — runtime invariants,
  daemon paths, version, etc. Personal facts are operator-bound,
  not deployment-bound.
- Different read defaults: realm is publicly readable by every
  agent (low-sensitivity infrastructure); personal is gated on
  genre + posture (operator's actual life).
- Different write semantics: realm changes when the deployment
  changes; personal changes when the operator decides.

Schema impact: `memory_entries.scope` already accepts arbitrary
strings; the v21→v22 migration is a no-op except for adding
`personal` to the validator's allow-list.

### Decision 4 — Operator profile read is a builtin tool

`operator_profile_read.v1` (read_only side-effect tier, all
genres eligible) returns the parsed OperatorProfile as a dict.

Agents that need operator context call this tool. The dispatcher's
governance pipeline gates the call exactly like any other tool
(constitution allows, genre permits, posture allows, etc.).

A future tranche (T2) ships `operator_profile_write.v1`
(requires_human_approval=true, gated to specific operator-fact
updates). For T1, the operator edits the YAML directly.

## Implementation Tranches

| # | Tranche | Description | Effort |
|---|---|---|---|
| T1 | Profile schema + module + read tool + CLI | data/operator/profile.yaml schema + core/operator_profile.py loader + operator_profile_read.v1 builtin + fsf operator CLI (show/edit/verify) + Reality Anchor seeding at boot | 1 burst (this burst, B277) |
| T2 | Operator-write tool + approval gate | operator_profile_write.v1 with require_human_approval; ground-truth re-seed on edit; audit `operator_profile_changed` event | 1 burst |
| T3 | `personal` memory scope | Validator allow-list update; default read permissions per genre; consent semantics; tests | 1 burst |
| T4 | Profile extension — trust circle | People the operator interacts with (name, relationship, comm preferences); per-person memory tagging | 1 burst |
| T5 | Profile extension — content + voice samples | Operator's writing samples for Content Studio style matching; pronunciation samples for Voice I/O TTS personalization | 1-2 bursts |
| T6 | Profile extension — financial + jurisdiction | Currency, tax-residence, fiscal-year start, financial-tooling preferences for Finance Guardian | 1 burst |
| T7 | Cross-domain consent prompts | First-boot wizard that walks the operator through enabling each domain's connector + consent posture; writes to profile.connectors.* | 2 bursts |
| T8 | Profile migration substrate | Schema-version-aware loader; auto-migration helpers; tests for v1→v2 etc. | 1 burst |

Total estimate: 9-10 bursts across T1-T8.

## Consequences

**Positive:**

- Every domain reads the same operator profile. No drift.
- Operator-truth becomes tamper-evident via Reality Anchor.
- Encryption-at-rest covers operator profile automatically (ADR-0050
  T5 picks up `.enc` variant transparently).
- Schema-evolution path is clear: additive fields per tranche +
  migration helpers when shape changes.
- Operator-editable YAML matches existing config patterns.

**Negative:**

- Adds a fifth memory scope. Existing memory tooling that hardcodes
  the four scopes needs an audit (covered by T3).
- First-boot bootstrap requires either operator interaction or an
  env-supplied skeleton profile. Headless daemons need a profile
  path env var to start without prompting.
- Profile data is high-sensitivity. Operator-deployment leaks would
  be more damaging than agent-leaks. Encryption-at-rest is
  effectively mandatory for production operator deployments.

**Neutral:**

- The Reality Anchor seeding pattern is reused, not rebuilt.
- The audit chain captures profile changes — same provenance shape
  as constitution edits.
- Per-genre read defaults are conservative; operators tighten via
  per-agent constitution overrides as needed.

## What this ADR does NOT do

- **Does not auto-fill the profile from external sources.** Email,
  timezone, name etc. all start blank or with placeholders; the
  operator fills them in. Future tranches may add helpers (system
  timezone detection, Apple Account name pull-in) gated behind
  explicit operator consent.
- **Does not gate read access by data sensitivity within the
  profile.** Any agent with `read:personal` sees the whole profile.
  Per-field ACL is queued for a later tranche if a use case
  surfaces.
- **Does not implement the cross-domain orchestrator.** ADR-0067
  ships separately and reads the profile as one of its inputs.
- **Does not replace the existing API token.** Profile is for
  operator identity + preferences; api_token in localStorage is
  for HTTP auth to the daemon. Different concerns, kept separate.

## See Also

- ADR-0027 (memory scopes) — ADR-0068 adds a fifth scope
- ADR-0050 (encryption-at-rest) — covers the profile file automatically
- ADR-0063 (reality anchor) — profile seeds the ground-truth catalog
- ADR-0067 (cross-domain orchestrator, queued) — primary consumer
- ADR-0070 (voice I/O substrate, queued) — voice front door reads profile
